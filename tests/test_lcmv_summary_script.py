from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from tools.summarize_lcmv_run import (
    estimate_jammer_only_suppression,
    operator_marked_rf_budget,
)


def test_summarize_lcmv_run_parses_small_fake_logs(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "\n".join(
            [
                "2026-07-04 05:44:13,000 | INFO | USRP stream started: addr=192.168.30.2",
                "2026-07-04 05:45:00,000 | INFO | UI action: lcmv_test_enabled=True",
                "2026-07-04 05:47:02,000 | INFO | UI action: lcmv_test_enabled=False",
                "2026-07-04 05:49:45,000 | INFO | USRP stream stopped (reason=normal stop)",
            ]
        ),
        encoding="utf-8",
    )
    (logs / "doa.log").write_text(
        "2026-07-04 05:44:14,000 | INFO | doa method=music nsrc=1 "
        "doa_deg_internal=350.00 doa_display_deg=100.00 peak_count=2 "
        "noise_tail_spread_db=4.20 noise_tail_flatness_db=0.70 "
        "noise_tail_white_like=0 "
        "source_est_gap=2 effective_rank=1.50\n",
        encoding="utf-8",
    )
    (logs / "gnss_handoff.log").write_text(
        "2026-07-04 05:44:15,000 | INFO | runtime->GNSS snapshot: mode=lcmv_test_nulling_continuous "
        "measured_output_reduction_vs_uniform_db=3.10 "
        "measured_output_reduction_vs_raw_avg_channel_db=1.00 "
        "raw_power_spread_db=4.20 cal_power_spread_db=3.50 used_pvt=G01,G02 "
        "pvt_gui_status=FIX pvt_observations=14 avg_cno_db_hz=38.00\n",
        encoding="utf-8",
    )
    spatial_payload = {
        "event": "spatial_vector_diagnostics",
        "sequence": 1,
        "music_internal_angle_deg": 350.0,
        "sample_count": 1024,
        "active_lcmv_method": "covariance_lcmv_ideal",
        "active_lcmv_null_method": "covariance_lcmv_ideal",
        "candidate_methods_valid": [
            "covariance_lcmv_ideal",
            "covariance_lcmv_measured_u1",
        ],
        "candidate_methods_rejected": {},
        "run_state_label": "jammer_like_event",
        "candidate_covariance_lcmv_measured_u1_valid": False,
        "candidate_covariance_lcmv_measured_u1_rejected_reason": "desired_loss_db 125.00 exceeds 6.00",
        "candidate_covariance_lcmv_measured_u1_u1_component_reduction_vs_reference_db": 120.0,
        "candidate_covariance_lcmv_measured_u1_ideal_component_reduction_vs_reference_db": 5.0,
        "candidate_covariance_lcmv_measured_u1_total_output_reduction_vs_reference_db": 9.0,
        "candidate_covariance_lcmv_measured_u1_desired_loss_vs_reference_db": 125.0,
        "candidate_covariance_lcmv_measured_u1_white_noise_gain_db": 7.2,
        "candidate_covariance_lcmv_measured_u1_noise_gain_vs_reference_db": 10.0,
        "candidate_covariance_lcmv_measured_u1_effective_js_improvement_u1_db": -5.0,
        "candidate_covariance_lcmv_measured_u1_effective_receiver_improvement_u1_db": -15.0,
        "candidate_covariance_lcmv_ideal_valid": True,
        "candidate_covariance_lcmv_ideal_u1_component_reduction_vs_reference_db": 55.0,
        "candidate_covariance_lcmv_ideal_ideal_component_reduction_vs_reference_db": 50.0,
        "candidate_covariance_lcmv_ideal_total_output_reduction_vs_reference_db": 7.0,
        "candidate_covariance_lcmv_ideal_desired_loss_vs_reference_db": 4.0,
        "candidate_covariance_lcmv_ideal_white_noise_gain_db": 1.0,
        "candidate_covariance_lcmv_ideal_noise_gain_vs_reference_db": 1.0,
        "candidate_covariance_lcmv_ideal_effective_js_improvement_u1_db": 51.0,
        "candidate_covariance_lcmv_ideal_effective_receiver_improvement_u1_db": 50.0,
        "ideal_measured_coherence_abs": 0.91,
        "ideal_measured_principal_angle_deg": 24.5,
        "ideal_measured_mismatch_db": -7.4,
        "measured_covariance_reduction_uniform_to_ideal_lcmv_db": 3.0,
        "measured_covariance_reduction_uniform_to_u1_lcmv_db": 9.0,
        "predicted_u1_lcmv_output_gain_over_ideal_lcmv_db": 6.0,
        "ideal_lcmv_to_u1_suppression_db": 4.0,
        "u1_lcmv_to_u1_suppression_db": 120.0,
    }
    spatial_line = json.dumps(spatial_payload, separators=(",", ":"))
    (logs / "analysis.log").write_text(
        '2026-07-04 05:44:16,000 | INFO | {"event":"lcmv_model_response_absolute",'
        '"model_response_at_selected_null_db":-120.0,'
        '"output_metrics":{"measured_output_reduction_vs_uniform_db":3.2}}\n'
        f"2026-07-04 05:44:17,000 | INFO | {spatial_line}\n",
        encoding="utf-8",
    )
    (logs / "spatial_vector_diagnostics.jsonl").write_text(
        f"2026-07-04 05:44:17,000 | INFO | {spatial_line}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "tools/summarize_lcmv_run.py", "--logs", str(logs)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "LCMV intervals" in result.stdout
    assert "measured_output_reduction_vs_uniform_db" in result.stdout
    assert "noise_tail_spread_db" in result.stdout
    assert "noise_tail_white_like distribution" in result.stdout
    assert "Diagnostic coverage" in result.stdout
    assert "Spatial vector diagnostics" in result.stdout
    assert "ideal_measured_coherence_abs" in result.stdout
    assert "R is not jammer-only; u1 is not always jammer" in result.stdout
    assert "candidate_methods_valid distribution" in result.stdout
    assert "candidate_metrics_by_method" in result.stdout
    assert "best_by_dominant_vector_suppression: covariance_lcmv_measured_u1" in result.stdout
    assert "best_by_total_output_reduction: covariance_lcmv_measured_u1" in result.stdout
    assert "best_by_effective_receiver_improvement: covariance_lcmv_ideal" in result.stdout
    assert "active_method_was_best_by_effective_receiver_improvement: True" in result.stdout
    assert "run_state_label distribution" in result.stdout
    assert "measured-u1 candidates need desired-loss validation" in result.stdout
    assert "pre-toggle LCMV OFF" in result.stdout
    assert "LCMV ON" in result.stdout
    assert "LCMV OFF" in result.stdout
    assert "No explicit operator event markers found." in result.stdout
    assert "Files" in result.stdout


def test_summarize_lcmv_run_reads_operator_events(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "operator_events.log").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-04T05:45:00+00:00",
                "event": "jammer_on",
                "attenuation_db": 50.0,
                "bladeRF_gain_db": None,
                "notes": "bench switch",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "tools/summarize_lcmv_run.py", "--logs", str(logs)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "event=jammer_on" in result.stdout
    assert "attenuation_db=50.0" in result.stdout


def test_mark_rf_event_appends_jsonl_marker(tmp_path) -> None:
    logs = tmp_path / "logs"

    subprocess.run(
        [
            sys.executable,
            "tools/mark_rf_event.py",
            "--logs",
            str(logs),
            "--event",
            "jammer_on",
            "--attenuation-db",
            "50",
            "--notes",
            "unit test",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads((logs / "operator_events.log").read_text(encoding="utf-8"))
    assert payload["event"] == "jammer_on"
    assert payload["attenuation_db"] == 50.0
    assert payload["notes"] == "unit test"


def test_jammer_only_estimate_requires_and_uses_marked_windows() -> None:
    start = datetime(2026, 7, 4, 5, 44, 0)
    operator_events = [
        (start, {"event": "jammer_off"}),
        (start + timedelta(seconds=2), {"event": "jammer_on"}),
    ]
    spatial_events = [
        (
            start + timedelta(seconds=1),
            {
                "active_method_applied": "uniform_array_sum",
                "active_total_output_power_from_R": 10.0,
            },
        ),
        (
            start + timedelta(seconds=3),
            {
                "active_method_applied": "uniform_array_sum",
                "active_total_output_power_from_R": 30.0,
            },
        ),
        (
            start + timedelta(seconds=4),
            {
                "active_method_applied": "covariance_lcmv_ideal",
                "active_total_output_power_from_R": 8.0,
                "active_healthy_baseline_output_power_from_R": 3.0,
            },
        ),
    ]

    estimate = estimate_jammer_only_suppression(operator_events, spatial_events)

    assert estimate["available"] is True
    assert estimate["jammer_before_power_linear"] == pytest.approx(20.0)
    assert estimate["jammer_after_power_linear"] == pytest.approx(5.0)
    assert estimate["suppression_db"] == pytest.approx(10.0 * math.log10(4.0))


def test_jammer_only_estimate_prefers_same_covariance_runtime_samples() -> None:
    start = datetime(2026, 7, 4, 5, 44, 0)
    operator_events = [
        (start, {"event": "jammer_off"}),
        (start + timedelta(seconds=2), {"event": "jammer_on"}),
        (start + timedelta(seconds=5), {"event": "jammer_off"}),
    ]
    spatial_events = [
        (
            start + timedelta(seconds=1),
            {
                "jammer_only_suppression_estimate_available": True,
                "jammer_only_suppression_db": 99.0,
                "jammer_only_power_before_uniform_linear": 100.0,
                "jammer_only_power_after_applied_linear": 1.0,
            },
        ),
        (
            start + timedelta(seconds=3),
            {
                "jammer_only_suppression_estimate_available": True,
                "jammer_only_suppression_db": 10.0,
                "jammer_only_power_before_uniform_linear": 10.0,
                "jammer_only_power_after_applied_linear": 1.0,
            },
        ),
        (
            start + timedelta(seconds=4),
            {
                "jammer_only_suppression_estimate_available": True,
                "jammer_only_suppression_db": 20.0,
                "jammer_only_power_before_uniform_linear": 100.0,
                "jammer_only_power_after_applied_linear": 1.0,
            },
        ),
        (
            start + timedelta(seconds=6),
            {
                "jammer_only_suppression_estimate_available": True,
                "jammer_only_suppression_db": 88.0,
                "jammer_only_power_before_uniform_linear": 100.0,
                "jammer_only_power_after_applied_linear": 1.0,
            },
        ),
    ]

    estimate = estimate_jammer_only_suppression(operator_events, spatial_events)

    assert estimate["available"] is True
    assert estimate["method"].startswith("same-covariance")
    assert estimate["sample_count"] == 2
    assert estimate["suppression_db"] == pytest.approx(10.0 * math.log10(55.0))
    assert estimate["mean_per_snapshot_suppression_db"] == pytest.approx(15.0)
    assert estimate["jammer_before_power_linear"] == pytest.approx(55.0)
    assert estimate["jammer_after_power_linear"] == pytest.approx(1.0)


def test_operator_marked_rf_budget_uses_explicit_attenuation() -> None:
    start = datetime(2026, 7, 4, 5, 44, 0)
    manifest = {
        "center_freq_hz": 1_575_420_000.0,
        "jammer_distance_m": 4.2672,
        "bladeRF_distance_m": 3.4798,
        "jammer_l1_4mhz_avg_dbm": 9.51,
        "jammer_peak_dbm": 21.4,
        "bladeRF_tx_power_dbm_est": -42.2691,
        "jammer_tx_antenna_gain_dbi": 2.0,
        "bladeRF_tx_antenna_gain_dbi": 2.0,
        "rx_antenna_gain_dbi": 5.0,
        "jammer_tx_cable_loss_db": 0.0,
        "bladeRF_tx_cable_loss_db": 0.0,
        "pre_bpf_cable_loss_db": 1.0,
        "bpf_l1_loss_db": 2.0,
        "lna_gain_db": 50.0,
        "lna_input_p1db_dbm": -30.2,
        "dc_block_loss_db": 0.5,
        "post_lna_cable_loss_db": 1.0,
        "twinrx_max_rf_input_dbm": 10.0,
    }
    events = [
        (
            start,
            {"event": "jammer_on", "attenuation_db": 30.0},
        )
    ]

    budget, basis = operator_marked_rf_budget(manifest, events)

    assert "explicit jammer_on marker" in basis
    assert budget["jammer_avg_usrp_rf_input_ideal_dbm"] == pytest.approx(
        -16.989, abs=0.002
    )
    assert budget["jammer_peak_usrp_above_twinrx_max"] is False
