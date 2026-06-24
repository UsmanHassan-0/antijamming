"""Shared observable snapshot helpers for GNSS-SDR monitor state."""

from __future__ import annotations

from .models import (
    _SatKey,
    _sat_constellation,
    _sat_label,
    _sat_prn,
    _sat_public_fields,
)


class ObservableStateMixin:
    def _valid_positive_int(self, value: object) -> int | None:
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _sat_key_for_observable_prn_locked(self, prn: int) -> _SatKey | None:
        candidates = [
            sat_key
            for sat_key, entry in self._prn_states.items()
            if _sat_prn(sat_key) == prn
            and str(entry.get("state", "")).lower() in {"tracking", "acquired", "assigned"}
        ]
        if len(candidates) == 1:
            return candidates[0]
        gps_candidates = [sat_key for sat_key in candidates if _sat_constellation(sat_key) == "gps"]
        if len(gps_candidates) == 1:
            return gps_candidates[0]
        if candidates:
            return None
        return prn

    def _observable_prn_fields(self, observable: dict[str, object]) -> dict[str, object]:
        return self._prefixed_synchro_fields(
            observable,
            prefix="observable",
            aliases={
                "rx_time": "observable_rx_time_s",
                "tow_s": "observable_tow_s",
                "carrier_doppler_hz": "observable_doppler_hz",
                "carrier_phase_cycles": "observable_carrier_phase_cycles",
                "carrier_phase_rads": "observable_carrier_phase_rads",
                "pseudorange_m": "observable_pseudorange_m",
                "cn0_db_hz": "observable_cno_db_hz",
                "valid_pseudorange": "observable_valid_pseudorange",
            },
        )

    def _tracking_monitor_prn_fields(self, tracking: dict[str, object]) -> dict[str, object]:
        return self._prefixed_synchro_fields(
            tracking,
            prefix="tracking_monitor",
            aliases={
                "prn": "tracking_monitor_prn",
                "channel": "tracking_monitor_channel",
                "rx_time": "tracking_monitor_rx_time_s",
                "tow_s": "tracking_monitor_tow_s",
                "carrier_doppler_hz": "tracking_monitor_doppler_hz",
                "carrier_phase_rads": "tracking_monitor_carrier_phase_rads",
                "pseudorange_m": "tracking_monitor_pseudorange_m",
                "cn0_db_hz": "tracking_monitor_cno_db_hz",
                "valid_symbol_output": "tracking_monitor_valid_symbol_output",
                "valid_word": "tracking_monitor_valid_word",
                "valid_pseudorange": "tracking_monitor_valid_pseudorange",
            },
        )

    def _prefixed_synchro_fields(
        self,
        entry: dict[str, object],
        *,
        prefix: str,
        aliases: dict[str, str],
    ) -> dict[str, object]:
        fields: dict[str, object] = {}
        for key, value in entry.items():
            if isinstance(value, (str, int, float, bool)):
                fields[f"{prefix}_{key}"] = value
        for src, dest in aliases.items():
            value = entry.get(src)
            if isinstance(value, (str, int, float, bool)):
                fields[dest] = value
        return fields

    def _public_observable_entry(
        self,
        sat_key: _SatKey,
        observable: dict[str, object],
    ) -> dict[str, object]:
        entry = {**_sat_public_fields(sat_key), **observable}
        entry["satellite_id"] = _sat_label(sat_key)
        return entry
