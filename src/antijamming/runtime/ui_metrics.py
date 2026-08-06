"""Structured backend snapshots emitted to the GUI."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

@dataclass(frozen=True, slots=True)
class RuntimeUiMetrics:
    """Structured payload emitted from the backend to the GUI."""

    powers: np.ndarray
    phase_offsets_deg: np.ndarray
    phase_offsets_raw_deg: np.ndarray
    phase_offsets_calibrated_deg: np.ndarray
    complex_samples: np.ndarray
    complex_samples_raw: np.ndarray
    complex_samples_calibrated: np.ndarray
    doa_raw_spectrum: np.ndarray
    doa_deg: float
    doa_display_deg: float
    lcmv_test: dict[str, object]
    rx_signal_health: dict[str, object]
    gnss_snapshot: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        # Preserve the historical metric keys used by the GUI and tests while
        # keeping backend construction typed and centralized.
        return {
            "powers": self.powers,
            "phase_offsets_deg": self.phase_offsets_deg,
            "phase_offsets_raw_deg": self.phase_offsets_raw_deg,
            "phase_offsets_calibrated_deg": self.phase_offsets_calibrated_deg,
            "complex_samples": self.complex_samples,
            "complex_samples_raw": self.complex_samples_raw,
            "complex_samples_calibrated": self.complex_samples_calibrated,
            "doa_raw_spectrum": self.doa_raw_spectrum,
            "doa_deg": self.doa_deg,
            "doa_display_deg": self.doa_display_deg,
            "lcmv_test": self.lcmv_test,
            "rx_signal_health": self.rx_signal_health,
            "gnss_snapshot": self.gnss_snapshot,
        }


__all__ = ["RuntimeUiMetrics"]
