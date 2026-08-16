"""Qt signal adapter for a backend running in a separate OS process."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from antijamming.runtime.ipc import JsonIpcClient


class RemoteStreamWorker(QObject):
    """Present the historical worker API while forwarding commands over IPC."""

    data_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, socket_path: Path | str) -> None:
        super().__init__()
        self._client = JsonIpcClient(socket_path, on_message=self._on_message)
        self._lock = threading.Lock()
        self._backend_running = False
        self._start_requested = False
        self._seen_running_since_start = False
        self._stopped = threading.Event()
        self._stopped.set()

    def connect_service(self, timeout_s: float = 8.0) -> None:
        self._client.connect(timeout_s=timeout_s)

    def start(self) -> None:
        with self._lock:
            if self._backend_running or self._start_requested:
                return
            self._start_requested = True
            self._seen_running_since_start = False
            self._stopped.clear()
        try:
            self._client.command("start", reason="standalone GUI Start")
        except Exception as exc:
            with self._lock:
                self._start_requested = False
                self._stopped.set()
            self.failed.emit(f"Backend start command failed: {exc}")

    def stop(self, reason: str = "normal stop") -> None:
        with self._lock:
            active = self._backend_running or self._start_requested
        if not active:
            return
        try:
            self._client.command("stop", reason=reason)
        except Exception as exc:
            self.failed.emit(f"Backend stop command failed: {exc}")

    def shutdown_service(self, reason: str = "GUI shutdown") -> None:
        try:
            self._client.command("shutdown", reason=reason)
        except Exception:
            pass

    def close(self) -> None:
        self._client.close()

    def wait(self, milliseconds: int = 0) -> bool:
        timeout_s = None if int(milliseconds) < 0 else max(0, int(milliseconds)) / 1000.0
        return self._stopped.wait(timeout_s)

    def isRunning(self) -> bool:
        with self._lock:
            return bool(self._backend_running or self._start_requested)

    def set_expected_sources(self, count: int) -> None:
        try:
            self._client.command("set_expected_sources", count=int(count))
        except Exception as exc:
            self.failed.emit(f"Could not set MUSIC source count: {exc}")

    def set_lcmv_test_enabled(self, enabled: bool) -> None:
        try:
            self._client.command(
                "set_lcmv_test_enabled",
                enabled=bool(enabled),
            )
        except Exception as exc:
            self.failed.emit(f"Could not set LCMV test state: {exc}")

    def mark_rf_event(
        self,
        event: str,
        *,
        attenuation_db: float | None = None,
        bladeRF_gain_db: float | None = None,
        notes: str = "",
    ) -> None:
        try:
            self._client.command(
                "mark_rf_event",
                event=str(event),
                attenuation_db=attenuation_db,
                bladeRF_gain_db=bladeRF_gain_db,
                notes=str(notes),
                source="gui",
            )
        except Exception as exc:
            self.failed.emit(f"Could not record RF event {event}: {exc}")

    def _on_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "metrics":
            payload = message.get("payload")
            if isinstance(payload, dict):
                self.data_ready.emit(payload)
            return
        if message_type == "status":
            self.status.emit(str(message.get("message", "Backend status")))
            return
        if message_type == "failed":
            with self._lock:
                self._start_requested = False
            self.failed.emit(str(message.get("message", "Backend failed")))
            return
        if message_type != "state":
            if message_type == "reply" and not bool(message.get("ok", False)):
                self.failed.emit(str(message.get("error", "Backend command failed")))
            return

        running = bool(message.get("backend_running", False))
        emit_finished = False
        with self._lock:
            was_active = self._backend_running or self._start_requested
            self._backend_running = running
            if running:
                self._seen_running_since_start = True
            elif self._seen_running_since_start:
                self._start_requested = False
                self._seen_running_since_start = False
                self._stopped.set()
                emit_finished = was_active
        if emit_finished:
            self.finished.emit()


__all__ = ["RemoteStreamWorker"]
