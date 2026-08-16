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
            self._backend.wait(timeout=15.0)
            self._server.close()
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
            count = int(arguments["count"])
            self._backend.set_expected_sources(count)
            return {"accepted": True, "count": count}
        if normalized == "set_lcmv_test_enabled":
            enabled = bool(arguments["enabled"])
            self._backend.set_lcmv_test_enabled(enabled)
            return {"accepted": True, "enabled": enabled}
        if normalized == "mark_rf_event":
            event = str(arguments["event"])
            result = self._backend.mark_rf_event(
                event,
                attenuation_db=self._optional_float(arguments.get("attenuation_db")),
                bladeRF_gain_db=self._optional_float(arguments.get("bladeRF_gain_db")),
                notes=str(arguments.get("notes", "")),
                source=str(arguments.get("source", "gui")),
            )
            return {"accepted": True, "event": result}
        raise ValueError(f"unknown antijamming command: {command!r}")

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    def _start_backend(self, reason: str) -> bool:
        with self._lifecycle_lock:
            if self._backend.is_running():
                return False
            self._monitor_generation += 1
            generation = self._monitor_generation
            self._loggers["app"].info("Headless backend start requested: %s", reason)
            self._backend.start()
            self._server.publish_state(
                backend_running=True,
                service_state="starting",
            )
            threading.Thread(
                target=self._monitor_backend,
                args=(generation,),
                name="antijam_backend_monitor",
                daemon=True,
            ).start()
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
        self._backend.wait()
        with self._lifecycle_lock:
            if generation != self._monitor_generation:
                return
            self._server.publish_state(
                backend_running=False,
                service_state="idle",
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless anti-jamming backend service")
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
    loggers = setup_logging(cfg.log_dir)
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
        except Exception:
            pass
    return service.serve(auto_start=bool(args.auto_start))


if __name__ == "__main__":
    raise SystemExit(run())
