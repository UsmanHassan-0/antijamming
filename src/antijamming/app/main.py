"""Fixed product entry point for the realtime anti-jamming GUI."""

from __future__ import annotations

import argparse
import math
import signal
import sys
from pathlib import Path

from threadpoolctl import threadpool_info, threadpool_limits

from antijamming.dsp.phase import load_phase_correction_vector

from antijamming.config import REPO_ROOT, StreamConfig, default_stream_config
from antijamming.logging import setup_logging
from antijamming.radio.usrp import usrp_arg_int, with_usrp_frame_sizes
from antijamming.radio.usrp.uhd_events import UhdConsoleMarkerMonitor

_NUMERIC_THREAD_LIMIT = 1
_NUMERIC_THREAD_CONTROLLER = threadpool_limits(limits=_NUMERIC_THREAD_LIMIT)


def parse_args() -> argparse.Namespace:
    """Parse product launcher controls.

    Runtime configuration still comes from configs/antijamming/x300_realtime.json.
    These flags only let automated diagnostics start and stop the fixed GUI path.
    """

    parser = argparse.ArgumentParser(
        description="Realtime anti-jamming GUI",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Start the stream automatically after the GUI opens.",
    )
    parser.add_argument(
        "--auto-stop-after-s",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop the stream automatically after this many seconds.",
    )
    parser.add_argument(
        "--quit-after-stop",
        action="store_true",
        help="Quit the GUI shortly after the automated stop.",
    )
    return parser.parse_args()


def _runtime_config() -> StreamConfig:
    """Build the fixed product runtime configuration."""

    cfg = default_stream_config()
    cfg.usrp_addr = with_usrp_frame_sizes(
        cfg.usrp_addr,
        recv_frame_size=int(cfg.recv_frame_size),
        send_frame_size=int(cfg.send_frame_size),
        recv_buff_size=int(cfg.recv_buff_size),
        num_recv_frames=int(cfg.num_recv_frames),
    )
    cfg.recv_frame_size = usrp_arg_int(cfg.usrp_addr, "recv_frame_size", int(cfg.recv_frame_size))
    cfg.send_frame_size = usrp_arg_int(cfg.usrp_addr, "send_frame_size", int(cfg.send_frame_size))
    cfg.process_every_n_chunks = max(1, int(cfg.process_every_n_chunks))
    cfg.ui_update_interval_s = max(0.05, float(cfg.ui_update_interval_s))
    cfg.dsp_update_interval_s = max(0.02, float(cfg.dsp_update_interval_s))
    cfg.ui_points = max(32, int(cfg.ui_points))
    cfg.startup_grace_s = max(0.0, float(cfg.startup_grace_s))
    cfg.min_sample_rate = max(1e5, float(cfg.min_sample_rate))
    cfg.max_overflow_streak = max(1, int(cfg.max_overflow_streak))
    cfg.max_total_overflow = max(1, int(cfg.max_total_overflow))

    channel_count = max(1, len(cfg.channels))
    if channel_count > 1 and float(cfg.array_spacing_m) > 0.0:
        cfg.uca_radius_m = float(cfg.array_spacing_m) / (
            2.0 * math.sin(math.pi / channel_count)
        )

    if cfg.phase_calibration_file is not None:
        calibration_file = Path(cfg.phase_calibration_file).expanduser()
        if not calibration_file.is_absolute():
            calibration_file = (REPO_ROOT / calibration_file).resolve()
        cfg.phase_calibration_file = calibration_file
        cfg.phase_correction_vector = tuple(load_phase_correction_vector(calibration_file))

    return cfg


def _run_gui(
    cfg: StreamConfig,
    *,
    auto_start: bool = False,
    auto_stop_after_s: float | None = None,
    quit_after_stop: bool = False,
) -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from antijamming.ui.main_window import MainWindow
    from antijamming.runtime import StreamWorker

    app = QApplication(sys.argv[:1])
    loggers = setup_logging(cfg.log_dir)
    uhd_marker_monitor = UhdConsoleMarkerMonitor(
        cfg.log_dir / "uhd_console.log",
        loggers,
        sample_rate_hz=float(cfg.sample_rate),
        channel_count=len(cfg.channels),
        samples_per_chunk=int(cfg.samples_per_chunk),
    )
    uhd_marker_monitor.start()
    numeric_pools = threadpool_info()
    loggers["app"].info(
        "Numeric thread pools limited to %d thread: %s",
        _NUMERIC_THREAD_LIMIT,
        ", ".join(
            f"{pool.get('internal_api', 'unknown')}={pool.get('num_threads', '--')}"
            for pool in numeric_pools
        )
        or "no native pool reported",
    )
    worker = StreamWorker(cfg, loggers=loggers)
    win = MainWindow(cfg, worker)
    shutdown_requested = {"value": False}

    def _cleanup_worker() -> None:
        uhd_marker_monitor.stop()
        if worker.isRunning():
            worker.stop()
            if not worker.wait(10000):
                loggers["errors"].error("GUI worker did not stop within 10 seconds.")

    def _request_shutdown(reason: str) -> None:
        if shutdown_requested["value"]:
            return
        shutdown_requested["value"] = True
        loggers["app"].info("GUI shutdown requested: %s", reason)
        worker.stop()
        QTimer.singleShot(100, app.quit)

    def _handle_signal(signum: int, _frame: object) -> None:
        QTimer.singleShot(0, lambda: _request_shutdown(f"signal {signum}"))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass

    signal_timer = QTimer(app)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)
    app.aboutToQuit.connect(_cleanup_worker)
    loggers["app"].info(
        "GUI initialized. Press Start stream to begin. auto_start=%s "
        "auto_stop_after_s=%s quit_after_stop=%s",
        bool(auto_start),
        "--" if auto_stop_after_s is None else f"{float(auto_stop_after_s):.1f}",
        bool(quit_after_stop),
    )

    def _show_window() -> None:
        win.maximize_to_available_screen()
        win.showMaximized()
        win.raise_()
        win.activateWindow()

    _show_window()
    QTimer.singleShot(250, _show_window)
    QTimer.singleShot(1000, _show_window)
    if auto_start:
        QTimer.singleShot(1500, win.start_stream)
    if auto_stop_after_s is not None:
        stop_delay_ms = max(0, int(round(float(auto_stop_after_s) * 1000.0)))

        def _automated_stop() -> None:
            win.stop_stream(f"auto-stop-after-s={float(auto_stop_after_s):.1f}")
            if quit_after_stop:
                QTimer.singleShot(3000, app.quit)

        QTimer.singleShot(stop_delay_ms, _automated_stop)
    loggers["app"].info(
        "GUI window shown: platform=%s geometry=%s",
        app.platformName(),
        win.geometry().getRect(),
    )
    print("[run_realtime] GUI window shown. Check logs/app.log if it is not visible.", flush=True)
    return app.exec()


def run() -> int:
    """Launch the fixed realtime GUI product path."""

    args = parse_args()
    if args.auto_stop_after_s is not None and float(args.auto_stop_after_s) < 0.0:
        raise SystemExit("--auto-stop-after-s must be >= 0")
    return _run_gui(
        _runtime_config(),
        auto_start=bool(args.auto_start),
        auto_stop_after_s=args.auto_stop_after_s,
        quit_after_stop=bool(args.quit_after_stop),
    )


if __name__ == "__main__":
    raise SystemExit(run())
