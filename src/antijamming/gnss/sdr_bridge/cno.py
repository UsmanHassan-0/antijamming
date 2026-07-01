"""C/N0 stability history and display-state helpers."""

from __future__ import annotations

import math

import numpy as np

from .constants import (
    PRN_CARRIER_LOCK_THRESHOLD,
    PRN_CNO_MAX_PEAK_TO_PEAK_DB,
    PRN_CNO_MAX_STDEV_DB,
    PRN_CNO_MIN_STABLE_DB_HZ,
    PRN_CNO_REQUIRED_STABLE_WINDOWS,
    PRN_CNO_STABILITY_WINDOW,
)
from .models import _SatKey, _sat_key

class CnoMixin:
    def _clear_cno_history(self, channel: int, sat_key: _SatKey) -> None:
        self._tracking_cno_history.pop((channel, sat_key), None)
        self._tracking_cno_stable_windows.pop((channel, sat_key), None)

    def _append_cno_sample(self, channel: int, sat_key: _SatKey, cno_db_hz: float) -> None:
        if not math.isfinite(cno_db_hz):
            return
        key = (channel, sat_key)
        history = self._tracking_cno_history.setdefault(key, [])
        history.append(float(cno_db_hz))
        del history[:-PRN_CNO_STABILITY_WINDOW]
        if self._cno_window_passes(history):
            self._tracking_cno_stable_windows[key] = (
                self._tracking_cno_stable_windows.get(key, 0) + 1
            )
        else:
            self._tracking_cno_stable_windows[key] = 0

    def _set_runtime_cno_sample(
        self,
        channel: int,
        constellation: object,
        prn: int,
        cno_db_hz: float,
    ) -> None:
        """Use operator console NAV C/N0 as a current tracking sample."""
        if not math.isfinite(cno_db_hz):
            return
        sat_key = _sat_key(constellation, prn)
        with self._state_lock:
            state_entry = self._prn_states.get(sat_key)
            if (
                state_entry is None
                or state_entry.get("state") != "tracking"
                or int(state_entry.get("channel", -1)) != channel
            ):
                return
            previous_dump_prn = self._tracking_prn_by_channel.get(channel)
            if previous_dump_prn is not None and previous_dump_prn != prn:
                previous_key = self._channel_prn.get(channel)
                if previous_key is not None:
                    self._clear_cno_history(channel, previous_key)
                self._clear_cno_history(channel, sat_key)
            self._tracking_prn_by_channel[channel] = prn
            self._tracking_cn0_by_channel[channel] = float(cno_db_hz)
            self._append_cno_sample(channel, sat_key, float(cno_db_hz))

    def _cno_window_stats(self, history: list[float]) -> tuple[float | None, float | None, float | None]:
        if not history:
            return None, None, None
        values = np.asarray(history, dtype=np.float64)
        smoothed = float(np.median(values))
        stdev = float(np.std(values)) if values.size >= 2 else None
        peak_to_peak = float(np.ptp(values)) if values.size >= 2 else None
        return smoothed, stdev, peak_to_peak

    def _cno_window_passes(self, history: list[float]) -> bool:
        sample_count = len(history)
        smoothed, stdev, peak_to_peak = self._cno_window_stats(history)
        return bool(
            sample_count >= PRN_CNO_STABILITY_WINDOW
            and smoothed is not None
            and smoothed >= PRN_CNO_MIN_STABLE_DB_HZ
            and stdev is not None
            and stdev <= PRN_CNO_MAX_STDEV_DB
            and peak_to_peak is not None
            and peak_to_peak <= PRN_CNO_MAX_PEAK_TO_PEAK_DB
        )

    def _cno_stability_fields(
        self,
        channel: int,
        sat_key: _SatKey,
        *,
        latest_cno_seen: bool,
        carrier_lock_test: float | None,
        telemetry_confirmed: bool = False,
        pvt_used: bool = False,
    ) -> dict[str, object]:
        key = (channel, sat_key)
        history = self._tracking_cno_history.get(key, [])
        sample_count = len(history)
        smoothed, stdev, peak_to_peak = self._cno_window_stats(history)
        latest_cno = history[-1] if history else None
        carrier_lock_valid = carrier_lock_test is not None and math.isfinite(carrier_lock_test)
        stable_window_count = self._tracking_cno_stable_windows.get(key, 0)
        cno_history_stable = (
            stable_window_count >= PRN_CNO_REQUIRED_STABLE_WINDOWS
            and self._cno_window_passes(history)
        )

        if not latest_cno_seen:
            self._tracking_cno_stable_windows[key] = 0
            stable_window_count = 0
            stable = False
            reason = "missing_cno"
        elif latest_cno is None:
            stable = False
            reason = "missing_cno"
        elif pvt_used and latest_cno > 0.0:
            # A current PVT solution is stronger display evidence than this
            # operator-facing stability filter; retain its plotted satellite.
            stable = True
            reason = ""
        elif (
            latest_cno < PRN_CNO_MIN_STABLE_DB_HZ
            or (smoothed is not None and smoothed < PRN_CNO_MIN_STABLE_DB_HZ)
        ):
            stable = False
            reason = "low_cno"
        elif sample_count < PRN_CNO_STABILITY_WINDOW:
            stable = False
            reason = "too_few_samples"
        elif not cno_history_stable:
            stable = False
            reason = "high_variance"
        elif not telemetry_confirmed:
            stable = False
            reason = "awaiting_nav"
        else:
            stable = True
            reason = ""
        stable_window_count = max(
            stable_window_count,
            PRN_CNO_REQUIRED_STABLE_WINDOWS if stable and pvt_used else 0,
        )

        return {
            "cno_smoothed_db_hz": smoothed,
            "cno_sample_count": sample_count,
            "cno_stdev_db": stdev,
            "cno_peak_to_peak_db": peak_to_peak,
            "cno_stable_window_count": stable_window_count,
            "cno_required_stable_windows": PRN_CNO_REQUIRED_STABLE_WINDOWS,
            "cno_history_stable": cno_history_stable,
            "telemetry_confirmed": bool(telemetry_confirmed),
            "carrier_lock_test": carrier_lock_test if carrier_lock_valid else None,
            "carrier_lock_threshold": PRN_CARRIER_LOCK_THRESHOLD,
            "cno_stable": stable,
            "cno_unstable_reason": reason,
        }
