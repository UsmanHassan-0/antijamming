"""Logger definitions and per-session log reset helpers."""

import logging
from pathlib import Path


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
    "gnss": ("antijamming.gnss_sdr", "gnss_sdr.log"),
    "health": ("antijamming.stream_health", "stream_health.log"),
    "ui": ("antijamming.ui", "ui_health.log"),
    "errors": ("antijamming.errors", "errors.log"),
}

SESSION_AUXILIARY_LOGS = ("operator_events.log",)


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
# Session Log Reset
# =============================================================================

# Session reset is called when streaming starts, not when the GUI imports. That
# keeps module import side-effect free while still giving each run clean logs.

def reset_session_logs(log_dir: Path, loggers: dict[str, logging.Logger]) -> None:
    """
    Start a new streaming session with clean logs.

    We reset by closing existing handlers and reopening only the known runtime
    logs with mode="w" so truncation happens safely at open time. Helper logs
    under logs/remote_gui_access and GNSS-SDR artifacts are left intact.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
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
        # Ensure the file is actually truncated on session start, even if a handler
        # implementation or filesystem caching behaves unexpectedly.
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
