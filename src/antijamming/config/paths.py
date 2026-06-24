"""Repository and runtime path helpers."""

from __future__ import annotations

from pathlib import Path


# Repository root used to keep product runtime artifacts under antijamming/logs
# even when the Python module is launched from another working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]


__all__ = ["REPO_ROOT"]
