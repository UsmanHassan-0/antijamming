from __future__ import annotations

from pathlib import Path
import socket
import sys
import threading
import time

import numpy as np
import pytest

from antijamming.app.headless import HeadlessRuntimeService
from antijamming.runtime.ipc import JsonIpcClient, JsonIpcServer, metrics_for_wire


def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


def _capture_exception(call, errors: list[Exception]) -> None:
    try:
        call()
    except Exception as exc:
        errors.append(exc)


def test_headless_module_does_not_import_qt() -> None:
    before = set(sys.modules)

    __import__("antijamming.app.headless")

    newly_loaded = set(sys.modules) - before
    assert not any(
        name == "PyQt6" or name.startswith("PyQt6.") for name in newly_loaded
    )


def test_wire_metrics_drop_iq_previews_and_convert_numpy() -> None:
    result = metrics_for_wire(
        {
            "ui_metrics_seq": np.int64(7),
            "doa_raw_spectrum": np.asarray([1.0, np.nan, 3.0]),
            "complex_samples": np.ones((4, 8192), dtype=np.complex64),
            "gnss_snapshot": {"tracking_count": np.int32(2)},
        }
    )

    assert result == {
        "ui_metrics_seq": 7,
        "doa_raw_spectrum": [1.0, None, 3.0],
        "gnss_snapshot": {"tracking_count": 2},
    }


def test_headless_service_stays_idle_until_explicit_start_command(tmp_path) -> None:
    created = []

    class FakeBackend:
        def __init__(self, **kwargs) -> None:
            self.running = False
            self.starts = 0
            self.stops = 0
            self.stopped = threading.Event()
            created.append(self)

        def is_running(self) -> bool:
            return self.running

        def start(self) -> None:
            self.starts += 1
            self.running = True
            self.stopped.clear()

        def stop(self, _reason: str) -> None:
            self.stops += 1
            self.running = False
            self.stopped.set()

        def wait(self, timeout=None) -> bool:
            return self.stopped.wait(timeout)

        def set_expected_sources(self, _count: int) -> None:
            pass

        def set_lcmv_test_enabled(self, _enabled: bool) -> None:
            pass

        def mark_rf_event(self, event: str, **kwargs) -> dict:
            return {"event": event, **kwargs}

    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-test")},
        tmp_path / "idle.sock",
        backend_factory=FakeBackend,  # type: ignore[arg-type]
    )
    backend = created[0]

    assert backend.starts == 0
    assert service._handle_command("ping", {})["backend_running"] is False
    assert backend.starts == 0

    service._handle_command("start", {"reason": "unit test"})
    assert backend.starts == 1
    service._handle_command("stop", {"reason": "unit test"})
    assert backend.stops == 1

    marker = service._handle_command(
        "mark_rf_event",
        {
            "event": "jammer_on",
            "notes": "unit test",
            "source": "gui",
        },
    )
    assert marker["event"]["event"] == "jammer_on"
    assert marker["event"]["notes"] == "unit test"


@pytest.mark.parametrize("invalid", [True, 1.0, "1", None])
def test_headless_rejects_noninteger_source_count(tmp_path, invalid) -> None:
    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-source-type-test")},
        tmp_path / "source-type.sock",
        backend_factory=lambda **_kwargs: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="integer 'count'"):
        service._handle_command("set_expected_sources", {"count": invalid})


@pytest.mark.parametrize("invalid", [0, 1, "false", None])
def test_headless_rejects_nonboolean_lcmv_state(tmp_path, invalid) -> None:
    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-lcmv-type-test")},
        tmp_path / "lcmv-type.sock",
        backend_factory=lambda **_kwargs: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="boolean 'enabled'"):
        service._handle_command("set_lcmv_test_enabled", {"enabled": invalid})


def test_headless_backend_monitor_start_failure_rolls_back_backend(
    tmp_path,
    monkeypatch,
) -> None:
    created = []

    class FakeBackend:
        def __init__(self, **_kwargs) -> None:
            self.running = False
            self.starts = 0
            self.stop_reasons: list[str] = []
            self.stopped = threading.Event()
            created.append(self)

        def is_running(self) -> bool:
            return self.running

        def start(self) -> None:
            self.starts += 1
            self.running = True
            self.stopped.clear()

        def stop(self, reason: str) -> None:
            self.stop_reasons.append(reason)
            self.running = False
            self.stopped.set()

        def wait(self, timeout=None) -> bool:
            return self.stopped.wait(timeout)

    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-monitor-start-test")},
        tmp_path / "monitor-start.sock",
        backend_factory=FakeBackend,  # type: ignore[arg-type]
    )
    real_start = threading.Thread.start

    def fail_monitor_start(thread) -> None:
        if thread.name == "antijam_backend_monitor":
            raise RuntimeError("synthetic monitor start failure")
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_monitor_start)

    with pytest.raises(RuntimeError, match="synthetic monitor start failure"):
        service._start_backend("unit test")

    backend = created[0]
    assert backend.starts == 1
    assert backend.stop_reasons == ["headless backend monitor startup failure"]
    assert backend.is_running() is False
    assert service._monitor_thread is None


def test_headless_does_not_start_while_prior_monitor_is_still_exiting(tmp_path) -> None:
    created = []

    class FakeBackend:
        def __init__(self, **_kwargs) -> None:
            self.starts = 0
            created.append(self)

        @staticmethod
        def is_running() -> bool:
            return False

        def start(self) -> None:
            self.starts += 1

    service = HeadlessRuntimeService(
        object(),  # type: ignore[arg-type]
        {"app": __import__("logging").getLogger("headless-monitor-race-test")},
        tmp_path / "monitor-race.sock",
        backend_factory=FakeBackend,  # type: ignore[arg-type]
    )
    release = threading.Event()
    prior_monitor = threading.Thread(target=release.wait)
    prior_monitor.start()
    service._monitor_thread = prior_monitor

    try:
        assert service._start_backend("racing restart") is False
        assert created[0].starts == 0
    finally:
        release.set()
        prior_monitor.join(timeout=1.0)


def test_json_ipc_round_trip_does_not_require_backend_or_hardware(tmp_path) -> None:
    commands: list[tuple[str, dict]] = []
    messages: list[dict] = []
    command_seen = threading.Event()

    def on_command(command: str, arguments: dict) -> dict:
        commands.append((command, arguments))
        command_seen.set()
        return {"accepted": True}

    server = JsonIpcServer(tmp_path / "headless.sock", on_command=on_command)
    client = JsonIpcClient(tmp_path / "headless.sock", on_message=messages.append)
    try:
        server.start()
        client.connect()
        client.command("ping", source="test")
        assert command_seen.wait(2.0)
        assert commands == [("ping", {"source": "test"})]

        server.publish_metrics(
            {
                "ui_metrics_seq": 11,
                "doa_raw_spectrum": np.asarray([0.25, 1.0]),
            }
        )
        _wait_for(
            lambda: any(
                message.get("type") == "metrics"
                and message.get("payload", {}).get("ui_metrics_seq") == 11
                for message in messages
            )
        )
    finally:
        client.close()
        assert server.close()

    assert client._reader is None


def test_json_ipc_client_close_interrupts_and_joins_reader(tmp_path) -> None:
    socket_path = tmp_path / "headless.sock"
    server = JsonIpcServer(socket_path, on_command=lambda _command, _args: {})
    client = JsonIpcClient(socket_path)
    server.start()
    try:
        client.connect()
        reader = client._reader
        assert reader is not None and reader.is_alive()
        assert client.close(timeout_s=1.0) is True
        assert not reader.is_alive()
        assert client._reader is None
        assert client._conn is None
    finally:
        client.close()
        server.close()


def test_json_ipc_client_close_prevents_late_connect_publication(
    tmp_path,
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    fake_instances = []

    class BlockingSocket:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False
            fake_instances.append(self)

        def connect(self, _path: str) -> None:
            entered.set()
            assert release.wait(2.0)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("antijamming.runtime.ipc.socket.socket", BlockingSocket)
    client = JsonIpcClient(tmp_path / "headless.sock")
    errors: list[Exception] = []
    connector = threading.Thread(
        target=lambda: _capture_exception(lambda: client.connect(), errors),
    )
    connector.start()
    assert entered.wait(1.0)

    assert client.close() is False
    release.set()
    connector.join(timeout=1.0)

    assert not connector.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert client._conn is None
    assert client._reader is None
    assert fake_instances[0].closed is True


def test_json_ipc_server_close_joins_acceptor_and_sessions(tmp_path) -> None:
    socket_path = tmp_path / "headless.sock"
    server = JsonIpcServer(socket_path, on_command=lambda _command, _args: {})
    clients = [JsonIpcClient(socket_path) for _ in range(8)]
    server.start()
    accept_thread = server._accept_thread
    assert accept_thread is not None

    try:
        for client in clients:
            client.connect()
        _wait_for(lambda: len(server._session_snapshot()) == len(clients))
    finally:
        server.close()
        for client in clients:
            client.close()

    assert not accept_thread.is_alive()
    assert server._session_snapshot() == []
    assert not socket_path.exists()


def test_json_ipc_server_accept_thread_start_failure_rolls_back_socket(
    tmp_path,
    monkeypatch,
) -> None:
    socket_path = tmp_path / "headless.sock"
    server = JsonIpcServer(socket_path, on_command=lambda _command, _args: {})
    real_start = threading.Thread.start
    injected = False

    def fail_first_accept_start(thread) -> None:
        nonlocal injected
        if thread.name == "antijam_ipc_accept" and not injected:
            injected = True
            raise RuntimeError("synthetic accept start failure")
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_first_accept_start)
    before_fds = len(list(Path("/proc/self/fd").iterdir()))

    with pytest.raises(RuntimeError, match="synthetic accept start failure"):
        server.start()

    assert len(list(Path("/proc/self/fd").iterdir())) == before_fds
    assert server._listener is None
    assert server._accept_thread is None
    assert not socket_path.exists()

    server.start()
    assert server.close()


def test_json_ipc_server_close_retains_live_acceptor_for_retry(tmp_path) -> None:
    class RetryingThread:
        ident = 1

        def __init__(self) -> None:
            self.join_calls = 0
            self.live = True

        def join(self, *, timeout: float) -> None:
            assert timeout == 1.0
            self.join_calls += 1

        def is_alive(self) -> bool:
            return self.live

    server = JsonIpcServer(tmp_path / "headless.sock", on_command=lambda _command, _args: {})
    acceptor = RetryingThread()
    server._accept_thread = acceptor  # type: ignore[assignment]

    assert server.close() is False
    assert server._accept_thread is acceptor

    acceptor.live = False
    assert server.close() is True
    assert server._accept_thread is None
    assert acceptor.join_calls == 1


def test_json_ipc_session_sender_start_failure_does_not_kill_acceptor(
    tmp_path,
    monkeypatch,
) -> None:
    socket_path = tmp_path / "headless.sock"
    server = JsonIpcServer(socket_path, on_command=lambda _command, _args: {})
    real_start = threading.Thread.start
    injected = False

    def fail_first_sender_start(thread) -> None:
        nonlocal injected
        if thread.name == "antijam_ipc_sender" and not injected:
            injected = True
            raise RuntimeError("synthetic sender start failure")
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_first_sender_start)
    server.start()
    first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = JsonIpcClient(socket_path)
    try:
        first.connect(str(socket_path))
        _wait_for(lambda: injected)
        _wait_for(lambda: server._session_snapshot() == [])
        client.connect()
        _wait_for(lambda: len(server._session_snapshot()) == 1)
        accept_thread = server._accept_thread
        assert accept_thread is not None and accept_thread.is_alive()
    finally:
        first.close()
        client.close()
        server.close()
