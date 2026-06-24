"""Pure algorithm pipeline helpers shared by GUI and runtime code."""

from __future__ import annotations

import numpy as np

from antijamming.dsp.beamforming.lcmv import (
    apply_beamformer,
    dominant_spatial_signature,
    lcmv_pattern_db,
    lcmv_weights,
    uniform_weights,
)
from antijamming.dsp.doa.music import music_spectrum
from antijamming.dsp.phase.alignment import apply_phase_calibration, phase_offsets_deg


# =============================================================================
# Phase Monitor Helpers
# =============================================================================

# Tone-bin mode is used when a conducted calibration tone is present. It isolates
# phase at the expected tone instead of relying on whole-chunk cross-correlation.

def _tone_bin_phase_offsets_deg(
    buffer: np.ndarray,
    sample_rate_hz: float,
    tone_offset_hz: float,
    ref_channel: int = 0,
) -> np.ndarray:
    data = np.asarray(buffer, dtype=np.complex128)
    if data.ndim != 2 or data.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    if data.shape[1] == 0:
        return np.zeros((data.shape[0],), dtype=np.float64)

    ref_channel = max(0, min(int(ref_channel), data.shape[0] - 1))
    sample_rate_hz = float(sample_rate_hz)
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        # Without a valid sample rate we cannot demodulate a tone bin; fall back
        # to the standard chunk-wide phase estimator.
        return phase_offsets_deg(data, ref_channel=ref_channel)

    n = np.arange(data.shape[1], dtype=np.float64)
    mixer = np.exp(
        -1j * 2.0 * np.pi * float(tone_offset_hz) * n / sample_rate_hz
    ).astype(np.complex128)
    tone = np.mean(data * mixer[None, :], axis=1)
    ref = tone[ref_channel]

    offsets: list[float] = []
    for ch in range(data.shape[0]):
        if ch == ref_channel:
            offsets.append(0.0)
            continue
        cross = tone[ch] * np.conj(ref)
        offsets.append(float(np.degrees(np.angle(cross))))
    return np.asarray(offsets, dtype=np.float64)


def _estimate_ref_tone_offset_hz(
    buffer: np.ndarray,
    sample_rate_hz: float,
    expected_tone_offset_hz: float,
    ref_channel: int = 0,
    search_half_span_hz: float = 10000.0,
) -> float:
    data = np.asarray(buffer, dtype=np.complex128)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
        return float(expected_tone_offset_hz)

    sample_rate_hz = float(sample_rate_hz)
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        return float(expected_tone_offset_hz)

    ref_channel = max(0, min(int(ref_channel), data.shape[0] - 1))
    ref = np.asarray(data[ref_channel], dtype=np.complex128)
    # Oversized FFT improves visual stability when the tone is not exactly on a
    # bin. This is UI/monitoring work, not a sample-perfect carrier tracker.
    n = max(ref.size, 8)
    n_fft = int(max(8192, 1 << int(np.ceil(np.log2(n * 16)))))
    window = np.hanning(ref.size).astype(np.float64)
    if not np.any(window):
        window = np.ones((ref.size,), dtype=np.float64)
    spectrum = np.fft.fftshift(np.fft.fft(ref * window, n=n_fft))
    freqs_hz = np.fft.fftshift(np.fft.fftfreq(n_fft, d=1.0 / sample_rate_hz))
    expected = float(expected_tone_offset_hz)
    half_span = max(1000.0, float(search_half_span_hz))
    mask = np.abs(freqs_hz - expected) <= half_span
    if not np.any(mask):
        return expected
    masked_spectrum = np.abs(spectrum[mask])
    masked_freqs = freqs_hz[mask]
    peak_idx = int(np.argmax(masked_spectrum))
    if 0 < peak_idx < masked_spectrum.size - 1:
        # Parabolic interpolation on log magnitude gives a small sub-bin
        # correction without bringing in a heavier spectral estimator.
        left = float(np.log(max(masked_spectrum[peak_idx - 1], 1e-300)))
        center = float(np.log(max(masked_spectrum[peak_idx], 1e-300)))
        right = float(np.log(max(masked_spectrum[peak_idx + 1], 1e-300)))
        denom = left - 2.0 * center + right
        if abs(denom) > 1e-30 and masked_freqs.size > 1:
            bin_delta = 0.5 * (left - right) / denom
            bin_delta = max(-0.5, min(0.5, bin_delta))
            return float(masked_freqs[peak_idx] + bin_delta * (masked_freqs[1] - masked_freqs[0]))
    return float(masked_freqs[peak_idx])


# =============================================================================
# Phase Metrics
# =============================================================================

# Phase metrics carry both raw and calibrated views because the GUI shows the
# before/after effect of static or dynamic phase alignment.

def compute_phase_metrics(
    buffer: np.ndarray,
    ref_channel: int = 0,
    preview_cols: int | None = None,
    phase_correction_vector: np.ndarray | None = None,
    sample_rate_hz: float | None = None,
    phase_monitor_tone_offset_hz: float = 0.0,
    phase_monitor_use_tone_bin: bool = False,
) -> dict:
    """Compute phase, power, and optional preview data for one sample buffer."""
    raw_buffer = np.asarray(buffer, dtype=np.complex128)
    corrected_buffer = apply_phase_calibration(
        raw_buffer,
        ref_channel=ref_channel,
        correction_vector=phase_correction_vector,
    )
    powers = np.mean(np.abs(raw_buffer) ** 2, axis=1)
    tone_monitor_estimated_offset_hz = float(phase_monitor_tone_offset_hz)
    if bool(phase_monitor_use_tone_bin):
        # Estimate the actual reference-channel tone offset first, then use that
        # same offset for every channel so relative phases stay comparable.
        tone_monitor_estimated_offset_hz = _estimate_ref_tone_offset_hz(
            raw_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            expected_tone_offset_hz=float(phase_monitor_tone_offset_hz),
            ref_channel=ref_channel,
        )
        raw_offsets = _tone_bin_phase_offsets_deg(
            raw_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            tone_offset_hz=tone_monitor_estimated_offset_hz,
            ref_channel=ref_channel,
        )
        corrected_offsets = _tone_bin_phase_offsets_deg(
            corrected_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            tone_offset_hz=tone_monitor_estimated_offset_hz,
            ref_channel=ref_channel,
        )
    else:
        raw_offsets = phase_offsets_deg(raw_buffer, ref_channel=ref_channel)
        corrected_offsets = phase_offsets_deg(corrected_buffer, ref_channel=ref_channel)

    metrics = {
        "raw_buffer": raw_buffer,
        "calibrated_buffer": corrected_buffer,
        "powers": powers,
        "phase_offsets_deg": raw_offsets,
        "phase_offsets_raw_deg": raw_offsets,
        "phase_offsets_calibrated_deg": corrected_offsets,
        "phase_estimator": "tone_bin_demod" if phase_monitor_use_tone_bin else "chunk_crosscorr",
        "phase_monitor_estimated_offset_hz": tone_monitor_estimated_offset_hz,
    }
    if preview_cols is not None and preview_cols > 0:
        cols = min(int(preview_cols), raw_buffer.shape[1])
        metrics["complex_samples_raw"] = np.asarray(raw_buffer[:, :cols], dtype=np.complex64)
        metrics["complex_samples_calibrated"] = np.asarray(corrected_buffer[:, :cols], dtype=np.complex64)
    return metrics


# =============================================================================
# Direction Finding Metrics
# =============================================================================

def compute_doa_metrics(
    corrected_buffer: np.ndarray,
    center_freq_hz: float,
    scan_angles_deg: np.ndarray,
    uca_radius_m: float,
    n_sources: int = 1,
    doa_method: str = "music",
) -> dict:
    """Compute MUSIC DoA metrics for the realtime receiver."""
    method = "music"
    corrected = np.asarray(corrected_buffer, dtype=np.complex128)
    doa_raw_spectrum = music_spectrum(
        x=corrected,
        rf_freq_hz=center_freq_hz,
        scan_angles_deg=scan_angles_deg,
        uca_radius_m=uca_radius_m,
        n_sources=max(int(n_sources), 1),
        normalize=False,
    )
    doa_spectrum = doa_raw_spectrum / (np.max(doa_raw_spectrum) + 1e-12)
    doa_deg = float(scan_angles_deg[int(np.argmax(doa_spectrum))])

    return {
        "doa_method": method,
        "n_sources": max(int(n_sources), 1),
        "doa_raw_spectrum": np.asarray(doa_raw_spectrum, dtype=np.float64),
        "doa_spectrum": np.asarray(doa_spectrum, dtype=np.float64),
        "music_spectrum": np.asarray(doa_spectrum, dtype=np.float64),
        "doa_deg": doa_deg,
    }


# =============================================================================
# Beamforming Metrics
# =============================================================================

# Beamforming metrics are computed from phase-calibrated samples. The backend
# decides whether these weights are active; this function only solves them.

def compute_lcmv_metrics(
    corrected_buffer: np.ndarray,
    center_freq_hz: float,
    scan_angles_deg: np.ndarray,
    uca_radius_m: float,
    null_theta_deg: float,
    ref_channel: int | None = None,
) -> dict:
    """Compute LCMV weights and the response curve for the requested null angle."""
    corrected = np.asarray(corrected_buffer, dtype=np.complex128)
    null_signature = dominant_spatial_signature(corrected)
    weights = lcmv_weights(
        x=corrected,
        rf_freq_hz=center_freq_hz,
        null_theta_deg=float(null_theta_deg),
        uca_radius_m=uca_radius_m,
        ref_channel=ref_channel,
        null_vector=null_signature,
    )
    pattern_db = lcmv_pattern_db(
        x=corrected,
        rf_freq_hz=center_freq_hz,
        scan_angles_deg=scan_angles_deg,
        null_theta_deg=float(null_theta_deg),
        uca_radius_m=uca_radius_m,
        ref_channel=ref_channel,
        null_vector=null_signature,
        weights=weights,
    )
    return {
        "weights": np.asarray(weights, dtype=np.complex128),
        "lcmv_pattern_db": np.asarray(pattern_db, dtype=np.float64),
        "null_signature": np.asarray(null_signature, dtype=np.complex128),
    }


def compute_gnss_output_vector(
    buffer: np.ndarray,
    ref_channel: int,
    beamformer_weights: np.ndarray,
    phase_correction_vector: np.ndarray | None = None,
) -> np.ndarray:
    """Render the calibrated, beamformed one-channel stream handed to GNSS-SDR."""
    source = np.asarray(buffer, dtype=np.complex128)
    if source.ndim != 2 or source.shape[1] == 0:
        return np.zeros((0,), dtype=np.complex64)
    corrected = apply_phase_calibration(
        source,
        ref_channel=ref_channel,
        correction_vector=phase_correction_vector,
    )
    return apply_beamformer(corrected, np.asarray(beamformer_weights, dtype=np.complex128))


# =============================================================================
# Combined Realtime Metrics
# =============================================================================

# This convenience function is used by tests and simple callers. The threaded
# backend uses the individual stage helpers so expensive work can run in queues.

def compute_realtime_metrics(
    buffer: np.ndarray,
    center_freq_hz: float,
    scan_angles_deg: np.ndarray,
    uca_radius_m: float,
    ref_channel: int = 0,
    n_sources: int = 1,
    doa_method: str = "music",
    phase_correction_vector: np.ndarray | None = None,
) -> dict:
    """Compute the combined phase, DoA, and LCMV metrics for a runtime chunk."""
    phase_metrics = compute_phase_metrics(
        buffer=buffer,
        ref_channel=ref_channel,
        phase_correction_vector=phase_correction_vector,
    )
    doa_metrics = compute_doa_metrics(
        corrected_buffer=phase_metrics["calibrated_buffer"],
        center_freq_hz=center_freq_hz,
        scan_angles_deg=scan_angles_deg,
        uca_radius_m=uca_radius_m,
        n_sources=n_sources,
        doa_method=doa_method,
    )
    lcmv_metrics = compute_lcmv_metrics(
        corrected_buffer=phase_metrics["calibrated_buffer"],
        center_freq_hz=center_freq_hz,
        scan_angles_deg=scan_angles_deg,
        uca_radius_m=uca_radius_m,
        null_theta_deg=float(doa_metrics["doa_deg"]),
    )

    return {
        "powers": phase_metrics["powers"],
        "phase_offsets_deg": phase_metrics["phase_offsets_deg"],
        "phase_offsets_raw_deg": phase_metrics["phase_offsets_raw_deg"],
        "phase_offsets_calibrated_deg": phase_metrics["phase_offsets_calibrated_deg"],
        "complex_samples": phase_metrics["raw_buffer"],
        "complex_samples_raw": phase_metrics["raw_buffer"],
        "complex_samples_calibrated": phase_metrics["calibrated_buffer"],
        "i_samples": np.real(phase_metrics["raw_buffer"]),
        "i_samples_raw": np.real(phase_metrics["raw_buffer"]),
        "i_samples_calibrated": np.real(phase_metrics["calibrated_buffer"]),
        "music_spectrum": doa_metrics["music_spectrum"],
        "doa_spectrum": doa_metrics["doa_spectrum"],
        "doa_raw_spectrum": doa_metrics["doa_raw_spectrum"],
        "doa_method": doa_metrics["doa_method"],
        "n_sources": doa_metrics["n_sources"],
        "doa_deg": doa_metrics["doa_deg"],
        "algorithm_mode": "lcmv",
        "lcmv_pattern_db": np.asarray(lcmv_metrics["lcmv_pattern_db"], dtype=np.float64),
    }
