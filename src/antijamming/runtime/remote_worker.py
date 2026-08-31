"""Qt signal adapter for a backend running in a separate OS process."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any
import uuid

from PyQt6.QtCore import QObject, pyqtSignal

from antijamming.runtime.ipc import JsonIpcClient


class RemoteStreamWorker(QObject):
    """Expose the GUI worker API while forwarding commands over IPC."""

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
        self._pending_start_request_id: str | None = None
        self._seen_running_since_start = False
        self._stopped = threading.Event()
        self._stopped.set()

    def connect_service(self, timeout_s: float = 8.0) -> None:
        self._client.connect(timeout_s=timeout_s)

    def start(self) -> None:
        request_id = uuid.uuid4().hex
        with self._lock:
            if self._backend_running or self._start_requested:
                return
            self._start_requested = True
            self._pending_start_request_id = request_id
            self._seen_running_since_start = False
            self._stopped.clear()
        try:
            self._client.command(
                "start",
                request_id=request_id,
                reason="standalone GUI Start",
            )
        except Exception as exc:
            with self._lock:
                self._start_requested = False
                self._pending_start_request_id = None
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

    def close(self) -> bool:
        stopped = self._client.close()
        if stopped:
            with self._lock:
                self._backend_running = False
                self._start_requested = False
                self._pending_start_request_id = None
                self._seen_running_since_start = False
                self._stopped.set()
        return stopped

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
        notes: str = "",
    ) -> None:
        try:
            self._client.command(
                "mark_rf_event",
                event=str(event),
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
                self._pending_start_request_id = None
                if not self._backend_running:
                    self._stopped.set()
            self.failed.emit(str(message.get("message", "Backend failed")))
            return
        if message_type != "state":
            if message_type == "reply":
                request_id = str(message.get("request_id", ""))
                result = message.get("result", {})
                start_rejected = bool(
                    isinstance(result, dict)
                    and result.get("started") is False
                )
                protocol_error = not bool(message.get("ok", False))
                if not protocol_error and not start_rejected:
                    return
                with self._lock:
                    if request_id == self._pending_start_request_id:
                        self._start_requested = False
                        self._pending_start_request_id = None
                        if not self._backend_running:
                            self._stopped.set()
                    else:
                        return
                error = (
                    message.get("error", "Backend command failed")
                    if protocol_error
                    else "Backend start was not accepted because a prior run is still finishing"
                )
                self.failed.emit(str(error))
            return

        running = bool(message.get("backend_running", False))
        emit_finished = False
        with self._lock:
            was_active = self._backend_running or self._start_requested
            self._backend_running = running
            if running:
                self._pending_start_request_id = None
                self._seen_running_since_start = True
            elif self._seen_running_since_start:
                self._start_requested = False
                self._pending_start_request_id = None
                self._seen_running_since_start = False
                self._stopped.set()
                emit_finished = was_active
        if emit_finished:
            self.finished.emit()


__all__ = ["RemoteStreamWorker"]
