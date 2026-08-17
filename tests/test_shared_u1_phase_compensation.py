from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from antijamming.config import StreamConfig
from antijamming.gnss import (
    GnssSdrBridge,
    SharedU1DesiredVectorMonitor,
    SharedU1PhaseCompensationBank,
    apply_shared_phase_fanout,
)
from antijamming.gnss.shared_u1_phase_compensation import (
    gps_l1_ca_code,
    phase_invariant_coherence,
)
from antijamming.logging import setup_logging
from antijamming.runtime.backend import BackendRuntime


def _loggers() -> dict[str, logging.Logger]:
    return {
        name: logging.getLogger(f"test.shared_u1_phase.{name}")
        for name in ("app", "gnss", "errors")
    }


def test_shared_u1_phase_rows_preserve_each_prn_response_and_one_null() -> None:
    common = np.ones((4,), dtype=np.complex128)
    # One shared spatial solution with a null toward [1,1,1,1].
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired_g03 = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    desired_g04 = np.asarray([0.2j, 1.0, -0.4j, 0.7], dtype=np.complex128)
    now = 100.0
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_g03,
            "updated_monotonic": now,
            "tracking_sample_counter": 1000,
        },
        "G04": {
            "desired_spatial_vector": desired_g04,
            "updated_monotonic": now,
            "tracking_sample_counter": 1000,
        },
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(3, 4),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )

    before, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=now,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=now + 0.01,
    )

    jammer = np.ones((4,), dtype=np.complex128)
    for row_index, (satellite, desired) in enumerate(
        (("G03", desired_g03), ("G04", desired_g04))
    ):
        row = applied[row_index]
        # Only one scalar may differ from the shared solution.
        ratios = row / shared
        assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
        # That scalar preserves the complete old complex response.
        assert np.allclose(
            np.vdot(row, desired),
            np.vdot(before[row_index], desired),
            rtol=1e-12,
            atol=1e-12,
        )
        # A scalar cannot move or fill the shared spatial null.
        assert abs(np.vdot(row, jammer)) < 1e-12
        assert status[satellite]["phase_compensation_applied"] is True
        assert status[satellite]["independent_per_prn_lcmv"] is False
        assert status[satellite]["continuity_residual_abs"] < 1e-12


def test_shared_u1_phase_renderer_pins_each_prn_to_one_synchronized_fifo(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(3, 4, 7, 8),
        gnss_sdr_runtime_dir=tmp_path / "runtime",
        gnss_sdr_log_dir=tmp_path / "glog",
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    rendered = bridge._render_config()

    assert "GNSS-SDR.num_sources=4" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=true" in rendered
    for index, prn in enumerate((3, 4, 7, 8)):
        assert f"SignalSource{index}.filename=" in rendered
        assert f"gnss_iq_G{prn:02d}.fifo" in rendered
        assert f"Channel{index}.satellite={prn}" in rendered
        assert f"Channel{index}.RF_channel_ID={index}" in rendered


def test_shared_u1_phase_renderer_uses_dynamic_channel_slots_without_pinned_prns(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(),
        gnss_1c_channel_count=4,
        gnss_channels_in_acquisition=4,
        gnss_sdr_runtime_dir=tmp_path / "runtime",
        gnss_sdr_log_dir=tmp_path / "glog",
    )
    bridge = GnssSdrBridge(cfg, _loggers())
    rendered = bridge._render_config()

    assert "GNSS-SDR.num_sources=4" in rendered
    assert "GNSS-SDR.synchronize_signal_sources=true" in rendered
    assert ".satellite=" not in rendered
    for index in range(4):
        assert f"SignalSource{index}.filename=" in rendered
        assert f"gnss_iq_channel_{index:02d}.fifo" in rendered
        assert f"SignalConditioner{index}.implementation=Signal_Conditioner" in rendered
        assert f"Channel{index}.signal=1C" in rendered
        assert f"Channel{index}.RF_channel_ID={index}" in rendered


def test_dynamic_source_reassignment_discards_old_prn_phase_state() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired_g03 = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    desired_g10 = np.asarray([0.1j, 1.0, -0.4j, 0.7], dtype=np.complex128)
    vectors = {
        "G03": {
            "desired_spatial_vector": desired_g03,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        },
        "G10": {
            "desired_spatial_vector": desired_g10,
            "updated_monotonic": 10.02,
            "tracking_sample_counter": 2000,
        },
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(),
        source_count=2,
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )

    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(3, 4),
        enabled_now=False,
        now_monotonic=10.0,
    )
    old_rows, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(3, 4),
        enabled_now=True,
        now_monotonic=10.01,
    )
    reassigned_rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        source_satellites=(10, 4),
        enabled_now=True,
        now_monotonic=10.02,
    )

    assert not np.allclose(old_rows[0], common)
    assert np.allclose(reassigned_rows[0], common)
    assert status["G10"]["source_index"] == 0
    assert status["G10"]["desired_vector_available"] is False
    assert "G03" not in status


def test_phase_compensation_above_six_db_is_allowed_when_weight_norm_is_safe() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([0.45, 0.0, 0.0, 0.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.complex128)
    vectors = {
        "G04": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(4,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
        max_weight_norm=8.0,
    )

    before, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )

    assert status["G04"]["amplitude_compensation_db"] > 6.0
    assert status["G04"]["phase_compensation_applied"] is True
    assert status["G04"]["phase_compensation_guard_reason"] is None
    assert np.linalg.norm(applied[0]) < 8.0
    assert np.allclose(
        np.vdot(applied[0], desired),
        np.vdot(before[0], desired),
        rtol=1e-12,
        atol=1e-12,
    )


def test_phase_compensation_still_rejects_unsafe_weight_norm() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([0.01, 1.0, 1.0, 1.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.complex128)
    vectors = {
        "G04": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(4,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
        max_weight_norm=8.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    applied, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )

    assert np.allclose(applied[0], common)
    assert status["G04"]["phase_compensation_applied"] is False
    assert status["G04"]["phase_compensation_guard_reason"] == (
        "phase_compensated_weight_norm_exceeded"
    )


def test_shared_u1_phase_runtime_labels_transport_and_actual_fifo_state(
    tmp_path: Path,
) -> None:
    cfg = StreamConfig(
        log_dir=tmp_path,
        gnss_shared_u1_phase_compensation_enabled=True,
        gnss_shared_u1_phase_satellites=(3, 4, 7, 8),
    )
    runtime = BackendRuntime(cfg, setup_logging(tmp_path))

    assert runtime._gnss_handoff_mode_label() == (
        "shared_prn_phase_continuity_fanout"
    )
    assert runtime._fifo_output_source_label() == (
        "shared_uniform_phase_reference_prn_fanout"
    )


def test_shared_u1_phase_waits_for_covariance_worker_without_missing_onset() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([1.0, -1.0, 1.0, -1.0], dtype=np.complex128)
    desired = np.asarray([1.0, 0.5j, 0.3, -0.2j], dtype=np.complex128)
    vectors = {
        "G03": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 10.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(3,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=20_000,
        transition_s=1.0,
    )
    bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=10.0,
    )
    waiting, waiting_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.01,
    )
    applied, applied_status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=10.02,
    )

    assert np.allclose(waiting[0], common)
    assert waiting_status["G03"]["shared_measured_u1_available"] is False
    assert applied_status["G03"]["phase_compensation_applied"] is True
    assert np.allclose(
        np.vdot(applied[0], desired),
        np.vdot(waiting[0], desired),
        rtol=1e-12,
        atol=1e-12,
    )


def test_prn_monitor_recovers_calibrated_spatial_iq_from_one_code_period(
    tmp_path: Path,
) -> None:
    """Code despreading must retain channel amplitude/phase, not carrier phase."""

    fs = 1_023_000.0
    prn = 3
    code = gps_l1_ca_code(prn).astype(np.complex64)
    spatial = np.asarray([1.0, 0.6 + 0.4j, -0.3 + 0.8j, 0.7 - 0.2j])
    correction = np.asarray([1.0, 1.0j, 0.5 - 0.2j, -0.8 + 0.1j])
    common_carrier_phase = np.exp(0.73j)
    raw = np.zeros((4, 1031), dtype=np.complex64)
    raw[:, 5:1028] = (
        spatial[:, None] * common_carrier_phase * code[None, :]
    )
    monitor = SharedU1DesiredVectorMonitor(
        sample_rate_hz=fs,
        channel_count=4,
        phase_correction_vector=tuple(correction),
        tracking_snapshot=lambda: {},
        session_dir=tmp_path,
        session_id="synthetic",
        logger=logging.getLogger("test.shared_u1_phase.monitor"),
        satellites=(prn,),
        min_quality_measurements=1,
    )
    monitor._append_span(
        -5,
        raw,
        np.ones((1, 4), dtype=np.complex128),
    )

    measurement = monitor._correlate(
        {"carrier_phase_rads": 123_456.75},
        "G03",
        prn,
        1023,
        0.0,
        45.0,
        0,
    )
    recovered = monitor.desired_vectors_snapshot()["G03"]

    assert measurement is not None
    assert measurement["quality_pass"] is True
    assert measurement["prompt_vs_wrong_code_db"] > 40.0
    assert measurement["reported_carrier_phase_rads"] == 123_456.75
    assert recovered["quality_pass_count"] == 1
    assert phase_invariant_coherence(
        recovered["desired_spatial_vector"],
        spatial * correction,
    ) > 1.0 - 1e-12


def test_shared_u1_phase_rephases_every_later_covariance_update() -> None:
    common = np.ones((4,), dtype=np.complex128)
    first = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    second = np.asarray([0.7 - 0.1j, 0.1 - 0.4j, 1.2 + 0.1j, 0.6 + 0.3j])
    desired = np.asarray([1.0, 0.8 + 0.1j, 0.5 - 0.2j, 0.4 + 0.3j])
    desired /= np.linalg.norm(desired)
    vectors = {
        "G05": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 20.0,
            "tracking_sample_counter": 1000,
        }
    }
    bank = SharedU1PhaseCompensationBank(
        satellites=(5,),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=32_768,
        transition_s=1.0,
    )
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=common,
        shared_measured_u1_available=False,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=20.0,
    )
    first_rows, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=first,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=20.01,
    )
    second_rows, status = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=second,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=21.01,
    )

    assert not np.allclose(first_rows, second_rows)
    np.testing.assert_allclose(
        np.vdot(second_rows[0], desired),
        np.vdot(baseline[0], desired),
        rtol=1e-12,
        atol=1e-12,
    )
    ratios = second_rows[0] / second
    assert np.allclose(ratios, ratios[0], rtol=1e-12, atol=1e-12)
    assert status["G05"]["continuity_residual_abs"] < 1e-12


def test_jammer_off_ramp_preserves_complex_response_at_every_chunk() -> None:
    common = np.ones((4,), dtype=np.complex128)
    shared = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    desired = np.asarray([1.0, 0.8 + 0.1j, 0.5 - 0.2j, 0.4 + 0.3j])
    desired /= np.linalg.norm(desired)
    vectors = {
        "G05": {
            "desired_spatial_vector": desired,
            "updated_monotonic": 20.0,
            "tracking_sample_counter": 1000,
        }
    }
    # Ten samples per chunk and a one-second transition produce ten exact
    # intermediate points, making every ramp step observable in this test.
    bank = SharedU1PhaseCompensationBank(
        satellites=(5,),
        channel_count=4,
        sample_rate_hz=100.0,
        samples_per_chunk=10,
        transition_s=1.0,
    )
    baseline, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=False,
        now_monotonic=20.0,
    )
    nulled, _ = bank.advance(
        shared_common_weights=common,
        shared_measured_u1_weights=shared,
        shared_measured_u1_available=True,
        desired_vectors=vectors,
        enabled_now=True,
        now_monotonic=20.01,
    )
    preserved_response = np.vdot(nulled[0], desired)
    assert not np.allclose(nulled, baseline)

    ramp_rows: list[np.ndarray] = []
    for chunk_index in range(10):
        rows, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=shared,
            shared_measured_u1_available=True,
            desired_vectors={},
            enabled_now=False,
            now_monotonic=21.0 + chunk_index / 10.0,
        )
        ramp_rows.append(np.array(rows[0], copy=True))
        np.testing.assert_allclose(
            np.vdot(rows[0], desired),
            preserved_response,
            rtol=1e-12,
            atol=1e-12,
        )
        assert status["G05"]["independent_per_prn_lcmv"] is False

    assert not np.allclose(ramp_rows[0], ramp_rows[-1])
    np.testing.assert_allclose(ramp_rows[-1], baseline[0], rtol=1e-12, atol=1e-12)


def test_shared_u1_phase_can_skip_per_chunk_status_allocation() -> None:
    bank = SharedU1PhaseCompensationBank(
        satellites=(3, 4, 7, 8),
        channel_count=4,
        sample_rate_hz=4_000_000.0,
        samples_per_chunk=32_768,
        transition_s=1.0,
    )
    rows, status = bank.advance(
        shared_common_weights=np.ones(4, dtype=np.complex128),
        shared_measured_u1_weights=np.ones(4, dtype=np.complex128),
        shared_measured_u1_available=False,
        desired_vectors={},
        enabled_now=False,
        now_monotonic=1.0,
        emit_status=False,
    )

    assert rows.shape == (4, 4)
    assert status == {}


def test_scalar_fanout_matches_general_four_channel_matrix() -> None:
    rng = np.random.default_rng(42)
    raw = (
        rng.standard_normal((4, 4096))
        + 1j * rng.standard_normal((4, 4096))
    ).astype(np.complex64)
    shared = np.asarray([0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j])
    scales = np.exp(1j * np.linspace(-0.8, 0.9, 10)) * np.linspace(0.7, 1.3, 10)
    rows = scales[:, None] * shared[None, :]
    correction = np.asarray([1.0, -0.9 + 0.2j, 0.7 - 0.4j, 0.3 - 1.0j])

    actual, fast_path = apply_shared_phase_fanout(raw, rows, correction)
    effective = np.asarray(
        np.conj(rows) * correction.astype(np.complex64)[None, :],
        dtype=np.complex64,
    )
    expected = np.ascontiguousarray(effective @ raw, dtype=np.complex64)

    assert fast_path is True
    np.testing.assert_allclose(actual, expected, rtol=5e-6, atol=2e-6)


def test_noncollinear_transition_rows_use_general_matrix() -> None:
    rng = np.random.default_rng(43)
    raw = (
        rng.standard_normal((4, 256))
        + 1j * rng.standard_normal((4, 256))
    ).astype(np.complex64)
    rows = np.asarray(
        [
            [1.0, 1.0, 1.0, 1.0],
            [0.8 + 0.1j, 0.2 - 0.3j, 1.1 + 0.2j, 0.5 + 0.4j],
        ],
        dtype=np.complex128,
    )
    correction = np.ones(4, dtype=np.complex64)

    actual, fast_path = apply_shared_phase_fanout(raw, rows, correction)
    expected = np.asarray(np.conj(rows).astype(np.complex64) @ raw, dtype=np.complex64)

    assert fast_path is False
    np.testing.assert_array_equal(actual, expected)


def test_shared_u1_phase_module_has_no_independent_covariance_solver() -> None:
    source = Path(
        "src/antijamming/gnss/shared_u1_phase_compensation.py"
    ).read_text(encoding="utf-8")
    assert "covariance_lcmv_vector_null_weights" not in source
    assert "per_satellite_covariance_lcmv_candidate" not in source
    assert "independent_per_prn_lcmv\": True" not in source
