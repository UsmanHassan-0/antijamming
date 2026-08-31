from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import numpy as np
import pytest

from antijamming.config import StreamConfig
from antijamming.dsp.beamforming import (
    apply_beamformer,
    covariance_lcmv_ideal_null_weights,
    covariance_lcmv_vector_null_weights,
    uniform_weights,
)
from antijamming.dsp.doa.music import steering_vector
from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
)
from antijamming.dsp.phase import apply_phase_calibration
from antijamming.runtime import BackendRuntime


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


def _prime_realtime_bladerf_reference(
    runtime: BackendRuntime,
    cfg: StreamConfig,
    *,
    internal_angle_deg: float,
    baseline_power_linear: float = 1.0,
    measured_vector: np.ndarray | None = None,
) -> np.ndarray:
    vector = (
        np.asarray(measured_vector, dtype=np.complex128).reshape(-1)
        if measured_vector is not None
        else steering_vector(
            np.asarray([internal_angle_deg], dtype=np.float64),
            cfg.center_freq_hz,
            cfg.array_spacing_m,
        ).reshape(-1)
    )
    normalized = vector / np.linalg.norm(vector)
    runtime._healthy_reference_vector = normalized
    runtime._healthy_reference_covariance = np.eye(4, dtype=np.complex128)
    runtime._healthy_reference_internal_angle_deg = internal_angle_deg
    runtime._healthy_reference_display_bearing_deg = (
        internal_angle_to_operator_bearing_deg(internal_angle_deg)
    )
    runtime._healthy_reference_updated_monotonic_s = time.monotonic()
    runtime._healthy_reference_confidence = 1.0
    runtime._healthy_reference_raw_power_linear = baseline_power_linear
    runtime._healthy_reference_cal_power_linear = baseline_power_linear
    return normalized


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


def test_dynamic_phase_source_mapping_ignores_stale_latest_by_prn_entry() -> None:
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(),
        gnss_1c_channel_count=4,
        gnss_channels_in_acquisition=4,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    snapshot = {
        # This collection deliberately reproduces the live failure: stale G23
        # and current G14 both claim channel 2, and sorted PRN order would make
        # G23 win if this archive-like collection were used for routing.
        "tracking_monitor": [
            {"channel": 2, "prn": 14, "system": "G", "signal": "1C"},
            {"channel": 2, "prn": 23, "system": "G", "signal": "1C"},
        ],
        "prns": [
            {
                "channel": 2,
                "prn": 14,
                "tracking_monitor_prn": 14,
                "state": "tracking",
                "system": "G",
                "signal": "1C",
            },
            {
                "channel": 2,
                "prn": 23,
                "tracking_monitor_prn": 14,
                "state": "lost",
                "system": "G",
                "signal": "1C",
            },
        ],
    }

    assert runtime._tracking_source_satellites(snapshot) == (None, None, 14, None)


def test_lcmv_steering_vector_uses_internal_angle_not_display_bearing() -> None:
    display_bearing = 170.0
    internal_angle = 280.0
    wrong_internal = display_bearing
    result = covariance_lcmv_ideal_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
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

    result = covariance_lcmv_ideal_null_weights(
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

    result = covariance_lcmv_vector_null_weights(
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


def test_lcmv_test_combiner_outputs_complex64_single_stream() -> None:
    rng = np.random.default_rng(17)
    x = (
        rng.standard_normal((4, 128)) + 1j * rng.standard_normal((4, 128))
    ).astype(np.complex128)
    result = covariance_lcmv_ideal_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
        n_channels=4,
        null_angle_deg=120.0,
        rf_freq_hz=1.57542e9,
        array_spacing_m=0.07,
    )

    y = apply_beamformer(x, result.weights)

    assert y.shape == (128,)
    assert y.dtype == np.complex64
    assert np.all(np.isfinite(y))


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


def test_beamformer_weight_ramp_preserves_measured_u1_response_each_chunk() -> None:
    cfg = StreamConfig(
        phase_correction_vector=None,
        lcmv_weight_transition_s=(3.0 * 32768.0 / 4_000_000.0),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    preserve = np.array(
        [1.0 + 0.0j, 0.55 + 0.35j, -0.25 + 0.80j, -0.65 - 0.15j],
        dtype=np.complex128,
    )
    preserve /= np.linalg.norm(preserve)
    null_vector = steering_vector(
        np.asarray([150.0], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    target = covariance_lcmv_vector_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
        null_vector=null_vector,
        preserve_vector=preserve,
    ).weights
    uniform = uniform_weights(4)
    preserve_target = np.vdot(preserve, uniform)
    chunk = np.ones((4, 16), dtype=np.complex64)

    runtime._schedule_beamformer_weights(target, reason="unit-test smooth ramp")
    before = runtime._beamformer_transition_payload()
    assert before["weight_transition_active"] is True
    assert before["weight_transition_total_chunks"] == 3

    for step in range(1, 4):
        runtime._gnss_output_vector(chunk)
        applied = runtime._get_beamformer_weights_copy()
        expected = uniform + (step / 3.0) * (target - uniform)
        assert np.allclose(applied, expected, atol=1e-10)
        assert abs(np.vdot(preserve, applied) - preserve_target) < 1e-10

    after = runtime._beamformer_transition_payload()
    assert after["weight_transition_active"] is False
    assert after["weight_transition_progress"] == pytest.approx(1.0)
    assert np.allclose(runtime._get_beamformer_weights_copy(), target)


def test_covariance_target_update_does_not_restart_active_weight_ramp() -> None:
    cfg = StreamConfig(
        phase_correction_vector=None,
        lcmv_weight_transition_s=(3.0 * 32768.0 / 4_000_000.0),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    first_target = np.array(
        [0.8 + 0.1j, 0.6 - 0.2j, 1.1 + 0.3j, 0.7 - 0.1j],
        dtype=np.complex128,
    )
    newer_target = np.array(
        [0.5 - 0.2j, 1.2 + 0.1j, 0.4 + 0.5j, 0.9 - 0.3j],
        dtype=np.complex128,
    )
    chunk = np.ones((4, 16), dtype=np.complex64)

    runtime._schedule_beamformer_weights(
        first_target,
        reason="first covariance target",
        preempt_active_transition=False,
    )
    runtime._gnss_output_vector(chunk)
    one_chunk = runtime._beamformer_transition_payload()
    assert one_chunk["weight_transition_completed_chunks"] == 1

    returned_target = runtime._schedule_beamformer_weights(
        newer_target,
        reason="new covariance target while ramping",
        preempt_active_transition=False,
    )
    deferred = runtime._beamformer_transition_payload()
    assert np.allclose(returned_target, first_target)
    assert deferred["weight_transition_completed_chunks"] == 1
    assert deferred["weight_transition_total_chunks"] == 3
    assert np.allclose(runtime._target_beamformer_weights, first_target)

    runtime._gnss_output_vector(chunk)
    runtime._gnss_output_vector(chunk)
    completed = runtime._beamformer_transition_payload()
    assert completed["weight_transition_active"] is False
    assert np.allclose(runtime._get_beamformer_weights_copy(), first_target)

    runtime._schedule_beamformer_weights(
        newer_target,
        reason="new covariance target after completed ramp",
        preempt_active_transition=False,
    )
    next_ramp = runtime._beamformer_transition_payload()
    assert next_ramp["weight_transition_active"] is True
    assert next_ramp["weight_transition_completed_chunks"] == 0
    assert np.allclose(runtime._beamformer_transition_start_weights, first_target)
    assert np.allclose(runtime._target_beamformer_weights, newer_target)


def test_backend_gnss_output_uses_uniform_combiner_by_default() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [9 + 0j, 8 + 0j],
            [1 + 0j, 1 + 0j],
            [0 + 0j, 0 + 0j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_backend_gnss_output_static_calibration_uses_uniform_combiner() -> None:
    correction = np.array([1 + 0j, 0 - 1j, -1 + 0j, 0 + 1j], dtype=np.complex128)
    weights = uniform_weights(4)
    cfg = StreamConfig(
        phase_correction_vector=tuple(correction),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [0 + 1j, 0 + 2j],
            [3 + 0j, 4 + 0j],
            [0 - 1j, 0 - 2j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_output_vector(x)
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
        runtime._latest_gnss_effective_weights,
        np.asarray(np.conj(weights) * correction, dtype=np.complex64),
    )


def test_backend_gnss_handoff_label_is_uniform_array_sum() -> None:
    cfg = StreamConfig(
        phase_correction_vector=None,
        gnss_shared_u1_phase_compensation_enabled=False,
    )
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
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
        lcmv_weight_transition_s=0.0,
        phase_correction_vector=None,
        gnss_shared_u1_phase_compensation_enabled=False,
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
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
        lcmv_weight_transition_s=0.0,
        phase_correction_vector=None,
        gnss_shared_u1_phase_compensation_enabled=False,
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
    assert status["description"] == "Covariance LCMV null active"
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
    assert "suppression_db" not in status
    assert "suppression_db_alias_of" not in status
    assert output_metrics["measured_output_reduction_vs_uniform_db"] is not None
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


def test_backend_lcmv_keeps_covariance_active_when_wng_exceeds_limit() -> None:
    rng = np.random.default_rng(240)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_ideal",
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
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


def test_backend_covariance_lcmv_measured_u1_mode_uses_covariance_weights() -> None:
    rng = np.random.default_rng(51)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_measured_u1",
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
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
    assert status["active_lcmv_null_method"] == "covariance_lcmv_measured_u1"
    assert status["active_lcmv_weights_source"] == "covariance_lcmv_measured_u1"
    assert "covariance_lcmv_measured_u1" in spatial["candidate_methods_valid"]
    assert spatial["candidate_covariance_lcmv_measured_u1_condition_number_R"] is not None
    assert spatial["active_lcmv_weights"]["real"] == status["output_metrics"]["lcmv_weights"]["real"]


def test_backend_lcmv_logs_all_candidate_methods_and_angle_fields() -> None:
    rng = np.random.default_rng(64)
    cfg = StreamConfig(
        lcmv_test_enabled=True,
        lcmv_test_null_method="covariance_lcmv_ideal",
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
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
        lcmv_preserve_constraint_mode="uniform",
        lcmv_target_selection_mode="strongest_music_peak",
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


def test_realtime_bladerf_tracker_wraps_angles_and_freezes_only_when_stable() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        lcmv_target_selection_mode="realtime_non_preserve_peak",
        lcmv_realtime_preserve_window_samples=8,
        lcmv_realtime_preserve_min_samples=4,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        lcmv_realtime_preserve_max_step_deg=15.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())

    for angle in (359.0, 1.0, 0.0, 2.0):
        payload = runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )

    center = float(payload["realtime_bladerf_tracker_center_internal_deg"])
    assert payload["realtime_bladerf_tracker_sample_count"] == 4
    assert payload["realtime_bladerf_tracker_stable"] is True
    assert min(center, 360.0 - center) < 1.0

    _prime_realtime_bladerf_reference(
        runtime,
        cfg,
        internal_angle_deg=center,
    )
    runtime.set_lcmv_test_enabled(True)
    frozen = runtime._realtime_preserve_tracker_payload()
    armed_status = runtime._lcmv_status_copy()
    frozen_angle = float(frozen["realtime_bladerf_frozen_internal_deg"])
    assert frozen["realtime_bladerf_angle_frozen"] is True
    assert frozen_angle == pytest.approx(center)
    assert armed_status["mode"] == "fallback"
    assert armed_status["spatial_vector_diagnostics"][
        "lcmv_jammer_activation_armed"
    ] is True

    after_jammer_like_peak = runtime._update_realtime_bladerf_angle_tracker(
        {"doa_peaks": [{"angle_deg": 140.0}]},
        primary_internal_deg=140.0,
    )
    assert after_jammer_like_peak["realtime_bladerf_frozen_internal_deg"] == pytest.approx(
        frozen_angle
    )
    assert after_jammer_like_peak["realtime_bladerf_tracker_center_internal_deg"] == pytest.approx(
        center
    )
    assert "frozen while LCMV is enabled" in str(
        after_jammer_like_peak["realtime_bladerf_tracker_reason"]
    )


def test_realtime_lcmv_target_ignores_peaks_inside_frozen_bladerf_guard() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        lcmv_target_selection_mode="realtime_non_preserve_peak",
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        lcmv_realtime_preserve_guard_deg=20.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )
    _prime_realtime_bladerf_reference(
        runtime,
        cfg,
        internal_angle_deg=40.0,
    )
    runtime.set_lcmv_test_enabled(True)

    internal, display, source = runtime._select_lcmv_target_from_doa_metrics(
        {
            "doa_peaks": [
                {"angle_deg": 42.0},
                {"angle_deg": 150.0},
            ]
        },
        primary_internal_deg=42.0,
        primary_display_deg=48.0,
    )

    assert internal == pytest.approx(150.0)
    assert display == pytest.approx(internal_angle_to_operator_bearing_deg(150.0))
    assert source == "strongest_music_peak_outside_frozen_bladerf_guard"


def test_realtime_lcmv_stays_uniform_for_angle_jump_without_jammer_evidence() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        lcmv_target_selection_mode="realtime_non_preserve_peak",
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        lcmv_realtime_preserve_guard_deg=20.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )
    _prime_realtime_bladerf_reference(
        runtime,
        cfg,
        internal_angle_deg=40.0,
        baseline_power_linear=1.0,
    )
    runtime.set_lcmv_test_enabled(True)
    x = 0.1 * np.ones((4, 1024), dtype=np.complex128)

    runtime._update_lcmv_test_from_music(
        x,
        150.0,
        internal_angle_to_operator_bearing_deg(150.0),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
        target_selection_source="strongest_music_peak_outside_frozen_bladerf_guard",
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    assert status["mode"] == "fallback"
    assert "armed with frozen measured bladeRF U1" in status["fallback_reason"]
    assert spatial["lcmv_jammer_activation_evidence_now"] is False
    assert spatial["lcmv_jammer_detected_latched"] is False
    assert spatial["lcmv_jammer_activation_angle_only_forbidden"] is True
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


def test_realtime_lcmv_preserves_frozen_bladerf_angle_and_nulls_other_peak() -> None:
    rng = np.random.default_rng(645)
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_test_null_method="covariance_lcmv_ideal",
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        lcmv_target_selection_mode="realtime_non_preserve_peak",
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        lcmv_realtime_preserve_guard_deg=20.0,
        lcmv_weight_transition_s=0.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )
    measured_preserve_vector = np.array(
        [1.0 + 0.0j, 0.55 + 0.35j, -0.25 + 0.80j, -0.65 - 0.15j],
        dtype=np.complex128,
    )
    preserve_vector = _prime_realtime_bladerf_reference(
        runtime,
        cfg,
        internal_angle_deg=40.0,
        baseline_power_linear=0.01,
        measured_vector=measured_preserve_vector,
    )
    runtime.set_lcmv_test_enabled(True)
    tracker = runtime._realtime_preserve_tracker_payload()
    preserve_angle = float(tracker["realtime_bladerf_frozen_internal_deg"])
    null_angle = 150.0

    null_vector = steering_vector(
        np.asarray([null_angle], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    desired = rng.standard_normal(2048) + 1j * rng.standard_normal(2048)
    jammer = rng.standard_normal(2048) + 1j * rng.standard_normal(2048)
    noise = 0.01 * (
        rng.standard_normal((4, 2048)) + 1j * rng.standard_normal((4, 2048))
    )
    x = (
        0.5 * preserve_vector[:, None] * desired[None, :]
        + 2.0 * null_vector[:, None] * jammer[None, :]
        + noise
    ).astype(np.complex128)

    runtime._update_lcmv_test_from_music(
        x,
        null_angle,
        internal_angle_to_operator_bearing_deg(null_angle),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
        target_selection_source="strongest_music_peak_outside_frozen_bladerf_guard",
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    weights = runtime._get_beamformer_weights_copy()
    preserve_norm = preserve_vector / np.linalg.norm(preserve_vector)
    null_norm = null_vector / np.linalg.norm(null_vector)
    uniform = uniform_weights(4)

    assert status["mode"] == "on"
    assert spatial["lcmv_jammer_activation_evidence_now"] is True
    assert spatial["lcmv_jammer_detected_latched"] is True
    assert spatial["lcmv_preserve_constraint_mode"] == "realtime_bladerf_measured_u1"
    assert spatial["lcmv_preserve_internal_angle_deg"] == pytest.approx(preserve_angle)
    assert spatial["null_internal_angle_deg"] == pytest.approx(null_angle)
    assert abs(np.vdot(preserve_norm, weights) - np.vdot(preserve_norm, uniform)) < 1e-6
    assert abs(np.vdot(null_norm, weights)) < 1e-6
    assert status["preserve_residual_abs"] < 1e-6
    assert status["null_residual_abs"] < 1e-6

    after_drop = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 0.01},
        cal_power_metrics={"cal_avg_channel_power_linear": 0.01},
    )
    assert after_drop["lcmv_jammer_activation_evidence_now"] is False
    assert after_drop["lcmv_jammer_detected_latched"] is True


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


def test_healthy_pvt_auto_arms_lcmv_but_keeps_uniform_until_jammer() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_auto_arm_after_pvt=True,
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        lcmv_target_selection_mode="realtime_non_preserve_peak",
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        gnss_shared_u1_phase_compensation_enabled=True,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": True,
            "pvt_gui_status": "FIX",
            "pvt_observation_count": 10,
            "avg_tracking_cno_db_hz": 42.0,
        }
    )
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )
    desired = steering_vector(
        np.asarray([40.0]), cfg.center_freq_hz, cfg.array_spacing_m
    ).reshape(-1)
    x = np.tile(desired[:, None], (1, 256))

    payload = runtime._update_healthy_reference_tracking_from_music(
        corrected_chunk=x,
        music_internal_deg=40.0,
        music_bearing_deg=internal_angle_to_operator_bearing_deg(40.0),
    )

    status = runtime._lcmv_status_copy()
    assert payload["lcmv_auto_arm_triggered"] is True
    assert runtime._lcmv_test_enabled is True
    assert runtime._lcmv_jammer_detected_latched is False
    assert status["mode"] == "fallback"
    assert status["spatial_vector_diagnostics"][
        "lcmv_jammer_activation_armed"
    ] is True
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


def test_lcmv_auto_arm_waits_for_pvt_and_respects_manual_off() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        lcmv_auto_arm_after_pvt=True,
        lcmv_preserve_constraint_mode="realtime_bladerf_measured_u1",
        gnss_shared_u1_phase_compensation_enabled=True,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime.set_lcmv_test_enabled(False)

    armed = runtime._maybe_auto_arm_lcmv_after_pvt(
        {
            "healthy_reference_updated": True,
            "pvt_healthy": True,
            "observations_healthy": True,
            "cn0_healthy": True,
        }
    )

    assert armed is False
    assert runtime._lcmv_auto_arm_suppressed_by_operator is True
    assert runtime._lcmv_test_enabled is False


def test_healthy_reference_does_not_treat_music_local_peaks_as_emitters() -> None:
    cfg = StreamConfig(
        lcmv_test_enabled=False,
        expected_sources=1,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": True,
            "pvt_gui_status": "FIX",
            "pvt_observation_count": 7,
            "avg_tracking_cno_db_hz": 45.3,
        }
    )
    runtime._latest_source_count_diagnostics = {
        "source_estimate_gap": 1,
        "peak_count": 4,
        "source_effective_rank": 2.66,
    }
    u1 = np.ones((4,), dtype=np.complex128) / 2.0
    x = np.tile(u1[:, None], (1, 64))

    payload = runtime._update_healthy_reference_from_chunk(
        corrected_chunk=x,
        music_internal_deg=307.0,
        music_bearing_deg=143.0,
        u1=u1,
    )

    assert payload["suspicious_source_structure"] is False
    assert payload["healthy_reference_update_allowed"] is True
    assert payload["healthy_reference_updated"] is True


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


def test_backend_candidate_payload_keeps_desired_loss_diagnostic_only() -> None:
    cfg = StreamConfig(
        phase_correction_vector=None,
        lcmv_min_predicted_jammer_suppression_db=3.0,
    )
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
    assert rejection == ""


def test_jammer_excess_covariance_reports_applied_and_target_suppression() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    jammer_vector = steering_vector(
        np.asarray([150.0], dtype=np.float64),
        cfg.center_freq_hz,
        cfg.array_spacing_m,
    ).reshape(-1)
    jammer_vector /= np.linalg.norm(jammer_vector)
    baseline = 0.01 * np.eye(4, dtype=np.complex128)
    current = baseline + 10.0 * np.outer(jammer_vector, jammer_vector.conj())
    target = covariance_lcmv_vector_null_weights(
        covariance=current,
        null_vector=jammer_vector,
        preserve_vector=np.ones((4,), dtype=np.complex128),
    ).weights
    runtime._realtime_preserve_frozen_covariance = baseline
    runtime._set_beamformer_weights(target)

    payload = runtime._jammer_excess_covariance_payload(
        current_covariance=current,
        uniform_weights_vector=uniform_weights(4),
        target_weights=target,
        jammer_latched=True,
    )

    assert payload["jammer_only_suppression_estimate_available"] is True
    assert payload["jammer_only_suppression_db"] > 100.0
    assert payload["jammer_only_target_suppression_db"] > 100.0
    assert payload["jammer_only_power_before_uniform_linear"] > 0.0
    assert payload["jammer_only_power_after_applied_linear"] >= 0.0


def test_jammer_excess_covariance_is_unavailable_before_jammer_latch() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    payload = runtime._jammer_excess_covariance_payload(
        current_covariance=np.eye(4, dtype=np.complex128),
        uniform_weights_vector=uniform_weights(4),
        target_weights=uniform_weights(4),
        jammer_latched=False,
    )

    assert payload["jammer_only_suppression_estimate_available"] is False
    assert payload["jammer_only_suppression_db"] is None
    assert "not latched" in payload["jammer_only_suppression_unavailable_reason"]
