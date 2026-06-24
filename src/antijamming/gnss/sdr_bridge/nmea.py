"""NMEA sentence parsing for PVT freshness and satellite geometry."""

from __future__ import annotations

import time

from antijamming.gnss.constellations import normalize_constellation

from .models import _constellation_from_token, _sat_constellation, _sat_key, _sat_public_fields

class NmeaMixin:
    def _handle_nmea_line(self, line: str) -> None:
        if not line.startswith("$"):
            return
        payload = line.split("*", 1)[0]
        fields = payload.split(",")
        if not fields:
            return
        constellation = self._nmea_constellation(fields[0])
        sentence = fields[0][-3:]
        if sentence == "GSV":
            if constellation is None:
                return
            self._handle_gsv_fields(fields, constellation)
        elif sentence == "GSA":
            if constellation is None:
                return
            self._handle_gsa_fields(fields, constellation)
        elif sentence in {"GGA", "RMC", "GLL"}:
            self._handle_nmea_utc_fields(fields, sentence)
            observation_count: int | None = None
            if sentence == "GGA" and len(fields) > 7:
                try:
                    observation_count = int(fields[7])
                except (TypeError, ValueError):
                    observation_count = None
            self._mark_pvt_observed(observation_count=observation_count)

    def _handle_nmea_utc_fields(self, fields: list[str], sentence: str) -> None:
        if len(fields) <= 1:
            return
        utc_text = str(fields[1]).strip()
        utc_s = self._parse_nmea_utc_seconds(utc_text)
        if utc_s is None:
            return
        previous_s = self._last_nmea_utc_s
        previous_text = self._last_nmea_utc_text
        if previous_s is not None and previous_text and (utc_s + 1.0) < previous_s:
            # A live receiver should not jump backward inside a run. In this
            # lab setup it usually means the replayed TX file looped.
            self._handoff_log.warning(
                "GNSS NMEA UTC moved backward: sentence=%s previous_utc=%s current_utc=%s "
                "delta_s=%.2f; replay/TX file loop or receiver time reset likely.",
                sentence,
                previous_text,
                utc_text,
                utc_s - previous_s,
            )
        self._last_nmea_utc_s = utc_s
        self._last_nmea_utc_text = utc_text

    @staticmethod
    def _parse_nmea_utc_seconds(value: str) -> float | None:
        text = str(value).strip()
        if len(text) < 6:
            return None
        try:
            hours = int(text[0:2])
            minutes = int(text[2:4])
            seconds = float(text[4:])
        except ValueError:
            return None
        if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0.0 <= seconds < 60.0):
            return None
        return float(hours * 3600 + minutes * 60) + seconds

    def _nmea_constellation(self, talker_sentence: str) -> str | None:
        talker = str(talker_sentence).lstrip("$")[:2].upper()
        if talker == "GN":
            return "gps"
        return normalize_constellation(talker, default=None)

    def _mark_pvt_observed(
        self,
        *,
        observation_count: int | None = None,
        now: float | None = None,
    ) -> None:
        with self._state_lock:
            self._mark_pvt_observed_locked(observation_count=observation_count, now=now)

    def _mark_pvt_observed_locked(
        self,
        *,
        observation_count: int | None = None,
        now: float | None = None,
    ) -> None:
        self._pvt_output_seen = True
        self._pvt_observed_monotonic_s = time.monotonic() if now is None else now
        if observation_count is not None:
            self._pvt_observation_count = max(0, int(observation_count))

    def _handle_gsv_fields(self, fields: list[str], constellation: object = "gps") -> None:
        if len(fields) < 4:
            return
        now = time.monotonic()
        for idx in range(4, len(fields), 4):
            if idx + 3 >= len(fields):
                break
            prn_text, el_text, az_text, snr_text = fields[idx : idx + 4]
            try:
                prn = int(prn_text)
                el_deg = float(el_text)
                az_deg = float(az_text)
            except (TypeError, ValueError):
                continue
            snr_db_hz: float | None = None
            try:
                if snr_text:
                    snr_db_hz = float(snr_text)
            except ValueError:
                snr_db_hz = None
            with self._state_lock:
                sat_key = _sat_key(constellation, prn)
                geometry = self._sat_geometry_by_prn.get(sat_key, {})
                geometry.update(
                    {
                        **_sat_public_fields(sat_key),
                        "az_deg": az_deg,
                        "el_deg": el_deg,
                        "observed_monotonic_s": now,
                    }
                )
                if snr_db_hz is not None:
                    geometry["snr_db_hz"] = snr_db_hz
                self._sat_geometry_by_prn[sat_key] = geometry

    def _handle_gsa_fields(self, fields: list[str], constellation: object = "gps") -> None:
        normalized_constellation = _constellation_from_token(constellation)
        used_prns: set[_SatKey] = set()
        for prn_text in fields[3:15]:
            try:
                if prn_text:
                    used_prns.add(_sat_key(normalized_constellation, int(prn_text)))
            except ValueError:
                continue
        with self._state_lock:
            self._used_in_fix_prns = {
                key
                for key in self._used_in_fix_prns
                if _sat_constellation(key) != normalized_constellation
            } | used_prns
            self._used_in_fix_observed_monotonic_s = time.monotonic()
