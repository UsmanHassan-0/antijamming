from __future__ import annotations

import numpy as np
import pytest

from antijamming.gnss.cold_start_acquisition import (
    SpatialPcpsResult,
    evaluate_spatial_pcps_consistency,
    noncoherent_spatial_pcps,
    sampled_gps_l1_ca_code,
)
from antijamming.gnss.shared_u1_phase_compensation import phase_invariant_coherence


def _steering(angle_deg: float, sensor_count: int = 4) -> np.ndarray:
    sensors = np.arange(sensor_count, dtype=np.float64)
    return np.exp(1j * np.pi * sensors * np.sin(np.deg2rad(angle_deg)))


def _nullspace_rows(null_vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(null_vectors, dtype=np.complex128)
    left, singular_values, _ = np.linalg.svd(vectors, full_matrices=True)
    tolerance = (
        max(vectors.shape)
        * np.finfo(np.float64).eps
        * float(singular_values[0])
    )
    rank = int(np.count_nonzero(singular_values > tolerance))
    return np.asarray(
        np.sqrt(vectors.shape[0]) * left[:, rank:].T,
        dtype=np.complex128,
    )


def test_rank2_jammer_first_spatial_acquisition_recovers_known_prns() -> None:
    sample_rate_hz = 4_000_000.0
    samples_per_ms = 4_000
    dwell_count = 5
    sample_count = samples_per_ms * dwell_count
    time_axis = np.arange(sample_count, dtype=np.float64) / sample_rate_hz
    rng = np.random.default_rng(20_260_823)
    raw = (
        rng.normal(size=(4, sample_count))
        + 1j * rng.normal(size=(4, sample_count))
    ) / np.sqrt(2.0)

    truths = {
        14: {"amplitude": 0.050, "doppler": -4_250.0, "delay": 731, "angle": 12.0},
        22: {"amplitude": 0.040, "doppler": 2_500.0, "delay": 2_511, "angle": 68.0},
    }
    for prn, truth in truths.items():
        one_ms = sampled_gps_l1_ca_code(
            prn,
            sample_rate_hz=sample_rate_hz,
            sample_count=samples_per_ms,
        )
        code = np.roll(np.tile(one_ms, dwell_count), int(truth["delay"]))
        signal = code * np.exp(
            2j * np.pi * float(truth["doppler"]) * time_axis
        )
        raw += (
            float(truth["amplitude"])
            * _steering(float(truth["angle"]))[:, None]
            * signal[None, :]
        )

    jammer_one = _steering(-34.0)
    jammer_two = _steering(47.0)
    raw += (
        18.0
        * jammer_one[:, None]
        * np.exp(-2j * np.pi * 372_070.3125 * time_axis)[None, :]
    )
    jammer_symbols = rng.choice((-1.0, 1.0), size=sample_count // 8 + 1)
    jammer_waveform = np.repeat(jammer_symbols, 8)[:sample_count] * np.exp(
        2j * np.pi * 615_000.0 * time_axis
    )
    raw += 8.0 * jammer_two[:, None] * jammer_waveform[None, :]
    raw = np.asarray(raw, dtype=np.complex64)

    true_jammer_subspace = np.column_stack((jammer_one, jammer_two))
    covariance = raw @ raw.conj().T / float(sample_count)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    estimated_jammer_subspace = eigenvectors[:, np.argsort(eigenvalues)[::-1][:2]]
    spatial_rows = _nullspace_rows(estimated_jammer_subspace)
    requested_prns = (1, 3, 7, 11, 14, 18, 22, 26, 31)

    raw_results = noncoherent_spatial_pcps(
        raw,
        np.eye(4, dtype=np.complex128),
        sample_rate_hz=sample_rate_hz,
        prns=requested_prns,
        dwell_count=dwell_count,
    )
    protected_results = noncoherent_spatial_pcps(
        raw,
        spatial_rows,
        sample_rate_hz=sample_rate_hz,
        prns=requested_prns,
        dwell_count=dwell_count,
    )
    protected_by_prn = {result.prn: result for result in protected_results}

    # The unprotected search's best PRN 14 cell is displaced by the jammer;
    # after spatial combination both simulated satellites land on exact truth.
    raw_prn14 = next(result for result in raw_results if result.prn == 14)
    assert (raw_prn14.doppler_hz, raw_prn14.code_phase_samples) != (-4_250.0, 731)
    assert [result.prn for result in protected_results[:2]] == [14, 22]
    for prn, truth in truths.items():
        result = protected_by_prn[prn]
        assert result.doppler_hz == float(truth["doppler"])
        assert result.code_phase_samples == int(truth["delay"])
        assert result.peak_to_median_db > 7.5
        true_projector = np.eye(4) - (
            estimated_jammer_subspace @ estimated_jammer_subspace.conj().T
        )
        projected_truth = true_projector @ _steering(float(truth["angle"]))
        assert phase_invariant_coherence(
            result.projected_spatial_vector, projected_truth
        ) > 0.95
        assert result.spatial_projector_dominant_fraction > 0.75

    residual = np.linalg.norm(
        (np.eye(4) - estimated_jammer_subspace @ estimated_jammer_subspace.conj().T)
        @ true_jammer_subspace
    ) / np.linalg.norm(true_jammer_subspace)
    assert residual < 0.002


def _consistency_result(
    *,
    prn: int,
    doppler_hz: float,
    code_phase_samples: int,
    vector: np.ndarray,
) -> SpatialPcpsResult:
    return SpatialPcpsResult(
        prn=prn,
        doppler_hz=doppler_hz,
        code_phase_samples=code_phase_samples,
        peak_to_median_db=9.0,
        peak_to_mean_db=8.8,
        projected_spatial_vector=np.asarray(vector, dtype=np.complex128),
        spatial_projector_dominant_fraction=0.98,
    )


def test_consistency_gate_accepts_physical_code_carrier_trajectory() -> None:
    sample_rate_hz = 4_000_000.0
    doppler_hz = -4_250.0
    starts = (0, 1_966_080, 3_932_160, 5_898_240)
    vector = _steering(22.0)
    expected_slope = -doppler_hz / 1_575_420_000.0 * sample_rate_hz
    observations = []
    for start in starts:
        elapsed = start / sample_rate_hz
        absolute_phase = int(round(731.0 + expected_slope * elapsed)) % 4_000
        local_phase = (absolute_phase - start) % 4_000
        observations.append(
            (
                start,
                _consistency_result(
                    prn=14,
                    doppler_hz=doppler_hz,
                    code_phase_samples=local_phase,
                    vector=vector * np.exp(1j * 0.37 * len(observations)),
                ),
            )
        )

    decision = evaluate_spatial_pcps_consistency(
        tuple(observations), sample_rate_hz=sample_rate_hz
    )

    assert decision.accepted is True
    assert decision.reasons == ()
    assert decision.code_phase_slope_error_samples_per_s < 1.0
    assert decision.code_phase_fit_max_residual_samples < 1.0
    assert decision.min_projected_vector_coherence == pytest.approx(1.0)


def test_consistency_gate_rejects_repeatable_nonphysical_jammer_correlation() -> None:
    starts = (0, 1_966_080, 3_932_160, 5_898_240)
    local_phases = (2_717, 2_759, 2_801, 2_843)
    vector = _steering(-34.0)
    observations = tuple(
        (
            start,
            _consistency_result(
                prn=4,
                doppler_hz=-8_250.0,
                code_phase_samples=phase,
                vector=vector,
            ),
        )
        for start, phase in zip(starts, local_phases, strict=True)
    )

    decision = evaluate_spatial_pcps_consistency(
        observations, sample_rate_hz=4_000_000.0
    )

    assert decision.accepted is False
    assert decision.code_phase_slope_error_samples_per_s > 3_000.0
    assert any("code/carrier slope error" in reason for reason in decision.reasons)


def test_spatial_pcps_honors_cooperative_cancellation() -> None:
    raw = np.zeros((4, 4_000), dtype=np.complex64)
    rows = np.eye(4, dtype=np.complex128)

    with pytest.raises(InterruptedError, match="cancelled"):
        noncoherent_spatial_pcps(
            raw,
            rows,
            sample_rate_hz=4_000_000.0,
            prns=(1,),
            dwell_count=1,
            cancel_requested=lambda: True,
        )
