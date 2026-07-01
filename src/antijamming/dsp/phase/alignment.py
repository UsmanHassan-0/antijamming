"""Phase-offset estimation and static phase-calibration helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


# =============================================================================
# Phase Offset Estimation
# =============================================================================

# The phase monitor estimates relative phase against a configurable reference
# channel. It does not attempt hardware calibration by itself; persisted
# calibration vectors are loaded separately and then applied by the pipeline.

def _normalized_ref_channel(buffer: np.ndarray, ref_channel: int) -> int:
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return 0
    return max(0, min(int(ref_channel), buffer.shape[0] - 1))


def phase_offsets_deg(buffer: np.ndarray, ref_channel: int = 0) -> np.ndarray:
    """Estimate per-channel phase offsets relative to a reference channel."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    if buffer.shape[1] == 0:
        return np.zeros((buffer.shape[0],), dtype=np.float64)

    ref_channel = _normalized_ref_channel(buffer, ref_channel)
    offsets: list[float] = []
    ref = np.asarray(buffer[ref_channel], dtype=np.complex128)
    for ch in range(buffer.shape[0]):
        if ch == ref_channel:
            offsets.append(0.0)
            continue
        # Mean cross-channel phase is stable for the narrowband calibration tone
        # and for short live chunks used by the GUI phase monitor.
        cross = np.mean(np.asarray(buffer[ch], dtype=np.complex128) * np.conj(ref))
        offsets.append(float(np.degrees(np.angle(cross))))
    return np.asarray(offsets, dtype=np.float64)


def phase_correction_vector(buffer: np.ndarray, ref_channel: int = 0) -> np.ndarray:
    """Build a complex correction vector that aligns channels to the reference."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0,), dtype=np.complex128)
    if buffer.shape[1] == 0:
        return np.ones((buffer.shape[0],), dtype=np.complex128)

    ref_channel = _normalized_ref_channel(buffer, ref_channel)
    ref = np.asarray(buffer[ref_channel], dtype=np.complex128)
    correction = np.ones((buffer.shape[0],), dtype=np.complex128)

    for ch in range(buffer.shape[0]):
        if ch == ref_channel:
            continue
        cross = np.mean(np.asarray(buffer[ch], dtype=np.complex128) * np.conj(ref))
        if np.abs(cross) > 0.0:
            # Use the negative measured phase so multiplying by the correction
            # rotates each channel back toward the reference channel.
            correction[ch] = np.exp(-1j * np.angle(cross))
    return correction


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

def load_phase_correction_vector(path: str | Path) -> np.ndarray:
    """Load and validate a persisted phase-calibration JSON file."""
    resolved = Path(path).expanduser()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("quality_pass") is False:
        raise ValueError(f"Refusing invalid phase calibration file: {resolved}")
    if "phase_offsets_std_deg" in payload:
        # Refuse calibration captures with noisy non-reference channels so a bad
        # file cannot silently degrade DoA or beamforming.
        std = np.asarray(payload["phase_offsets_std_deg"], dtype=np.float64).reshape(-1)
        threshold = float(payload.get("quality_max_phase_std_deg", 3.0))
        if std.size > 1 and float(np.max(std[1:])) > threshold:
            raise ValueError(
                f"Refusing noisy phase calibration file: {resolved} "
                f"(max std {float(np.max(std[1:])):.2f} deg > {threshold:.2f} deg)"
            )
    if "correction_vector" in payload:
        vector: list[complex] = []
        for item in payload["correction_vector"]:
            if isinstance(item, dict):
                vector.append(complex(float(item["real"]), float(item["imag"])))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                vector.append(complex(float(item[0]), float(item[1])))
            else:
                raise ValueError(f"Unsupported correction_vector entry: {item!r}")
        return np.asarray(vector, dtype=np.complex128)
    if "phase_offsets_deg" in payload:
        return correction_vector_from_phase_offsets_deg(payload["phase_offsets_deg"])
    raise ValueError(f"No correction_vector or phase_offsets_deg in {path}")


# =============================================================================
# Calibration Application
# =============================================================================

# Static calibration is preferred when supplied. Dynamic per-chunk correction is
# retained as the fallback so legacy behavior still works without a file.

def apply_phase_calibration(
    buffer: np.ndarray,
    ref_channel: int = 0,
    correction_vector: np.ndarray | None = None,
) -> np.ndarray:
    """Apply static correction when available, otherwise estimate alignment live."""
    buffer = np.asarray(buffer, dtype=np.complex128)
    if buffer.ndim != 2 or buffer.shape[0] == 0:
        return np.zeros((0, 0), dtype=np.complex128)
    if correction_vector is None:
        correction_vector = phase_correction_vector(buffer, ref_channel=ref_channel)
    correction = np.asarray(correction_vector, dtype=np.complex128).reshape(-1)
    if correction.size != buffer.shape[0]:
        raise ValueError(
            f"phase correction size {correction.size} does not match channel count {buffer.shape[0]}"
        )
    return np.asarray(buffer * correction[:, None], dtype=np.complex128)
