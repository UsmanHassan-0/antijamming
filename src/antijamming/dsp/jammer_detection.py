"""Cold-start jammer evidence that does not require a clean RF baseline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from antijamming.dsp.doa.music import (
    covariance_eigendecomposition_from_matrix,
    spatial_covariance,
)


@dataclass(frozen=True)
class ColdStartJammerEvidence:
    """Rank and spectral evidence for one persistent in-band interferer."""

    detected: bool
    wideband_high_power_candidate: bool
    input_power_linear: float
    reference_power_linear: float | None
    input_power_over_reference_db: float | None
    dominant_fraction: float
    dominant_over_second_db: float
    strongest_bin_fraction: float
    peak_over_median_db: float
    peak_normalized_frequency: float
    excess_occupied_bandwidth_fraction_90: float
    secondary_strongest_bin_fraction: float
    secondary_peak_over_median_db: float
    secondary_peak_normalized_frequency: float
    secondary_excess_occupied_bandwidth_fraction_90: float
    covariance: np.ndarray
    dominant_vector: np.ndarray
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray


def cold_start_jammer_evidence(
    corrected_chunk: np.ndarray,
    *,
    min_dominant_fraction: float,
    min_eigen_gap_db: float,
    min_strongest_bin_fraction: float,
    min_peak_over_median_db: float,
    strongest_bin_fraction: float = 0.01,
    max_fft_size: int = 4096,
    reference_power_linear: float | None = None,
    min_wideband_excess_power_db: float = 20.0,
) -> ColdStartJammerEvidence:
    """Detect a narrowband rank-one source without a jammer-off reference.

    A coherent desired waveform can also be rank one across the antenna array.
    ``detected`` therefore remains the strict narrowband decision.  A coherent
    broadband source can only become a *candidate* when its calibrated input
    power is far above a supplied receiver-noise reference.  The caller must
    still veto that candidate with physically consistent GNSS acquisition
    before treating it as a jammer.
    """

    x = np.asarray(corrected_chunk, dtype=np.complex128)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 64:
        return _empty_evidence(x.shape[0] if x.ndim == 2 else 0)
    if not np.all(np.isfinite(x)):
        return _empty_evidence(x.shape[0])

    input_power_linear = float(np.mean(np.abs(x) ** 2))
    reference = (
        float(reference_power_linear)
        if reference_power_linear is not None
        else None
    )
    if reference is not None and np.isfinite(reference) and reference > 0.0:
        input_power_over_reference_db = float(
            10.0 * np.log10(max(input_power_linear, 1e-30) / reference)
        )
    else:
        reference = None
        input_power_over_reference_db = None

    covariance = spatial_covariance(x)
    eigenvalues, eigenvectors = covariance_eigendecomposition_from_matrix(
        covariance
    )
    if eigenvalues.size < 2 or eigenvectors.shape[1] < 1:
        return _empty_evidence(x.shape[0])

    positive = np.maximum(np.asarray(eigenvalues, dtype=np.float64), 1e-30)
    total = float(np.sum(positive))
    dominant_fraction = float(positive[0] / total) if total > 0.0 else 0.0
    dominant_over_second_db = float(10.0 * np.log10(positive[0] / positive[1]))
    dominant_vector = np.asarray(eigenvectors[:, 0], dtype=np.complex128)

    dominant_stream = dominant_vector.conj() @ x
    dominant_stream = dominant_stream - np.mean(dominant_stream)
    nfft_limit = max(64, min(int(max_fft_size), int(dominant_stream.size)))
    nfft = 1 << int(np.floor(np.log2(nfft_limit)))
    window = np.hanning(nfft)
    spectrum = np.zeros((nfft,), dtype=np.float64)
    segment_count = 0
    for start in range(0, dominant_stream.size - nfft + 1, nfft):
        transformed = np.fft.fft(dominant_stream[start : start + nfft] * window)
        spectrum += np.abs(transformed) ** 2
        segment_count += 1
    if segment_count <= 0:
        return _empty_evidence(x.shape[0])
    spectrum /= float(segment_count)
    spectrum = np.maximum(spectrum, 1e-30)

    fraction = min(max(float(strongest_bin_fraction), 1.0 / nfft), 1.0)
    top_count = max(1, int(np.ceil(fraction * nfft)))
    ordered = np.sort(spectrum)[::-1]
    strongest_fraction = float(np.sum(ordered[:top_count]) / np.sum(ordered))
    peak_over_median_db = float(
        10.0 * np.log10(float(np.max(spectrum)) / float(np.median(spectrum)))
    )
    peak_index = int(np.argmax(spectrum))
    peak_normalized_frequency = float(np.fft.fftfreq(nfft)[peak_index])
    excess = np.maximum(spectrum - float(np.median(spectrum)), 0.0)
    excess_total = float(np.sum(excess))
    if excess_total > 0.0:
        bin_index = np.arange(nfft, dtype=np.int64)
        circular_distance = np.minimum(
            np.mod(bin_index - peak_index, nfft),
            np.mod(peak_index - bin_index, nfft),
        )
        radial_order = np.argsort(circular_distance, kind="stable")
        radial_cumulative = np.cumsum(excess[radial_order])
        cutoff_position = int(
            np.searchsorted(radial_cumulative, 0.90 * excess_total, side="left")
        )
        cutoff_position = min(cutoff_position, nfft - 1)
        occupied_radius_bins = int(circular_distance[radial_order[cutoff_position]])
        excess_occupied_bandwidth_fraction_90 = float(
            min(nfft, 2 * occupied_radius_bins + 1) / nfft
        )
    else:
        excess_occupied_bandwidth_fraction_90 = 0.0
    secondary_strongest_fraction = 0.0
    secondary_peak_over_median_db = 0.0
    secondary_peak_normalized_frequency = 0.0
    secondary_excess_occupied_bandwidth_fraction_90 = 0.0
    if eigenvectors.shape[1] >= 2:
        (
            secondary_strongest_fraction,
            secondary_peak_over_median_db,
            secondary_peak_normalized_frequency,
            secondary_excess_occupied_bandwidth_fraction_90,
        ) = _mode_spectral_metrics(
            np.asarray(eigenvectors[:, 1], dtype=np.complex128).conj() @ x,
            strongest_bin_fraction=strongest_bin_fraction,
            max_fft_size=max_fft_size,
        )
    detected = bool(
        dominant_fraction >= float(min_dominant_fraction)
        and dominant_over_second_db >= float(min_eigen_gap_db)
        and strongest_fraction >= float(min_strongest_bin_fraction)
        and peak_over_median_db >= float(min_peak_over_median_db)
    )
    rank_supported = bool(
        dominant_fraction >= float(min_dominant_fraction)
        and dominant_over_second_db >= float(min_eigen_gap_db)
    )
    wideband_high_power_candidate = bool(
        rank_supported
        and not detected
        and input_power_over_reference_db is not None
        and input_power_over_reference_db
        >= float(min_wideband_excess_power_db)
    )
    return ColdStartJammerEvidence(
        detected=detected,
        wideband_high_power_candidate=wideband_high_power_candidate,
        input_power_linear=input_power_linear,
        reference_power_linear=reference,
        input_power_over_reference_db=input_power_over_reference_db,
        dominant_fraction=dominant_fraction,
        dominant_over_second_db=dominant_over_second_db,
        strongest_bin_fraction=strongest_fraction,
        peak_over_median_db=peak_over_median_db,
        peak_normalized_frequency=peak_normalized_frequency,
        excess_occupied_bandwidth_fraction_90=(
            excess_occupied_bandwidth_fraction_90
        ),
        secondary_strongest_bin_fraction=secondary_strongest_fraction,
        secondary_peak_over_median_db=secondary_peak_over_median_db,
        secondary_peak_normalized_frequency=secondary_peak_normalized_frequency,
        secondary_excess_occupied_bandwidth_fraction_90=(
            secondary_excess_occupied_bandwidth_fraction_90
        ),
        covariance=np.asarray(covariance, dtype=np.complex128),
        dominant_vector=dominant_vector,
        eigenvalues=np.asarray(eigenvalues, dtype=np.float64),
        eigenvectors=np.asarray(eigenvectors, dtype=np.complex128),
    )


def selected_interference_subspace(
    evidence: ColdStartJammerEvidence,
    *,
    max_rank: int,
    min_secondary_eigen_gap_db: float,
    min_secondary_spectral_concentration: float = 0.60,
    min_secondary_peak_over_median_db: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Select supported interference eigenvectors without consuming all DoF.

    The dominant vector is always selected after cold-start detection. A
    secondary vector is selected only when it is separated from the following
    covariance eigenvalue by the configured dB gap. At least two sensor-space
    dimensions are retained for unknown GNSS directions on arrays with three
    or more elements.
    """

    eigenvalues = np.maximum(
        np.asarray(evidence.eigenvalues, dtype=np.float64).reshape(-1), 1e-30
    )
    eigenvectors = np.asarray(
        getattr(
            evidence,
            "eigenvectors",
            np.asarray(evidence.dominant_vector, dtype=np.complex128)[:, np.newaxis],
        ),
        dtype=np.complex128,
    )
    if (
        eigenvectors.ndim != 2
        or eigenvectors.shape[0] < 2
        or eigenvectors.shape[1] < 1
        or eigenvalues.size < eigenvectors.shape[1]
        or not np.all(np.isfinite(eigenvectors))
    ):
        row_count = eigenvectors.shape[0] if eigenvectors.ndim == 2 else 0
        return (
            np.zeros((row_count, 0), dtype=np.complex128),
            np.zeros((0,), dtype=np.float64),
        )
    sensor_count = int(eigenvectors.shape[0])
    retain_two_limit = max(1, sensor_count - 2)
    rank_limit = min(
        max(1, int(max_rank)),
        retain_two_limit,
        int(eigenvectors.shape[1]),
        int(eigenvalues.size - 1),
    )
    selected_rank = 1
    gaps: list[float] = []
    threshold = float(min_secondary_eigen_gap_db)
    for candidate_rank in range(2, rank_limit + 1):
        gap_db = float(
            10.0
            * np.log10(
                eigenvalues[candidate_rank - 1] / eigenvalues[candidate_rank]
            )
        )
        gaps.append(gap_db)
        secondary_is_spectrally_jammer_like = bool(
            float(
                getattr(evidence, "secondary_strongest_bin_fraction", 0.0)
            )
            >= float(min_secondary_spectral_concentration)
            and float(
                getattr(evidence, "secondary_peak_over_median_db", 0.0)
            )
            >= float(min_secondary_peak_over_median_db)
        )
        if gap_db < threshold or not secondary_is_spectrally_jammer_like:
            break
        selected_rank = candidate_rank
    selected = np.asarray(
        eigenvectors[:, :selected_rank], dtype=np.complex128
    )
    return selected, np.asarray(gaps, dtype=np.float64)


def _mode_spectral_metrics(
    stream: np.ndarray,
    *,
    strongest_bin_fraction: float,
    max_fft_size: int,
) -> tuple[float, float, float, float]:
    samples = np.asarray(stream, dtype=np.complex128).reshape(-1)
    if samples.size < 64 or not np.all(np.isfinite(samples)):
        return 0.0, 0.0, 0.0, 0.0
    samples = samples - np.mean(samples)
    nfft_limit = max(64, min(int(max_fft_size), int(samples.size)))
    nfft = 1 << int(np.floor(np.log2(nfft_limit)))
    window = np.hanning(nfft)
    spectrum = np.zeros((nfft,), dtype=np.float64)
    segment_count = 0
    for start in range(0, samples.size - nfft + 1, nfft):
        transformed = np.fft.fft(samples[start : start + nfft] * window)
        spectrum += np.abs(transformed) ** 2
        segment_count += 1
    if segment_count <= 0:
        return 0.0, 0.0, 0.0, 0.0
    spectrum = np.maximum(spectrum / float(segment_count), 1e-30)
    fraction = min(max(float(strongest_bin_fraction), 1.0 / nfft), 1.0)
    top_count = max(1, int(np.ceil(fraction * nfft)))
    ordered = np.sort(spectrum)[::-1]
    strongest_fraction = float(np.sum(ordered[:top_count]) / np.sum(ordered))
    peak_over_median_db = float(
        10.0 * np.log10(float(np.max(spectrum)) / float(np.median(spectrum)))
    )
    peak_index = int(np.argmax(spectrum))
    peak_normalized_frequency = float(np.fft.fftfreq(nfft)[peak_index])
    excess = np.maximum(spectrum - float(np.median(spectrum)), 0.0)
    excess_total = float(np.sum(excess))
    if excess_total <= 0.0:
        return (
            strongest_fraction,
            peak_over_median_db,
            peak_normalized_frequency,
            0.0,
        )
    bin_index = np.arange(nfft, dtype=np.int64)
    circular_distance = np.minimum(
        np.mod(bin_index - peak_index, nfft),
        np.mod(peak_index - bin_index, nfft),
    )
    radial_order = np.argsort(circular_distance, kind="stable")
    radial_cumulative = np.cumsum(excess[radial_order])
    cutoff_position = int(
        np.searchsorted(radial_cumulative, 0.90 * excess_total, side="left")
    )
    cutoff_position = min(cutoff_position, nfft - 1)
    occupied_radius_bins = int(circular_distance[radial_order[cutoff_position]])
    occupied_fraction = float(min(nfft, 2 * occupied_radius_bins + 1) / nfft)
    return (
        strongest_fraction,
        peak_over_median_db,
        peak_normalized_frequency,
        occupied_fraction,
    )


def _empty_evidence(channel_count: int) -> ColdStartJammerEvidence:
    count = max(0, int(channel_count))
    return ColdStartJammerEvidence(
        detected=False,
        wideband_high_power_candidate=False,
        input_power_linear=0.0,
        reference_power_linear=None,
        input_power_over_reference_db=None,
        dominant_fraction=0.0,
        dominant_over_second_db=0.0,
        strongest_bin_fraction=0.0,
        peak_over_median_db=0.0,
        peak_normalized_frequency=0.0,
        excess_occupied_bandwidth_fraction_90=0.0,
        secondary_strongest_bin_fraction=0.0,
        secondary_peak_over_median_db=0.0,
        secondary_peak_normalized_frequency=0.0,
        secondary_excess_occupied_bandwidth_fraction_90=0.0,
        covariance=np.zeros((count, count), dtype=np.complex128),
        dominant_vector=np.zeros((count,), dtype=np.complex128),
        eigenvalues=np.zeros((count,), dtype=np.float64),
        eigenvectors=np.zeros((count, count), dtype=np.complex128),
    )


__all__ = [
    "ColdStartJammerEvidence",
    "cold_start_jammer_evidence",
    "selected_interference_subspace",
]
