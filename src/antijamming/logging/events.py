"""Atomic, time-aligned operator and runtime event recording."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Mapping

from .setup import CURRENT_SESSION_FILE


RF_EVENTS = frozenset(
    {
        "jammer_on",
        "jammer_off",
        "jammer_moved",
        "bladeRF_on",
        "bladeRF_off",
        "lcmv_on",
        "lcmv_off",
        "attenuation_db",
        "bladeRF_gain_db",
        "expected_sources_changed",
        "stream_start",
        "stream_stop",
        "notes",
    }
)


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, encoded)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def current_session_dir(log_dir: Path) -> Path | None:
    try:
        path = Path(
            (Path(log_dir) / CURRENT_SESSION_FILE).read_text(encoding="utf-8").strip()
        ).resolve()
    except (OSError, ValueError):
        return None
    try:
        path.relative_to(Path(log_dir).resolve())
    except ValueError:
        return None
    return path if path.is_dir() else None


def record_event(
    log_dir: Path,
    event: str,
    *,
    source: str,
    attenuation_db: float | None = None,
    bladeRF_gain_db: float | None = None,
    notes: str = "",
    context: Mapping[str, object] | None = None,
    session_id: str | None = None,
    session_elapsed_s: float | None = None,
    append_current_session: bool = True,
) -> dict[str, object]:
    """Append one event to both the stable and per-run event ledgers."""

    normalized = str(event).strip()
    if normalized not in RF_EVENTS:
        raise ValueError(f"unsupported RF/runtime event: {event!r}")
    wall_time_ns = time.time_ns()
    local_now = datetime.fromtimestamp(wall_time_ns / 1e9).astimezone()
    utc_now = datetime.fromtimestamp(wall_time_ns / 1e9, timezone.utc)
    payload: dict[str, object] = {
        "schema_version": 1,
        "timestamp": utc_now.isoformat(),
        "timestamp_utc": utc_now.isoformat(),
        "timestamp_local": local_now.isoformat(),
        "wall_time_unix_ns": wall_time_ns,
        "monotonic_ns": time.monotonic_ns(),
        "event": normalized,
        "source": str(source),
        "session_id": session_id,
        "session_elapsed_s": session_elapsed_s,
        "attenuation_db": attenuation_db,
        "bladeRF_gain_db": bladeRF_gain_db,
        "notes": str(notes),
    }
    if context:
        payload["context"] = dict(context)

    root = Path(log_dir).expanduser().resolve()
    _append_jsonl(root / "operator_events.log", payload)
    session_dir = current_session_dir(root) if append_current_session else None
    if session_dir is not None:
        _append_jsonl(session_dir / "operator_events.jsonl", payload)
    return payload
