"""Cold-start GPS L1 acquisition across a jammer-null spatial subspace.

The stock GNSS-SDR PCPS block accepts one sample stream.  This module performs
the missing array operation before tracking: every spatial output is searched
with the same PRN/code/Doppler hypothesis, then squared correlation magnitudes
are summed across both spatial outputs and time dwells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from antijamming.gnss.shared_u1_phase_compensation import gps_l1_ca_code


GPS_CA_RATE_HZ = 1_023_000.0
GPS_L1_HZ = 1_575_420_000.0


@dataclass(frozen=True, slots=True)
class SpatialPcpsResult:
    """One PRN result from noncoherent spatial PCPS acquisition."""

    prn: int
    doppler_hz: float
    code_phase_samples: int
    peak_to_median_db: float
    peak_to_mean_db: float
    projected_spatial_vector: np.ndarray
    spatial_projector_dominant_fraction: float


@dataclass(frozen=True, slots=True)
class SpatialPcpsConsistencyResult:
    """Physical consistency decision across separated PCPS observations."""

    accepted: bool
    reasons: tuple[str, ...]
    observation_count: int
    min_peak_to_median_db: float
    doppler_span_hz: float
    measured_code_phase_slope_samples_per_s: float
    expected_code_phase_slope_samples_per_s: float
    code_phase_slope_error_samples_per_s: float
    code_phase_fit_max_residual_samples: float
    min_projected_vector_coherence: float
    min_spatial_projector_dominant_fraction: float
    latest_projected_spatial_vector: np.ndarray


def sampled_gps_l1_ca_code(
    prn: int,
    *,
    sample_rate_hz: float,
    sample_count: int,
) -> np.ndarray:
    """Return one sampled GPS L1 C/A replica at the requested rate."""

    count = int(sample_count)
    rate = float(sample_rate_hz)
    if count <= 0 or not np.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample count and sample rate must be positive")
    chips = gps_l1_ca_code(int(prn))
    indices = np.floor(
        np.arange(count, dtype=np.float64) * GPS_CA_RATE_HZ / rate
    ).astype(np.int64)
    return np.asarray(chips[indices % chips.size], dtype=np.complex64)


def noncoherent_spatial_pcps(
    raw_channels: np.ndarray,
    spatial_rows: np.ndarray,
    *,
    sample_rate_hz: float,
    prns: tuple[int, ...],
    dwell_count: int,
    doppler_max_hz: float = 10_000.0,
    doppler_step_hz: float = 250.0,
    cancel_requested: Callable[[], bool] | None = None,
) -> list[SpatialPcpsResult]:
    """Search common PRN hypotheses and combine power across spatial outputs.

    ``raw_channels`` has shape ``(sensors, samples)``.  Each row of
    ``spatial_rows`` is a beamforming weight vector and produces ``w^H x``.
    Rows normally span the measured jammer's orthogonal complement.  They do
    not need a desired-satellite steering vector.

    The returned desired vector is estimated only inside the row subspace.  A
    projector average makes it insensitive to the common navigation-bit and
    carrier phase of individual millisecond prompts.
    """

    raw = np.asarray(raw_channels, dtype=np.complex64)
    rows = np.asarray(spatial_rows, dtype=np.complex128)
    if raw.ndim != 2 or raw.shape[0] < 2 or raw.shape[1] < 1:
        raise ValueError("raw_channels must be a nonempty sensor-by-sample matrix")
    if (
        rows.ndim != 2
        or rows.shape[0] < 1
        or rows.shape[1] != raw.shape[0]
        or not np.all(np.isfinite(rows))
    ):
        raise ValueError("spatial row dimensions or values are invalid")
    if not np.all(np.isfinite(raw)):
        raise ValueError("raw channel samples contain NaN or Inf")
    if not prns:
        raise ValueError("at least one PRN must be requested")

    rate = float(sample_rate_hz)
    step = float(doppler_step_hz)
    maximum = float(doppler_max_hz)
    if (
        not np.isfinite(rate)
        or rate <= 0.0
        or not np.isfinite(step)
        or step <= 0.0
        or not np.isfinite(maximum)
        or maximum < 0.0
    ):
        raise ValueError("sample-rate and Doppler parameters are invalid")
    samples_per_ms = int(round(rate / 1000.0))
    available_dwells = int(raw.shape[1] // samples_per_ms)
    dwells = min(max(1, int(dwell_count)), available_dwells)
    if dwells < 1:
        raise ValueError("raw capture does not contain one complete millisecond")

    # Orthonormal basis for the span of the supplied weight vectors.  It is
    # used only to reconstruct the desired prompt vector in the same subspace;
    # the acquisition statistic uses the caller's actual rows and scaling.
    row_basis, row_singular_values, _ = np.linalg.svd(
        rows.T, full_matrices=False
    )
    tolerance = (
        max(rows.T.shape)
        * np.finfo(np.float64).eps
        * max(float(row_singular_values[0]), 1.0)
    )
    row_rank = int(np.count_nonzero(row_singular_values > tolerance))
    if row_rank < 1:
        raise ValueError("spatial rows have zero rank")
    allowed_basis = np.asarray(row_basis[:, :row_rank], dtype=np.complex128)
    allowed_projector = allowed_basis @ allowed_basis.conj().T

    used_samples = dwells * samples_per_ms
    raw_blocks = np.asarray(
        raw[:, :used_samples].reshape(raw.shape[0], dwells, samples_per_ms),
        dtype=np.complex64,
    )
    outputs = np.asarray(np.conj(rows) @ raw[:, :used_samples], dtype=np.complex64)
    output_blocks = outputs.reshape(rows.shape[0], dwells, samples_per_ms)
    output_blocks = output_blocks - np.mean(output_blocks, axis=2, keepdims=True)

    dopplers = np.arange(
        -maximum,
        maximum + 0.5 * step,
        step,
        dtype=np.float64,
    )
    time_axis = np.arange(samples_per_ms, dtype=np.float64) / rate
    carriers = np.exp(-2j * np.pi * dopplers[:, None] * time_axis[None, :])
    output_spectra = np.fft.fft(
        output_blocks[:, :, None, :] * carriers[None, None, :, :], axis=3
    )

    results: list[SpatialPcpsResult] = []
    for requested_prn in prns:
        if cancel_requested is not None and cancel_requested():
            raise InterruptedError("spatial PCPS cancelled")
        prn = int(requested_prn)
        code = sampled_gps_l1_ca_code(
            prn,
            sample_rate_hz=rate,
            sample_count=samples_per_ms,
        )
        conjugate_code_spectrum = np.conj(np.fft.fft(code))
        correlations = np.fft.ifft(
            output_spectra
            * conjugate_code_spectrum[None, None, None, :],
            axis=3,
        )
        power_grid = np.sum(np.abs(correlations) ** 2, axis=(0, 1))
        doppler_index, code_index = np.unravel_index(
            int(np.argmax(power_grid)), power_grid.shape
        )
        peak = float(power_grid[doppler_index, code_index])
        median = float(np.median(power_grid))
        mean = float(np.mean(power_grid))

        # Re-correlate the physical sensors only at the winning hypothesis.
        # Project each millisecond prompt into the same jammer-free subspace,
        # normalize away common amplitude, then average rank-one projectors.
        raw_wiped = (
            raw_blocks
            * carriers[doppler_index][None, None, :]
        )
        raw_correlations = np.fft.ifft(
            np.fft.fft(raw_wiped, axis=2)
            * conjugate_code_spectrum[None, None, :],
            axis=2,
        )
        prompts = np.asarray(
            raw_correlations[:, :, code_index], dtype=np.complex128
        )
        projected_prompts = allowed_projector @ prompts
        projector_sum = np.zeros(
            (raw.shape[0], raw.shape[0]), dtype=np.complex128
        )
        accepted = 0
        for dwell_index in range(dwells):
            prompt = projected_prompts[:, dwell_index]
            norm = float(np.linalg.norm(prompt))
            if norm <= np.finfo(np.float64).tiny:
                continue
            unit = prompt / norm
            projector_sum += np.outer(unit, np.conj(unit))
            accepted += 1
        if accepted == 0:
            desired = np.zeros((raw.shape[0],), dtype=np.complex128)
            dominant_fraction = 0.0
        else:
            projector_average = projector_sum / float(accepted)
            eigenvalues, eigenvectors = np.linalg.eigh(projector_average)
            order = np.argsort(np.real(eigenvalues))[::-1]
            eigenvalues = np.maximum(np.real(eigenvalues[order]), 0.0)
            desired = np.asarray(eigenvectors[:, order[0]], dtype=np.complex128)
            dominant_fraction = float(
                eigenvalues[0] / max(float(np.sum(eigenvalues)), 1e-30)
            )
            pivot = int(np.argmax(np.abs(desired)))
            desired *= np.exp(-1j * np.angle(desired[pivot]))

        results.append(
            SpatialPcpsResult(
                prn=prn,
                doppler_hz=float(dopplers[doppler_index]),
                code_phase_samples=int(code_index),
                peak_to_median_db=float(
                    10.0 * np.log10(max(peak, 1e-30) / max(median, 1e-30))
                ),
                peak_to_mean_db=float(
                    10.0 * np.log10(max(peak, 1e-30) / max(mean, 1e-30))
                ),
                projected_spatial_vector=desired,
                spatial_projector_dominant_fraction=dominant_fraction,
            )
        )

    return sorted(results, key=lambda row: row.peak_to_median_db, reverse=True)


def evaluate_spatial_pcps_consistency(
    observations: tuple[tuple[int, SpatialPcpsResult], ...],
    *,
    sample_rate_hz: float,
    min_observations: int = 4,
    min_peak_to_median_db: float = 6.5,
    max_doppler_span_hz: float = 500.0,
    max_code_phase_fit_residual_samples: float = 5.0,
    max_code_carrier_slope_error_samples_per_s: float = 15.0,
    min_projected_vector_coherence: float = 0.95,
    min_spatial_projector_dominant_fraction: float = 0.75,
) -> SpatialPcpsConsistencyResult:
    """Reject one-shot or physically impossible jammer/code correlations.

    A real GPS L1 C/A signal must preserve one Doppler trajectory, one array
    vector, and one absolute code phase across separated capture windows.  Its
    code-delay rate is tied to carrier Doppler by approximately
    ``-doppler / GPS_L1 * sample_rate``.  Strong CW interference can create a
    high and repeatable PCPS cell, but it does not generally satisfy that
    code/carrier relation.
    """

    required = max(2, int(min_observations))
    rate = float(sample_rate_hz)
    if not np.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample_rate_hz must be positive")
    ordered = tuple(sorted(observations, key=lambda item: int(item[0])))
    if not ordered:
        zero = np.zeros((0,), dtype=np.complex128)
        return SpatialPcpsConsistencyResult(
            accepted=False,
            reasons=(f"observation_count 0 below {required}",),
            observation_count=0,
            min_peak_to_median_db=float("-inf"),
            doppler_span_hz=float("inf"),
            measured_code_phase_slope_samples_per_s=float("nan"),
            expected_code_phase_slope_samples_per_s=float("nan"),
            code_phase_slope_error_samples_per_s=float("inf"),
            code_phase_fit_max_residual_samples=float("inf"),
            min_projected_vector_coherence=0.0,
            min_spatial_projector_dominant_fraction=0.0,
            latest_projected_spatial_vector=zero,
        )
    prns = {int(result.prn) for _, result in ordered}
    if len(prns) != 1:
        raise ValueError("all consistency observations must describe one PRN")
    starts = np.asarray([int(start) for start, _ in ordered], dtype=np.float64)
    results = tuple(result for _, result in ordered)
    elapsed = starts / rate
    dopplers = np.asarray([result.doppler_hz for result in results])
    samples_per_ms = int(round(rate / 1000.0))
    absolute_code_phases = np.asarray(
        [
            (int(result.code_phase_samples) + int(starts[index]))
            % samples_per_ms
            for index, result in enumerate(results)
        ],
        dtype=np.float64,
    )
    phase_radians = 2.0 * np.pi * absolute_code_phases / float(samples_per_ms)
    unwrapped = np.unwrap(phase_radians) * float(samples_per_ms) / (2.0 * np.pi)
    if len(ordered) >= 2 and float(np.ptp(elapsed)) > 0.0:
        measured_slope, intercept = np.polyfit(elapsed, unwrapped, 1)
        fit = measured_slope * elapsed + intercept
        fit_residual = float(np.max(np.abs(unwrapped - fit)))
    else:
        measured_slope = float("nan")
        fit_residual = float("inf")
    mean_doppler = float(np.mean(dopplers))
    expected_slope = float(-mean_doppler / GPS_L1_HZ * rate)
    slope_error = float(abs(measured_slope - expected_slope))
    reference = results[0].projected_spatial_vector
    coherences = [
        float(
            abs(
                np.vdot(
                    np.asarray(reference, dtype=np.complex128),
                    np.asarray(result.projected_spatial_vector, dtype=np.complex128),
                )
            )
            ** 2
            / max(
                float(np.vdot(reference, reference).real)
                * float(
                    np.vdot(
                        result.projected_spatial_vector,
                        result.projected_spatial_vector,
                    ).real
                ),
                1e-30,
            )
        )
        for result in results[1:]
    ]
    minimum_coherence = float(min(coherences) if coherences else 1.0)
    minimum_peak = float(min(result.peak_to_median_db for result in results))
    doppler_span = float(np.ptp(dopplers))
    minimum_fraction = float(
        min(result.spatial_projector_dominant_fraction for result in results)
    )
    reasons: list[str] = []
    if len(ordered) < required:
        reasons.append(f"observation_count {len(ordered)} below {required}")
    if minimum_peak < float(min_peak_to_median_db):
        reasons.append(
            f"minimum peak/median {minimum_peak:.3f} dB below "
            f"{float(min_peak_to_median_db):.3f} dB"
        )
    if doppler_span > float(max_doppler_span_hz):
        reasons.append(
            f"Doppler span {doppler_span:.3f} Hz exceeds "
            f"{float(max_doppler_span_hz):.3f} Hz"
        )
    if fit_residual > float(max_code_phase_fit_residual_samples):
        reasons.append(
            f"code-phase fit residual {fit_residual:.3f} samples exceeds "
            f"{float(max_code_phase_fit_residual_samples):.3f}"
        )
    if slope_error > float(max_code_carrier_slope_error_samples_per_s):
        reasons.append(
            f"code/carrier slope error {slope_error:.3f} samples/s exceeds "
            f"{float(max_code_carrier_slope_error_samples_per_s):.3f}"
        )
    if minimum_coherence < float(min_projected_vector_coherence):
        reasons.append(
            f"spatial-vector coherence {minimum_coherence:.6f} below "
            f"{float(min_projected_vector_coherence):.6f}"
        )
    if minimum_fraction < float(min_spatial_projector_dominant_fraction):
        reasons.append(
            f"prompt projector fraction {minimum_fraction:.6f} below "
            f"{float(min_spatial_projector_dominant_fraction):.6f}"
        )
    return SpatialPcpsConsistencyResult(
        accepted=not reasons,
        reasons=tuple(reasons),
        observation_count=len(ordered),
        min_peak_to_median_db=minimum_peak,
        doppler_span_hz=doppler_span,
        measured_code_phase_slope_samples_per_s=float(measured_slope),
        expected_code_phase_slope_samples_per_s=expected_slope,
        code_phase_slope_error_samples_per_s=slope_error,
        code_phase_fit_max_residual_samples=fit_residual,
        min_projected_vector_coherence=minimum_coherence,
        min_spatial_projector_dominant_fraction=minimum_fraction,
        latest_projected_spatial_vector=np.asarray(
            results[-1].projected_spatial_vector, dtype=np.complex128
        ),
    )


__all__ = [
    "SpatialPcpsResult",
    "SpatialPcpsConsistencyResult",
    "evaluate_spatial_pcps_consistency",
    "noncoherent_spatial_pcps",
    "sampled_gps_l1_ca_code",
]
