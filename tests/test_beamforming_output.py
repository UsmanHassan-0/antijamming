from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest

from antijamming.config import StreamConfig
from antijamming.dsp.beamforming import (
    apply_beamformer,
    legacy_angle_fan_diagnostic_weights,
    legacy_constraint_null_ideal_weights,
    uniform_preserving_covariance_lcmv_null_weights,
    uniform_preserving_covariance_vector_null_weights,
    uniform_preserving_vector_null_weights,
    uniform_weights,
)
from antijamming.dsp.doa.music import steering_vector
from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
    operator_bearing_to_internal_angle_deg,
)
from antijamming.dsp.phase import apply_phase_calibration
from antijamming.runtime import BackendRuntime, StreamWorker


def _build_loggers() -> dict[str, logging.Logger]:
    keys = [
        "app",
        "hw",
        "stream",
        "transport",
        "handoff",
        "phase",
        "doa",
        "lcmv",
        "analysis",
        "lcmv_pattern",
        "spatial_vector",
        "gnss",
        "health",
        "errors",
    ]
    return {k: logging.getLogger(f"test.bf.{k}") for k in keys}


def test_uniform_weights_are_raw_sum_coefficients() -> None:
    weights = uniform_weights(4)

    assert weights.shape == (4,)
    assert np.allclose(weights, np.ones((4,), dtype=np.complex128))
    assert np.isclose(np.sum(weights), 4.0 + 0.0j)


def test_uniform_combiner_outputs_single_stream() -> None:
    rng = np.random.default_rng(7)
    x = (
        rng.standard_normal((4, 256)) + 1j * rng.standard_normal((4, 256))
    ).astype(np.complex128)
    weights = uniform_weights(4)
    y = apply_beamformer(x, weights)

    assert y.shape == (256,)
    assert y.dtype == np.complex64
    assert np.allclose(y, np.asarray(weights.conj() @ x, dtype=np.complex64))


def test_uniform_combiner_rejects_wrong_weight_count() -> None:
    x = np.ones((4, 8), dtype=np.complex128)

    with pytest.raises(ValueError, match="weight count"):
        apply_beamformer(x, uniform_weights(3))


def test_lcmv_test_weights_are_finite_and_satisfy_constraints() -> None:
    null_angle = 72.0
    result = legacy_constraint_null_ideal_weights(
        n_channels=4,
        null_angle_deg=null_angle,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )
    unity = np.ones((4,), dtype=np.complex128)
    null_steering = steering_vector(
        np.asarray([null_angle], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)

    assert result.weights.shape == (4,)
    assert np.all(np.isfinite(result.weights))
    assert result.weight_norm <= 8.0
    assert np.vdot(unity, result.weights) == pytest.approx(4.0 + 0.0j, abs=1e-10)
    assert abs(np.vdot(null_steering, result.weights)) < 1e-10
    assert abs(result.unity_residual) < 1e-10
    assert abs(result.null_residual) < 1e-10


def test_display_bearing_to_internal_angle_convention_is_invertible() -> None:
    assert operator_bearing_to_internal_angle_deg(170.0) == pytest.approx(280.0)
    assert operator_bearing_to_internal_angle_deg(290.0) == pytest.approx(160.0)
    for internal in (0.0, 88.75, 160.0, 280.0, 359.5):
        display = internal_angle_to_operator_bearing_deg(internal)
        assert operator_bearing_to_internal_angle_deg(display) == pytest.approx(
            internal % 360.0
        )


def test_lcmv_steering_vector_uses_internal_angle_not_display_bearing() -> None:
    display_bearing = 170.0
    internal_angle = operator_bearing_to_internal_angle_deg(display_bearing)
    wrong_internal = display_bearing
    result = legacy_constraint_null_ideal_weights(
        n_channels=4,
        null_angle_deg=internal_angle,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )
    correct_steering = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)
    wrong_steering = steering_vector(
        np.asarray([wrong_internal], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)

    assert abs(np.vdot(correct_steering, result.weights)) < 1e-10
    assert abs(np.vdot(wrong_steering, result.weights)) > 1e-3


def test_covariance_lcmv_ideal_weights_satisfy_constraints() -> None:
    null_angle = 123.0
    steering = steering_vector(
        np.asarray([null_angle], dtype=np.float64),
        1.57542e9,
        0.07,
    ).reshape(-1)
    covariance = 0.05 * np.eye(4, dtype=np.complex128) + 12.0 * np.outer(
        steering,
        steering.conj(),
    )

    result = uniform_preserving_covariance_lcmv_null_weights(
        covariance=covariance,
        n_channels=4,
        null_angle_deg=null_angle,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
        diagonal_loading_rel=1e-3,
        diagonal_loading_abs=0.0,
    )

    assert result.diagonal_loading > 0.0
    assert np.vdot(np.ones((4,), dtype=np.complex128), result.weights) == pytest.approx(
        4.0 + 0.0j,
        abs=1e-9,
    )
    assert abs(np.vdot(steering, result.weights)) < 1e-8


def test_covariance_lcmv_measured_vector_weights_satisfy_constraints() -> None:
    null_vector = np.array([1.0, 0.7j, -0.4 + 0.2j, 0.2 - 0.9j], dtype=np.complex128)
    null_norm = null_vector / np.linalg.norm(null_vector)
    covariance = 0.03 * np.eye(4, dtype=np.complex128) + 8.0 * np.outer(
        null_norm,
        null_norm.conj(),
    )

    result = uniform_preserving_covariance_vector_null_weights(
        covariance=covariance,
        null_vector=null_vector,
        diagonal_loading_rel=1e-3,
    )

    assert result.null_angle_deg is None
    assert np.vdot(np.ones((4,), dtype=np.complex128), result.weights) == pytest.approx(
        4.0 + 0.0j,
        abs=1e-9,
    )
    assert abs(np.vdot(null_norm, result.weights)) < 1e-8


def test_ideal_angle_fan_weights_null_each_internal_fan_angle() -> None:
    result = legacy_angle_fan_diagnostic_weights(
        n_channels=4,
        center_angle_deg=280.0,
        offsets_deg=[-4.0, 0.0, 4.0],
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )
    steering = steering_vector(
        result.fan_internal_angles_deg,
        1.57542e9,
        0.07,
    )

    assert result.fan_display_bearings_deg.tolist() == pytest.approx(
        [
            internal_angle_to_operator_bearing_deg(angle)
            for angle in result.fan_internal_angles_deg
        ]
    )
    assert np.vdot(np.ones((4,), dtype=np.complex128), result.weights) == pytest.approx(
        4.0 + 0.0j,
        abs=1e-9,
    )
    assert np.max(np.abs(steering.conj().T @ result.weights)) < 1e-8


def test_measured_vector_lcmv_weights_are_finite_and_satisfy_constraints() -> None:
    null_vector = np.array([1.0, 1.0j, -1.0, -1.0j], dtype=np.complex128)
    result = uniform_preserving_vector_null_weights(null_vector=null_vector)
    unity = np.ones((4,), dtype=np.complex128)
    null_norm = null_vector / np.linalg.norm(null_vector)

    assert result.weights.shape == (4,)
    assert np.all(np.isfinite(result.weights))
    assert result.weight_norm <= 8.0
    assert np.vdot(unity, result.weights) == pytest.approx(4.0 + 0.0j, abs=1e-10)
    assert abs(np.vdot(null_norm, result.weights)) < 1e-10
    assert abs(result.unity_residual) < 1e-10
    assert abs(result.null_residual) < 1e-10


def test_lcmv_test_combiner_outputs_complex64_single_stream() -> None:
    rng = np.random.default_rng(17)
    x = (
        rng.standard_normal((4, 128)) + 1j * rng.standard_normal((4, 128))
    ).astype(np.complex128)
    result = legacy_constraint_null_ideal_weights(
        n_channels=4,
        null_angle_deg=120.0,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )

    y = apply_beamformer(x, result.weights)

    assert y.shape == (128,)
    assert y.dtype == np.complex64
    assert np.all(np.isfinite(y))


def test_received_iq_power_uses_channel_sample_power() -> None:
    cfg = StreamConfig()
    runtime = BackendRuntime(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [0 + 3j, 0 + 4j],
        ],
        dtype=np.complex128,
    )

    power_db = runtime._received_iq_power_db(x)
    expected_power_db = 10.0 * np.log10(np.mean(np.abs(x) ** 2))

    assert np.isclose(power_db, expected_power_db)


def test_rx_signal_health_does_not_flag_quiet_iq_as_near_full_scale() -> None:
    x = np.array(
        [
            [0.10 + 0.20j, -0.30 + 0.05j],
            [0.25 - 0.15j, -0.40 - 0.20j],
        ],
        dtype=np.complex64,
    )

    stats = BackendRuntime._rx_signal_health_for_chunk(
        x,
        component_threshold=0.98,
    )

    assert stats["sample_count"] == 4
    assert stats["near_full_scale_count"] == 0
    assert stats["near_full_scale_fraction"] == pytest.approx(0.0)
    assert stats["peak_component"] == pytest.approx(0.40)


def test_rx_signal_health_flags_iq_components_near_full_scale() -> None:
    x = np.array(
        [
            [0.99 + 0.10j, -0.20 + 0.05j],
            [0.15 - 0.99j, -0.40 - 0.20j],
        ],
        dtype=np.complex64,
    )

    stats = BackendRuntime._rx_signal_health_for_chunk(
        x,
        component_threshold=0.98,
    )

    assert stats["sample_count"] == 4
    assert stats["near_full_scale_count"] == 2
    assert stats["near_full_scale_fraction"] == pytest.approx(0.5)
    assert stats["peak_component"] == pytest.approx(0.99)


def test_backend_gnss_weighted_sum_fast_path_matches_effective_weights() -> None:
    rng = np.random.default_rng(42)
    x = (
        rng.normal(size=(4, 64)) + 1j * rng.normal(size=(4, 64))
    ).astype(np.complex64)
    effective_weights = np.array(
        [0.25 + 0.10j, -0.15 + 0.20j, 0.40 - 0.05j, 0.05 - 0.30j],
        dtype=np.complex64,
    )

    y = BackendRuntime._weighted_sum_complex64(x, effective_weights)
    expected = np.asarray(effective_weights @ x, dtype=np.complex64)

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-6)


def test_worker_gnss_output_uses_uniform_combiner_by_default() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    worker = StreamWorker(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [9 + 0j, 8 + 0j],
            [1 + 0j, 1 + 0j],
            [0 + 0j, 0 + 0j],
        ],
        dtype=np.complex64,
    )

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_worker_gnss_output_static_calibration_uses_uniform_combiner() -> None:
    correction = np.array([1 + 0j, 0 - 1j, -1 + 0j, 0 + 1j], dtype=np.complex128)
    weights = uniform_weights(4)
    cfg = StreamConfig(
        phase_correction_vector=tuple(correction),
    )
    worker = StreamWorker(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [0 + 1j, 0 + 2j],
            [3 + 0j, 4 + 0j],
            [0 - 1j, 0 - 2j],
        ],
        dtype=np.complex64,
    )

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(
            x.astype(np.complex128),
            correction_vector=correction,
        ),
        weights,
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)
    assert np.allclose(
        worker._backend._latest_gnss_effective_weights,
        np.asarray(np.conj(weights) * correction, dtype=np.complex64),
    )


def test_backend_gnss_handoff_label_is_uniform_array_sum() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [10 + 0j, 20 + 0j],
            [100 + 0j, 200 + 0j],
            [1000 + 0j, 2000 + 0j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert runtime._gnss_handoff_mode_label() == "uniform_array_sum_continuous"
    assert np.allclose(y, expected, atol=1e-5)


def test_backend_lcmv_test_missing_music_bearing_falls_back_to_uniform() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime.set_lcmv_test_enabled(True)
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [9 + 0j, 8 + 0j],
            [1 + 0j, 1 + 0j],
            [0 + 0j, 0 + 0j],
        ],
        dtype=np.complex64,
    )

    runtime._update_lcmv_test_from_music(
        x.astype(np.complex128),
        float("nan"),
        float("nan"),
    )

    status = runtime._lcmv_status_copy()
    y = runtime._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert status["mode"] == "fallback"
    assert status["fallback_reason"] == "no valid MUSIC bearing available"
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))
    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_backend_lcmv_test_phase_mismatch_keeps_uniform_stream() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        phase_correction_vector=(1 + 0j, 1 + 0j),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime.set_lcmv_test_enabled(True)
    x = np.ones((4, 8), dtype=np.complex64)

    runtime._update_lcmv_test_from_music(
        x.astype(np.complex128),
        10.0,
        80.0,
    )
    y = runtime._gnss_output_vector(x)

    status = runtime._lcmv_status_copy()
    assert status["mode"] == "fallback"
    assert "phase correction" in str(status["fallback_reason"])
    assert "channel count 4" in str(status["fallback_reason"])
    assert y.shape == (8,)
    assert y.dtype == np.complex64
    assert np.allclose(y, np.full((8,), 4.0 + 0.0j, dtype=np.complex64))


def test_backend_lcmv_test_valid_music_bearing_updates_gnss_weights() -> None:
    rng = np.random.default_rng(24)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 88.75
    source_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    source = rng.standard_normal(512) + 1j * rng.standard_normal(512)
    noise = 0.002 * (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    )
    x = (source_vector[:, None] * source[None, :] + noise).astype(np.complex64)

    runtime._update_lcmv_test_from_music(
        x.astype(np.complex128),
        internal_angle,
        (90.0 - internal_angle) % 360.0,
    )

    status = runtime._lcmv_status_copy()
    y = runtime._gnss_output_vector(x)

    assert status["mode"] == "on"
    assert status["description"] == "Nulling strongest MUSIC peak"
    assert status["active_lcmv_null_method"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_method"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_weights_source"] == "covariance_lcmv_ideal"
    lcmv_response_db = np.asarray(status["lcmv_response_db"], dtype=np.float64)
    lcmv_response_abs = np.asarray(status["lcmv_response_abs"], dtype=np.float64)
    output_metrics = status["output_metrics"]
    assert lcmv_response_db.shape == (cfg.doa_points,)
    assert lcmv_response_abs.shape == (cfg.doa_points,)
    assert np.all(np.isfinite(lcmv_response_db))
    assert np.all(np.isfinite(lcmv_response_abs))
    assert status["suppression_db_alias_of"] == "measured_output_reduction_vs_uniform_db"
    assert output_metrics["measured_output_reduction_vs_uniform_db"] == pytest.approx(
        status["suppression_db"]
    )
    assert "lcmv_model_summary" in status
    assert runtime._gnss_handoff_mode_label() == "lcmv_test_nulling_continuous"
    active_weights = np.asarray(
        status["spatial_vector_diagnostics"]["active_lcmv_weights"]["real"],
        dtype=np.float64,
    ) + 1j * np.asarray(
        status["spatial_vector_diagnostics"]["active_lcmv_weights"]["imag"],
        dtype=np.float64,
    )
    assert np.allclose(y, apply_beamformer(x.astype(np.complex128), active_weights))
    assert y.shape == (512,)
    assert y.dtype == np.complex64
    assert np.all(np.isfinite(y))


def test_backend_lcmv_protects_bladerf_bearing_from_nulling() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_desired_loss_guard_enabled=True,
        phase_correction_vector=None,
        experiment={
            "bladeRF_expected_bearing_deg_min": 280.0,
            "bladeRF_expected_bearing_deg_max": 300.0,
            "jammer_expected_bearing_deg_min": 160.0,
            "jammer_expected_bearing_deg_max": 175.0,
        },
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 160.0
    display_bearing = internal_angle_to_operator_bearing_deg(internal_angle)
    x = np.ones((4, 256), dtype=np.complex128)

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)

    status = runtime._lcmv_status_copy()
    assert status["mode"] == "fallback"
    assert "inside protected bladeRF range" in status["fallback_reason"]
    assert status["active_lcmv_fallback_reason"] == status["fallback_reason"]
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


def test_backend_lcmv_jammer_bearing_bypasses_desired_loss_guard() -> None:
    rng = np.random.default_rng(241)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_desired_loss_guard_enabled=True,
        phase_correction_vector=None,
        experiment={
            "bladeRF_expected_bearing_deg_min": 280.0,
            "bladeRF_expected_bearing_deg_max": 300.0,
            "jammer_expected_bearing_deg_min": 160.0,
            "jammer_expected_bearing_deg_max": 175.0,
        },
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 280.0
    display_bearing = internal_angle_to_operator_bearing_deg(internal_angle)
    jammer_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    runtime._healthy_reference_vector = jammer_vector / np.linalg.norm(jammer_vector)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = jammer_vector[:, None] * source[None, :] + noise

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    assert status["mode"] == "on"
    assert spatial["lcmv_target_classification"] == "expected_jammer"
    assert spatial["lcmv_target_confirmed_jammer_bearing"] is True
    assert spatial["lcmv_desired_loss_guard_enforced"] is False
    assert spatial["active_desired_loss_guard_enforced"] is False
    assert (
        spatial["candidate_covariance_lcmv_ideal_desired_loss_vs_reference_db"]
        > cfg.lcmv_max_desired_loss_db
    )
    assert spatial["candidate_covariance_lcmv_ideal_desired_loss_guard_enforced"] is False
    assert "covariance_lcmv_ideal" in spatial["candidate_methods_valid"]


def test_backend_lcmv_unclassified_bearing_keeps_desired_loss_guard() -> None:
    rng = np.random.default_rng(242)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_desired_loss_guard_enabled=True,
        phase_correction_vector=None,
        experiment={
            "bladeRF_expected_bearing_deg_min": 280.0,
            "bladeRF_expected_bearing_deg_max": 300.0,
            "jammer_expected_bearing_deg_min": 160.0,
            "jammer_expected_bearing_deg_max": 175.0,
        },
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 200.0
    display_bearing = internal_angle_to_operator_bearing_deg(internal_angle)
    source_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    runtime._healthy_reference_vector = source_vector / np.linalg.norm(source_vector)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = source_vector[:, None] * source[None, :] + noise

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)

    status = runtime._lcmv_status_copy()
    assert status["mode"] == "fallback"
    assert "desired_loss_db" in status["fallback_reason"]
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


def test_backend_lcmv_disabled_desired_loss_guard_keeps_lcmv_active() -> None:
    rng = np.random.default_rng(243)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_desired_loss_guard_enabled=False,
        phase_correction_vector=None,
        experiment={
            "bladeRF_expected_bearing_deg_min": 280.0,
            "bladeRF_expected_bearing_deg_max": 300.0,
            "jammer_expected_bearing_deg_min": 160.0,
            "jammer_expected_bearing_deg_max": 175.0,
        },
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 200.0
    display_bearing = internal_angle_to_operator_bearing_deg(internal_angle)
    source_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    runtime._healthy_reference_vector = source_vector / np.linalg.norm(source_vector)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = source_vector[:, None] * source[None, :] + noise

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    assert status["mode"] == "on"
    assert status["active_lcmv_fallback_used"] is False
    assert status["active_lcmv_fallback_reason"] == ""
    assert spatial["lcmv_target_classification"] == "unclassified"
    assert spatial["lcmv_desired_loss_guard_enabled"] is False
    assert spatial["lcmv_desired_loss_guard_enforced"] is False
    assert spatial["active_desired_loss_guard_enforced"] is False
    assert spatial["active_desired_loss_vs_reference_db"] > cfg.lcmv_max_desired_loss_db
    assert "covariance_lcmv_ideal" in spatial["candidate_methods_valid"]


def test_backend_lcmv_keeps_covariance_active_when_wng_exceeds_limit() -> None:
    rng = np.random.default_rng(240)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_ideal",
        lcmv_max_white_noise_gain_db=-100.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 280.0
    source_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = (source_vector[:, None] * source[None, :] + noise).astype(np.complex128)

    runtime._update_lcmv_test_from_music(
        x,
        internal_angle,
        internal_angle_to_operator_bearing_deg(internal_angle),
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]

    assert status["mode"] == "on"
    assert status["active_lcmv_method"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_null_method"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_weights_source"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_fallback_used"] is False
    assert status["active_lcmv_fallback_reason"] == ""
    assert "covariance_lcmv_ideal" in spatial["candidate_methods_valid"]
    assert "covariance_lcmv_ideal" not in spatial["candidate_methods_rejected"]
    assert "white_noise_gain_db" in spatial[
        "candidate_covariance_lcmv_ideal_white_noise_gain_warning"
    ]


def test_backend_lcmv_measured_vector_mode_uses_u1_candidate_weights() -> None:
    rng = np.random.default_rng(51)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="measured_dominant_eigenvector",
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    source_vector = np.array([1.0, 1.0j, -0.5, -0.75j], dtype=np.complex128)
    source = rng.standard_normal(512) + 1j * rng.standard_normal(512)
    noise = 0.005 * (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    )
    x = (source_vector[:, None] * source[None, :] + noise).astype(np.complex128)

    runtime._update_lcmv_test_from_music(
        x,
        88.75,
        (90.0 - 88.75) % 360.0,
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]

    assert status["mode"] == "on"
    assert status["active_lcmv_null_method"] == "measured_dominant_eigenvector"
    assert status["active_lcmv_weights_source"] == "measured_dominant_eigenvector"
    assert spatial["candidate_u1_lcmv_available"] is True
    assert spatial["active_lcmv_weights"]["real"] == status["output_metrics"]["lcmv_weights"]["real"]


def test_backend_lcmv_logs_all_candidate_methods_and_angle_fields() -> None:
    rng = np.random.default_rng(64)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_ideal",
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._healthy_reference_vector = np.ones((4,), dtype=np.complex128) / 2.0
    internal_angle = 280.0
    display_bearing = internal_angle_to_operator_bearing_deg(internal_angle)
    jammer_vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = (jammer_vector[:, None] * source[None, :] + noise).astype(np.complex128)

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)
    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]

    assert status["mode"] == "on"
    assert status["active_lcmv_method"] == "covariance_lcmv_ideal"
    assert status["active_lcmv_weights_source"] == "covariance_lcmv_ideal"
    assert status["heavy_diagnostics_emitted"] is True
    assert status["heavy_diagnostics_skipped_due_to_throttle"] is False
    assert spatial["music_internal_angle_deg"] == pytest.approx(280.0)
    assert spatial["music_display_bearing_deg"] == pytest.approx(170.0)
    assert spatial["null_internal_angle_deg"] == pytest.approx(280.0)
    assert spatial["null_display_bearing_deg"] == pytest.approx(170.0)
    assert spatial["steering_vector_angle_used_internal_deg"] == pytest.approx(280.0)
    assert spatial["steering_vector_angle_used_display_deg"] == pytest.approx(170.0)
    assert spatial["display_bearing_formula"] == "(90 - internal_angle_deg) % 360"
    assert set(spatial["candidate_methods_computed"]) == {
        "measured_dominant_eigenvector",
        "covariance_lcmv_ideal",
        "covariance_lcmv_measured_u1",
    }
    assert "ideal_steering" not in spatial["candidate_methods_computed"]
    assert "ideal_angle_fan" not in spatial["candidate_methods_computed"]
    assert "covariance_lcmv_ideal" in spatial["candidate_methods_valid"]
    assert "candidate_covariance_lcmv_ideal_white_noise_gain_db" in spatial
    assert spatial["candidate_covariance_lcmv_ideal_desired_loss_vs_reference_db"] is not None
    assert spatial["candidate_covariance_lcmv_ideal_effective_receiver_improvement_u1_db"] is not None
    assert "candidate_covariance_lcmv_ideal_predicted_target_suppression_db" in spatial
    assert "candidate_covariance_lcmv_ideal_condition_number_constraint" in spatial
    assert "candidate_covariance_lcmv_ideal_output_power_from_R" in spatial
    assert "candidate_covariance_lcmv_ideal_dominant_vector_suppression_db" in spatial
    assert "dominant_vector_suppression_db" in spatial
    assert spatial["covariance_lcmv_ideal_preserve_residual_abs"] < 1e-6
    assert spatial["covariance_lcmv_ideal_null_residual_abs"] < 1e-6
    assert spatial["jammer_only_suppression_estimate_available"] is False
    assert spatial["active_method_applied"] == "covariance_lcmv_ideal"

    runtime._update_lcmv_test_from_music(x, internal_angle, display_bearing)
    status_after_second_update = runtime._lcmv_status_copy()
    assert status_after_second_update["heavy_diagnostics_emitted"] is False
    assert status_after_second_update["heavy_diagnostics_skipped_due_to_throttle"] is True


def test_backend_lcmv_candidate_methods_do_not_feed_fifo_when_disabled() -> None:
    rng = np.random.default_rng(641)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_ideal",
        lcmv_candidate_methods_enabled=False,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    internal_angle = 280.0
    vector = steering_vector(
        np.asarray([internal_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    source = rng.standard_normal(1024) + 1j * rng.standard_normal(1024)
    noise = 0.002 * (
        rng.standard_normal((4, 1024)) + 1j * rng.standard_normal((4, 1024))
    )
    x = (vector[:, None] * source[None, :] + noise).astype(np.complex128)

    runtime._update_lcmv_test_from_music(
        x,
        internal_angle,
        internal_angle_to_operator_bearing_deg(internal_angle),
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    assert status["active_lcmv_weights_source"] == "covariance_lcmv_ideal"
    assert spatial["candidate_methods_computed"] == ["covariance_lcmv_ideal"]
    assert np.allclose(
        runtime._gnss_output_vector(x),
        apply_beamformer(x, runtime._get_beamformer_weights_copy()),
    )


def test_healthy_reference_updates_during_lcmv_off_healthy_baseline() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": True,
            "pvt_gui_status": "FIX",
            "pvt_observations": 10,
            "avg_cno_db_hz": 38.0,
        }
    )
    u1 = np.ones((4,), dtype=np.complex128) / 2.0
    x = np.tile(u1[:, None], (1, 64))

    payload = runtime._update_healthy_reference_tracking_from_music(
        corrected_chunk=x,
        music_internal_deg=280.0,
        music_bearing_deg=170.0,
    )

    assert payload["healthy_reference_updated"] is True
    assert payload["healthy_reference_update_allowed"] is True
    assert payload["run_state_label"] == "healthy_baseline"
    assert payload["lcmv_safe_baseline"] is True
    assert payload["healthy_reference_available"] is True
    assert runtime._healthy_reference_vector is not None


def test_healthy_reference_freezes_during_lcmv_on_even_with_good_gnss() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._set_lcmv_status(enabled=True, mode="on", reason="unit test active null")
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": True,
            "pvt_gui_status": "FIX",
            "pvt_observation_count": 10,
            "avg_tracking_cno_db_hz": 38.0,
        }
    )
    u1 = np.ones((4,), dtype=np.complex128) / 2.0
    x = np.tile(u1[:, None], (1, 64))

    payload = runtime._update_healthy_reference_from_chunk(
        corrected_chunk=x,
        music_internal_deg=280.0,
        music_bearing_deg=170.0,
        u1=u1,
    )

    assert payload["healthy_reference_updated"] is False
    assert payload["healthy_reference_update_allowed"] is False
    assert payload["lcmv_safe_baseline"] is False
    assert "lcmv_active_or_not_safe" in str(payload["healthy_reference_freeze_reason"])
    assert "lcmv_active_or_not_safe" in payload["healthy_reference_freeze_reasons"]
    assert payload["run_state_label"] == "lcmv_on_no_jammer"
    assert runtime._healthy_reference_vector is None


def test_healthy_reference_does_not_treat_no_fix_as_fix() -> None:
    cfg = StreamConfig(lcmv_test_enabled=False, phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": False,
            "pvt_gui_status": "NO_FIX",
            "pvt_observations": 10,
            "avg_cno_db_hz": 38.0,
        }
    )
    u1 = np.ones((4,), dtype=np.complex128) / 2.0
    x = np.tile(u1[:, None], (1, 64))

    payload = runtime._update_healthy_reference_from_chunk(
        corrected_chunk=x,
        music_internal_deg=280.0,
        music_bearing_deg=170.0,
        u1=u1,
    )

    assert payload["pvt_healthy"] is False
    assert payload["healthy_reference_updated"] is False
    assert "pvt_not_healthy" in payload["healthy_reference_freeze_reasons"]


def test_backend_candidate_payload_computes_noise_gain_and_desired_loss() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    weights = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.complex128)
    reference = np.ones((4,), dtype=np.complex128)
    healthy = reference / np.linalg.norm(reference)
    result = SimpleNamespace(
        weights=weights,
        weight_norm=float(np.linalg.norm(weights)),
        condition_number=1.0,
    )

    payload, rejection = runtime._candidate_method_payload(
        prefix="candidate_test",
        result=result,
        error="",
        covariance=np.eye(4, dtype=np.complex128),
        reference_weights=reference,
        uniform_sum_weights=reference,
        uniform_average_weights=reference / 4.0,
        ideal_vector=healthy,
        u1_vector=healthy,
        healthy_reference_vector=healthy,
    )

    assert payload["candidate_test_noise_gain_vs_reference_db"] == pytest.approx(
        10.0 * np.log10(1.0 / 4.0)
    )
    assert payload["candidate_test_desired_loss_vs_reference_db"] == pytest.approx(
        10.0 * np.log10(4.0 / 0.25)
    )
    assert "desired_loss_db" in rejection
