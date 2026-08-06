"""Typed work items passed between backend DSP stages."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class PhaseWorkItem:
    """Raw RX chunk queued for phase processing."""

    chunk: np.ndarray


@dataclass(slots=True)
class PhaseResult:
    """Phase-calibrated chunk passed from phase processing to DoA."""

    calibrated_chunk: np.ndarray
    raw_chunk: np.ndarray | None = None
    raw_power_metrics: dict[str, object] | None = None
    cal_power_metrics: dict[str, object] | None = None


__all__ = [
    "PhaseResult",
    "PhaseWorkItem",
]
