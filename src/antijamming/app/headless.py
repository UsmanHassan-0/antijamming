"""Headless anti-jamming backend service.

Starting this module creates only the control/telemetry service.  UHD, the USRP,
DSP worker threads, and GNSS-SDR start only after an explicit ``start`` command.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import threading
from typing import Any, Callable

from threadpoolctl import threadpool_info, threadpool_limits

from antijamming.app.runtime_config import build_runtime_config
from antijamming.config import StreamConfig
from antijamming.logging import setup_logging
from antijamming.runtime.backend import BackendRuntime
from antijamming.runtime.ipc import JsonIpcServer, default_socket_path

_NUMERIC_THREAD_LIMIT = 1
_NUMERIC_THREAD_CONTROLLER = threadpool_limits(limits=_NUMERIC_THREAD_LIMIT)


class HeadlessRuntimeService:
    """Own one backend runtime and expose lifecycle commands over local IPC."""

    def __init__(
        self,
        config: StreamConfig,
        loggers: dict[str, logging.Logger],
        socket_path: Path | str,
        *,
        backend_factory: Callable[..., BackendRuntime] = BackendRuntime,
    ) -> None:
        self._config = config
        self._loggers = loggers
        self._server = JsonIpcServer(socket_path, on_command=self._handle_command)
        self._shutdown = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._monitor_generation = 0
        self._monitor_thread: threading.Thread | None = None
        self._backend = backend_factory(
            config=config,
            loggers=loggers,
            on_data=self._on_data,
            on_status=self._server.publish_status,
            on_failed=self._server.publish_failed,
        )

    @property
    def socket_path(self) -> Path:
        return self._server.socket_path

    def serve(self, *, auto_start: bool = False) -> int:
        self._server.start()
        self._server.publish_state(
            backend_running=False,
            service_state="idle",
            socket_path=str(self.socket_path),
        )
        self._loggers["app"].info(
            "Headless service ready: pid=%d socket=%s auto_start=%s",
            os.getpid(),
            self.socket_path,
            bool(auto_start),
        )
        if auto_start:
            self._start_backend("headless --auto-start")
        try:
            self._shutdown.wait()
        finally:
            self._stop_backend("headless service shutdown")
            if not self._backend.wait(timeout=15.0):
                self._loggers.get("errors", self._loggers["app"]).error(
                    "Headless backend did not stop within 15 seconds; "
                    "the service process still owns the live backend."
                )
            if not self._server.close():
                self._loggers.get("errors", self._loggers["app"]).error(
                    "Headless IPC server retained live accept/session threads at shutdown."
                )
        return 0

    def request_shutdown(self, reason: str) -> None:
        self._loggers["app"].info("Headless shutdown requested: %s", reason)
        self._shutdown.set()

    def _on_data(self, metrics: dict[str, Any]) -> None:
        enriched = dict(metrics)
        enriched["doa_scan_angles_deg"] = self._config.angle_scan_spec().values()
        self._server.publish_metrics(enriched)

    def _handle_command(
        self,
        command: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = command.strip().lower()
        if normalized == "ping":
            return {
                "service": "antijamming-headless",
                "backend_running": self._backend.is_running(),
            }
        if normalized == "start":
            started = self._start_backend(str(arguments.get("reason", "IPC start")))
            return {"accepted": True, "started": started}
        if normalized == "stop":
            stopped = self._stop_backend(str(arguments.get("reason", "IPC stop")))
            return {"accepted": True, "stopped": stopped}
        if normalized == "shutdown":
            self._stop_backend(str(arguments.get("reason", "IPC shutdown")))
            self.request_shutdown(str(arguments.get("reason", "IPC shutdown")))
            return {"accepted": True}
        if normalized == "set_expected_sources":
            count = arguments.get("count")
            if type(count) is not int:
                raise ValueError("set_expected_sources requires an integer 'count'")
            self._backend.set_expected_sources(count)
            return {"accepted": True, "count": count}
        if normalized == "set_lcmv_test_enabled":
            enabled = arguments.get("enabled")
            if type(enabled) is not bool:
                raise ValueError(
                    "set_lcmv_test_enabled requires a boolean 'enabled'"
                )
            self._backend.set_lcmv_test_enabled(enabled)
            return {"accepted": True, "enabled": enabled}
        if normalized == "mark_rf_event":
            event = str(arguments["event"])
            result = self._backend.mark_rf_event(
                event,
                notes=str(arguments.get("notes", "")),
                source=str(arguments.get("source", "gui")),
            )
            return {"accepted": True, "event": result}
        raise ValueError(f"unknown antijamming command: {command!r}")

    def _start_backend(self, reason: str) -> bool:
        with self._lifecycle_lock:
            if self._backend.is_running():
                return False
            prior_monitor = self._monitor_thread
            if prior_monitor is not None and prior_monitor.is_alive():
                # The previous monitor may have observed backend exit but not
                # yet completed its own state publication. Starting another
                # run here would let that old waiter attach to the new run.
                return False
            if self._monitor_thread is prior_monitor:
                self._monitor_thread = None
            self._monitor_generation += 1
            generation = self._monitor_generation
            self._loggers["app"].info("Headless backend start requested: %s", reason)
            self._backend.start()
            monitor = threading.Thread(
                target=self._monitor_backend,
                args=(generation,),
                name="antijam_backend_monitor",
                daemon=True,
            )
            try:
                monitor.start()
            except BaseException:
                self._backend.stop("headless backend monitor startup failure")
                if not self._backend.wait(timeout=15.0):
                    self._loggers.get("errors", self._loggers["app"]).error(
                        "Backend monitor thread failed to start and backend "
                        "did not stop within 15 seconds."
                    )
                raise
            self._monitor_thread = monitor
            self._server.publish_state(
                backend_running=True,
                service_state="starting",
            )
            return True

    def _stop_backend(self, reason: str) -> bool:
        with self._lifecycle_lock:
            if not self._backend.is_running():
                return False
            self._loggers["app"].info("Headless backend stop requested: %s", reason)
            self._server.publish_state(
                backend_running=True,
                service_state="stopping",
            )
            self._backend.stop(reason)
            return True

    def _monitor_backend(self, generation: int) -> None:
        try:
            self._backend.wait()
            with self._lifecycle_lock:
                if generation != self._monitor_generation:
                    return
                self._server.publish_state(
                    backend_running=False,
                    service_state="idle",
                )
        finally:
            with self._lifecycle_lock:
                if self._monitor_thread is threading.current_thread():
                    self._monitor_thread = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless anti-jamming backend service"
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=default_socket_path(),
        help="Local Unix socket used for control and telemetry.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Start hardware immediately instead of waiting for an IPC Start command.",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    cfg = build_runtime_config()
    loggers = setup_logging(cfg.log_dir, enabled=cfg.logging_enabled)
    numeric_pools = threadpool_info()
    loggers["app"].info(
        "Headless numeric thread pools limited to %d thread: %s",
        _NUMERIC_THREAD_LIMIT,
        ", ".join(
            f"{pool.get('internal_api', 'unknown')}={pool.get('num_threads', '--')}"
            for pool in numeric_pools
        )
        or "no native pool reported",
    )
    service = HeadlessRuntimeService(cfg, loggers, args.socket)

    def _handle_signal(signum: int, _frame: object) -> None:
        service.request_shutdown(f"signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception as exc:
            loggers.get("errors", loggers["app"]).warning(
                "Could not install signal handler for %s: %s",
                sig,
                exc,
            )
    return service.serve(auto_start=bool(args.auto_start))


if __name__ == "__main__":
    raise SystemExit(run())
