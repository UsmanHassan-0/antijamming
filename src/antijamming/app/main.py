"""Fixed product entry point for the realtime anti-jamming GUI."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from threadpoolctl import threadpool_info, threadpool_limits

from antijamming.app.runtime_config import build_runtime_config
from antijamming.config import REPO_ROOT, StreamConfig
from antijamming.logging import setup_logging
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

    return build_runtime_config()


def _reap_backend_process(
    process: subprocess.Popen,
    loggers: dict,
    *,
    initial_wait_s: float,
) -> bool:
    """Wait, terminate, then kill and reap one owned backend process."""

    try:
        process.wait(timeout=max(0.0, float(initial_wait_s)))
        return True
    except subprocess.TimeoutExpired:
        loggers["errors"].error(
            "Headless backend service did not exit within %.1f seconds; sending SIGTERM.",
            max(0.0, float(initial_wait_s)),
        )
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5.0)
        return True
    except subprocess.TimeoutExpired:
        loggers["errors"].error(
            "Headless backend service ignored SIGTERM for 5.0 seconds; sending SIGKILL."
        )
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=2.0)
        return True
    except subprocess.TimeoutExpired:
        loggers["errors"].error(
            "Headless backend service was not reaped within 2.0 seconds after SIGKILL."
        )
        return False


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
    from antijamming.runtime.remote_worker import RemoteStreamWorker

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
    socket_path = Path("/tmp") / f"antijamming-gui-{os.getpid()}.sock"
    try:
        backend_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "antijamming.app.headless",
                "--socket",
                str(socket_path),
            ],
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except BaseException:
        uhd_marker_monitor.stop()
        raise
    worker = RemoteStreamWorker(socket_path)
    try:
        worker.connect_service(timeout_s=8.0)
    except BaseException:
        worker.close()
        _reap_backend_process(
            backend_process,
            loggers,
            initial_wait_s=0.0,
        )
        uhd_marker_monitor.stop()
        raise
    shutdown_requested = {"value": False}
    cleanup_completed = {"value": False}

    def _cleanup_worker() -> None:
        if cleanup_completed["value"]:
            return
        cleanup_completed["value"] = True
        uhd_marker_monitor.stop()
        if worker.isRunning():
            worker.stop()
            if not worker.wait(10000):
                loggers["errors"].error("GUI worker did not stop within 10 seconds.")
        worker.shutdown_service("standalone GUI exit")
        _reap_backend_process(
            backend_process,
            loggers,
            initial_wait_s=18.0,
        )
        worker.close()

    try:
        win = MainWindow(cfg, worker)
    except BaseException:
        _cleanup_worker()
        raise

    def _request_shutdown(reason: str) -> None:
        if shutdown_requested["value"]:
            return
        shutdown_requested["value"] = True
        loggers["app"].info("GUI shutdown requested: %s", reason)
        worker.stop()
        QTimer.singleShot(100, app.quit)

    def _handle_signal(signum: int, _frame: object) -> None:
        # Python delivers these handlers on the main thread, so request the Qt
        # shutdown immediately. Deferring through a zero-delay timer can lose
        # the request when the launcher exits during signal handling.
        _request_shutdown(f"signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception as exc:
            loggers["errors"].warning(
                "Could not install GUI signal handler for %s: %s",
                sig,
                exc,
            )

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

    def _show_window(*, focus: bool = False) -> None:
        win.maximize_to_available_screen()
        if not win.isVisible():
            win.showMaximized()
        if focus:
            win.raise_()
            win.activateWindow()

    _show_window(focus=True)
    QTimer.singleShot(250, _show_window)
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
    print(
        "[run_realtime] GUI window shown. Check logs/app.log if it is not visible.",
        flush=True,
    )
    try:
        return app.exec()
    finally:
        _cleanup_worker()


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
