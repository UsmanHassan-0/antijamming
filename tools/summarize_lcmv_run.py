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
from typing import Iterable

LOG_PREFIX_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \| "
    r"(?P<level>\w+) \| (?P<msg>.*)$"
)
KV_RE = re.compile(
    r"(?P<key>[A-Za-z0-9_./-]+)=(?P<value>\[[^\]]*\]|\"[^\"]*\"|'[^']*'|\S+)"
)

CURRENT_LCMV_METHODS = {
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
    model_min_db: list[float] = field(default_factory=list)
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
    spatial_coherence_abs: list[float] = field(default_factory=list)
    spatial_principal_angle_deg: list[float] = field(default_factory=list)
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
    operator_event_path = logs / "operator_events.jsonl"
    if not operator_event_path.is_file():
        operator_event_path = logs / "operator_events.log"
    operator_events = parse_operator_events(operator_event_path)
    for ts, payload in operator_events:
        event = str(payload.get("event", ""))
        if event in {"lcmv_on", "lcmv_off"}:
            app.setdefault("toggles", []).append((ts, event == "lcmv_on"))
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
    for key in (
        "center_freq_hz",
        "sample_rate_sps",
        "rx_bandwidth_hz",
        "rx_gain_db",
        "channels",
    ):
        print(f"  {key}: {usrp.get(key, '--')}")
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


def parse_usrp_config(path: Path) -> dict[str, object]:
    cfg: dict[str, object] = {}
    for _ts, msg in iter_log(path):
        assign_regex(
            cfg, "center_freq_hz", msg, r"(?:freq|center_freq)=([0-9.]+)\s*MHz", 1e6
        )
        assign_regex(
            cfg, "sample_rate_sps", msg, r"(?:rate|sample_rate)=([0-9.]+)\s*Msps", 1e6
        )
        assign_regex(
            cfg, "rx_bandwidth_hz", msg, r"(?:rx_bw|bandwidth)=([0-9.]+)\s*MHz", 1e6
        )
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
        if event == "full_angle_analysis":
            events.append((ts, "analysis", payload))
    for ts, msg in iter_log(logs / "lcmv_pattern_absolute.jsonl"):
        payload = parse_json_line(msg)
        if payload:
            events.append((ts, "lcmv_pattern", payload))
    return sorted(events, key=lambda item: item[0])


def parse_spatial_events(logs: Path) -> list[tuple[datetime, dict[str, object]]]:
    events: list[tuple[datetime, dict[str, object]]] = []
    for ts, msg in iter_log(logs / "spatial_vector_diagnostics.jsonl"):
        payload = parse_json_line(msg)
        if payload and payload.get("event") == "spatial_vector_diagnostics":
            events.append((ts, payload))
    return sorted(events, key=lambda item: item[0])


def parse_calibration_logs(logs: Path) -> dict[str, object]:
    manifests: list[dict[str, object]] = []
    phase_diags: list[dict[str, object]] = []
    for _ts, msg in iter_log(logs / "app.log"):
        payload = parse_json_after(msg, "calibration_manifest ")
        if payload:
            manifests.append(payload)
    for _ts, msg in iter_log(logs / "phase_alignment.log"):
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
            append_float(
                interval.noise_tail_spread_db, data.get("noise_tail_spread_db")
            )
            append_float(
                interval.noise_tail_flatness_db, data.get("noise_tail_flatness_db")
            )
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
            for raw_prn in str(used_value).split(","):
                prn = raw_prn.strip()
                if prn and prn != "--":
                    interval.used_prns[prn] += 1
            append_float(
                interval.reduction_uniform_db,
                data.get("measured_output_reduction_vs_uniform_db"),
            )
            append_float(
                interval.reduction_raw_avg_db,
                data.get("measured_output_reduction_vs_raw_avg_channel_db"),
            )
            append_float(
                interval.reduction_raw_sum_db,
                data.get("measured_output_reduction_vs_raw_sum_channels_db"),
            )
            append_float(interval.raw_spread_db, data.get("raw_power_spread_db"))
            append_float(interval.cal_spread_db, data.get("cal_power_spread_db"))
        elif kind == "lcmv_pattern":
            append_float(
                interval.model_min_db, data.get("model_min_response_db")
            )
            out = data.get("output_metrics", {})
            if isinstance(out, dict):
                append_float(
                    interval.reduction_uniform_db,
                    out.get("measured_output_reduction_vs_uniform_db"),
                )
                append_float(
                    interval.reduction_raw_avg_db,
                    out.get("measured_output_reduction_vs_raw_avg_channel_db"),
                )
                append_float(
                    interval.reduction_raw_sum_db,
                    out.get("measured_output_reduction_vs_raw_sum_channels_db"),
                )
        elif kind == "analysis":
            lcmv = data.get("lcmv", {})
            if isinstance(lcmv, dict):
                summary = lcmv.get("lcmv_model_summary", {})
                if isinstance(summary, dict):
                    append_float(
                        interval.model_min_db,
                        summary.get("model_min_response_db"),
                    )
        elif kind == "spatial":
            append_float(
                interval.spatial_coherence_abs, data.get("ideal_measured_coherence_abs")
            )
            append_float(
                interval.spatial_principal_angle_deg,
                data.get("ideal_measured_principal_angle_deg"),
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
            result["clipping_suspected_count"] = (
                int(result["clipping_suspected_count"]) + 1
            )
    result["max_raw_peak_component"] = max(raw_peaks) if raw_peaks else None
    result["max_fifo_peak_component"] = max(fifo_peaks) if fifo_peaks else None
    return result


def parse_transport(path: Path) -> dict[str, object]:
    result = {"rx_overflows": 0, "rx_timeouts": 0}
    for _ts, msg in iter_log(path):
        kv = parse_kv(msg)
        if "rx_overflows" in kv:
            result["rx_overflows"] = max(
                result["rx_overflows"], int_or_zero(kv["rx_overflows"])
            )
        if "rx_timeouts" in kv:
            result["rx_timeouts"] = max(
                result["rx_timeouts"], int_or_zero(kv["rx_timeouts"])
            )
    return result


def iter_log(path: Path) -> Iterable[tuple[datetime, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            match = LOG_PREFIX_RE.match(line)
            if match:
                yield parse_ts(match.group("ts")), match.group("msg")
            else:
                yield datetime.min, line


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")


def parse_kv(message: str) -> dict[str, object]:
    return {
        match.group("key"): strip_value(match.group("value"))
        for match in KV_RE.finditer(message)
    }


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
    return f"avg={sum(values) / len(values):.2f} min={min(values):.2f} max={max(values):.2f} n={len(values)}"


def mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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
        while (
            marker_index < len(jammer_markers) and jammer_markers[marker_index][0] <= ts
        ):
            jammer_on = jammer_markers[marker_index][1]
            marker_index += 1
        if jammer_on is not True or not bool(
            payload.get("jammer_only_suppression_estimate_available", False)
        ):
            continue
        suppression = numeric_or_none(payload.get("jammer_only_suppression_db"))
        before = numeric_or_none(payload.get("jammer_only_power_before_uniform_linear"))
        after = numeric_or_none(payload.get("jammer_only_power_after_applied_linear"))
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
            "mean_per_snapshot_suppression_db": mean_or_none(runtime_suppression_db),
            "jammer_before_power_linear": mean_before,
            "jammer_after_power_linear": mean_after,
        }
    return {
        "available": False,
        "reason": "no current same-covariance jammer-only samples in marked window",
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
    print(
        f"  jammer_only_suppression_sample_count: {estimate.get('sample_count', '--')}"
    )
    print(
        "  jammer_only_suppression_estimate_db: "
        f"{numeric_or_none(estimate.get('suppression_db')):.2f}"
    )
    snapshot_mean = numeric_or_none(estimate.get("mean_per_snapshot_suppression_db"))
    if snapshot_mean is not None:
        print(f"  jammer_only_mean_per_snapshot_suppression_db: {snapshot_mean:.2f}")
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
        "array_geometry_manifest": contains_text(
            logs / "app.log", "array_geometry_manifest "
        ),
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
        "calibration_manifest": contains_text(logs / "app.log", "calibration_manifest "),
        "calibration_gain_effect_ch0_db": contains_text(
            logs / "phase_alignment.log",
            "calibration_gain_effect_ch0_db",
        )
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
    reduction_uniform: list[float] = []
    reduction_raw_avg: list[float] = []
    for _ts, payload in spatial_events:
        append_float(coherence, payload.get("ideal_measured_coherence_abs"))
        append_float(principal, payload.get("ideal_measured_principal_angle_deg"))
    for interval in intervals:
        reduction_uniform.extend(interval.reduction_uniform_db)
        reduction_raw_avg.extend(interval.reduction_raw_avg_db)
    print(f"  ideal_measured_coherence_abs: {stat(coherence)}")
    print(f"  ideal_measured_principal_angle_deg: {stat(principal)}")
    print(f"  measured_output_reduction_vs_uniform_db: {stat(reduction_uniform)}")
    print(
        f"  measured_output_reduction_vs_raw_avg_channel_db: {stat(reduction_raw_avg)}"
    )


def print_spatial_summary(events: list[tuple[datetime, dict[str, object]]]) -> None:
    """Report measured-vector evidence without recommending retired methods."""
    if not events:
        print("  --")
        return
    print(f"  events: {len(events)}")
    print("  warning: R is not jammer-only; u1 is not always jammer")
    print("  warning: total output reduction is not jammer-only suppression")
    print("  warning: desired-response and receiver evidence are required")
    methods: Counter[str] = Counter()
    valid: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    run_states: Counter[str] = Counter()
    prefix = "candidate_covariance_lcmv_measured_u1"
    metric_names = (
        "u1_component_reduction_vs_reference_db",
        "total_output_reduction_vs_reference_db",
        "desired_loss_vs_reference_db",
        "white_noise_gain_db",
        "noise_gain_vs_reference_db",
        "effective_js_improvement_u1_db",
        "effective_receiver_improvement_u1_db",
    )
    stats: dict[str, list[float]] = {name: [] for name in metric_names}
    diagnostics: dict[str, list[float]] = {
        name: [] for name in (
            "ideal_measured_coherence_abs",
            "ideal_measured_principal_angle_deg",
            "ideal_measured_mismatch_db",
            "measured_covariance_reduction_uniform_to_active_lcmv_db",
            "active_lcmv_to_u1_suppression_db",
        )
    }
    for _ts, payload in events:
        methods[str(payload.get("active_lcmv_method", "--"))] += 1
        run_states[str(payload.get("run_state_label", "--"))] += 1
        if f"{prefix}_valid" in payload:
            valid[str(bool(payload[f"{prefix}_valid"]))] += 1
        reason = payload.get(f"{prefix}_rejected_reason")
        if reason:
            rejected[str(reason)] += 1
        for name in metric_names:
            append_float(stats[name], payload.get(f"{prefix}_{name}"))
        for name in diagnostics:
            append_float(diagnostics[name], payload.get(name))
    print(f"  active_lcmv_method distribution: {dict(methods)}")
    print(f"  run_state_label distribution: {dict(run_states)}")
    print(f"  measured_u1_valid distribution: {dict(valid)}")
    print(f"  measured_u1_rejected distribution: {dict(rejected)}")
    for name, values in diagnostics.items():
        print(f"  {name}: {stat(values)}")
    print("  measured_u1_metrics:")
    for name, values in stats.items():
        print(f"    {name}: {stat(values)}")
    print("  No automatic method ranking: these diagnostics do not establish "
          "physical suppression, phase continuity, or successful PVT.")


def contains_text(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def print_interval(interval: IntervalStats) -> None:
    print(
        f"  {interval.name}: {format_ts(interval.start)} -> {format_ts(interval.end)}"
    )
    print(f"    MUSIC primary bearing: {stat(interval.doa)}")
    print(f"    peak_count distribution: {dict(interval.peak_counts) or '--'}")
    print(f"    source_est_gap distribution: {dict(interval.source_gap) or '--'}")
    print(f"    noise_tail_spread_db: {stat(interval.noise_tail_spread_db)}")
    print(f"    noise_tail_flatness_db: {stat(interval.noise_tail_flatness_db)}")
    print(
        f"    noise_tail_white_like distribution: {dict(interval.noise_tail_white_like) or '--'}"
    )
    print(f"    model_min_response_db: {stat(interval.model_min_db)}")
    print(
        f"    measured_output_reduction_vs_uniform_db: {stat(interval.reduction_uniform_db)}"
    )
    print(
        f"    measured_output_reduction_vs_raw_avg_channel_db: {stat(interval.reduction_raw_avg_db)}"
    )
    print(
        "    measured_output_reduction_vs_raw_sum_channels_db: "
        f"{stat(interval.reduction_raw_sum_db)}"
    )
    print(f"    raw_power_spread_db: {stat(interval.raw_spread_db)}")
    print(f"    cal_power_spread_db: {stat(interval.cal_spread_db)}")
    print(
        f"    spatial ideal_measured_coherence_abs: {stat(interval.spatial_coherence_abs)}"
    )
    print(
        "    spatial ideal_measured_principal_angle_deg: "
        f"{stat(interval.spatial_principal_angle_deg)}"
    )
    print(
        f"    spatial active_lcmv_null_method: {dict(interval.spatial_active_methods) or '--'}"
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
