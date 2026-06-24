from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pytest

from antijamming.dsp.beamforming import (
    apply_beamformer,
    dominant_spatial_signature,
    lcmv_weights,
    uniform_weights,
)
from antijamming.dsp.doa.music import steering_vector
from antijamming.dsp.phase import apply_phase_calibration
from antijamming.config import StreamConfig
from antijamming.runtime import BackendRuntime
from antijamming.runtime import BeamformingWorkItem
from antijamming.runtime import StreamWorker


def _build_loggers() -> dict[str, logging.Logger]:
    keys = [
        "app",
        "hw",
        "stream",
        "transport",
        "phase",
        "doa",
        "jammer",
        "lcmv",
        "gnss",
        "health",
        "errors",
    ]
    return {k: logging.getLogger(f"test.bf.{k}") for k in keys}


def test_uniform_weights_sum_to_one() -> None:
    weights = uniform_weights(4)
    assert weights.shape == (4,)
    assert np.isclose(np.sum(weights), 1.0 + 0.0j)


def test_lcmv_weights_drive_single_beamformed_stream() -> None:
    rng = np.random.default_rng(7)
    x = (
        rng.standard_normal((4, 256)) + 1j * rng.standard_normal((4, 256))
    ).astype(np.complex128)
    weights = lcmv_weights(
        x=x,
        rf_freq_hz=1.57542e9,
        null_theta_deg=45.0,
        uca_radius_m=0.0669,
    )
    y = apply_beamformer(x, weights)

    assert weights.shape == (4,)
    assert y.shape == (256,)
    assert y.dtype == np.complex64
    assert np.all(np.isfinite(y.real))
    assert np.all(np.isfinite(y.imag))


def test_reference_preserving_lcmv_keeps_ref_channel_and_nulls_jammer() -> None:
    rng = np.random.default_rng(8)
    x = (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    ).astype(np.complex128)
    null_theta = 45.0
    weights = lcmv_weights(
        x=x,
        rf_freq_hz=1.57542e9,
        null_theta_deg=null_theta,
        uca_radius_m=0.0669,
        ref_channel=0,
    )
    jammer_sv = steering_vector(
        np.array([null_theta], dtype=np.float64),
        1.57542e9,
        4,
        0.0669,
    ).reshape(-1)

    assert weights.shape == (4,)
    assert np.isclose(weights[0], 1.0 + 0.0j, atol=1e-6)
    assert np.isclose(np.vdot(weights, jammer_sv), 0.0 + 0.0j, atol=1e-5)


def test_data_driven_lcmv_null_suppresses_mismatched_jammer_signature() -> None:
    rng = np.random.default_rng(9)
    jammer_signature = np.array(
        [1.0 + 0.0j, -0.55 + 0.72j, 0.34 - 0.91j, -0.88 - 0.25j],
        dtype=np.complex128,
    )
    jammer_signature *= np.sqrt(4.0) / np.linalg.norm(jammer_signature)
    jammer = rng.standard_normal(4096) + 1j * rng.standard_normal(4096)
    noise = 1e-4 * (
        rng.standard_normal((4, jammer.size)) + 1j * rng.standard_normal((4, jammer.size))
    )
    x = jammer_signature[:, None] * jammer[None, :] + noise

    null_signature = dominant_spatial_signature(x)
    weights = lcmv_weights(
        x=x,
        rf_freq_hz=1.57542e9,
        null_theta_deg=45.0,
        uca_radius_m=0.0669,
        ref_channel=0,
        null_vector=null_signature,
    )
    y = apply_beamformer(x, weights)
    input_power = np.mean(np.abs(x) ** 2)
    output_power = np.mean(np.abs(y) ** 2)
    suppression_db = 10.0 * np.log10(input_power / output_power)

    assert np.isclose(weights[0], 1.0 + 0.0j, atol=1e-6)
    assert suppression_db > 60.0


def test_lcmv_standby_still_reports_candidate_null_pattern() -> None:
    rng = np.random.default_rng(11)
    cfg = StreamConfig(
        lcmv_force_null=False,
        doa_points=181,
        dsp_update_interval_s=0.02,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    x = (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    ).astype(np.complex128)

    runtime._running = True
    thread = threading.Thread(target=runtime._lcmv_loop, daemon=True)
    thread.start()
    runtime._lcmv_queue.put(BeamformingWorkItem(calibrated_chunk=x, doa_deg=87.0))
    pattern = np.zeros((0,), dtype=np.float64)
    active = True
    for _ in range(100):
        with runtime._results_lock:
            pattern = np.array(runtime._latest_lcmv_db, copy=True)
            active = bool(runtime._latest_lcmv_null_active)
        if pattern.size == cfg.doa_points and np.nanmin(pattern) < -1.0:
            break
        time.sleep(0.01)
    runtime._running = False
    runtime._lcmv_queue.put(None)
    thread.join(timeout=1.0)

    assert active is False
    assert pattern.shape == (cfg.doa_points,)
    assert np.nanmin(pattern) < -1.0


def test_lcmv_no_jammer_keeps_gnss_on_uniform_calibrated_sum() -> None:
    rng = np.random.default_rng(15)
    cfg = StreamConfig(
        lcmv_force_null=False,
        doa_points=181,
        dsp_update_interval_s=0.02,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    x = (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    ).astype(np.complex128)

    runtime._running = True
    thread = threading.Thread(target=runtime._lcmv_loop, daemon=True)
    thread.start()
    runtime._lcmv_queue.put(BeamformingWorkItem(calibrated_chunk=x, doa_deg=87.0))
    expected = uniform_weights(len(cfg.channels))
    weights = np.zeros((0,), dtype=np.complex128)
    active = True
    for _ in range(100):
        with runtime._beamformer_lock:
            weights = np.array(runtime._latest_beamformer_weights, copy=True)
        with runtime._results_lock:
            active = bool(runtime._latest_lcmv_null_active)
        if weights.shape == expected.shape and np.allclose(weights, expected, atol=1e-10):
            break
        time.sleep(0.01)
    runtime._running = False
    runtime._lcmv_queue.put(None)
    thread.join(timeout=1.0)

    assert active is False
    assert np.allclose(weights, expected, atol=1e-10)


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


def test_jammer_detection_latches_null_after_transient_alarm() -> None:
    cfg = StreamConfig()
    runtime = BackendRuntime(cfg, _build_loggers())
    detected_status = {
        "detected": True,
        "state": "detected",
        "reason": "test detection",
        "doa_deg": 87.0,
    }

    active, doa = runtime._jammer_mitigation_state(detected_status, 87.0)

    assert active is True
    assert doa == 87.0
    assert detected_status["mitigation_active"] is True

    quiet_status = {
        "detected": False,
        "state": "not_detected",
        "reason": "below threshold",
        "doa_deg": 12.0,
    }
    active, doa = runtime._jammer_mitigation_state(quiet_status, 12.0)

    assert active is True
    assert doa == 87.0
    assert quiet_status["state"] == "suspected"
    assert quiet_status["mitigation_reason"] == "hold_after_detection"


def test_lcmv_applies_null_when_jammer_is_detected() -> None:
    rng = np.random.default_rng(12)
    cfg = StreamConfig(
        lcmv_force_null=False,
        doa_points=181,
        dsp_update_interval_s=0.02,
    )
    runtime = BackendRuntime(cfg, _build_loggers())
    x = (
        rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512))
    ).astype(np.complex128)

    runtime._running = True
    thread = threading.Thread(target=runtime._lcmv_loop, daemon=True)
    thread.start()
    runtime._lcmv_queue.put(
        BeamformingWorkItem(
            calibrated_chunk=x,
            doa_deg=87.0,
            jammer_detected=True,
        )
    )
    pattern = np.zeros((0,), dtype=np.float64)
    active = False
    for _ in range(100):
        with runtime._results_lock:
            pattern = np.array(runtime._latest_lcmv_db, copy=True)
            active = bool(runtime._latest_lcmv_null_active)
        if active and pattern.size == cfg.doa_points and np.nanmin(pattern) < -1.0:
            break
        time.sleep(0.01)
    runtime._running = False
    runtime._lcmv_queue.put(None)
    thread.join(timeout=1.0)

    assert active is True
    assert pattern.shape == (cfg.doa_points,)
    assert np.nanmin(pattern) < -1.0


def test_worker_gnss_output_beamforming_uses_selected_weights() -> None:
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
    selected_weights = np.array([0 + 0j, 1 + 0j, 0 + 0j, 0 + 0j], dtype=np.complex128)
    worker._backend._set_beamformer_weights(selected_weights)
    with worker._backend._results_lock:
        worker._backend._latest_lcmv_null_active = True

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        selected_weights,
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_worker_gnss_output_static_calibration_uses_fast_weighted_sum_when_null_active() -> None:
    correction = np.array([1 + 0j, 0 - 1j, -1 + 0j, 0 + 1j], dtype=np.complex128)
    weights = np.array([0.25 + 0j, 0.25 + 0j, 0.25 + 0j, 0.25 + 0j], dtype=np.complex128)
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
    worker._backend._set_beamformer_weights(weights)
    with worker._backend._results_lock:
        worker._backend._latest_lcmv_null_active = True

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(
            x.astype(np.complex128),
            ref_channel=cfg.phase_ref_channel,
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


def test_worker_gnss_output_uses_uniform_beamformer_when_no_null_is_active() -> None:
    cfg = StreamConfig(
        phase_correction_vector=None,
    )
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
    worker._backend._set_beamformer_weights(uniform_weights(len(cfg.channels)))
    with worker._backend._results_lock:
        worker._backend._latest_lcmv_null_active = False

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        uniform_weights(len(cfg.channels)),
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_worker_gnss_output_static_calibration_stays_beamformed_without_null() -> None:
    correction = np.array([1 + 0j, 0 - 1j, -1 + 0j, 0 + 1j], dtype=np.complex128)
    weights = np.array([0 + 0j, 1 + 0j, 0 + 0j, 0 + 0j], dtype=np.complex128)
    cfg = StreamConfig(
        phase_correction_vector=tuple(correction),
    )
    worker = StreamWorker(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [0 + 9j, 0 + 8j],
            [3 + 0j, 4 + 0j],
            [0 - 1j, 0 - 2j],
        ],
        dtype=np.complex64,
    )
    worker._backend._set_beamformer_weights(weights)
    with worker._backend._results_lock:
        worker._backend._latest_lcmv_null_active = False

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(
            x.astype(np.complex128),
            ref_channel=cfg.phase_ref_channel,
            correction_vector=correction,
        ),
        weights,
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_worker_gnss_output_uses_uniform_beamformer_by_default() -> None:
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
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        uniform_weights(len(cfg.channels)),
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


def test_worker_gnss_output_uses_selected_beamformer_weights() -> None:
    cfg = StreamConfig(phase_correction_vector=None)
    worker = StreamWorker(cfg, _build_loggers())
    x = np.array(
        [
            [1 + 0j, 2 + 0j],
            [7 + 0j, 6 + 0j],
            [0 + 0j, 0 + 0j],
            [0 + 0j, 0 + 0j],
        ],
        dtype=np.complex64,
    )
    selected_weights = np.array([0 + 0j, 1 + 0j, 0 + 0j, 0 + 0j], dtype=np.complex128)
    worker._backend._set_beamformer_weights(selected_weights)
    with worker._backend._results_lock:
        worker._backend._latest_lcmv_null_active = True

    y = worker._backend._gnss_output_vector(x)
    expected = apply_beamformer(
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        selected_weights,
    )

    assert np.allclose(y, expected, atol=1e-5)


def test_backend_gnss_handoff_label_is_beamformed() -> None:
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
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        uniform_weights(len(cfg.channels)),
    )

    assert runtime._gnss_handoff_mode_label() == "beamformed_continuous"
    assert np.allclose(y, expected, atol=1e-5)
