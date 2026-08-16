#!/usr/bin/env python3
"""Audit one marked live anti-jamming session, including carrier continuity."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
from typing import Iterable

try:
    from tools.summarize_lcmv_run import (
        estimate_jammer_only_suppression,
        parse_operator_events,
        parse_spatial_events,
        parse_timeline,
    )
except ImportError:  # Direct ``python tools/audit_live_session.py`` execution.
    from summarize_lcmv_run import (
        estimate_jammer_only_suppression,
        parse_operator_events,
        parse_spatial_events,
        parse_timeline,
    )


TRANSITION_EVENTS = {
    "jammer_on",
    "jammer_off",
    "jammer_moved",
    "bladeRF_on",
    "bladeRF_off",
    "lcmv_on",
    "lcmv_off",
}


def _iso_to_epoch_s(value: object) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def resolve_session(logs: Path, session: Path | None = None) -> Path:
    if session is not None:
        resolved = session.expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(f"session directory does not exist: {resolved}")
        return resolved
    root = logs.expanduser().resolve()
    for name in ("CURRENT_SESSION", "LATEST_SESSION"):
        pointer = root / name
        try:
            candidate = Path(pointer.read_text(encoding="utf-8").strip()).resolve()
        except OSError:
            continue
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"no CURRENT_SESSION or LATEST_SESSION pointer exists under {root}"
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                # Runtime JSONL loggers use the common timestamp/level prefix.
                # The JSON payload is the final field after `` | ``.
                if " | " not in text:
                    continue
                try:
                    row = json.loads(text.rsplit(" | ", 1)[-1].strip())
                except json.JSONDecodeError:
                    continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_events(session: Path) -> list[dict[str, object]]:
    path = session / "operator_events.jsonl"
    if not path.is_file():
        path = session / "operator_events.log"
    events = read_jsonl(path)
    for event in events:
        epoch = event.get("wall_time_unix_ns")
        if isinstance(epoch, int):
            event["_epoch_s"] = epoch / 1e9
        else:
            event["_epoch_s"] = _iso_to_epoch_s(
                event.get("timestamp_utc", event.get("timestamp"))
            )
    return sorted(
        (event for event in events if isinstance(event.get("_epoch_s"), (int, float))),
        key=lambda event: float(event["_epoch_s"]),
    )


def load_tracking(session: Path) -> list[dict[str, object]]:
    rows = read_jsonl(session / "tracking_observables.jsonl")
    for row in rows:
        epoch = row.get("wall_time_unix_ns")
        if isinstance(epoch, int):
            row["_epoch_s"] = epoch / 1e9
        elif isinstance(row.get("wall_time_unix_s"), (int, float)):
            row["_epoch_s"] = float(row["wall_time_unix_s"])
        else:
            row["_epoch_s"] = _iso_to_epoch_s(row.get("timestamp_utc"))
    return sorted(
        (row for row in rows if isinstance(row.get("_epoch_s"), (int, float))),
        key=lambda row: float(row["_epoch_s"]),
    )


def load_runtime_evidence(session: Path) -> list[dict[str, object]]:
    rows = read_jsonl(session / "runtime_evidence.jsonl")
    for row in rows:
        epoch = row.get("wall_time_unix_ns")
        if isinstance(epoch, int):
            row["_epoch_s"] = epoch / 1e9
        else:
            row["_epoch_s"] = _iso_to_epoch_s(row.get("timestamp_utc"))
    return sorted(
        (row for row in rows if isinstance(row.get("_epoch_s"), (int, float))),
        key=lambda row: float(row["_epoch_s"]),
    )


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: Iterable[object]) -> float | None:
    finite = [number for value in values if (number := _finite(value)) is not None]
    return statistics.fmean(finite) if finite else None


def _wrap_rads(value: float) -> float:
    return (float(value) + math.pi) % (2.0 * math.pi) - math.pi


def _phase_residuals_at_event(
    records: list[dict[str, object]],
    event_time_s: float,
    *,
    pre_s: float,
    post_s: float,
) -> dict[str, object]:
    by_satellite: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in records:
        timestamp = float(row["_epoch_s"])
        if (event_time_s - pre_s) <= timestamp <= (event_time_s + post_s):
            by_satellite[str(row.get("satellite_id", row.get("prn", "--")))].append(row)

    residuals: dict[str, float] = {}
    phase_gaps: dict[str, float] = {}
    time_basis: dict[str, str] = {}
    for satellite, rows in by_satellite.items():
        before = [row for row in rows if float(row["_epoch_s"]) < event_time_s]
        after = [row for row in rows if float(row["_epoch_s"]) >= event_time_s]
        if len(before) < 2 or not after:
            continue
        first, second = before[-2], before[-1]
        third = after[0]
        p0 = _finite(first.get("carrier_phase_rads"))
        p1 = _finite(second.get("carrier_phase_rads"))
        p2 = _finite(third.get("carrier_phase_rads"))
        sample_times: list[float] = []
        for row in (first, second, third):
            sample_counter = _finite(row.get("tracking_sample_counter"))
            sample_rate = _finite(row.get("fs"))
            if sample_counter is None or sample_rate is None or sample_rate <= 0.0:
                sample_times = []
                break
            sample_times.append(sample_counter / sample_rate)
        if len(sample_times) == 3:
            t0, t1, t2 = sample_times
            time_basis[satellite] = "tracking_sample_counter"
        else:
            # Compatibility fallback for older archives that predate sample
            # counters. UDP receive timestamps contain scheduler jitter and are
            # therefore weaker carrier-phase evidence.
            t0 = float(first["_epoch_s"])
            t1 = float(second["_epoch_s"])
            t2 = float(third["_epoch_s"])
            time_basis[satellite] = "udp_wall_time_fallback"
        if p0 is None or p1 is None or p2 is None or t1 <= t0 or t2 < t1:
            continue
        local_rate = (p1 - p0) / (t1 - t0)
        predicted = p1 + local_rate * (t2 - t1)
        residuals[satellite] = _wrap_rads(p2 - predicted)
        phase_gaps[satellite] = t2 - t1

    return {
        "carrier_phase_residual_rads_by_satellite": residuals,
        "carrier_phase_sample_gap_s_by_satellite": phase_gaps,
        "carrier_phase_time_basis_by_satellite": time_basis,
        "carrier_phase_residual_satellite_count": len(residuals),
        "max_abs_carrier_phase_residual_rads": (
            max(abs(value) for value in residuals.values()) if residuals else None
        ),
    }


def summarize_tracking_transition(
    records: list[dict[str, object]],
    event: dict[str, object],
    *,
    pre_s: float = 2.0,
    post_s: float = 3.0,
) -> dict[str, object]:
    event_time = float(event["_epoch_s"])
    before = [
        row
        for row in records
        if (event_time - pre_s) <= float(row["_epoch_s"]) < event_time
    ]
    after = [
        row
        for row in records
        if event_time <= float(row["_epoch_s"]) <= (event_time + post_s)
    ]
    sats_before = sorted({str(row.get("satellite_id", row.get("prn"))) for row in before})
    sats_after = sorted({str(row.get("satellite_id", row.get("prn"))) for row in after})
    common = sorted(set(sats_before) & set(sats_after))
    slips = sorted(
        {
            str(row.get("satellite_id", row.get("prn")))
            for row in after
            if bool(row.get("cycle_slip", False))
        }
    )
    cno_before = _mean(row.get("cn0_db_hz") for row in before)
    cno_after = _mean(row.get("cn0_db_hz") for row in after)
    summary: dict[str, object] = {
        "window_before_s": pre_s,
        "window_after_s": post_s,
        "satellites_before": sats_before,
        "satellites_after": sats_after,
        "continuous_satellites": common,
        "continuous_satellite_fraction": (
            len(common) / len(sats_before) if sats_before else None
        ),
        "cycle_slip_satellites_after": slips,
        "cycle_slip_detected_after": bool(slips),
        "mean_cno_before_db_hz": cno_before,
        "mean_cno_after_db_hz": cno_after,
        "mean_cno_change_db": (
            cno_after - cno_before
            if cno_before is not None and cno_after is not None
            else None
        ),
        "tracking_evidence_available": bool(before and after),
    }
    summary.update(
        _phase_residuals_at_event(
            records,
            event_time,
            pre_s=pre_s,
            post_s=post_s,
        )
    )
    return summary


def scenario_coverage(events: list[dict[str, object]]) -> dict[str, bool]:
    state = {"jammer": "unknown", "bladeRF": "unknown", "lcmv": "off"}
    coverage = {
        "bladeRF_baseline_lcmv_off_jammer_off": False,
        "lcmv_on_bladeRF_on_jammer_off": False,
        "jammer_on_during_lcmv": False,
        "jammer_off_during_lcmv": False,
        "lcmv_off_after_jammer_cycle": False,
        "lcmv_reenabled_with_bladeRF_on_jammer_off": False,
        "jammer_reenabled_after_lcmv_rearm": False,
        "bladeRF_on_while_lcmv_on": False,
        "jammer_moved_while_lcmv_on": False,
    }
    jammer_cycle_seen = False
    lcmv_on_count = 0
    second_lcmv_on_epoch: float | None = None
    for payload in events:
        event = str(payload.get("event", ""))
        if event == "jammer_on":
            state["jammer"] = "on"
            if state["lcmv"] == "on":
                coverage["jammer_on_during_lcmv"] = True
                if second_lcmv_on_epoch is not None and float(payload["_epoch_s"]) >= second_lcmv_on_epoch:
                    coverage["jammer_reenabled_after_lcmv_rearm"] = True
        elif event == "jammer_off":
            state["jammer"] = "off"
            if state["lcmv"] == "on":
                coverage["jammer_off_during_lcmv"] = True
                if coverage["jammer_on_during_lcmv"]:
                    jammer_cycle_seen = True
        elif event == "bladeRF_on":
            state["bladeRF"] = "on"
            if state["lcmv"] == "on":
                coverage["bladeRF_on_while_lcmv_on"] = True
        elif event == "bladeRF_off":
            state["bladeRF"] = "off"
        elif event == "lcmv_on":
            state["lcmv"] = "on"
            lcmv_on_count += 1
            if lcmv_on_count >= 2:
                second_lcmv_on_epoch = float(payload["_epoch_s"])
        elif event == "lcmv_off":
            state["lcmv"] = "off"
            if jammer_cycle_seen:
                coverage["lcmv_off_after_jammer_cycle"] = True
        elif event == "jammer_moved" and state["lcmv"] == "on":
            coverage["jammer_moved_while_lcmv_on"] = True

        baseline = (
            state["bladeRF"] == "on"
            and state["jammer"] == "off"
            and state["lcmv"] == "off"
        )
        lcmv_preserve = (
            state["bladeRF"] == "on"
            and state["jammer"] == "off"
            and state["lcmv"] == "on"
        )
        coverage["bladeRF_baseline_lcmv_off_jammer_off"] |= baseline
        coverage["lcmv_on_bladeRF_on_jammer_off"] |= lcmv_preserve
        coverage["lcmv_reenabled_with_bladeRF_on_jammer_off"] |= (
            lcmv_on_count >= 2 and lcmv_preserve
        )
    return coverage


def _nearest_gnss_snapshot(
    timeline: list[tuple[datetime, str, dict[str, object]]],
    event_time_s: float,
    *,
    before: bool,
    max_distance_s: float = 5.0,
) -> dict[str, object] | None:
    candidates: list[tuple[float, dict[str, object]]] = []
    for timestamp, kind, payload in timeline:
        if kind != "gnss" or timestamp == datetime.min:
            continue
        # Python logging's ``asctime`` is local wall time. A naive datetime's
        # timestamp() therefore applies the machine/session local timezone.
        epoch = timestamp.timestamp()
        delta = epoch - event_time_s
        if (before and delta <= 0.0) or (not before and delta >= 0.0):
            if abs(delta) <= max_distance_s:
                candidates.append((abs(delta), payload))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _timeline_epoch(timestamp: datetime) -> float:
    # Logger timestamps are local, naive wall time.
    return timestamp.timestamp()


def summarize_gnss_window(
    timeline: list[tuple[datetime, str, dict[str, object]]],
    start_s: float,
    end_s: float,
) -> dict[str, object]:
    snapshots = [
        payload
        for timestamp, kind, payload in timeline
        if kind == "gnss"
        and timestamp != datetime.min
        and start_s <= _timeline_epoch(timestamp) <= end_s
    ]
    pvt_states = [
        bool(payload.get("pvt_current"))
        for payload in snapshots
        if payload.get("pvt_current") is not None
    ]
    used_counts = [
        number
        for payload in snapshots
        if (number := _finite(payload.get("pvt_observations"))) is not None
    ]
    cno_values = [
        number
        for payload in snapshots
        if (number := _finite(payload.get("avg_cno_db_hz"))) is not None
    ]
    return {
        "snapshot_count": len(snapshots),
        "pvt_state_sample_count": len(pvt_states),
        "pvt_all_current": all(pvt_states) if pvt_states else None,
        "pvt_loss_observed": any(not state for state in pvt_states),
        "minimum_pvt_observations": min(used_counts) if used_counts else None,
        "minimum_avg_cno_db_hz": min(cno_values) if cno_values else None,
        "mean_avg_cno_db_hz": statistics.fmean(cno_values) if cno_values else None,
    }


def summarize_jammer_windows(
    events: list[dict[str, object]],
    tracking: list[dict[str, object]],
    timeline: list[tuple[datetime, str, dict[str, object]]],
) -> list[dict[str, object]]:
    end_candidates = [float(event["_epoch_s"]) for event in events]
    end_candidates.extend(float(row["_epoch_s"]) for row in tracking)
    end_candidates.extend(
        _timeline_epoch(timestamp)
        for timestamp, kind, _payload in timeline
        if kind == "gnss" and timestamp != datetime.min
    )
    evidence_end = max(end_candidates) if end_candidates else 0.0
    windows: list[tuple[dict[str, object], float]] = []
    open_event: dict[str, object] | None = None
    for event in events:
        name = str(event.get("event", ""))
        if name == "jammer_on":
            if open_event is not None:
                windows.append((open_event, float(event["_epoch_s"])))
            open_event = event
        elif name == "jammer_off" and open_event is not None:
            windows.append((open_event, float(event["_epoch_s"])))
            open_event = None
    if open_event is not None:
        windows.append((open_event, evidence_end))

    summaries: list[dict[str, object]] = []
    for start_event, stop_s in windows:
        start_s = float(start_event["_epoch_s"])
        rows = [
            row for row in tracking if start_s <= float(row["_epoch_s"]) <= stop_s
        ]
        cycle_slips = sorted(
            {
                str(row.get("satellite_id", row.get("prn", "--")))
                for row in rows
                if bool(row.get("cycle_slip", False))
            }
        )
        context = start_event.get("context", {})
        lcmv_enabled = (
            context.get("lcmv_enabled")
            if isinstance(context, dict)
            else None
        )
        summaries.append(
            {
                "start_timestamp_utc": start_event.get(
                    "timestamp_utc", start_event.get("timestamp")
                ),
                "start_session_elapsed_s": start_event.get("session_elapsed_s"),
                "duration_s": max(0.0, stop_s - start_s),
                "lcmv_enabled_at_marker": lcmv_enabled,
                "gnss": summarize_gnss_window(timeline, start_s, stop_s),
                "tracking_record_count": len(rows),
                "cycle_slip_satellites": cycle_slips,
                "cycle_slip_observed": bool(cycle_slips),
                "minimum_tracking_cno_db_hz": (
                    min(values)
                    if (
                        values := [
                            number
                            for row in rows
                            if (number := _finite(row.get("cn0_db_hz"))) is not None
                        ]
                    )
                    else None
                ),
            }
        )
    return summaries


def summarize_automatic_evidence(
    rows: list[dict[str, object]],
) -> dict[str, object]:
    snapshots = [
        row
        for row in rows
        if row.get("event") == "automatic_runtime_evidence_snapshot"
    ]
    transitions = [
        {
            "timestamp_utc": row.get("timestamp_utc"),
            "session_elapsed_s": row.get("session_elapsed_s"),
            "previous_state_key": row.get("previous_state_key"),
            "current_state_key": row.get("current_state_key"),
        }
        for row in rows
        if row.get("event") == "automatic_runtime_state_transition"
    ]
    jammer_states: Counter[str] = Counter()
    lcmv_states: Counter[str] = Counter()
    weight_snapshots = 0
    seen_pvt_fix = False
    inferred_jammer_after_fix = 0
    inferred_jammer_pvt_loss = 0
    inferred_excess_before: list[float] = []
    inferred_excess_after: list[float] = []
    for row in snapshots:
        context = row.get("context", {})
        if not isinstance(context, dict):
            continue
        inference = context.get("physical_state_inference", {})
        if isinstance(inference, dict):
            jammer_state = str(inference.get("jammer_inferred_state", "unknown"))
        else:
            jammer_state = "unknown"
        jammer_states[jammer_state] += 1
        lcmv_states[
            f"enabled={bool(context.get('lcmv_enabled', False))},"
            f"mode={context.get('lcmv_mode', '--')}"
        ] += 1
        beamformer = context.get("beamformer", {})
        if (
            isinstance(beamformer, dict)
            and isinstance(beamformer.get("uniform_logical_weights"), dict)
            and isinstance(beamformer.get("current_logical_weights"), dict)
            and isinstance(beamformer.get("target_logical_weights"), dict)
            and isinstance(
                beamformer.get(
                    "effective_gnss_multiply_coefficients_conj_w_times_calibration"
                ),
                dict,
            )
        ):
            weight_snapshots += 1
        gnss = context.get("gnss", {})
        pvt_current = bool(gnss.get("pvt_current", False)) if isinstance(gnss, dict) else False
        seen_pvt_fix |= pvt_current
        if seen_pvt_fix and jammer_state in {"likely_present", "jammer_like_change"}:
            inferred_jammer_after_fix += 1
            inferred_jammer_pvt_loss += int(not pvt_current)
        spatial = context.get("spatial", {})
        if (
            jammer_state in {"likely_present", "jammer_like_change"}
            and isinstance(spatial, dict)
            and bool(spatial.get("jammer_only_suppression_estimate_available", False))
        ):
            before = _finite(spatial.get("jammer_only_power_before_uniform_linear"))
            after = _finite(spatial.get("jammer_only_power_after_applied_linear"))
            if before is not None and after is not None and before > 0.0 and after > 0.0:
                inferred_excess_before.append(before)
                inferred_excess_after.append(after)
    inferred_suppression_db = None
    if inferred_excess_before and inferred_excess_after:
        mean_before = statistics.fmean(inferred_excess_before)
        mean_after = statistics.fmean(inferred_excess_after)
        if mean_before > 0.0 and mean_after > 0.0:
            inferred_suppression_db = 10.0 * math.log10(mean_before / mean_after)
    return {
        "snapshot_count": len(snapshots),
        "state_transition_count": len(transitions),
        "state_transitions": transitions,
        "jammer_inferred_state_counts": dict(jammer_states),
        "lcmv_state_counts": dict(lcmv_states),
        "complete_weight_snapshot_count": weight_snapshots,
        "all_snapshots_have_complete_weight_state": (
            weight_snapshots == len(snapshots) if snapshots else False
        ),
        "inferred_jammer_snapshots_after_first_fix": inferred_jammer_after_fix,
        "pvt_loss_snapshots_during_inferred_jammer_after_first_fix": (
            inferred_jammer_pvt_loss
        ),
        "inferred_added_scene_suppression_sample_count": len(
            inferred_excess_before
        ),
        "inferred_added_scene_suppression_db": inferred_suppression_db,
        "inferred_added_scene_suppression_method": (
            "aggregate uniform/applied powers from PSD-projected "
            "R_current-R_arm during automatic jammer-like inference"
        ),
        "inferred_added_scene_suppression_warning": (
            "This is suppression of automatically inferred added-scene covariance; "
            "without physical ground truth it is not proven jammer-only suppression"
        ),
        "warning": (
            "Automatic RF state is receiver inference, not proof of a physical "
            "external switch position"
        ),
    }


def audit_session(session: Path) -> dict[str, object]:
    manifest_path = session / "session_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    events = load_events(session)
    tracking = load_tracking(session)
    runtime_evidence = load_runtime_evidence(session)
    timeline = parse_timeline(session)
    operator_event_path = session / "operator_events.jsonl"
    if not operator_event_path.is_file():
        operator_event_path = session / "operator_events.log"
    suppression = estimate_jammer_only_suppression(
        parse_operator_events(operator_event_path),
        parse_spatial_events(session),
    )
    transitions: list[dict[str, object]] = []
    for event in events:
        name = str(event.get("event", ""))
        if name not in TRANSITION_EVENTS:
            continue
        epoch = float(event["_epoch_s"])
        transitions.append(
            {
                "event": name,
                "timestamp_utc": event.get("timestamp_utc", event.get("timestamp")),
                "session_elapsed_s": event.get("session_elapsed_s"),
                "event_context": event.get("context", {}),
                "tracking": summarize_tracking_transition(tracking, event),
                "gnss_before": _nearest_gnss_snapshot(timeline, epoch, before=True),
                "gnss_after": _nearest_gnss_snapshot(timeline, epoch, before=False),
            }
        )
    coverage = scenario_coverage(events)
    jammer_windows = summarize_jammer_windows(events, tracking, timeline)
    automatic_evidence = summarize_automatic_evidence(runtime_evidence)
    automatic_transition_details: list[dict[str, object]] = []
    for row in runtime_evidence:
        if row.get("event") != "automatic_runtime_state_transition":
            continue
        epoch = float(row["_epoch_s"])
        automatic_transition_details.append(
            {
                "timestamp_utc": row.get("timestamp_utc"),
                "session_elapsed_s": row.get("session_elapsed_s"),
                "previous_state_key": row.get("previous_state_key"),
                "current_state_key": row.get("current_state_key"),
                "tracking": summarize_tracking_transition(tracking, row),
                "gnss_before": _nearest_gnss_snapshot(
                    timeline,
                    epoch,
                    before=True,
                ),
                "gnss_after": _nearest_gnss_snapshot(
                    timeline,
                    epoch,
                    before=False,
                ),
            }
        )
    automatic_evidence["state_transition_details"] = automatic_transition_details
    return {
        "schema_version": 1,
        "session": str(session),
        "session_manifest": manifest,
        "event_count": len(events),
        "tracking_record_count": len(tracking),
        "automatic_runtime_evidence_record_count": len(runtime_evidence),
        "carrier_phase_archive_available": bool(tracking),
        "scenario_coverage": coverage,
        "jammer_only_suppression": suppression,
        "jammer_windows": jammer_windows,
        "automatic_evidence": automatic_evidence,
        "all_required_core_scenarios_covered": all(
            coverage[name]
            for name in (
                "bladeRF_baseline_lcmv_off_jammer_off",
                "lcmv_on_bladeRF_on_jammer_off",
                "jammer_on_during_lcmv",
                "jammer_off_during_lcmv",
                "lcmv_off_after_jammer_cycle",
                "lcmv_reenabled_with_bladeRF_on_jammer_off",
                "jammer_reenabled_after_lcmv_rearm",
            )
        ),
        "transitions": transitions,
    }


def print_report(report: dict[str, object]) -> None:
    print(f"Session: {report['session']}")
    print(
        "Evidence: "
        f"events={report['event_count']} "
        f"tracking_rows={report['tracking_record_count']} "
        f"carrier_archive={report['carrier_phase_archive_available']}"
    )
    print("Optional operator-marker scenario coverage (physical ground truth)")
    coverage = report.get("scenario_coverage", {})
    if isinstance(coverage, dict):
        for name, covered in coverage.items():
            print(f"  {'PASS' if covered else 'MISSING':7s} {name}")
    automatic = report.get("automatic_evidence", {})
    if isinstance(automatic, dict):
        print("Automatic evidence (no operator button required)")
        print(f"  snapshots={automatic.get('snapshot_count', 0)}")
        print(f"  transitions={automatic.get('state_transition_count', 0)}")
        print(f"  jammer_inference={automatic.get('jammer_inferred_state_counts', {})}")
        print(f"  lcmv_states={automatic.get('lcmv_state_counts', {})}")
        print(
            "  complete_weight_state="
            f"{automatic.get('all_snapshots_have_complete_weight_state', False)}"
        )
        print(
            "  inferred_jammer_pvt_loss_snapshots="
            f"{automatic.get('pvt_loss_snapshots_during_inferred_jammer_after_first_fix', 0)}"
        )
        print(
            "  inferred_added_scene_suppression_db="
            f"{automatic.get('inferred_added_scene_suppression_db', '--')}"
        )
        for transition in automatic.get("state_transition_details", []):
            if not isinstance(transition, dict):
                continue
            tracking_state = transition.get("tracking", {})
            if not isinstance(tracking_state, dict):
                tracking_state = {}
            print(
                "  auto_transition "
                f"{transition.get('timestamp_utc', '--')}: "
                f"continuous={tracking_state.get('continuous_satellite_fraction')} "
                f"cycle_slip={tracking_state.get('cycle_slip_detected_after')} "
                f"cno_change_db={tracking_state.get('mean_cno_change_db')} "
                "max_phase_residual_rad="
                f"{tracking_state.get('max_abs_carrier_phase_residual_rads')}"
            )
    suppression = report.get("jammer_only_suppression", {})
    if isinstance(suppression, dict):
        print("Jammer-only suppression")
        print(f"  available={suppression.get('available', False)}")
        print(f"  method={suppression.get('method', suppression.get('reason', '--'))}")
        print(f"  suppression_db={suppression.get('suppression_db', '--')}")
    print("Jammer windows")
    for index, window in enumerate(report.get("jammer_windows", []), start=1):
        if not isinstance(window, dict):
            continue
        gnss = window.get("gnss", {})
        if not isinstance(gnss, dict):
            gnss = {}
        print(
            f"  {index}: duration_s={window.get('duration_s')} "
            f"lcmv={window.get('lcmv_enabled_at_marker')} "
            f"pvt_loss={gnss.get('pvt_loss_observed')} "
            f"cycle_slip={window.get('cycle_slip_observed')} "
            f"min_cno={window.get('minimum_tracking_cno_db_hz')}"
        )
    print("Transitions")
    for transition in report.get("transitions", []):
        if not isinstance(transition, dict):
            continue
        tracking = transition.get("tracking", {})
        if not isinstance(tracking, dict):
            tracking = {}
        print(
            f"  {transition.get('timestamp_utc', '--')} "
            f"{transition.get('event', '--')}: "
            f"continuous={tracking.get('continuous_satellite_fraction')} "
            f"cycle_slip={tracking.get('cycle_slip_detected_after')} "
            f"cno_change_db={tracking.get('mean_cno_change_db')} "
            f"max_phase_residual_rad={tracking.get('max_abs_carrier_phase_residual_rads')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    parser.add_argument("--session", type=Path)
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Output path; defaults to <session>/session_audit.json",
    )
    args = parser.parse_args()
    session = resolve_session(args.logs, args.session)
    report = audit_session(session)
    output = args.output_json or (session / "session_audit.json")
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    report_text_path = session / "session_audit.txt"
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        print_report(report)
    report_text = buffer.getvalue()
    report_text_path.write_text(report_text, encoding="utf-8")
    print(report_text, end="")
    print(f"Machine-readable audit: {output}")
    print(f"Human-readable audit: {report_text_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
