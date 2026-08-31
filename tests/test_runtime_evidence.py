from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np

from antijamming.logging import (
    finalize_session_logs,
    record_event,
    reset_session_logs,
    setup_logging,
)
from antijamming.config import StreamConfig
from antijamming.dsp.pipeline import compute_doa_metrics
from antijamming.runtime.backend import BackendRuntime
from tools.audit_live_session import audit_session
from tools.summarize_lcmv_run import parse_json_line


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_session_finalization_retains_root_logs_and_dual_event_ledger(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    session = reset_session_logs(tmp_path, loggers)
    loggers["app"].info("evidence row")

    payload = record_event(
        tmp_path,
        "jammer_on",
        source="test",
        session_id=session.session_id,
        session_elapsed_s=session.elapsed_s(),
        context={"pvt_current": True},
    )
    finalize_session_logs(
        tmp_path,
        session,
        loggers,
        stop_reason="unit test",
    )

    assert payload["timestamp_utc"]
    assert (tmp_path / "CURRENT_SESSION").exists() is False
    assert (tmp_path / "LATEST_SESSION").read_text().strip() == str(session.session_dir)
    assert "evidence row" in (session.session_dir / "app.log").read_text()
    assert _read_jsonl(tmp_path / "operator_events.log")[0]["event"] == "jammer_on"
    archived = _read_jsonl(session.session_dir / "operator_events.jsonl")
    assert archived[0]["context"] == {"pvt_current": True}
    assert "attenuation_db" not in archived[0]
    assert "bladeRF_gain_db" not in archived[0]
    manifest = json.loads((session.session_dir / "session_manifest.json").read_text())
    assert manifest["finalized"] is True
    assert manifest["stop_reason"] == "unit test"


def test_disabled_logging_builds_event_payload_without_writing_files(tmp_path) -> None:
    payload = record_event(
        tmp_path,
        "jammer_on",
        source="test",
        persist=False,
    )

    assert payload["event"] == "jammer_on"
    assert list(tmp_path.iterdir()) == []


def test_disabled_logging_skips_context_serialization_and_timing_bookkeeping(
    tmp_path,
    monkeypatch,
) -> None:
    loggers = setup_logging(tmp_path, enabled=False)
    backend = BackendRuntime(
        StreamConfig(log_dir=tmp_path, logging_enabled=False),
        loggers,
    )
    monkeypatch.setattr(
        backend,
        "_event_context_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled logging must not build evidence context")
        ),
    )

    payload = backend._record_runtime_event("notes", source="test")
    backend._maybe_log_runtime_evidence({"pvt_current": True})
    backend._record_runtime_timing("synthetic", 0.25)

    assert payload["event"] == "notes"
    assert "context" not in payload
    assert backend._perf_stats == {}
    assert list(tmp_path.iterdir()) == []


def test_finalization_does_not_overwrite_exact_pid_scoped_gnss_artifacts(
    tmp_path: Path,
) -> None:
    loggers = setup_logging(tmp_path)
    session = reset_session_logs(tmp_path, loggers)
    stale = tmp_path / "gnss-sdr/runtime/fifo_gps_l1.conf"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale-stable-path", encoding="utf-8")
    exact = session.session_dir / "gnss-sdr/runtime/fifo_gps_l1.conf"
    exact.parent.mkdir(parents=True, exist_ok=True)
    exact.write_text("exact-pid-scoped", encoding="utf-8")

    finalize_session_logs(
        tmp_path,
        session,
        loggers,
        stop_reason="unit test",
    )

    assert exact.read_text(encoding="utf-8") == "exact-pid-scoped"


def test_event_without_owned_session_does_not_enter_current_run(tmp_path) -> None:
    session = tmp_path / "runs" / "owned-by-live-backend"
    session.mkdir(parents=True)
    (tmp_path / "CURRENT_SESSION").write_text(str(session), encoding="utf-8")

    payload = record_event(
        tmp_path,
        "lcmv_on",
        source="unit_fixture",
        append_current_session=False,
    )

    assert payload["event"] == "lcmv_on"
    assert (tmp_path / "operator_events.log").exists()
    assert not (session / "operator_events.jsonl").exists()


def test_next_start_recovers_an_unfinalized_previous_session(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    previous = reset_session_logs(tmp_path, loggers)
    loggers["app"].info("row before unclean termination")

    current = reset_session_logs(tmp_path, loggers)

    previous_manifest = json.loads(
        (previous.session_dir / "session_manifest.json").read_text()
    )
    assert previous_manifest["outcome"] == "recovered_after_unclean_end"
    assert "row before unclean termination" in (
        previous.session_dir / "app.log"
    ).read_text()
    assert (tmp_path / "CURRENT_SESSION").read_text().strip() == str(current.session_dir)


def test_json_ready_mapping_preserves_boolean_types() -> None:
    result = BackendRuntime._json_ready_mapping(
        {"enabled": True, "nested": {"locked": False}, "flags": [True, False]}
    )

    assert result == {
        "enabled": True,
        "nested": {"locked": False},
        "flags": [True, False],
    }


def test_automatic_runtime_evidence_records_weights_inference_and_gnss(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    cfg = StreamConfig(log_dir=tmp_path, gain_db=45.0)
    backend = BackendRuntime(cfg, loggers)
    backend._log_session = reset_session_logs(tmp_path, loggers)
    backend._running = True
    backend._stream_start_ts = time.monotonic()

    backend._maybe_log_runtime_evidence(
        {
            "pvt_output_seen": True,
            "pvt_current": True,
            "pvt_observation_count": 8,
            "tracking_count": 8,
            "tracking_satellites": ["G01", "G03"],
            "stable_tracking_satellites": ["G01", "G03"],
            "used_in_fix_count": 8,
            "used_in_fix_satellites": ["G01", "G03"],
            "avg_tracking_cno_db_hz": 44.0,
        }
    )
    for handler in loggers["runtime_evidence"].handlers:
        handler.flush()

    payloads = [
        payload
        for line in (tmp_path / "runtime_evidence.jsonl").read_text().splitlines()
        if (payload := parse_json_line(line)) is not None
    ]
    snapshot = next(
        payload
        for payload in payloads
        if payload["event"] == "automatic_runtime_evidence_snapshot"
    )
    context = snapshot["context"]

    assert context["configured_receiver"]["usrp_rx_gain_db"] == 45.0
    assert context["gnss"]["pvt_current"] is True
    assert context["physical_state_inference"]["jammer_inferred_state"] == "unknown"
    assert context["beamformer"]["uniform_logical_weights"]["real"] == [1.0] * 4
    assert context["beamformer"]["current_logical_weights"]["real"] == [1.0] * 4
    assert context["beamformer"]["weight_transition_active"] is False
    assert "weight_transition" not in context
    assert any(
        payload["event"] == "automatic_runtime_state_transition"
        for payload in payloads
    )


def test_runtime_evidence_compacts_derived_power_and_pvt_aliases() -> None:
    powers = BackendRuntime._runtime_power_evidence(
        {
            "raw_ch0_power_linear": 1.0,
            "raw_ch0_peak_component": 0.25,
            "raw_ch0_dc_power_linear": 0.01,
            "raw_channel_powers_linear": [1.0, 2.0, 3.0, 4.0],
        },
        {"cal_power_spread_db": 0.2},
        {
            "fifo_output_power_linear": 2.5,
            "fifo_output_source": "shared",
            "lcmv_weights": {"real": [1.0] * 4},
        },
    )
    accuracy = BackendRuntime._runtime_accuracy_evidence(
        {
            "fix_count": 100,
            "lat_deg": 37.0,
            "cep50_m": 1.2,
            "pvt_solution": {"monitor_pvt": {"duplicated": True}},
        }
    )

    assert powers["raw_ch0_power_linear"] == 1.0
    assert powers["raw_ch0_peak_component"] == 0.25
    assert powers["fifo_output_power_linear"] == 2.5
    assert "raw_ch0_dc_power_linear" not in powers
    assert "lcmv_weights" not in powers
    assert accuracy == {"fix_count": 100, "lat_deg": 37.0, "cep50_m": 1.2}


def test_spatial_diagnostics_are_not_duplicated_into_analysis_log(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    backend = BackendRuntime(StreamConfig(log_dir=tmp_path), loggers)

    backend._log_spatial_vector_diagnostics(
        {"event": "spatial_vector_diagnostics", "sequence": 7}
    )
    for logger in loggers.values():
        for handler in logger.handlers:
            handler.flush()

    assert '"sequence":7' in (
        tmp_path / "spatial_vector_diagnostics.jsonl"
    ).read_text(encoding="utf-8")
    assert "spatial_vector_diagnostics" not in (
        tmp_path / "analysis.log"
    ).read_text(encoding="utf-8")


def test_full_angle_log_keeps_one_copy_of_each_spectrum(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    cfg = StreamConfig(log_dir=tmp_path)
    backend = BackendRuntime(cfg, loggers)
    scan = np.linspace(0.0, 330.0, 12)
    samples = np.ones((4, 128), dtype=np.complex128)
    backend._scan_angles_deg = scan
    doa = compute_doa_metrics(
        samples,
        cfg.center_freq_hz,
        scan,
        cfg.array_spacing_m,
        n_sources=1,
    )
    common_response = np.linspace(0.0, -30.0, scan.size)
    fifo_response = np.linspace(-3.0, -18.0, scan.size)
    backend._set_lcmv_status(
        enabled=True,
        mode="on",
        lcmv_response_db=common_response,
        gnss_fifo_measured_u1_model_response_db=fifo_response,
        gnss_fifo_measured_u1_model_summary={"minimum_db": -18.0},
        gnss_fifo_measured_u1_protection_available=True,
    )

    backend._log_full_angle_analysis(
        doa_metrics=doa,
        raw_spec=doa["doa_raw_spectrum"],
        doa_internal_deg=float(doa["doa_deg"]),
        doa_display_deg=backend._internal_angle_to_display(doa["doa_deg"]),
        calibrated_chunk=samples,
    )
    for handler in loggers["analysis"].handlers:
        handler.flush()
    record = parse_json_line((tmp_path / "analysis.log").read_text())

    assert record is not None
    assert record["schema_version"] == 4
    assert len(record["music_spectrum_linear"]) == scan.size
    assert len(record["bartlett_spectrum_linear"]) == scan.size
    assert "music_raw_spectrum" not in record
    assert "music_raw_display_sorted" not in record
    assert "bartlett_raw_spectrum" not in record
    assert "scan_display_sorted_deg" not in record
    assert np.allclose(record["lcmv"]["lcmv_model_response_db"], common_response)
    assert np.allclose(
        record["lcmv"]["gnss_fifo_measured_u1_model_response_db"],
        fifo_response,
    )
    assert record["lcmv"]["gnss_fifo_measured_u1_protection_available"] is True
    assert record["lcmv"]["common_measured_u1_target_response_scope"] == (
        "ideal_steering_scan_not_measured_ota_null_depth"
    )
    assert record["lcmv"]["gnss_fifo_measured_u1_response_scope"] == (
        "ideal_steering_scan_of_shared_weights_not_measured_ota_null"
    )
    assert "common_model_music_comparison" in record["lcmv"]
    assert "null_match" not in record["lcmv"]


def test_session_audit_reports_scenario_and_carrier_continuity(tmp_path) -> None:
    session = tmp_path / "runs" / "synthetic"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text(
        json.dumps({"session_id": "synthetic"}), encoding="utf-8"
    )
    base_ns = 2_000_000_000_000_000_000
    names = (
        "bladeRF_on",
        "jammer_off",
        "lcmv_on",
        "jammer_on",
        "jammer_off",
        "lcmv_off",
        "lcmv_on",
        "jammer_on",
    )
    events = [
        {
            "event": name,
            "timestamp_utc": "2033-05-18T03:33:20+00:00",
            "wall_time_unix_ns": base_ns + index * 1_000_000_000,
            "session_elapsed_s": float(index),
        }
        for index, name in enumerate(names)
    ]
    (session / "operator_events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    tracking = []
    for index in range(-4, 19):
        epoch_ns = base_ns + index * 500_000_000
        tracking.append(
            {
                "wall_time_unix_ns": epoch_ns,
                "satellite_id": "G01",
                "carrier_phase_rads": 0.2 * index,
                "tracking_sample_counter": (index + 4) * 2_000_000,
                "fs": 4_000_000,
                "cn0_db_hz": 45.0,
                "cycle_slip": False,
            }
        )
    (session / "tracking_observables.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tracking), encoding="utf-8"
    )
    runtime_context = {
        "lcmv_enabled": True,
        "lcmv_mode": "active",
        "physical_state_inference": {"jammer_inferred_state": "likely_present"},
        "beamformer": {
            "uniform_logical_weights": {"real": [1.0] * 4},
            "current_logical_weights": {"real": [0.5] * 4},
            "target_logical_weights": {"real": [0.4] * 4},
            "effective_gnss_multiply_coefficients_conj_w_times_calibration": {
                "real": [0.5] * 4
            },
        },
        "gnss": {"pvt_current": True},
    }
    runtime_row = {
        "event": "automatic_runtime_evidence_snapshot",
        "timestamp_utc": "2033-05-18T03:33:20+00:00",
        "wall_time_unix_ns": base_ns,
        "context": runtime_context,
    }
    (session / "runtime_evidence.jsonl").write_text(
        "2033-05-18 08:33:20,000 | INFO | " + json.dumps(runtime_row) + "\n",
        encoding="utf-8",
    )

    report = audit_session(session)

    assert report["all_required_core_scenarios_covered"] is True
    assert report["carrier_phase_archive_available"] is True
    assert report["automatic_runtime_evidence_record_count"] == 1
    assert report["automatic_evidence"]["snapshot_count"] == 1
    assert report["automatic_evidence"][
        "all_snapshots_have_complete_weight_state"
    ] is True
    assert len(report["transitions"]) == len(names)
    jammer_on = report["transitions"][3]
    assert jammer_on["tracking"]["continuous_satellite_fraction"] == 1.0
    assert jammer_on["tracking"]["cycle_slip_detected_after"] is False
    assert jammer_on["tracking"]["max_abs_carrier_phase_residual_rads"] < 1e-9
