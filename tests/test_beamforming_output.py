from __future__ import annotations

import logging
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import antijamming.dsp.beamforming as beamforming
import antijamming.dsp.beamforming.lcmv as lcmv_module
from antijamming.config import StreamConfig
from antijamming.dsp.beamforming import (
    apply_beamformer,
    covariance_lcmv_vector_null_weights,
    uniform_weights,
)
from antijamming.dsp.doa.music import steering_vector
from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
)
from antijamming.dsp.phase import apply_phase_calibration
from antijamming.gnss.shared_u1_phase_compensation import (
    SharedU1PhaseCompensationBank,
)
from antijamming.runtime import BackendRuntime
from antijamming.runtime.ipc import metrics_for_wire


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


def _install_product_fifo_bank(runtime: BackendRuntime) -> None:
    cfg = runtime._config
    source_count = int(cfg.gnss_1c_channel_count)
    runtime._shared_u1_phase_bank = SharedU1PhaseCompensationBank(
        source_count=source_count,
        channel_count=len(cfg.channels),
        sample_rate_hz=float(cfg.sample_rate),
        samples_per_chunk=int(cfg.samples_per_chunk),
        transition_s=float(cfg.gnss_shared_u1_phase_transition_s),
        max_weight_norm=float(cfg.lcmv_max_weight_norm),
    )
    runtime._shared_u1_source_satellites_cache = tuple(
        None for _ in range(source_count)
    )
    runtime._last_shared_u1_phase_status_log_ts = time.monotonic()


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


def _arm_from_healthy_reference(runtime: BackendRuntime) -> None:
    """Exercise the automatic transition with explicit healthy receiver evidence."""

    assert runtime._maybe_auto_arm_lcmv_after_pvt({
        "healthy_reference_updated": True,
        "pvt_healthy": True,
        "observations_healthy": True,
        "cn0_healthy": True,
    })


def _prepare_automatic_reference(runtime: BackendRuntime) -> None:
    """Seed a stable synthetic baseline for tests of post-arming behavior."""

    _seed_automatic_reference(runtime)
    _arm_from_healthy_reference(runtime)


def _seed_automatic_reference(runtime: BackendRuntime) -> None:
    cfg = runtime._config
    for _ in range(cfg.lcmv_realtime_preserve_min_samples):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": 40.0}]}, primary_internal_deg=40.0
        )
    _prime_realtime_bladerf_reference(runtime, cfg, internal_angle_deg=40.0)


def test_uniform_weights_are_raw_sum_coefficients() -> None:
    weights = uniform_weights(4)

    assert weights.shape == (4,)
    assert np.allclose(weights, np.ones((4,), dtype=np.complex128))
    assert np.isclose(np.sum(weights), 4.0 + 0.0j)


def test_ideal_null_solver_is_removed_not_disabled() -> None:
    assert not hasattr(beamforming, "covariance_lcmv_ideal_null_weights")
    assert not hasattr(lcmv_module, "covariance_lcmv_ideal_null_weights")
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    manifest = runtime._lcmv_runtime_manifest()
    assert manifest["configured_common_lcmv_method"] == "covariance_lcmv_measured_u1"
    assert manifest["candidate_methods"] == ["covariance_lcmv_measured_u1"]


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

    assert not hasattr(result, "null_angle_deg")
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
    result = covariance_lcmv_vector_null_weights(
        covariance=np.eye(4, dtype=np.complex128),
        null_vector=np.array([1.0, 0.7j, -0.4 + 0.2j, 0.2 - 0.9j]),
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
    runtime._schedule_beamformer_weights(target, reason="unit-test smooth ramp")
    before = runtime._beamformer_transition_payload()
    assert before["weight_transition_active"] is True
    assert before["weight_transition_total_chunks"] == 3

    for step in range(1, 4):
        runtime._advance_beamformer_transition()
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
    runtime._schedule_beamformer_weights(
        first_target,
        reason="first covariance target",
        preempt_active_transition=False,
    )
    runtime._advance_beamformer_transition()
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

    runtime._advance_beamformer_transition()
    runtime._advance_beamformer_transition()
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


def test_backend_product_fifo_rows_use_uniform_combiner_by_default() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [9 + 0j, 8 + 0j],
            [1 + 0j, 1 + 0j],
            [0 + 0j, 0 + 0j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_shared_u1_phase_output_matrix(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert y.shape == (cfg.gnss_1c_channel_count, x.shape[1])
    assert y.dtype == np.complex64
    assert np.allclose(y, np.tile(expected, (cfg.gnss_1c_channel_count, 1)), atol=1e-5)


def test_backend_product_fifo_rows_apply_static_calibration() -> None:
    correction = np.array([1 + 0j, 0 - 1j, -1 + 0j, 0 + 1j], dtype=np.complex128)
    weights = uniform_weights(4)
    cfg = StreamConfig(
        phase_correction_vector=tuple(correction),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [0 + 1j, 0 + 2j],
            [3 + 0j, 4 + 0j],
            [0 - 1j, 0 - 2j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_shared_u1_phase_output_matrix(x)
    expected = apply_beamformer(
        apply_phase_calibration(
            x.astype(np.complex128),
            correction_vector=correction,
        ),
        weights,
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, np.tile(expected, (cfg.gnss_1c_channel_count, 1)), atol=1e-5)
    assert np.allclose(
        runtime._latest_gnss_effective_weights,
        np.asarray(np.conj(weights) * correction, dtype=np.complex64),
    )


def test_backend_gnss_handoff_label_is_uniform_array_sum() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [10 + 0j, 20 + 0j],
            [100 + 0j, 200 + 0j],
            [1000 + 0j, 2000 + 0j],
        ],
        dtype=np.complex64,
    )

    y = runtime._gnss_shared_u1_phase_output_matrix(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert runtime._gnss_handoff_mode_label() == "shared_prn_phase_continuity_fanout"
    assert np.allclose(y, np.tile(expected, (cfg.gnss_1c_channel_count, 1)), atol=1e-5)


def test_backend_lcmv_test_missing_music_bearing_falls_back_to_uniform() -> None:
    cfg = StreamConfig(
        lcmv_weight_transition_s=0.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    _prepare_automatic_reference(runtime)
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
        target_selection_source="test_injected_invalid_target",
    )

    status = runtime._lcmv_status_copy()
    y = runtime._gnss_shared_u1_phase_output_matrix(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128)),
        uniform_weights(len(cfg.channels)),
    )

    assert status["mode"] == "fallback"
    assert status["active_lcmv_method"] == "uniform_array_sum"
    assert status["active_lcmv_null_method"] == "none"
    assert status["fallback_reason"] == "no valid MUSIC bearing available"
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))
    assert y.dtype == np.complex64
    assert np.allclose(y, np.tile(expected, (cfg.gnss_1c_channel_count, 1)), atol=1e-5)


def test_lcmv_status_names_only_the_method_that_is_actually_applied() -> None:
    runtime = BackendRuntime(StreamConfig(), _build_loggers())

    off = runtime._lcmv_status_snapshot(enabled=False, mode="off")
    fallback = runtime._lcmv_status_snapshot(enabled=True, mode="fallback")
    active = runtime._lcmv_status_snapshot(enabled=True, mode="on")

    assert off["active_lcmv_method"] == "uniform_array_sum"
    assert off["active_lcmv_null_method"] == "none"
    assert fallback["active_lcmv_method"] == "uniform_array_sum"
    assert fallback["active_lcmv_null_method"] == "none"
    assert active["active_lcmv_method"] == "covariance_lcmv_measured_u1"
    assert active["active_lcmv_null_method"] == "covariance_lcmv_measured_u1"


def test_backend_product_fifo_rejects_phase_correction_length_mismatch() -> None:
    cfg = StreamConfig(
        phase_correction_vector=(1 + 0j, 1 + 0j),
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    x = np.ones((4, 8), dtype=np.complex64)

    with pytest.raises(ValueError, match="phase correction length mismatch"):
        runtime._gnss_shared_u1_phase_output_matrix(x)


def test_realtime_bladerf_tracker_wraps_angles_and_freezes_only_when_stable() -> None:
    cfg = StreamConfig(
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
    _arm_from_healthy_reference(runtime)
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
    _arm_from_healthy_reference(runtime)

    internal, display, source = runtime._select_lcmv_target_from_doa_metrics(
        {
            "doa_peaks": [
                {"angle_deg": 42.0},
                {"angle_deg": 150.0},
            ]
        },
    )

    assert internal == pytest.approx(150.0)
    assert display == pytest.approx(internal_angle_to_operator_bearing_deg(150.0))
    assert source == "strongest_music_peak_outside_frozen_bladerf_guard"


def test_realtime_lcmv_stays_uniform_for_angle_jump_without_jammer_evidence() -> None:
    cfg = StreamConfig(
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
    _arm_from_healthy_reference(runtime)
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
    assert spatial["lcmv_target_activation_evidence_met"] is False
    assert "lcmv_target_confirmed_jammer_bearing" not in spatial
    assert spatial["lcmv_jammer_activation_angle_only_forbidden"] is True
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


@pytest.mark.parametrize("reported_music_angle", [150.0, 250.0])
def test_realtime_lcmv_preserves_frozen_vector_and_nulls_measured_u1(
    reported_music_angle: float, monkeypatch,
) -> None:
    rng = np.random.default_rng(645)
    cfg = StreamConfig(
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
    _arm_from_healthy_reference(runtime)
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

    calls = []
    def measured_solver(**kwargs):
        calls.append(kwargs)
        return covariance_lcmv_vector_null_weights(**kwargs)
    monkeypatch.setattr(
        "antijamming.runtime.backend.covariance_lcmv_vector_null_weights", measured_solver
    )
    runtime._update_lcmv_test_from_music(
        x,
        reported_music_angle,
        internal_angle_to_operator_bearing_deg(reported_music_angle),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
        target_selection_source="strongest_music_peak_outside_frozen_bladerf_guard",
    )

    status = runtime._lcmv_status_copy()
    spatial = status["spatial_vector_diagnostics"]
    weights = runtime._get_beamformer_weights_copy()
    preserve_norm = preserve_vector / np.linalg.norm(preserve_vector)
    _, eigenvectors = np.linalg.eigh(x @ x.conj().T / x.shape[1])
    null_norm = eigenvectors[:, -1]
    uniform = uniform_weights(4)

    assert status["mode"] == "on"
    assert spatial["lcmv_jammer_activation_evidence_now"] is True
    assert spatial["lcmv_jammer_detected_latched"] is True
    assert spatial["lcmv_target_activation_evidence_met"] is True
    assert "lcmv_target_confirmed_jammer_bearing" not in spatial
    assert spatial["preserve_strategy"] == "realtime_bladerf_measured_u1"
    assert spatial["lcmv_preserve_internal_angle_deg"] == pytest.approx(preserve_angle)
    assert "null_internal_angle_deg" not in spatial
    assert "null_internal_deg" not in status
    assert spatial["music_internal_angle_deg"] == reported_music_angle
    assert spatial["active_lcmv_method"] == "covariance_lcmv_measured_u1"
    assert spatial["candidate_methods_computed"] == ["covariance_lcmv_measured_u1"]
    assert len(calls) == 1
    assert "null_angle_deg" not in calls[0]
    assert np.allclose(weights, runtime._get_shared_measured_u1_protection_weights_copy())
    assert not any("ideal_lcmv" in key or "lcmv_ideal" in key for key in spatial)
    assert abs(np.vdot(preserve_norm, weights) - np.vdot(preserve_norm, uniform)) < 1e-6
    assert abs(np.vdot(null_norm, weights)) < 1e-6
    assert status["preserve_residual_abs"] < 1e-6
    assert status["null_residual_abs"] < 1e-6
    assert status["gnss_fifo_measured_u1_protection_available"] is True
    assert np.asarray(
        status["gnss_fifo_measured_u1_model_response_db"], dtype=np.float64
    ).shape == (cfg.doa_points,)
    assert status["gnss_fifo_measured_u1_response_scope"] == (
        "ideal_steering_scan_of_shared_weights_not_measured_ota_null"
    )

    after_drop = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 0.01},
        cal_power_metrics={"cal_avg_channel_power_linear": 0.01},
    )
    assert after_drop["lcmv_jammer_activation_evidence_now"] is False
    assert after_drop["lcmv_jammer_detected_latched"] is True
    assert after_drop["lcmv_jammer_protection_active"] is True


def test_jammer_release_requires_valid_low_evidence_for_full_hold(monkeypatch) -> None:
    cfg = StreamConfig(
        lcmv_jammer_release_max_input_power_jump_db=1.5,
        lcmv_jammer_release_max_generalized_gain_db=3.0,
        lcmv_jammer_release_hold_s=2.0,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    runtime._realtime_preserve_frozen_covariance = np.eye(4, dtype=np.complex128)
    runtime._realtime_preserve_frozen_raw_power_linear = 1.0
    runtime._realtime_preserve_frozen_cal_power_linear = 1.0
    clock = [0.0]
    monkeypatch.setattr(
        "antijamming.runtime.backend.time.monotonic",
        lambda: clock[0],
    )

    activated = runtime._lcmv_jammer_activation_evidence(
        covariance=10.0 * np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 10.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 10.0},
    )
    assert activated["lcmv_jammer_activation_evidence_now"] is True
    assert activated["lcmv_jammer_detected_latched"] is True
    assert activated["lcmv_jammer_protection_active"] is True

    clock[0] = 1.0
    low_start = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
    )
    assert low_start["lcmv_jammer_release_evidence_now"] is True
    assert low_start["lcmv_jammer_release_candidate_age_s"] == pytest.approx(0.0)
    assert low_start["lcmv_jammer_protection_active"] is True

    clock[0] = 2.0
    ambiguous = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(3, dtype=np.complex128),
        raw_power_metrics=None,
        cal_power_metrics=None,
    )
    assert ambiguous["lcmv_jammer_release_evidence_now"] is False
    assert ambiguous["lcmv_jammer_protection_active"] is True

    clock[0] = 3.0
    restarted = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
    )
    assert restarted["lcmv_jammer_release_candidate_age_s"] == pytest.approx(0.0)

    clock[0] = 4.0
    hysteresis_band = runtime._lcmv_jammer_activation_evidence(
        covariance=2.0 * np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 2.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 2.0},
    )
    assert hysteresis_band["lcmv_jammer_activation_evidence_now"] is False
    assert hysteresis_band["lcmv_jammer_release_evidence_now"] is False
    assert hysteresis_band["lcmv_jammer_protection_active"] is True

    clock[0] = 5.0
    runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
    )
    clock[0] = 7.1
    released = runtime._lcmv_jammer_activation_evidence(
        covariance=np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
    )
    assert released["lcmv_jammer_protection_released_now"] is True
    assert released["lcmv_jammer_protection_active"] is False
    assert released["lcmv_jammer_detected_latched"] is True

    clock[0] = 8.0
    reactivated = runtime._lcmv_jammer_activation_evidence(
        covariance=10.0 * np.eye(4, dtype=np.complex128),
        raw_power_metrics={"raw_avg_channel_power_linear": 10.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 10.0},
    )
    assert reactivated["lcmv_jammer_activation_evidence_now"] is True
    assert reactivated["lcmv_jammer_protection_active"] is True
    assert reactivated["lcmv_jammer_detected_latched"] is True


def test_release_is_evaluated_without_music_target_and_fifo_returns_uniform(
    monkeypatch,
) -> None:
    cfg = StreamConfig(
        lcmv_jammer_release_hold_s=1.0,
        lcmv_weight_transition_s=0.0,
        gnss_shared_u1_phase_transition_s=0.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    _prepare_automatic_reference(runtime)
    runtime._lcmv_jammer_detected_latched = True
    runtime._lcmv_jammer_protection_active = True
    runtime._realtime_preserve_frozen_covariance = np.eye(4, dtype=np.complex128)
    runtime._realtime_preserve_frozen_raw_power_linear = 1.0
    runtime._realtime_preserve_frozen_cal_power_linear = 1.0
    runtime._set_shared_measured_u1_protection_weights(
        np.asarray([1.0, 0.5j, -0.5, -1.0j], dtype=np.complex128)
    )
    clock = [10.0]
    monkeypatch.setattr(
        "antijamming.runtime.backend.time.monotonic",
        lambda: clock[0],
    )
    x = np.ones((4, 32), dtype=np.complex64)

    for now in (10.0, 11.1):
        clock[0] = now
        runtime._update_lcmv_test_from_music(
            x,
            float("nan"),
            float("nan"),
            raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
            cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
            covariance_matrix=np.eye(4, dtype=np.complex128),
            target_selection_source="no_peak_after_jammer_off",
        )

    status = runtime._lcmv_status_copy()
    output = runtime._gnss_shared_u1_phase_output_matrix(x)
    expected = apply_beamformer(x.astype(np.complex128), uniform_weights(4))
    assert runtime._lcmv_jammer_detected_latched is True
    assert runtime._lcmv_jammer_protection_active is False
    assert runtime._shared_measured_u1_protection_is_available() is False
    assert status["mode"] == "fallback"
    assert status["spatial_vector_diagnostics"][
        "lcmv_jammer_protection_released_now"
    ] is True
    assert np.allclose(output, np.tile(expected, (cfg.gnss_1c_channel_count, 1)))


@pytest.mark.parametrize("music_angle", [float("nan"), 150.0], ids=["no-target", "target"])
@pytest.mark.parametrize(
    "invalid_kind",
    [
        "covariance-string", "covariance-mapping", "covariance-ragged",
        "covariance-overflow", "chunk-string", "chunk-mapping",
        "chunk-nan", "covariance-shape", "eigensolver-failure",
    ],
)
def test_failed_evidence_restarts_release_hold_and_preserves_fifo(
    monkeypatch, music_angle, invalid_kind,
) -> None:
    """An invalid update cannot bridge two otherwise-low intervals."""

    cfg = StreamConfig(
        lcmv_jammer_release_hold_s=2.0,
        lcmv_weight_transition_s=0.0,
        gnss_shared_u1_phase_transition_s=0.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    _prepare_automatic_reference(runtime)
    clock = [9.0]
    monkeypatch.setattr("antijamming.runtime.backend.time.monotonic", lambda: clock[0])
    x = np.tile(np.eye(4, dtype=np.complex64), (1, 8))
    desired = runtime._realtime_preserve_frozen_vector.copy()
    runtime._shared_u1_source_satellites_cache = tuple(
        range(1, cfg.gnss_1c_channel_count + 1)
    )
    runtime._shared_u1_desired_vectors_cache = {
        f"G{prn:02d}": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 9.0,
            "tracking_sample_counter": 1,
        }
        for prn in runtime._shared_u1_source_satellites_cache
    }
    runtime._last_shared_u1_phase_status_log_ts = 0.0
    runtime._gnss_shared_u1_phase_output_matrix(x)
    runtime._lcmv_jammer_activation_evidence(
        covariance=10.0 * np.eye(4),
        raw_power_metrics={"raw_avg_channel_power_linear": 10.0},
        cal_power_metrics={"cal_avg_channel_power_linear": 10.0},
    )
    runtime._set_shared_measured_u1_protection_weights(
        np.asarray([1.0, 0.5j, -0.5, -1.0j], dtype=np.complex128)
    )
    clock[0] = 10.0

    def update(chunk=x, covariance=None):
        runtime._update_lcmv_test_from_music(
            chunk, music_angle, music_angle,
            covariance_matrix=np.eye(4) if covariance is None else covariance,
            raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
            cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
            target_selection_source="invalid_evidence_regression",
        )

    update()
    assert runtime._lcmv_jammer_release_candidate_since_monotonic_s == 10.0
    weights_before = runtime._get_shared_measured_u1_protection_weights_copy()
    fifo_before = runtime._gnss_shared_u1_phase_output_matrix(x)
    rows_before = runtime._latest_shared_u1_phase_logical_weights.copy()
    assert not np.allclose(rows_before, rows_before[:, :1])
    clock[0] = 11.0
    invalid_covariances = {
        "covariance-string": "malformed",
        "covariance-mapping": {},
        "covariance-ragged": [[1, 0], [1]],
        "covariance-overflow": [[10**1000]],
        "covariance-shape": np.eye(3),
    }
    if invalid_kind in invalid_covariances:
        update(covariance=invalid_covariances[invalid_kind])
    elif invalid_kind == "chunk-string":
        update(chunk="malformed")
    elif invalid_kind == "chunk-mapping":
        update(chunk={})
    elif invalid_kind == "chunk-nan":
        # No cached covariance: exercise the actual IQ-to-covariance producer.
        runtime._update_lcmv_test_from_music(
            np.full((4, 32), np.nan), music_angle, music_angle,
            target_selection_source="invalid_evidence_regression",
        )
    else:
        with monkeypatch.context() as failed_solver:
            def fail_eigh(*args, **kwargs):
                raise np.linalg.LinAlgError("injected covariance failure")
            failed_solver.setattr(np.linalg, "eigh", fail_eigh)
            update()

    assert runtime._lcmv_jammer_release_candidate_since_monotonic_s is None
    assert runtime._lcmv_jammer_protection_active is True
    assert runtime._lcmv_jammer_detected_latched is True
    assert runtime._shared_measured_u1_protection_is_available() is True
    np.testing.assert_array_equal(
        runtime._get_shared_measured_u1_protection_weights_copy(), weights_before,
    )
    np.testing.assert_allclose(runtime._gnss_shared_u1_phase_output_matrix(x), fifo_before)

    for now in (12.1, 14.0):
        clock[0] = now
        update()
        assert runtime._lcmv_jammer_protection_active is True
        assert runtime._lcmv_jammer_release_candidate_since_monotonic_s == 12.1
        assert runtime._shared_measured_u1_protection_is_available() is True

    clock[0] = 14.11
    update()
    assert runtime._lcmv_jammer_protection_active is False
    assert runtime._lcmv_jammer_detected_latched is True
    assert runtime._lcmv_jammer_release_candidate_since_monotonic_s is None
    assert runtime._shared_measured_u1_protection_is_available() is False
    output = runtime._gnss_shared_u1_phase_output_matrix(x)
    released_rows = runtime._latest_shared_u1_phase_logical_weights
    # Release is uniform spatially, with the existing per-PRN continuity scalar.
    np.testing.assert_allclose(released_rows, np.repeat(released_rows[:, :1], 4, axis=1))
    np.testing.assert_allclose(released_rows.conj() @ desired, rows_before.conj() @ desired)
    np.testing.assert_allclose(output, released_rows.conj() @ x, atol=1e-6)


def test_run_reset_waits_for_inflight_lcmv_update_and_clears_protection(
    monkeypatch,
) -> None:
    rng = np.random.default_rng(902)
    cfg = StreamConfig(
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
        lcmv_weight_transition_s=0.0,
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]},
            primary_internal_deg=angle,
        )
    preserve = _prime_realtime_bladerf_reference(
        runtime,
        cfg,
        internal_angle_deg=40.0,
        baseline_power_linear=0.01,
    )
    _arm_from_healthy_reference(runtime)
    null_angle = 150.0
    null_vector = steering_vector(
        np.asarray([null_angle]), cfg.center_freq_hz, cfg.array_spacing_m
    ).reshape(-1)
    desired = rng.standard_normal(512) + 1j * rng.standard_normal(512)
    jammer = rng.standard_normal(512) + 1j * rng.standard_normal(512)
    x = (
        0.5 * preserve[:, None] * desired[None, :]
        + 2.0 * null_vector[:, None] * jammer[None, :]
    )

    solver_entered = threading.Event()
    allow_solver = threading.Event()
    reset_done = threading.Event()
    errors: list[BaseException] = []
    real_solver = covariance_lcmv_vector_null_weights

    def blocked_solver(**kwargs):
        solver_entered.set()
        if not allow_solver.wait(timeout=2.0):
            raise TimeoutError("test did not release blocked LCMV solve")
        return real_solver(**kwargs)

    monkeypatch.setattr(
        "antijamming.runtime.backend.covariance_lcmv_vector_null_weights",
        blocked_solver,
    )

    def update() -> None:
        try:
            runtime._update_lcmv_test_from_music(
                x,
                null_angle,
                internal_angle_to_operator_bearing_deg(null_angle),
                raw_power_metrics={"raw_avg_channel_power_linear": 1.0},
                cal_power_metrics={"cal_avg_channel_power_linear": 1.0},
                target_selection_source="race_regression",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def reset_run() -> None:
        try:
            runtime._reset_lcmv_for_run()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            reset_done.set()

    update_thread = threading.Thread(target=update)
    reset_thread = threading.Thread(target=reset_run)
    update_thread.start()
    try:
        assert solver_entered.wait(timeout=2.0)
        reset_thread.start()
        assert reset_done.wait(timeout=0.05) is False
    finally:
        allow_solver.set()
        update_thread.join(timeout=2.0)
        if reset_thread.ident is not None:
            reset_thread.join(timeout=2.0)

    assert not update_thread.is_alive()
    assert not reset_thread.is_alive()
    assert errors == []
    assert runtime._lcmv_armed is False
    assert runtime._lcmv_jammer_detected_latched is False
    assert runtime._lcmv_jammer_protection_active is False
    assert runtime._shared_measured_u1_protection_is_available() is False
    assert runtime._lcmv_status_copy()["mode"] == "off"
    assert np.allclose(runtime._get_beamformer_target_weights_copy(), uniform_weights(4))


def test_healthy_reference_updates_during_lcmv_off_healthy_baseline() -> None:
    cfg = StreamConfig(
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
        lcmv_realtime_preserve_min_samples=3,
        lcmv_realtime_preserve_max_circular_std_deg=5.0,
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
    assert runtime._lcmv_armed is True
    assert runtime._lcmv_jammer_detected_latched is False
    assert status["mode"] == "fallback"
    assert status["spatial_vector_diagnostics"][
        "lcmv_jammer_activation_armed"
    ] is True
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


@pytest.mark.parametrize("missing", [
    "healthy_reference_updated", "pvt_healthy", "observations_healthy", "cn0_healthy",
])
def test_automatic_arming_requires_every_health_gate(missing) -> None:
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    _seed_automatic_reference(runtime)
    state = {
        "healthy_reference_updated": True,
        "pvt_healthy": True,
        "observations_healthy": True,
        "cn0_healthy": True,
    }
    state[missing] = False
    assert runtime._maybe_auto_arm_lcmv_after_pvt(state) is False
    assert runtime._lcmv_armed is False
    assert runtime._realtime_preserve_frozen_vector is None
    assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


@pytest.mark.parametrize(("field", "invalid"), [
    ("_healthy_reference_vector", None),
    ("_healthy_reference_vector", np.empty(0)),
    ("_healthy_reference_vector", np.zeros(4)),
    ("_healthy_reference_vector", np.full(4, np.nan)),
    ("_healthy_reference_vector", np.full(4, 1e308)),
    ("_healthy_reference_covariance", None),
    ("_healthy_reference_covariance", np.eye(3)),
    ("_healthy_reference_covariance", np.full((4, 4), np.nan)),
    ("_healthy_reference_updated_monotonic_s", 97.0),
    ("_healthy_reference_updated_monotonic_s", 101.0),
    ("_healthy_reference_updated_monotonic_s", None),
    ("_healthy_reference_confidence", 0.79),
    ("_healthy_reference_confidence", float("inf")),
    ("_healthy_reference_internal_angle_deg", 180.0),
    ("_realtime_preserve_stable", False),
])
def test_automatic_arming_rejects_unusable_reference(field, invalid, monkeypatch):
    monkeypatch.setattr("antijamming.runtime.backend.time.monotonic", lambda: 100.0)
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    _seed_automatic_reference(runtime)
    setattr(runtime, field, invalid)
    assert not runtime._maybe_auto_arm_lcmv_after_pvt({
        "healthy_reference_updated": True,
        "pvt_healthy": True,
        "observations_healthy": True,
        "cn0_healthy": True,
    })
    assert runtime._lcmv_armed is False
    assert runtime._realtime_preserve_frozen_vector is None
    assert not runtime._shared_measured_u1_protection_is_available()


def test_stop_request_prevents_automatic_arming():
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    _seed_automatic_reference(runtime)
    runtime._stop_requested.set()
    assert not runtime._maybe_auto_arm_lcmv_after_pvt({
        "healthy_reference_updated": True,
        "pvt_healthy": True,
        "observations_healthy": True,
        "cn0_healthy": True,
    })
    assert runtime._lcmv_armed is False


def test_automatic_arming_once_per_run_owns_reference_and_reset_relearns():
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    for _ in range(3):
        _seed_automatic_reference(runtime)
        vector = runtime._healthy_reference_vector
        covariance = runtime._healthy_reference_covariance
        _arm_from_healthy_reference(runtime)
        frozen = runtime._realtime_preserve_frozen_vector.copy()
        assert not np.shares_memory(vector, runtime._realtime_preserve_frozen_vector)
        assert not np.shares_memory(
            covariance, runtime._realtime_preserve_frozen_covariance
        )
        vector[:] = 0
        covariance[:] = 0
        assert np.array_equal(runtime._realtime_preserve_frozen_vector, frozen)
        runtime._lcmv_jammer_detected_latched = True
        runtime._lcmv_jammer_protection_active = True
        assert not runtime._maybe_auto_arm_lcmv_after_pvt({
            "healthy_reference_updated": True,
            "pvt_healthy": True,
            "observations_healthy": True,
            "cn0_healthy": True,
        })
        assert runtime._lcmv_jammer_protection_active
        runtime._reset_lcmv_for_run()
        assert not runtime._lcmv_armed
        assert not runtime._lcmv_jammer_detected_latched
        assert not runtime._lcmv_jammer_protection_active
        assert runtime._healthy_reference_vector is None
        assert runtime._healthy_reference_covariance is None
        assert runtime._realtime_preserve_frozen_vector is None
        assert runtime._realtime_preserve_frozen_covariance is None
        assert not runtime._realtime_preserve_stable
        assert not runtime._shared_measured_u1_protection_is_available()
        assert np.allclose(runtime._get_beamformer_weights_copy(), uniform_weights(4))


def test_automatic_arming_is_serialized_and_publishes_only_once(monkeypatch):
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    _seed_automatic_reference(runtime)
    entered = threading.Event()
    release = threading.Event()
    events = []
    results = []
    errors = []

    def record(event, **kwargs):
        events.append(event)
        entered.set()
        assert release.wait(2.0)

    monkeypatch.setattr(runtime, "_record_runtime_event", record)

    def arm():
        try:
            results.append(runtime._maybe_auto_arm_lcmv_after_pvt({
                "healthy_reference_updated": True,
                "pvt_healthy": True,
                "observations_healthy": True,
                "cn0_healthy": True,
            }))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=arm)
    second = threading.Thread(target=arm)
    first.start()
    try:
        assert entered.wait(2.0)
        second.start()
    finally:
        release.set()
        first.join(2.0)
        if second.ident is not None:
            second.join(2.0)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert sorted(results) == [False, True]
    assert events == ["lcmv_on"]


def test_automatic_arm_status_survives_headless_wire_serialization():
    runtime = BackendRuntime(StreamConfig(), _build_loggers())
    _prepare_automatic_reference(runtime)
    wire = metrics_for_wire({"lcmv_test": runtime._lcmv_status_copy()})
    received = json.loads(json.dumps(wire, allow_nan=False))["lcmv_test"]
    assert received["enabled"] is True
    assert received["mode"] == "fallback"
    assert received["active_lcmv_method"] == "uniform_array_sum"
    assert received["spatial_vector_diagnostics"]["lcmv_jammer_activation_armed"]
    assert not received["spatial_vector_diagnostics"]["lcmv_jammer_protection_active"]


def test_automatic_live_contract_arms_releases_and_reactivates_fifo(monkeypatch):
    """Synthetic health/IQ exercise the runtime, solver and actual FIFO bank."""

    clock = [100.0]
    monkeypatch.setattr("antijamming.runtime.backend.time.monotonic", lambda: clock[0])
    cfg = StreamConfig(
        phase_correction_vector=None,
        lcmv_realtime_preserve_min_samples=3,
        lcmv_weight_transition_s=0.0,
        gnss_shared_u1_phase_transition_s=0.0,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _install_product_fifo_bank(runtime)
    runtime._gnss_bridge = SimpleNamespace(snapshot=lambda: {
        "pvt_current": True, "pvt_gui_status": "FIX",
        "pvt_observation_count": 10, "avg_tracking_cno_db_hz": 42.0,
    })
    rng = np.random.default_rng(20260914)
    desired = steering_vector(
        np.asarray([40.0]), cfg.center_freq_hz, cfg.array_spacing_m
    ).reshape(-1)
    jammer = steering_vector(
        np.asarray([150.0]), cfg.center_freq_hz, cfg.array_spacing_m
    ).reshape(-1)
    healthy = (
        0.2 * desired[:, None]
        * (rng.standard_normal(2048) + 1j * rng.standard_normal(2048))
        + 0.01 * (rng.standard_normal((4, 2048)) + 1j * rng.standard_normal((4, 2048)))
    )
    interfered = healthy + 2.0 * jammer[:, None] * (
        rng.standard_normal(2048) + 1j * rng.standard_normal(2048)
    )
    for angle in (39.0, 40.0, 41.0):
        runtime._update_realtime_bladerf_angle_tracker(
            {"doa_peaks": [{"angle_deg": angle}]}, primary_internal_deg=angle
        )
    payload = runtime._update_healthy_reference_tracking_from_music(
        corrected_chunk=healthy,
        music_internal_deg=40.0,
        music_bearing_deg=internal_angle_to_operator_bearing_deg(40.0),
    )
    assert payload["lcmv_auto_arm_triggered"]
    assert runtime._lcmv_armed and not runtime._lcmv_jammer_protection_active
    frozen = runtime._realtime_preserve_frozen_vector.copy()
    for cycle in range(2):
        clock[0] = 101.0 + cycle * 5.0
        runtime._update_lcmv_test_from_music(
            interfered, 150.0, internal_angle_to_operator_bearing_deg(150.0),
            target_selection_source="strongest_music_peak_outside_frozen_bladerf_guard",
        )
        assert runtime._lcmv_status_copy()["mode"] == "on"
        assert runtime._lcmv_jammer_protection_active
        assert runtime._shared_measured_u1_protection_is_available()
        protected = runtime._gnss_shared_u1_phase_output_matrix(
            interfered.astype(np.complex64)
        )
        expected = apply_beamformer(
            interfered.astype(np.complex64),
            runtime._get_shared_measured_u1_protection_weights_copy(),
        )
        # Production does complex64 matrix multiplication; the reference helper
        # accumulates in complex128. Bound float32 rounding, including near zero.
        np.testing.assert_allclose(
            protected, np.tile(expected, (cfg.gnss_1c_channel_count, 1)),
            atol=2e-6, rtol=1e-5,
        )
        for delta in (1.0, 3.1):
            clock[0] = 101.0 + cycle * 5.0 + delta
            runtime._update_lcmv_test_from_music(
                healthy, float("nan"), float("nan"),
                target_selection_source="no_peak_after_jammer_off",
            )
            runtime._gnss_shared_u1_phase_output_matrix(healthy.astype(np.complex64))
        assert runtime._lcmv_armed
        assert runtime._lcmv_jammer_detected_latched
        assert not runtime._lcmv_jammer_protection_active
        assert not runtime._shared_measured_u1_protection_is_available()
        assert np.array_equal(runtime._realtime_preserve_frozen_vector, frozen)
        recovered = runtime._gnss_shared_u1_phase_output_matrix(
            healthy.astype(np.complex64)
        )
        expected = apply_beamformer(healthy.astype(np.complex64), uniform_weights(4))
        np.testing.assert_allclose(
            recovered, np.tile(expected, (cfg.gnss_1c_channel_count, 1)),
            atol=2e-6, rtol=1e-5,
        )



def test_healthy_reference_does_not_treat_music_local_peaks_as_emitters() -> None:
    cfg = StreamConfig(
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
        phase_correction_vector=None,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    _prepare_automatic_reference(runtime)
    original_reference = runtime._healthy_reference_vector.copy()
    runtime._set_lcmv_status(enabled=True, mode="on", reason="unit test active null")
    runtime._gnss_bridge = SimpleNamespace(
        snapshot=lambda: {
            "pvt_current": True,
            "pvt_gui_status": "FIX",
            "pvt_observation_count": 10,
            "avg_tracking_cno_db_hz": 38.0,
        }
    )
    u1 = original_reference.copy()
    x = np.tile(u1[:, None], (1, 64))

    payload = runtime._update_healthy_reference_from_chunk(
        corrected_chunk=x,
        music_internal_deg=40.0,
        music_bearing_deg=internal_angle_to_operator_bearing_deg(40.0),
        u1=u1,
    )

    assert payload["healthy_reference_updated"] is False
    assert payload["healthy_reference_update_allowed"] is False
    assert payload["lcmv_safe_baseline"] is False
    assert "lcmv_active_or_not_safe" in str(payload["healthy_reference_freeze_reason"])
    assert "lcmv_active_or_not_safe" in payload["healthy_reference_freeze_reasons"]
    assert payload["run_state_label"] == "lcmv_on_no_jammer"
    assert np.array_equal(runtime._healthy_reference_vector, original_reference)


def test_healthy_reference_does_not_treat_no_fix_as_fix() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
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
