from __future__ import annotations

import errno
import fcntl
import io
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time

import numpy as np
import pytest

from antijamming.config import StreamConfig
from antijamming.gnss import GnssSdrBridge
from antijamming.gnss.sdr_bridge.bridge import GnssFifoWriteStall
from antijamming.gnss.gnss_sdr import (
    PRN_CARRIER_LOCK_THRESHOLD,
    PRN_CNO_MAX_PEAK_TO_PEAK_DB,
    PRN_CNO_MAX_STDEV_DB,
    PRN_CNO_MIN_STABLE_DB_HZ,
    PRN_CNO_REQUIRED_STABLE_WINDOWS,
    PRN_CNO_STABILITY_WINDOW,
    PVT_ACCURACY_TIMEOUT_S,
    SKY_GEOMETRY_TIMEOUT_S,
    USED_IN_FIX_TIMEOUT_S,
    _GnssSdrProcessInfo,
)
from antijamming.gnss.sdr_bridge.protobuf import gnss_synchro_pb2, monitor_pvt_pb2
from antijamming.runtime import BackendRuntime


def _loggers() -> dict[str, logging.Logger]:
    keys = ["app", "gnss", "errors"]
    return {key: logging.getLogger(f"test.gnss.{key}") for key in keys}


def _runtime_loggers() -> dict[str, logging.Logger]:
    keys = [
        "app",
        "hw",
        "stream",
        "transport",
        "phase",
        "doa",
        "gnss",
        "health",
        "errors",
    ]
    return {key: logging.getLogger(f"test.runtime.{key}") for key in keys}


def _fifo_runtime_dir(tmp_path: Path) -> Path:
    return tmp_path / "gnss-sdr" / "logs" / "runtime" / "fifo-x300"


def _pipe_backed_bridge(
    tmp_path: Path,
    *,
    source_count: int,
) -> tuple[GnssSdrBridge, list[int], list[int]]:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._fifo_paths = [tmp_path / f"source_{index}.fifo" for index in range(source_count)]
    read_fds: list[int] = []
    write_fds: list[int] = []
    for _ in range(source_count):
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        read_fds.append(read_fd)
        write_fds.append(write_fd)
    bridge._fifo_fds = list(write_fds)
    bridge._fifo_fd = write_fds[0]
    bridge._fifo_source_bytes = [0 for _ in range(source_count)]
    return bridge, read_fds, write_fds


def _close_pipe_fds(*groups: list[int]) -> None:
    for group in groups:
        for fd in group:
            try:
                os.close(fd)
            except OSError:
                pass


def test_fifo_fair_poll_writes_each_source_without_serial_blocking(
    tmp_path: Path,
) -> None:
    bridge, read_fds, write_fds = _pipe_backed_bridge(tmp_path, source_count=2)
    bridge._fifo_write_stall_timeout_s = 0.5
    samples = np.stack(
        [
            np.arange(16_384, dtype=np.float32).astype(np.complex64),
            (10_000 + np.arange(16_384, dtype=np.float32)).astype(np.complex64),
        ]
    )
    expected_bytes = samples.shape[1] * np.dtype(np.complex64).itemsize
    received = [bytearray(), bytearray()]

    def reader(index: int) -> None:
        while len(received[index]) < expected_bytes:
            chunk = os.read(read_fds[index], expected_bytes - len(received[index]))
            if not chunk:
                return
            received[index].extend(chunk)

    threads = [threading.Thread(target=reader, args=(index,)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        assert bridge.write(samples)
        for thread in threads:
            thread.join(timeout=1.0)
            assert not thread.is_alive()
        for index in range(2):
            np.testing.assert_array_equal(
                np.frombuffer(received[index], dtype=np.complex64),
                samples[index],
            )
    finally:
        _close_pipe_fds(write_fds, read_fds)


def test_fifo_fair_poll_identifies_one_stalled_reader_with_bounded_latency(
    tmp_path: Path,
) -> None:
    bridge, read_fds, write_fds = _pipe_backed_bridge(tmp_path, source_count=2)
    bridge._fifo_write_stall_timeout_s = 0.05
    for write_fd in write_fds:
        fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
    samples = np.ones((2, 8192), dtype=np.complex64)
    source_zero_done = threading.Event()

    def drain_source_zero() -> None:
        expected_bytes = samples.shape[1] * np.dtype(np.complex64).itemsize
        received = 0
        while received < expected_bytes:
            chunk = os.read(read_fds[0], expected_bytes - received)
            if not chunk:
                return
            received += len(chunk)
        source_zero_done.set()

    reader = threading.Thread(target=drain_source_zero)
    started_at = time.monotonic()
    try:
        reader.start()
        with pytest.raises(GnssFifoWriteStall) as caught:
            bridge.write(samples)
        elapsed_s = time.monotonic() - started_at
        assert elapsed_s < 0.5
        assert caught.value.stalled_sources == (1,)
        assert caught.value.stalled_paths == (tmp_path / "source_1.fifo",)
        assert len(caught.value.pending_bytes) == 1
        assert caught.value.pending_bytes[0] > 0
        assert "sources=1" in str(caught.value)
        assert "source_1.fifo" in str(caught.value)
        assert bridge._drop_count == 1
    finally:
        _close_pipe_fds(write_fds, read_fds)
        reader.join(timeout=1.0)


def _tracking_monitor_message(
    *,
    cno_db_hz: float,
    prn: int,
    carrier_lock_test: float = 1.0,
    channel: int = 0,
    valid_pseudorange: bool = False,
    cycle_slip: bool = False,
) -> gnss_synchro_pb2.Observables:
    message = gnss_synchro_pb2.Observables()
    observable = message.observable.add()
    observable.system = "G"
    observable.signal = "1C"
    observable.prn = int(prn)
    observable.channel_id = int(channel)
    observable.cn0_db_hz = float(cno_db_hz)
    observable.carrier_doppler_hz = -1350.25
    observable.carrier_phase_rads = 123_456.75
    observable.code_phase_samples = 12.0
    observable.prompt_i = 0.75
    observable.prompt_q = -0.25
    observable.fs = 4_000_000
    observable.tracking_sample_counter = 123_456
    observable.correlation_length_ms = 1
    observable.flag_valid_symbol_output = True
    observable.flag_valid_word = False
    observable.flag_valid_pseudorange = bool(valid_pseudorange)
    observable.pseudorange_m = 21_234_567.0
    observable.rx_time = 345_600.5
    observable.flag_PLL_180_deg_phase_locked = carrier_lock_test >= PRN_CARRIER_LOCK_THRESHOLD
    observable.flag_cycle_slip = bool(cycle_slip)
    return message


def _observables_monitor_message(
    *,
    prn: int,
    cn0_db_hz: float = 42.5,
    pseudorange_m: float = 21_234_567.0,
    carrier_doppler_hz: float = -1350.25,
    carrier_phase_rads: float = 123_456.75,
    valid_pseudorange: bool = True,
    channel: int = 0,
) -> gnss_synchro_pb2.Observables:
    message = gnss_synchro_pb2.Observables()
    observable = message.observable.add()
    observable.system = "G"
    observable.signal = "1C"
    observable.prn = int(prn)
    observable.channel_id = int(channel)
    observable.cn0_db_hz = float(cn0_db_hz)
    observable.carrier_doppler_hz = float(carrier_doppler_hz)
    observable.carrier_phase_rads = float(carrier_phase_rads)
    observable.pseudorange_m = float(pseudorange_m)
    observable.rx_time = 345_600.5
    observable.tow_at_current_symbol_ms = 12_000
    observable.flag_valid_symbol_output = True
    observable.flag_valid_pseudorange = bool(valid_pseudorange)
    return message


def _monitor_pvt_message(
    *,
    lat_deg: float,
    lon_deg: float,
    height_m: float,
    valid_sats: int = 6,
    hdop: float = 1.2,
    vdop: float = 1.8,
    pdop: float = 2.0,
    gdop: float = 2.4,
) -> monitor_pvt_pb2.MonitorPvt:
    message = monitor_pvt_pb2.MonitorPvt()
    message.tow_at_current_symbol_ms = 123000
    message.week = 2400
    message.rx_time = 123.0
    message.user_clk_offset = 0.01
    message.pos_x = 1.0
    message.pos_y = 2.0
    message.pos_z = 3.0
    message.vel_x = 0.1
    message.vel_y = 0.2
    message.vel_z = 0.3
    message.cov_xx = 0.5
    message.cov_yy = 0.6
    message.cov_zz = 0.7
    message.cov_xy = 0.8
    message.cov_yz = 0.9
    message.cov_zx = 1.0
    message.latitude = float(lat_deg)
    message.longitude = float(lon_deg)
    message.height = float(height_m)
    message.valid_sats = int(valid_sats)
    message.solution_status = 1
    message.solution_type = 0
    message.ar_ratio_factor = 2.5
    message.ar_ratio_threshold = 3.5
    message.gdop = float(gdop)
    message.pdop = float(pdop)
    message.hdop = float(hdop)
    message.vdop = float(vdop)
    message.user_clk_drift_ppm = 0.25
    message.utc_time = "2026-06-18T07:00:00Z"
    message.vel_e = 0.4
    message.vel_n = 0.5
    message.vel_u = 0.6
    message.cog = 45.0
    message.geohash = "testhash"
    return message


def _make_tracking_bridge(tmp_path: Path) -> GnssSdrBridge:
    template_path = tmp_path / "fifo.conf.template"
    template_path.write_text(
        "SignalSource.filename={fifo_path}\n"
        "GNSS-SDR.internal_fs_sps={internal_fs_sps}\n",
        encoding="utf-8",
    )
    cfg = StreamConfig(
        gnss_sdr_config_template=template_path,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._session_epoch_s = 1.0
    bridge._tracking_outputs_dir.mkdir(parents=True, exist_ok=True)
    return bridge


def _append_tracking_monitor_sample(
    bridge: GnssSdrBridge,
    *,
    cno_db_hz: float,
    prn: int,
    carrier_lock_test: float = 1.0,
    channel: int = 0,
) -> None:
    message = _tracking_monitor_message(
        cno_db_hz=cno_db_hz,
        prn=prn,
        carrier_lock_test=carrier_lock_test,
        channel=channel,
    )
    bridge._handle_observables_message(message, source="tracking")


def _feed_tracking_cno(
    bridge: GnssSdrBridge,
    values: list[float] | tuple[float, ...],
    *,
    prn: int,
    carrier_lock_test: float = 1.0,
    channel: int = 0,
) -> None:
    for cno_db_hz in values:
        _append_tracking_monitor_sample(
            bridge,
            cno_db_hz=cno_db_hz,
            prn=prn,
            carrier_lock_test=carrier_lock_test,
            channel=channel,
        )
        bridge.snapshot()


def _confirm_gps_nav(
    bridge: GnssSdrBridge,
    *,
    prn: int = 8,
    channel: int = 0,
    cno_db_hz: float = 38.0,
) -> None:
    bridge._handle_runtime_line(
        f"New GPS NAV message received in channel {channel}: subframe 2 from satellite "
        f"GPS PRN {prn:02d} (Block IIF) with CN0={cno_db_hz:.2f} dB-Hz"
    )


def test_bridge_prefers_product_local_executable(tmp_path: Path) -> None:
    install_dir = tmp_path / "gnss-sdr" / "install"
    exe_path = install_dir / "gnss-sdr"
    exe_path.parent.mkdir(parents=True)
    exe_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    exe_path.chmod(0o755)

    template_path = tmp_path / "fifo.conf.template"
    template_path.write_text(
        "SignalSource.filename={fifo_path}\n"
        "GNSS-SDR.internal_fs_sps={internal_fs_sps}\n"
        "PVT.dump_filename={output_dir}/PVT\n"
        "SignalSource.sample_type={sample_type}\n",
        encoding="utf-8",
    )

    cfg = StreamConfig(
        gnss_sdr_install_dir=install_dir,
        gnss_sdr_build_dir=tmp_path / "build-antijamming",
        gnss_sdr_repo_dir=tmp_path / "gnss-sdr",
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_config_template=template_path,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    assert bridge._resolve_local_executable() == exe_path.resolve()


def test_bridge_product_profile_uses_udp_and_has_no_dump_parsers(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=False,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    assert bridge._cfg.gnss_pvt_monitor_enable is True
    assert bridge._cfg.gnss_monitor_enable is True
    assert bridge._cfg.gnss_tracking_monitor_enable is True
    assert bridge._cfg.gnss_pvt_nmea_tty_enable is True
    assert bridge._cfg.gnss_pvt_nmea_output_file_enable is False
    assert bridge._cfg.gnss_pvt_nmea_rate_ms == 1000
    assert not hasattr(bridge, "_refresh_pvt_output_state")
    assert not hasattr(bridge, "_refresh_observables_output_state")


def test_bridge_launch_args_capture_gnss_sdr_logs_on_stable_console(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=False,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    args = bridge._gnss_sdr_launch_args(Path("/repo/gnss-sdr/install/gnss-sdr"))
    command_args = bridge._gnss_sdr_command_args(Path("/repo/gnss-sdr/install/gnss-sdr"))

    assert "/repo/gnss-sdr/install/gnss-sdr" in command_args
    if command_args[0].endswith("stdbuf"):
        assert command_args[1:3] == ["-oL", "-eL"]
    assert f"--config_file={bridge._config_path}" in command_args
    assert "--logtostderr=1" in command_args
    assert "--minloglevel=1" in command_args
    assert all(not arg.startswith("--log_dir=") for arg in command_args)
    assert "antijamming.gnss.sdr_bridge.parent_guard" in args
    assert "--parent-pid" in args
    assert args[-len(command_args):] == command_args


def test_fifo_startup_without_deadline_waits_while_process_is_alive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_startup_timeout_s=0.0,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    class LiveProcess:
        @staticmethod
        def poll() -> None:
            return None

    bridge._proc = LiveProcess()  # type: ignore[assignment]
    outcomes: list[OSError | int] = [
        OSError(errno.ENXIO, "reader not ready"),
        OSError(errno.ENXIO, "reader not ready"),
        73,
    ]

    def fake_open(_path: Path, _flags: int) -> int:
        outcome = outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.os.open", fake_open)
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.os.set_blocking", lambda *_: None)
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.time.sleep", lambda *_: None)
    monkeypatch.setattr(bridge, "_configure_pipe", lambda _fd: None)

    assert bridge._open_fifo_writer(cfg.gnss_sdr_startup_timeout_s) == 73
    assert outcomes == []


def test_fifo_startup_finite_deadline_reports_cold_fftw_hint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    class LiveProcess:
        @staticmethod
        def poll() -> None:
            return None

    bridge._proc = LiveProcess()  # type: ignore[assignment]
    monotonic_values = iter([100.0, 100.0, 101.1])

    def reader_not_ready(_path: Path, _flags: int) -> int:
        raise OSError(errno.ENXIO, "reader not ready")

    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.os.open", reader_not_ready)
    monkeypatch.setattr(
        "antijamming.gnss.sdr_bridge.fifo.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.time.sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="cold FFTW plan"):
        bridge._open_fifo_writer(timeout_s=1.0)


def test_fifo_startup_reports_cold_cache_progress_and_completion_to_console(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    class LiveProcess:
        @staticmethod
        def poll() -> None:
            return None

    bridge._proc = LiveProcess()  # type: ignore[assignment]
    outcomes: list[OSError | int] = [
        OSError(errno.ENXIO, "reader not ready"),
        73,
    ]
    monotonic_values = iter([100.0, 111.0, 112.0])

    def fake_open(_path: Path, _flags: int) -> int:
        outcome = outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    monkeypatch.setenv("ANTIJAM_GNSS_STARTUP_CONSOLE", "1")
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.os.open", fake_open)
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.os.set_blocking", lambda *_: None)
    monkeypatch.setattr("antijamming.gnss.sdr_bridge.fifo.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "antijamming.gnss.sdr_bridge.fifo.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(bridge, "_configure_pipe", lambda _fd: None)

    assert bridge._open_fifo_writer(timeout_s=0.0) == 73
    output = capsys.readouterr().out
    assert "FFTW is measuring missing plans" in output
    assert "one-time cache build" in output
    assert "GNSS-SDR ready after 12.0s" in output
    assert ".gr_fftw_wisdom" in output


def test_parent_guard_execs_requested_command(monkeypatch) -> None:
    from antijamming.gnss.sdr_bridge import parent_guard

    executed: dict[str, object] = {}

    monkeypatch.setattr(parent_guard, "_set_parent_death_signal", lambda _signum: None)
    monkeypatch.setattr(parent_guard.os, "getppid", lambda: 1234)

    def fake_execvp(program: str, args: list[str]) -> None:
        executed["program"] = program
        executed["args"] = list(args)
        raise SystemExit(0)

    monkeypatch.setattr(parent_guard.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit):
        parent_guard.main(["--parent-pid", "1234", "--", "/bin/echo", "ok"])

    assert executed == {"program": "/bin/echo", "args": ["/bin/echo", "ok"]}


def test_bridge_detects_only_matching_gnss_sdr_runtime_processes(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    matching_config = str(bridge._config_path)
    matching_runtime = str(bridge._runtime_dir)

    assert bridge._cmdline_matches_runtime(
        ("/home/qvise/antijamming/gnss-sdr/install/gnss-sdr", f"--config_file={matching_config}")
    )
    assert bridge._cmdline_matches_runtime(
        ("/home/qvise/antijamming/gnss-sdr/install/gnss-sdr", f"--log_dir={matching_runtime}/glog")
    )
    legacy_config = (
        cfg.gnss_sdr_repo_dir.expanduser().resolve()
        / "logs"
        / "runtime"
        / "fifo-x300"
        / "fifo_gps_l1.conf"
    )
    assert bridge._cmdline_matches_runtime(
        ("/home/qvise/antijamming/gnss-sdr/install/gnss-sdr", f"--config_file={legacy_config}")
    )
    assert not bridge._cmdline_matches_runtime(
        ("/home/qvise/other/gnss-sdr", "--config_file=/home/qvise/other/fifo_gps_l1.conf")
    )
    assert not bridge._cmdline_matches_runtime(
        ("/usr/bin/python", f"--config_file={matching_config}")
    )


def test_bridge_stale_cleanup_targets_only_matching_processes(tmp_path: Path, monkeypatch) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    matching = _GnssSdrProcessInfo(
        pid=101,
        cmdline=("/home/qvise/antijamming/gnss-sdr/install/gnss-sdr", f"--config_file={bridge._config_path}"),
    )
    unrelated = _GnssSdrProcessInfo(
        pid=202,
        cmdline=("/home/qvise/other/gnss-sdr", "--config_file=/home/qvise/other/fifo.conf"),
    )
    terminated: list[int] = []

    monkeypatch.setattr(bridge, "_matching_gnss_sdr_processes", lambda: [matching])
    monkeypatch.setattr(
        bridge,
        "_terminate_stale_process",
        lambda process: terminated.append(process.pid),
    )

    bridge._terminate_matching_stale_processes()

    assert terminated == [matching.pid]
    assert unrelated.pid not in terminated


def test_bridge_drain_stdout_writes_clean_console_log_parses_and_stays_quiet_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=False,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge._proc = object()  # type: ignore[assignment]
    bridge._stdout_handle = io.StringIO("Current receiver time: 12 s\n")

    bridge._drain_stdout()

    assert bridge._console_log_path.read_text(encoding="utf-8") == (
        "Current receiver time: 12 s\n"
    )
    assert bridge._receiver_log_path.read_text(encoding="utf-8") == ""
    assert bridge.snapshot()["receiver_time_s"] == 12
    assert capsys.readouterr().out == ""


def test_bridge_drain_stdout_can_echo_clean_console_lines(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=True,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge._proc = object()  # type: ignore[assignment]
    bridge._stdout_handle = io.StringIO("Current receiver time: 13 s\n")

    bridge._drain_stdout()

    assert bridge._console_log_path.read_text(encoding="utf-8") == (
        "Current receiver time: 13 s\n"
    )
    assert bridge._receiver_log_path.read_text(encoding="utf-8") == ""
    assert bridge.snapshot()["receiver_time_s"] == 13
    assert capsys.readouterr().out == "Current receiver time: 13 s\n"


def test_bridge_drain_stdout_normalizes_carriage_return_console_records(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=True,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge._proc = object()  # type: ignore[assignment]
    bridge._stdout_handle = io.StringIO(
        "Current receiver time: 14 s\r"
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 13 (Block IIR) in channel 0\r"
    )

    bridge._drain_stdout()

    expected = (
        "Current receiver time: 14 s\n"
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 13 (Block IIR) in channel 0\n"
    )
    assert bridge._console_log_path.read_text(encoding="utf-8") == expected
    assert bridge._receiver_log_path.read_text(encoding="utf-8") == ""
    assert capsys.readouterr().out == expected
    snapshot = bridge.snapshot()
    assert snapshot["receiver_time_s"] == 14
    assert snapshot["tracking_prns"] == [13]


def test_bridge_drain_stdout_routes_glog_diagnostics_to_receiver_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    glog_line = (
        "I20260521 11:18:52.939307 pcps_acquisition.cc:307] "
        "Acquisition decision: negative, satellite G 9, test_statistics 21.874, threshold 38.7484\n"
    )
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=True,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge._proc = object()  # type: ignore[assignment]
    bridge._stdout_handle = io.StringIO(glog_line)

    bridge._drain_stdout()

    assert bridge._console_log_path.read_text(encoding="utf-8") == ""
    assert bridge._receiver_log_path.read_text(encoding="utf-8") == glog_line
    assert bridge.snapshot()["prns"][0]["prn"] == 9
    assert capsys.readouterr().out == ""


def test_bridge_routes_solver_diagnostics_and_fragments_out_of_console_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_sdr_log_dir=_fifo_runtime_dir(tmp_path) / "glog",
        gnss_sdr_echo_stdout=True,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge._proc = object()  # type: ignore[assignment]
    bridge._stdout_handle = io.StringIO(
        "i-square error nv=10 vv=18.0 cs=16.3)\n"
        "5)\n"
        "RTKLIB_PVT_RESIDUAL_SUMMARY tow_s=123.000 week=2400 sol_ns=14 resp_rms_m=0.450\n"
        "Current receiver time: 5 min 50 s\n"
    )

    bridge._drain_stdout()

    assert bridge._console_log_path.read_text(encoding="utf-8") == (
        "Current receiver time: 5 min 50 s\n"
    )
    receiver_log = bridge._receiver_log_path.read_text(encoding="utf-8")
    assert "i-square error nv=10 vv=18.0 cs=16.3)\n" in receiver_log
    assert "5)\n" in receiver_log
    assert "RTKLIB_PVT_RESIDUAL_SUMMARY tow_s=123.000" in receiver_log
    assert bridge.snapshot()["receiver_time_s"] == 350
    assert capsys.readouterr().out == "Current receiver time: 5 min 50 s\n"


def test_bridge_parses_receiver_time_after_one_hour(tmp_path: Path) -> None:
    bridge = GnssSdrBridge(
        StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path)),
        _loggers(),
    )

    bridge._handle_runtime_line("Current receiver time: 1 h 8 min 21 s")

    assert bridge.snapshot()["receiver_time_s"] == 4101


def test_bridge_renders_fifo_config_with_runtime_paths(tmp_path: Path) -> None:
    template_path = tmp_path / "fifo.conf.template"
    template_path.write_text(
        "SignalSource.filename={fifo_path}\n"
        "GNSS-SDR.internal_fs_sps={internal_fs_sps}\n"
        "PVT.dump_filename={output_dir}/PVT\n"
        "SignalSource.sample_type={sample_type}\n",
        encoding="utf-8",
    )

    cfg = StreamConfig(
        sample_rate=8e6,
        gnss_shared_u1_phase_compensation_enabled=False,
        gnss_sdr_config_template=template_path,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    rendered = bridge._render_config()

    assert str(cfg.gnss_sdr_runtime_dir / "gnss_iq.fifo") in rendered
    assert "GNSS-SDR.internal_fs_sps=8000000" in rendered
    assert "PVT.dump_filename=outputs/PVT" in rendered
    assert f"SignalSource.sample_type={cfg.gnss_sdr_sample_type}" in rendered


def test_bridge_fifo_config_derives_gps_l1_filter_at_4mhz(tmp_path: Path) -> None:
    cfg = StreamConfig(
        sample_rate=4e6,
        gnss_shared_u1_phase_compensation_enabled=False,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    rendered = bridge._render_config()

    assert "SignalSource.implementation=Fifo_Signal_Source" in rendered
    assert "SignalSource.dump_filename=./outputs/signal_source/signal_source.dat" in rendered
    assert "GNSS-SDR.internal_fs_sps=4000000" in rendered
    assert "SignalConditioner.implementation=Signal_Conditioner" in rendered
    assert "DataTypeAdapter.implementation=Pass_Through" in rendered
    assert "DataTypeAdapter.item_type=gr_complex" in rendered
    assert "InputFilter.implementation=Freq_Xlating_Fir_Filter" in rendered
    assert "InputFilter.number_of_taps=" not in rendered
    assert "InputFilter.dump_filename=./outputs/signal_conditioner/input_filter.dat" in rendered
    assert "InputFilter.filter_type=lowpass" in rendered
    assert "InputFilter.bw=1385000" in rendered
    assert "InputFilter.tw=175000" in rendered
    assert "InputFilter.IF=0" in rendered
    assert "InputFilter.decimation_factor=1" in rendered
    assert "Resampler.sample_freq_in=4000000" in rendered
    assert "Resampler.dump_filename=./outputs/signal_conditioner/resampler.dat" in rendered
    assert "PVT.output_path=./outputs/pvt" in rendered
    assert "PVT.dump_filename=pvt" in rendered
    assert bridge.input_filter_bandwidth_hz == 2_600_000.0


def test_bridge_archives_exact_pid_scoped_gnss_runtime_artifacts(tmp_path: Path) -> None:
    runtime_dir = _fifo_runtime_dir(tmp_path)
    session_dir = tmp_path / "runs" / "session"
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=runtime_dir,
        gnss_sdr_log_dir=runtime_dir / "glog",
    )
    bridge = GnssSdrBridge(
        cfg,
        _loggers(),
        session_id="session",
        session_dir=session_dir,
    )
    bridge._config_path.parent.mkdir(parents=True, exist_ok=True)
    bridge._config_path.write_text("exact-current-config", encoding="utf-8")
    bridge._console_log_path.write_text("exact-current-console", encoding="utf-8")
    bridge._receiver_log_path.parent.mkdir(parents=True, exist_ok=True)
    bridge._receiver_log_path.write_text("exact-current-receiver", encoding="utf-8")
    bridge._pvt_outputs_dir.mkdir(parents=True, exist_ok=True)
    (bridge._pvt_outputs_dir / "current.gpx").write_text("exact-current-pvt", encoding="utf-8")

    bridge._archive_runtime_artifacts(config_only=False)

    assert (
        session_dir / "gnss-sdr/runtime/fifo_gps_l1.conf"
    ).read_text(encoding="utf-8") == "exact-current-config"
    assert (
        session_dir / "gnss-sdr/runtime/console.log"
    ).read_text(encoding="utf-8") == "exact-current-console"
    assert (
        session_dir / "gnss-sdr/glog/receiver.log"
    ).read_text(encoding="utf-8") == "exact-current-receiver"
    assert (
        session_dir / "gnss-sdr/runtime/outputs/pvt/current.gpx"
    ).read_text(encoding="utf-8") == "exact-current-pvt"


def test_bridge_gps_l1_filter_keeps_physical_bandwidth_when_rate_changes(tmp_path: Path) -> None:
    cfg = StreamConfig(
        sample_rate=8e6,
        gnss_shared_u1_phase_compensation_enabled=False,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    rendered = bridge._render_config()

    assert "InputFilter.number_of_taps=" not in rendered
    assert "InputFilter.bw=1385000" in rendered
    assert "InputFilter.tw=175000" in rendered
    assert bridge.input_filter_bandwidth_hz == 2_600_000.0


def test_bridge_gps_l1_filter_rejects_rate_below_stopband(tmp_path: Path) -> None:
    cfg = StreamConfig(
        sample_rate=3e6,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    with pytest.raises(ValueError, match="cannot place the GPS L1 input-filter stopband"):
        bridge._render_config()


def test_bridge_gps_l1_filter_auto_design_meets_response_limits() -> None:
    from gnuradio.filter import firdes

    from antijamming.gnss.sdr_bridge.constants import (
        GNSS_INPUT_FILTER_CUTOFF_HZ,
        GNSS_INPUT_FILTER_PASSBAND_RIPPLE_DB,
        GNSS_INPUT_FILTER_STOPBAND_ATTENUATION_DB,
        GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ,
        GPS_L1_CA_PROCESSING_BANDWIDTH_HZ,
    )

    sample_rate_hz = 4_000_000.0
    coefficients = np.asarray(
        firdes.low_pass(
            1.0,
            sample_rate_hz,
            GNSS_INPUT_FILTER_CUTOFF_HZ,
            GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ,
        )
    )
    response = np.fft.rfft(coefficients, n=1 << 20)
    frequency_hz = np.linspace(0.0, sample_rate_hz / 2.0, response.size)
    response_db = 20.0 * np.log10(np.maximum(np.abs(response), 1e-15))
    passband = response_db[
        frequency_hz <= GPS_L1_CA_PROCESSING_BANDWIDTH_HZ / 2.0
    ]
    stopband = response_db[frequency_hz >= 1_500_000.0]
    ripple_db = float(np.ptp(passband))
    stopband_max_db = float(np.max(stopband))

    # GNSS-SDR authors only bw/tw; GNU Radio chooses this length. Verify the
    # exact generated response rather than supplying an explicit tap count.
    assert len(coefficients) == 55
    assert ripple_db == pytest.approx(0.443464, abs=1e-5)
    assert stopband_max_db == pytest.approx(-43.702208, abs=1e-5)
    assert ripple_db <= GNSS_INPUT_FILTER_PASSBAND_RIPPLE_DB
    assert stopband_max_db <= -GNSS_INPUT_FILTER_STOPBAND_ATTENUATION_DB


def test_bridge_renders_default_gps_l1_baseline_settings(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_1c_channel_count=10,
        gnss_channels_in_acquisition=30,
        gnss_acquisition_pfa=0.01,
        gnss_acquisition_doppler_max_hz=5000,
        gnss_acquisition_doppler_step_hz=500,
        gnss_acquisition_max_dwells=1,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._nmea_tty_path = "/dev/pts/77"

    rendered = bridge._render_config()

    assert "Channels_1C.count=10" in rendered
    assert "Channels.in_acquisition=10" in rendered
    assert "Channel0.signal=1C" in rendered
    assert "Channel9.signal=1C" in rendered
    assert "PVT.positioning_mode=PPP_Static" in rendered
    assert "PVT.positioning_mode=Single" not in rendered
    assert "GNSS-SDR.tow_to_trk=true" in rendered
    assert "GNSS-SDR.AGNSS_XML_enabled=false" in rendered
    assert "GNSS-SDR.AGNSS_ref_location=" in rendered
    assert "GNSS-SDR.AGNSS_ref_utc_time=" in rendered
    assert (
        f"GNSS-SDR.AGNSS_gps_ephemeris_xml={cfg.gnss_agnss_gps_ephemeris_xml}"
        in rendered
    )
    assert "PVT.dump=false" in rendered
    assert "PVT.dump_mat=false" in rendered
    assert "PVT.dump_filename=pvt" in rendered
    assert "PVT.enable_monitor=true" in rendered
    assert "PVT.monitor_client_addresses=127.0.0.1" in rendered
    assert "PVT.monitor_udp_port=1111" in rendered
    assert "PVT.enable_protobuf=true" in rendered
    assert "PVT.log_rtklib_residuals=true" in rendered
    assert "PVT.rtklib_residual_log_period_ms=1000" in rendered
    assert "PVT.nmea_output_file_enabled=false" in rendered
    assert "PVT.nmea_rate_ms=1000" in rendered
    assert "PVT.flag_nmea_tty_port=true" in rendered
    assert "PVT.nmea_dump_devname=/dev/pts/77" in rendered
    assert "PVT.nmea_dump_devname=/dev/pts/4" not in rendered
    assert "Monitor.enable_monitor=true" in rendered
    assert "Monitor.enable_protobuf=true" in rendered
    assert "Monitor.client_addresses=127.0.0.1" in rendered
    assert "Monitor.udp_port=1112" in rendered
    assert "Monitor.decimation_factor=1" in rendered
    assert "TrackingMonitor.enable_monitor=true" in rendered
    assert "TrackingMonitor.enable_protobuf=true" in rendered
    assert "TrackingMonitor.client_addresses=127.0.0.1" in rendered
    assert "TrackingMonitor.udp_port=1236" in rendered
    assert "TrackingMonitor.decimation_factor=10" in rendered
    assert "PVT.monitor_udp_port=1234" not in rendered
    assert "Monitor.udp_port=1234" not in rendered
    assert "Observables.dump=false" in rendered
    assert "Observables.dump_filename=./outputs/observables/observables.dat" in rendered
    assert "Acquisition_1C.pfa=0.01" in rendered
    assert "Acquisition_1C.doppler_max=5000" in rendered
    assert "Acquisition_1C.doppler_step=500" in rendered
    assert "Acquisition_1C.max_dwells=1" in rendered
    assert "Acquisition_1C.dump=false" in rendered
    assert "Tracking_1C.dump=false" in rendered
    assert "Tracking_1C.dump_mat=false" in rendered
    assert "Tracking_1C.pll_bw_hz=35.0" in rendered
    assert "Tracking_1C.dll_bw_hz=0.5" in rendered
    assert "Tracking_1C.pll_filter_order=3" in rendered
    assert "Tracking_1C.dll_filter_order=2" in rendered
    assert "Tracking_1C.early_late_space_chips=0.25" in rendered
    assert "Tracking_1C.early_late_space_narrow_chips=0.15" in rendered
    assert "Tracking_1C.pll_bw_narrow_hz=5.0" in rendered
    assert "Tracking_1C.dll_bw_narrow_hz=0.75" in rendered
    assert "Tracking_1C.extend_correlation_symbols=1" in rendered
    assert "Tracking_1C.enable_fll_pull_in=true" in rendered
    assert "Tracking_1C.enable_fll_steady_state=false" in rendered
    assert "Tracking_1C.fll_bw_hz=10.0" in rendered
    assert "Tracking_1C.pull_in_time_s=2" in rendered
    assert "Tracking_1C.bit_synchronization_time_limit_s=30" in rendered
    assert "Tracking_1C.cn0_min=25" in rendered
    assert PRN_CNO_MIN_STABLE_DB_HZ == 25.0
    assert "Tracking_1C.cn0_samples=" not in rendered


def test_bridge_forces_gnss_sdr_dumps_off(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    rendered = bridge._render_config()

    assert "Tracking_1C.dump=false" in rendered
    assert "PVT.dump=false" in rendered
    assert "Observables.dump=false" in rendered
    assert "Acquisition_1C.dump=false" in rendered
    assert "Tracking_1C.max_lock_fail=" not in rendered
    assert "Tracking_1C.max_carrier_lock_fail=" not in rendered
    assert "Tracking_1C.carrier_lock_th=" not in rendered


def test_bridge_tracks_gps_and_beidou_prns_without_number_collision(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Tracking of GPS L1 C/A signal started on channel 0 for satellite GPS PRN 12"
    )
    bridge._handle_runtime_line(
        "Tracking of BeiDou B1 signal started on channel 10 for satellite BeiDou PRN C12"
    )
    bridge._handle_runtime_line(
        "New BeiDou B1 NAV message received in channel 10 from satellite "
        "BeiDou PRN C12 with CN0=39.5 dB-Hz"
    )
    bridge._handle_nmea_line("$GPGSV,1,1,01,12,30,010,37*00")
    bridge._handle_nmea_line("$GBGSV,1,1,01,12,40,020,38*00")
    bridge._handle_nmea_line("$GBGSA,A,3,12,,,,,,,,,,,,1.0,1.0,1.0*00")

    snapshot = bridge.snapshot()

    assert [entry.get("satellite_id", f"G{entry['prn']:02d}") for entry in snapshot["prns"]] == [
        "G12",
        "C12",
    ]
    assert [entry.get("satellite_id", f"G{entry['prn']:02d}") for entry in snapshot["sky_prns"]] == [
        "G12",
        "C12",
    ]
    beidou = snapshot["sky_prns"][1]
    assert beidou["constellation"] == "beidou"
    assert beidou["used_in_fix"] is True
    assert snapshot["prns"][1]["cno_db_hz"] == 39.5
    assert snapshot["used_in_fix_prns"] == [12]
    assert snapshot["used_in_fix_satellites"] == ["C12"]


def test_bridge_reports_constellation_labels_for_duplicate_used_pvt_prns(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Tracking of GPS L1 C/A signal started on channel 0 for satellite GPS PRN 05"
    )
    bridge._handle_runtime_line(
        "Tracking of BeiDou B1 signal started on channel 10 for satellite BeiDou PRN C05"
    )
    bridge._handle_nmea_line("$GPGSA,A,3,05,,,,,,,,,,,,1.0,1.0,1.0*00")
    bridge._handle_nmea_line("$GBGSA,A,3,05,,,,,,,,,,,,1.0,1.0,1.0*00")

    snapshot = bridge.snapshot()

    assert snapshot["used_in_fix_count"] == 2
    assert snapshot["used_in_fix_prns"] == [5, 5]
    assert snapshot["used_in_fix_satellites"] == ["G05", "C05"]
    assert [
        (entry.get("satellite_id", f"G{entry['prn']:02d}"), entry["used_in_fix"])
        for entry in snapshot["prns"]
    ] == [("G05", True), ("C05", True)]


def test_bridge_tracks_supported_non_gps_constellations_from_logs_and_nmea(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Tracking of BeiDou B1 signal started on channel 11 for satellite BeiDou PRN C07"
    )
    bridge._handle_runtime_line(
        "Tracking of GLONASS L1 signal started on channel 12 for satellite GLONASS PRN R03"
    )
    bridge._handle_runtime_line(
        "New BeiDou B1 NAV message received in channel 11 from satellite "
        "BeiDou PRN C07 with CN0=37.5 dB-Hz"
    )
    bridge._handle_runtime_line(
        "New GLONASS L1 NAV message received in channel 12 from satellite "
        "GLONASS PRN R03 with CN0=36.5 dB-Hz"
    )
    bridge._handle_nmea_line("$GBGSV,1,1,01,07,45,120,39*00")
    bridge._handle_nmea_line("$GLGSV,1,1,01,03,35,220,38*00")
    bridge._handle_nmea_line("$GBGSA,A,3,07,,,,,,,,,,,,1.0,1.0,1.0*00")
    bridge._handle_nmea_line("$GLGSA,A,3,03,,,,,,,,,,,,1.0,1.0,1.0*00")

    snapshot = bridge.snapshot()

    assert [entry["satellite_id"] for entry in snapshot["prns"]] == ["C07", "R03"]
    assert [entry["satellite_id"] for entry in snapshot["sky_prns"]] == ["C07", "R03"]
    assert [entry["constellation"] for entry in snapshot["sky_prns"]] == ["beidou", "glonass"]
    assert [entry["used_in_fix"] for entry in snapshot["sky_prns"]] == [True, True]
    assert snapshot["prns"][0]["cno_db_hz"] == 37.5
    assert snapshot["prns"][1]["cno_db_hz"] == 36.5


def test_bridge_ignores_unsupported_nmea_satellite_geometry(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_nmea_line("$QZGSV,1,1,01,01,45,120,39*00")
    bridge._handle_nmea_line("$GPGSV,1,1,01,05,45,120,39*00")

    snapshot = bridge.snapshot()

    assert [entry.get("satellite_id", f"G{entry['prn']:02d}") for entry in snapshot["sky_prns"]] == ["G05"]


def test_bridge_snapshot_tracks_prn_states_from_gnss_sdr_lines(tmp_path: Path) -> None:
    template_path = tmp_path / "fifo.conf.template"
    template_path.write_text(
        "SignalSource.filename={fifo_path}\n"
        "GNSS-SDR.internal_fs_sps={internal_fs_sps}\n",
        encoding="utf-8",
    )
    cfg = StreamConfig(
        gnss_sdr_config_template=template_path,
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line("Channel 0 assigned to GPS PRN 01 (Block IIF) Signal 1C")
    bridge._handle_runtime_line("Successful acquisition in channel 0 for satellite G 14")
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 14 (Block III) in channel 0"
    )
    bridge._handle_runtime_line(
        "Loss of lock in channel 0, satellite GPS PRN 14 (Block III) (carrier_lock_fail_counter:5001 code_lock_fail_counter : 0)"
    )
    bridge._handle_runtime_line("Current receiver time: 12 s")

    snapshot = bridge.snapshot()

    assert snapshot["tracking_count"] == 0
    assert snapshot["lost_count"] == 1
    assert snapshot["receiver_time_s"] == 12
    assert snapshot["prns"] == [
        {"prn": 14, "channel": 0, "state": "lost", "used_in_fix": False}
    ]


def test_bridge_console_tracking_start_keeps_cno_visible_after_pvt_lock(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Tracking of GPS L1 C/A signal started on channel 0 "
        "for satellite GPS PRN 08 (Block IIF)"
    )
    _feed_tracking_cno(
        bridge,
        tuple(
            38.0
            for _ in range(PRN_CNO_STABILITY_WINDOW + PRN_CNO_REQUIRED_STABLE_WINDOWS - 1)
        ),
        prn=8,
    )

    bridge._handle_nmea_line("$GPGGA,123519,0000.00,N,00000.00,E,1,04,2.0,0.0,M,0.0,M,,*00")
    bridge._handle_nmea_line("$GPGSA,A,3,08,,,,,,,,,,,,1.0,1.0,1.0*00")

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert snapshot["pvt_current"] is True
    assert snapshot["tracking_prns"] == [8]
    assert prn["state"] == "tracking"
    assert prn["used_in_fix"] is True
    assert prn["cno_db_hz"] == pytest.approx(38.0)
    assert prn["cno_stable"] is True
    assert prn["cno_unstable_reason"] == ""
    assert snapshot["stable_tracking_prns"] == [8]
    assert snapshot["pending_tracking_prns"] == []
    assert snapshot["unstable_tracking_prns"] == []


def test_bridge_parses_bit_sync_lock_as_tracking_state(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "GPS L1 C/A histogram bit synchronization locked in channel 7 "
        "for satellite GPS PRN 25 (Block IIF)"
    )

    snapshot = bridge.snapshot()

    assert snapshot["tracking_prns"] == [25]
    assert snapshot["prns"] == [
        {
            "prn": 25,
            "channel": 7,
            "state": "tracking",
            "cno_smoothed_db_hz": None,
            "cno_sample_count": 0,
            "cno_stdev_db": None,
            "cno_peak_to_peak_db": None,
            "cno_stable_window_count": 0,
            "cno_required_stable_windows": PRN_CNO_REQUIRED_STABLE_WINDOWS,
            "cno_history_stable": False,
            "telemetry_confirmed": False,
            "carrier_lock_test": None,
            "carrier_lock_threshold": PRN_CARRIER_LOCK_THRESHOLD,
            "cno_stable": False,
            "cno_unstable_reason": "missing_cno",
            "used_in_fix": False,
        }
    ]
    assert snapshot["pending_tracking_prns"] == [25]


def test_bridge_parses_nav_message_cn0_as_current_tracking_sample(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "New GPS NAV message received in channel 3: subframe 2 from satellite "
        "GPS PRN 29 (Block IIR-M) with CN0=38.73 dB-Hz"
    )

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert snapshot["tracking_prns"] == [29]
    assert prn["state"] == "tracking"
    assert prn["channel"] == 3
    assert prn["cno_db_hz"] == pytest.approx(38.73)
    assert prn["cno_smoothed_db_hz"] == pytest.approx(38.73)
    assert prn["cno_sample_count"] == 1
    assert prn["telemetry_confirmed"] is True
    assert prn["cno_history_stable"] is False
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "too_few_samples"
    assert snapshot["stable_tracking_prns"] == []
    assert snapshot["pending_tracking_prns"] == [29]


def test_bridge_marks_console_position_lines_as_current_pvt(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "\x1b[1m\x1b[32mPosition at 2021-Jun-19 10:09:24.000000 UTC "
        "using 8 observations is Lat = 33.684401 [deg], Long = 73.047899 [deg], "
        "Height = 539.28 [m]\x1b[0m"
    )

    snapshot = bridge.snapshot()

    assert snapshot["pvt_output_seen"] is True
    assert snapshot["pvt_current"] is True
    assert snapshot["pvt_observation_count"] == 8


def test_bridge_surfaces_negative_acquisition_decisions_as_searched_prns(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "I20260519 11:18:52.939307 pcps_acquisition.cc:307] "
        "Acquisition decision: negative, satellite G 9, sample_stamp 1418555267, "
        "test_statistics 21.874, threshold 38.7484"
    )
    bridge._handle_runtime_line(
        "I20260519 11:18:52.941285 pcps_acquisition.cc:307] "
        "Acquisition decision: positive, satellite G 15, sample_stamp 1418563458, "
        "test_statistics 41.2, threshold 38.7484"
    )

    snapshot = bridge.snapshot()

    assert snapshot["prns"] == [
        {
            "prn": 9,
            "channel": -1,
            "state": "searched",
            "acq_test_statistic": 21.874,
            "acq_threshold": 38.7484,
            "used_in_fix": False,
        },
        {
            "prn": 15,
            "channel": -1,
            "state": "acquired",
            "acq_test_statistic": 41.2,
            "acq_threshold": 38.7484,
            "used_in_fix": False,
        },
    ]
    assert snapshot["acquired_prns"] == [15]


def test_bridge_keeps_gsv_snr_separate_from_tracking_cno(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line("Channel 0 assigned to GPS PRN 11 (Block IIF) Signal 1C")
    bridge._handle_nmea_line("$GPGSV,1,1,01,11,30,010,37*00")

    snapshot = bridge.snapshot()

    assert snapshot["sky_prns"] == [
        {
            "prn": 11,
            "az_deg": 10.0,
            "el_deg": 30.0,
            "snr_db_hz": 37.0,
            "used_in_fix": False,
            "state": "assigned",
            "channel": 0,
        }
    ]
    assert snapshot["prns"] == [
        {
            "prn": 11,
            "channel": 0,
            "state": "assigned",
            "az_deg": 10.0,
            "el_deg": 30.0,
            "snr_db_hz": 37.0,
            "used_in_fix": False,
        }
    ]


def test_bridge_expires_stale_gsv_geometry(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_nmea_line("$GPGSV,1,1,01,11,30,010,37*00")
    fresh_snapshot = bridge.snapshot()

    assert fresh_snapshot["sky_prns"][0]["prn"] == 11
    assert fresh_snapshot["sky_geometry_count"] == 1
    assert "observed_monotonic_s" in bridge._sat_geometry_by_prn[11]

    bridge._sat_geometry_by_prn[11]["observed_monotonic_s"] = (
        time.monotonic() - SKY_GEOMETRY_TIMEOUT_S - 1.0
    )
    stale_snapshot = bridge.snapshot()

    assert stale_snapshot["sky_prns"] == []
    assert stale_snapshot["sky_geometry_count"] == 0
    assert 11 not in bridge._sat_geometry_by_prn


def test_bridge_expires_used_in_fix_without_fresh_gsa(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 05 (Block III) in channel 0"
    )
    bridge._handle_nmea_line("$GPGSA,A,3,05,,,,,,,,,,,,1.0,1.0,1.0*00")
    fresh_snapshot = bridge.snapshot()

    assert fresh_snapshot["used_in_fix_count"] == 1
    assert fresh_snapshot["prns"][0]["used_in_fix"] is True

    bridge._used_in_fix_observed_monotonic_s = time.monotonic() - USED_IN_FIX_TIMEOUT_S - 1.0
    stale_snapshot = bridge.snapshot()

    assert stale_snapshot["used_in_fix_count"] == 0
    assert stale_snapshot["prns"][0]["used_in_fix"] is False


def test_bridge_expires_pvt_accuracy_cache(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    now = time.monotonic()

    bridge._pvt_output_seen = True
    bridge._pvt_observed_monotonic_s = now
    bridge._latest_accuracy = {"fix_type": "3D Fix", "three_d_error_m": 1.5}
    bridge._latest_accuracy_observed_monotonic_s = now
    fresh_snapshot = bridge.snapshot()

    assert fresh_snapshot["pvt_current"] is True
    assert fresh_snapshot["accuracy"] == {"fix_type": "3D Fix", "three_d_error_m": 1.5}

    old = time.monotonic() - PVT_ACCURACY_TIMEOUT_S - 1.0
    bridge._pvt_observed_monotonic_s = old
    bridge._latest_accuracy_observed_monotonic_s = old
    stale_snapshot = bridge.snapshot()

    assert stale_snapshot["pvt_current"] is False
    assert stale_snapshot["accuracy"] == {}
    assert stale_snapshot["stale_reason"] == "pvt_stale"


def test_bridge_does_not_create_sky_geometry_for_tracking_only_prn(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 12 (Block III) in channel 0"
    )
    snapshot = bridge.snapshot()

    assert snapshot["tracking_prns"] == [12]
    assert snapshot["sky_prns"] == []
    assert snapshot["sky_geometry_count"] == 0


def test_bridge_snapshot_reads_tracking_monitor_udp_cn0(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())

    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 9 (Block III) in channel 0"
    )
    bridge._handle_observables_message(
        _tracking_monitor_message(cno_db_hz=41.75, prn=9, channel=0),
        source="tracking",
    )

    snapshot = bridge.snapshot()

    assert snapshot["avg_tracking_cno_db_hz"] == 41.75
    assert snapshot["tracking_monitor_count"] == 1
    assert snapshot["tracking_monitor"][0]["satellite_id"] == "G09"
    assert snapshot["tracking_monitor"][0]["flag_valid_symbol_output"] is True
    assert len(snapshot["prns"]) == 1
    prn = snapshot["prns"][0]
    assert prn["prn"] == 9
    assert prn["channel"] == 0
    assert prn["state"] == "tracking"
    assert prn["signal"] == "1C"
    assert prn["cno_db_hz"] == 41.75
    assert prn["tracking_monitor_prn"] == 9
    assert prn["cno_smoothed_db_hz"] == 41.75
    assert prn["cno_sample_count"] == 1
    assert prn["cno_stdev_db"] is None
    assert prn["cno_peak_to_peak_db"] is None
    assert prn["cno_stable_window_count"] == 0
    assert prn["cno_required_stable_windows"] == PRN_CNO_REQUIRED_STABLE_WINDOWS
    assert prn["cno_history_stable"] is False
    assert prn["telemetry_confirmed"] is False
    assert prn["carrier_lock_test"] is None
    assert prn["carrier_lock_threshold"] == PRN_CARRIER_LOCK_THRESHOLD
    assert "observable_valid_pseudorange" not in prn
    assert prn["tracking_monitor_valid_pseudorange"] is False
    assert prn["tracking_monitor_rx_time_s"] == pytest.approx(345600.5)
    assert prn["tracking_monitor_tow_s"] == pytest.approx(0.0)
    assert prn["tracking_monitor_doppler_hz"] == pytest.approx(-1350.25)
    assert prn["tracking_monitor_carrier_phase_rads"] == pytest.approx(123456.75)
    assert prn["tracking_monitor_pseudorange_m"] == pytest.approx(21234567.0)
    assert prn["tracking_monitor_cno_db_hz"] == pytest.approx(41.75)
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "too_few_samples"
    assert prn["used_in_fix"] is False


def test_bridge_archives_tracking_carrier_code_iq_and_cycle_slip(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    archive = io.StringIO()
    bridge._tracking_state_handle = archive

    bridge._handle_observables_message(
        _tracking_monitor_message(
            cno_db_hz=41.75,
            prn=9,
            channel=2,
            cycle_slip=True,
        ),
        source="tracking",
    )

    record = json.loads(archive.getvalue())
    assert record["schema_version"] == 1
    assert record["sequence"] == 1
    assert record["timestamp_utc"]
    assert record["timestamp_local"]
    assert record["wall_time_unix_ns"] > 0
    assert record["monotonic_ns"] > 0
    assert record["satellite_id"] == "G09"
    assert record["signal"] == "1C"
    assert record["channel"] == 2
    assert record["prompt_i"] == pytest.approx(0.75)
    assert record["prompt_q"] == pytest.approx(-0.25)
    assert record["prompt_magnitude"] == pytest.approx(np.hypot(0.75, -0.25))
    assert record["prompt_phase_rads"] == pytest.approx(np.arctan2(-0.25, 0.75))
    assert record["cn0_db_hz"] == pytest.approx(41.75)
    assert record["carrier_doppler_hz"] == pytest.approx(-1350.25)
    assert record["carrier_phase_rads"] == pytest.approx(123456.75)
    assert record["carrier_phase_cycles"] == pytest.approx(123456.75 / (2.0 * np.pi))
    assert record["code_phase_samples"] == pytest.approx(12.0)
    assert record["code_phase_seconds"] == pytest.approx(12.0 / record["fs"])
    assert record["tracking_sample_counter"] == 123456
    assert record["valid_acquisition"] is False
    assert record["valid_symbol_output"] is True
    assert record["valid_word"] is False
    assert record["valid_pseudorange"] is False
    assert record["pll_180_deg_phase_locked"] is True
    assert record["cycle_slip"] is True
    assert bridge.snapshot()["tracking_state_archive_rows"] == 1


def test_bridge_does_not_attach_stale_tracking_monitor_cn0_to_acquired_prn(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._handle_observables_message(
        _tracking_monitor_message(cno_db_hz=41.75, prn=9, channel=0),
        source="tracking",
    )

    bridge._handle_runtime_line("Channel 0 assigned to GPS PRN 15 (Block IIF) Signal 1C")
    bridge._handle_runtime_line("Successful acquisition in channel 0 for satellite G 15")

    snapshot = bridge.snapshot()

    assert snapshot["avg_tracking_cno_db_hz"] is None
    prns = {entry["prn"]: entry for entry in snapshot["prns"]}
    assert prns[9]["state"] == "lost"
    assert prns[15]["state"] == "acquired"
    assert prns[15]["channel"] == 0
    assert "cno_db_hz" not in prns[15]
    assert "tracking_monitor_prn" not in prns[15]
    assert prns[15]["used_in_fix"] is False


def test_bridge_marks_tracking_cno_unstable_until_window_is_full(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    _feed_tracking_cno(bridge, tuple(38.0 for _ in range(PRN_CNO_STABILITY_WINDOW - 1)), prn=8)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_db_hz"] == 38.0
    assert prn["cno_smoothed_db_hz"] == 38.0
    assert prn["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW - 1
    if PRN_CNO_STABILITY_WINDOW == 2:
        assert prn["cno_stdev_db"] is None
        assert prn["cno_peak_to_peak_db"] is None
    else:
        assert prn["cno_stdev_db"] == pytest.approx(0.0)
        assert prn["cno_peak_to_peak_db"] == pytest.approx(0.0)
    assert prn["carrier_lock_test"] is None
    assert prn["carrier_lock_threshold"] == PRN_CARRIER_LOCK_THRESHOLD
    assert prn["cno_stable_window_count"] == 0
    assert prn["cno_required_stable_windows"] == PRN_CNO_REQUIRED_STABLE_WINDOWS
    assert prn["cno_history_stable"] is False
    assert prn["telemetry_confirmed"] is False
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "too_few_samples"
    assert snapshot["stable_tracking_prns"] == []
    assert snapshot["pending_tracking_prns"] == [8]


def test_bridge_smooth_tracking_cno_waits_for_decoded_nav_message(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    values = tuple(38.0 for _ in range(PRN_CNO_STABILITY_WINDOW))
    _feed_tracking_cno(bridge, values, prn=8)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_db_hz"] == pytest.approx(38.0)
    assert prn["cno_smoothed_db_hz"] == pytest.approx(38.0)
    assert prn["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW
    assert prn["cno_stdev_db"] <= PRN_CNO_MAX_STDEV_DB
    assert prn["cno_peak_to_peak_db"] <= PRN_CNO_MAX_PEAK_TO_PEAK_DB
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable_window_count"] == 1
    assert prn["cno_history_stable"] is True
    assert prn["telemetry_confirmed"] is False
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "awaiting_nav"
    assert snapshot["pending_tracking_prns"] == [8]
    assert snapshot["stable_tracking_prns"] == []


def test_bridge_marks_tracking_cno_stable_after_required_close_windows(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    values = tuple(
        38.0
        for _ in range(PRN_CNO_STABILITY_WINDOW + PRN_CNO_REQUIRED_STABLE_WINDOWS - 1)
    )
    _feed_tracking_cno(bridge, values, prn=8)
    _confirm_gps_nav(bridge)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_db_hz"] == pytest.approx(38.0)
    assert prn["cno_smoothed_db_hz"] == pytest.approx(38.0)
    assert prn["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW
    assert prn["cno_stdev_db"] <= PRN_CNO_MAX_STDEV_DB
    assert prn["cno_peak_to_peak_db"] <= PRN_CNO_MAX_PEAK_TO_PEAK_DB
    assert prn["cno_stable_window_count"] >= PRN_CNO_REQUIRED_STABLE_WINDOWS
    assert prn["cno_required_stable_windows"] == PRN_CNO_REQUIRED_STABLE_WINDOWS
    assert prn["carrier_lock_test"] is None
    assert prn["carrier_lock_threshold"] == PRN_CARRIER_LOCK_THRESHOLD
    assert prn["cno_history_stable"] is True
    assert prn["telemetry_confirmed"] is True
    assert prn["cno_stable"] is True
    assert prn["cno_unstable_reason"] == ""


def test_bridge_keeps_cno_qualified_prn_visible_when_nav_is_confirmed(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    _feed_tracking_cno(
        bridge,
        tuple(38.0 for _ in range(PRN_CNO_STABILITY_WINDOW)),
        prn=8,
        carrier_lock_test=PRN_CARRIER_LOCK_THRESHOLD - 0.1,
    )
    _confirm_gps_nav(bridge)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_db_hz"] == pytest.approx(38.0)
    assert prn["carrier_lock_test"] is None
    assert prn["carrier_lock_threshold"] == PRN_CARRIER_LOCK_THRESHOLD
    assert prn["cno_stable"] is True
    assert prn["cno_unstable_reason"] == ""
    assert snapshot["unstable_tracking_prns"] == []
    assert snapshot["stable_tracking_prns"] == [8]


def test_bridge_rejects_tracking_cno_below_twenty_five_db_hz(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    _feed_tracking_cno(
        bridge,
        tuple(24.9 for _ in range(PRN_CNO_STABILITY_WINDOW)),
        prn=8,
        carrier_lock_test=1.0,
    )

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_db_hz"] == pytest.approx(24.9)
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "low_cno"
    assert snapshot["unstable_tracking_prns"] == [8]
    assert snapshot["stable_tracking_prns"] == []


def test_bridge_keeps_pvt_used_prn_visible_when_latest_cno_dips(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    _feed_tracking_cno(
        bridge,
        tuple(38.0 for _ in range(PRN_CNO_STABILITY_WINDOW)),
        prn=8,
        carrier_lock_test=1.0,
    )
    bridge._handle_nmea_line("$GPGGA,123519,0000.00,N,00000.00,E,1,04,2.0,0.0,M,0.0,M,,*00")
    bridge._handle_nmea_line("$GPGSA,A,3,08,,,,,,,,,,,,1.0,1.0,1.0*00")
    _append_tracking_monitor_sample(
        bridge,
        cno_db_hz=20.0,
        prn=8,
        carrier_lock_test=PRN_CARRIER_LOCK_THRESHOLD - 0.1,
    )

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["used_in_fix"] is True
    assert prn["cno_db_hz"] == pytest.approx(20.0)
    assert prn["cno_smoothed_db_hz"] == pytest.approx(38.0)
    assert prn["cno_stdev_db"] > PRN_CNO_MAX_STDEV_DB
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable"] is True
    assert prn["cno_unstable_reason"] == ""
    assert snapshot["stable_tracking_prns"] == [8]
    assert snapshot["unstable_tracking_prns"] == []


def test_bridge_rejects_high_variance_tracking_cno(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    values = (37.0, 39.0) * 10
    _feed_tracking_cno(bridge, values, prn=8)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW
    assert prn["cno_smoothed_db_hz"] == pytest.approx(38.0)
    assert prn["cno_stdev_db"] > PRN_CNO_MAX_STDEV_DB
    assert prn["cno_peak_to_peak_db"] <= PRN_CNO_MAX_PEAK_TO_PEAK_DB
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "high_variance"
    assert snapshot["unstable_tracking_prns"] == [8]
    assert snapshot["stable_tracking_prns"] == []


def test_bridge_rejects_high_peak_to_peak_tracking_cno(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    values = [38.0] * (PRN_CNO_STABILITY_WINDOW - 1)
    values.append(40.1)
    _feed_tracking_cno(bridge, tuple(values), prn=8)

    snapshot = bridge.snapshot()
    prn = snapshot["prns"][0]

    assert prn["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW
    assert prn["cno_stdev_db"] is not None
    assert prn["cno_peak_to_peak_db"] > PRN_CNO_MAX_PEAK_TO_PEAK_DB
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "high_variance"
    assert snapshot["unstable_tracking_prns"] == [8]
    assert snapshot["stable_tracking_prns"] == []


def test_bridge_loss_of_lock_clears_tracking_cno_stability(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    values = [38.0, 38.2, 37.9, 38.1] * 5
    values.extend([38.0, 38.1])
    _feed_tracking_cno(bridge, tuple(values), prn=8)
    _confirm_gps_nav(bridge)
    assert bridge.snapshot()["prns"][0]["cno_stable"] is True

    bridge._handle_runtime_line(
        "Loss of lock in channel 0, satellite GPS PRN 8 (Block III) (carrier_lock_fail_counter:5001 code_lock_fail_counter : 0)"
    )
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 8 (Block III) in channel 0"
    )
    _append_tracking_monitor_sample(bridge, cno_db_hz=38.0, prn=8)

    prn = bridge.snapshot()["prns"][0]

    assert prn["state"] == "tracking"
    assert prn["cno_sample_count"] == 1
    assert prn["carrier_lock_test"] is None
    assert prn["cno_stable_window_count"] == 0
    assert prn["telemetry_confirmed"] is False
    assert prn["cno_stable"] is False
    assert prn["cno_unstable_reason"] == "too_few_samples"


def test_bridge_tracking_monitor_prn_moves_channel_assignment(tmp_path: Path) -> None:
    bridge = _make_tracking_bridge(tmp_path)
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 ( 0.00032575 s)for satellite GPS PRN 15 (Block III) in channel 0"
    )
    _feed_tracking_cno(bridge, tuple(38.0 for _ in range(PRN_CNO_STABILITY_WINDOW + 2)), prn=9)

    snapshot = bridge.snapshot()
    prns = {entry["prn"]: entry for entry in snapshot["prns"]}

    assert prns[15]["state"] == "lost"
    assert prns[9]["state"] == "tracking"
    assert prns[9]["channel"] == 0
    assert prns[9]["tracking_monitor_prn"] == 9
    assert prns[9]["cno_db_hz"] == pytest.approx(38.0)
    assert prns[9]["cno_sample_count"] == PRN_CNO_STABILITY_WINDOW


def test_bridge_pvt_output_seen_requires_pvt_monitor_udp(tmp_path: Path) -> None:
    cfg = StreamConfig(gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path))
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._session_epoch_s = 1.0
    bridge._tracking_outputs_dir.mkdir(parents=True, exist_ok=True)
    bridge._pvt_outputs_dir.mkdir(parents=True, exist_ok=True)

    tracking_only = bridge._tracking_outputs_dir / "tracking_ch_0.dat"
    tracking_only.write_bytes(b"tracking")
    snapshot = bridge.snapshot()
    assert snapshot["pvt_output_seen"] is False

    nmea_path = bridge._pvt_outputs_dir / "gnss_sdr_pvt.nmea"
    nmea_path.write_text("$GPGGA,123519,0000.00,N,00000.00,E,1,04,2.0,0.0,M,0.0,M,,*00\n", encoding="utf-8")
    snapshot = bridge.snapshot()
    assert snapshot["pvt_output_seen"] is False

    bridge._handle_monitor_pvt_message(
        _monitor_pvt_message(
            lat_deg=33.684405,
            lon_deg=73.047899,
            height_m=538.0,
            valid_sats=7,
        )
    )
    snapshot = bridge.snapshot()
    assert snapshot["pvt_output_seen"] is True


def test_bridge_reads_pvt_monitor_udp_for_accuracy_and_valid_sat_count(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_truth_static_lat_deg=33.6844,
        gnss_truth_static_lon_deg=73.0479,
        gnss_truth_static_alt_m=540.0,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._session_epoch_s = 1.0
    bridge._handle_monitor_pvt_message(
        _monitor_pvt_message(
            lat_deg=33.684405,
            lon_deg=73.047899,
            height_m=538.0,
            valid_sats=7,
            hdop=1.4,
            vdop=2.0,
            pdop=2.4,
            gdop=2.8,
        )
    )

    snapshot = bridge.snapshot()

    assert snapshot["pvt_output_seen"] is True
    assert snapshot["pvt_current"] is True
    assert snapshot["pvt_observation_count"] == 7
    accuracy = snapshot["accuracy"]
    assert accuracy["accuracy_source"] == "pvt_udp"
    assert accuracy["lat_deg"] == pytest.approx(33.684405)
    assert accuracy["lon_deg"] == pytest.approx(73.047899)
    assert accuracy["alt_m"] == pytest.approx(538.0)
    assert accuracy["utm_easting_m"] == pytest.approx(319050.1875)
    assert accuracy["utm_northing_m"] == pytest.approx(3728874.3543)
    assert accuracy["utm_zone"] == "43N"
    assert "local_origin_source" not in accuracy
    assert "local_east_m" not in accuracy
    assert "local_north_m" not in accuracy
    assert "local_up_m" not in accuracy
    assert "local_horizontal_m" not in accuracy
    assert "local_three_d_m" not in accuracy
    assert accuracy["hdop"] == pytest.approx(1.4)
    assert accuracy["vdop"] == pytest.approx(2.0)
    assert accuracy["pdop"] == pytest.approx(2.4)
    assert accuracy["gdop"] == pytest.approx(2.8)
    assert accuracy["valid_sats"] == pytest.approx(7)
    assert "horizontal_error_m" in accuracy
    assert "three_d_uncertainty_1sigma_m" not in accuracy
    assert accuracy["pvt_solution"]["ecef_x_m"] == pytest.approx(1.0)
    assert accuracy["pvt_solution"]["rx_time_s"] == pytest.approx(123.0)
    assert accuracy["pvt_solution"]["utc_time"] == "2026-06-18T07:00:00Z"
    assert accuracy["pvt_solution"]["geohash"] == "testhash"
    assert accuracy["pvt_solution"]["monitor_pvt"]["geohash"] == "testhash"

    bridge._handle_monitor_pvt_message(
        _monitor_pvt_message(
            lat_deg=33.684406,
            lon_deg=73.047901,
            height_m=539.5,
            valid_sats=7,
        )
    )
    next_accuracy = bridge.snapshot()["accuracy"]
    assert "local_east_m" not in next_accuracy
    assert "local_north_m" not in next_accuracy
    assert "local_up_m" not in next_accuracy
    assert "local_three_d_m" not in next_accuracy


def test_bridge_reads_observables_monitor_udp_into_snapshot_and_prn_fields(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_1c_channel_count=1,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._session_epoch_s = 1.0
    bridge._handle_runtime_line(
        "Pull-in: Number of samples between Acquisition and Tracking = 1303 "
        "( 0.00032575 s)for satellite GPS PRN 9 (Block III) in channel 0"
    )
    bridge._handle_observables_message(
        _observables_monitor_message(
            prn=9,
            cn0_db_hz=43.25,
            pseudorange_m=20_123_456.5,
            carrier_doppler_hz=-1220.5,
            carrier_phase_rads=98765.25,
        ),
        source="observables",
    )

    snapshot = bridge.snapshot()

    assert snapshot["observables_count"] == 1
    assert snapshot["valid_observables_count"] == 1
    assert snapshot["avg_observable_cno_db_hz"] == pytest.approx(43.25)
    assert snapshot["avg_tracking_cno_db_hz"] is None
    assert snapshot["stable_tracking_prns"] == []
    observable = snapshot["observables"][0]
    assert observable["satellite_id"] == "G09"
    assert observable["channel_id"] == 0
    assert observable["system"] == "G"
    assert observable["signal"] == "1C"
    assert observable["valid_symbol_output"] is True
    assert observable["flag_valid_symbol_output"] is True
    assert observable["tow_s"] == pytest.approx(12.0)
    assert observable["pseudorange_m"] == pytest.approx(20_123_456.5)
    assert observable["carrier_doppler_hz"] == pytest.approx(-1220.5)
    assert observable["carrier_phase_rads"] == pytest.approx(98765.25)
    prn = snapshot["prns"][0]
    assert prn["observable_valid_pseudorange"] is True
    assert "cno_db_hz" not in prn
    assert "tracking_monitor_cno_db_hz" not in prn
    assert prn["used_in_fix"] is False
    assert snapshot["used_in_fix_count"] == 0
    assert snapshot["used_in_fix_source"] == "nmea_gsa"
    assert prn["observable_pseudorange_m"] == pytest.approx(20_123_456.5)
    assert prn["observable_doppler_hz"] == pytest.approx(-1220.5)
    assert prn["observable_carrier_phase_rads"] == pytest.approx(98765.25)
    assert prn["observable_cno_db_hz"] == pytest.approx(43.25)


def test_bridge_builds_pvt_accuracy_snapshot_from_truth_and_dops(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_truth_static_lat_deg=33.6844,
        gnss_truth_static_lon_deg=73.0479,
        gnss_truth_static_alt_m=540.0,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    accuracy = bridge._build_accuracy_snapshot(
        [
            {
                "latitude": 33.684405,
                "longitude": 73.047899,
                "altitude": 538.0,
                "hdop": 2.5,
                "vdop": 4.0,
                "pdop": 4.7,
                "gdop": 5.1,
            }
        ]
    )
    assert accuracy["fix_type"] == "3D Fix"
    assert accuracy["truth_available"] is True
    assert accuracy["utm_easting_m"] == pytest.approx(319050.1875)
    assert accuracy["utm_northing_m"] == pytest.approx(3728874.3543)
    assert accuracy["utm_zone"] == "43N"
    assert "local_origin_source" not in accuracy
    assert "local_east_m" not in accuracy
    assert "local_north_m" not in accuracy
    assert "local_up_m" not in accuracy
    assert "horizontal_error_m" in accuracy
    assert "three_d_uncertainty_1sigma_m" not in accuracy


def test_bridge_reports_empirical_cep_only_after_accuracy_window_is_full(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_truth_static_lat_deg=0.0,
        gnss_truth_static_lon_deg=0.0,
        gnss_truth_static_alt_m=0.0,
        gnss_accuracy_window_points=4,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    meters_per_lon_at_equator = 111_132.954 - 93.5 + 0.118

    def point(east_error_m: float) -> dict[str, float]:
        return {
            "latitude": 0.0,
            "longitude": east_error_m / meters_per_lon_at_equator,
            "altitude": 0.0,
        }

    warming = bridge._build_accuracy_snapshot([point(3.0), point(1.0), point(2.0)])
    assert warming["cep_ready"] is False
    assert warming["cep_sample_count"] == 3
    assert warming["cep_window_points"] == 4
    assert warming["cep_min_points"] == 4
    assert warming["cep_scope"] == "run_cumulative"
    assert "cep50_m" not in warming
    assert "cep95_m" not in warming

    ready = bridge._build_accuracy_snapshot(
        [point(3.0), point(1.0), point(100.0), point(2.0)]
    )
    assert ready["cep_ready"] is True
    assert ready["cep_sample_count"] == 4
    assert ready["cep50_m"] == pytest.approx(2.0)
    assert ready["cep95_m"] == pytest.approx(100.0)

    # A fifth fix must be included instead of retaining only the configured
    # four-point warm-up count. With all five radii the median is 3 m; a
    # four-point rolling window would incorrectly report 2 m.
    cumulative = bridge._build_accuracy_snapshot(
        [point(3.0), point(1.0), point(100.0), point(2.0), point(4.0)]
    )
    assert cumulative["accuracy_window_points"] == 5
    assert cumulative["cep_sample_count"] == 5
    assert cumulative["cep50_m"] == pytest.approx(3.0)
    assert cumulative["cep95_m"] == pytest.approx(100.0)


def test_bridge_cep_stays_unavailable_without_configured_truth(tmp_path: Path) -> None:
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=_fifo_runtime_dir(tmp_path),
        gnss_truth_static_lat_deg=None,
        gnss_truth_static_lon_deg=None,
        gnss_truth_static_alt_m=None,
        gnss_accuracy_window_points=2,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    accuracy = bridge._build_accuracy_snapshot(
        [
            {"latitude": 33.0, "longitude": 73.0, "altitude": 500.0},
            {"latitude": 33.1, "longitude": 73.1, "altitude": 501.0},
        ]
    )

    assert accuracy["truth_available"] is False
    assert accuracy["cep_ready"] is False
    assert accuracy["cep_sample_count"] == 0
    assert "cep50_m" not in accuracy
    assert "cep95_m" not in accuracy


def test_bridge_reset_runtime_dir_clears_runtime_and_separate_glog_dir(tmp_path: Path) -> None:
    runtime_dir = _fifo_runtime_dir(tmp_path)
    log_dir = runtime_dir / "glog"
    cfg = StreamConfig(
        gnss_sdr_runtime_dir=runtime_dir,
        gnss_sdr_log_dir=log_dir,
    )
    bridge = GnssSdrBridge(cfg, _loggers())

    (runtime_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "outputs" / "stale.dat").write_text("old", encoding="utf-8")
    (runtime_dir / "console.log").write_text("old", encoding="utf-8")
    (runtime_dir / "fifo_gps_l1.conf").write_text("old", encoding="utf-8")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "gnss-sdr.test.INFO.1").write_text("old", encoding="utf-8")

    bridge._reset_runtime_dir()

    assert runtime_dir.exists()
    assert log_dir.exists()
    assert not (runtime_dir / "outputs").exists()
    assert not (runtime_dir / "console.log").exists()
    assert not (runtime_dir / "fifo_gps_l1.conf").exists()
    assert list(log_dir.iterdir()) == []


def test_bridge_reset_runtime_dir_falls_back_after_root_owned_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_runtime = tmp_path / "configured" / "runtime"
    configured_log = tmp_path / "configured" / "glog"
    fallback_runtime = tmp_path / "user-runtime"
    bridge = GnssSdrBridge(
        StreamConfig(
            gnss_shared_u1_phase_compensation_enabled=False,
            gnss_sdr_runtime_dir=configured_runtime,
            gnss_sdr_log_dir=configured_log,
        ),
        _loggers(),
    )
    original_clear = bridge._clear_dir

    def reject_configured_runtime(path: Path) -> None:
        if path == configured_runtime.resolve():
            raise PermissionError(errno.EACCES, "Permission denied", str(path / "tracking"))
        original_clear(path)

    monkeypatch.setattr(bridge, "_clear_dir", reject_configured_runtime)
    monkeypatch.setattr(bridge, "_runtime_fallback_dir", lambda: fallback_runtime)

    bridge._reset_runtime_dir()

    assert bridge._runtime_dir == fallback_runtime.resolve()
    assert bridge._log_dir == (fallback_runtime / "glog").resolve()
    assert bridge._fifo_path == fallback_runtime.resolve() / "gnss_iq.fifo"
    assert bridge._config_path == fallback_runtime.resolve() / "fifo_gps_l1.conf"
    assert bridge._outputs_dir == fallback_runtime.resolve() / "outputs"
    assert bridge._tracking_outputs_dir == fallback_runtime.resolve() / "outputs" / "tracking"
    assert bridge._receiver_log_path == (fallback_runtime / "glog" / "receiver.log").resolve()
    assert fallback_runtime.is_dir()
    assert (fallback_runtime / "glog").is_dir()


def test_bridge_session_glog_scan_uses_log_dir(tmp_path: Path) -> None:
    template_path = tmp_path / "fifo.conf.template"
    template_path.write_text(
        "SignalSource.filename={fifo_path}\n"
        "GNSS-SDR.internal_fs_sps={internal_fs_sps}\n",
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime" / "gnss-sdr"
    log_dir = tmp_path / "logs" / "gnss-sdr"
    log_dir.mkdir(parents=True)
    cfg = StreamConfig(
        gnss_sdr_config_template=template_path,
        gnss_sdr_runtime_dir=runtime_dir,
        gnss_sdr_log_dir=log_dir,
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    bridge._session_epoch_s = time.time()
    glog_path = log_dir / "gnss-sdr.test.INFO.1"
    glog_path.write_text("Current receiver time: 3 s\n", encoding="utf-8")

    assert bridge._latest_session_glog_path() == glog_path


class _FakeBridge:
    def __init__(self) -> None:
        self.stop_reasons: list[str] = []

    @property
    def active(self) -> bool:
        return True

    def stop(self, reason: str = "normal stop") -> None:
        self.stop_reasons.append(reason)


class _BlockingWriteBridge(_FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.write_started = threading.Event()
        self.release_first_write = threading.Event()
        self.writes: list[np.ndarray] = []

    def write(self, samples: np.ndarray) -> bool:
        self.writes.append(np.array(samples, copy=True))
        if len(self.writes) == 1:
            self.write_started.set()
            if not self.release_first_write.wait(timeout=2.0):
                raise RuntimeError("test timed out waiting to release FIFO write")
        return True


class _FakeOverflowDevice:
    def __init__(self, channels: int) -> None:
        self.channels = channels
        self.stopped = False

    def recv_chunk(self) -> tuple[object, str]:
        import numpy as np

        return np.zeros((self.channels, 0), dtype=np.complex64), "overflow"

    def stop(self) -> None:
        self.stopped = True


class _FakeOverflowThenIdleDevice:
    def __init__(self, runtime: BackendRuntime, channels: int, overflows: int) -> None:
        self._runtime = runtime
        self.channels = channels
        self.overflows = overflows
        self.recv_count = 0
        self.stopped = False

    def recv_chunk(self) -> tuple[object, str]:
        import numpy as np

        self.recv_count += 1
        if self.recv_count >= self.overflows:
            self._runtime._running = False
        return np.zeros((self.channels, 0), dtype=np.complex64), "overflow"

    def stop(self) -> None:
        self.stopped = True


class _CountingChunk:
    def __init__(self) -> None:
        self.shape = (4, 8)
        self.copy_count = 0

    def copy(self) -> "_CountingChunk":
        self.copy_count += 1
        return self


class _FakeOneChunkDevice:
    def __init__(self, runtime: BackendRuntime, chunk: _CountingChunk) -> None:
        self._runtime = runtime
        self._chunk = chunk
        self.stopped = False

    def recv_chunk(self) -> tuple[_CountingChunk, str]:
        self._runtime._running = False
        return self._chunk, "ok"

    def stop(self) -> None:
        self.stopped = True


class _FakePausableDevice:
    def __init__(self) -> None:
        self.paused = False
        self.stopped = False
        self.stop_calls = 0

    def pause_stream(self) -> None:
        self.paused = True

    def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1


class _FakeStartupDevice:
    def __init__(self, channels: int, error: BaseException | None = None) -> None:
        self.channels = channels
        self.error = error
        self.recv_count = 0
        self.restarted = False
        self.stopped = False

    def recv_chunk(self) -> tuple[object, str]:
        import numpy as np

        self.recv_count += 1
        if self.error is not None:
            if self.restarted:
                self.error = None
            else:
                raise self.error
        if self.error is not None:
            raise self.error
        return np.zeros((self.channels, 8), dtype=np.complex64), "ok"

    def restart_stream(self) -> None:
        self.restarted = True

    def stop(self) -> None:
        self.stopped = True


def test_backend_stop_calls_gnss_bridge_stop() -> None:
    runtime = BackendRuntime(StreamConfig(), _runtime_loggers())
    bridge = _FakeBridge()
    runtime._gnss_bridge = bridge  # type: ignore[assignment]

    runtime.stop("normal stop")

    assert bridge.stop_reasons == ["normal stop"]


def test_backend_preserved_usrp_stop_pauses_reusable_device() -> None:
    statuses: list[str] = []
    runtime = BackendRuntime(
        StreamConfig(preserve_usrp_session_on_stop=True),
        _runtime_loggers(),
        on_status=statuses.append,
    )
    device = _FakePausableDevice()
    runtime._device = device  # type: ignore[assignment]

    runtime.stop("normal stop")

    assert device.paused is True
    assert device.stopped is False
    assert statuses == ["Stopping USRP stream"]


def test_backend_finalizer_stops_and_detaches_device_after_exception() -> None:
    runtime = BackendRuntime(
        StreamConfig(preserve_usrp_session_on_stop=False),
        _runtime_loggers(),
    )
    device = _FakePausableDevice()
    runtime._device = device  # type: ignore[assignment]

    runtime._finalize_usrp_device()
    runtime._finalize_usrp_device()

    assert device.stop_calls == 1
    assert runtime._device is None


def test_backend_finalizer_pauses_and_retains_preserved_device() -> None:
    runtime = BackendRuntime(
        StreamConfig(preserve_usrp_session_on_stop=True),
        _runtime_loggers(),
    )
    device = _FakePausableDevice()
    runtime._device = device  # type: ignore[assignment]

    runtime._finalize_usrp_device()

    assert device.paused is True
    assert device.stopped is False
    assert runtime._device is device


def test_backend_startup_probe_restarts_usrp_after_first_recv_socket_close(
    monkeypatch,
) -> None:
    statuses: list[str] = []
    cfg = StreamConfig()
    runtime = BackendRuntime(cfg, _runtime_loggers(), on_status=statuses.append)
    first = _FakeStartupDevice(len(cfg.channels), RuntimeError("IOError: socket closed"))
    runtime._device = first  # type: ignore[assignment]
    monkeypatch.setattr("antijamming.runtime.backend.time.sleep", lambda _seconds: None)

    runtime._prime_usrp_rx_startup()

    assert first.recv_count == 2
    assert first.restarted is True
    assert first.stopped is False
    assert runtime._device is first
    assert statuses == ["Retrying USRP RX startup"]


def test_backend_cleanup_stop_does_not_overwrite_rx_failure_reason() -> None:
    runtime = BackendRuntime(StreamConfig(), _runtime_loggers())
    runtime._failed_stop("RX recv failed: EnvironmentError: IOError: socket closed")

    runtime.stop("GUI close")

    assert runtime._stop_reason == "RX recv failed: EnvironmentError: IOError: socket closed"


def test_backend_gnss_exception_path_pauses_handoff_only() -> None:
    failures: list[str] = []
    statuses: list[str] = []
    runtime = BackendRuntime(
        StreamConfig(gnss_sdr_require_local=False),
        _runtime_loggers(),
        on_failed=failures.append,
        on_status=statuses.append,
    )
    bridge = _FakeBridge()
    device = _FakePausableDevice()
    runtime._gnss_bridge = bridge  # type: ignore[assignment]
    runtime._device = device  # type: ignore[assignment]
    runtime._running = True

    runtime._handle_gnss_pipeline_error(RuntimeError("fifo disconnected"))

    assert bridge.stop_reasons == ["GNSS pipeline failed: fifo disconnected"]
    assert runtime._gnss_bridge is None
    assert runtime._gnss_raw_queue is None
    assert runtime._gnss_output_queue is None
    assert runtime._running is True
    assert device.stopped is False
    assert failures == []
    assert statuses == [
        "GNSS-SDR handoff paused; SDR stream still running (fifo disconnected)"
    ]
    assert runtime._stop_reason == "not started"


def test_backend_handles_concurrent_gnss_failure_only_once() -> None:
    failures: list[str] = []
    runtime = BackendRuntime(
        StreamConfig(),
        _runtime_loggers(),
        on_failed=failures.append,
    )
    bridge = _FakeBridge()
    runtime._gnss_bridge = bridge  # type: ignore[assignment]
    runtime._running = True

    runtime._handle_gnss_pipeline_error(RuntimeError("queue full"))
    runtime._handle_gnss_pipeline_error(RuntimeError("fifo closed"))

    assert bridge.stop_reasons == ["GNSS pipeline failed: queue full"]
    assert failures == []
    assert runtime._running is True
    assert runtime._stop_reason == "not started"


def test_backend_overflow_stop_reason_is_recorded() -> None:
    failures: list[str] = []
    cfg = StreamConfig(
        startup_grace_s=0.0,
        stop_on_overflow=True,
        max_total_overflow=1,
        max_overflow_streak=10,
    )
    runtime = BackendRuntime(cfg, _runtime_loggers(), on_failed=failures.append)
    device = _FakeOverflowDevice(len(cfg.channels))
    runtime._device = device  # type: ignore[assignment]
    runtime._running = True
    runtime._stream_start_ts = time.monotonic() - 1.0

    runtime._rx_drain_loop()

    assert runtime._running is False
    assert device.stopped is True
    assert failures
    assert failures[-1].startswith("Auto-stop on RX overflow:")
    assert runtime._stop_reason == failures[-1]


def test_backend_pauses_gnss_handoff_when_raw_queue_is_full() -> None:
    failures: list[str] = []
    statuses: list[str] = []
    cfg = StreamConfig(
        process_every_n_chunks=999,
        gnss_feed_queue_maxsize=1,
    )
    runtime = BackendRuntime(
        cfg,
        _runtime_loggers(),
        on_failed=failures.append,
        on_status=statuses.append,
    )
    stale = np.ones((len(cfg.channels), 8), dtype=np.complex64)
    chunk = np.zeros((len(cfg.channels), 8), dtype=np.complex64)
    device = _FakeOneChunkDevice(runtime, chunk)  # type: ignore[arg-type]
    raw_q: queue.Queue = queue.Queue(maxsize=1)
    raw_q.put(stale)
    runtime._device = device  # type: ignore[assignment]
    runtime._gnss_raw_queue = raw_q
    runtime._gnss_bridge = _FakeBridge()  # type: ignore[assignment]
    runtime._running = True
    runtime._stop_reason = "normal stop"
    runtime._stream_start_ts = time.monotonic() - 1.0

    runtime._rx_drain_loop()

    assert raw_q.qsize() == 1
    assert raw_q.get_nowait() is stale
    assert runtime._gnss_raw_drops == 1
    assert runtime._gnss_bridge is None
    assert runtime._gnss_raw_queue is None
    assert device.stopped is False
    assert failures == []
    assert statuses == [
        "GNSS-SDR handoff paused; SDR stream still running "
        "(GNSS raw queue full; paused handoff instead of dropping contiguous IQ)"
    ]
    assert runtime._stop_reason == "normal stop"


def test_backend_queues_contiguous_gnss_chunk_without_copy() -> None:
    cfg = StreamConfig(
        process_every_n_chunks=999,
        gnss_feed_queue_maxsize=1,
    )
    runtime = BackendRuntime(cfg, _runtime_loggers())
    chunk = np.zeros((len(cfg.channels), 8), dtype=np.complex64)
    device = _FakeOneChunkDevice(runtime, chunk)  # type: ignore[arg-type]
    raw_q: queue.Queue = queue.Queue(maxsize=1)
    runtime._device = device  # type: ignore[assignment]
    runtime._gnss_raw_queue = raw_q
    runtime._gnss_bridge = _FakeBridge()  # type: ignore[assignment]
    runtime._running = True
    runtime._stream_start_ts = time.monotonic() - 1.0

    runtime._rx_drain_loop()

    assert raw_q.get_nowait() is chunk
    assert runtime._gnss_raw_drops == 0


def test_backend_overlaps_beamforming_with_blocking_fifo_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = StreamConfig(gnss_shared_u1_phase_compensation_enabled=False)
    runtime = BackendRuntime(cfg, _runtime_loggers())
    bridge = _BlockingWriteBridge()
    raw_q: queue.Queue = queue.Queue(maxsize=4)
    output_q: queue.Queue = queue.Queue(maxsize=4)
    runtime._gnss_bridge = bridge  # type: ignore[assignment]
    runtime._gnss_raw_queue = raw_q
    runtime._gnss_output_queue = output_q
    runtime._running = True
    monkeypatch.setattr(runtime, "_gnss_output_vector", lambda chunk: chunk[0])
    monkeypatch.setattr(
        runtime,
        "_get_gnss_monitor_logical_weights_copy",
        lambda: np.ones((1, len(cfg.channels)), dtype=np.complex128),
    )

    fifo_thread = threading.Thread(target=runtime._gnss_fifo_write_loop)
    beamform_thread = threading.Thread(target=runtime._gnss_beamform_loop)
    fifo_thread.start()
    beamform_thread.start()
    first = np.ones((len(cfg.channels), 8), dtype=np.complex64)
    second = np.full((len(cfg.channels), 8), 2.0, dtype=np.complex64)
    raw_q.put(first)
    assert bridge.write_started.wait(timeout=1.0)
    raw_q.put(second)

    deadline = time.monotonic() + 1.0
    while output_q.qsize() < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert raw_q.empty()
    assert output_q.qsize() == 1

    raw_q.put(None)
    beamform_thread.join(timeout=1.0)
    assert not beamform_thread.is_alive()
    bridge.release_first_write.set()
    assert runtime._put_shutdown_sentinel(output_q, timeout_s=1.0)
    fifo_thread.join(timeout=1.0)
    assert not fifo_thread.is_alive()
    assert len(bridge.writes) == 2
    np.testing.assert_array_equal(bridge.writes[0], first[0])
    np.testing.assert_array_equal(bridge.writes[1], second[0])
