"""Logger definitions and durable per-run session helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
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
    "per_prn_weights": (
        "antijamming.per_prn_weights",
        "per_prn_weights.jsonl",
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


def setup_logging(log_dir: Path) -> dict[str, logging.Logger]:
    """Create named runtime loggers with stable, non-rotating file names."""
    log_dir.mkdir(parents=True, exist_ok=True)

    logger_map: dict[str, logging.Logger] = {}
    for key, (logger_name, file_name) in LOGGER_DEFS.items():
        # Clear inherited handlers on every setup so repeated GUI launches in one
        # Python process do not duplicate log lines.
        log_path = log_dir / file_name
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.propagate = False
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


def _git_output(repo_root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _source_tree_provenance() -> dict[str, object]:
    """Fingerprint the exact committed, tracked-dirty, and untracked source."""

    repo_root = Path(__file__).resolve().parents[3]
    head = _git_output(repo_root, "rev-parse", "HEAD")
    status = _git_output(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    tracked_diff = _git_output(repo_root, "diff", "--binary", "HEAD", "--")
    cached_diff = _git_output(repo_root, "diff", "--binary", "--cached", "--")
    untracked = _git_output(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository_root": str(repo_root),
        "git_available": head is not None,
        "git_head": (
            head.decode("ascii", errors="replace").strip()
            if head is not None
            else None
        ),
        "git_status_porcelain_v1": (
            status.decode(errors="surrogateescape").replace("\0", "\n").rstrip()
            if status is not None
            else None
        ),
        "git_status_sha256": (
            hashlib.sha256(status).hexdigest() if status is not None else None
        ),
        "git_tracked_diff_sha256": (
            hashlib.sha256(tracked_diff).hexdigest()
            if tracked_diff is not None
            else None
        ),
        "git_tracked_diff_bytes": (
            len(tracked_diff) if tracked_diff is not None else None
        ),
        "git_cached_diff_sha256": (
            hashlib.sha256(cached_diff).hexdigest()
            if cached_diff is not None
            else None
        ),
        "git_cached_diff_bytes": (
            len(cached_diff) if cached_diff is not None else None
        ),
    }
    untracked_files: list[dict[str, object]] = []
    for encoded in (untracked or b"").split(b"\0"):
        if not encoded:
            continue
        relative = os.fsdecode(encoded)
        candidate = repo_root / relative
        record: dict[str, object] = {"path": relative}
        try:
            if candidate.is_symlink():
                target = os.readlink(candidate)
                target_bytes = os.fsencode(target)
                record.update(
                    {
                        "type": "symlink",
                        "target": target,
                        "bytes": len(target_bytes),
                        "sha256": hashlib.sha256(target_bytes).hexdigest(),
                    }
                )
            elif candidate.is_file():
                record.update(
                    {
                        "type": "file",
                        "bytes": candidate.stat().st_size,
                        "sha256": _sha256_file(candidate),
                    }
                )
            else:
                record["type"] = "missing_or_non_regular"
        except OSError as exc:
            record.update({"type": "unreadable", "error": str(exc)})
        untracked_files.append(record)
    payload["untracked_files"] = untracked_files
    return payload


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
            # The stable compatibility directory can contain a previous run
            # and must never overwrite that evidence during finalization.
            copied.append(str(destination.relative_to(session_dir)))
            continue
        try:
            shutil.copy2(source, destination)
        except OSError:
            continue
        copied.append(str(destination.relative_to(session_dir)))
    return copied


def _session_artifact_inventory(session_dir: Path) -> list[dict[str, object]]:
    """Fingerprint every archived artifact after writers have been flushed.

    The manifest cannot fingerprint itself because finalizing the manifest
    changes its own bytes.  Everything else in the PID-scoped run directory is
    recorded with a relative path, byte count, and SHA-256 digest so later
    audits can prove exactly which evidence files they inspected.
    """

    inventory: list[dict[str, object]] = []
    for path in sorted(session_dir.rglob("*")):
        if path == session_dir / "session_manifest.json":
            continue
        if not path.is_file():
            continue
        try:
            inventory.append(
                {
                    "path": str(path.relative_to(session_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        except OSError as exc:
            inventory.append(
                {
                    "path": str(path.relative_to(session_dir)),
                    "error": str(exc),
                }
            )
    return inventory


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
    artifact_inventory = _session_artifact_inventory(session.session_dir)
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
        artifact_inventory=artifact_inventory,
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
        for handler in list(logger.handlers):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
        logger.handlers.clear()

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
    _write_manifest(
        session,
        outcome="running",
        finalized=False,
        source_provenance=_source_tree_provenance(),
    )
    (log_dir / CURRENT_SESSION_FILE).write_text(
        str(session_dir) + "\n",
        encoding="utf-8",
    )
    return session
