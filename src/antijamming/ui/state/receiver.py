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

_PRN_DISPLAY_HOLD_S = 8.0
_PRN_DISPLAY_HOLD_REASONS = {
    "missing_cno",
    "too_few_samples",
    "awaiting_nav",
}


@dataclass(frozen=True)
class ReceiverViewState:
    """Coherent operator projection derived from one GNSS bridge snapshot."""

    prn_entries: list[dict[str, object]]
    sky_entries: list[dict[str, object]]
    current_tracking_prns: list[int]
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

    def __init__(self) -> None:
        self.display_hold: dict[str, tuple[dict[str, object], float]] = {}

    def clear_display_hold(self) -> None:
        self.display_hold.clear()

    def build_view_state(
        self,
        gnss_snapshot: dict[str, object],
        *,
        now: float,
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

        prn_entries = self._apply_prn_display_hold(
            prn_entries,
            pvt_current,
            raw_used_in_fix_satellites,
            now=now,
        )

        current_tracking_prns = sorted(
            {
                prn
                for entry in prn_entries
                if str(entry.get("state", "")).lower() == "tracking"
                for prn in [valid_prn(entry.get("prn"))]
                if prn is not None
            }
        )
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
            prn_entries=prn_entries,
            sky_entries=projected_sky_entries,
            current_tracking_prns=current_tracking_prns,
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

    def _apply_prn_display_hold(
        self,
        prn_entries: list[dict[str, object]],
        pvt_current: bool,
        raw_used_in_fix_satellites: set[str],
        *,
        now: float,
    ) -> list[dict[str, object]]:
        if not pvt_current:
            self.display_hold.clear()
            return prn_entries

        entries_by_satellite = {
            sat_id: dict(entry)
            for entry in prn_entries
            for sat_id in [satellite_id(entry)]
            if sat_id != "--"
        }
        displayed_by_satellite = dict(entries_by_satellite)
        expired: list[str] = []

        for sat_id, entry in entries_by_satellite.items():
            state = str(entry.get("state", "")).lower()
            if state != "tracking":
                self.display_hold.pop(sat_id, None)
                continue
            if is_stable_tracking_entry(entry):
                self.display_hold[sat_id] = (dict(entry), now)
                continue

            held = self.display_hold.get(sat_id)
            if held is None:
                continue
            held_entry, held_at = held
            pvt_used = sat_id in raw_used_in_fix_satellites
            if not pvt_used and (now - held_at) > _PRN_DISPLAY_HOLD_S:
                expired.append(sat_id)
                continue
            reason = str(entry.get("cno_unstable_reason", ""))
            if pvt_used or reason in _PRN_DISPLAY_HOLD_REASONS:
                merged = dict(held_entry)
                merged["used_in_fix"] = pvt_used or bool(
                    entry.get("used_in_fix", held_entry.get("used_in_fix", False))
                )
                merged["display_hold_active"] = True
                merged["display_hold_reason"] = reason
                displayed_by_satellite[sat_id] = merged

        for sat_id, (held_entry, held_at) in list(self.display_hold.items()):
            pvt_used = sat_id in raw_used_in_fix_satellites
            if not pvt_used and (now - held_at) > _PRN_DISPLAY_HOLD_S:
                expired.append(sat_id)
                continue
            if sat_id not in displayed_by_satellite:
                merged = dict(held_entry)
                merged["display_hold_active"] = True
                merged["display_hold_reason"] = "missing_snapshot_entry"
                merged["used_in_fix"] = pvt_used or bool(held_entry.get("used_in_fix", False))
                displayed_by_satellite[sat_id] = merged

        for sat_id in expired:
            self.display_hold.pop(sat_id, None)

        return [
            displayed_by_satellite[sat_id]
            for sat_id in sorted(displayed_by_satellite, key=satellite_sort_key)
        ]


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
    "prn_monitor_entries",
    "satellite_id",
    "satellite_sort_key",
    "valid_float",
    "valid_int",
    "valid_prn",
]
