"""Phase-offset estimation and static phase-calibration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np


# =============================================================================
# Phase Offset Estimation
# =============================================================================

# The phase monitor estimates relative phase against the array aggregate.
# Runtime hardware calibration comes only from persisted calibration vectors.

def phase_offsets_deg(buffer: np.ndarray) -> np.ndarray:
    """Estimate per-channel phase offsets relative to the array aggregate."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    if buffer.shape[1] == 0:
        return np.zeros((buffer.shape[0],), dtype=np.float64)

    aggregate = np.mean(buffer, axis=0)
    aggregate_power = float(np.mean(np.abs(aggregate) ** 2))
    if aggregate_power <= 1e-24 or not np.isfinite(aggregate_power):
        return np.zeros((buffer.shape[0],), dtype=np.float64)
    offsets = []
    for ch in range(buffer.shape[0]):
        # Mean cross-array phase is stable for the narrowband calibration tone
        # and for short live chunks used by the GUI phase monitor.
        cross = np.mean(np.asarray(buffer[ch], dtype=np.complex128) * np.conj(aggregate))
        offsets.append(float(np.degrees(np.angle(cross))))
    return np.asarray(offsets, dtype=np.float64)


def phase_correction_vector(buffer: np.ndarray) -> np.ndarray:
    """Build a complex correction vector that aligns channels to the aggregate."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0,), dtype=np.complex128)
    if buffer.shape[1] == 0:
        return np.ones((buffer.shape[0],), dtype=np.complex128)

    return correction_vector_from_phase_offsets_deg(phase_offsets_deg(buffer))


def correction_vector_from_phase_offsets_deg(offsets_deg: np.ndarray | list[float]) -> np.ndarray:
    """Convert measured phase offsets in degrees into complex correction weights."""
    offsets = np.asarray(offsets_deg, dtype=np.float64).reshape(-1)
    if offsets.size == 0:
        return np.zeros((0,), dtype=np.complex128)
    return np.asarray(np.exp(-1j * np.deg2rad(offsets)), dtype=np.complex128)


# =============================================================================
# Calibration File I/O
# =============================================================================

# Calibration files may contain either explicit complex correction weights or
# measured phase offsets. Quality metadata is honored before applying either.

CALIBRATION_MODE_PHASE_ONLY = "phase_only"
CALIBRATION_MODE_COMPLEX_GAIN = "complex_gain"
VALID_CALIBRATION_CORRECTION_MODES = {
    CALIBRATION_MODE_PHASE_ONLY,
    CALIBRATION_MODE_COMPLEX_GAIN,
}


@dataclass(frozen=True, slots=True)
class CalibrationCorrectionSelection:
    """Selected runtime correction vector and proof metadata."""

    file_path: Path
    configured_mode: str
    applied_mode: str
    vector: np.ndarray
    phase_only_vector_available: bool
    complex_gain_vector_available: bool
    fallback_used: bool
    fallback_reason: str
    reference_channel: object

    def metadata(self, *, expected_channel_count: int | None = None) -> dict[str, object]:
        vector = np.asarray(self.vector, dtype=np.complex128).reshape(-1)
        magnitudes = np.abs(vector)
        phases_deg = np.degrees(np.angle(vector))
        power_gain_db = 20.0 * np.log10(
            np.maximum(magnitudes, np.finfo(np.float64).tiny)
        )
        return {
            "calibration_file_path": str(self.file_path),
            "calibration_correction_mode_configured": self.configured_mode,
            "calibration_correction_mode_applied": self.applied_mode,
            "complex_gain_vector_available": bool(self.complex_gain_vector_available),
            "phase_only_vector_available": bool(self.phase_only_vector_available),
            "fallback_used": bool(self.fallback_used),
            "fallback_reason": self.fallback_reason,
            "applied_correction_vector_real": [
                _finite_float(np.real(value)) for value in vector
            ],
            "applied_correction_vector_imag": [
                _finite_float(np.imag(value)) for value in vector
            ],
            "applied_correction_magnitudes": [
                _finite_float(value) for value in magnitudes
            ],
            "applied_correction_phases_deg": [
                _finite_float(value) for value in phases_deg
            ],
            "applied_correction_power_gain_db": [
                _finite_float(value) for value in power_gain_db
            ],
            "reference_channel": self.reference_channel,
            "correction_vector_length": int(vector.size),
            "expected_channel_count": (
                int(expected_channel_count)
                if expected_channel_count is not None
                else None
            ),
        }


def normalize_calibration_correction_mode(mode: object) -> str:
    value = str(mode or CALIBRATION_MODE_COMPLEX_GAIN).strip().lower()
    if value in VALID_CALIBRATION_CORRECTION_MODES:
        return value
    return CALIBRATION_MODE_COMPLEX_GAIN


def load_calibration_correction_selection(
    path: str | Path,
    *,
    mode: str = CALIBRATION_MODE_COMPLEX_GAIN,
    expected_channel_count: int | None = None,
) -> CalibrationCorrectionSelection:
    """Load the requested runtime calibration vector with explicit fallback metadata."""

    resolved = Path(path).expanduser()
    payload = _read_validated_calibration_payload(resolved)
    configured_mode = normalize_calibration_correction_mode(mode)
    if str(mode or "").strip().lower() not in VALID_CALIBRATION_CORRECTION_MODES:
        invalid_mode_reason = (
            f"unknown calibration_correction_mode={mode!r}; using complex_gain"
        )
    else:
        invalid_mode_reason = ""

    phase_vector, phase_error = _calibration_phase_only_vector(payload)
    complex_vector, complex_error = _calibration_complex_gain_vector(payload)
    phase_available = _vector_valid_for_runtime(
        phase_vector,
        expected_channel_count=expected_channel_count,
    )
    complex_available = _vector_valid_for_runtime(
        complex_vector,
        expected_channel_count=expected_channel_count,
    )

    fallback_used = bool(invalid_mode_reason)
    fallback_reason = invalid_mode_reason
    applied_mode = configured_mode
    selected = phase_vector if phase_available else None

    if configured_mode == CALIBRATION_MODE_COMPLEX_GAIN:
        if complex_available:
            selected = complex_vector
            applied_mode = CALIBRATION_MODE_COMPLEX_GAIN
        else:
            fallback_used = True
            applied_mode = CALIBRATION_MODE_PHASE_ONLY
            fallback_reason = (
                "complex_gain requested but invalid: "
                f"{complex_error or _vector_validation_reason(complex_vector, expected_channel_count)}"
            )
            if phase_available:
                selected = phase_vector
            else:
                selected = None
                fallback_reason += (
                    "; phase_only invalid: "
                    f"{phase_error or _vector_validation_reason(phase_vector, expected_channel_count)}"
                )
    elif not phase_available:
        fallback_used = True
        applied_mode = CALIBRATION_MODE_PHASE_ONLY
        selected = None
        fallback_reason = (
            (fallback_reason + "; " if fallback_reason else "")
            + "phase_only invalid: "
            + (phase_error or _vector_validation_reason(phase_vector, expected_channel_count))
        )

    if selected is None:
        fallback_used = True
        selected = np.ones(
            (max(0, int(expected_channel_count or 0)),),
            dtype=np.complex128,
        )
        if selected.size == 0:
            selected = np.zeros((0,), dtype=np.complex128)
        fallback_reason = (
            (fallback_reason + "; " if fallback_reason else "")
            + "using all-ones correction vector"
        )

    return CalibrationCorrectionSelection(
        file_path=resolved,
        configured_mode=configured_mode,
        applied_mode=applied_mode,
        vector=np.asarray(selected, dtype=np.complex128).reshape(-1),
        phase_only_vector_available=phase_available,
        complex_gain_vector_available=complex_available,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        reference_channel=_calibration_reference_channel(payload),
    )


def load_phase_correction_vector(path: str | Path) -> np.ndarray:
    """Load and validate a persisted phase-calibration JSON file."""
    payload = _read_validated_calibration_payload(Path(path).expanduser())
    vector, error = _calibration_phase_only_vector(payload)
    if vector is None:
        raise ValueError(error or f"No correction_vector or phase_offsets_deg in {path}")
    return vector


def _read_validated_calibration_payload(resolved: Path) -> dict[str, object]:
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Phase calibration file must contain a JSON object: {resolved}")
    if payload.get("quality_pass") is False:
        raise ValueError(f"Refusing invalid phase calibration file: {resolved}")
    if "phase_offsets_std_deg" in payload:
        # Refuse noisy calibration captures so a bad file cannot silently
        # degrade DoA or beamforming.
        std = np.asarray(payload["phase_offsets_std_deg"], dtype=np.float64).reshape(-1)
        threshold = float(payload.get("quality_max_phase_std_deg", 3.0))
        if std.size > 1 and float(np.max(std[1:])) > threshold:
            raise ValueError(
                f"Refusing noisy phase calibration file: {resolved} "
                f"(max std {float(np.max(std[1:])):.2f} deg > {threshold:.2f} deg)"
            )
    return payload


def _calibration_phase_only_vector(
    payload: dict[str, object],
) -> tuple[np.ndarray | None, str]:
    if "correction_vector" in payload:
        try:
            return _parse_complex_vector(payload["correction_vector"]), ""
        except Exception as exc:
            return None, f"correction_vector invalid: {exc}"
    if "phase_offsets_deg" in payload:
        try:
            return correction_vector_from_phase_offsets_deg(payload["phase_offsets_deg"]), ""
        except Exception as exc:
            return None, f"phase_offsets_deg invalid: {exc}"
    return None, "missing correction_vector and phase_offsets_deg"


def _calibration_complex_gain_vector(
    payload: dict[str, object],
) -> tuple[np.ndarray | None, str]:
    key = "complex_gain_phase_correction_vector"
    if key not in payload:
        return None, f"missing {key}"
    try:
        return _parse_complex_vector(payload[key]), ""
    except Exception as exc:
        return None, f"{key} invalid: {exc}"


def _parse_complex_vector(values: object) -> np.ndarray:
    if not isinstance(values, list):
        raise ValueError("vector is not a JSON list")
    vector: list[complex] = []
    for item in values:
        if isinstance(item, dict):
            vector.append(complex(float(item["real"]), float(item["imag"])))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            vector.append(complex(float(item[0]), float(item[1])))
        else:
            raise ValueError(f"unsupported vector entry: {item!r}")
    arr = np.asarray(vector, dtype=np.complex128).reshape(-1)
    if arr.size == 0:
        raise ValueError("vector is empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError("vector contains NaN or Inf")
    return arr


def _vector_valid_for_runtime(
    vector: np.ndarray | None,
    *,
    expected_channel_count: int | None,
) -> bool:
    if vector is None:
        return False
    return _vector_validation_reason(vector, expected_channel_count) == ""


def _vector_validation_reason(
    vector: np.ndarray | None,
    expected_channel_count: int | None,
) -> str:
    if vector is None:
        return "vector missing"
    arr = np.asarray(vector, dtype=np.complex128).reshape(-1)
    if arr.size == 0:
        return "vector empty"
    if expected_channel_count is not None and arr.size != int(expected_channel_count):
        return f"vector length {arr.size} != expected {int(expected_channel_count)}"
    if not np.all(np.isfinite(arr)):
        return "vector contains NaN or Inf"
    return ""


def _calibration_reference_channel(payload: dict[str, object]) -> object:
    for key in ("reference_channel", "calibration_reference_channel", "ref_channel"):
        if key in payload:
            return payload.get(key)
    return None


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


# =============================================================================
# Calibration Application
# =============================================================================

# Static calibration is the only runtime correction. If no calibration vector is
# supplied, samples pass through unchanged instead of using any channel as master.

def apply_phase_calibration(
    buffer: np.ndarray,
    correction_vector: np.ndarray | None = None,
) -> np.ndarray:
    """Apply a static correction vector when available."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0, 0), dtype=np.complex128)
    if correction_vector is None:
        return np.asarray(buffer, dtype=np.complex128)
    correction = np.asarray(correction_vector, dtype=np.complex128).reshape(-1)
    if correction.size != buffer.shape[0]:
        raise ValueError(
            f"phase correction size {correction.size} does not match channel count {buffer.shape[0]}"
        )
    return np.asarray(buffer * correction[:, None], dtype=np.complex128)
