"""Local JSON-line IPC for the headless runtime and its GUI clients."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import os
from pathlib import Path
import queue
import socket
import threading
import time
import uuid
from typing import Any

import numpy as np

PROTOCOL_NAME = "antijamming-local"
PROTOCOL_VERSION = 1

# The GUI does not consume the multi-megabyte IQ preview arrays.  Keeping them
# out of IPC makes telemetry latest-only and prevents plotting from delaying DSP.
_WIRE_METRIC_KEYS = frozenset(
    {
        "backend_monotonic_s",
        "ui_metrics_seq",
        "powers",
        "phase_offsets_deg",
        "phase_offsets_raw_deg",
        "phase_offsets_calibrated_deg",
        "doa_raw_spectrum",
        "doa_scan_angles_deg",
        "doa_deg",
        "doa_display_deg",
        "lcmv_test",
        "rx_signal_health",
        "gnss_snapshot",
        "n_sources",
        "source_count",
        "source_estimate_gap",
        "source_effective_rank",
    }
)


def default_socket_path() -> Path:
    """Return a per-user default socket outside the source tree."""

    return Path("/tmp") / f"antijamming-{os.getuid()}.sock"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return {
                "real": _json_safe(np.real(value)),
                "imag": _json_safe(np.imag(value)),
            }
        return _json_safe(value.tolist())
    if isinstance(value, (complex, np.complexfloating)):
        number = complex(value)
        return {
            "real": _json_safe(number.real),
            "imag": _json_safe(number.imag),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)


def metrics_for_wire(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a backend snapshot to fields used by either current GUI."""

    return {
        key: _json_safe(value)
        for key, value in metrics.items()
        if key in _WIRE_METRIC_KEYS
    }


def _envelope(message_type: str, **fields: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "type": str(message_type),
        **fields,
    }


class _ClientSession:
    def __init__(self, server: "JsonIpcServer", conn: socket.socket) -> None:
        self._server = server
        self._conn = conn
        self._conn.settimeout(0.5)
        self._closed = threading.Event()
        self._send_ready = threading.Event()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=32)
        self._latest_lock = threading.Lock()
        self._latest_metrics: dict[str, Any] | None = None
        self._latest_metrics_seq: object = object()
        self._last_sent_metrics_seq: object = object()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="antijam_ipc_reader",
            daemon=True,
        )
        self._sender = threading.Thread(
            target=self._sender_loop,
            name="antijam_ipc_sender",
            daemon=True,
        )

    def start(self) -> None:
        self._reader.start()
        self._sender.start()

    def enqueue(self, message: dict[str, Any]) -> None:
        if self._closed.is_set():
            return
        try:
            self._events.put_nowait(message)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(message)
            except queue.Full:
                pass
        self._send_ready.set()

    def set_latest_metrics(self, message: dict[str, Any]) -> None:
        if self._closed.is_set():
            return
        payload = message.get("payload", {})
        sequence = payload.get("ui_metrics_seq") if isinstance(payload, dict) else None
        with self._latest_lock:
            self._latest_metrics = message
            self._latest_metrics_seq = sequence
        self._send_ready.set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._send_ready.set()
        try:
            self._conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._conn.close()
        except OSError:
            pass

    def _send(self, message: dict[str, Any]) -> None:
        encoded = (
            json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        self._conn.sendall(encoded)

    def _sender_loop(self) -> None:
        try:
            while not self._closed.is_set():
                self._send_ready.wait(0.5)
                self._send_ready.clear()
                while not self._closed.is_set():
                    try:
                        event = self._events.get_nowait()
                    except queue.Empty:
                        break
                    self._send(event)

                with self._latest_lock:
                    metrics = self._latest_metrics
                    sequence = self._latest_metrics_seq
                if metrics is not None and sequence != self._last_sent_metrics_seq:
                    self._send(metrics)
                    self._last_sent_metrics_seq = sequence
        except (OSError, ValueError):
            pass
        finally:
            self.close()
            self._server._discard(self)

    def _reader_loop(self) -> None:
        buffer = bytearray()
        try:
            while not self._closed.is_set():
                try:
                    chunk = self._conn.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    if not raw.strip():
                        continue
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        self.enqueue(
                            _envelope("reply", ok=False, error=f"invalid JSON: {exc}")
                        )
                        continue
                    self._handle_message(message)
        except OSError:
            pass
        finally:
            self.close()
            self._server._discard(self)

    def _handle_message(self, message: object) -> None:
        if not isinstance(message, dict) or message.get("type") != "command":
            self.enqueue(_envelope("reply", ok=False, error="expected command"))
            return
        request_id = message.get("request_id")
        command = str(message.get("command", "")).strip()
        arguments = message.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = self._server.handle_command(command, arguments)
        except Exception as exc:
            self.enqueue(
                _envelope(
                    "reply",
                    request_id=request_id,
                    ok=False,
                    error=str(exc),
                )
            )
            return
        self.enqueue(
            _envelope(
                "reply",
                request_id=request_id,
                ok=True,
                result=_json_safe(result or {}),
            )
        )


class JsonIpcServer:
    """Nonblocking publisher and command server on a local Unix socket."""

    def __init__(
        self,
        socket_path: Path | str,
        *,
        on_command: Callable[[str, dict[str, Any]], Mapping[str, Any] | None],
    ) -> None:
        self.socket_path = Path(socket_path)
        self._on_command = on_command
        self._listener: socket.socket | None = None
        self._closed = threading.Event()
        self._sessions_lock = threading.Lock()
        self._sessions: set[_ClientSession] = set()
        self._latest_metrics: dict[str, Any] | None = None
        self._latest_state = _envelope(
            "state",
            service_pid=os.getpid(),
            backend_running=False,
        )
        self._accept_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        listener.listen(8)
        listener.settimeout(0.5)
        self._listener = listener
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="antijam_ipc_accept",
            daemon=True,
        )
        self._accept_thread.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._sessions_lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()
        try:
            if self.socket_path.is_socket():
                self.socket_path.unlink()
        except OSError:
            pass

    def handle_command(
        self,
        command: str,
        arguments: dict[str, Any],
    ) -> Mapping[str, Any] | None:
        return self._on_command(command, arguments)

    def publish_metrics(self, metrics: Mapping[str, Any]) -> None:
        message = _envelope("metrics", payload=metrics_for_wire(metrics))
        self._latest_metrics = message
        for session in self._session_snapshot():
            session.set_latest_metrics(message)

    def publish_status(self, message: str) -> None:
        self._broadcast(_envelope("status", message=str(message)))

    def publish_failed(self, message: str) -> None:
        self._broadcast(_envelope("failed", message=str(message)))

    def publish_state(self, *, backend_running: bool, **fields: Any) -> None:
        state = _envelope(
            "state",
            service_pid=os.getpid(),
            backend_running=bool(backend_running),
            **_json_safe(fields),
        )
        self._latest_state = state
        self._broadcast(state)

    def _broadcast(self, message: dict[str, Any]) -> None:
        for session in self._session_snapshot():
            session.enqueue(message)

    def _session_snapshot(self) -> list[_ClientSession]:
        with self._sessions_lock:
            return list(self._sessions)

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            listener = self._listener
            if listener is None:
                break
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            session = _ClientSession(self, conn)
            with self._sessions_lock:
                self._sessions.add(session)
            session.enqueue(self._latest_state)
            if self._latest_metrics is not None:
                session.set_latest_metrics(self._latest_metrics)
            session.start()

    def _discard(self, session: _ClientSession) -> None:
        with self._sessions_lock:
            self._sessions.discard(session)

    def _remove_stale_socket(self) -> None:
        if not self.socket_path.exists():
            return
        if not self.socket_path.is_socket():
            raise RuntimeError(
                f"refusing to replace non-socket path: {self.socket_path}"
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            probe.connect(str(self.socket_path))
        except OSError:
            self.socket_path.unlink()
        else:
            raise RuntimeError(
                f"antijamming service already listening at {self.socket_path}"
            )
        finally:
            probe.close()


class JsonIpcClient:
    """Background-reader client shared by Qt and hardware-free tests."""

    def __init__(
        self,
        socket_path: Path | str,
        *,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._on_message = on_message
        self._conn: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None

    def connect(self, timeout_s: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        last_error: OSError | None = None
        while not self._closed.is_set():
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                conn.connect(str(self.socket_path))
            except OSError as exc:
                last_error = exc
                conn.close()
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
                continue
            self._conn = conn
            self._reader = threading.Thread(
                target=self._reader_loop,
                name="antijam_ipc_client",
                daemon=True,
            )
            self._reader.start()
            return
        raise ConnectionError(
            f"could not connect to antijamming service at {self.socket_path}: "
            f"{last_error or 'timeout'}"
        )

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def command(self, command: str, **arguments: Any) -> str:
        conn = self._conn
        if conn is None:
            raise ConnectionError("antijamming IPC client is not connected")
        request_id = uuid.uuid4().hex
        message = {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "type": "command",
            "request_id": request_id,
            "command": str(command),
            "arguments": _json_safe(arguments),
        }
        encoded = (
            json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        with self._send_lock:
            conn.sendall(encoded)
        return request_id

    def _reader_loop(self) -> None:
        conn = self._conn
        if conn is None:
            return
        buffer = bytearray()
        try:
            while not self._closed.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    if not raw.strip():
                        continue
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    if self._on_message is not None:
                        self._on_message(message)
        except OSError:
            pass
        finally:
            self.close()


__all__ = [
    "JsonIpcClient",
    "JsonIpcServer",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "default_socket_path",
    "metrics_for_wire",
]
