"""Typed shared models for scan geometry and operator bearings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# =============================================================================
# Operator Bearing Helpers
# =============================================================================

def normalize_angle_deg(angle_deg: float) -> float:
    """Normalize an azimuth angle into the 0..360 degree interval."""

    return float(angle_deg) % 360.0


def internal_angle_to_operator_bearing_deg(angle_deg: float) -> float:
    """Map the internal steering angle to the operator clockwise bearing."""

    angle = normalize_angle_deg(float(angle_deg))
    return (360.0 - angle) % 360.0


def operator_bearing_axis_for_internal_scan(
    scan_angles_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the sorted operator bearing axis and source-order index."""

    internal_scan = np.asarray(scan_angles_deg, dtype=np.float64)
    display_scan = np.asarray((360.0 - internal_scan) % 360.0, dtype=np.float64)
    order = np.argsort(display_scan)
    return display_scan[order], order


# =============================================================================
# Scan Geometry
# =============================================================================

@dataclass(frozen=True, slots=True)
class AngleScanSpec:
    """Inclusive angular scan definition used by DoA and beamforming plots."""

    min_deg: float = 0.0
    max_deg: float = 359.0
    points: int = 721

    def values(self) -> np.ndarray:
        return self.values_for_size(self.points)

    def values_for_size(self, size: int) -> np.ndarray:
        return np.linspace(
            float(self.min_deg),
            float(self.max_deg),
            max(1, int(size)),
            dtype=np.float64,
        )
