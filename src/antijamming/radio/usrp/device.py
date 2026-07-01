"""USRP RX device wrapper with UHD-specific setup isolated from the GUI."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "error")

try:
    import uhd
except ImportError as exc:  # pragma: no cover - exercised only without UHD installed.
    uhd = None  # type: ignore[assignment]
    _UHD_IMPORT_ERROR: ImportError | None = exc
else:
    _UHD_IMPORT_ERROR = None

from antijamming.config import StreamConfig
from antijamming.radio.usrp.discovery import usrp_arg_int, with_usrp_frame_sizes

# Prefer enum checks: str(error_code) is easy to get wrong across UHD versions.
_RXEC: Any = uhd.types.RXMetadataErrorCode if uhd is not None else None
_REQUIRED_X300_FPGA_IMAGE = "XG"
_X300_MBOARD_NAME_MARKERS = ("x300", "x310")
_FPGA_IMAGE_MARKERS = (
    "fpga_image",
    "fpga image",
    "fpga_path",
    "fpga path",
    "fpga",
    "image",
)

RxState = Literal["ok", "overflow", "timeout", "other"]


@dataclass(frozen=True)
class RxChunkResult:
    """One recv() result plus UHD metadata needed for transport audits."""

    chunk: np.ndarray
    state: RxState
    got_samples: int
    error_code: str
    out_of_sequence: bool
    time_spec_s: float | None

    def __iter__(self):
        # Backwards-compatible unpacking for existing call sites:
        # ``chunk, state = device.recv_chunk()``.
        yield self.chunk
        yield self.state


# =============================================================================
# USRP RX Runtime Device
# =============================================================================

# This wrapper owns all UHD calls. Importing GUI/runtime modules should never
# construct hardware; the device is created only when the backend starts.

class UsrpRxDevice:
    """Configure and stream complex samples from the X300/TwinRX receiver."""

    def __init__(self, config: StreamConfig) -> None:
        if uhd is None:
            raise RuntimeError(
                "UHD Python bindings are unavailable; install UHD before creating "
                "a USRP runtime device."
            ) from _UHD_IMPORT_ERROR
        self._cfg = config
        resolved_addr = with_usrp_frame_sizes(
            config.usrp_addr,
            recv_frame_size=int(config.recv_frame_size),
            send_frame_size=int(config.send_frame_size),
            recv_buff_size=int(config.recv_buff_size),
            num_recv_frames=int(config.num_recv_frames),
        )
        config.usrp_addr = resolved_addr
        config.recv_frame_size = usrp_arg_int(
            resolved_addr, "recv_frame_size", int(config.recv_frame_size)
        )
        config.send_frame_size = usrp_arg_int(
            resolved_addr, "send_frame_size", int(config.send_frame_size)
        )
        self._usrp = uhd.usrp.MultiUSRP(resolved_addr)
        self._fpga_image_report = self._verify_x300_fpga_image()
        self._lo_lock_wait_elapsed_s = 0.0
        # Timed tuning gives all channels a common retune edge, which matters for
        # phase-coherent array processing.
        self._timed_tune_lead_s = 0.05
        if config.twinrx_lo_sharing:
            self._configure_twinrx_lo_sharing()
        for ch in config.channels:
            # Apply per-channel UHD settings before the shared timed tune.
            antenna = self._rx_antenna_for_channel(ch)
            self._usrp.set_rx_rate(config.sample_rate, ch)
            if float(config.usrp_rx_bandwidth_hz) > 0.0:
                self._usrp.set_rx_bandwidth(config.usrp_rx_bandwidth_hz, ch)
            self._usrp.set_rx_gain(config.gain_db, ch)
            self._usrp.set_rx_antenna(antenna, ch)
        self._tune_rx_channels_timed()
        self._lo_lock_report = self._wait_for_lo_lock()

        # Keep device defaults unless explicitly required; repeated source/time
        # reconfiguration during rapid restarts can destabilize RFNoC graph setup.

        # cpu=fc32 host samples, wire=sc16 over Ethernet (half link load vs fc32 wire).
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = list(config.channels)
        self._rx_streamer = self._usrp.get_rx_stream(stream_args)
        self._metadata = uhd.types.RXMetadata()
        self._stopping = False
        self._started = False
        self._startup_recv_pending = False
        self._startup_recv_deadline_monotonic = 0.0

    # -------------------------------------------------------------------------
    # Channel RF Configuration
    # -------------------------------------------------------------------------

    # Channel maps are tuple-backed so runtime config can express TwinRX board
    # differences without hard-coding them in this device wrapper.

    def _rx_antenna_for_channel(self, ch: int) -> str:
        forced = str(self._cfg.antenna).strip().upper()
        if forced:
            return forced
        antennas = tuple(str(ant).strip().upper() for ant in self._cfg.rx_antennas_by_channel)
        if not antennas:
            return "RX2"
        if 0 <= int(ch) < len(antennas):
            return antennas[int(ch)]
        return antennas[-1]

    def _rx_lo_source_for_channel(self, ch: int) -> str:
        sources = tuple(str(src).strip().lower() for src in self._cfg.rx_lo_sources_by_channel)
        if not sources:
            return "internal"
        if 0 <= int(ch) < len(sources):
            return sources[int(ch)]
        return sources[-1]

    def _rx_lo_export_for_channel(self, ch: int) -> bool:
        exports = tuple(bool(value) for value in self._cfg.rx_lo_exports_by_channel)
        if not exports:
            return False
        if 0 <= int(ch) < len(exports):
            return exports[int(ch)]
        return exports[-1]

    def _configure_twinrx_lo_sharing(self) -> None:
        # Configure source/export before tuning so external/companion LO users tune coherently.
        for ch in self._cfg.channels:
            source = self._rx_lo_source_for_channel(ch)
            available_sources = {src.lower() for src in self._usrp.get_rx_lo_sources("all", ch)}
            if source not in available_sources:
                raise RuntimeError(
                    f"RX channel {ch} does not support LO source {source!r}; "
                    f"available={sorted(available_sources)}"
                )
            self._usrp.set_rx_lo_source(source, "all", ch)

        for ch in self._cfg.channels:
            self._usrp.set_rx_lo_export_enabled(
                self._rx_lo_export_for_channel(ch), "all", ch
            )

    def _rx_lo_names_for_channel(self, ch: int) -> tuple[str, ...]:
        try:
            names = tuple(str(name) for name in self._usrp.get_rx_lo_names(ch))
        except Exception:
            return ("all",)
        if not names:
            return ("all",)
        return names

    def _rx_lo_stage_report_lines(self, ch: int) -> list[str]:
        # LO reports are diagnostic-only and tolerate UHD variants that expose
        # different LO stage names or fail individual queries.
        lines: list[str] = []
        lo_names = self._rx_lo_names_for_channel(ch)
        lines.append(f"Ch{ch} LO names: {list(lo_names)}")
        report_names = ("all", *tuple(name for name in lo_names if name.lower() != "all"))
        for lo_name in report_names:
            try:
                sources = list(self._usrp.get_rx_lo_sources(lo_name, ch))
            except Exception:
                sources = []
            try:
                source = self._usrp.get_rx_lo_source(lo_name, ch)
            except Exception:
                if lo_name.lower() != "all" and not sources:
                    continue
                source = "not_exposed_by_uhd"
            try:
                export = self._usrp.get_rx_lo_export_enabled(lo_name, ch)
            except Exception:
                if lo_name.lower() != "all" and not sources:
                    continue
                export = "not_exposed_by_uhd"
            lines.append(
                f"Ch{ch} LO {lo_name}: source={source}, export={export}, "
                f"available_sources={sources}"
            )
        return lines

    def _tune_rx_channels_timed(self) -> None:
        # gr-doa's X440 source tunes channels under one command time. Do the same
        # here so all TwinRX channels retune on the same device time edge.
        tune_time = self._usrp.get_time_now().get_real_secs() + self._timed_tune_lead_s
        self._usrp.set_command_time(uhd.libpyuhd.types.time_spec(tune_time))
        try:
            for ch in self._cfg.channels:
                self._usrp.set_rx_freq(
                    uhd.libpyuhd.types.tune_request(self._cfg.center_freq_hz), ch
                )
        finally:
            self._usrp.clear_command_time()
        time.sleep(self._timed_tune_lead_s)

    def _wait_for_lo_lock(self) -> dict[int, bool]:
        start = time.monotonic()
        timeout_s = max(0.0, float(self._cfg.lo_lock_timeout_s))
        deadline = start + timeout_s
        state: dict[int, bool] = {}
        while True:
            all_locked = True
            state = {}
            for ch in self._cfg.channels:
                try:
                    sensor_names = {
                        str(name).lower() for name in self._usrp.get_rx_sensor_names(ch)
                    }
                    if "lo_locked" not in sensor_names:
                        continue
                    locked = bool(self._usrp.get_rx_sensor("lo_locked", ch).to_bool())
                except Exception:
                    continue
                state[int(ch)] = locked
                all_locked = all_locked and locked
            self._lo_lock_wait_elapsed_s = time.monotonic() - start
            if all_locked:
                return state
            if time.monotonic() >= deadline:
                unlocked = [ch for ch, locked in sorted(state.items()) if not locked]
                if unlocked:
                    raise RuntimeError(
                        "Timed out waiting for TwinRX LO lock after "
                        f"{timeout_s:.2f}s: unlocked channels={unlocked}"
                    )
                return state
        time.sleep(0.05)

    # -------------------------------------------------------------------------
    # Stream Startup and Diagnostics
    # -------------------------------------------------------------------------

    def _issue_start_cont(self) -> None:
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        cmd.stream_now = False
        cmd.time_spec = uhd.libpyuhd.types.time_spec(self._usrp.get_time_now().get_real_secs() + 0.02)
        self._rx_streamer.issue_stream_cmd(cmd)
        self._started = True
        self._startup_recv_pending = True
        self._startup_recv_deadline_monotonic = time.monotonic() + 0.12

    def startup_report_lines(self) -> list[str]:
        lines: list[str] = []
        n_channels = len(self._cfg.channels)
        est_rx_mbps_sc16 = (self._cfg.sample_rate * n_channels * 32.0) / 1e6
        est_rx_mbps_fc32 = (self._cfg.sample_rate * n_channels * 64.0) / 1e6
        lines.append(
            "RX config: "
            f"addr={self._cfg.usrp_addr}, "
            f"channels={list(self._cfg.channels)}, "
            f"rate={self._cfg.sample_rate/1e6:.3f} Msps, "
            f"freq={self._cfg.center_freq_hz/1e6:.3f} MHz, "
            f"rx_bw={self._cfg.usrp_rx_bandwidth_hz/1e6:.3f} MHz, "
            f"gain={self._cfg.gain_db:.1f} dB, "
            f"antennas={self._rx_antenna_report()}, "
            f"twinrx_lo_sharing={self._cfg.twinrx_lo_sharing}"
        )
        if self._cfg.twinrx_lo_sharing:
            lines.append(f"TwinRX LO sharing map: {self._rx_lo_report()}")
        if self._fpga_image_report:
            lines.extend(self._fpga_image_report)
        if self._lo_lock_report:
            lock_text = ",".join(
                f"ch{ch}:{'locked' if locked else 'unlocked'}"
                for ch, locked in sorted(self._lo_lock_report.items())
            )
            lines.append(
                f"LO lock ready after {self._lo_lock_wait_elapsed_s:.2f}s "
                f"(timeout {self._cfg.lo_lock_timeout_s:.2f}s): {lock_text}"
            )
        lines.append(
            f"Rate budget estimate: sc16_wire={est_rx_mbps_sc16:.1f} Mbps, "
            f"fc32_host={est_rx_mbps_fc32:.1f} Mbps"
        )
        lines.append(
            "Stream format: cpu=fc32 (host), wire=sc16 (OTW) — see UHD StreamArgs"
        )
        lines.append(
            f"Transport config: recv_frame_size={self._cfg.recv_frame_size}, "
            f"send_frame_size={self._cfg.send_frame_size}, "
            f"recv_buff_size={self._cfg.recv_buff_size}, "
            f"num_recv_frames={self._cfg.num_recv_frames}"
        )
        lines.append(
            "Tune command: all RX channels tuned with one UHD command_time "
            f"(lead {self._timed_tune_lead_s*1000.0:.0f} ms)."
        )
        lines.append(
            "Stream command: start_cont with stream_now=false, time_spec ~10ms in future "
            "(aligned start; avoid retuning clock/time while streaming)."
        )
        try:
            n_mboards = int(self._usrp.get_num_mboards())
            lines.append(f"Mboards detected: {n_mboards}")
            for m in range(n_mboards):
                try:
                    lines.append(f"Mboard{m} name: {self._usrp.get_mboard_name(m)}")
                except Exception:
                    pass
                try:
                    lines.append(f"Mboard{m} clock source: {self._usrp.get_clock_source(m)}")
                except Exception:
                    pass
                try:
                    lines.append(f"Mboard{m} time source: {self._usrp.get_time_source(m)}")
                except Exception:
                    pass
                try:
                    sens_names = list(self._usrp.get_mboard_sensor_names(m))
                    lines.append(f"Mboard{m} sensors: {sens_names}")
                    for sn in sens_names:
                        try:
                            sval = self._usrp.get_mboard_sensor(sn, m).to_pp_string()
                        except Exception:
                            sval = str(self._usrp.get_mboard_sensor(sn, m))
                        lines.append(f"Mboard{m} sensor {sn}: {sval}")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            lines.append(f"RX subdev spec: {self._usrp.get_rx_subdev_spec()}")
        except Exception:
            pass
        # Clarify channel numbering: UHD has fixed physical channel indices (0..N-1),
        # while we may request a custom order for the multi-channel streamer.
        try:
            ch_list = list(self._cfg.channels)
            lines.append("Requested channel order (streamer index -> USRP channel):")
            for i, ch in enumerate(ch_list):
                lines.append(
                    f"  Streamer[{i}] -> Ch{ch} ({self._rx_antenna_for_channel(ch)})"
                )
        except Exception:
            pass
        if self._cfg.recv_frame_size < 8000 or self._cfg.send_frame_size < 8000:
            lines.append(
                "WARN transport frame size below UHD recommendation (>=8000). "
                "Throughput and stability may be degraded."
            )
        for ch in self._cfg.channels:
            try:
                rx_info = self._usrp.get_usrp_rx_info(ch)
                if isinstance(rx_info, dict):
                    dboard = rx_info.get("rx_id", "unknown")
                    serial = rx_info.get("rx_serial", "unknown")
                    ant = rx_info.get("rx_antenna", self._cfg.antenna)
                    lines.append(
                        f"Ch{ch} frontend: dboard={dboard}, serial={serial}, antenna={ant}"
                    )
            except Exception:
                pass
            rate_act = float(self._usrp.get_rx_rate(ch))
            bw_act = self._usrp.get_rx_bandwidth(ch)
            try:
                bw_hz = float(bw_act)
            except Exception:
                bw_hz = float(bw_act.start()) if hasattr(bw_act, "start") else float(bw_act)
            line = (
                f"Ch{ch} readback: rate={rate_act/1e6:.3f} Msps, "
                f"frontend_bw={bw_hz/1e6:.3f} MHz"
            )
            if abs(rate_act - self._cfg.sample_rate) > 1.0:
                line += " [WARN rate mismatch]"
            try:
                lo_locked = self._usrp.get_rx_sensor("lo_locked", ch).to_bool()
                line += f", lo_locked={lo_locked}"
            except Exception:
                pass
            try:
                lo_source = self._usrp.get_rx_lo_source("all", ch)
                lo_export = self._usrp.get_rx_lo_export_enabled("all", ch)
                line += f", lo_source={lo_source}, lo_export={lo_export}"
            except Exception:
                pass
            if self._cfg.twinrx_lo_sharing:
                lines.extend(self._rx_lo_stage_report_lines(ch))
            try:
                rx_sensor_names = list(self._usrp.get_rx_sensor_names(ch))
                line += f", sensors={rx_sensor_names}"
                for sn in rx_sensor_names:
                    try:
                        sval = self._usrp.get_rx_sensor(sn, ch).to_pp_string()
                    except Exception:
                        sval = str(self._usrp.get_rx_sensor(sn, ch))
                    lines.append(f"Ch{ch} sensor {sn}: {sval}")
            except Exception:
                pass
            lines.append(line)
        return lines

    def _verify_x300_fpga_image(self) -> list[str]:
        """Check the reported X300/X310 FPGA image flavor when UHD exposes it."""

        report: list[str] = []
        unknown: list[int] = []
        for mboard in self._x300_mboard_indices():
            evidence = self._mboard_fpga_evidence_text(mboard)
            flavor = _fpga_image_flavor_from_text(evidence)
            if flavor == _REQUIRED_X300_FPGA_IMAGE:
                report.append(f"Mboard{mboard} FPGA image: {_REQUIRED_X300_FPGA_IMAGE}")
                continue
            if flavor:
                raise RuntimeError(
                    "USRP X300/X310 is running the "
                    f"{flavor} FPGA image; this runtime requires "
                    f"{_REQUIRED_X300_FPGA_IMAGE} for the 10GbE/XG path. "
                    "Run `./setup.sh`, then power-cycle the USRP if UHD requests it."
                )
            unknown.append(mboard)

        if unknown:
            boards = ",".join(str(mboard) for mboard in unknown)
            report.append(
                "Mboard FPGA image: not reported by UHD "
                f"(mboard_indices={boards}); XG could not be verified from runtime metadata."
            )
        return report

    def _x300_mboard_indices(self) -> list[int]:
        try:
            n_mboards = int(self._usrp.get_num_mboards())
        except Exception:
            return [0]
        indices: list[int] = []
        for mboard in range(n_mboards):
            try:
                name = str(self._usrp.get_mboard_name(mboard)).lower()
            except Exception:
                name = ""
            if not name or any(marker in name for marker in _X300_MBOARD_NAME_MARKERS):
                indices.append(mboard)
        return indices

    def _mboard_fpga_evidence_text(self, mboard: int) -> str:
        fields: list[str] = []
        try:
            fields.append(f"name={self._usrp.get_mboard_name(mboard)}")
        except Exception:
            pass
        try:
            sensor_names = list(self._usrp.get_mboard_sensor_names(mboard))
        except Exception:
            sensor_names = []
        for sensor_name in sensor_names:
            name = str(sensor_name)
            try:
                sensor = self._usrp.get_mboard_sensor(sensor_name, mboard)
                try:
                    value = sensor.to_pp_string()
                except Exception:
                    value = str(sensor)
            except Exception:
                continue
            fields.append(f"{name}={value}")
        return "\n".join(fields)

    def _rx_antenna_report(self) -> str:
        return ",".join(f"ch{ch}:{self._rx_antenna_for_channel(ch)}" for ch in self._cfg.channels)

    def _rx_lo_report(self) -> str:
        return ",".join(
            f"ch{ch}:{self._rx_lo_source_for_channel(ch)}"
            f"/export={self._rx_lo_export_for_channel(ch)}"
            for ch in self._cfg.channels
        )

    # -------------------------------------------------------------------------
    # RX Streaming Control
    # -------------------------------------------------------------------------

    def _steady_recv_timeout_s(self) -> float:
        chunk_s = float(self._cfg.samples_per_chunk) / max(
            1.0, float(self._cfg.sample_rate)
        )
        return min(0.25, max(0.01, chunk_s * 3.0))

    def recv_chunk(self) -> RxChunkResult:
        if _RXEC is None:
            raise RuntimeError("UHD RX metadata types are unavailable.")
        if self._stopping:
            chunk = np.zeros((len(self._cfg.channels), 0), dtype=np.complex64)
            return RxChunkResult(
                chunk=chunk,
                state="timeout",
                got_samples=0,
                error_code="stopping",
                out_of_sequence=False,
                time_spec_s=None,
            )
        if not self._started:
            self._issue_start_cont()
        n = self._cfg.samples_per_chunk
        chunk = np.empty((len(self._cfg.channels), n), dtype=np.complex64)
        timeout_s = 0.05 if self._startup_recv_pending else self._steady_recv_timeout_s()
        got = self._rx_streamer.recv(chunk, self._metadata, timeout=timeout_s)
        code = self._metadata.error_code
        is_overflow = code in (_RXEC.overflow, _RXEC.late)
        is_timeout = code == _RXEC.timeout
        error_code = _metadata_error_code_name(code)
        out_of_sequence = bool(getattr(self._metadata, "out_of_sequence", False))
        time_spec_s = _metadata_time_spec_s(self._metadata)
        if got > 0 or time.monotonic() >= self._startup_recv_deadline_monotonic:
            self._startup_recv_pending = False
        if got <= 0:
            if is_overflow:
                return RxChunkResult(
                    chunk=chunk[:, :0],
                    state="overflow",
                    got_samples=0,
                    error_code=error_code,
                    out_of_sequence=out_of_sequence,
                    time_spec_s=time_spec_s,
                )
            if is_timeout:
                return RxChunkResult(
                    chunk=chunk[:, :0],
                    state="timeout",
                    got_samples=0,
                    error_code=error_code,
                    out_of_sequence=out_of_sequence,
                    time_spec_s=time_spec_s,
                )
            return RxChunkResult(
                chunk=chunk[:, :0],
                state="other",
                got_samples=0,
                error_code=error_code,
                out_of_sequence=out_of_sequence,
                time_spec_s=time_spec_s,
            )
        if is_overflow:
            # UHD may report overflow (or late) and still return valid samples. Keep draining.
            return RxChunkResult(
                chunk=chunk[:, :got],
                state="overflow",
                got_samples=int(got),
                error_code=error_code,
                out_of_sequence=out_of_sequence,
                time_spec_s=time_spec_s,
            )
        if is_timeout:
            return RxChunkResult(
                chunk=chunk[:, :got],
                state="timeout",
                got_samples=int(got),
                error_code=error_code,
                out_of_sequence=out_of_sequence,
                time_spec_s=time_spec_s,
            )
        return RxChunkResult(
            chunk=chunk[:, :got],
            state="ok",
            got_samples=int(got),
            error_code=error_code,
            out_of_sequence=out_of_sequence,
            time_spec_s=time_spec_s,
        )

    def restart_stream(self) -> None:
        if self._stopping:
            return
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        cmd.stream_now = True
        self._rx_streamer.issue_stream_cmd(cmd)
        self._started = False
        self._startup_recv_pending = False
        self._startup_recv_deadline_monotonic = 0.0
        self._issue_start_cont()

    def stop(self) -> None:
        self._stopping = True
        self.pause_stream()

    def pause_stream(self) -> None:
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        cmd.stream_now = True
        self._rx_streamer.issue_stream_cmd(cmd)
        self._started = False
        self._startup_recv_pending = False
        self._startup_recv_deadline_monotonic = 0.0


def _fpga_image_flavor_from_text(text: str) -> str | None:
    normalized = str(text)
    for line in normalized.splitlines():
        lowered = line.lower()
        if not any(marker in lowered for marker in _FPGA_IMAGE_MARKERS):
            continue
        match = re.search(r"usrp_x300_fpga_([hx]g)\.bit", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
        match = re.search(r"\b([hx]g)\b", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _metadata_error_code_name(code: object) -> str:
    name = getattr(code, "name", None)
    if name is not None:
        return str(name)
    text = str(code)
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _metadata_time_spec_s(metadata: object) -> float | None:
    if not bool(getattr(metadata, "has_time_spec", False)):
        return None
    time_spec = getattr(metadata, "time_spec", None)
    if time_spec is None:
        return None
    get_real_secs = getattr(time_spec, "get_real_secs", None)
    if callable(get_real_secs):
        try:
            return float(get_real_secs())
        except Exception:
            return None
    try:
        return float(time_spec)
    except Exception:
        return None
