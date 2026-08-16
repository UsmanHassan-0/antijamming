from __future__ import annotations

import json
import logging
from pathlib import Path
import time

from antijamming.logging import (
    finalize_session_logs,
    record_event,
    reset_session_logs,
    setup_logging,
)
from antijamming.config import StreamConfig
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
        attenuation_db=50.0,
        bladeRF_gain_db=50.0,
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
    manifest = json.loads((session.session_dir / "session_manifest.json").read_text())
    assert manifest["finalized"] is True
    assert manifest["stop_reason"] == "unit test"


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
    assert any(
        payload["event"] == "automatic_runtime_state_transition"
        for payload in payloads
    )


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
