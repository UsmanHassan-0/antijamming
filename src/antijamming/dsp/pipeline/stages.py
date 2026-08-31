"""Pure algorithm pipeline helpers shared by GUI and runtime code."""

from __future__ import annotations

import numpy as np

from antijamming.dsp.doa.music import (
    bartlett_spectrum_from_covariance,
    covariance_eigendecomposition_from_matrix,
    music_spectrum_from_eigenvectors,
    source_count_diagnostics_from_eigenvalues,
    spatial_covariance,
)
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
) -> np.ndarray:
    data = np.asarray(buffer, dtype=np.complex128)
    if data.ndim != 2 or data.shape[0] == 0:
        return np.zeros((0,), dtype=np.float64)
    if data.shape[1] == 0:
        return np.zeros((data.shape[0],), dtype=np.float64)

    sample_rate_hz = float(sample_rate_hz)
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        # Without a valid sample rate we cannot demodulate a tone bin; fall back
        # to the standard chunk-wide phase estimator.
        return phase_offsets_deg(data)

    n = np.arange(data.shape[1], dtype=np.float64)
    mixer = np.exp(
        -1j * 2.0 * np.pi * float(tone_offset_hz) * n / sample_rate_hz
    ).astype(np.complex128)
    tone = np.mean(data * mixer[None, :], axis=1)
    aggregate = np.mean(tone)
    if np.abs(aggregate) <= 1e-24 or not np.isfinite(np.abs(aggregate)):
        return np.zeros((data.shape[0],), dtype=np.float64)

    offsets: list[float] = []
    for ch in range(data.shape[0]):
        cross = tone[ch] * np.conj(aggregate)
        offsets.append(float(np.degrees(np.angle(cross))))
    return np.asarray(offsets, dtype=np.float64)


def _estimate_array_tone_offset_hz(
    buffer: np.ndarray,
    sample_rate_hz: float,
    expected_tone_offset_hz: float,
    search_half_span_hz: float = 10000.0,
) -> float:
    data = np.asarray(buffer, dtype=np.complex128)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
        return float(expected_tone_offset_hz)

    sample_rate_hz = float(sample_rate_hz)
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        return float(expected_tone_offset_hz)

    # Oversized FFT improves visual stability when the tone is not exactly on a
    # bin. This is UI/monitoring work, not a sample-perfect carrier tracker.
    n = max(data.shape[1], 8)
    n_fft = int(max(8192, 1 << int(np.ceil(np.log2(n * 16)))))
    window = np.hanning(data.shape[1]).astype(np.float64)
    if not np.any(window):
        window = np.ones((data.shape[1],), dtype=np.float64)
    spectra = np.fft.fftshift(
        np.fft.fft(data * window[None, :], n=n_fft, axis=1),
        axes=1,
    )
    spectrum = np.mean(np.abs(spectra), axis=0)
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
            return float(
                masked_freqs[peak_idx] + bin_delta * (masked_freqs[1] - masked_freqs[0])
            )
    return float(masked_freqs[peak_idx])


# =============================================================================
# Phase Metrics
# =============================================================================

# Phase metrics carry both raw and calibrated views because the GUI shows the
# before/after effect of the loaded static phase calibration.


def compute_phase_metrics(
    buffer: np.ndarray,
    preview_cols: int | None = None,
    phase_correction_vector: np.ndarray | None = None,
    sample_rate_hz: float | None = None,
    phase_monitor_tone_offset_hz: float = 0.0,
    phase_monitor_use_tone_bin: bool = False,
) -> dict:
    """Compute phase, power, and optional preview data for one sample buffer."""
    raw_buffer = np.asarray(buffer, dtype=np.complex128)
    if raw_buffer.ndim != 2 or raw_buffer.shape[0] == 0 or raw_buffer.shape[1] == 0:
        raise ValueError("phase metrics require a non-empty [channels, samples] buffer")
    if not np.all(np.isfinite(raw_buffer)):
        raise ValueError("phase metrics buffer contains NaN or Inf")
    corrected_buffer = apply_phase_calibration(
        raw_buffer,
        correction_vector=phase_correction_vector,
    )
    powers = np.mean(np.abs(raw_buffer) ** 2, axis=1)
    tone_monitor_estimated_offset_hz = float(phase_monitor_tone_offset_hz)
    if bool(phase_monitor_use_tone_bin):
        # Estimate the actual array tone offset first, then use that
        # same offset for every channel so relative phases stay comparable.
        tone_monitor_estimated_offset_hz = _estimate_array_tone_offset_hz(
            raw_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            expected_tone_offset_hz=float(phase_monitor_tone_offset_hz),
        )
        raw_offsets = _tone_bin_phase_offsets_deg(
            raw_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            tone_offset_hz=tone_monitor_estimated_offset_hz,
        )
        corrected_offsets = _tone_bin_phase_offsets_deg(
            corrected_buffer,
            sample_rate_hz=float(sample_rate_hz or 0.0),
            tone_offset_hz=tone_monitor_estimated_offset_hz,
        )
    else:
        raw_offsets = phase_offsets_deg(raw_buffer)
        corrected_offsets = phase_offsets_deg(corrected_buffer)

    metrics = {
        "raw_buffer": raw_buffer,
        "calibrated_buffer": corrected_buffer,
        "powers": powers,
        "phase_offsets_deg": raw_offsets,
        "phase_offsets_raw_deg": raw_offsets,
        "phase_offsets_calibrated_deg": corrected_offsets,
        "phase_estimator": "tone_bin_demod"
        if phase_monitor_use_tone_bin
        else "chunk_crosscorr",
        "phase_monitor_estimated_offset_hz": tone_monitor_estimated_offset_hz,
    }
    if preview_cols is not None and preview_cols > 0:
        cols = min(int(preview_cols), raw_buffer.shape[1])
        metrics["complex_samples_raw"] = np.asarray(
            raw_buffer[:, :cols], dtype=np.complex64
        )
        metrics["complex_samples_calibrated"] = np.asarray(
            corrected_buffer[:, :cols], dtype=np.complex64
        )
    return metrics


# =============================================================================
# Direction Finding Metrics
# =============================================================================


def _angle_distance_deg(a: float, b: float) -> float:
    """Return the shortest circular distance between two azimuth angles."""

    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _dominant_spectrum_peaks(
    scan_angles_deg: np.ndarray,
    spectrum: np.ndarray,
    *,
    max_reported_peaks: int = 4,
    min_peak_height: float = 0.25,
    min_separation_deg: float = 8.0,
) -> list[dict[str, float]]:
    """Return separated local maxima from a normalized circular DoA spectrum."""

    scan = np.asarray(scan_angles_deg, dtype=np.float64).reshape(-1)
    spec = np.asarray(spectrum, dtype=np.float64).reshape(-1)
    if scan.size != spec.size or scan.size < 3:
        return []

    finite = np.isfinite(spec)
    if not np.any(finite):
        return []
    spec = np.where(finite, spec, -np.inf)

    prev_spec = np.roll(spec, 1)
    next_spec = np.roll(spec, -1)
    local_maxima = np.where((spec >= prev_spec) & (spec > next_spec))[0]
    local_maxima = local_maxima[spec[local_maxima] >= float(min_peak_height)]
    if local_maxima.size == 0:
        local_maxima = np.asarray([int(np.nanargmax(spec))], dtype=np.int64)

    ordered = local_maxima[np.argsort(spec[local_maxima])[::-1]]
    selected: list[dict[str, float]] = []
    for idx in ordered:
        angle = float(scan[int(idx)] % 360.0)
        if any(
            _angle_distance_deg(angle, peak["angle_deg"]) < float(min_separation_deg)
            for peak in selected
        ):
            continue
        height = float(spec[int(idx)])
        selected.append(
            {
                "angle_deg": angle,
                "height": height,
                "rel_db": float(10.0 * np.log10(max(height, 1e-12))),
            }
        )
        if len(selected) >= int(max_reported_peaks):
            break
    return selected


def compute_doa_metrics(
    corrected_buffer: np.ndarray,
    center_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    n_sources: int = 1,
) -> dict:
    """Compute MUSIC DoA metrics plus Bartlett diagnostic spectra."""
    corrected = np.asarray(corrected_buffer, dtype=np.complex128)
    if corrected.ndim != 2 or corrected.shape[0] != 4 or corrected.shape[1] == 0:
        raise ValueError("DoA metrics require a non-empty 4-channel sample buffer")
    if not np.all(np.isfinite(corrected)):
        raise ValueError("DoA metrics buffer contains NaN or Inf")
    scan = np.asarray(scan_angles_deg, dtype=np.float64).reshape(-1)
    if scan.size == 0:
        raise ValueError("DoA metrics require at least one scan angle")
    if not np.all(np.isfinite(scan)):
        raise ValueError("DoA scan angles contain NaN or Inf")
    n_channels = int(corrected.shape[0])
    source_count = min(max(int(n_sources), 1), max(n_channels - 1, 1))
    covariance = spatial_covariance(corrected)
    eigenvalues, eigenvectors = covariance_eigendecomposition_from_matrix(covariance)
    source_diagnostics = source_count_diagnostics_from_eigenvalues(
        eigenvalues,
        noise_tail_sources=source_count,
    )
    doa_raw_spectrum = music_spectrum_from_eigenvectors(
        eigenvectors=eigenvectors,
        rf_freq_hz=center_freq_hz,
        scan_angles_deg=scan,
        array_spacing_m=array_spacing_m,
        n_sources=source_count,
        normalize=False,
    )
    normalized_for_peaks = doa_raw_spectrum / (np.max(doa_raw_spectrum) + 1e-12)
    doa_deg = float(scan[int(np.argmax(doa_raw_spectrum))])
    doa_peaks = _dominant_spectrum_peaks(scan, normalized_for_peaks)
    bartlett_raw_spectrum = bartlett_spectrum_from_covariance(
        covariance=covariance,
        rf_freq_hz=center_freq_hz,
        scan_angles_deg=scan,
        array_spacing_m=array_spacing_m,
        normalize=False,
    )
    bartlett_normalized_for_peaks = bartlett_raw_spectrum / (
        np.max(bartlett_raw_spectrum) + 1e-12
    )
    bartlett_deg = float(scan[int(np.argmax(bartlett_raw_spectrum))])
    bartlett_peaks = _dominant_spectrum_peaks(
        scan,
        bartlett_normalized_for_peaks,
    )

    return {
        "n_sources": source_count,
        "doa_raw_spectrum": np.asarray(doa_raw_spectrum, dtype=np.float64),
        "doa_deg": doa_deg,
        "doa_peaks": doa_peaks,
        "doa_peak_count": len(doa_peaks),
        "bartlett_raw_spectrum": np.asarray(bartlett_raw_spectrum, dtype=np.float64),
        "bartlett_deg": bartlett_deg,
        "bartlett_peaks": bartlett_peaks,
        "bartlett_peak_count": len(bartlett_peaks),
        "covariance_matrix": covariance,
        "covariance_eigenvectors": eigenvectors,
        **source_diagnostics,
    }


# =============================================================================
# Combined Realtime Metrics
# =============================================================================

# This convenience function is used by tests and simple callers. The threaded
# backend uses the individual stage helpers so expensive work can run in queues.


def compute_realtime_metrics(
    buffer: np.ndarray,
    center_freq_hz: float,
    scan_angles_deg: np.ndarray,
    array_spacing_m: float,
    n_sources: int = 1,
    phase_correction_vector: np.ndarray | None = None,
) -> dict:
    """Compute the combined phase and DoA metrics for a runtime chunk."""
    phase_metrics = compute_phase_metrics(
        buffer=buffer,
        phase_correction_vector=phase_correction_vector,
    )
    doa_metrics = compute_doa_metrics(
        corrected_buffer=phase_metrics["calibrated_buffer"],
        center_freq_hz=center_freq_hz,
        scan_angles_deg=scan_angles_deg,
        array_spacing_m=array_spacing_m,
        n_sources=n_sources,
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
        "doa_raw_spectrum": doa_metrics["doa_raw_spectrum"],
        "n_sources": doa_metrics["n_sources"],
        "doa_deg": doa_metrics["doa_deg"],
        "doa_peaks": doa_metrics["doa_peaks"],
        "doa_peak_count": doa_metrics["doa_peak_count"],
        "bartlett_raw_spectrum": doa_metrics["bartlett_raw_spectrum"],
        "bartlett_deg": doa_metrics["bartlett_deg"],
        "bartlett_peaks": doa_metrics["bartlett_peaks"],
        "bartlett_peak_count": doa_metrics["bartlett_peak_count"],
        "covariance_eigenvalues": doa_metrics["covariance_eigenvalues"],
        "covariance_eigenvalues_db": doa_metrics["covariance_eigenvalues_db"],
        "covariance_eigenvalues_rel_db": doa_metrics["covariance_eigenvalues_rel_db"],
        "covariance_eigen_gap_db": doa_metrics["covariance_eigen_gap_db"],
        "noise_tail_assumed_sources": doa_metrics["noise_tail_assumed_sources"],
        "noise_tail_count": doa_metrics["noise_tail_count"],
        "noise_tail_eigenvalues_rel_db": doa_metrics["noise_tail_eigenvalues_rel_db"],
        "noise_tail_spread_db": doa_metrics["noise_tail_spread_db"],
        "noise_tail_flatness_db": doa_metrics["noise_tail_flatness_db"],
        "noise_tail_testable": doa_metrics["noise_tail_testable"],
        "noise_tail_white_like": doa_metrics["noise_tail_white_like"],
        "noise_tail_spread_threshold_db": doa_metrics["noise_tail_spread_threshold_db"],
        "noise_tail_flatness_threshold_db": doa_metrics[
            "noise_tail_flatness_threshold_db"
        ],
        "source_estimate_gap": doa_metrics["source_estimate_gap"],
        "source_effective_rank": doa_metrics["source_effective_rank"],
    }
