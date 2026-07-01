"""Receiver snapshot assembly, freshness checks, and snapshot timing."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .constants import (
    PVT_ACCURACY_TIMEOUT_S,
    SKY_GEOMETRY_TIMEOUT_S,
    USED_IN_FIX_TIMEOUT_S,
)
from .models import (
    _SatKey,
    _sat_label,
    _sat_prn,
    _sat_public_fields,
    _sat_sort_key,
)

class SnapshotMixin:
    def snapshot(self) -> dict[str, object]:
        # Live receiver state is updated by GNSS-SDR stdout/glog parsing and the
        # localhost UDP monitor threads. Snapshot construction must not poll
        # growing dump/NMEA files.
        snapshot_t0 = time.monotonic()
        now = time.monotonic()
        state_t0 = time.monotonic()
        with self._state_lock:
            fresh_geometry = self._fresh_sky_geometry(now)
            used_in_fix_prns = self._fresh_used_in_fix_prns(now)
            pvt_current = self._pvt_is_current(now)
            pvt_observation_count = self._pvt_observation_count if pvt_current else None
            latest_accuracy = dict(self._latest_accuracy) if self._accuracy_is_current(now) else {}
            latest_observables = {
                sat_key: dict(observable)
                for sat_key, observable in self._latest_observables_by_prn.items()
            }
            latest_tracking_monitor = {
                sat_key: dict(tracking)
                for sat_key, tracking in self._latest_tracking_monitor_by_prn.items()
            }
            stale_reason = None if pvt_current else ("no_pvt" if not self._pvt_output_seen else "pvt_stale")
            prns: list[dict[str, object]] = []
            for sat_key, entry in sorted(self._prn_states.items(), key=lambda item: _sat_sort_key(item[0])):
                item = dict(entry)
                item.update(_sat_public_fields(sat_key))
                channel = int(item.get("channel", -1))
                tracking_monitor_prn = self._tracking_prn_by_channel.get(channel)
                if tracking_monitor_prn is not None:
                    item["tracking_monitor_prn"] = tracking_monitor_prn
                cno_db_hz = self._tracking_cn0_by_channel.get(channel)
                carrier_lock_test = self._tracking_carrier_lock_by_channel.get(channel)
                item_prn = _sat_prn(sat_key)
                if (
                    item.get("state") == "tracking"
                    and cno_db_hz is not None
                    and tracking_monitor_prn == item_prn
                ):
                    # Attach C/N0 only when the tracking monitor PRN still matches
                    # the channel assignment. This prevents stale C/N0 from a
                    # previous PRN making a new tracking channel look stable.
                    item["cno_db_hz"] = cno_db_hz
                    if carrier_lock_test is not None:
                        item["carrier_lock_test"] = carrier_lock_test
                    item.update(
                        self._cno_stability_fields(
                            channel,
                            sat_key,
                            latest_cno_seen=True,
                            carrier_lock_test=carrier_lock_test,
                            telemetry_confirmed=bool(item.get("telemetry_confirmed", False)),
                            pvt_used=sat_key in used_in_fix_prns,
                        )
                    )
                elif item.get("state") == "tracking":
                    item.update(
                        self._cno_stability_fields(
                            channel,
                            sat_key,
                            latest_cno_seen=False,
                            carrier_lock_test=None,
                            telemetry_confirmed=bool(item.get("telemetry_confirmed", False)),
                            pvt_used=sat_key in used_in_fix_prns,
                        )
                    )
                geometry = fresh_geometry.get(sat_key)
                if geometry is not None:
                    item.update(geometry)
                observable = latest_observables.get(sat_key)
                if observable is not None:
                    item.update(self._observable_prn_fields(observable))
                tracking_monitor = latest_tracking_monitor.get(sat_key)
                if tracking_monitor is not None:
                    item.update(self._tracking_monitor_prn_fields(tracking_monitor))
                item["used_in_fix"] = sat_key in used_in_fix_prns
                prns.append(item)
            tracking_keys = sorted(
                (sat_key for sat_key, entry in self._prn_states.items() if entry["state"] == "tracking"),
                key=_sat_sort_key,
            )
            acquired_keys = sorted(
                (sat_key for sat_key, entry in self._prn_states.items() if entry["state"] == "acquired"),
                key=_sat_sort_key,
            )
            assigned_keys = sorted(
                (sat_key for sat_key, entry in self._prn_states.items() if entry["state"] == "assigned"),
                key=_sat_sort_key,
            )
            lost_keys = sorted(
                (sat_key for sat_key, entry in self._prn_states.items() if entry["state"] == "lost"),
                key=_sat_sort_key,
            )
            tracking = [_sat_prn(sat_key) for sat_key in tracking_keys]
            acquired = [_sat_prn(sat_key) for sat_key in acquired_keys]
            assigned = [_sat_prn(sat_key) for sat_key in assigned_keys]
            lost = [_sat_prn(sat_key) for sat_key in lost_keys]
            receiver_time_s = self._receiver_time_s
            pvt_output_seen = bool(self._pvt_output_seen)
            nmea_tty_line_count = int(self._nmea_tty_line_count)
            nmea_tty_last_monotonic_s = self._nmea_tty_last_monotonic_s
            nmea_tty_age_s = (
                max(0.0, now - nmea_tty_last_monotonic_s)
                if nmea_tty_last_monotonic_s is not None
                else None
            )
            nmea_tty_devname = self._nmea_tty_path
            sky_prns: list[dict[str, object]] = []
            for sat_key, geometry in sorted(fresh_geometry.items(), key=lambda item: _sat_sort_key(item[0])):
                # Skyplot placement requires geometry from GSV/KML/NMEA sources.
                # Tracking-only PRNs without az/el are reported elsewhere, not
                # plotted at invented positions.
                entry = {**_sat_public_fields(sat_key), **geometry, "used_in_fix": sat_key in used_in_fix_prns}
                state_entry = self._prn_states.get(sat_key)
                if state_entry is not None:
                    entry["state"] = state_entry.get("state", "visible")
                    entry["channel"] = state_entry.get("channel")
                else:
                    entry["state"] = "visible"
                sky_prns.append(entry)
            tracking_cno_values = [
                float(entry["cno_db_hz"])
                for entry in prns
                if entry.get("state") == "tracking" and isinstance(entry.get("cno_db_hz"), (int, float))
            ]
            stable_tracking_prns = sorted(
                int(entry["prn"])
                for entry in prns
                if entry.get("state") == "tracking"
                and isinstance(entry.get("cno_db_hz"), (int, float))
                and bool(entry.get("cno_stable", False))
            )
            stable_tracking_satellites = [
                str(entry.get("satellite_id", f"G{int(entry['prn']):02d}"))
                for entry in prns
                if entry.get("state") == "tracking"
                and isinstance(entry.get("cno_db_hz"), (int, float))
                and bool(entry.get("cno_stable", False))
            ]
            pending_tracking_prns = sorted(
                int(entry["prn"])
                for entry in prns
                if entry.get("state") == "tracking"
                and not bool(entry.get("cno_stable", False))
                and str(entry.get("cno_unstable_reason", ""))
                in {"missing_cno", "too_few_samples", "awaiting_nav"}
            )
            pending_tracking_satellites = [
                str(entry.get("satellite_id", f"G{int(entry['prn']):02d}"))
                for entry in prns
                if entry.get("state") == "tracking"
                and not bool(entry.get("cno_stable", False))
                and str(entry.get("cno_unstable_reason", ""))
                in {"missing_cno", "too_few_samples", "awaiting_nav"}
            ]
            unstable_tracking_prns = sorted(
                int(entry["prn"])
                for entry in prns
                if entry.get("state") == "tracking"
                and not bool(entry.get("cno_stable", False))
                and str(entry.get("cno_unstable_reason", ""))
                in {"low_cno", "high_variance"}
            )
            unstable_tracking_satellites = [
                str(entry.get("satellite_id", f"G{int(entry['prn']):02d}"))
                for entry in prns
                if entry.get("state") == "tracking"
                and not bool(entry.get("cno_stable", False))
                and str(entry.get("cno_unstable_reason", ""))
                in {"low_cno", "high_variance"}
            ]
            observables = [
                self._public_observable_entry(sat_key, observable)
                for sat_key, observable in sorted(
                    latest_observables.items(),
                    key=lambda item: _sat_sort_key(item[0]),
                )
            ]
            tracking_monitor_entries = [
                self._public_observable_entry(sat_key, tracking)
                for sat_key, tracking in sorted(
                    latest_tracking_monitor.items(),
                    key=lambda item: _sat_sort_key(item[0]),
                )
            ]
            observable_cno_values = [
                float(observable["cn0_db_hz"])
                for observable in observables
                if isinstance(observable.get("cn0_db_hz"), (int, float))
            ]
            valid_observables_count = sum(
                1 for observable in observables if bool(observable.get("valid_pseudorange", False))
            )
            used_in_fix_keys = sorted(used_in_fix_prns, key=_sat_sort_key)
            used_in_fix_prn_list = [_sat_prn(sat_key) for sat_key in used_in_fix_keys]
            used_in_fix_satellites = [_sat_label(sat_key) for sat_key in used_in_fix_keys]
        self._record_snapshot_timing("snapshot_state_build", time.monotonic() - state_t0)
        io_t0 = time.monotonic()
        output_io_metrics = self._refresh_output_io_metrics(now)
        self._record_snapshot_timing("snapshot_output_io", time.monotonic() - io_t0)
        self._record_snapshot_timing("snapshot_total", time.monotonic() - snapshot_t0)
        return {
            "prns": prns,
            "tracking_prns": tracking,
            "tracking_satellites": [_sat_label(sat_key) for sat_key in tracking_keys],
            "stable_tracking_prns": stable_tracking_prns,
            "stable_tracking_satellites": stable_tracking_satellites,
            "pending_tracking_prns": pending_tracking_prns,
            "pending_tracking_satellites": pending_tracking_satellites,
            "unstable_tracking_prns": unstable_tracking_prns,
            "unstable_tracking_satellites": unstable_tracking_satellites,
            "acquired_prns": acquired,
            "acquired_satellites": [_sat_label(sat_key) for sat_key in acquired_keys],
            "assigned_prns": assigned,
            "assigned_satellites": [_sat_label(sat_key) for sat_key in assigned_keys],
            "lost_prns": lost,
            "lost_satellites": [_sat_label(sat_key) for sat_key in lost_keys],
            "tracking_count": len(tracking),
            "acquired_count": len(acquired),
            "assigned_count": len(assigned),
            "lost_count": len(lost),
            "receiver_time_s": receiver_time_s,
            "pvt_output_seen": pvt_output_seen,
            "pvt_current": pvt_current,
            "pvt_observation_count": pvt_observation_count,
            "accuracy": latest_accuracy,
            "sky_prns": sky_prns,
            "sky_geometry_count": len(sky_prns),
            "used_in_fix_count": len(used_in_fix_prns),
            "used_in_fix_prns": used_in_fix_prn_list,
            "used_in_fix_satellites": used_in_fix_satellites,
            "used_in_fix_source": "nmea_gsa",
            "nmea_tty_devname": nmea_tty_devname,
            "nmea_tty_line_count": nmea_tty_line_count,
            "nmea_tty_age_s": nmea_tty_age_s,
            "observables": observables,
            "observables_count": len(observables),
            "valid_observables_count": valid_observables_count,
            "avg_observable_cno_db_hz": (
                float(np.mean(observable_cno_values)) if observable_cno_values else None
            ),
            "tracking_monitor": tracking_monitor_entries,
            "tracking_monitor_count": len(tracking_monitor_entries),
            "stale_reason": stale_reason,
            "avg_tracking_cno_db_hz": (
                float(np.mean(tracking_cno_values)) if tracking_cno_values else None
            ),
            "max_tracking_cno_db_hz": (
                float(np.max(tracking_cno_values)) if tracking_cno_values else None
            ),
            **output_io_metrics,
        }

    def _record_snapshot_timing(self, name: str, elapsed_s: float) -> None:
        elapsed = max(0.0, float(elapsed_s))
        now = time.monotonic()
        should_log = False
        snapshot: dict[str, dict[str, float]] = {}
        with self._snapshot_perf_lock:
            stats = self._snapshot_perf_stats.setdefault(
                name,
                {"count": 0.0, "total_s": 0.0, "max_s": 0.0},
            )
            stats["count"] += 1.0
            stats["total_s"] += elapsed
            stats["max_s"] = max(stats["max_s"], elapsed)
            if (now - self._last_snapshot_perf_log_ts) >= 1.0:
                self._last_snapshot_perf_log_ts = now
                snapshot = self._snapshot_perf_stats
                self._snapshot_perf_stats = {}
                should_log = True
        if should_log and snapshot:
            self._log_snapshot_timing_summary(snapshot)

    def _log_snapshot_timing_summary(
        self,
        snapshot: dict[str, dict[str, float]],
    ) -> None:
        order = (
            "snapshot_state_build",
            "snapshot_output_io",
            "snapshot_total",
        )
        parts: list[str] = []
        for name in order:
            stats = snapshot.get(name)
            if not stats:
                continue
            count = max(1.0, float(stats.get("count", 0.0)))
            avg_ms = 1000.0 * float(stats.get("total_s", 0.0)) / count
            max_ms = 1000.0 * float(stats.get("max_s", 0.0))
            parts.append(
                f"{name}_avg_ms={avg_ms:.2f} "
                f"{name}_max_ms={max_ms:.2f} "
                f"{name}_n={int(count)}"
            )
        if parts:
            self._log.info("GNSS snapshot timing: %s", " ".join(parts))

    def _fresh_sky_geometry(self, now: float) -> dict[_SatKey, dict[str, object]]:
        fresh: dict[_SatKey, dict[str, object]] = {}
        expired: list[_SatKey] = []
        for sat_key, geometry in self._sat_geometry_by_prn.items():
            observed = self._to_float(geometry.get("observed_monotonic_s"))
            if observed is None or (now - observed) > SKY_GEOMETRY_TIMEOUT_S:
                expired.append(sat_key)
                continue
            public_geometry = {
                key: value for key, value in geometry.items() if key != "observed_monotonic_s"
            }
            fresh[sat_key] = public_geometry
        for sat_key in expired:
            self._sat_geometry_by_prn.pop(sat_key, None)
        return fresh

    def _fresh_used_in_fix_prns(self, now: float) -> set[_SatKey]:
        observed = self._used_in_fix_observed_monotonic_s
        if observed is None or (now - observed) > USED_IN_FIX_TIMEOUT_S:
            self._used_in_fix_prns.clear()
            return set()
        return set(self._used_in_fix_prns)

    def _pvt_is_current(self, now: float) -> bool:
        observed = self._pvt_observed_monotonic_s
        if observed is None:
            return False
        return (now - observed) <= PVT_ACCURACY_TIMEOUT_S

    def _accuracy_is_current(self, now: float) -> bool:
        observed = self._latest_accuracy_observed_monotonic_s
        if observed is None or not self._latest_accuracy:
            return False
        return (now - observed) <= PVT_ACCURACY_TIMEOUT_S

    def _refresh_output_io_metrics(self, now: float) -> dict[str, object]:
        if (now - self._output_io_refresh_ts) < 1.0:
            return dict(self._output_io_metrics)
        self._output_io_refresh_ts = now
        receiver_log_bytes = self._file_size(self._receiver_log_path)

        receiver_log_rate_bps = 0.0
        previous = self._last_output_io_sample
        if previous is not None:
            (
                previous_ts,
                previous_receiver_log_bytes,
            ) = previous
            dt_s = max(1e-6, now - previous_ts)
            receiver_log_rate_bps = max(
                0.0, float(receiver_log_bytes - previous_receiver_log_bytes) / dt_s
            )
        self._last_output_io_sample = (
            now,
            receiver_log_bytes,
        )
        self._output_io_metrics = {
            "receiver_log_bytes": receiver_log_bytes,
            "receiver_log_rate_bps": receiver_log_rate_bps,
            **self._udp_monitor_metrics(now),
        }
        return dict(self._output_io_metrics)

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    @staticmethod
    def _file_age_s(path: Path) -> float | None:
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            return None
