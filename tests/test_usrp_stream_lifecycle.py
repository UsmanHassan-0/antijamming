from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np

from antijamming.radio.usrp import device as device_module
from antijamming.radio.usrp.device import UsrpRxDevice


class _ErrorCodes:
    none = object()
    overflow = object()
    late = object()
    timeout = object()
    other = object()


class _StreamMode:
    start_cont = "start"
    stop_cont = "stop"


class _StreamCmd:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stream_now = False
        self.time_spec = None


class _TimeSpec:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


class _Now:
    @staticmethod
    def get_real_secs() -> float:
        return 1.0


class _Usrp:
    @staticmethod
    def get_time_now() -> _Now:
        return _Now()


class _BlockingStreamer:
    def __init__(self, *, recv_error: BaseException | None = None) -> None:
        self.recv_entered = threading.Event()
        self.release_recv = threading.Event()
        self.command_issued = threading.Event()
        self.commands: list[str] = []
        self.recv_calls = 0
        self.recv_error = recv_error

    def recv(self, _chunk, _metadata, *, timeout: float) -> int:
        assert timeout > 0.0
        self.recv_calls += 1
        self.recv_entered.set()
        assert self.release_recv.wait(2.0)
        if self.recv_error is not None:
            raise self.recv_error
        return 0

    @staticmethod
    def get_max_num_samps() -> int:
        return 16

    def issue_stream_cmd(self, command: _StreamCmd) -> None:
        self.commands.append(command.mode)
        self.command_issued.set()


class _PacketStreamer:
    def __init__(self, metadata) -> None:
        self._metadata = metadata
        self.requests: list[tuple[int, int]] = []
        self.sample_offset = 0

    @staticmethod
    def get_max_num_samps() -> int:
        return 4

    def recv(self, chunk, metadata, *, timeout: float) -> int:
        assert timeout > 0.0
        self.requests.append(tuple(chunk.shape))
        count = int(chunk.shape[1])
        values = np.arange(self.sample_offset, self.sample_offset + count)
        chunk[:] = values[None, :]
        self.sample_offset += count
        metadata.error_code = _ErrorCodes.none
        return count

    def issue_stream_cmd(self, _command: _StreamCmd) -> None:
        raise AssertionError("already-started test device must not issue a stream command")


class _InvalidPacketSizeStreamer(_PacketStreamer):
    @staticmethod
    def get_max_num_samps() -> int:
        return 0


class _OverreportingPacketStreamer(_PacketStreamer):
    def recv(self, chunk, metadata, *, timeout: float) -> int:
        super().recv(chunk, metadata, timeout=timeout)
        return int(chunk.shape[1]) + 1


class _UnknownMetadataPacketStreamer(_PacketStreamer):
    def recv(self, chunk, metadata, *, timeout: float) -> int:
        count = super().recv(chunk, metadata, timeout=timeout)
        metadata.error_code = _ErrorCodes.other
        return count


def _device(monkeypatch, streamer: _BlockingStreamer, *, started: bool = True):
    fake_uhd = SimpleNamespace(
        types=SimpleNamespace(StreamCMD=_StreamCmd, StreamMode=_StreamMode),
        libpyuhd=SimpleNamespace(
            types=SimpleNamespace(time_spec=_TimeSpec),
        ),
    )
    monkeypatch.setattr(device_module, "uhd", fake_uhd)
    monkeypatch.setattr(device_module, "_RXEC", _ErrorCodes)

    receiver = UsrpRxDevice.__new__(UsrpRxDevice)
    receiver._cfg = SimpleNamespace(
        channels=(0, 1), samples_per_chunk=16, sample_rate=4_000_000.0
    )
    receiver._usrp = _Usrp()
    receiver._rx_streamer = streamer
    receiver._metadata = SimpleNamespace(
        error_code=_ErrorCodes.timeout,
        out_of_sequence=False,
        has_time_spec=False,
    )
    receiver._stream_lock = threading.Lock()
    receiver._stopping = False
    receiver._started = started
    receiver._startup_recv_pending = False
    receiver._startup_recv_deadline_monotonic = 0.0
    return receiver


def test_recv_assembles_dsp_chunk_from_uhd_advertised_packet_size(monkeypatch) -> None:
    metadata = SimpleNamespace(
        error_code=_ErrorCodes.none,
        out_of_sequence=False,
        has_time_spec=False,
    )
    streamer = _PacketStreamer(metadata)
    receiver = _device(monkeypatch, streamer)
    receiver._cfg.samples_per_chunk = 10
    receiver._metadata = metadata

    result = receiver.recv_chunk()

    assert result.state == "ok"
    assert result.got_samples == 10
    assert result.chunk.shape == (2, 10)
    assert streamer.requests == [(2, 4), (2, 4), (2, 2)]
    np.testing.assert_array_equal(result.chunk[0].real, np.arange(10))


def test_recv_rejects_nonpositive_uhd_packet_size(monkeypatch) -> None:
    metadata = SimpleNamespace(
        error_code=_ErrorCodes.none,
        out_of_sequence=False,
        has_time_spec=False,
    )
    receiver = _device(monkeypatch, _InvalidPacketSizeStreamer(metadata))

    try:
        receiver.recv_chunk()
    except RuntimeError as exc:
        assert "nonpositive maximum packet size" in str(exc)
    else:
        raise AssertionError("invalid UHD packet size should be rejected")


def test_recv_rejects_uhd_sample_count_larger_than_requested(monkeypatch) -> None:
    metadata = SimpleNamespace(
        error_code=_ErrorCodes.none,
        out_of_sequence=False,
        has_time_spec=False,
    )
    receiver = _device(monkeypatch, _OverreportingPacketStreamer(metadata))

    try:
        receiver.recv_chunk()
    except RuntimeError as exc:
        assert "invalid sample count" in str(exc)
    else:
        raise AssertionError("overreported UHD sample count should be rejected")


def test_recv_does_not_label_unknown_uhd_error_with_samples_as_ok(monkeypatch) -> None:
    metadata = SimpleNamespace(
        error_code=_ErrorCodes.none,
        out_of_sequence=False,
        has_time_spec=False,
    )
    receiver = _device(monkeypatch, _UnknownMetadataPacketStreamer(metadata))

    result = receiver.recv_chunk()

    assert result.state == "other"
    assert result.got_samples > 0


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_stop_waits_until_inflight_recv_has_left_uhd(monkeypatch) -> None:
    streamer = _BlockingStreamer()
    receiver = _device(monkeypatch, streamer)
    result = []

    recv_thread = threading.Thread(target=lambda: result.append(receiver.recv_chunk()))
    recv_thread.start()
    assert streamer.recv_entered.wait(1.0)

    stop_thread = threading.Thread(target=receiver.stop)
    stop_thread.start()
    time.sleep(0.05)

    assert stop_thread.is_alive()
    assert streamer.commands == []
    streamer.release_recv.set()
    _join(recv_thread)
    _join(stop_thread)

    assert result[0].state == "timeout"
    assert streamer.commands == ["stop"]


def test_many_concurrent_stops_issue_one_native_stop(monkeypatch) -> None:
    streamer = _BlockingStreamer()
    receiver = _device(monkeypatch, streamer)
    threads = [threading.Thread(target=receiver.stop) for _ in range(64)]

    for thread in threads:
        thread.start()
    for thread in threads:
        _join(thread)

    assert streamer.commands == ["stop"]
    assert receiver._stopping is True


def test_recv_failure_releases_lock_for_startup_recovery_and_stop(monkeypatch) -> None:
    streamer = _BlockingStreamer(recv_error=OSError("simulated socket close"))
    receiver = _device(monkeypatch, streamer)
    caught: list[BaseException] = []

    def receive() -> None:
        try:
            receiver.recv_chunk()
        except BaseException as exc:
            caught.append(exc)

    recv_thread = threading.Thread(target=receive)
    recv_thread.start()
    assert streamer.recv_entered.wait(1.0)
    streamer.release_recv.set()
    _join(recv_thread)
    assert isinstance(caught[0], OSError)

    # A failed recv must not leave the mutex locked.  The startup retry can
    # stop/start the streamer and a later Stop can still close it once.
    receiver.restart_stream()
    assert streamer.commands == ["stop", "start"]
    receiver.stop()
    assert streamer.commands == ["stop", "start", "stop"]


def test_recv_after_stop_is_empty_and_does_not_reenter_uhd(monkeypatch) -> None:
    streamer = _BlockingStreamer()
    receiver = _device(monkeypatch, streamer)

    receiver.stop()
    result = receiver.recv_chunk()
    receiver.stop()

    assert result.state == "timeout"
    assert result.error_code == "stopping"
    assert result.chunk.shape == (2, 0)
    assert streamer.recv_calls == 0
    assert streamer.commands == ["stop"]


def test_failed_native_stop_is_retried_before_owner_can_be_released(monkeypatch) -> None:
    class FailsFirstStopStreamer(_BlockingStreamer):
        def __init__(self) -> None:
            super().__init__()
            self.stop_attempts = 0

        def issue_stream_cmd(self, command: _StreamCmd) -> None:
            if command.mode == "stop":
                self.stop_attempts += 1
                if self.stop_attempts == 1:
                    raise OSError("synthetic native stop failure")
            super().issue_stream_cmd(command)

    streamer = FailsFirstStopStreamer()
    receiver = _device(monkeypatch, streamer)

    try:
        receiver.stop()
    except OSError as exc:
        assert "synthetic native stop failure" in str(exc)
    else:
        raise AssertionError("first native stop should fail")

    assert receiver._stopping is True
    assert receiver._started is True

    receiver.stop()

    assert receiver._started is False
    assert streamer.stop_attempts == 2
    assert streamer.commands == ["stop"]
