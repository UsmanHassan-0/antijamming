"""Public GNSS-SDR FIFO bridge class."""

from __future__ import annotations

import atexit
import logging
import os
import pty
import subprocess
import threading
import time

import numpy as np

from antijamming.config import StreamConfig

from .accuracy import AccuracyMixin
from .cno import CnoMixin
from .config_renderer import ConfigRendererMixin
from .fifo import FifoMixin, complex64_contiguous_vector
from .models import _SatKey
from .nmea import NmeaMixin
from .observable_state import ObservableStateMixin
from .output_monitor import OutputMonitorMixin
from .process import ProcessMixin
from .receiver_state import ReceiverStateMixin
from .snapshot import SnapshotMixin
from .udp_monitor import UdpMonitorMixin


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

    def __init__(self, config: StreamConfig, loggers: dict[str, logging.Logger]) -> None:
        # Config and loggers.
        self._cfg = config
        self._log = loggers["gnss"]
        self._handoff_log = loggers.get("handoff", self._log)
        self._app_log = loggers["app"]
        self._err_log = loggers["errors"]

        # Runtime paths and process handles.
        self._runtime_dir = config.gnss_sdr_runtime_dir.expanduser().resolve()
        self._log_dir = config.gnss_sdr_log_dir.expanduser().resolve()
        self._proc: subprocess.Popen[bytes] | None = None
        self._fifo_fd: int | None = None
        self._fifo_path = self._runtime_dir / "gnss_iq.fifo"
        self._config_path = self._runtime_dir / "fifo_gps_l1.conf"
        self._console_log_path = self._runtime_dir / "console.log"
        self._receiver_log_path = self._log_dir / "receiver.log"
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
        self._pipe_size_bytes: int | None = None
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

        # GNSS-SDR output directories.
        self._outputs_dir = self._runtime_dir / "outputs"
        self._signal_source_outputs_dir = self._outputs_dir / "signal_source"
        self._signal_conditioner_outputs_dir = self._outputs_dir / "signal_conditioner"
        self._tracking_outputs_dir = self._outputs_dir / "tracking"
        self._acquisition_outputs_dir = self._outputs_dir / "acquisition"
        self._telemetry_outputs_dir = self._outputs_dir / "telemetry"
        self._observables_outputs_dir = self._outputs_dir / "observables"
        self._pvt_outputs_dir = self._outputs_dir / "pvt"

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

    @property
    def active(self) -> bool:
        return self._fifo_fd is not None and self._proc is not None and self._proc.poll() is None

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
        os.mkfifo(self._fifo_path)
        self._console_log_path.write_text("", encoding="utf-8")
        self._receiver_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._receiver_log_path.write_text("", encoding="utf-8")
        self._prepare_nmea_tty()
        rendered_config = self._render_config()
        self._config_path.write_text(rendered_config, encoding="utf-8")
        self._log_rendered_config_summary(rendered_config)
        self._monitor_stop.clear()
        self._session_epoch_s = time.time()
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
        self._stdout_handle = os.fdopen(
            master_fd,
            "rb",
            buffering=0,
        )
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout,
            name="gnss_sdr_stdout",
            daemon=True,
        )
        self._stdout_thread.start()
        self._start_nmea_tty_reader()
        self._glog_thread = threading.Thread(
            target=self._monitor_glog_files,
            name="gnss_sdr_glog",
            daemon=True,
        )
        self._glog_thread.start()
        try:
            self._fifo_fd = self._open_fifo_writer(timeout_s=5.0)
        except Exception:
            self.stop("startup failure")
            raise
        self._drop_count = 0
        self._write_count = 0
        self._write_bytes = 0
        self._write_time_total_s = 0.0
        self._write_max_latency_s = 0.0

        self._log.info("Launching product GNSS-SDR: %s", exe_path)
        self._log.info("GNSS-SDR config: %s", self._config_path)
        self._log.info("GNSS IQ FIFO: %s", self._fifo_path)
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
            self._fifo_path,
            self._config_path,
            self._console_log_path,
            self._receiver_log_path,
        )
        self._log.info(
            "GNSS FIFO writer ready: blocking=true pipe_size=%s chunk_bytes=%d sample_rate=%.3f Msps software_if_bw=%.3f MHz",
            self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
            int(self._cfg.samples_per_chunk) * np.dtype(np.complex64).itemsize,
            float(self._cfg.sample_rate) / 1e6,
            float(self._cfg.gnss_sdr_if_bandwidth_hz) / 1e6,
        )
        self._warn_if_gps_l1_is_outside_capture_band()
        self._app_log.info("GNSS-SDR bridge active: %s", exe_path)
        return True

    def write(self, samples: np.ndarray) -> bool:
        arr = complex64_contiguous_vector(samples)
        if arr.size == 0:
            return True

        payload = memoryview(arr).cast("B")
        started_at = time.monotonic()
        try:
            with self._fifo_lock:
                fifo_fd = self._fifo_fd
                if fifo_fd is None:
                    return False
                while payload:
                    written = os.write(fifo_fd, payload)
                    if written <= 0:
                        raise RuntimeError("GNSS-SDR FIFO write returned no progress")
                    payload = payload[written:]
            elapsed_s = time.monotonic() - started_at
            self._write_count += 1
            self._write_bytes += arr.nbytes
            self._write_time_total_s += elapsed_s
            self._write_max_latency_s = max(self._write_max_latency_s, elapsed_s)
            if elapsed_s >= self._write_warn_threshold_s:
                avg_ms = 1000.0 * (self._write_time_total_s / max(1, self._write_count))
                self._log.warning(
                    "GNSS FIFO write latency %.1f ms for %d bytes (writes=%d avg=%.1f ms max=%.1f ms pipe=%s).",
                    elapsed_s * 1000.0,
                    arr.nbytes,
                    self._write_count,
                    avg_ms,
                    self._write_max_latency_s * 1000.0,
                    self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
                )
            return True
        except BlockingIOError:
            self._drop_count += 1
            if self._drop_count == 1 or self._drop_count % 50 == 0:
                self._log.warning(
                    "GNSS-SDR FIFO backpressure: dropped %d IQ chunk(s).",
                    self._drop_count,
                )
            return False
        except BrokenPipeError as exc:
            raise RuntimeError("GNSS-SDR FIFO reader disconnected") from exc
        except OSError as exc:
            raise RuntimeError(f"GNSS-SDR FIFO write failed: {exc}") from exc

    def stop(self, reason: str = "normal stop") -> None:
        self._monitor_stop.set()
        self._stop_udp_monitors()
        with self._fifo_lock:
            fifo_fd = self._fifo_fd
            self._fifo_fd = None
            if fifo_fd is not None:
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
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=1.0)
            self._stdout_thread = None
        if self._stdout_handle is not None:
            try:
                self._stdout_handle.close()
            except OSError:
                pass
            self._stdout_handle = None
        if self._glog_thread is not None:
            self._glog_thread.join(timeout=1.0)
            self._glog_thread = None

        if self._write_count > 0 or self._drop_count > 0:
            avg_ms = 1000.0 * (self._write_time_total_s / max(1, self._write_count))
            self._log.info(
                "GNSS FIFO summary: writes=%d bytes=%d drops=%d avg_write_ms=%.2f max_write_ms=%.2f pipe=%s",
                self._write_count,
                self._write_bytes,
                self._drop_count,
                avg_ms,
                self._write_max_latency_s * 1000.0,
                self._pipe_size_bytes if self._pipe_size_bytes is not None else "unknown",
            )

        self._cleanup_fifo()


__all__ = ["GnssSdrBridge"]
