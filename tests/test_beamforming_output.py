from __future__ import annotations

import logging

import numpy as np
import pytest

from antijamming.config import StreamConfig
from antijamming.dsp.beamforming import apply_beamformer, uniform_weights
from antijamming.dsp.phase import apply_phase_calibration
from antijamming.runtime import BackendRuntime, StreamWorker


def _build_loggers() -> dict[str, logging.Logger]:
    keys = [
        "app",
        "hw",
        "stream",
        "transport",
        "phase",
        "doa",
        "jammer",
        "gnss",
        "health",
        "errors",
    ]
    return {k: logging.getLogger(f"test.bf.{k}") for k in keys}


def test_uniform_weights_sum_to_one() -> None:
    weights = uniform_weights(4)
    assert weights.shape == (4,)
    assert np.isclose(np.sum(weights), 1.0 + 0.0j)


def test_uniform_weights_drive_single_combined_stream() -> None:
    rng = np.random.default_rng(7)
    x = (
        rng.standard_normal((4, 256)) + 1j * rng.standard_normal((4, 256))
    ).astype(np.complex128)
    weights = uniform_weights(4)
    y = apply_beamformer(x, weights)

    assert y.shape == (256,)
    assert y.dtype == np.complex64
    assert np.allclose(y, np.asarray(np.mean(x, axis=0), dtype=np.complex64))


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
        apply_phase_calibration(x.astype(np.complex128), ref_channel=cfg.phase_ref_channel),
        uniform_weights(len(cfg.channels)),
    )

    assert y.dtype == np.complex64
    assert np.allclose(y, expected, atol=1e-5)


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


def test_backend_gnss_handoff_label_is_uniform_array_combiner() -> None:
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

    assert runtime._gnss_handoff_mode_label() == "uniform_array_combined"
    assert np.allclose(y, expected, atol=1e-5)
