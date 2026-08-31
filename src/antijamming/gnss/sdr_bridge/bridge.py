"""Public GNSS-SDR FIFO bridge class."""

from __future__ import annotations

import atexit
import errno
import logging
import os
import pty
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from antijamming.config import StreamConfig

from .accuracy import AccuracyMixin
from .cno import CnoMixin
from .config_renderer import ConfigRendererMixin
from .fifo import (
    PER_SOURCE_FIFO_STRIPE_SAMPLES,
    FifoMixin,
    complex64_contiguous_vector,
)
from .models import _SatKey
from .nmea import NmeaMixin
from .observable_state import ObservableStateMixin
from .output_monitor import OutputMonitorMixin
from .process import ProcessMixin
from .receiver_state import ReceiverStateMixin
from .snapshot import SnapshotMixin
from .udp_monitor import UdpMonitorMixin


class GnssFifoWriteStall(RuntimeError):
    """A bounded FIFO write failure with exact stalled-source provenance."""

    def __init__(
        self,
        *,
        stalled_sources: tuple[int, ...],
        stalled_paths: tuple[Path, ...],
        pending_bytes: tuple[int, ...],
        timeout_s: float,
    ) -> None:
        self.stalled_sources = stalled_sources
        self.stalled_paths = stalled_paths
        self.pending_bytes = pending_bytes
        self.timeout_s = float(timeout_s)
        source_text = ",".join(str(value) for value in stalled_sources)
        path_text = ",".join(str(value) for value in stalled_paths)
        pending_text = ",".join(str(value) for value in pending_bytes)
        super().__init__(
            "GNSS-SDR FIFO reader stalled: "
            f"sources={source_text} paths={path_text} "
            f"pending_bytes={pending_text} timeout_s={self.timeout_s:.3f}"
        )


class GnssSdrBridge(
    SnapshotMixin,
    ProcessMixin,
    FifoMixin,
    ConfigRendererMixin,
    OutputMonitorMixin,
    ReceiverStateMixin,
    ObservableStateMixin,
    NmeaMixin,
    CnoMixin,
    UdpMonitorMixin,
    AccuracyMixin,
):
    """Manage the GNSS-SDR subprocess, FIFO writes, logs, and receiver snapshot."""

    def __init__(
        self,
        config: StreamConfig,
        loggers: dict[str, logging.Logger],
        *,
        session_id: str | None = None,
        session_dir: Path | None = None,
    ) -> None:
        # Config and loggers.
        self._cfg = config
        self._log = loggers["gnss"]
        self._handoff_log = loggers.get("handoff", self._log)
        self._app_log = loggers["app"]
        self._err_log = loggers["errors"]

        # Runtime paths and process handles.
        self._set_session_paths(
            config.gnss_sdr_runtime_dir,
            config.gnss_sdr_log_dir,
        )
        self._tracking_state_archive_dir = (
            Path(config.log_dir).expanduser().resolve() / "tracking-state"
        )
        self._runtime_session_id = str(session_id) if session_id else None
        self._runtime_session_dir = (
            Path(session_dir).expanduser().resolve() if session_dir is not None else None
        )
        self._tracking_state_log_path: Path | None = None
        self._tracking_state_compat_path: Path | None = None
        self._tracking_state_handle = None
        self._tracking_state_sequence = 0
        self._tracking_state_started_monotonic_ns = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._fifo_fd: int | None = None
        self._fifo_fds: list[int] = []
        self._stdout_thread: threading.Thread | None = None
        self._stdout_handle = None
        self._nmea_thread: threading.Thread | None = None
        self._nmea_master_fd: int | None = None
        self._nmea_slave_fd: int | None = None
        self._nmea_tty_path: str | None = None
        self._atexit_registered = False

        # FIFO health counters.
        self._drop_count = 0
        self._write_count = 0
        self._write_bytes = 0
        self._write_time_total_s = 0.0
        self._write_max_latency_s = 0.0
        self._write_warn_threshold_s = 0.05
        # Normal ten-source writes complete in a few milliseconds.  A quarter
        # second allows transient scheduler jitter without letting a blocked
        # reader consume the backend's multi-second raw queue unnoticed.
        self._fifo_write_stall_timeout_s = 0.25
        self._pipe_size_bytes: int | None = None
        self._fifo_source_bytes: list[int] = []
        self._fifo_max_source_lead_bytes = 0
        self._glog_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._fifo_lock = threading.Lock()

        # Receiver state extracted from GNSS-SDR logs and outputs.
        self._state_lock = threading.Lock()
        self._prn_states: dict[_SatKey, dict[str, object]] = {}
        self._channel_prn: dict[int, _SatKey] = {}
        self._receiver_time_s: int | None = None
        self._session_epoch_s = 0.0
        self._pvt_output_seen = False
        self._pvt_observed_monotonic_s: float | None = None
        self._pvt_observation_count: int | None = None

        # GNSS monitor state.
        self._tracking_cn0_by_channel: dict[int, float] = {}
        self._tracking_carrier_lock_by_channel: dict[int, float] = {}
        self._tracking_prn_by_channel: dict[int, int] = {}
        self._tracking_cno_history: dict[tuple[int, _SatKey], list[float]] = {}
        self._tracking_cno_stable_windows: dict[tuple[int, _SatKey], int] = {}
        self._sat_geometry_by_prn: dict[_SatKey, dict[str, object]] = {}
        self._used_in_fix_prns: set[_SatKey] = set()
        self._used_in_fix_observed_monotonic_s: float | None = None
        self._last_nmea_utc_s: float | None = None
        self._last_nmea_utc_text: str | None = None
        self._nmea_tty_line_count = 0
        self._nmea_tty_last_monotonic_s: float | None = None
        self._latest_tracking_monitor_by_prn: dict[_SatKey, dict[str, object]] = {}
        self._latest_observables_by_prn: dict[_SatKey, dict[str, object]] = {}
        self._latest_truth_position = self._load_truth_position()
        self._recent_receiver_events: dict[str, float] = {}
        self._output_io_refresh_ts = 0.0
        self._output_io_metrics: dict[str, object] = {}
        self._last_output_io_sample: tuple[float, int] | None = None
        self._udp_monitor_threads: list[threading.Thread] = []
        self._udp_monitor_sockets = []
        self._udp_monitor_stats: dict[str, object] = {}
        self._udp_parse_error_log_ts: dict[str, float] = {}
        self._udp_monitor_logged: dict[tuple[str, int], tuple[_SatKey, float, float]] = {}
        self._pvt_udp_points: list[dict[str, object]] = []
        self._snapshot_perf_lock = threading.Lock()
        self._snapshot_perf_stats: dict[str, dict[str, float]] = {}
        self._last_snapshot_perf_log_ts = 0.0

        # Accuracy display cache and log throttling.
        self._truth_warning_logged = False
        self._last_accuracy_log_ts = 0.0
        self._last_accuracy_point_count = 0
        self._latest_accuracy: dict[str, object] = {}
        self._latest_accuracy_observed_monotonic_s: float | None = None

    def _set_session_paths(self, runtime_dir: Path, log_dir: Path) -> None:
        """Set every path derived from a GNSS-SDR runtime/log directory pair."""

        self._runtime_dir = Path(runtime_dir).expanduser().resolve()
        self._log_dir = Path(log_dir).expanduser().resolve()
        shared_phase = bool(
            getattr(self._cfg, "gnss_shared_u1_phase_compensation_enabled", False)
        )
        satellites = tuple(
            int(value)
            for value in getattr(self._cfg, "gnss_shared_u1_phase_satellites", ())
        )
        if shared_phase:
            source_count = (
                len(satellites)
                if satellites
                else max(1, int(self._cfg.gnss_1c_channel_count))
            )
            self._fifo_paths = [
                self._runtime_dir
                / (
                    f"gnss_iq_G{satellites[index]:02d}.fifo"
                    if satellites
                    else f"gnss_iq_channel_{index:02d}.fifo"
                )
                for index in range(source_count)
            ]
        else:
            self._fifo_paths = [self._runtime_dir / "gnss_iq.fifo"]
        self._fifo_path = self._fifo_paths[0]
        self._config_path = self._runtime_dir / "fifo_gps_l1.conf"
        self._console_log_path = self._runtime_dir / "console.log"
        self._receiver_log_path = self._log_dir / "receiver.log"
        self._outputs_dir = self._runtime_dir / "outputs"
        self._signal_source_outputs_dir = self._outputs_dir / "signal_source"
        self._signal_conditioner_outputs_dir = self._outputs_dir / "signal_conditioner"
        self._tracking_outputs_dir = self._outputs_dir / "tracking"
        self._acquisition_outputs_dir = self._outputs_dir / "acquisition"
        self._telemetry_outputs_dir = self._outputs_dir / "telemetry"
        self._observables_outputs_dir = self._outputs_dir / "observables"
        self._pvt_outputs_dir = self._outputs_dir / "pvt"

    @property
    def active(self) -> bool:
        return (
            bool(self._fifo_fds)
            and len(self._fifo_fds) == len(self._fifo_paths)
            and self._proc is not None
            and self._proc.poll() is None
        )

    def start(self) -> bool:
        if not self._cfg.gnss_sdr_enable:
            self._log.info("GNSS-SDR bridge disabled by configuration.")
            return False

        # Product runs prefer the repo-local GNSS-SDR build so behavior matches
        # the rendered FIFO config and patched receiver output layout.
        exe_path = self._resolve_local_executable()
        if exe_path is None:
            msg = (
                "Repo-local GNSS-SDR executable not found. Expected "
                f"{self._cfg.gnss_sdr_install_dir / 'gnss-sdr'}, "
                f"{self._cfg.gnss_sdr_install_dir / 'bin' / 'gnss-sdr'}, or "
                f"{self._cfg.gnss_sdr_build_dir / 'src' / 'main' / 'gnss-sdr'} "
                "(legacy build-usman is also checked for compatibility)."
            )
            system_gnss = self._system_gnss_sdr_path()
            if system_gnss is not None:
                msg += (
                    f" System gnss-sdr at {system_gnss} is being ignored because it is not repo-local."
                )
            if self._cfg.gnss_sdr_require_local:
                raise FileNotFoundError(msg)
            self._log.warning("%s", msg)
            return False

        try:
            return self._start_resolved(exe_path)
        except BaseException:
            self.stop("startup failure")
            raise

    def _start_resolved(self, exe_path: Path) -> bool:
        """Start after executable resolution, rolling owned resources back on error."""

        self._terminate_matching_stale_processes()
        self._reset_runtime_dir()
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        self._signal_source_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._signal_conditioner_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._tracking_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._acquisition_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._telemetry_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._observables_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._pvt_outputs_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_fifo()
        # A FIFO gives GNSS-SDR a live complex64 source while keeping SDR capture
        # in this Python process.
        for fifo_path in self._fifo_paths:
            os.mkfifo(fifo_path)
        self._console_log_path.write_text("", encoding="utf-8")
        self._receiver_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._receiver_log_path.write_text("", encoding="utf-8")
        self._prepare_nmea_tty()
        rendered_config = self._render_config()
        self._config_path.write_text(rendered_config, encoding="utf-8")
        self._archive_runtime_artifacts(config_only=True)
        self._log_rendered_config_summary(rendered_config)
        self._monitor_stop.clear()
        self._session_epoch_s = time.time()
        self._tracking_state_started_monotonic_ns = time.monotonic_ns()
        self._tracking_state_sequence = 0
        self._open_tracking_state_log()
        with self._state_lock:
            self._prn_states.clear()
            self._channel_prn.clear()
            self._receiver_time_s = None
            self._pvt_output_seen = False
            self._pvt_observed_monotonic_s = None
            self._pvt_observation_count = None
            self._tracking_cn0_by_channel.clear()
            self._tracking_prn_by_channel.clear()
            self._tracking_cno_history.clear()
            self._tracking_cno_stable_windows.clear()
            self._sat_geometry_by_prn.clear()
            self._used_in_fix_prns.clear()
            self._used_in_fix_observed_monotonic_s = None
            self._last_nmea_utc_s = None
            self._last_nmea_utc_text = None
            self._nmea_tty_line_count = 0
            self._nmea_tty_last_monotonic_s = None
            self._latest_tracking_monitor_by_prn.clear()
            self._latest_observables_by_prn.clear()
            self._udp_monitor_stats.clear()
            self._udp_parse_error_log_ts.clear()
            self._udp_monitor_logged.clear()
            self._pvt_udp_points.clear()
            self._last_accuracy_log_ts = 0.0
            self._last_accuracy_point_count = 0
            self._latest_accuracy.clear()
            self._latest_accuracy_observed_monotonic_s = None
        self._output_io_refresh_ts = 0.0
        self._output_io_metrics = {}
        self._last_output_io_sample = None
        self._start_udp_monitors()

        env = os.environ.copy()
        env.setdefault("GLOG_logbufsecs", "0")
        # A PTY keeps GNSS-SDR stdout line-buffered so state parsing reacts
        # quickly during startup and acquisition.
        master_fd, slave_fd = pty.openpty()
        try:
            self._proc = subprocess.Popen(
                self._gnss_sdr_launch_args(exe_path),
                cwd=self._runtime_dir,
                stdout=slave_fd,
                stderr=slave_fd,
                text=False,
                bufsize=0,
                env=env,
                start_new_session=True,
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        if not self._atexit_registered:
            atexit.register(self.stop, "python shutdown")
            self._atexit_registered = True
        self._log.info(
            "GNSS-SDR pid started: pid=%d config=%s runtime_dir=%s",
            int(self._proc.pid),
            self._config_path,
            self._runtime_dir,
        )
        try:
            self._stdout_handle = os.fdopen(
                master_fd,
                "rb",
                buffering=0,
            )
        except BaseException:
            os.close(master_fd)
            raise
        stdout_thread = threading.Thread(
            target=self._drain_stdout,
            name="gnss_sdr_stdout",
            daemon=True,
        )
        stdout_thread.start()
        self._stdout_thread = stdout_thread
        self._start_nmea_tty_reader()
        glog_thread = threading.Thread(
            target=self._monitor_glog_files,
            name="gnss_sdr_glog",
            daemon=True,
        )
        glog_thread.start()
        self._glog_thread = glog_thread
        startup_timeout_s = float(self._cfg.gnss_sdr_startup_timeout_s)
        timeout_label = "none" if startup_timeout_s <= 0.0 else f"{startup_timeout_s:.1f}s"
        self._report_startup(
            "GNSS-SDR startup at %.3f Msps: checking cached FFTW plans in %s "
            "(timeout=%s). A cached rate normally starts in under a second; a new "
            "rate can take several minutes once while FFTW measures new plans.",
            float(self._cfg.sample_rate) / 1e6,
            str(Path.home() / ".gr_fftw_wisdom"),
            timeout_label,
        )
        self._fifo_fds = []
        for fifo_path in self._fifo_paths:
            self._fifo_fds.append(
                self._open_fifo_writer(
                    timeout_s=startup_timeout_s,
                    fifo_path=fifo_path,
                )
            )
        self._fifo_fd = self._fifo_fds[0]
        self._drop_count = 0
        self._write_count = 0
        self._write_bytes = 0
        self._write_time_total_s = 0.0
        self._write_max_latency_s = 0.0
        self._fifo_source_bytes = [0 for _ in self._fifo_paths]
        self._fifo_max_source_lead_bytes = 0

        self._log.info("Launching product GNSS-SDR: %s", exe_path)
        self._log.info("GNSS-SDR config: %s", self._config_path)
        self._log.info("GNSS IQ FIFO(s): %s", ", ".join(map(str, self._fifo_paths)))
        self._log.info("GNSS-SDR console log: %s", self._console_log_path)
        self._log.info("GNSS-SDR receiver log: %s", self._receiver_log_path)
        self._log.info("GNSS-SDR runtime dir: %s", self._runtime_dir)
        self._log.info("GNSS-SDR log dir: %s", self._log_dir)
        self._handoff_log.info(
            "GUI->GNSS handoff paths: runtime_dir=%s log_dir=%s outputs_dir=%s tracking_dir=%s pvt_dir=%s fifo=%s config=%s console=%s receiver_log=%s",
            self._runtime_dir,
            self._log_dir,
            self._outputs_dir,
            self._tracking_outputs_dir,
            self._pvt_outputs_dir,
            ",".join(map(str, self._fifo_paths)),
            self._config_path,
            self._console_log_path,
            self._receiver_log_path,
        )
        self._log.info(
            "GNSS FIFO writer ready: blocking=false fair_poll=true stall_timeout_s=%.3f pipe_size=%s chunk_bytes=%d sample_rate=%.3f Msps software_if_bw=%.3f MHz",
            self._fifo_write_stall_timeout_s,
            self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
            int(self._cfg.samples_per_chunk) * np.dtype(np.complex64).itemsize,
            float(self._cfg.sample_rate) / 1e6,
            self.input_filter_bandwidth_hz / 1e6,
        )
        self._warn_if_gps_l1_is_outside_capture_band()
        self._app_log.info("GNSS-SDR bridge active: %s", exe_path)
        return True

    def write(self, samples: np.ndarray) -> bool:
        source_count = len(self._fifo_paths)
        sample_array = np.asarray(samples)
        if source_count == 1:
            arrays = [complex64_contiguous_vector(sample_array)]
        else:
            if sample_array.ndim != 2 or sample_array.shape[0] != source_count:
                raise ValueError(
                    "shared-U1 phase FIFO write expects sources x samples: "
                    f"got {sample_array.shape}, sources={source_count}"
                )
            arrays = [
                complex64_contiguous_vector(sample_array[index])
                for index in range(source_count)
            ]
        if not arrays or arrays[0].size == 0:
            return True
        if any(array.size != arrays[0].size for array in arrays):
            raise ValueError("shared-U1 phase FIFO streams have unequal sample counts")
        started_at = time.monotonic()
        try:
            with self._fifo_lock:
                fifo_fds = tuple(self._fifo_fds)
                if len(fifo_fds) != len(arrays):
                    return False
                if len(self._fifo_source_bytes) != len(arrays):
                    self._fifo_source_bytes = [0 for _ in arrays]
                    self._fifo_max_source_lead_bytes = 0
                stripe_samples = (
                    arrays[0].size
                    if len(arrays) == 1
                    else PER_SOURCE_FIFO_STRIPE_SAMPLES
                )
                for start in range(0, arrays[0].size, stripe_samples):
                    stop = min(arrays[0].size, start + stripe_samples)
                    self._write_fifo_stripe_fair(
                        fifo_fds=fifo_fds,
                        arrays=arrays,
                        start=start,
                        stop=stop,
                    )
            elapsed_s = time.monotonic() - started_at
            self._write_count += 1
            payload_bytes = sum(array.nbytes for array in arrays)
            self._write_bytes += payload_bytes
            self._write_time_total_s += elapsed_s
            self._write_max_latency_s = max(self._write_max_latency_s, elapsed_s)
            if elapsed_s >= self._write_warn_threshold_s:
                avg_ms = 1000.0 * (self._write_time_total_s / max(1, self._write_count))
                self._log.warning(
                    "GNSS FIFO write latency %.1f ms for %d bytes (writes=%d avg=%.1f ms max=%.1f ms pipe=%s).",
                    elapsed_s * 1000.0,
                    payload_bytes,
                    self._write_count,
                    avg_ms,
                    self._write_max_latency_s * 1000.0,
                    self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
                )
            return True
        except GnssFifoWriteStall:
            self._drop_count += 1
            raise
        except BrokenPipeError as exc:
            raise RuntimeError("GNSS-SDR FIFO reader disconnected") from exc
        except OSError as exc:
            raise RuntimeError(f"GNSS-SDR FIFO write failed: {exc}") from exc

    def _write_fifo_stripe_fair(
        self,
        *,
        fifo_fds: tuple[int, ...],
        arrays: list[np.ndarray],
        start: int,
        stop: int,
    ) -> None:
        """Write one equal-sized stripe without blocking behind one source.

        All FIFO descriptors remain nonblocking.  ``poll`` services whichever
        GNSS-SDR readers are currently writable, while a single deadline bounds
        the whole stripe.  Completion of every source is required, so this does
        not silently drop samples or advance one PRN stream to the next stripe.
        """

        payloads = [memoryview(array[start:stop]).cast("B") for array in arrays]
        offsets = [0 for _ in payloads]
        poller = select.poll()
        fd_to_source: dict[int, int] = {}
        event_mask = select.POLLOUT | select.POLLERR | select.POLLHUP | select.POLLNVAL
        for source_index, fifo_fd in enumerate(fifo_fds):
            poller.register(fifo_fd, event_mask)
            fd_to_source[fifo_fd] = source_index

        pending = set(range(len(payloads)))
        timeout_s = max(0.001, float(self._fifo_write_stall_timeout_s))
        deadline = time.monotonic() + timeout_s
        while pending:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0.0:
                self._raise_fifo_write_stall(
                    pending=pending,
                    payloads=payloads,
                    offsets=offsets,
                    timeout_s=timeout_s,
                )
            events = poller.poll(max(1, int(remaining_s * 1000.0)))
            if not events:
                self._raise_fifo_write_stall(
                    pending=pending,
                    payloads=payloads,
                    offsets=offsets,
                    timeout_s=timeout_s,
                )
            for fifo_fd, event in events:
                source_index = fd_to_source.get(fifo_fd)
                if source_index is None or source_index not in pending:
                    continue
                if event & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    path = self._fifo_paths[source_index]
                    raise RuntimeError(
                        "GNSS-SDR FIFO reader disconnected: "
                        f"source={source_index} path={path} poll_event={event}"
                    )
                if not event & select.POLLOUT:
                    continue
                payload = payloads[source_index]
                offset = offsets[source_index]
                try:
                    written = os.write(fifo_fd, payload[offset:])
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    raise
                if written <= 0:
                    raise RuntimeError(
                        "GNSS-SDR FIFO write returned no progress: "
                        f"source={source_index} path={self._fifo_paths[source_index]}"
                    )
                offsets[source_index] += written
                self._fifo_source_bytes[source_index] += written
                if len(self._fifo_source_bytes) > 1:
                    source_lead = max(self._fifo_source_bytes) - min(
                        self._fifo_source_bytes
                    )
                    self._fifo_max_source_lead_bytes = max(
                        self._fifo_max_source_lead_bytes,
                        source_lead,
                    )
                if offsets[source_index] >= len(payload):
                    pending.remove(source_index)
                    poller.unregister(fifo_fd)

    def _raise_fifo_write_stall(
        self,
        *,
        pending: set[int],
        payloads: list[memoryview],
        offsets: list[int],
        timeout_s: float,
    ) -> None:
        stalled_sources = tuple(sorted(pending))
        raise GnssFifoWriteStall(
            stalled_sources=stalled_sources,
            stalled_paths=tuple(self._fifo_paths[index] for index in stalled_sources),
            pending_bytes=tuple(
                len(payloads[index]) - offsets[index] for index in stalled_sources
            ),
            timeout_s=timeout_s,
        )

    def stop(self, reason: str = "normal stop") -> None:
        self._monitor_stop.set()
        self._stop_udp_monitors()
        self._close_tracking_state_log()
        with self._fifo_lock:
            fifo_fds = tuple(self._fifo_fds)
            self._fifo_fds = []
            self._fifo_fd = None
            for fifo_fd in fifo_fds:
                try:
                    os.close(fifo_fd)
                except OSError:
                    pass

        if self._proc is not None:
            pid = int(self._proc.pid)
            try:
                if self._proc.poll() is None:
                    self._terminate_process_group_or_process(pid)
                    try:
                        self._proc.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        self._kill_process_group_or_process(pid)
                        self._proc.wait(timeout=2.0)
                        self._log.warning(
                            "GNSS-SDR pid killed after stop timeout: pid=%d reason=%s",
                            pid,
                            reason,
                        )
                    else:
                        self._log.info(
                            "GNSS-SDR pid stopped: pid=%d reason=%s",
                            pid,
                            reason,
                        )
                else:
                    self._log.info(
                        "GNSS-SDR pid already exited: pid=%d returncode=%s reason=%s",
                        pid,
                        self._proc.returncode,
                        reason,
                    )
            except Exception as exc:
                self._err_log.error("Failed to stop GNSS-SDR cleanly: %s", exc)
            self._proc = None

        self._stop_nmea_tty_reader()
        stdout_handle = self._stdout_handle
        self._stdout_handle = None
        if stdout_handle is not None:
            try:
                stdout_handle.close()
            except OSError:
                pass
        stdout_thread = self._stdout_thread
        if (
            stdout_thread is not None
            and stdout_thread.ident is not None
            and stdout_thread is not threading.current_thread()
        ):
            stdout_thread.join(timeout=1.0)
        if stdout_thread is not None and stdout_thread.is_alive():
            self._err_log.error(
                "GNSS-SDR stdout monitor did not stop within 1.0 s; "
                "retaining the live thread reference."
            )
        elif self._stdout_thread is stdout_thread:
            self._stdout_thread = None
        glog_thread = self._glog_thread
        if (
            glog_thread is not None
            and glog_thread.ident is not None
            and glog_thread is not threading.current_thread()
        ):
            glog_thread.join(timeout=1.0)
        if glog_thread is not None and glog_thread.is_alive():
            self._err_log.error(
                "GNSS-SDR glog monitor did not stop within 1.0 s; "
                "retaining the live thread reference."
            )
        elif self._glog_thread is glog_thread:
            self._glog_thread = None

        self._archive_runtime_artifacts(config_only=False)

        if self._write_count > 0 or self._drop_count > 0:
            avg_ms = 1000.0 * (self._write_time_total_s / max(1, self._write_count))
            self._log.info(
                "GNSS FIFO summary: writes=%d bytes=%d drops=%d avg_write_ms=%.2f max_write_ms=%.2f pipe=%s source_byte_spread=%d max_source_lead_samples=%d stripe_samples=%d",
                self._write_count,
                self._write_bytes,
                self._drop_count,
                avg_ms,
                self._write_max_latency_s * 1000.0,
                self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
                (
                    max(self._fifo_source_bytes) - min(self._fifo_source_bytes)
                    if self._fifo_source_bytes
                    else 0
                ),
                self._fifo_max_source_lead_bytes // np.dtype(np.complex64).itemsize,
                PER_SOURCE_FIFO_STRIPE_SAMPLES if len(self._fifo_paths) > 1 else 0,
            )

        self._cleanup_fifo()

    def _archive_runtime_artifacts(self, *, config_only: bool) -> None:
        """Copy the exact per-process GNSS-SDR evidence into this run."""

        if self._runtime_session_dir is None:
            return
        mappings = [(self._config_path, Path("gnss-sdr/runtime/fifo_gps_l1.conf"))]
        if not config_only:
            mappings.extend(
                [
                    (self._console_log_path, Path("gnss-sdr/runtime/console.log")),
                    (self._receiver_log_path, Path("gnss-sdr/glog/receiver.log")),
                ]
            )
            if self._pvt_outputs_dir.is_dir():
                for source in self._pvt_outputs_dir.iterdir():
                    if source.is_file():
                        mappings.append(
                            (
                                source,
                                Path("gnss-sdr/runtime/outputs/pvt") / source.name,
                            )
                        )
            if self._observables_outputs_dir.is_dir():
                for source in self._observables_outputs_dir.iterdir():
                    if source.is_file():
                        mappings.append(
                            (
                                source,
                                Path("gnss-sdr/runtime/outputs/observables") / source.name,
                            )
                        )
        for source, relative_destination in mappings:
            if not source.is_file():
                continue
            destination = self._runtime_session_dir / relative_destination
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            except OSError as exc:
                self._err_log.error(
                    "Failed archiving GNSS-SDR runtime artifact %s: %s",
                    source,
                    exc,
                )

    def _open_tracking_state_log(self) -> None:
        """Open a durable, per-run archive of GNSS tracking observables."""

        self._close_tracking_state_log()
        timestamp = time.strftime(
            "%Y%m%dT%H%M%SZ",
            time.gmtime(self._session_epoch_s),
        )
        compatibility_path = (
            self._tracking_state_archive_dir / f"tracking_{timestamp}_{os.getpid()}.jsonl"
        )
        path = (
            self._runtime_session_dir / "tracking_observables.jsonl"
            if self._runtime_session_dir is not None
            else compatibility_path
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._tracking_state_handle = path.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            self._tracking_state_log_path = None
            self._tracking_state_compat_path = None
            self._tracking_state_handle = None
            self._err_log.error("Failed opening GNSS tracking-state archive %s: %s", path, exc)
            return
        if path != compatibility_path:
            try:
                compatibility_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    compatibility_path.unlink()
                except FileNotFoundError:
                    pass
                os.link(path, compatibility_path)
            except OSError as exc:
                self._err_log.warning(
                    "Could not create tracking-state compatibility hardlink %s: %s",
                    compatibility_path,
                    exc,
                )
        self._tracking_state_log_path = path
        self._tracking_state_compat_path = compatibility_path
        self._handoff_log.info(
            "GNSS tracking-state archive: %s compatibility_path=%s session_id=%s",
            path,
            compatibility_path,
            self._runtime_session_id or "--",
        )

    def _close_tracking_state_log(self) -> None:
        handle = self._tracking_state_handle
        self._tracking_state_handle = None
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except OSError as exc:
            self._err_log.warning("Failed closing GNSS tracking-state archive: %s", exc)


__all__ = ["GnssSdrBridge"]
