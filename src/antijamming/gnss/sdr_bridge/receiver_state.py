"""Receiver-event and PRN/channel state updates from GNSS-SDR lines."""

from __future__ import annotations

import math
import time

from .log_parsers import (
    _ACQUIRED_RE,
    _ACQUISITION_DECISION_RE,
    _ASSIGNED_RE,
    _BIT_SYNC_LOCK_RE,
    _CLOCK_OFFSET_RE,
    _CYCLE_SLIP_RE,
    _GLOG_PREFIX_RE,
    _IDLE_RE,
    _LOSS_RE,
    _NAV_MESSAGE_RE,
    _PVT_RESET_RE,
    _RTKPOS_ERROR_RE,
    _TRACKING_RE,
    _TRACKING_STARTED_RE,
    _parse_acquisition_metrics,
)
from .models import _sat_key, _sat_label, _sat_public_fields

class ReceiverStateMixin:
    def _handle_receiver_event_line(self, text: str) -> None:
        clean = _GLOG_PREFIX_RE.sub("", text).strip()
        cycle_slip_match = _CYCLE_SLIP_RE.search(clean)
        if cycle_slip_match:
            self._log_receiver_event_once(
                "cycle_slip channel=%s rx_time_s=%s satellite=%s signal=%s"
                % (
                    cycle_slip_match.group(1),
                    cycle_slip_match.group(2),
                    cycle_slip_match.group(3).strip(),
                    cycle_slip_match.group(4),
                )
            )
            return

        loss_match = _LOSS_RE.search(clean)
        if loss_match:
            self._log_receiver_event_once(
                "loss_of_lock channel=%s satellite=%s%s"
                % (
                    loss_match.group(1),
                    (loss_match.group(2) or loss_match.group(3) or "").strip(),
                    loss_match.group(4),
                )
            )
            return

        rtkpos_match = _RTKPOS_ERROR_RE.search(clean)
        if rtkpos_match:
            detail = " ".join(rtkpos_match.group(1).split())
            self._log_receiver_event_once(f"rtklib_position_error detail={detail}")
            return

        if _PVT_RESET_RE.search(clean):
            if "Received reset observables" in clean:
                self._log_receiver_event_once("observables_reset source=pvt_tow_command")
            else:
                self._log_receiver_event_once(
                    "pvt_reset_observables reason=consecutive_position_solver_errors"
                )
            return

        clock_offset_match = _CLOCK_OFFSET_RE.search(clean)
        if clock_offset_match:
            offset_s = clock_offset_match.group(1)
            offset_ms = clock_offset_match.group(2)
            if offset_s is not None:
                self._log_receiver_event_once(f"clock_offset_correction offset_s={offset_s}")
            elif offset_ms is not None:
                self._log_receiver_event_once(f"rx_time_offset_corrected offset_ms={offset_ms}")

    def _log_receiver_event_once(self, message: str) -> None:
        now = time.monotonic()
        previous = self._recent_receiver_events.get(message)
        if previous is not None and (now - previous) < 2.0:
            return
        self._recent_receiver_events[message] = now
        if len(self._recent_receiver_events) > 256:
            cutoff = now - 30.0
            self._recent_receiver_events = {
                key: ts for key, ts in self._recent_receiver_events.items() if ts >= cutoff
            }
        with self._state_lock:
            latest_accuracy = dict(self._latest_accuracy)
            pvt_observation_count = self._pvt_observation_count
            receiver_time_s = self._receiver_time_s
            used_count = len(self._used_in_fix_prns)
        self._handoff_log.info(
            "GNSS receiver event: %s receiver_time_s=%s pvt_observations=%s "
            "used_count=%d valid_sats=%s fix_type=%s hdop=%s vdop=%s pdop=%s gdop=%s "
            "lat_deg=%s lon_deg=%s alt_m=%s "
            "truth_h_error_m=%s truth_3d_error_m=%s",
            message,
            receiver_time_s if receiver_time_s is not None else "--",
            pvt_observation_count if pvt_observation_count is not None else "--",
            used_count,
            self._format_receiver_event_float(latest_accuracy.get("valid_sats"), 0),
            str(latest_accuracy.get("fix_type", "--") or "--").replace(" ", "_"),
            self._format_receiver_event_float(latest_accuracy.get("hdop")),
            self._format_receiver_event_float(latest_accuracy.get("vdop")),
            self._format_receiver_event_float(latest_accuracy.get("pdop")),
            self._format_receiver_event_float(latest_accuracy.get("gdop")),
            self._format_receiver_event_float(latest_accuracy.get("lat_deg"), 7),
            self._format_receiver_event_float(latest_accuracy.get("lon_deg"), 7),
            self._format_receiver_event_float(latest_accuracy.get("alt_m"), 2),
            self._format_receiver_event_float(latest_accuracy.get("horizontal_error_m"), 2),
            self._format_receiver_event_float(latest_accuracy.get("three_d_error_m"), 2),
        )

    @staticmethod
    def _format_receiver_event_float(value: object, digits: int = 2) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "--"
        if not math.isfinite(number):
            return "--"
        return f"{number:.{max(0, int(digits))}f}"

    def _handle_prn_state_line(self, text: str) -> None:
        assigned_match = _ASSIGNED_RE.search(text)
        if assigned_match:
            self._set_prn_state(
                channel=int(assigned_match.group(1)),
                constellation=assigned_match.group(2) or assigned_match.group(3),
                prn=int(assigned_match.group(4)),
                state="assigned",
            )
            return

        acquired_match = _ACQUIRED_RE.search(text)
        if acquired_match:
            self._set_prn_state(
                channel=int(acquired_match.group(1)),
                constellation=acquired_match.group(2),
                prn=int(acquired_match.group(3)),
                state="acquired",
            )
            return

        decision_match = _ACQUISITION_DECISION_RE.search(text)
        if decision_match:
            constellation = decision_match.group(2)
            prn = int(decision_match.group(3))
            acquisition_metrics = _parse_acquisition_metrics(text)
            if decision_match.group(1) == "positive":
                self._set_prn_state(
                    channel=-1,
                    constellation=constellation,
                    prn=prn,
                    state="acquired",
                    updates=acquisition_metrics,
                )
            else:
                self._set_prn_search_state(constellation, prn, updates=acquisition_metrics)
            return

        tracking_match = _TRACKING_RE.search(text)
        if tracking_match and "Pull-in:" in text:
            self._set_prn_state(
                channel=int(tracking_match.group(4)),
                constellation=tracking_match.group(1) or tracking_match.group(2),
                prn=int(tracking_match.group(3)),
                state="tracking",
            )
            return

        tracking_started_match = _TRACKING_STARTED_RE.search(text)
        if tracking_started_match:
            # Normal GNSS-SDR console output reports tracking starts without the
            # glog Pull-in prefix. Accept that operator-facing line as tracking
            # state so C/N0 bars do not depend on the INFO glog monitor keeping
            # up with the TrackingMonitor UDP stream.
            self._set_prn_state(
                channel=int(tracking_started_match.group(2)),
                constellation=tracking_started_match.group(1),
                prn=int(tracking_started_match.group(3)),
                state="tracking",
            )
            return

        bit_sync_match = _BIT_SYNC_LOCK_RE.search(text)
        if bit_sync_match:
            self._set_prn_state(
                channel=int(bit_sync_match.group(2)),
                constellation=bit_sync_match.group(1),
                prn=int(bit_sync_match.group(3)),
                state="tracking",
            )
            return

        nav_message_match = _NAV_MESSAGE_RE.search(text)
        if nav_message_match:
            constellation = nav_message_match.group(1)
            channel = int(nav_message_match.group(2))
            prn = int(nav_message_match.group(3))
            cno_db_hz = float(nav_message_match.group(4))
            self._set_prn_state(
                channel=channel,
                constellation=constellation,
                prn=prn,
                state="tracking",
                updates={"telemetry_confirmed": True},
            )
            self._set_runtime_cno_sample(
                channel=channel,
                constellation=constellation,
                prn=prn,
                cno_db_hz=cno_db_hz,
            )
            return

        loss_match = _LOSS_RE.search(text)
        if loss_match:
            self._set_prn_state(
                channel=int(loss_match.group(1)),
                constellation=loss_match.group(2) or loss_match.group(3),
                prn=int(loss_match.group(4)),
                state="lost",
            )
            return

        idle_match = _IDLE_RE.search(text)
        if idle_match:
            self._set_channel_idle(int(idle_match.group(1)))

    def _set_prn_state(
        self,
        channel: int,
        constellation: object,
        prn: int,
        state: str,
        updates: dict[str, object] | None = None,
    ) -> None:
        sat_key = _sat_key(constellation, prn)
        with self._state_lock:
            previous_key = self._channel_prn.get(channel)
            previous_state = None
            if previous_key is not None:
                previous_entry = self._prn_states.get(previous_key)
                if previous_entry is not None:
                    previous_state = str(previous_entry.get("state", ""))
            if previous_key is not None and previous_key != sat_key:
                previous_entry = self._prn_states.get(previous_key)
                if previous_entry is not None and previous_entry.get("state") == "assigned":
                    self._prn_states.pop(previous_key, None)
                elif previous_entry is not None and int(previous_entry.get("channel", -1)) == channel:
                    previous_entry = dict(previous_entry)
                    previous_entry.pop("telemetry_confirmed", None)
                    previous_entry["state"] = "lost"
                    self._prn_states[previous_key] = previous_entry
                if channel >= 0:
                    self._clear_cno_history(channel, previous_key)
                    self._tracking_cn0_by_channel.pop(channel, None)
                    self._tracking_prn_by_channel.pop(channel, None)
                    self._tracking_carrier_lock_by_channel.pop(channel, None)
            if channel >= 0:
                self._channel_prn[channel] = sat_key
                if state != "tracking" or previous_key != sat_key or previous_state != "tracking":
                    self._clear_cno_history(channel, sat_key)
                    self._tracking_cn0_by_channel.pop(channel, None)
            entry = dict(self._prn_states.get(sat_key, {}))
            if state != "tracking":
                entry.pop("telemetry_confirmed", None)
            elif previous_key != sat_key or previous_state != "tracking":
                entry["telemetry_confirmed"] = False
            entry.update(
                {
                    **_sat_public_fields(sat_key),
                    "channel": channel,
                    "state": state,
                }
            )
            if updates:
                entry.update(updates)
            self._prn_states[sat_key] = entry
        if previous_key != sat_key or previous_state != state:
            self._handoff_log.info(
                "GNSS receiver state: channel=%d satellite=%s state=%s",
                channel,
                _sat_label(sat_key),
                state,
            )

    def _set_prn_search_state(
        self,
        constellation: object,
        prn: int,
        updates: dict[str, object] | None = None,
    ) -> None:
        sat_key = _sat_key(constellation, prn)
        with self._state_lock:
            existing = self._prn_states.get(sat_key)
            if existing is not None and existing.get("state") != "searched":
                return
            entry = {
                **_sat_public_fields(sat_key),
                "channel": -1,
                "state": "searched",
            }
            if updates:
                entry.update(updates)
            self._prn_states[sat_key] = entry

    def _set_channel_idle(self, channel: int) -> None:
        with self._state_lock:
            sat_key = self._channel_prn.pop(channel, None)
            if sat_key is None:
                return
            entry = self._prn_states.get(sat_key)
            if entry is not None and entry.get("state") == "assigned":
                self._prn_states.pop(sat_key, None)
            self._clear_cno_history(channel, sat_key)
            self._tracking_cn0_by_channel.pop(channel, None)
        self._handoff_log.info(
            "GNSS receiver state: channel=%d satellite=%s state=idle",
            channel,
            _sat_label(sat_key) if sat_key is not None else "--",
        )
