"""GNSS-SDR UDP monitor protobuf listeners."""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from google.protobuf.message import DecodeError

from .models import _SatKey, _sat_key, _sat_label, _sat_prn
from .protobuf import gnss_synchro_pb2, monitor_pvt_pb2


@dataclass(frozen=True)
class _UdpMonitorSpec:
    name: str
    port: int
    handler: Callable[[bytes], None]


class UdpMonitorMixin:
    def _start_udp_monitors(self) -> None:
        self._stop_udp_monitors()
        specs = self._udp_monitor_specs()
        if not specs:
            self._log.info("GNSS-SDR UDP monitors disabled in configuration.")
            return

        for spec in specs:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.2)
            try:
                sock.bind(("127.0.0.1", spec.port))
            except OSError as exc:
                sock.close()
                self._err_log.error(
                    "Failed binding GNSS-SDR UDP %s monitor on 127.0.0.1:%d: %s",
                    spec.name,
                    spec.port,
                    exc,
                )
                continue
            thread = threading.Thread(
                target=self._run_udp_monitor,
                name=f"gnss_sdr_udp_{spec.name}",
                args=(spec, sock),
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                sock.close()
                raise
            self._udp_monitor_sockets.append(sock)
            self._udp_monitor_threads.append(thread)
            self._handoff_log.info(
                "GNSS-SDR UDP %s monitor listening on 127.0.0.1:%d",
                spec.name,
                spec.port,
            )

    def _stop_udp_monitors(self) -> None:
        sockets = list(getattr(self, "_udp_monitor_sockets", []))
        self._udp_monitor_sockets.clear()
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
        threads = list(getattr(self, "_udp_monitor_threads", []))
        for thread in threads:
            if thread.ident is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
        live_threads = [thread for thread in threads if thread.is_alive()]
        self._udp_monitor_threads[:] = live_threads
        if live_threads:
            self._err_log.error(
                "GNSS-SDR UDP monitor threads did not stop within 1.0 s: %s",
                ",".join(thread.name for thread in live_threads),
            )

    def _udp_monitor_specs(self) -> list[_UdpMonitorSpec]:
        specs: list[_UdpMonitorSpec] = []
        if bool(self._cfg.gnss_pvt_monitor_enable):
            specs.append(
                _UdpMonitorSpec(
                    "pvt",
                    self._udp_port(self._cfg.gnss_pvt_monitor_udp_port),
                    self._handle_pvt_udp_payload,
                )
            )
        if bool(self._cfg.gnss_monitor_enable):
            specs.append(
                _UdpMonitorSpec(
                    "observables",
                    self._udp_port(self._cfg.gnss_monitor_udp_port),
                    self._handle_observables_udp_payload,
                )
            )
        if bool(self._cfg.gnss_tracking_monitor_enable):
            specs.append(
                _UdpMonitorSpec(
                    "tracking",
                    self._udp_port(self._cfg.gnss_tracking_monitor_udp_port),
                    self._handle_tracking_udp_payload,
                )
            )
        return specs

    @staticmethod
    def _udp_port(value: object) -> int:
        port = int(str(value).strip())
        if not (1 <= port <= 65535):
            raise ValueError(f"invalid UDP monitor port: {value!r}")
        return port

    def _run_udp_monitor(self, spec: _UdpMonitorSpec, sock: socket.socket) -> None:
        while not self._monitor_stop.is_set():
            try:
                payload, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not payload:
                continue
            try:
                spec.handler(payload)
            except Exception as exc:
                self._record_udp_parse_error(spec.name, str(exc))

    def _handle_pvt_udp_payload(self, payload: bytes) -> None:
        message = monitor_pvt_pb2.MonitorPvt()
        try:
            message.ParseFromString(payload)
        except DecodeError as exc:
            self._record_udp_parse_error("pvt", str(exc))
            return
        self._handle_monitor_pvt_message(message)

    def _handle_observables_udp_payload(self, payload: bytes) -> None:
        self._handle_observables_payload(payload, source="observables")

    def _handle_tracking_udp_payload(self, payload: bytes) -> None:
        self._handle_observables_payload(payload, source="tracking")

    def _handle_observables_payload(self, payload: bytes, *, source: str) -> None:
        message = gnss_synchro_pb2.Observables()
        try:
            message.ParseFromString(payload)
        except DecodeError as exc:
            self._record_udp_parse_error(source, str(exc))
            return
        self._handle_observables_message(message, source=source)

    def _handle_monitor_pvt_message(self, message: monitor_pvt_pb2.MonitorPvt) -> None:
        point = self._pvt_point_from_monitor(message)
        if point is None:
            self._record_udp_parse_error("pvt", "invalid MonitorPvt position fields")
            return
        now = time.monotonic()
        valid_sats = self._valid_positive_int(message.valid_sats)
        with self._state_lock:
            self._pvt_udp_points.append(point)
            # Keep every fix for the lifetime of this receiver run. Exact
            # cumulative CEP50/CEP95 cannot be reconstructed after discarding
            # older fixes; gnss_accuracy_window_points is now the minimum sample
            # count before publishing CEP, not a rolling retention limit.
            accuracy = self._build_accuracy_snapshot(list(self._pvt_udp_points))
            accuracy.update(
                {
                    "accuracy_source": "pvt_udp",
                    "fix_count": int(self._udp_monitor_stats.get("pvt_packets", 0)) + 1,
                    "valid_sats": valid_sats,
                    "solution_status": int(message.solution_status),
                    "solution_type": int(message.solution_type),
                    "pvt_solution": dict(point),
                }
            )
            self._latest_accuracy = dict(accuracy)
            self._latest_accuracy_observed_monotonic_s = now
            self._mark_pvt_observed_locked(observation_count=valid_sats, now=now)
            self._udp_monitor_stats["pvt_packets"] = int(
                self._udp_monitor_stats.get("pvt_packets", 0)
            ) + 1
            self._udp_monitor_stats["pvt_last_monotonic_s"] = now

    def _pvt_point_from_monitor(
        self,
        message: monitor_pvt_pb2.MonitorPvt,
    ) -> dict[str, object] | None:
        lat = self._to_float(message.latitude)
        lon = self._to_float(message.longitude)
        height = self._to_float(message.height)
        if lat is None or lon is None or height is None:
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        point: dict[str, object] = {
            "utc_time": str(message.utc_time),
            "latitude": lat,
            "longitude": lon,
            "altitude": height,
            "tow_ms": float(message.tow_at_current_symbol_ms),
            "week": float(message.week),
            "rx_time_s": float(message.rx_time),
            "user_clock_offset_s": float(message.user_clk_offset),
            "ecef_x_m": float(message.pos_x),
            "ecef_y_m": float(message.pos_y),
            "ecef_z_m": float(message.pos_z),
            "ecef_vx_mps": float(message.vel_x),
            "ecef_vy_mps": float(message.vel_y),
            "ecef_vz_mps": float(message.vel_z),
            "cov_xx_m2": float(message.cov_xx),
            "cov_yy_m2": float(message.cov_yy),
            "cov_zz_m2": float(message.cov_zz),
            "cov_xy_m2": float(message.cov_xy),
            "cov_yz_m2": float(message.cov_yz),
            "cov_zx_m2": float(message.cov_zx),
            "ar_ratio_factor": float(message.ar_ratio_factor),
            "ar_ratio_threshold": float(message.ar_ratio_threshold),
            "gdop": float(message.gdop),
            "pdop": float(message.pdop),
            "hdop": float(message.hdop),
            "vdop": float(message.vdop),
            "user_clk_drift_ppm": float(message.user_clk_drift_ppm),
            "vel_e_mps": float(message.vel_e),
            "vel_n_mps": float(message.vel_n),
            "vel_u_mps": float(message.vel_u),
            "cog_deg": float(message.cog),
            "valid_sats": float(message.valid_sats),
            "solution_status": float(message.solution_status),
            "solution_type": float(message.solution_type),
        }
        point["height"] = height
        if message.utc_time:
            point["utc_time"] = message.utc_time
        if message.geohash:
            point["geohash"] = message.geohash
        point["monitor_pvt"] = self._protobuf_scalar_fields(message)
        return point

    def _handle_observables_message(
        self,
        message: gnss_synchro_pb2.Observables,
        *,
        source: str,
    ) -> None:
        now = time.monotonic()
        packet_count_key = (
            "tracking_packets" if source == "tracking" else "observables_packets"
        )
        with self._state_lock:
            self._udp_monitor_stats[packet_count_key] = int(
                self._udp_monitor_stats.get(packet_count_key, 0)
            ) + 1
            self._udp_monitor_stats[f"{source}_last_monotonic_s"] = now
            for observable in message.observable:
                self._apply_gnss_synchro_observable_locked(observable, source=source, now=now)

    def _apply_gnss_synchro_observable_locked(
        self,
        observable: gnss_synchro_pb2.GnssSynchro,
        *,
        source: str,
        now: float,
    ) -> None:
        prn = int(observable.prn)
        channel = int(observable.channel_id)
        if prn <= 0 or channel < 0:
            return
        sat_key = _sat_key(observable.system or "G", prn)
        synchro_entry = self._synchro_entry_from_message(observable)
        if synchro_entry is None:
            return
        cno_db_hz = self._finite_float(observable.cn0_db_hz)

        if source == "tracking":
            self._persist_tracking_observable_locked(
                observable,
                sat_key=sat_key,
                channel=channel,
                now=now,
            )
            previous_key = self._channel_prn.get(channel)
            if previous_key is not None and previous_key != sat_key:
                previous_entry = self._prn_states.get(previous_key)
                if previous_entry is not None and int(previous_entry.get("channel", -1)) == channel:
                    previous_entry = dict(previous_entry)
                    previous_entry.pop("telemetry_confirmed", None)
                    previous_entry["state"] = "lost"
                    self._prn_states[previous_key] = previous_entry
                self._clear_cno_history(channel, previous_key)
                self._tracking_cn0_by_channel.pop(channel, None)
                self._tracking_prn_by_channel.pop(channel, None)
                self._tracking_carrier_lock_by_channel.pop(channel, None)
            self._channel_prn[channel] = sat_key
            state_entry = self._prn_states.setdefault(
                sat_key,
                {
                    "prn": _sat_prn(sat_key),
                    "channel": channel,
                    "state": "tracking",
                },
            )
            state_entry["channel"] = channel
            state_entry["state"] = "tracking"
            if observable.signal:
                state_entry["signal"] = str(observable.signal)
            if bool(observable.flag_valid_word):
                state_entry["telemetry_confirmed"] = True
            if cno_db_hz is not None and cno_db_hz > 0.0:
                self._tracking_prn_by_channel[channel] = prn
                self._tracking_cn0_by_channel[channel] = cno_db_hz
                self._append_cno_sample(channel, sat_key, cno_db_hz)
            self._latest_tracking_monitor_by_prn[sat_key] = synchro_entry
            previous = self._udp_monitor_logged.get((source, channel))
            if previous is None or previous[0] != sat_key or (
                cno_db_hz is not None and abs(previous[1] - cno_db_hz) >= 2.0
            ):
                self._udp_monitor_logged[(source, channel)] = (
                    sat_key,
                    cno_db_hz if cno_db_hz is not None else 0.0,
                    now,
                )
                self._handoff_log.info(
                    "GNSS tracking UDP: channel=%d satellite=%s cno_db_hz=%s valid_symbol=%s valid_word=%s",
                    channel,
                    _sat_label(sat_key),
                    f"{cno_db_hz:.2f}" if cno_db_hz is not None else "--",
                    bool(observable.flag_valid_symbol_output),
                    bool(observable.flag_valid_word),
                )
            return

        self._latest_observables_by_prn[sat_key] = synchro_entry

    def _persist_tracking_observable_locked(
        self,
        observable: gnss_synchro_pb2.GnssSynchro,
        *,
        sat_key: _SatKey,
        channel: int,
        now: float,
    ) -> None:
        """Archive carrier/code tracking state received from TrackingMonitor."""

        handle = getattr(self, "_tracking_state_handle", None)
        if handle is None:
            return

        wall_time_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        self._tracking_state_sequence = int(
            getattr(self, "_tracking_state_sequence", 0)
        ) + 1
        fs = int(observable.fs)
        carrier_phase_rads = self._finite_float(observable.carrier_phase_rads)
        code_phase_samples = self._finite_float(observable.code_phase_samples)
        prompt_i = self._finite_float(observable.prompt_i)
        prompt_q = self._finite_float(observable.prompt_q)
        fields = {
            "schema_version": 1,
            "session_id": getattr(self, "_runtime_session_id", None),
            "sequence": self._tracking_state_sequence,
            "timestamp_utc": datetime.fromtimestamp(
                wall_time_ns / 1e9,
                timezone.utc,
            ).isoformat(),
            "timestamp_local": datetime.fromtimestamp(
                wall_time_ns / 1e9,
            ).astimezone().isoformat(),
            "wall_time_unix_ns": wall_time_ns,
            "wall_time_unix_s": wall_time_ns / 1e9,
            "monotonic_s": now,
            "monotonic_ns": monotonic_ns,
            "session_elapsed_s": (
                (monotonic_ns - self._tracking_state_started_monotonic_ns) / 1e9
                if getattr(self, "_tracking_state_started_monotonic_ns", 0) > 0
                else None
            ),
            "receiver_time_s": self._finite_float(observable.rx_time),
            "tow_s": self._finite_float(observable.tow_at_current_symbol_ms / 1000.0),
            "system": str(observable.system or "G"),
            "signal": str(observable.signal),
            "satellite_id": _sat_label(sat_key),
            "prn": int(observable.prn),
            "channel": channel,
            "fs": fs,
            "prompt_i": prompt_i,
            "prompt_q": prompt_q,
            "prompt_magnitude": (
                math.hypot(prompt_i, prompt_q)
                if prompt_i is not None and prompt_q is not None
                else None
            ),
            "prompt_phase_rads": (
                math.atan2(prompt_q, prompt_i)
                if prompt_i is not None and prompt_q is not None
                else None
            ),
            "cn0_db_hz": self._finite_float(observable.cn0_db_hz),
            "carrier_doppler_hz": self._finite_float(observable.carrier_doppler_hz),
            "carrier_phase_rads": carrier_phase_rads,
            "carrier_phase_cycles": (
                carrier_phase_rads / (2.0 * math.pi)
                if carrier_phase_rads is not None
                else None
            ),
            "code_phase_samples": code_phase_samples,
            "code_phase_seconds": (
                code_phase_samples / fs
                if code_phase_samples is not None and fs > 0
                else None
            ),
            "tracking_sample_counter": int(observable.tracking_sample_counter),
            "correlation_length_ms": int(observable.correlation_length_ms),
            "pseudorange_m": self._finite_float(observable.pseudorange_m),
            "valid_acquisition": bool(observable.flag_valid_acquisition),
            "valid_symbol_output": bool(observable.flag_valid_symbol_output),
            "valid_word": bool(observable.flag_valid_word),
            "valid_pseudorange": bool(observable.flag_valid_pseudorange),
            "pll_180_deg_phase_locked": bool(observable.flag_PLL_180_deg_phase_locked),
            "cycle_slip": bool(observable.flag_cycle_slip),
        }
        try:
            handle.write(json.dumps(fields, allow_nan=False, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._udp_monitor_stats["tracking_archive_write_errors"] = int(
                self._udp_monitor_stats.get("tracking_archive_write_errors", 0)
            ) + 1
            previous = float(self._udp_parse_error_log_ts.get("tracking_archive", 0.0))
            if (now - previous) >= 5.0:
                self._udp_parse_error_log_ts["tracking_archive"] = now
                self._err_log.warning("GNSS tracking-state archive write failed: %s", exc)
            return
        self._udp_monitor_stats["tracking_archive_rows"] = int(
            self._udp_monitor_stats.get("tracking_archive_rows", 0)
        ) + 1

    def _synchro_entry_from_message(
        self,
        observable: gnss_synchro_pb2.GnssSynchro,
    ) -> dict[str, object] | None:
        prn = int(observable.prn)
        if prn <= 0:
            return None
        entry: dict[str, object] = self._protobuf_scalar_fields(observable)
        entry["prn"] = prn
        entry["channel"] = int(observable.channel_id)
        entry["valid_acquisition"] = bool(observable.flag_valid_acquisition)
        entry["valid_symbol_output"] = bool(observable.flag_valid_symbol_output)
        entry["valid_word"] = bool(observable.flag_valid_word)
        entry["valid_pseudorange"] = bool(observable.flag_valid_pseudorange)
        entry["pll_180_deg_phase_locked"] = bool(observable.flag_PLL_180_deg_phase_locked)
        entry["cycle_slip"] = bool(observable.flag_cycle_slip)
        tow_s = self._finite_float(observable.tow_at_current_symbol_ms / 1000.0)
        if tow_s is not None:
            entry["tow_s"] = tow_s
        return entry

    @staticmethod
    def _protobuf_scalar_fields(message: object) -> dict[str, object]:
        fields: dict[str, object] = {}
        descriptor = getattr(message, "DESCRIPTOR", None)
        if descriptor is None:
            return fields
        for field in descriptor.fields:
            value = getattr(message, field.name)
            is_repeated = bool(getattr(field, "is_repeated", False))
            if not is_repeated and hasattr(field, "label") and hasattr(field, "LABEL_REPEATED"):
                is_repeated = field.label == field.LABEL_REPEATED
            if is_repeated:
                fields[field.name] = list(value)
            else:
                fields[field.name] = value
        return fields

    @staticmethod
    def _finite_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _record_udp_parse_error(self, source: str, detail: str) -> None:
        now = time.monotonic()
        with self._state_lock:
            self._udp_monitor_stats["parse_errors"] = int(
                self._udp_monitor_stats.get("parse_errors", 0)
            ) + 1
            self._udp_monitor_stats[f"{source}_parse_errors"] = int(
                self._udp_monitor_stats.get(f"{source}_parse_errors", 0)
            ) + 1
        previous = self._udp_parse_error_log_ts.get(source, 0.0)
        if (now - previous) >= 5.0:
            self._udp_parse_error_log_ts[source] = now
            self._err_log.warning(
                "GNSS-SDR UDP %s monitor parse error: %s",
                source,
                detail,
            )

    def _udp_monitor_metrics(self, now: float) -> dict[str, object]:
        with self._state_lock:
            stats = dict(self._udp_monitor_stats)
        metrics: dict[str, object] = {
            "udp_pvt_packets": int(stats.get("pvt_packets", 0)),
            "udp_observables_packets": int(stats.get("observables_packets", 0)),
            "udp_tracking_packets": int(stats.get("tracking_packets", 0)),
            "udp_parse_errors": int(stats.get("parse_errors", 0)),
            "tracking_state_archive_rows": int(stats.get("tracking_archive_rows", 0)),
            "tracking_state_archive_write_errors": int(
                stats.get("tracking_archive_write_errors", 0)
            ),
        }
        archive_path = getattr(self, "_tracking_state_log_path", None)
        metrics["tracking_state_archive_path"] = (
            None if archive_path is None else str(archive_path)
        )
        try:
            metrics["tracking_state_archive_bytes"] = (
                0 if archive_path is None else int(archive_path.stat().st_size)
            )
        except OSError:
            metrics["tracking_state_archive_bytes"] = 0
        metrics["tracking_state_session_id"] = getattr(
            self,
            "_runtime_session_id",
            None,
        )
        for key, output_key in (
            ("pvt_last_monotonic_s", "udp_pvt_age_s"),
            ("observables_last_monotonic_s", "udp_observables_age_s"),
            ("tracking_last_monotonic_s", "udp_tracking_age_s"),
        ):
            observed = self._finite_float(stats.get(key))
            metrics[output_key] = None if observed is None else max(0.0, now - observed)
        return metrics
