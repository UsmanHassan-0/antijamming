from __future__ import annotations

import numpy as np
import pytest

from antijamming.dsp.beamforming import (
    covariance_lcmv_vector_null_weights,
    lcmv_model_response,
)
from antijamming.dsp.diagnostics import (
    component_power_after_beamformer,
    covariance_output_power,
    channel_power_metrics,
    output_reduction_metrics,
    signal_power_metrics,
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
    assert "suppression_db_alias_of" not in metrics


def test_lcmv_model_response_arrays_are_absolute_not_normalized() -> None:
    scan = np.linspace(0.0, 359.0, 721)
    result = covariance_lcmv_vector_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
        null_vector=np.array([1.0, 0.7j, -0.4 + 0.2j, 0.2 - 0.9j]),
    )

    model = lcmv_model_response(
        weights=result.weights,
        scan_angles_deg=scan,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )

    assert model.response_abs.shape == scan.shape
    assert model.response_power.shape == scan.shape
    assert model.response_db.shape == scan.shape
    assert model.response_power_db.shape == scan.shape
    assert np.all(np.isfinite(model.response_abs))
    assert np.all(np.isfinite(model.response_db))
    assert not hasattr(model, "model_response_at_selected_null_db")
    assert float(np.max(model.response_db)) != pytest.approx(0.0)


def test_spatial_vector_coherence_aligns_arbitrary_eigenvector_phase() -> None:
    ideal = np.array([1.0, 1.0j, -1.0, -1.0j], dtype=np.complex128)
    measured = ideal * np.exp(1j * np.deg2rad(37.0))

    metrics = spatial_vector_coherence_metrics(ideal, measured)

    assert metrics["ideal_measured_vector_available"] is True
    assert metrics["ideal_measured_coherence_abs"] == pytest.approx(1.0)
    assert metrics["ideal_measured_mismatch_power"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["ideal_measured_principal_angle_deg"] == pytest.approx(0.0)
    assert (
        max(abs(v or 0.0) for v in metrics["u1_aligned_over_ideal_phase_diff_deg"])
        < 1e-9
    )


def test_component_power_after_beamformer_reports_predicted_suppression() -> None:
    vector = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    result = covariance_lcmv_vector_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
        null_vector=vector,
    )

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


def test_signal_power_metrics_rejects_nonfinite_iq() -> None:
    with pytest.raises(ValueError, match="samples contain NaN or Inf"):
        signal_power_metrics(
            np.asarray([1.0 + 0.0j, np.nan + 0.0j]),
            prefix="raw",
        )


@pytest.mark.parametrize(
    ("covariance", "message"),
    [
        (
            np.asarray(
                [
                    [1.0, 1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                dtype=np.complex128,
            ),
            "not Hermitian",
        ),
        (
            np.diag([-1.0, 1.0, 1.0, 1.0]).astype(np.complex128),
            "not positive semidefinite",
        ),
    ],
)
def test_covariance_lcmv_rejects_invalid_covariance(covariance, message) -> None:
    with pytest.raises(ValueError, match=message):
        covariance_lcmv_vector_null_weights(
            covariance=covariance,
            null_vector=np.asarray([1.0, -1.0, 1.0, -1.0]),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"diagonal_loading_rel": np.nan},
        {"diagonal_loading_abs": np.inf},
        {"condition_number_limit": np.nan},
        {"max_weight_norm": np.inf},
    ],
)
def test_covariance_lcmv_rejects_nonfinite_control_values(kwargs) -> None:
    with pytest.raises(ValueError, match="finite"):
        covariance_lcmv_vector_null_weights(
            covariance=np.eye(4, dtype=np.complex128),
            null_vector=np.asarray([1.0, -1.0, 1.0, -1.0]),
            **kwargs,
        )


def test_lcmv_model_response_rejects_nonfinite_epsilon() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        lcmv_model_response(
            weights=np.ones((4,), dtype=np.complex128),
            scan_angles_deg=np.asarray([0.0, 90.0]),
            rf_freq_hz=1.57542e9,
            array_spacing_m=0.07,
            response_db_epsilon=np.nan,
        )


def test_covariance_lcmv_random_psd_cases_satisfy_both_constraints() -> None:
    rng = np.random.default_rng(20260831)
    preserve = np.ones((4,), dtype=np.complex128)

    for _ in range(100):
        matrix = rng.standard_normal((4, 4)) + 1j * rng.standard_normal((4, 4))
        covariance = matrix @ matrix.conj().T + 0.05 * np.eye(4)
        null_vector = rng.standard_normal(4) + 1j * rng.standard_normal(4)
        result = covariance_lcmv_vector_null_weights(
            covariance=covariance,
            null_vector=null_vector,
            diagonal_loading_rel=1e-3,
            condition_number_limit=1e12,
            max_weight_norm=100.0,
        )

        np.testing.assert_allclose(
            np.vdot(preserve, result.weights),
            4.0 + 0.0j,
            rtol=1e-9,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            np.vdot(result.null_vector, result.weights),
            0.0 + 0.0j,
            rtol=0.0,
            atol=1e-9,
        )
        assert np.all(np.isfinite(result.weights))
