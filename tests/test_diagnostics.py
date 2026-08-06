from __future__ import annotations

import numpy as np
import pytest

from antijamming.dsp.beamforming import (
    legacy_constraint_null_ideal_weights,
    lcmv_model_response,
    uniform_preserving_vector_null_weights,
)
from antijamming.dsp.diagnostics import (
    component_power_after_beamformer,
    covariance_output_power,
    channel_power_metrics,
    output_reduction_metrics,
    spatial_vector_coherence_metrics,
)


def test_channel_power_metrics_reports_per_channel_values() -> None:
    x = np.array(
        [
            [1 + 0j, 1 + 0j],
            [2 + 0j, 2 + 0j],
            [0 + 3j, 0 + 3j],
            [0.5 + 0.25j, -0.5 - 0.25j],
        ],
        dtype=np.complex64,
    )

    metrics = channel_power_metrics(x, prefix="raw", component_threshold=0.9)

    assert metrics["raw_channel_count"] == 4
    assert metrics["raw_ch0_power_linear"] == pytest.approx(1.0)
    assert metrics["raw_ch1_power_linear"] == pytest.approx(4.0)
    assert metrics["raw_ch2_power_linear"] == pytest.approx(9.0)
    assert metrics["raw_ch0_rms_complex"] == pytest.approx(1.0)
    assert metrics["raw_ch2_peak_component"] == pytest.approx(3.0)
    assert metrics["raw_strongest_channel"] == 2
    assert metrics["raw_weakest_channel"] == 3
    assert metrics["raw_power_spread_db"] == pytest.approx(
        10.0 * np.log10(9.0 / 0.3125)
    )


def test_channel_power_metrics_accepts_non_four_channel_count() -> None:
    x = np.ones((2, 8), dtype=np.complex64)

    metrics = channel_power_metrics(x, prefix="cal")

    assert metrics["cal_channel_count"] == 2
    assert metrics["cal_ch0_power_linear"] == pytest.approx(1.0)
    assert metrics["cal_ch1_power_linear"] == pytest.approx(1.0)


def test_output_reduction_metrics_use_explicit_power_ratios() -> None:
    metrics = output_reduction_metrics(
        raw_channel_powers_linear=[4.0, 4.0, 4.0, 4.0],
        uniform_output_power_linear=2.0,
        lcmv_output_power_linear=0.5,
    )

    assert metrics["measured_output_reduction_vs_uniform_db"] == pytest.approx(
        10.0 * np.log10(2.0 / 0.5)
    )
    assert metrics["measured_output_reduction_vs_raw_avg_channel_db"] == pytest.approx(
        10.0 * np.log10(4.0 / 0.5)
    )
    assert metrics["measured_output_reduction_vs_raw_sum_channels_db"] == pytest.approx(
        10.0 * np.log10(16.0 / 0.5)
    )
    assert metrics["lcmv_vs_uniform_power_ratio_linear"] == pytest.approx(0.25)
    assert metrics["suppression_db_alias_of"] == "measured_output_reduction_vs_uniform_db"


def test_lcmv_model_response_arrays_are_absolute_not_normalized() -> None:
    scan = np.linspace(0.0, 359.0, 721)
    result = legacy_constraint_null_ideal_weights(
        n_channels=4,
        null_angle_deg=72.0,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )

    model = lcmv_model_response(
        weights=result.weights,
        scan_angles_deg=scan,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
        selected_null_angle_deg=result.null_angle_deg,
    )

    assert model.response_abs.shape == scan.shape
    assert model.response_power.shape == scan.shape
    assert model.response_db.shape == scan.shape
    assert model.response_power_db.shape == scan.shape
    assert np.all(np.isfinite(model.response_abs))
    assert np.all(np.isfinite(model.response_db))
    assert model.model_response_at_selected_null_db is not None
    assert model.model_response_at_selected_null_db < -40.0
    assert float(np.max(model.response_db)) != pytest.approx(0.0)


def test_spatial_vector_coherence_aligns_arbitrary_eigenvector_phase() -> None:
    ideal = np.array([1.0, 1.0j, -1.0, -1.0j], dtype=np.complex128)
    measured = ideal * np.exp(1j * np.deg2rad(37.0))

    metrics = spatial_vector_coherence_metrics(ideal, measured)

    assert metrics["ideal_measured_vector_available"] is True
    assert metrics["ideal_measured_coherence_abs"] == pytest.approx(1.0)
    assert metrics["ideal_measured_mismatch_power"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["ideal_measured_principal_angle_deg"] == pytest.approx(0.0)
    assert max(abs(v or 0.0) for v in metrics["u1_aligned_over_ideal_phase_diff_deg"]) < 1e-9


def test_component_power_after_beamformer_reports_predicted_suppression() -> None:
    vector = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    result = uniform_preserving_vector_null_weights(null_vector=vector)

    metrics = component_power_after_beamformer(
        component_power_before=10.0,
        weights=result.weights,
        vector=vector,
    )

    assert metrics["response_abs"] == pytest.approx(0.0, abs=1e-10)
    assert metrics["power_after_linear"] == pytest.approx(0.0, abs=1e-20)
    assert metrics["suppression_db"] is not None
    assert metrics["suppression_db"] > 100.0


def test_covariance_output_power_matches_quadratic_form() -> None:
    covariance = np.diag([4.0, 1.0, 1.0, 1.0]).astype(np.complex128)
    weights = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.complex128)

    power = covariance_output_power(covariance=covariance, weights=weights)

    assert power == pytest.approx(1.25)
