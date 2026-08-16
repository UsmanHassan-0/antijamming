#!/usr/bin/env python3
"""Summarize one realtime LCMV/MUSIC diagnostic run from runtime logs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from antijamming.rf import compute_rf_budget


LOG_PREFIX_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \| "
    r"(?P<level>\w+) \| (?P<msg>.*)$"
)
KV_RE = re.compile(r"(?P<key>[A-Za-z0-9_./-]+)=(?P<value>\[[^\]]*\]|\"[^\"]*\"|'[^']*'|\S+)")

CURRENT_LCMV_METHODS = {
    "covariance_lcmv_ideal",
    "covariance_lcmv_measured_u1",
}


@dataclass
class IntervalStats:
    name: str
    start: datetime
    end: datetime | None = None
    doa: list[float] = field(default_factory=list)
    peak_counts: Counter[int] = field(default_factory=Counter)
    source_gap: Counter[int] = field(default_factory=Counter)
    noise_tail_spread_db: list[float] = field(default_factory=list)
    noise_tail_flatness_db: list[float] = field(default_factory=list)
    noise_tail_white_like: Counter[str] = field(default_factory=Counter)
    model_null_db: list[float] = field(default_factory=list)
    reduction_uniform_db: list[float] = field(default_factory=list)
    reduction_raw_avg_db: list[float] = field(default_factory=list)
    reduction_raw_sum_db: list[float] = field(default_factory=list)
    raw_spread_db: list[float] = field(default_factory=list)
    cal_spread_db: list[float] = field(default_factory=list)
    cno: list[float] = field(default_factory=list)
    pvt_status: Counter[str] = field(default_factory=Counter)
    observations: Counter[int] = field(default_factory=Counter)
    observation_values: list[float] = field(default_factory=list)
    used_prns: Counter[str] = field(default_factory=Counter)
    null_in_jammer: Counter[str] = field(default_factory=Counter)
    null_in_bladerf: Counter[str] = field(default_factory=Counter)
    music_in_jammer: Counter[str] = field(default_factory=Counter)
    music_in_bladerf: Counter[str] = field(default_factory=Counter)
    spatial_coherence_abs: list[float] = field(default_factory=list)
    spatial_principal_angle_deg: list[float] = field(default_factory=list)
    spatial_predicted_u1_gain_db: list[float] = field(default_factory=list)
    spatial_active_methods: Counter[str] = field(default_factory=Counter)
    spatial_active_applied_methods: Counter[str] = field(default_factory=Counter)
    spatial_candidate_valid: Counter[str] = field(default_factory=Counter)
    spatial_candidate_rejected: Counter[str] = field(default_factory=Counter)
    run_states: Counter[str] = field(default_factory=Counter)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    args = parser.parse_args()
    logs = args.logs

    found = {path.name for path in logs.glob("*") if path.is_file()}
    missing = [
        name
        for name in (
            "app.log",
            "usrp_hardware.log",
            "lcmv.log",
            "analysis.log",
            "lcmv_pattern_absolute.jsonl",
            "doa.log",
            "stream_health.log",
            "phase_alignment.log",
            "gnss_handoff.log",
            "spatial_vector_diagnostics.jsonl",
            "transport.log",
            "errors.log",
        )
        if name not in found
    ]

    app = parse_app_log(logs / "app.log")
    operator_events = parse_operator_events(logs / "operator_events.log")
    for ts, payload in operator_events:
        event = str(payload.get("event", ""))
        if event in {"lcmv_on", "lcmv_off"}:
            app.setdefault("toggles", []).append((ts, event == "lcmv_on"))
    manifest, rf_budget = parse_manifest_budget(logs)
    marked_rf_budget, marked_rf_basis = operator_marked_rf_budget(
        manifest, operator_events
    )
    usrp = parse_usrp_config(logs / "usrp_hardware.log")
    timeline = parse_timeline(logs)
    spatial_events = parse_spatial_events(logs)
    timeline.extend((ts, "spatial", payload) for ts, payload in spatial_events)
    timeline.sort(key=lambda item: item[0])
    intervals = build_intervals(app, timeline)
    assign_metrics_to_intervals(intervals, timeline)
    health = parse_health(logs / "stream_health.log")
    transport = parse_transport(logs / "transport.log")
    calibration = parse_calibration_logs(logs)

    print("Run")
    print(f"  start: {format_ts(app.get('start'))}")
    print(f"  end:   {format_ts(app.get('stop'))}")
    print(f"  duration_s: {duration_s(app.get('start'), app.get('stop'))}")
    print()
    print("USRP config")
    for key in ("center_freq_hz", "sample_rate_sps", "rx_bandwidth_hz", "rx_gain_db", "channels"):
        print(f"  {key}: {usrp.get(key, '--')}")
    print()
    print("Experiment manifest")
    print_json_summary(manifest)
    print()
    print("RF budget")
    print_json_summary(rf_budget)
    print_js_summary(rf_budget)
    print_metadata_warnings(manifest)
    if marked_rf_budget:
        print()
        print("Operator-marked RF budget")
        print(f"  basis: {marked_rf_basis}")
        print_json_summary(marked_rf_budget)
        print_js_summary(marked_rf_budget)
    print()
    print("Operator events")
    print_operator_events(operator_events)
    print()
    print("Diagnostic coverage")
    print_diagnostic_coverage(logs, spatial_events)
    print()
    print("Calibration Summary")
    print_calibration_summary(calibration)
    print()
    print("Calibration Effect on Null Diagnostics")
    print_calibration_effect_summary(calibration, spatial_events, intervals)
    print()
    print("Spatial vector diagnostics")
    print_spatial_summary(spatial_events)
    print_jammer_only_estimate(
        estimate_jammer_only_suppression(operator_events, spatial_events)
    )
    print()
    print("LCMV intervals")
    for interval in intervals:
        print_interval(interval)
    print(f"  GNSS recovery delay: {gnss_recovery_delay(operator_events, timeline)}")
    print()
    print("Run health")
    print(f"  max_raw_peak_component: {fmt(health.get('max_raw_peak_component'))}")
    print(f"  max_fifo_peak_component: {fmt(health.get('max_fifo_peak_component'))}")
    print(f"  clipping_suspected_count: {health.get('clipping_suspected_count', 0)}")
    print(f"  rx_overflows: {transport.get('rx_overflows', 0)}")
    print(f"  rx_timeouts: {transport.get('rx_timeouts', 0)}")
    print()
    print("Files")
    print(f"  found: {', '.join(sorted(found)) if found else '--'}")
    print(f"  missing: {', '.join(missing) if missing else '--'}")
    return 0


def parse_app_log(path: Path) -> dict[str, object]:
    result: dict[str, object] = {"toggles": []}
    for ts, msg in iter_log(path):
        if "USRP stream started" in msg and result.get("start") is None:
            result["start"] = ts
        if "USRP stream stopped" in msg:
            result["stop"] = ts
        if "lcmv_test_enabled=" in msg:
            enabled = "lcmv_test_enabled=True" in msg
            result.setdefault("toggles", []).append((ts, enabled))
    return result


def parse_manifest_budget(logs: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest: dict[str, object] = {}
    budget: dict[str, object] = {}
    for path in (logs / "app.log", logs / "analysis.log", logs / "lcmv.log"):
        for _ts, msg in iter_log(path):
            if msg.startswith("experiment_manifest "):
                manifest = parse_json_after(msg, "experiment_manifest ") or manifest
            elif msg.startswith("rf_budget "):
                budget = parse_json_after(msg, "rf_budget ") or budget
        if manifest and budget:
            break
    return manifest, budget


def parse_usrp_config(path: Path) -> dict[str, object]:
    cfg: dict[str, object] = {}
    for _ts, msg in iter_log(path):
        assign_regex(cfg, "center_freq_hz", msg, r"(?:freq|center_freq)=([0-9.]+)\s*MHz", 1e6)
        assign_regex(cfg, "sample_rate_sps", msg, r"(?:rate|sample_rate)=([0-9.]+)\s*Msps", 1e6)
        assign_regex(cfg, "rx_bandwidth_hz", msg, r"(?:rx_bw|bandwidth)=([0-9.]+)\s*MHz", 1e6)
        assign_regex(cfg, "rx_gain_db", msg, r"gain=([0-9.]+)\s*dB", 1.0)
        if "channels=" in msg and "channels" not in cfg:
            match = re.search(r"channels=(\[[^\]]+\])", msg)
            cfg["channels"] = match.group(1) if match else msg
    return cfg


def parse_operator_events(path: Path) -> list[tuple[datetime, dict[str, object]]]:
    events: list[tuple[datetime, dict[str, object]]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        payload = parse_json_line(line)
        if not payload:
            continue
        timestamp = payload.get("timestamp")
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            # Python log ``asctime`` values are local wall time. Convert an
            # aware marker (normally UTC) to this host's local zone before
            # removing tzinfo so event/spatial comparisons remain aligned on
            # non-UTC systems such as the Pakistan-time DGX.
            ts = (
                parsed.astimezone().replace(tzinfo=None)
                if parsed.tzinfo is not None
                else parsed
            )
        except (TypeError, ValueError):
            continue
        events.append((ts, payload))
    return sorted(events, key=lambda item: item[0])


def parse_timeline(logs: Path) -> list[tuple[datetime, str, dict[str, object]]]:
    events: list[tuple[datetime, str, dict[str, object]]] = []
    for ts, msg in iter_log(logs / "app.log"):
        if "lcmv_test_enabled=" in msg:
            events.append((ts, "toggle", {"enabled": "lcmv_test_enabled=True" in msg}))
    for ts, msg in iter_log(logs / "doa.log"):
        if "doa method=music" in msg:
            kv = parse_kv(msg)
            events.append((ts, "doa", kv))
    for ts, msg in iter_log(logs / "gnss_handoff.log"):
        if msg.startswith("runtime->GNSS snapshot:"):
            events.append((ts, "gnss", parse_kv(msg)))
    for ts, msg in iter_log(logs / "analysis.log"):
        payload = parse_json_line(msg)
        if not payload:
            continue
        event = str(payload.get("event", ""))
        if event == "lcmv_model_response_absolute":
            events.append((ts, "lcmv_pattern", payload))
        elif event == "full_angle_analysis":
            events.append((ts, "analysis", payload))
    for ts, msg in iter_log(logs / "lcmv_pattern_absolute.jsonl"):
        payload = parse_json_line(msg)
        if payload:
            events.append((ts, "lcmv_pattern", payload))
    return sorted(events, key=lambda item: item[0])


def parse_spatial_events(logs: Path) -> list[tuple[datetime, dict[str, object]]]:
    events: list[tuple[datetime, dict[str, object]]] = []
    seen: set[tuple[object, object, object]] = set()
    for path in (logs / "analysis.log", logs / "spatial_vector_diagnostics.jsonl"):
        for ts, msg in iter_log(path):
            payload = parse_json_line(msg)
            if not payload or payload.get("event") != "spatial_vector_diagnostics":
                continue
            key = (
                payload.get("sequence"),
                payload.get("music_internal_angle_deg"),
                payload.get("sample_count"),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append((ts, payload))
    return sorted(events, key=lambda item: item[0])


def parse_calibration_logs(logs: Path) -> dict[str, object]:
    manifests: list[dict[str, object]] = []
    phase_diags: list[dict[str, object]] = []
    for path in (logs / "app.log", logs / "analysis.log"):
        for _ts, msg in iter_log(path):
            payload = parse_json_after(msg, "calibration_manifest ")
            if payload:
                manifests.append(payload)
    for path in (logs / "phase_alignment.log", logs / "stream_health.log", logs / "analysis.log"):
        for _ts, msg in iter_log(path):
            payload = parse_json_after(msg, "phase_channel_diagnostics ")
            if payload:
                phase_diags.append(payload)
    return {"manifests": manifests, "phase_diags": phase_diags}


def build_intervals(
    app: dict[str, object],
    timeline: list[tuple[datetime, str, dict[str, object]]],
) -> list[IntervalStats]:
    start = app.get("start")
    if not isinstance(start, datetime):
        start = timeline[0][0] if timeline else None
    stop = app.get("stop")
    if not isinstance(stop, datetime):
        stop = timeline[-1][0] if timeline else None
    if not isinstance(start, datetime):
        return []
    toggles = sorted(
        [
        (ts, enabled)
        for ts, enabled in app.get("toggles", [])
        if isinstance(ts, datetime) and ts >= start
        ],
        key=lambda item: item[0],
    )
    intervals: list[IntervalStats] = []
    current_start = start
    current_enabled = False
    first_interval = True
    for ts, enabled in toggles:
        if ts > current_start:
            intervals.append(
                IntervalStats(
                    name=(
                        "pre-toggle LCMV OFF"
                        if first_interval
                        else ("LCMV ON" if current_enabled else "LCMV OFF")
                    ),
                    start=current_start,
                    end=ts,
                )
            )
            first_interval = False
        current_start = ts
        current_enabled = bool(enabled)
    intervals.append(
        IntervalStats(
            name="LCMV ON" if current_enabled else "LCMV OFF",
            start=current_start,
            end=stop if isinstance(stop, datetime) else None,
        )
    )
    return intervals


def assign_metrics_to_intervals(
    intervals: list[IntervalStats],
    timeline: list[tuple[datetime, str, dict[str, object]]],
) -> None:
    for ts, kind, data in timeline:
        interval = find_interval(intervals, ts)
        if interval is None:
            continue
        if kind == "doa":
            append_float(interval.doa, data.get("doa_display_deg"))
            increment_int(interval.peak_counts, data.get("peak_count"))
            increment_int(interval.source_gap, data.get("source_est_gap"))
            append_float(interval.noise_tail_spread_db, data.get("noise_tail_spread_db"))
            append_float(interval.noise_tail_flatness_db, data.get("noise_tail_flatness_db"))
            white_like = data.get("noise_tail_white_like")
            if white_like is not None:
                interval.noise_tail_white_like[str(white_like)] += 1
        elif kind == "gnss":
            append_float(interval.cno, data.get("avg_cno_db_hz"))
            increment_int(interval.observations, data.get("pvt_observations"))
            append_float(interval.observation_values, data.get("pvt_observations"))
            interval.pvt_status[str(data.get("pvt_gui_status", "--"))] += 1
            used_value = data.get("used_pvt", "")
            if used_value is None:
                used_value = ""
            for prn in str(used_value).split(","):
                prn = prn.strip()
                if prn and prn != "--":
                    interval.used_prns[prn] += 1
            append_float(interval.reduction_uniform_db, data.get("measured_output_reduction_vs_uniform_db"))
            append_float(interval.reduction_raw_avg_db, data.get("measured_output_reduction_vs_raw_avg_channel_db"))
            append_float(
                interval.reduction_raw_sum_db,
                data.get("measured_output_reduction_vs_raw_sum_channels_db"),
            )
            append_float(interval.raw_spread_db, data.get("raw_power_spread_db"))
            append_float(interval.cal_spread_db, data.get("cal_power_spread_db"))
        elif kind == "lcmv_pattern":
            append_float(interval.model_null_db, data.get("model_response_at_selected_null_db"))
            out = data.get("output_metrics", {})
            if isinstance(out, dict):
                append_float(interval.reduction_uniform_db, out.get("measured_output_reduction_vs_uniform_db"))
                append_float(interval.reduction_raw_avg_db, out.get("measured_output_reduction_vs_raw_avg_channel_db"))
                append_float(
                    interval.reduction_raw_sum_db,
                    out.get("measured_output_reduction_vs_raw_sum_channels_db"),
                )
        elif kind == "analysis":
            hints = data.get("classification_hints", {})
            if isinstance(hints, dict):
                counter_bool(interval.music_in_jammer, hints.get("primary_peak_inside_expected_jammer_range"))
                counter_bool(interval.music_in_bladerf, hints.get("primary_peak_inside_expected_bladeRF_range"))
            lcmv = data.get("lcmv", {})
            if isinstance(lcmv, dict):
                summary = lcmv.get("lcmv_model_summary", {})
                if isinstance(summary, dict):
                    append_float(interval.model_null_db, summary.get("model_response_at_selected_null_db"))
        elif kind == "spatial":
            append_float(interval.spatial_coherence_abs, data.get("ideal_measured_coherence_abs"))
            append_float(
                interval.spatial_principal_angle_deg,
                data.get("ideal_measured_principal_angle_deg"),
            )
            append_float(
                interval.spatial_predicted_u1_gain_db,
                data.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db"),
            )
            method = str(data.get("active_lcmv_null_method", "--"))
            interval.spatial_active_methods[method] += 1
            applied = str(data.get("active_lcmv_method", method))
            interval.spatial_active_applied_methods[applied] += 1
            valid = data.get("candidate_methods_valid", [])
            if isinstance(valid, list):
                for candidate in valid:
                    if str(candidate) in CURRENT_LCMV_METHODS:
                        interval.spatial_candidate_valid[str(candidate)] += 1
            rejected = data.get("candidate_methods_rejected", {})
            if isinstance(rejected, dict):
                for candidate in rejected:
                    if str(candidate) in CURRENT_LCMV_METHODS:
                        interval.spatial_candidate_rejected[str(candidate)] += 1
            interval.run_states[str(data.get("run_state_label", "--"))] += 1


def parse_health(path: Path) -> dict[str, object]:
    result: dict[str, object] = {"clipping_suspected_count": 0}
    raw_peaks: list[float] = []
    fifo_peaks: list[float] = []
    for _ts, msg in iter_log(path):
        if "phase_channel_diagnostics " in msg:
            payload = parse_json_after(msg, "phase_channel_diagnostics ")
            if payload:
                for key, value in payload.items():
                    if key.startswith("raw_ch") and key.endswith("_peak_component"):
                        append_float(raw_peaks, value)
        if "gnss fifo iq:" in msg:
            kv = parse_kv(msg)
            append_float(fifo_peaks, kv.get("iq_peak_component"))
        if "clipping_suspected=True" in msg:
            result["clipping_suspected_count"] = int(result["clipping_suspected_count"]) + 1
    result["max_raw_peak_component"] = max(raw_peaks) if raw_peaks else None
    result["max_fifo_peak_component"] = max(fifo_peaks) if fifo_peaks else None
    return result


def parse_transport(path: Path) -> dict[str, object]:
    result = {"rx_overflows": 0, "rx_timeouts": 0}
    for _ts, msg in iter_log(path):
        kv = parse_kv(msg)
        if "rx_overflows" in kv:
            result["rx_overflows"] = max(result["rx_overflows"], int_or_zero(kv["rx_overflows"]))
        if "rx_timeouts" in kv:
            result["rx_timeouts"] = max(result["rx_timeouts"], int_or_zero(kv["rx_timeouts"]))
    return result


def iter_log(path: Path) -> Iterable[tuple[datetime, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            match = LOG_PREFIX_RE.match(line)
            if match:
                yield parse_ts(match.group("ts")), match.group("msg")
            else:
                yield datetime.min, line


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")


def parse_kv(message: str) -> dict[str, object]:
    return {match.group("key"): strip_value(match.group("value")) for match in KV_RE.finditer(message)}


def parse_json_after(message: str, prefix: str) -> dict[str, object] | None:
    if not message.startswith(prefix):
        return None
    return parse_json_line(message[len(prefix) :])


def parse_json_line(message: str) -> dict[str, object] | None:
    text = message.strip()
    if " | " in text:
        text = text.rsplit(" | ", 1)[-1].strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def strip_value(value: str) -> object:
    text = value.strip().strip(",")
    if text in {"--", "None", "null"}:
        return None
    if text in {"True", "true"}:
        return True
    if text in {"False", "false"}:
        return False
    try:
        if any(char in text for char in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text.strip("'\"")


def find_interval(intervals: list[IntervalStats], ts: datetime) -> IntervalStats | None:
    for interval in intervals:
        if ts < interval.start:
            continue
        if interval.end is not None and ts >= interval.end:
            continue
        return interval
    return intervals[-1] if intervals else None


def append_float(values: list[float], value: object) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return
    if number == number and number not in (float("inf"), float("-inf")):
        values.append(number)


def increment_int(counter: Counter[int], value: object) -> None:
    try:
        counter[int(value)] += 1
    except (TypeError, ValueError):
        return


def counter_bool(counter: Counter[str], value: object) -> None:
    if value is None:
        counter["unknown"] += 1
    else:
        counter[str(bool(value))] += 1


def int_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def numeric_or_none(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def first_number(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", text, re.IGNORECASE)
    return float(match.group(0)) if match else None


def assign_regex(
    target: dict[str, object],
    key: str,
    text: str,
    pattern: str,
    scale: float,
) -> None:
    if key in target:
        return
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return
    target[key] = float(match.group(1)) * float(scale)


def format_ts(value: object) -> str:
    return value.isoformat(sep=" ") if isinstance(value, datetime) else "--"


def duration_s(start: object, stop: object) -> str:
    if isinstance(start, datetime) and isinstance(stop, datetime):
        return f"{(stop - start).total_seconds():.1f}"
    return "--"


def fmt(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{number:.3f}"


def format_list(value: object) -> str:
    if not isinstance(value, (list, tuple)):
        return "--"
    formatted: list[str] = []
    for item in value:
        try:
            formatted.append(f"{float(item):.4f}")
        except (TypeError, ValueError):
            formatted.append("--")
    return "[" + ", ".join(formatted) + "]"


def stat(values: list[float]) -> str:
    if not values:
        return "--"
    return f"avg={sum(values)/len(values):.2f} min={min(values):.2f} max={max(values):.2f} n={len(values)}"


def mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def print_json_summary(payload: dict[str, object]) -> None:
    if not payload:
        print("  --")
        return
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, float):
            value = f"{value:.3f}"
        print(f"  {key}: {value}")


def print_metadata_warnings(manifest: dict[str, object]) -> None:
    required = (
        "bladeRF_tx_gain_db",
        "bladeRF_tx_power_dbm_est",
        "jammer_attenuation_db",
        "jammer_l1_4mhz_avg_dbm",
        "bladeRF_distance_m",
        "jammer_distance_m",
        "usrp_rx_gain_db",
        "center_freq_hz",
        "sample_rate_sps",
        "rx_bandwidth_hz",
    )
    missing = [key for key in required if manifest.get(key) in (None, "", "unknown")]
    if missing:
        print(f"  metadata warning: missing/unknown {', '.join(missing)}")
    else:
        print("  metadata warning: none")


def operator_marked_rf_budget(
    manifest: dict[str, object],
    operator_events: list[tuple[datetime, dict[str, object]]],
) -> tuple[dict[str, object], str]:
    """Recompute RF levels from the latest explicit attenuation marker."""

    attenuation_db: float | None = None
    marker_event = ""
    marker_time: datetime | None = None
    for ts, payload in operator_events:
        event = str(payload.get("event", ""))
        if event not in {"attenuation_db", "jammer_on"}:
            continue
        candidate = numeric_or_none(payload.get("attenuation_db"))
        if candidate is None:
            continue
        attenuation_db = candidate
        marker_event = event
        marker_time = ts
    if attenuation_db is None:
        return {}, ""
    marked_manifest = dict(manifest)
    marked_manifest["jammer_attenuation_db"] = attenuation_db
    basis = (
        f"explicit {marker_event} marker at {format_ts(marker_time)} with "
        f"attenuation_db={attenuation_db:.2f}"
    )
    return compute_rf_budget(marked_manifest), basis


def print_js_summary(rf_budget: dict[str, object]) -> None:
    jammer = numeric_or_none(rf_budget.get("jammer_avg_usrp_rf_input_ideal_dbm"))
    jammer_peak = numeric_or_none(
        rf_budget.get("jammer_peak_usrp_rf_input_ideal_dbm")
    )
    desired = numeric_or_none(rf_budget.get("bladeRF_usrp_rf_input_ideal_dbm"))
    if jammer is None or desired is None:
        print("  expected J/S vs bladeRF at USRP RF input: --")
        return
    print(f"  expected average J/S vs bladeRF at USRP RF input: {jammer - desired:.2f} dB")
    if jammer_peak is not None:
        print(
            "  expected peak J/S vs bladeRF at USRP RF input: "
            f"{jammer_peak - desired:.2f} dB"
        )


def print_operator_events(events: list[tuple[datetime, dict[str, object]]]) -> None:
    if not events:
        print("  No explicit operator event markers found.")
        print(
            "  No explicit operator jammer/bladeRF markers found; jammer states "
            "inferred from RF/GNSS logs only."
        )
        return
    for ts, payload in events:
        print(
            f"  {format_ts(ts)} event={payload.get('event', '--')} "
            f"attenuation_db={payload.get('attenuation_db', '--')} "
            f"bladeRF_gain_db={payload.get('bladeRF_gain_db', '--')} "
            f"notes={payload.get('notes', '') or '--'}"
        )


def gnss_recovery_delay(
    operator_events: list[tuple[datetime, dict[str, object]]],
    timeline: list[tuple[datetime, str, dict[str, object]]],
) -> str:
    jammer_off = [
        ts
        for ts, payload in operator_events
        if str(payload.get("event", "")) == "jammer_off"
    ]
    if not jammer_off:
        return "-- (no explicit jammer_off marker)"
    marker = jammer_off[-1]
    for ts, kind, payload in timeline:
        if ts < marker or kind != "gnss":
            continue
        status = str(payload.get("pvt_gui_status", "")).upper()
        if "FIX" in status and "NO_FIX" not in status:
            return f"{(ts - marker).total_seconds():.2f} s"
    return "-- (no later PVT FIX in parsed logs)"


def estimate_jammer_only_suppression(
    operator_events: list[tuple[datetime, dict[str, object]]],
    spatial_events: list[tuple[datetime, dict[str, object]]],
) -> dict[str, object]:
    jammer_markers = [
        (ts, str(payload.get("event", "")) == "jammer_on")
        for ts, payload in operator_events
        if str(payload.get("event", "")) in {"jammer_on", "jammer_off"}
    ]
    if not jammer_markers:
        return {
            "available": False,
            "reason": "no explicit jammer_on/jammer_off operator markers",
        }

    # Prefer the runtime's same-covariance estimate.  It evaluates the uniform
    # and actually applied weights against the same PSD-projected covariance
    # difference R_current - R_arm.  The automatic activation latch alone is
    # not physical truth, so accept these samples only while an explicit
    # operator marker says that the physical jammer is on.
    runtime_suppression_db: list[float] = []
    runtime_before: list[float] = []
    runtime_after: list[float] = []
    marker_index = 0
    jammer_on: bool | None = None
    for ts, payload in spatial_events:
        while marker_index < len(jammer_markers) and jammer_markers[marker_index][0] <= ts:
            jammer_on = jammer_markers[marker_index][1]
            marker_index += 1
        if jammer_on is not True or not bool(
            payload.get("jammer_only_suppression_estimate_available", False)
        ):
            continue
        suppression = numeric_or_none(payload.get("jammer_only_suppression_db"))
        before = numeric_or_none(
            payload.get("jammer_only_power_before_uniform_linear")
        )
        after = numeric_or_none(
            payload.get("jammer_only_power_after_applied_linear")
        )
        if (
            suppression is not None
            and before is not None
            and after is not None
            and before > 0.0
            and after >= 0.0
        ):
            runtime_suppression_db.append(suppression)
            runtime_before.append(before)
            runtime_after.append(after)
    if runtime_suppression_db:
        mean_before = mean_or_none(runtime_before)
        mean_after = mean_or_none(runtime_after)
        aggregate_suppression_db = (
            10.0 * math.log10(mean_before / mean_after)
            if mean_before is not None
            and mean_after is not None
            and mean_before > 0.0
            and mean_after > 0.0
            else None
        )
        return {
            "available": True,
            "method": (
                "same-covariance PSD-projected R_current-R_arm inside explicit "
                "jammer_on marker window"
            ),
            "sample_count": len(runtime_suppression_db),
            # Aggregate powers first, then form the ratio. Averaging values
            # that are already in dB gives a different number whenever the
            # per-window input powers differ, so keep that only as a clearly
            # labelled distribution diagnostic.
            "suppression_db": aggregate_suppression_db,
            "mean_per_snapshot_suppression_db": mean_or_none(
                runtime_suppression_db
            ),
            "jammer_before_power_linear": mean_before,
            "jammer_after_power_linear": mean_after,
        }

    baseline_off: list[float] = []
    jammer_on_lcmv_off: list[float] = []
    jammer_on_lcmv_on_excess: list[float] = []
    marker_index = 0
    jammer_on: bool | None = None
    for ts, payload in spatial_events:
        while marker_index < len(jammer_markers) and jammer_markers[marker_index][0] <= ts:
            jammer_on = jammer_markers[marker_index][1]
            marker_index += 1
        if jammer_on is None:
            continue
        total = numeric_or_none(payload.get("active_total_output_power_from_R"))
        if total is None:
            continue
        applied = str(payload.get("active_method_applied", ""))
        lcmv_on = applied in CURRENT_LCMV_METHODS
        if not jammer_on and not lcmv_on:
            baseline_off.append(total)
        elif jammer_on and not lcmv_on:
            jammer_on_lcmv_off.append(total)
        elif jammer_on and lcmv_on:
            adjusted_baseline = numeric_or_none(
                payload.get("active_healthy_baseline_output_power_from_R")
            )
            if adjusted_baseline is not None and total > adjusted_baseline:
                jammer_on_lcmv_on_excess.append(total - adjusted_baseline)

    missing: list[str] = []
    if not baseline_off:
        missing.append("jammer-off/LCMV-off baseline")
    if not jammer_on_lcmv_off:
        missing.append("jammer-on/LCMV-off window")
    if not jammer_on_lcmv_on_excess:
        missing.append("positive jammer-on/LCMV-on baseline-adjusted window")
    if missing:
        return {"available": False, "reason": "missing " + ", ".join(missing)}

    baseline_power = mean_or_none(baseline_off)
    jammer_before_total = mean_or_none(jammer_on_lcmv_off)
    jammer_after = mean_or_none(jammer_on_lcmv_on_excess)
    jammer_before = (
        jammer_before_total - baseline_power
        if jammer_before_total is not None and baseline_power is not None
        else None
    )
    if jammer_before is None or jammer_after is None or jammer_before <= 0.0 or jammer_after <= 0.0:
        return {
            "available": False,
            "reason": "non-positive power after jammer-off baseline subtraction",
        }
    return {
        "available": True,
        "method": "matched marked windows with healthy-baseline subtraction",
        "sample_count": len(jammer_on_lcmv_on_excess),
        "suppression_db": 10.0 * math.log10(jammer_before / jammer_after),
        "jammer_before_power_linear": jammer_before,
        "jammer_after_power_linear": jammer_after,
        "baseline_power_linear": baseline_power,
    }


def print_jammer_only_estimate(estimate: dict[str, object]) -> None:
    if not bool(estimate.get("available", False)):
        print("  jammer_only_suppression_estimate_available: false")
        print(
            "  jammer_only_suppression_unavailable_reason: "
            f"{estimate.get('reason', 'insufficient marked windows')}"
        )
        return
    print("  jammer_only_suppression_estimate_available: true")
    print(f"  jammer_only_suppression_method: {estimate.get('method', '--')}")
    print(f"  jammer_only_suppression_sample_count: {estimate.get('sample_count', '--')}")
    print(
        "  jammer_only_suppression_estimate_db: "
        f"{numeric_or_none(estimate.get('suppression_db')):.2f}"
    )
    snapshot_mean = numeric_or_none(
        estimate.get("mean_per_snapshot_suppression_db")
    )
    if snapshot_mean is not None:
        print(
            "  jammer_only_mean_per_snapshot_suppression_db: "
            f"{snapshot_mean:.2f}"
        )
    print(
        "  jammer_only_power_before/after_linear: "
        f"{estimate.get('jammer_before_power_linear')} / "
        f"{estimate.get('jammer_after_power_linear')}"
    )


def print_diagnostic_coverage(
    logs: Path,
    spatial_events: list[tuple[datetime, dict[str, object]]],
) -> None:
    checks = {
        "experiment_manifest": contains_text(logs / "app.log", "experiment_manifest "),
        "rf_budget": contains_text(logs / "app.log", "rf_budget "),
        "array_geometry_manifest": contains_text(logs / "app.log", "array_geometry_manifest "),
        "phase_channel_diagnostics": contains_text(
            logs / "phase_alignment.log",
            "phase_channel_diagnostics ",
        ),
        "lcmv_model_response_absolute": contains_text(
            logs / "lcmv_pattern_absolute.jsonl",
            "lcmv_model_response_absolute",
        ),
        "spatial_vector_diagnostics_jsonl": contains_text(
            logs / "spatial_vector_diagnostics.jsonl",
            '"event":"spatial_vector_diagnostics"',
        ),
        "fifo_output_power_linear": contains_text(
            logs / "gnss_handoff.log",
            "fifo_output_power_linear",
        )
        or contains_text(logs / "stream_health.log", "fifo_output_power_linear"),
        "calibration_manifest": contains_text(logs / "app.log", "calibration_manifest ")
        or contains_text(logs / "analysis.log", "calibration_manifest "),
        "calibration_gain_effect_ch0_db": contains_text(
            logs / "phase_alignment.log",
            "calibration_gain_effect_ch0_db",
        )
        or contains_text(logs / "stream_health.log", "calibration_gain_effect_ch0_db"),
    }
    for key, value in checks.items():
        print(f"  {key}: {'yes' if value else 'no'}")
    print(f"  spatial_vector_event_count: {len(spatial_events)}")


def print_calibration_summary(calibration: dict[str, object]) -> None:
    manifests = [
        item for item in calibration.get("manifests", []) if isinstance(item, dict)
    ]
    phase_diags = [
        item for item in calibration.get("phase_diags", []) if isinstance(item, dict)
    ]
    latest = manifests[-1] if manifests else {}
    if not latest and not phase_diags:
        print("  --")
        return
    raw_spreads: list[float] = []
    cal_spreads: list[float] = []
    gain_effect: dict[int, list[float]] = defaultdict(list)
    for payload in phase_diags:
        append_float(raw_spreads, payload.get("raw_power_spread_db"))
        append_float(cal_spreads, payload.get("cal_power_spread_db"))
        for channel in range(8):
            append_float(
                gain_effect[channel],
                payload.get(f"calibration_gain_effect_ch{channel}_db"),
            )
    print(
        "  calibration_correction_mode_applied: "
        f"{latest.get('calibration_correction_mode_applied', '--')}"
    )
    print(
        "  complex_gain_vector_available: "
        f"{latest.get('complex_gain_vector_available', '--')}"
    )
    print(f"  fallback_used: {latest.get('fallback_used', '--')}")
    print(f"  fallback_reason: {latest.get('fallback_reason', '--') or '--'}")
    print(
        "  applied_correction_magnitudes: "
        f"{format_list(latest.get('applied_correction_magnitudes'))}"
    )
    print(
        "  applied_correction_phases_deg: "
        f"{format_list(latest.get('applied_correction_phases_deg'))}"
    )
    print(f"  avg raw_power_spread_db: {fmt(mean_or_none(raw_spreads))}")
    print(f"  avg cal_power_spread_db: {fmt(mean_or_none(cal_spreads))}")
    print(f"  cal_power_spread_db: {stat(cal_spreads)}")
    for channel in sorted(gain_effect):
        if gain_effect[channel]:
            print(
                f"  avg calibration_gain_effect_ch{channel}_db: "
                f"{fmt(mean_or_none(gain_effect[channel]))}"
            )
    print(
        "  If complex_gain mode is active and cal_power_spread_db is identical "
        "to raw_power_spread_db, verify correction application."
    )


def print_calibration_effect_summary(
    calibration: dict[str, object],
    spatial_events: list[tuple[datetime, dict[str, object]]],
    intervals: list[IntervalStats],
) -> None:
    coherence: list[float] = []
    principal: list[float] = []
    u1_gain: list[float] = []
    reduction_uniform: list[float] = []
    reduction_raw_avg: list[float] = []
    for _ts, payload in spatial_events:
        append_float(coherence, payload.get("ideal_measured_coherence_abs"))
        append_float(principal, payload.get("ideal_measured_principal_angle_deg"))
        append_float(
            u1_gain,
            payload.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db"),
        )
    for interval in intervals:
        reduction_uniform.extend(interval.reduction_uniform_db)
        reduction_raw_avg.extend(interval.reduction_raw_avg_db)
    print(f"  ideal_measured_coherence_abs: {stat(coherence)}")
    print(f"  ideal_measured_principal_angle_deg: {stat(principal)}")
    print(
        "  measured_output_reduction_vs_uniform_db: "
        f"{stat(reduction_uniform)}"
    )
    print(
        "  measured_output_reduction_vs_raw_avg_channel_db: "
        f"{stat(reduction_raw_avg)}"
    )
    print(
        "  predicted_u1_lcmv_output_gain_over_ideal_lcmv_db: "
        f"{stat(u1_gain)}"
    )


def print_spatial_summary(events: list[tuple[datetime, dict[str, object]]]) -> None:
    if not events:
        print("  --")
        return
    coherence: list[float] = []
    principal: list[float] = []
    mismatch_db: list[float] = []
    ideal_reduction: list[float] = []
    u1_reduction: list[float] = []
    u1_gain: list[float] = []
    ideal_to_u1_suppression: list[float] = []
    u1_to_u1_suppression: list[float] = []
    methods: Counter[str] = Counter()
    applied_methods: Counter[str] = Counter()
    candidates: Counter[str] = Counter()
    candidate_valid: Counter[str] = Counter()
    candidate_rejected: Counter[str] = Counter()
    run_states: Counter[str] = Counter()
    healthy_reference_available: Counter[str] = Counter()
    jammer_only_available: Counter[str] = Counter()
    jammer_only_unavailable_reasons: Counter[str] = Counter()
    candidate_metric_names = (
        "u1_component_reduction_vs_reference_db",
        "ideal_component_reduction_vs_reference_db",
        "total_output_reduction_vs_reference_db",
        "desired_loss_vs_reference_db",
        "white_noise_gain_db",
        "noise_gain_vs_reference_db",
        "effective_js_improvement_u1_db",
        "effective_receiver_improvement_u1_db",
    )
    candidate_stats: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {name: [] for name in candidate_metric_names}
    )
    candidate_valid_by_method: dict[str, Counter[str]] = defaultdict(Counter)
    candidate_rejection_reasons: dict[str, Counter[str]] = defaultdict(Counter)
    prefix_by_method = {
        "covariance_lcmv_ideal": "candidate_covariance_lcmv_ideal",
        "covariance_lcmv_measured_u1": "candidate_covariance_lcmv_measured_u1",
    }
    for _ts, payload in events:
        append_float(coherence, payload.get("ideal_measured_coherence_abs"))
        append_float(principal, payload.get("ideal_measured_principal_angle_deg"))
        append_float(mismatch_db, payload.get("ideal_measured_mismatch_db"))
        append_float(
            ideal_reduction,
            payload.get("measured_covariance_reduction_uniform_to_ideal_lcmv_db"),
        )
        append_float(
            u1_reduction,
            payload.get("measured_covariance_reduction_uniform_to_u1_lcmv_db"),
        )
        append_float(
            u1_gain,
            payload.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db"),
        )
        append_float(
            ideal_to_u1_suppression,
            payload.get("ideal_lcmv_to_u1_suppression_db"),
        )
        append_float(
            u1_to_u1_suppression,
            payload.get("u1_lcmv_to_u1_suppression_db"),
        )
        methods[str(payload.get("active_lcmv_null_method", "--"))] += 1
        applied_methods[str(payload.get("active_lcmv_method", payload.get("active_lcmv_null_method", "--")))] += 1
        candidates[str(bool(payload.get("candidate_u1_lcmv_available", False)))] += 1
        valid = payload.get("candidate_methods_valid", [])
        if isinstance(valid, list):
            for candidate in valid:
                if str(candidate) in CURRENT_LCMV_METHODS:
                    candidate_valid[str(candidate)] += 1
        rejected = payload.get("candidate_methods_rejected", {})
        if isinstance(rejected, dict):
            for candidate in rejected:
                if str(candidate) in CURRENT_LCMV_METHODS:
                    candidate_rejected[str(candidate)] += 1
        run_states[str(payload.get("run_state_label", "--"))] += 1
        healthy_reference_available[
            str(bool(payload.get("healthy_reference_available", False)))
        ] += 1
        jammer_only_available[
            str(bool(payload.get("jammer_only_suppression_estimate_available", False)))
        ] += 1
        jammer_only_reason = payload.get(
            "jammer_only_suppression_unavailable_reason"
        )
        if jammer_only_reason:
            jammer_only_unavailable_reasons[str(jammer_only_reason)] += 1
        for method, prefix in prefix_by_method.items():
            stats = candidate_stats[method]
            for metric_name in candidate_metric_names:
                append_float(stats[metric_name], payload.get(f"{prefix}_{metric_name}"))
            if f"{prefix}_valid" in payload:
                candidate_valid_by_method[method][str(bool(payload.get(f"{prefix}_valid")))] += 1
            reason = payload.get(f"{prefix}_rejected_reason")
            if reason:
                candidate_rejection_reasons[method][str(reason)] += 1
    print(f"  events: {len(events)}")
    print(
        "  signal_model: x[n]=desired/SOI + jammer + real sky GNSS + noise + "
        "multipath + receiver artifacts"
    )
    print("  warning: R is not jammer-only; u1 is not always jammer")
    print("  warning: Dominant-vector suppression is not automatically jammer-only suppression")
    print(
        "  warning: u1 suppression means jammer-like suppression only during "
        "jammer-like windows"
    )
    print(
        "  warning: during healthy/no-jammer windows, u1 suppression may represent "
        "desired/SOI suppression"
    )
    print("  warning: total output reduction is not jammer-only suppression")
    print("  warning: Desired-loss metrics require healthy_reference_available=true")
    print(f"  active_lcmv_null_method distribution: {dict(methods)}")
    print(f"  active_lcmv_method distribution: {dict(applied_methods)}")
    print(f"  candidate_u1_lcmv_available distribution: {dict(candidates)}")
    print(f"  candidate_methods_valid distribution: {dict(candidate_valid) or '--'}")
    print(f"  candidate_methods_rejected distribution: {dict(candidate_rejected) or '--'}")
    print(f"  run_state_label distribution: {dict(run_states) or '--'}")
    print(
        "  healthy_reference_available distribution: "
        f"{dict(healthy_reference_available) or '--'}"
    )
    print(
        "  jammer_only_suppression_estimate_available distribution: "
        f"{dict(jammer_only_available) or '--'}"
    )
    print(
        "  jammer_only_suppression_unavailable_reason: "
        f"{dict(jammer_only_unavailable_reasons) or '--'}"
    )
    print(f"  ideal_measured_coherence_abs: {stat(coherence)}")
    print(f"  ideal_measured_principal_angle_deg: {stat(principal)}")
    print(f"  ideal_measured_mismatch_db: {stat(mismatch_db)}")
    print(
        "  measured_covariance_reduction_uniform_to_ideal_lcmv_db: "
        f"{stat(ideal_reduction)}"
    )
    print(
        "  measured_covariance_reduction_uniform_to_u1_lcmv_db: "
        f"{stat(u1_reduction)}"
    )
    print(
        "  predicted_u1_lcmv_output_gain_over_ideal_lcmv_db: "
        f"{stat(u1_gain)}"
    )
    print(f"  ideal_lcmv_to_u1_suppression_db: {stat(ideal_to_u1_suppression)}")
    print(f"  u1_lcmv_to_u1_suppression_db: {stat(u1_to_u1_suppression)}")

    def ranked_rows(metric_name: str) -> list[tuple[float, str, dict[str, list[float]]]]:
        rows: list[tuple[float, str, dict[str, list[float]]]] = []
        for method, stats_map in candidate_stats.items():
            avg = mean_or_none(stats_map[metric_name])
            if avg is not None:
                rows.append((avg, method, stats_map))
        return sorted(rows, reverse=True)

    print("  candidate_metrics_by_method:")
    any_candidate_metrics = False
    for method in prefix_by_method:
        stats_map = candidate_stats[method]
        if not any(stats_map[name] for name in candidate_metric_names):
            continue
        any_candidate_metrics = True
        print(
            f"    {method}: "
            f"candidate_valid={dict(candidate_valid_by_method.get(method, Counter())) or '--'} "
            f"candidate_rejection_reason={dict(candidate_rejection_reasons.get(method, Counter())) or '--'} "
            f"u1_component_reduction_vs_reference_db={stat(stats_map['u1_component_reduction_vs_reference_db'])} "
            f"ideal_component_reduction_vs_reference_db={stat(stats_map['ideal_component_reduction_vs_reference_db'])} "
            f"total_output_reduction_vs_reference_db={stat(stats_map['total_output_reduction_vs_reference_db'])} "
            f"desired_loss_vs_reference_db={stat(stats_map['desired_loss_vs_reference_db'])} "
            f"white_noise_gain_db={stat(stats_map['white_noise_gain_db'])} "
            f"noise_gain_vs_reference_db={stat(stats_map['noise_gain_vs_reference_db'])} "
            f"effective_js_improvement_u1_db={stat(stats_map['effective_js_improvement_u1_db'])} "
            f"effective_receiver_improvement_u1_db={stat(stats_map['effective_receiver_improvement_u1_db'])}"
        )
    if not any_candidate_metrics:
        print("    --")

    best_u1 = ranked_rows("u1_component_reduction_vs_reference_db")
    best_total = ranked_rows("total_output_reduction_vs_reference_db")
    best_effective = ranked_rows("effective_receiver_improvement_u1_db")
    print(
        "  best_by_dominant_vector_suppression: "
        f"{best_u1[0][1]} avg={best_u1[0][0]:.2f} dB" if best_u1 else
        "  best_by_dominant_vector_suppression: --"
    )
    print(
        "  best_by_total_output_reduction: "
        f"{best_total[0][1]} avg={best_total[0][0]:.2f} dB" if best_total else
        "  best_by_total_output_reduction: --"
    )
    print(
        "  best_by_effective_receiver_improvement: "
        f"{best_effective[0][1]} avg={best_effective[0][0]:.2f} dB" if best_effective else
        "  best_by_effective_receiver_improvement: -- "
        "(effective receiver improvement unavailable because healthy reference missing)"
    )
    selected_active_methods = Counter(
        {
            method: count
            for method, count in applied_methods.items()
            if method in prefix_by_method
        }
    )
    active_method = (
        selected_active_methods.most_common(1)[0][0]
        if selected_active_methods
        else "--"
    )
    best_effective_method = best_effective[0][1] if best_effective else "--"
    print(f"  active_method: {active_method}")
    print(
        "  active_method_was_best_by_effective_receiver_improvement: "
        f"{active_method == best_effective_method if best_effective else '--'}"
    )
    print(f"  recommendation: {spatial_recommendation(coherence, u1_gain)}")


def spatial_recommendation(coherence: list[float], u1_gain_db: list[float]) -> str:
    avg_coherence = mean_or_none(coherence)
    avg_gain = mean_or_none(u1_gain_db)
    if avg_coherence is None or avg_gain is None:
        return "insufficient spatial-vector evidence"
    if avg_coherence >= 0.97 and avg_gain <= 1.0:
        return "keep covariance_lcmv_ideal active; measured u1 is close to its ideal null vector"
    if avg_coherence < 0.95 and avg_gain >= 3.0:
        return "keep covariance_lcmv_ideal active; measured-u1 candidates need desired-loss validation before selection"
    return "mixed evidence; inspect per-event coherence and predicted gain before changing the active method"


def contains_text(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def print_interval(interval: IntervalStats) -> None:
    print(f"  {interval.name}: {format_ts(interval.start)} -> {format_ts(interval.end)}")
    print(f"    MUSIC primary bearing: {stat(interval.doa)}")
    print(f"    peak_count distribution: {dict(interval.peak_counts) or '--'}")
    print(f"    source_est_gap distribution: {dict(interval.source_gap) or '--'}")
    print(f"    noise_tail_spread_db: {stat(interval.noise_tail_spread_db)}")
    print(f"    noise_tail_flatness_db: {stat(interval.noise_tail_flatness_db)}")
    print(f"    noise_tail_white_like distribution: {dict(interval.noise_tail_white_like) or '--'}")
    print(f"    model_response_at_selected_null_db: {stat(interval.model_null_db)}")
    print(f"    measured_output_reduction_vs_uniform_db: {stat(interval.reduction_uniform_db)}")
    print(f"    measured_output_reduction_vs_raw_avg_channel_db: {stat(interval.reduction_raw_avg_db)}")
    print(
        "    measured_output_reduction_vs_raw_sum_channels_db: "
        f"{stat(interval.reduction_raw_sum_db)}"
    )
    print(f"    raw_power_spread_db: {stat(interval.raw_spread_db)}")
    print(f"    cal_power_spread_db: {stat(interval.cal_spread_db)}")
    print(f"    spatial ideal_measured_coherence_abs: {stat(interval.spatial_coherence_abs)}")
    print(
        "    spatial ideal_measured_principal_angle_deg: "
        f"{stat(interval.spatial_principal_angle_deg)}"
    )
    print(
        "    spatial predicted_u1_lcmv_output_gain_over_ideal_lcmv_db: "
        f"{stat(interval.spatial_predicted_u1_gain_db)}"
    )
    print(f"    spatial active_lcmv_null_method: {dict(interval.spatial_active_methods) or '--'}")
    print(
        "    spatial active_lcmv_method: "
        f"{dict(interval.spatial_active_applied_methods) or '--'}"
    )
    print(
        "    spatial candidate_methods_valid: "
        f"{dict(interval.spatial_candidate_valid) or '--'}"
    )
    print(
        "    spatial candidate_methods_rejected: "
        f"{dict(interval.spatial_candidate_rejected) or '--'}"
    )
    print(f"    spatial run_state_label: {dict(interval.run_states) or '--'}")
    print(f"    avg_cno_db_hz: {stat(interval.cno)}")
    print(f"    PVT status counts: {dict(interval.pvt_status) or '--'}")
    print(f"    observation distribution: {dict(interval.observations) or '--'}")
    print(f"    observation min/avg/max: {stat(interval.observation_values)}")
    print(f"    used PRNs top10: {dict(interval.used_prns.most_common(10)) or '--'}")
    print(f"    MUSIC primary in jammer range: {dict(interval.music_in_jammer) or '--'}")
    print(f"    MUSIC primary in bladeRF range: {dict(interval.music_in_bladerf) or '--'}")


if __name__ == "__main__":
    raise SystemExit(main())
