"""Receiver operator-view state models and GNSS snapshot projection."""

from __future__ import annotations

from dataclasses import dataclass
import math

from antijamming.gnss.constellations import (
    normalize_constellation,
    satellite_label,
    satellite_label_constellation,
    satellite_sort_key as constellation_sort_key,
)

@dataclass(frozen=True)
class ReceiverViewState:
    """Coherent operator projection derived from one GNSS bridge snapshot."""

    prn_entries: list[dict[str, object]]
    sky_entries: list[dict[str, object]]
    current_tracking_prns: list[int]
    current_tracking_satellite_ids: list[str]
    stable_prns: list[int]
    stable_satellite_ids: list[str]
    current_used_in_pvt_prns: list[int]
    current_used_in_pvt_satellites: list[str]
    raw_used_in_fix_prns: list[int]
    raw_used_in_fix_satellites: list[str]
    fresh_geometry_prns: list[int]
    tracking_without_geometry: list[int]
    pvt_current: bool
    used_for_pvt_count: int


class ReceiverProjection:
    """Project GNSS bridge snapshots into operator-facing receiver state."""

    def build_view_state(
        self,
        gnss_snapshot: dict[str, object],
    ) -> ReceiverViewState:
        prns = gnss_snapshot.get("prns", [])
        sky_prns = gnss_snapshot.get("sky_prns", [])
        prn_entries = prn_monitor_entries(prns if isinstance(prns, list) else [])
        raw_sky_entries = sky_prns if isinstance(sky_prns, list) else []
        pvt_current = bool(
            gnss_snapshot.get("pvt_current", gnss_snapshot.get("pvt_output_seen", False))
        )

        raw_used_in_fix_prns: set[int] = set()
        raw_used_in_fix_satellites: set[str] = set()
        if pvt_current:
            raw_used_in_fix_prns.update(
                prn
                for entry in prn_entries
                if bool(entry.get("used_in_fix", False))
                for prn in [valid_prn(entry.get("prn"))]
                if prn is not None
            )
            raw_used_in_fix_satellites.update(
                sat_id
                for entry in prn_entries
                if bool(entry.get("used_in_fix", False))
                for sat_id in [satellite_id(entry)]
                if sat_id != "--"
            )
            raw_used_in_fix_prns.update(
                prn
                for entry in raw_sky_entries
                if isinstance(entry, dict) and bool(entry.get("used_in_fix", False))
                for prn in [valid_prn(entry.get("prn"))]
                if prn is not None
            )
            raw_used_in_fix_satellites.update(
                sat_id
                for entry in raw_sky_entries
                if isinstance(entry, dict) and bool(entry.get("used_in_fix", False))
                for sat_id in [satellite_id(entry)]
                if sat_id != "--"
            )

        # Count only the receiver's current tracking state. Never turn a
        # previously tracked satellite into a current one.
        current_tracking_satellite_ids = {
            sat_id
            for entry in prn_entries
            if str(entry.get("state", "")).lower() == "tracking"
            for sat_id in [satellite_id(entry)]
            if sat_id != "--"
        }
        current_tracking_prns = sorted(
            {
                prn
                for entry in prn_entries
                if str(entry.get("state", "")).lower() == "tracking"
                for prn in [valid_prn(entry.get("prn"))]
                if prn is not None
            }
        )

        # The C/N0 chart is a measurement chart, not a channel-state chart.
        # Admit a currently tracking satellite as soon as GNSS-SDR supplies a
        # real tracking C/N0.  Stability and PVT use affect qualification and
        # colour, but must not delay the first honest C/N0 bar.  Never carry a
        # prior value through a missing sample: that produced labelled PRNs
        # with a "--" bar and made a lost or pending channel look current.
        stable_prns = sorted(
            {
                prn
                for entry in prn_entries
                if is_stable_tracking_entry(entry)
                for prn in [valid_prn(entry.get("prn"))]
                if prn is not None
            }
        )
        stable_satellite_ids = {
            sat_id
            for entry in prn_entries
            if is_stable_tracking_entry(entry)
            for sat_id in [satellite_id(entry)]
            if sat_id != "--"
        }
        tracking_cno_entries = [
            entry for entry in prn_entries if is_current_tracking_cno_entry(entry)
        ]

        current_used_in_pvt_prns = set(stable_prns) & raw_used_in_fix_prns
        current_used_in_pvt_satellites = stable_satellite_ids & raw_used_in_fix_satellites
        used_for_pvt_count = len(raw_used_in_fix_satellites) if pvt_current else 0
        pvt_observation_count = valid_int(gnss_snapshot.get("pvt_observation_count"))
        if pvt_current and pvt_observation_count is not None and pvt_observation_count > 0:
            used_for_pvt_count = pvt_observation_count

        for entry in prn_entries:
            prn = valid_prn(entry.get("prn"))
            if prn is not None:
                entry["used_in_fix"] = satellite_id(entry) in current_used_in_pvt_satellites

        fresh_geometry_by_prn: dict[int, dict[str, object]] = {}
        fresh_geometry_by_satellite: dict[str, dict[str, object]] = {}
        for raw_entry in raw_sky_entries:
            if not isinstance(raw_entry, dict):
                continue
            prn = valid_prn(raw_entry.get("prn"))
            if prn is None:
                continue
            if valid_float(raw_entry.get("az_deg")) is None:
                continue
            if valid_float(raw_entry.get("el_deg")) is None:
                continue
            fresh_geometry_by_prn[prn] = dict(raw_entry)
            sat_id = satellite_id(raw_entry)
            if sat_id == "--":
                continue
            fresh_geometry_by_satellite[sat_id] = dict(raw_entry)

        projected_sky_entries: list[dict[str, object]] = []
        for sat_id in sorted(fresh_geometry_by_satellite, key=satellite_sort_key):
            if sat_id not in stable_satellite_ids:
                continue
            entry = dict(fresh_geometry_by_satellite[sat_id])
            entry["state"] = "tracking"
            entry["used_in_fix"] = sat_id in current_used_in_pvt_satellites
            projected_sky_entries.append(entry)

        fresh_geometry_prns = sorted(fresh_geometry_by_prn)
        tracking_without_geometry = sorted(set(current_tracking_prns) - set(fresh_geometry_prns))

        return ReceiverViewState(
            prn_entries=tracking_cno_entries,
            sky_entries=projected_sky_entries,
            current_tracking_prns=current_tracking_prns,
            current_tracking_satellite_ids=sorted(
                current_tracking_satellite_ids,
                key=satellite_sort_key,
            ),
            stable_prns=stable_prns,
            stable_satellite_ids=sorted(stable_satellite_ids, key=satellite_sort_key),
            current_used_in_pvt_prns=sorted(current_used_in_pvt_prns),
            current_used_in_pvt_satellites=sorted(
                current_used_in_pvt_satellites,
                key=satellite_sort_key,
            ),
            raw_used_in_fix_prns=sorted(raw_used_in_fix_prns),
            raw_used_in_fix_satellites=sorted(
                raw_used_in_fix_satellites,
                key=satellite_sort_key,
            ),
            fresh_geometry_prns=fresh_geometry_prns,
            tracking_without_geometry=tracking_without_geometry,
            pvt_current=pvt_current,
            used_for_pvt_count=used_for_pvt_count,
        )

def prn_monitor_entries(prns: list[object]) -> list[dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    for raw_entry in prns:
        if not isinstance(raw_entry, dict):
            continue
        prn = valid_prn(raw_entry.get("prn"))
        if prn is None:
            continue
        state = str(raw_entry.get("state", "")).lower()
        if state not in {"searched", "assigned", "acquired", "tracking", "lost"}:
            continue
        entry = dict(raw_entry)
        sat_id = satellite_id(entry)
        if sat_id == "--":
            continue
        entries[sat_id] = entry
    return [entries[sat_id] for sat_id in sorted(entries, key=satellite_sort_key)]


def is_stable_tracking_entry(entry: dict[str, object]) -> bool:
    if str(entry.get("state", "")).lower() != "tracking":
        return False
    cno = valid_float(entry.get("cno_db_hz"))
    return bool(entry.get("cno_stable", False)) and cno is not None and cno > 0.0


def is_current_tracking_cno_entry(entry: dict[str, object]) -> bool:
    """Return true when a live tracking C/N0 can be plotted honestly."""

    if str(entry.get("state", "")).lower() != "tracking":
        return False
    cno = valid_float(entry.get("cno_db_hz"))
    return cno is not None and cno > 0.0


def valid_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def valid_prn(value: object) -> int | None:
    try:
        prn = int(value)
    except (TypeError, ValueError):
        return None
    return prn if prn > 0 else None


def valid_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def satellite_id(entry: dict[str, object]) -> str:
    explicit = entry.get("satellite_id")
    if explicit:
        value = str(explicit)
        return value if satellite_label_constellation(value) is not None else "--"
    prn = valid_prn(entry.get("prn"))
    if prn is None:
        return "--"
    constellation = normalize_constellation(
        entry.get("constellation", entry.get("system", entry.get("gnss", "gps")))
    )
    label = satellite_label(constellation, prn) if constellation is not None else None
    return label or "--"


def satellite_sort_key(sat_id: str) -> tuple[int, int, str]:
    return constellation_sort_key(sat_id)


__all__ = [
    "ReceiverProjection",
    "ReceiverViewState",
    "is_stable_tracking_entry",
    "is_current_tracking_cno_entry",
    "prn_monitor_entries",
    "satellite_id",
    "satellite_sort_key",
    "valid_float",
    "valid_int",
    "valid_prn",
]
