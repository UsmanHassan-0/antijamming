from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from antijamming.config import StreamConfig
from antijamming.gnss import GnssSdrBridge, SharedU1PhaseCompensationBank


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


def test_shared_u1_phase_module_has_no_independent_covariance_solver() -> None:
    source = Path(
        "src/antijamming/gnss/shared_u1_phase_compensation.py"
    ).read_text(encoding="utf-8")
    assert "covariance_lcmv_vector_null_weights" not in source
    assert "per_satellite_covariance_lcmv_candidate" not in source
    assert "independent_per_prn_lcmv\": True" not in source
