from __future__ import annotations

import numpy as np
import pytest

from antijamming.dsp.jammer_detection import (
    cold_start_jammer_evidence,
    selected_interference_subspace,
)


_THRESHOLDS = {
    "min_dominant_fraction": 0.90,
    "min_eigen_gap_db": 10.0,
    "min_strongest_bin_fraction": 0.60,
    "min_peak_over_median_db": 20.0,
}


def _array_vector() -> np.ndarray:
    vector = np.asarray(
        [1.0 + 0.0j, 0.7 + 0.5j, -0.2 + 0.8j, -0.6 - 0.3j],
        dtype=np.complex128,
    )
    return vector / np.linalg.norm(vector)


def test_persistent_coherent_tone_is_cold_start_jammer_evidence() -> None:
    rng = np.random.default_rng(20260823)
    sample_count = 32768
    index = np.arange(sample_count, dtype=np.float64)
    tone = np.exp(2j * np.pi * 0.173 * index)
    noise = 0.03 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    chunk = 3.0 * _array_vector()[:, None] * tone[None, :] + noise

    evidence = cold_start_jammer_evidence(chunk, **_THRESHOLDS)

    assert evidence.detected is True
    assert evidence.dominant_fraction > 0.99
    assert evidence.dominant_over_second_db > 20.0
    assert evidence.strongest_bin_fraction > 0.90
    assert evidence.peak_over_median_db > 40.0
    assert evidence.peak_normalized_frequency == pytest.approx(
        0.173,
        abs=1.0 / 4096,
    )
    assert evidence.excess_occupied_bandwidth_fraction_90 < 0.01


def test_wideband_coherent_bladerf_like_signal_is_not_cold_start_jammer() -> None:
    rng = np.random.default_rng(20260824)
    sample_count = 32768
    wideband = (
        rng.standard_normal(sample_count) + 1j * rng.standard_normal(sample_count)
    )
    noise = 0.03 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    chunk = 3.0 * _array_vector()[:, None] * wideband[None, :] + noise

    evidence = cold_start_jammer_evidence(chunk, **_THRESHOLDS)

    assert evidence.dominant_fraction > 0.99
    assert evidence.dominant_over_second_db > 20.0
    assert evidence.strongest_bin_fraction < 0.10
    assert evidence.detected is False
    assert evidence.wideband_high_power_candidate is False


def test_high_power_wideband_source_is_only_an_unconfirmed_candidate() -> None:
    rng = np.random.default_rng(2026082401)
    sample_count = 32768
    wideband = (
        rng.standard_normal(sample_count) + 1j * rng.standard_normal(sample_count)
    )
    noise = 0.03 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    chunk = 3.0 * _array_vector()[:, None] * wideband[None, :] + noise

    evidence = cold_start_jammer_evidence(
        chunk,
        **_THRESHOLDS,
        reference_power_linear=0.01,
        min_wideband_excess_power_db=20.0,
    )

    assert evidence.detected is False
    assert evidence.wideband_high_power_candidate is True
    assert evidence.input_power_over_reference_db is not None
    assert evidence.input_power_over_reference_db > 20.0


def test_low_power_wideband_source_is_not_a_candidate() -> None:
    rng = np.random.default_rng(2026082402)
    sample_count = 32768
    wideband = (
        rng.standard_normal(sample_count) + 1j * rng.standard_normal(sample_count)
    )
    chunk = 0.05 * _array_vector()[:, None] * wideband[None, :]

    evidence = cold_start_jammer_evidence(
        chunk,
        **_THRESHOLDS,
        reference_power_linear=0.01,
        min_wideband_excess_power_db=20.0,
    )

    assert evidence.detected is False
    assert evidence.wideband_high_power_candidate is False


def test_spatially_white_receiver_noise_is_not_cold_start_jammer() -> None:
    rng = np.random.default_rng(20260825)
    chunk = (
        rng.standard_normal((4, 32768))
        + 1j * rng.standard_normal((4, 32768))
    )

    evidence = cold_start_jammer_evidence(chunk, **_THRESHOLDS)

    assert evidence.dominant_fraction < 0.30
    assert evidence.detected is False


def test_two_supported_interference_modes_select_rank_two_and_retain_two_dof() -> None:
    rng = np.random.default_rng(20260826)
    sample_count = 32768
    index = np.arange(sample_count, dtype=np.float64)
    manifold = rng.standard_normal((4, 4)) + 1j * rng.standard_normal((4, 4))
    basis = np.asarray(np.linalg.qr(manifold)[0], dtype=np.complex128)
    dominant = 5.0 * np.exp(2j * np.pi * 0.173 * index)
    secondary = 1.0 * np.exp(2j * np.pi * -0.117 * index)
    noise = 0.03 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    chunk = (
        basis[:, 0, None] * dominant[None, :]
        + basis[:, 1, None] * secondary[None, :]
        + noise
    )

    evidence = cold_start_jammer_evidence(chunk, **_THRESHOLDS)
    selected, secondary_gaps_db = selected_interference_subspace(
        evidence,
        max_rank=2,
        min_secondary_eigen_gap_db=6.0,
    )

    assert evidence.detected is True
    assert selected.shape == (4, 2)
    assert secondary_gaps_db.shape == (1,)
    assert secondary_gaps_db[0] > 6.0
    assert evidence.secondary_strongest_bin_fraction > 0.90
    assert evidence.secondary_peak_over_median_db > 40.0
    np.testing.assert_allclose(selected.conj().T @ selected, np.eye(2), atol=1e-10)


def test_wideband_secondary_mode_is_not_mislabeled_as_interference() -> None:
    rng = np.random.default_rng(20260827)
    sample_count = 32768
    index = np.arange(sample_count, dtype=np.float64)
    manifold = rng.standard_normal((4, 4)) + 1j * rng.standard_normal((4, 4))
    basis = np.asarray(np.linalg.qr(manifold)[0], dtype=np.complex128)
    dominant = 5.0 * np.exp(2j * np.pi * 0.173 * index)
    desired_like_wideband = rng.standard_normal(sample_count) + 1j * rng.standard_normal(
        sample_count
    )
    noise = 0.03 * (
        rng.standard_normal((4, sample_count))
        + 1j * rng.standard_normal((4, sample_count))
    )
    chunk = (
        basis[:, 0, None] * dominant[None, :]
        + basis[:, 1, None] * desired_like_wideband[None, :]
        + noise
    )

    evidence = cold_start_jammer_evidence(chunk, **_THRESHOLDS)
    selected, secondary_gaps_db = selected_interference_subspace(
        evidence,
        max_rank=2,
        min_secondary_eigen_gap_db=6.0,
    )

    assert evidence.detected is True
    assert secondary_gaps_db[0] > 6.0
    assert evidence.secondary_strongest_bin_fraction < 0.10
    assert selected.shape == (4, 1)
