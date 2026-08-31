"""Logger definitions and durable per-run session helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import time


# =============================================================================
# Logger Definitions
# =============================================================================

# Each key is used throughout the runtime, so names and file paths are centralized
# here instead of being duplicated across backend, GNSS bridge, and GUI code.
LOGGER_DEFS: dict[str, tuple[str, str]] = {
    "app": ("antijamming.app", "app.log"),
    "hw": ("antijamming.hardware", "usrp_hardware.log"),
    "stream": ("antijamming.stream", "stream.log"),
    "transport": ("antijamming.transport", "transport.log"),
    "handoff": ("antijamming.gnss_handoff", "gnss_handoff.log"),
    "phase": ("antijamming.phase_alignment", "phase_alignment.log"),
    "doa": ("antijamming.doa", "doa.log"),
    "lcmv": ("antijamming.lcmv", "lcmv.log"),
    "analysis": ("antijamming.analysis", "analysis.log"),
    "lcmv_pattern": ("antijamming.lcmv_pattern", "lcmv_pattern_absolute.jsonl"),
    "spatial_vector": (
        "antijamming.spatial_vector",
        "spatial_vector_diagnostics.jsonl",
    ),
    "runtime_evidence": (
        "antijamming.runtime_evidence",
        "runtime_evidence.jsonl",
    ),
    "gnss": ("antijamming.gnss_sdr", "gnss_sdr.log"),
    "health": ("antijamming.stream_health", "stream_health.log"),
    "ui": ("antijamming.ui", "ui_health.log"),
    "errors": ("antijamming.errors", "errors.log"),
}

SESSION_AUXILIARY_LOGS = ("operator_events.log",)
CURRENT_SESSION_FILE = "CURRENT_SESSION"
LATEST_SESSION_FILE = "LATEST_SESSION"


@dataclass
class RuntimeLogSession:
    """Filesystem identity and timing origin for one Start-to-Stop run."""

    session_id: str
    session_dir: Path
    started_utc: str
    started_local: str
    started_wall_time_ns: int
    started_monotonic_ns: int
    finalized: bool = False

    def elapsed_s(self) -> float:
        return max(0.0, (time.monotonic_ns() - self.started_monotonic_ns) / 1e9)

    def manifest(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "started_utc": self.started_utc,
            "started_local": self.started_local,
            "started_wall_time_ns": self.started_wall_time_ns,
            "started_monotonic_ns": self.started_monotonic_ns,
            "finalized": bool(self.finalized),
        }


# =============================================================================
# Logger Setup
# =============================================================================


class ImmediateFileHandler(logging.FileHandler):
    """File handler that flushes each emitted record to the OS immediately."""

    def flush(self) -> None:
        # Flush is enough for tail/VS Code/other readers to see new lines.
        # fsync() on every runtime log line is far too expensive for the
        # realtime RX/GNSS path and can create avoidable latency spikes.
        super().flush()


def _build_file_handler(path: Path, mode: str = "a") -> logging.FileHandler:
    handler = ImmediateFileHandler(path, mode=mode, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(fmt)
    return handler


def _close_logger_handlers(logger: logging.Logger) -> None:
    """Detach and close every handler currently owned by one named logger."""

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass


def setup_logging(
    log_dir: Path,
    *,
    enabled: bool = True,
) -> dict[str, logging.Logger]:
    """Create named runtime loggers, or silent endpoints when disabled."""
    if enabled:
        log_dir.mkdir(parents=True, exist_ok=True)

    logger_map: dict[str, logging.Logger] = {}
    for key, (logger_name, file_name) in LOGGER_DEFS.items():
        # Clear inherited handlers on every setup so repeated GUI launches in one
        # Python process do not duplicate log lines.
        log_path = log_dir / file_name
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        _close_logger_handlers(logger)
        logger.propagate = False
        logger.disabled = not enabled
        if enabled:
            logger.addHandler(_build_file_handler(log_path, mode="a"))
        logger_map[key] = logger

    return logger_map


# =============================================================================
# Session Log Lifecycle
# =============================================================================


def _timestamp_pair() -> tuple[str, str, int]:
    now = datetime.now().astimezone()
    wall_time_ns = time.time_ns()
    return (
        datetime.fromtimestamp(wall_time_ns / 1e9, timezone.utc).isoformat(),
        now.isoformat(),
        wall_time_ns,
    )


def _session_manifest_path(session: RuntimeLogSession) -> Path:
    return session.session_dir / "session_manifest.json"


def _write_manifest(session: RuntimeLogSession, **updates: object) -> None:
    payload = session.manifest()
    path = _session_manifest_path(session)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict):
            payload = {**existing, **payload}
    payload.update(updates)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _session_from_pointer(log_dir: Path) -> RuntimeLogSession | None:
    pointer = log_dir / CURRENT_SESSION_FILE
    try:
        session_dir = Path(pointer.read_text(encoding="utf-8").strip()).resolve()
        payload = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or bool(payload.get("finalized", False)):
        return None
    try:
        return RuntimeLogSession(
            session_id=str(payload["session_id"]),
            session_dir=session_dir,
            started_utc=str(payload["started_utc"]),
            started_local=str(payload["started_local"]),
            started_wall_time_ns=int(payload["started_wall_time_ns"]),
            started_monotonic_ns=int(payload["started_monotonic_ns"]),
            finalized=False,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _copy_session_artifacts(log_dir: Path, session_dir: Path) -> list[str]:
    """Snapshot current-run evidence without copying FIFO or IQ dump data."""

    copied: list[str] = []
    for file_name in [
        *(definition[1] for definition in LOGGER_DEFS.values()),
        *SESSION_AUXILIARY_LOGS,
        "uhd_console.log",
    ]:
        source = log_dir / file_name
        if not source.is_file():
            continue
        destination = session_dir / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination)
        except OSError:
            continue
        copied.append(str(destination.relative_to(session_dir)))

    nested = (
        log_dir / "gnss-sdr" / "runtime" / "fifo_gps_l1.conf",
        log_dir / "gnss-sdr" / "runtime" / "console.log",
        log_dir / "gnss-sdr" / "glog" / "receiver.log",
    )
    for source in nested:
        if not source.is_file():
            continue
        destination = session_dir / source.relative_to(log_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            # The GNSS bridge writes the exact PID-scoped runtime files here.
            # The stable root directory can contain a previous run
            # and must never overwrite that evidence during finalization.
            copied.append(str(destination.relative_to(session_dir)))
            continue
        try:
            shutil.copy2(source, destination)
        except OSError:
            continue
        copied.append(str(destination.relative_to(session_dir)))
    return copied


def finalize_session_logs(
    log_dir: Path,
    session: RuntimeLogSession | None,
    loggers: dict[str, logging.Logger],
    *,
    stop_reason: str,
    outcome: str = "stopped",
) -> None:
    """Make a complete immutable-by-convention copy of the current run logs."""

    if session is None or session.finalized:
        return
    for logger in loggers.values():
        for handler in list(logger.handlers):
            try:
                handler.flush()
            except Exception:
                pass
    copied = _copy_session_artifacts(log_dir, session.session_dir)
    stopped_utc, stopped_local, stopped_wall_time_ns = _timestamp_pair()
    session.finalized = True
    _write_manifest(
        session,
        stopped_utc=stopped_utc,
        stopped_local=stopped_local,
        stopped_wall_time_ns=stopped_wall_time_ns,
        duration_monotonic_s=session.elapsed_s(),
        stop_reason=str(stop_reason),
        outcome=str(outcome),
        copied_artifacts=sorted(copied),
        finalized=True,
    )
    (log_dir / LATEST_SESSION_FILE).write_text(
        str(session.session_dir) + "\n",
        encoding="utf-8",
    )
    current = log_dir / CURRENT_SESSION_FILE
    try:
        if current.read_text(encoding="utf-8").strip() == str(session.session_dir):
            current.unlink()
    except OSError:
        pass


def _recover_unfinalized_session(
    log_dir: Path,
    loggers: dict[str, logging.Logger],
) -> None:
    previous = _session_from_pointer(log_dir)
    if previous is None:
        return
    finalize_session_logs(
        log_dir,
        previous,
        loggers,
        stop_reason="recovered before next Start; prior process did not finalize",
        outcome="recovered_after_unclean_end",
    )


def reset_session_logs(
    log_dir: Path,
    loggers: dict[str, logging.Logger],
) -> RuntimeLogSession:
    """Start a clean root log set and a durable per-run archive directory.

    Root filenames remain stable for tailing and the existing summary tool.
    On Stop they are copied into ``logs/runs/<session_id>``. If a process dies
    before Stop, the next Start recovers those root logs before truncation.
    """

    log_dir.mkdir(parents=True, exist_ok=True)
    _recover_unfinalized_session(log_dir, loggers)

    for file_name in SESSION_AUXILIARY_LOGS:
        try:
            (log_dir / file_name).write_text("", encoding="utf-8")
        except Exception:
            pass
    for logger in loggers.values():
        _close_logger_handlers(logger)

    for key, (_logger_name, file_name) in LOGGER_DEFS.items():
        logger = loggers.get(key)
        log_path = log_dir / file_name
        try:
            log_path.write_text("", encoding="utf-8")
        except Exception:
            pass
        for rotated in log_dir.glob(f"{file_name}.*"):
            try:
                if rotated.is_file() or rotated.is_symlink():
                    rotated.unlink()
            except FileNotFoundError:
                continue
            except Exception:
                pass
        if logger is None:
            continue
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(_build_file_handler(log_path, mode="w"))

    started_utc, started_local, started_wall_time_ns = _timestamp_pair()
    session_id = datetime.fromtimestamp(
        started_wall_time_ns / 1e9,
        timezone.utc,
    ).strftime("%Y%m%dT%H%M%S.%fZ") + f"_pid{os.getpid()}"
    session_dir = (log_dir / "runs" / session_id).resolve()
    session_dir.mkdir(parents=True, exist_ok=False)
    session = RuntimeLogSession(
        session_id=session_id,
        session_dir=session_dir,
        started_utc=started_utc,
        started_local=started_local,
        started_wall_time_ns=started_wall_time_ns,
        started_monotonic_ns=time.monotonic_ns(),
    )
    _write_manifest(session, outcome="running", finalized=False)
    (log_dir / CURRENT_SESSION_FILE).write_text(
        str(session_dir) + "\n",
        encoding="utf-8",
    )
    return session
