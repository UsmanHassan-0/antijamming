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


__all__ = [
    "PhaseResult",
    "PhaseWorkItem",
]
