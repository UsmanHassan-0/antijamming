"""Threaded backend runtime for SDR capture, DSP, and GNSS handoff."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from antijamming.config import StreamConfig
from antijamming.gnss import GnssSdrBridge
from antijamming.gnss.sdr_bridge.constants import (
    PVT_DEGRADED_PDOP_THRESHOLD,
    PVT_LOW_OBSERVATION_COUNT,
    PVT_LOW_USED_SATELLITE_COUNT,
)
from antijamming.radio.transport import collect_host_transport_report
from antijamming.logging import reset_session_logs
from .latest_queue import put_latest
from .ui_metrics import RuntimeUiMetrics
from .work_items import BeamformingWorkItem, PhaseResult, PhaseWorkItem
from antijamming.radio.usrp import UsrpRxDevice
from antijamming.detection.jammer import JammerDetector, JammerDetectorConfig
from antijamming.dsp.beamforming import (
    apply_beamformer,
    beamformer_pattern_db,
    lcmv_pattern_db,
    uniform_weights,
)
from antijamming.dsp.doa import music_spectrum
from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
    normalize_algorithm_mode,
)
from antijamming.dsp.pipeline import (
    compute_doa_metrics,
    compute_gnss_output_vector,
    compute_lcmv_metrics,
    compute_phase_metrics,
)

_JAMMER_NULL_HOLD_S = 2.0


# =============================================================================
# Threaded Backend Runtime
# =============================================================================

# BackendRuntime is the ownership boundary for hardware, worker threads, and
# GNSS-SDR integration. GUI code should control it through methods/signals only.

class BackendRuntime:
    """Owns SDR capture, DSP workers, GNSS handoff, and UI metric emission."""

    def __init__(
        self,
        config: StreamConfig,
        loggers: dict[str, logging.Logger],
        on_data: Callable[[dict], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._loggers = loggers
        self._on_data = on_data
        self._on_status = on_status
        self._on_failed = on_failed
        self._handoff_log = loggers.get("handoff", loggers["transport"])
        self._jammer_log = loggers.get("jammer", loggers["doa"])
        self._running = False
        self._thread: threading.Thread | None = None
        self._angle_scan = config.angle_scan_spec()
        self._scan_angles_deg = self._angle_scan.values()
        self._doa_method = "music"
        self._expected_sources = config.expected_sources
        self._doa_log_interval_s = max(0.0, float(config.doa_log_interval_s))
        # Log timestamps throttle high-rate DSP state so logs remain useful
        # during long streams.
        self._last_phase_log_ts = 0.0
        self._last_doa_log_ts = 0.0
        self._last_lcmv_log_ts = 0.0
        self._lcmv_force_null = bool(config.lcmv_force_null)
        self._algorithm_mode = normalize_algorithm_mode(config.algorithm_mode)
        self._device: UsrpRxDevice | None = None
        self._gnss_bridge: GnssSdrBridge | None = None
        self._ui_emit_interval_s = max(0.05, float(config.ui_update_interval_s))
        self._dsp_emit_interval_s = max(0.02, float(config.dsp_update_interval_s))
        self._process_every_n_chunks = max(1, int(config.process_every_n_chunks))
        self._last_ui_emit_ts = 0.0
        self._startup_grace_s = max(0.0, float(config.startup_grace_s))
        self._rx_thread: threading.Thread | None = None
        self._phase_thread: threading.Thread | None = None
        self._doa_thread: threading.Thread | None = None
        self._lcmv_thread: threading.Thread | None = None
        self._gnss_handoff_thread: threading.Thread | None = None
        self._gnss_raw_queue: queue.Queue | None = None
        self._gnss_raw_drops: int = 0
        self._gnss_raw_q_highwater: int = 0
        self._gnss_raw_q_interval_highwater: int = 0
        self._gnss_raw_q_marks_logged: set[int] = set()
        self._gnss_failure_lock = threading.Lock()
        self._gnss_pipeline_failed = False

        # DSP stage queues are intentionally shallow and latest-only; processing
        # stale chunks is worse than dropping them for realtime monitoring.
        self._dsp_stage_queue_maxsize = 2
        self._phase_queue: queue.Queue[PhaseWorkItem | None] = queue.Queue(
            maxsize=self._dsp_stage_queue_maxsize
        )
        self._doa_queue: queue.Queue[PhaseResult | None] = queue.Queue(
            maxsize=self._dsp_stage_queue_maxsize
        )
        self._lcmv_queue: queue.Queue[BeamformingWorkItem | None] = queue.Queue(
            maxsize=self._dsp_stage_queue_maxsize
        )
        self._raw_chunk_count = 0
        self._processed_emit_count = 0
        self._ui_metrics_seq = 0
        self._overflow_count = 0
        self._overflow_streak = 0
        self._timeout_count = 0
        self._stop_reason = "not started"
        self._startup_overflow_count = 0
        self._startup_timeout_count = 0
        self._stream_start_ts = 0.0
        self._rx_prev_recv_ts = 0.0
        self._rx_max_gap_s = 0.0
        self._last_good_rx_time_spec_s: float | None = None
        self._pending_rx_overflow_time_spec_s: float | None = None
        self._pending_rx_overflow_marker = "--"
        self._pending_rx_overflow_raw_count = 0
        self._pending_rx_overflow_error_code = "--"
        self._rx_health_chunk_counter = 0
        self._rx_health_peak_component = 0.0
        self._rx_health_peak_magnitude = 0.0
        self._rx_health_power_sum = 0.0
        self._rx_health_sample_count = 0
        self._rx_health_near_full_scale_count = 0
        self._rx_clipping_suspected_count = 0
        self._gnss_fifo_health_chunk_counter = 0
        self._gnss_fifo_health_peak_component = 0.0
        self._gnss_fifo_health_peak_magnitude = 0.0
        self._gnss_fifo_health_power_sum = 0.0
        self._gnss_fifo_health_sample_count = 0
        self._gnss_fifo_health_near_full_scale_count = 0
        self._latest_rx_signal_health: dict[str, object] = {
            "assessed": False,
            "clipping_suspected": False,
        }
        self._dsp_chunk_counter = 0

        self._results_lock = threading.Lock()
        self._beamformer_lock = threading.Lock()
        # Latest-result fields are copied into RuntimeUiMetrics. The locks keep
        # UI emission consistent while worker stages update independently.
        initial_weights = uniform_weights(len(config.channels))
        self._latest_beamformer_weights = initial_weights
        self._latest_gnss_effective_weights = self._effective_gnss_weights(initial_weights)
        self._latest_powers = np.zeros((len(config.channels),), dtype=np.float64)
        self._latest_phase_offsets = np.zeros((len(config.channels),), dtype=np.float64)
        self._latest_phase_offsets_raw = np.zeros((len(config.channels),), dtype=np.float64)
        self._latest_phase_offsets_calibrated = np.zeros((len(config.channels),), dtype=np.float64)
        preview_cols = max(1024, min(8192, max(int(config.ui_points), 64) * 16))
        # GUI preview buffers are capped separately from DSP chunk size so plot
        # rendering remains bounded even with large SDR chunks.
        self._ui_preview_cols = preview_cols
        self._latest_ui_raw_preview = np.zeros(
            (len(config.channels), preview_cols), dtype=np.complex64
        )
        self._latest_ui_calibrated_preview = np.zeros(
            (len(config.channels), preview_cols), dtype=np.complex64
        )
        self._latest_doa_spectrum = np.zeros((config.doa_points,), dtype=np.float64)
        self._latest_doa_raw_spectrum = np.zeros((config.doa_points,), dtype=np.float64)
        self._latest_doa_deg = float(config.doa_min_deg)
        self._latest_lcmv_db = np.zeros((config.doa_points,), dtype=np.float64)
        self._latest_lcmv_null_active = False
        self._latest_lcmv_input_power_db = float("nan")
        self._latest_lcmv_output_power_db = float("nan")
        self._latest_lcmv_power_delta_db = float("nan")
        self._jammer_null_hold_until_s = 0.0
        self._jammer_null_hold_doa_deg = float(config.doa_min_deg)
        self._last_phase_ts = 0.0
        self._last_doa_ts = 0.0
        self._last_lcmv_ts = 0.0
        self._last_gnss_snapshot_log_ts = 0.0
        self._last_logged_pvt_current: bool | None = None
        self._last_logged_pvt_seen: bool | None = None
        self._last_logged_receiver_time_s: object = None
        self._last_logged_pvt_quality_key: tuple[object, ...] | None = None
        self._perf_lock = threading.Lock()
        self._perf_stats: dict[str, dict[str, float]] = {}
        self._last_perf_log_ts = 0.0
        self._jammer_detector = JammerDetector(
            JammerDetectorConfig(
                enabled=bool(config.jammer_detection_enabled),
                min_power_db=float(config.jammer_detection_min_power_db),
                power_rise_db=float(config.jammer_detection_power_rise_db),
                baseline_alpha=float(config.jammer_detection_power_baseline_alpha),
                consecutive_alarms=int(config.jammer_detection_consecutive_alarms),
            )
        )
        self._latest_jammer_status: dict[str, object] = {
            "assessed": False,
            "detected": False,
            "state": "not_assessed",
            "confidence": 0.0,
            "reason": "No raw power estimate yet",
            "doa_deg": float("nan"),
        }

    # -------------------------------------------------------------------------
    # Public Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self.run,
            name="antijamming_backend",
            daemon=False,
        )
        self._thread.start()

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_running(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        return bool(self._running)

    @property
    def _jammer_power_baseline_db(self) -> float:
        return self._jammer_detector.power_baseline_db

    @_jammer_power_baseline_db.setter
    def _jammer_power_baseline_db(self, value: float) -> None:
        self._jammer_detector.power_baseline_db = float(value)

    def run(self) -> None:
        self._running = True
        self._stop_reason = "normal stop"
        self._overflow_count = 0
        self._overflow_streak = 0
        self._timeout_count = 0
        self._startup_overflow_count = 0
        self._startup_timeout_count = 0
        self._last_ui_emit_ts = 0.0
        self._last_phase_log_ts = 0.0
        self._last_doa_log_ts = 0.0
        self._last_lcmv_log_ts = 0.0
        self._last_perf_log_ts = 0.0
        self._last_gnss_snapshot_log_ts = 0.0
        self._last_logged_pvt_current = None
        self._last_logged_pvt_seen = None
        self._last_logged_receiver_time_s = None
        self._last_logged_pvt_quality_key = None
        self._gnss_pipeline_failed = False
        self._gnss_raw_q_highwater = 0
        self._gnss_raw_q_interval_highwater = 0
        self._gnss_raw_q_marks_logged.clear()
        with self._perf_lock:
            self._perf_stats.clear()
        self._algorithm_mode = normalize_algorithm_mode(self._config.algorithm_mode)
        self._raw_chunk_count = 0
        self._processed_emit_count = 0
        self._ui_metrics_seq = 0
        self._rx_clipping_suspected_count = 0
        self._reset_rx_signal_health()
        self._phase_queue = queue.Queue(maxsize=self._dsp_stage_queue_maxsize)
        self._doa_queue = queue.Queue(maxsize=self._dsp_stage_queue_maxsize)
        self._lcmv_queue = queue.Queue(maxsize=self._dsp_stage_queue_maxsize)
        with self._results_lock:
            self._latest_powers = np.zeros((len(self._config.channels),), dtype=np.float64)
            self._latest_phase_offsets = np.zeros((len(self._config.channels),), dtype=np.float64)
            self._latest_phase_offsets_raw = np.zeros((len(self._config.channels),), dtype=np.float64)
            self._latest_phase_offsets_calibrated = np.zeros(
                (len(self._config.channels),), dtype=np.float64
            )
            self._latest_ui_raw_preview = np.zeros(
                (len(self._config.channels), self._ui_preview_cols), dtype=np.complex64
            )
            self._latest_ui_calibrated_preview = np.zeros(
                (len(self._config.channels), self._ui_preview_cols), dtype=np.complex64
            )
            self._latest_doa_spectrum = np.zeros((self._config.doa_points,), dtype=np.float64)
            self._latest_doa_raw_spectrum = np.zeros((self._config.doa_points,), dtype=np.float64)
            self._latest_doa_deg = float(self._config.doa_min_deg)
            self._latest_lcmv_db = np.zeros((self._config.doa_points,), dtype=np.float64)
            self._latest_lcmv_null_active = False
            self._latest_lcmv_input_power_db = float("nan")
            self._latest_lcmv_output_power_db = float("nan")
            self._latest_lcmv_power_delta_db = float("nan")
            self._last_phase_ts = 0.0
            self._last_doa_ts = 0.0
            self._last_lcmv_ts = 0.0
            self._jammer_detector.reset()
            self._latest_jammer_status = {
                "assessed": False,
                "detected": False,
                "state": "not_assessed",
                "confidence": 0.0,
                "reason": "No raw power estimate yet",
                "doa_deg": float("nan"),
            }
        self._set_beamformer_weights(uniform_weights(len(self._config.channels)))
        try:
            self._emit_status("Preparing session logs")
            reset_session_logs(self._config.log_dir, self._loggers)
            self._loggers["app"].info(
                "Runtime startup: algorithm_mode=%s beamforming=active force_null=%s",
                self._algorithm_mode,
                bool(self._lcmv_force_null),
            )
            if self._device is None:
                self._emit_status("Initializing USRP")
                self._device = UsrpRxDevice(self._config)
            else:
                self._emit_status("Resuming USRP session")
                self._loggers["stream"].info(
                    "Reusing preserved USRP session; no MultiUSRP recreate or retune."
                )
            for line in collect_host_transport_report(self._config.usrp_addr):
                self._loggers["transport"].info(line)
                self._loggers["hw"].info(line)
            self._emit_status("Starting GNSS-SDR")
            self._gnss_bridge = GnssSdrBridge(self._config, self._loggers)
            gnss_ok = self._gnss_bridge.start()
            if gnss_ok:
                gnss_raw_q_maxsize = max(1, int(self._config.gnss_feed_queue_maxsize))
                gnss_chunk_s = float(self._config.samples_per_chunk) / max(
                    1.0,
                    float(self._config.sample_rate),
                )
                gnss_queue_buffer_s = gnss_raw_q_maxsize * gnss_chunk_s
                gnss_queue_bytes = (
                    gnss_raw_q_maxsize
                    * len(self._config.channels)
                    * int(self._config.samples_per_chunk)
                    * np.dtype(np.complex64).itemsize
                )
                self._loggers["gnss"].info("GNSS-SDR bridge started.")
                self._loggers["transport"].info(
                    "GNSS handoff mode: %s",
                    self._gnss_handoff_mode_label(),
                )
                self._handoff_log.info(
                    "runtime->GNSS session: mode=%s center_freq=%.3f_mhz sample_rate=%.3f_msps "
                    "gnss_if_bw=%.3f_mhz samples_per_chunk=%d raw_q=%d "
                    "raw_q_buffer_s=%.2f raw_q_memory_mib=%.1f",
                    self._gnss_handoff_mode_label(),
                    float(self._config.center_freq_hz) / 1e6,
                    float(self._config.sample_rate) / 1e6,
                    float(self._config.gnss_sdr_if_bandwidth_hz) / 1e6,
                    int(self._config.samples_per_chunk),
                    gnss_raw_q_maxsize,
                    gnss_queue_buffer_s,
                    gnss_queue_bytes / float(1024 * 1024),
                )
                self._gnss_raw_queue = queue.Queue(
                    maxsize=gnss_raw_q_maxsize
                )
                self._gnss_raw_drops = 0
                self._gnss_raw_q_highwater = 0
                self._gnss_raw_q_interval_highwater = 0
                self._gnss_raw_q_marks_logged.clear()
                self._gnss_handoff_thread = threading.Thread(
                    target=self._gnss_beamform_loop,
                    name="gnss_ordered_handoff",
                    daemon=True,
                )
                self._gnss_handoff_thread.start()
                self._loggers["transport"].info(
                    "GNSS pipeline: thread gnss_ordered_handoff "
                    "(raw_q=%d, buffer=%.2fs, memory=%.1f MiB); recv() publishes "
                    "ordered queue-backed work items.",
                    gnss_raw_q_maxsize,
                    gnss_queue_buffer_s,
                    gnss_queue_bytes / float(1024 * 1024),
                )
            self._prime_usrp_rx_startup()
            for line in self._device.startup_report_lines():
                self._loggers["stream"].info(line)
                self._loggers["hw"].info(line)
            warmup_chunk = np.zeros(
                (len(self._config.channels), self._config.samples_per_chunk),
                dtype=np.complex128,
            )
            _ = music_spectrum(
                x=warmup_chunk,
                rf_freq_hz=self._config.center_freq_hz,
                scan_angles_deg=self._scan_angles_deg,
                uca_radius_m=self._config.uca_radius_m,
                n_sources=max(int(self._expected_sources), 1),
            )
            _ = lcmv_pattern_db(
                x=warmup_chunk,
                rf_freq_hz=self._config.center_freq_hz,
                scan_angles_deg=self._scan_angles_deg,
                null_theta_deg=0.0,
                uca_radius_m=self._config.uca_radius_m,
            )
            self._emit_status("USRP stream started")
            self._loggers["app"].info("USRP stream started: %s", self._config.usrp_addr)
            self._stream_start_ts = time.monotonic()
            self._rx_prev_recv_ts = 0.0
            self._rx_max_gap_s = 0.0
            self._last_good_rx_time_spec_s = None
            self._pending_rx_overflow_time_spec_s = None
            self._pending_rx_overflow_marker = "--"
            self._pending_rx_overflow_raw_count = 0
            self._pending_rx_overflow_error_code = "--"
            self._rx_health_chunk_counter = 0
            self._dsp_chunk_counter = 0

            self._rx_thread = threading.Thread(
                target=self._rx_drain_loop,
                name="usrp_rx_drain",
                daemon=True,
            )
            self._rx_thread.start()

            self._phase_thread = threading.Thread(
                target=self._phase_loop, name="dsp_phase", daemon=True
            )
            self._doa_thread = threading.Thread(
                target=self._doa_loop, name="dsp_doa", daemon=True
            )
            self._lcmv_thread = threading.Thread(
                target=self._lcmv_loop, name="dsp_lcmv", daemon=True
            )
            self._phase_thread.start()
            self._doa_thread.start()
            self._lcmv_thread.start()

            while self._running:
                now = time.monotonic()
                if (now - self._last_ui_emit_ts) >= self._ui_emit_interval_s:
                    metrics = self._compose_metrics_for_ui()
                    self._emit_data(metrics)
                    self._last_ui_emit_ts = now
                    continue
                next_emit_s = self._last_ui_emit_ts + self._ui_emit_interval_s
                time.sleep(min(0.02, max(0.001, next_emit_s - now)))
        except Exception as exc:
            msg = f"Backend runtime failed: {exc}"
            self._stop_reason = f"exception: {exc}"
            self._loggers["errors"].exception(msg)
            self._emit_failed(msg)
        finally:
            self._running = False
            self._emit_status("Stopping DSP and GNSS handoff")
            self._signal_dsp_shutdown()
            for t in [self._rx_thread]:
                if t is None:
                    continue
                try:
                    t.join(timeout=2.0)
                except Exception:
                    pass
            self._rx_thread = None
            if self._gnss_handoff_thread is not None:
                rq = self._gnss_raw_queue
                if rq is not None:
                    try:
                        rq.put_nowait(None)
                    except queue.Full:
                        pass
                if self._gnss_handoff_thread is not None:
                    try:
                        self._gnss_handoff_thread.join(timeout=4.0)
                    except Exception:
                        pass
                    self._gnss_handoff_thread = None
            self._gnss_raw_queue = None
            if self._config.gnss_sdr_enable:
                self._loggers["transport"].info(
                    "GNSS queue summary: raw_highwater=%d/%d raw_rejections=%d",
                    self._gnss_raw_q_highwater,
                    max(1, int(self._config.gnss_feed_queue_maxsize)),
                    self._gnss_raw_drops,
                )
            for t in [self._phase_thread, self._doa_thread, self._lcmv_thread]:
                if t is None:
                    continue
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass
            self._phase_thread = None
            self._doa_thread = None
            self._lcmv_thread = None
            if self._gnss_bridge is not None:
                self._emit_status("Finalizing GNSS-SDR logs")
                self._gnss_bridge.stop(self._stop_reason)
            self._gnss_bridge = None
            if self._device is not None:
                if bool(self._config.preserve_usrp_session_on_stop):
                    try:
                        self._device.pause_stream()
                    except Exception:
                        pass
                    self._loggers["stream"].info(
                        "USRP session preserved after stop; next start can reuse current LO/tune state."
                    )
                else:
                    self._device = None
            self._emit_status("USRP stream stopped")
            self._loggers["app"].info(
                "USRP stream stopped (reason=%s, raw=%d, overflow=%d, timeout=%d, "
                "startup_overflow=%d, startup_timeout=%d, clipping_suspected_intervals=%d)",
                self._stop_reason,
                self._raw_chunk_count,
                self._overflow_count,
                self._timeout_count,
                self._startup_overflow_count,
                self._startup_timeout_count,
                self._rx_clipping_suspected_count,
            )
            for logger in self._loggers.values():
                for h in list(logger.handlers):
                    try:
                        h.flush()
                    except Exception:
                        pass

    # -------------------------------------------------------------------------
    # Runtime Control Mutators
    # -------------------------------------------------------------------------

    def stop(self, reason: str = "normal stop") -> None:
        self._set_stop_reason(reason)
        self._running = False
        self._signal_dsp_shutdown()
        with self._gnss_failure_lock:
            bridge = self._gnss_bridge
            self._gnss_bridge = None
        if bridge is not None:
            self._emit_status("Stopping GNSS-SDR")
            bridge.stop(reason)
        if self._device is not None:
            self._emit_status("Stopping USRP stream")
            try:
                if bool(self._config.preserve_usrp_session_on_stop):
                    self._device.pause_stream()
                else:
                    self._device.stop()
            except Exception:
                pass

    def set_doa_method(self, method: str) -> None:
        self._doa_method = "music"

    def set_expected_sources(self, count: int) -> None:
        self._expected_sources = max(1, int(count))

    def set_algorithm_mode(self, mode: str) -> None:
        normalized = normalize_algorithm_mode(mode)
        if normalized == self._algorithm_mode:
            return
        self._algorithm_mode = normalized
        self._config.algorithm_mode = normalized  # type: ignore[assignment]
        self._set_beamformer_weights(uniform_weights(len(self._config.channels)))
        with self._results_lock:
            self._latest_lcmv_db = np.zeros((self._config.doa_points,), dtype=np.float64)
            self._latest_lcmv_null_active = False
        self._loggers["app"].info("Runtime action: algorithm_mode=%s", normalized)
        self._emit_status(f"Algorithm mode: {normalized.upper()}")

    def set_jammer_detection_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        self._config.jammer_detection_enabled = normalized
        self._jammer_detector.set_enabled(normalized)
        with self._results_lock:
            self._latest_jammer_status = {
                "assessed": False,
                "detected": False,
                "state": "not_assessed" if normalized else "disabled",
                "confidence": 0.0,
                "reason": (
                    "Jammer detection waiting for raw power estimate"
                    if normalized
                    else "Jammer detection disabled"
                ),
                "doa_deg": float("nan"),
            }
        self._loggers["app"].info(
            "Runtime action: jammer_detection_enabled=%s",
            normalized,
        )
        self._emit_status(
            "Jammer detection enabled" if normalized else "Jammer detection disabled"
        )

    # -------------------------------------------------------------------------
    # Callback Emission
    # -------------------------------------------------------------------------

    def _emit_data(self, metrics: dict) -> None:
        if self._on_data is None:
            return
        try:
            emit_t0 = time.monotonic()
            self._on_data(metrics)
            self._record_runtime_timing("ui_emit_callback", time.monotonic() - emit_t0)
        except Exception as exc:
            self._loggers["errors"].error("Data callback failed: %s", exc)

    def _emit_status(self, message: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(message)
        except Exception as exc:
            self._loggers["errors"].error("Status callback failed: %s", exc)

    def _emit_failed(self, message: str) -> None:
        if self._on_failed is None:
            return
        try:
            self._on_failed(message)
        except Exception as exc:
            self._loggers["errors"].error("Failure callback failed: %s", exc)

    # -------------------------------------------------------------------------
    # RX Drain Loop
    # -------------------------------------------------------------------------

    def _prime_usrp_rx_startup(self) -> None:
        """Start the RX streamer and retry one first-recv socket failure."""
        device = self._device
        if device is None:
            raise RuntimeError("USRP RX startup probe has no device.")
        for attempt in (1, 2):
            try:
                rx_result = device.recv_chunk()
                chunk, rx_state = rx_result
                rx_info = self._rx_result_info(rx_result)
            except Exception as exc:
                if attempt >= 2:
                    raise RuntimeError(
                        f"USRP RX startup probe failed after retry: {exc}"
                    ) from exc
                self._loggers["stream"].warning(
                    "USRP RX startup probe failed before first chunk: %s. "
                    "Restarting the existing RX streamer once.",
                    exc,
                )
                self._emit_status("Retrying USRP RX startup")
                try:
                    device.restart_stream()
                except Exception as restart_exc:
                    raise RuntimeError(
                        f"USRP RX startup retry could not restart the streamer: {restart_exc}"
                    ) from exc
                # A short settle keeps the next recv from racing the failed UHD
                # transport socket teardown on the same RX streamer.
                time.sleep(0.2)
                continue
            sample_count = int(chunk.shape[1]) if getattr(chunk, "ndim", 0) >= 2 else 0
            self._loggers["stream"].info(
                "USRP RX startup probe ready: attempt=%d state=%s samples=%d "
                "uhd_error_code=%s out_of_sequence=%s time_spec_s=%s",
                attempt,
                rx_state,
                sample_count,
                rx_info.get("error_code", "--"),
                bool(rx_info.get("out_of_sequence", False)),
                self._format_optional_float(rx_info.get("time_spec_s")),
            )
            return

    def _rx_drain_loop(self) -> None:
        while self._running and self._device is not None:
            try:
                recv_t0 = time.monotonic()
                rx_result = self._device.recv_chunk()
                chunk, rx_state = rx_result
                rx_info = self._rx_result_info(rx_result)
                recv_elapsed_s = time.monotonic() - recv_t0
                self._record_runtime_timing("rx_recv", recv_elapsed_s)
            except Exception as exc:
                self._loggers["errors"].exception("RX drain loop crashed: %s", exc)
                self._failed_stop(f"RX recv failed: {exc}")
                return
            now = time.monotonic()
            if self._rx_prev_recv_ts > 0.0:
                gap = now - self._rx_prev_recv_ts
                if gap > self._rx_max_gap_s:
                    self._rx_max_gap_s = gap
            self._rx_prev_recv_ts = now
            in_startup_grace = (
                (time.monotonic() - self._stream_start_ts) < self._startup_grace_s
                if self._stream_start_ts > 0.0
                else False
            )
            if rx_state == "overflow":
                if in_startup_grace:
                    self._startup_overflow_count += 1
                else:
                    self._overflow_count += 1
                    self._overflow_streak += 1
                    self._log_rx_uhd_metadata_event(
                        rx_state=rx_state,
                        rx_info=rx_info,
                        recv_elapsed_s=recv_elapsed_s,
                        sample_count=int(chunk.shape[1]),
                        in_startup_grace=in_startup_grace,
                    )
                    self._log_rx_transport_event_context("overflow", recv_elapsed_s)
                    if self._overflow_count > 0 and self._overflow_count % 20 == 0:
                        self._loggers["transport"].warning(
                            "RX overflow count=%d streak=%d (raw=%d)",
                            self._overflow_count,
                            self._overflow_streak,
                            self._raw_chunk_count,
                        )
                    if self._config.stop_on_overflow and (
                        self._overflow_streak >= self._config.max_overflow_streak
                        or self._overflow_count >= self._config.max_total_overflow
                    ):
                        if self._overflow_streak >= self._config.max_overflow_streak:
                            reason = (
                                "Auto-stop on RX overflow streak: "
                                f"streak={self._overflow_streak}/{self._config.max_overflow_streak}, "
                                f"total={self._overflow_count}, "
                                f"rate={self._config.sample_rate/1e6:.3f} Msps, "
                                f"channels={len(self._config.channels)}"
                            )
                        else:
                            reason = (
                                "Auto-stop on RX overflow: "
                                f"total={self._overflow_count}/{self._config.max_total_overflow}, "
                                f"rate={self._config.sample_rate/1e6:.3f} Msps, "
                                f"channels={len(self._config.channels)}"
                            )
                        self._loggers["transport"].error("%s", reason)
                        self._loggers["errors"].error("%s", reason)
                        self._stop_reason = reason
                        self._running = False
                        try:
                            self._device.stop()
                        except Exception:
                            pass
                        self._emit_failed(reason)
                        return
            elif rx_state == "timeout":
                if in_startup_grace:
                    self._startup_timeout_count += 1
                else:
                    self._timeout_count += 1
                    if rx_info.get("error_code") not in (None, "", "none", "timeout"):
                        self._log_rx_uhd_metadata_event(
                            rx_state=rx_state,
                            rx_info=rx_info,
                            recv_elapsed_s=recv_elapsed_s,
                            sample_count=int(chunk.shape[1]),
                            in_startup_grace=in_startup_grace,
                        )
                    self._log_rx_transport_event_context("timeout", recv_elapsed_s)
            elif not in_startup_grace:
                self._overflow_streak = 0

            if chunk.shape[1] > 0:
                if rx_state == "ok":
                    self._maybe_log_rx_dropped_sample_estimate(rx_info)
                self._raw_chunk_count += 1
                if isinstance(chunk, np.ndarray):
                    self._update_rx_signal_health(chunk)
                rq = self._gnss_raw_queue
                bridge = self._gnss_bridge
                if rq is not None and bridge is not None and bridge.active:
                    publish_t0 = time.monotonic()
                    try:
                        self._publish_gnss_raw_chunk(rq, chunk)
                    except queue.Full:
                        self._record_gnss_raw_drop(rq)
                        self._handle_gnss_pipeline_error(
                            RuntimeError(
                                "GNSS raw queue full; paused handoff instead of "
                                "dropping contiguous IQ"
                            )
                        )
                    self._record_runtime_timing(
                        "rx_gnss_queue_publish",
                        time.monotonic() - publish_t0,
                    )
                self._rx_health_chunk_counter += 1
                if self._rx_health_chunk_counter >= max(
                    1, int(self._config.rx_health_log_interval_chunks)
                ):
                    self._log_rx_health_summary(samples_per_chunk=int(chunk.shape[1]))
                    self._rx_max_gap_s = 0.0
                    self._reset_rx_signal_health()
                self._dsp_chunk_counter += 1
                if (self._dsp_chunk_counter % self._process_every_n_chunks) == 0:
                    put_latest(
                        self._phase_queue,
                        PhaseWorkItem(
                            chunk=chunk,
                        ),
                    )

    def _rx_result_info(self, rx_result: object) -> dict[str, object]:
        return {
            "got_samples": int(getattr(rx_result, "got_samples", 0) or 0),
            "error_code": self._normalize_rx_error_code(
                getattr(rx_result, "error_code", "none")
            ),
            "out_of_sequence": bool(getattr(rx_result, "out_of_sequence", False)),
            "time_spec_s": getattr(rx_result, "time_spec_s", None),
        }

    def _normalize_rx_error_code(self, error_code: object) -> str:
        text = str(error_code or "--")
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        return text.strip().lower() or "--"

    def _rx_marker_equivalent(self, rx_info: dict[str, object]) -> str:
        if bool(rx_info.get("out_of_sequence", False)):
            return "D"
        error_code = str(rx_info.get("error_code", "--")).lower()
        if "overflow" in error_code or "late" in error_code:
            return "O"
        return "--"

    def _log_rx_uhd_metadata_event(
        self,
        *,
        rx_state: str,
        rx_info: dict[str, object],
        recv_elapsed_s: float,
        sample_count: int,
        in_startup_grace: bool,
    ) -> None:
        marker = self._rx_marker_equivalent(rx_info)
        time_spec_s = self._coerce_optional_float(rx_info.get("time_spec_s"))
        if rx_state == "overflow" and time_spec_s is not None:
            self._pending_rx_overflow_time_spec_s = time_spec_s
            self._pending_rx_overflow_marker = marker
            self._pending_rx_overflow_raw_count = int(self._raw_chunk_count)
            self._pending_rx_overflow_error_code = str(rx_info.get("error_code", "--"))
        message = (
            "RX UHD metadata event: state=%s marker_equivalent=%s "
            "error_code=%s out_of_sequence=%s time_spec_s=%s got_samples=%d "
            "recv_ms=%.2f raw=%d rx_gap_max_ms=%.2f startup_grace=%s "
            "note=%s"
        )
        args = (
            rx_state,
            marker,
            rx_info.get("error_code", "--"),
            bool(rx_info.get("out_of_sequence", False)),
            self._format_optional_seconds(time_spec_s),
            int(sample_count),
            recv_elapsed_s * 1000.0,
            int(self._raw_chunk_count),
            self._rx_max_gap_s * 1000.0,
            bool(in_startup_grace),
            "D means UHD RX packet sequence error; O means UHD overflow inline message.",
        )
        self._loggers["transport"].warning(message, *args)
        self._loggers["health"].warning(message, *args)

    def _maybe_log_rx_dropped_sample_estimate(self, rx_info: dict[str, object]) -> None:
        current_time_spec_s = self._coerce_optional_float(rx_info.get("time_spec_s"))
        if current_time_spec_s is None:
            return
        pending_time_spec_s = self._pending_rx_overflow_time_spec_s
        previous_good_time_spec_s = self._last_good_rx_time_spec_s
        self._last_good_rx_time_spec_s = current_time_spec_s
        if pending_time_spec_s is None:
            return
        gap_s = current_time_spec_s - pending_time_spec_s
        sample_rate = max(1.0, float(self._config.sample_rate))
        estimated_samples = int(round(max(0.0, gap_s) * sample_rate))
        estimated_gap_ms = max(0.0, gap_s) * 1000.0
        expected_chunk_ms = (
            1000.0
            * float(self._config.samples_per_chunk)
            / max(1.0, float(self._config.sample_rate))
        )
        message = (
            "RX dropped-sample estimate: marker_equivalent=%s "
            "overflow_error_code=%s overflow_time_spec_s=%s next_good_time_spec_s=%s "
            "previous_good_time_spec_s=%s estimated_samples_per_channel=%d "
            "estimated_gap_ms=%.3f expected_chunk_ms=%.3f raw_at_overflow=%d "
            "raw_at_recovery=%d sample_rate_hz=%.3f method=%s"
        )
        args = (
            self._pending_rx_overflow_marker,
            self._pending_rx_overflow_error_code,
            self._format_optional_seconds(pending_time_spec_s),
            self._format_optional_seconds(current_time_spec_s),
            self._format_optional_seconds(previous_good_time_spec_s),
            estimated_samples,
            estimated_gap_ms,
            expected_chunk_ms,
            int(self._pending_rx_overflow_raw_count),
            int(self._raw_chunk_count),
            sample_rate,
            "next_good_time_spec_minus_overflow_time_spec",
        )
        self._loggers["transport"].warning(message, *args)
        self._loggers["health"].warning(message, *args)
        self._pending_rx_overflow_time_spec_s = None
        self._pending_rx_overflow_marker = "--"
        self._pending_rx_overflow_raw_count = 0
        self._pending_rx_overflow_error_code = "--"

    def _coerce_optional_float(self, value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(number):
            return None
        return number

    def _format_optional_seconds(self, value: object) -> str:
        number = self._coerce_optional_float(value)
        if number is None:
            return "--"
        return f"{number:.6f}"

    def _reset_rx_signal_health(self) -> None:
        self._rx_health_chunk_counter = 0
        self._rx_health_peak_component = 0.0
        self._rx_health_peak_magnitude = 0.0
        self._rx_health_power_sum = 0.0
        self._rx_health_sample_count = 0
        self._rx_health_near_full_scale_count = 0

    def _update_rx_signal_health(self, chunk: np.ndarray) -> None:
        stats = self._rx_signal_health_for_chunk(
            chunk,
            component_threshold=float(self._config.rx_clipping_component_threshold),
        )
        sample_count = int(stats["sample_count"])
        if sample_count <= 0:
            return
        self._rx_health_peak_component = max(
            self._rx_health_peak_component,
            float(stats["peak_component"]),
        )
        self._rx_health_peak_magnitude = max(
            self._rx_health_peak_magnitude,
            float(stats["peak_magnitude"]),
        )
        self._rx_health_power_sum += float(stats["power_sum"])
        self._rx_health_sample_count += sample_count
        self._rx_health_near_full_scale_count += int(stats["near_full_scale_count"])

    @staticmethod
    def _rx_signal_health_for_chunk(
        samples: np.ndarray,
        component_threshold: float,
    ) -> dict[str, float | int]:
        values = np.asarray(samples)
        if values.size == 0:
            return {
                "sample_count": 0,
                "peak_component": float("nan"),
                "peak_magnitude": float("nan"),
                "rms_magnitude": float("nan"),
                "power_sum": 0.0,
                "near_full_scale_count": 0,
                "near_full_scale_fraction": float("nan"),
            }

        component_limit = max(0.0, float(component_threshold))
        real_abs = np.abs(values.real)
        imag_abs = np.abs(values.imag)
        component_abs = np.maximum(real_abs, imag_abs)
        magnitudes = np.abs(values)
        sample_count = int(values.size)
        near_full_scale_count = int(np.count_nonzero(component_abs >= component_limit))
        power_sum = float(np.sum(magnitudes.astype(np.float64) ** 2))
        rms_magnitude = float(np.sqrt(power_sum / max(sample_count, 1)))
        return {
            "sample_count": sample_count,
            "peak_component": float(np.max(component_abs)),
            "peak_magnitude": float(np.max(magnitudes)),
            "rms_magnitude": rms_magnitude,
            "power_sum": power_sum,
            "near_full_scale_count": near_full_scale_count,
            "near_full_scale_fraction": near_full_scale_count / max(sample_count, 1),
        }

    def _log_rx_health_summary(self, samples_per_chunk: int) -> None:
        sample_count = max(0, int(self._rx_health_sample_count))
        if sample_count > 0:
            rms_magnitude = float(
                np.sqrt(self._rx_health_power_sum / max(sample_count, 1))
            )
            near_full_scale_fraction = (
                self._rx_health_near_full_scale_count / max(sample_count, 1)
            )
        else:
            rms_magnitude = float("nan")
            near_full_scale_fraction = float("nan")

        fraction_threshold = max(
            0.0,
            float(self._config.rx_clipping_fraction_threshold),
        )
        clipping_suspected = bool(
            np.isfinite(near_full_scale_fraction)
            and near_full_scale_fraction >= fraction_threshold
        )
        near_full_scale_pct = 100.0 * near_full_scale_fraction
        health_snapshot: dict[str, object] = {
            "assessed": sample_count > 0,
            "max_gap_between_recv_ms": self._rx_max_gap_s * 1000.0,
            "chunks": int(self._rx_health_chunk_counter),
            "samples_per_chunk": int(samples_per_chunk),
            "sample_count": int(sample_count),
            "iq_peak_component": float(self._rx_health_peak_component),
            "iq_peak_magnitude": float(self._rx_health_peak_magnitude),
            "iq_rms_magnitude": rms_magnitude,
            "near_full_scale_pct": near_full_scale_pct,
            "threshold_component": float(self._config.rx_clipping_component_threshold),
            "threshold_pct": 100.0 * fraction_threshold,
            "clipping_suspected": clipping_suspected,
            "clipping_suspected_count": int(self._rx_clipping_suspected_count)
            + int(clipping_suspected),
        }
        with self._results_lock:
            self._latest_rx_signal_health = health_snapshot
        self._loggers["health"].info(
            "recv pacing: max_gap_between_recv_ms=%.2f chunks=%d samples/chunk=%d "
            "iq_peak_component=%.4f iq_peak_magnitude=%.4f iq_rms_magnitude=%.4f "
            "near_full_scale_pct=%.4f threshold_component=%.3f threshold_pct=%.4f "
            "clipping_suspected=%s",
            self._rx_max_gap_s * 1000.0,
            self._rx_health_chunk_counter,
            samples_per_chunk,
            self._rx_health_peak_component,
            self._rx_health_peak_magnitude,
            rms_magnitude,
            near_full_scale_pct,
            float(self._config.rx_clipping_component_threshold),
            100.0 * fraction_threshold,
            clipping_suspected,
        )
        if clipping_suspected:
            self._rx_clipping_suspected_count += 1
            self._loggers["health"].warning(
                "RX clipping suspected: near_full_scale_pct=%.4f >= %.4f "
                "peak_component=%.4f peak_magnitude=%.4f rms_magnitude=%.4f "
                "gain_db=%.1f",
                near_full_scale_pct,
                100.0 * fraction_threshold,
                self._rx_health_peak_component,
                self._rx_health_peak_magnitude,
                rms_magnitude,
                float(self._config.gain_db),
            )

    def _reset_gnss_fifo_signal_health(self) -> None:
        self._gnss_fifo_health_chunk_counter = 0
        self._gnss_fifo_health_peak_component = 0.0
        self._gnss_fifo_health_peak_magnitude = 0.0
        self._gnss_fifo_health_power_sum = 0.0
        self._gnss_fifo_health_sample_count = 0
        self._gnss_fifo_health_near_full_scale_count = 0

    def _update_gnss_fifo_signal_health(self, samples: np.ndarray) -> None:
        stats = self._rx_signal_health_for_chunk(
            samples,
            component_threshold=float(self._config.rx_clipping_component_threshold),
        )
        sample_count = int(stats["sample_count"])
        if sample_count <= 0:
            return
        self._gnss_fifo_health_chunk_counter += 1
        self._gnss_fifo_health_peak_component = max(
            self._gnss_fifo_health_peak_component,
            float(stats["peak_component"]),
        )
        self._gnss_fifo_health_peak_magnitude = max(
            self._gnss_fifo_health_peak_magnitude,
            float(stats["peak_magnitude"]),
        )
        self._gnss_fifo_health_power_sum += float(stats["power_sum"])
        self._gnss_fifo_health_sample_count += sample_count
        self._gnss_fifo_health_near_full_scale_count += int(stats["near_full_scale_count"])

    def _log_gnss_fifo_signal_health_summary(self, samples_per_chunk: int) -> None:
        sample_count = max(0, int(self._gnss_fifo_health_sample_count))
        if sample_count > 0:
            rms_magnitude = float(
                np.sqrt(self._gnss_fifo_health_power_sum / max(sample_count, 1))
            )
            near_full_scale_fraction = (
                self._gnss_fifo_health_near_full_scale_count / max(sample_count, 1)
            )
        else:
            rms_magnitude = float("nan")
            near_full_scale_fraction = float("nan")
        self._loggers["health"].info(
            "gnss fifo iq: chunks=%d samples/chunk=%d "
            "iq_peak_component=%.4f iq_peak_magnitude=%.4f iq_rms_magnitude=%.4f "
            "near_full_scale_pct=%.4f threshold_component=%.3f",
            self._gnss_fifo_health_chunk_counter,
            samples_per_chunk,
            self._gnss_fifo_health_peak_component,
            self._gnss_fifo_health_peak_magnitude,
            rms_magnitude,
            100.0 * near_full_scale_fraction,
            float(self._config.rx_clipping_component_threshold),
        )

    def _record_runtime_timing(self, name: str, elapsed_s: float) -> None:
        elapsed = max(0.0, float(elapsed_s))
        now = time.monotonic()
        should_log = False
        with self._perf_lock:
            stats = self._perf_stats.setdefault(
                name,
                {"count": 0.0, "total_s": 0.0, "max_s": 0.0},
            )
            stats["count"] += 1.0
            stats["total_s"] += elapsed
            stats["max_s"] = max(stats["max_s"], elapsed)
            if (now - self._last_perf_log_ts) >= 1.0:
                self._last_perf_log_ts = now
                snapshot = self._perf_stats
                self._perf_stats = {}
                should_log = True
        if should_log and snapshot:
            self._log_runtime_timing_summary(snapshot)

    def _log_runtime_timing_summary(self, snapshot: dict[str, dict[str, float]]) -> None:
        order = (
            "rx_recv",
            "rx_gnss_queue_publish",
            "gnss_queue_wait",
            "gnss_beamform_compute",
            "gnss_fifo_write",
            "dsp_phase",
            "dsp_doa",
            "dsp_lcmv",
            "ui_gnss_snapshot",
            "ui_compose_metrics",
            "ui_emit_callback",
        )
        parts: list[str] = []
        for name in order:
            stats = snapshot.get(name)
            if not stats:
                continue
            count = max(1.0, float(stats.get("count", 0.0)))
            avg_ms = 1000.0 * float(stats.get("total_s", 0.0)) / count
            max_ms = 1000.0 * float(stats.get("max_s", 0.0))
            parts.append(f"{name}_avg_ms={avg_ms:.2f} {name}_max_ms={max_ms:.2f} {name}_n={int(count)}")
        if parts:
            self._loggers["health"].info("runtime timing: %s", " ".join(parts))
            self._log_transport_heartbeat(snapshot)

    def _perf_ms(
        self,
        snapshot: dict[str, dict[str, float]],
        name: str,
        field: str,
    ) -> float:
        stats = snapshot.get(name)
        if not stats:
            return float("nan")
        if field == "avg":
            count = max(1.0, float(stats.get("count", 0.0)))
            return 1000.0 * float(stats.get("total_s", 0.0)) / count
        return 1000.0 * float(stats.get("max_s", 0.0))

    def _perf_count(self, snapshot: dict[str, dict[str, float]], name: str) -> int:
        stats = snapshot.get(name)
        if not stats:
            return 0
        return int(float(stats.get("count", 0.0)))

    def _transport_queue_snapshot(self) -> dict[str, float | int]:
        q = self._gnss_raw_queue
        configured_max = max(1, int(self._config.gnss_feed_queue_maxsize))
        if q is None:
            qsize = 0
            maxsize = configured_max
        else:
            qsize = int(q.qsize())
            maxsize = max(1, int(q.maxsize) or configured_max)
        interval_highwater = max(int(self._gnss_raw_q_interval_highwater), qsize)
        self._gnss_raw_q_interval_highwater = qsize
        return {
            "qsize": qsize,
            "maxsize": maxsize,
            "pct": 100.0 * qsize / maxsize,
            "interval_highwater": interval_highwater,
            "interval_highwater_pct": 100.0 * interval_highwater / maxsize,
            "lifetime_highwater": max(int(self._gnss_raw_q_highwater), interval_highwater),
            "lifetime_highwater_pct": 100.0
            * max(int(self._gnss_raw_q_highwater), interval_highwater)
            / maxsize,
        }

    def _peek_transport_queue_snapshot(self) -> dict[str, float | int]:
        q = self._gnss_raw_queue
        configured_max = max(1, int(self._config.gnss_feed_queue_maxsize))
        if q is None:
            qsize = 0
            maxsize = configured_max
        else:
            qsize = int(q.qsize())
            maxsize = max(1, int(q.maxsize) or configured_max)
        highwater = max(int(self._gnss_raw_q_highwater), int(self._gnss_raw_q_interval_highwater), qsize)
        return {
            "qsize": qsize,
            "maxsize": maxsize,
            "pct": 100.0 * qsize / maxsize,
            "lifetime_highwater": highwater,
            "lifetime_highwater_pct": 100.0 * highwater / maxsize,
        }

    def _log_rx_transport_event_context(self, event: str, recv_elapsed_s: float) -> None:
        if event == "timeout" and self._timeout_count > 5 and self._timeout_count % 20 != 0:
            return
        qstats = self._peek_transport_queue_snapshot()
        expected_chunk_ms = (
            1000.0
            * float(self._config.samples_per_chunk)
            / max(1.0, float(self._config.sample_rate))
        )
        gnss_snapshot: dict[str, object] = {}
        bridge = self._gnss_bridge
        if bridge is not None:
            try:
                gnss_snapshot = bridge.snapshot()
            except Exception as exc:  # pragma: no cover - diagnostic path
                gnss_snapshot = {"snapshot_error": str(exc)}
        count = self._overflow_count if event == "overflow" else self._timeout_count
        self._loggers["transport"].warning(
            "RX %s context: count=%d raw=%d overflow_total=%d overflow_streak=%d "
            "timeout_total=%d recv_ms=%.2f expected_chunk_ms=%.2f rx_gap_max_ms=%.2f "
            "raw_q=%d/%d raw_q_pct=%.1f raw_highwater=%d/%d raw_highwater_pct=%.1f "
            "raw_rejections=%d receiver_time_s=%s pvt_seen=%s pvt_current=%s "
            "pvt_stale_reason=%s udp_pvt_packets=%s udp_observables_packets=%s "
            "udp_tracking_packets=%s udp_parse_errors=%s udp_pvt_age_s=%s "
            "udp_observables_age_s=%s udp_tracking_age_s=%s receiver_log_mb=%s "
            "receiver_log_kbps=%s snapshot_error=%s",
            event,
            int(count),
            int(self._raw_chunk_count),
            int(self._overflow_count),
            int(self._overflow_streak),
            int(self._timeout_count),
            recv_elapsed_s * 1000.0,
            expected_chunk_ms,
            self._rx_max_gap_s * 1000.0,
            int(qstats["qsize"]),
            int(qstats["maxsize"]),
            float(qstats["pct"]),
            int(qstats["lifetime_highwater"]),
            int(qstats["maxsize"]),
            float(qstats["lifetime_highwater_pct"]),
            int(self._gnss_raw_drops),
            gnss_snapshot.get("receiver_time_s", "--"),
            bool(gnss_snapshot.get("pvt_output_seen", False)),
            bool(gnss_snapshot.get("pvt_current", False)),
            gnss_snapshot.get("stale_reason", "--"),
            gnss_snapshot.get("udp_pvt_packets", "--"),
            gnss_snapshot.get("udp_observables_packets", "--"),
            gnss_snapshot.get("udp_tracking_packets", "--"),
            gnss_snapshot.get("udp_parse_errors", "--"),
            self._format_optional_float(gnss_snapshot.get("udp_pvt_age_s")),
            self._format_optional_float(gnss_snapshot.get("udp_observables_age_s")),
            self._format_optional_float(gnss_snapshot.get("udp_tracking_age_s")),
            self._format_mb(gnss_snapshot.get("receiver_log_bytes")),
            self._format_kbps(gnss_snapshot.get("receiver_log_rate_bps")),
            gnss_snapshot.get("snapshot_error", "--"),
        )

    def _log_transport_heartbeat(self, snapshot: dict[str, dict[str, float]]) -> None:
        qstats = self._transport_queue_snapshot()
        self._loggers["transport"].info(
            "GNSS transport heartbeat: raw_q=%d/%d raw_q_pct=%.1f "
            "raw_q_interval_highwater=%d/%d raw_q_interval_highwater_pct=%.1f "
            "raw_q_lifetime_highwater=%d/%d raw_q_lifetime_highwater_pct=%.1f "
            "raw_rejections=%d rx_recv_avg_ms=%.2f rx_recv_max_ms=%.2f rx_recv_n=%d "
            "rx_gnss_queue_publish_avg_ms=%.2f rx_gnss_queue_publish_max_ms=%.2f "
            "rx_gnss_queue_publish_n=%d gnss_queue_wait_avg_ms=%.2f "
            "gnss_queue_wait_max_ms=%.2f gnss_queue_wait_n=%d "
            "gnss_beamform_compute_avg_ms=%.2f gnss_beamform_compute_max_ms=%.2f "
            "gnss_beamform_compute_n=%d gnss_fifo_write_avg_ms=%.2f "
            "gnss_fifo_write_max_ms=%.2f gnss_fifo_write_n=%d "
            "dsp_phase_avg_ms=%.2f dsp_phase_max_ms=%.2f dsp_phase_n=%d "
            "dsp_doa_avg_ms=%.2f dsp_doa_max_ms=%.2f dsp_doa_n=%d "
            "dsp_lcmv_avg_ms=%.2f dsp_lcmv_max_ms=%.2f dsp_lcmv_n=%d "
            "ui_gnss_snapshot_avg_ms=%.2f ui_gnss_snapshot_max_ms=%.2f "
            "ui_gnss_snapshot_n=%d ui_compose_metrics_avg_ms=%.2f "
            "ui_compose_metrics_max_ms=%.2f ui_compose_metrics_n=%d "
            "ui_emit_callback_avg_ms=%.2f ui_emit_callback_max_ms=%.2f "
            "ui_emit_callback_n=%d "
            "rx_gap_max_ms=%.2f "
            "rx_overflows=%d rx_overflow_streak=%d rx_timeouts=%d",
            int(qstats["qsize"]),
            int(qstats["maxsize"]),
            float(qstats["pct"]),
            int(qstats["interval_highwater"]),
            int(qstats["maxsize"]),
            float(qstats["interval_highwater_pct"]),
            int(qstats["lifetime_highwater"]),
            int(qstats["maxsize"]),
            float(qstats["lifetime_highwater_pct"]),
            int(self._gnss_raw_drops),
            self._perf_ms(snapshot, "rx_recv", "avg"),
            self._perf_ms(snapshot, "rx_recv", "max"),
            self._perf_count(snapshot, "rx_recv"),
            self._perf_ms(snapshot, "rx_gnss_queue_publish", "avg"),
            self._perf_ms(snapshot, "rx_gnss_queue_publish", "max"),
            self._perf_count(snapshot, "rx_gnss_queue_publish"),
            self._perf_ms(snapshot, "gnss_queue_wait", "avg"),
            self._perf_ms(snapshot, "gnss_queue_wait", "max"),
            self._perf_count(snapshot, "gnss_queue_wait"),
            self._perf_ms(snapshot, "gnss_beamform_compute", "avg"),
            self._perf_ms(snapshot, "gnss_beamform_compute", "max"),
            self._perf_count(snapshot, "gnss_beamform_compute"),
            self._perf_ms(snapshot, "gnss_fifo_write", "avg"),
            self._perf_ms(snapshot, "gnss_fifo_write", "max"),
            self._perf_count(snapshot, "gnss_fifo_write"),
            self._perf_ms(snapshot, "dsp_phase", "avg"),
            self._perf_ms(snapshot, "dsp_phase", "max"),
            self._perf_count(snapshot, "dsp_phase"),
            self._perf_ms(snapshot, "dsp_doa", "avg"),
            self._perf_ms(snapshot, "dsp_doa", "max"),
            self._perf_count(snapshot, "dsp_doa"),
            self._perf_ms(snapshot, "dsp_lcmv", "avg"),
            self._perf_ms(snapshot, "dsp_lcmv", "max"),
            self._perf_count(snapshot, "dsp_lcmv"),
            self._perf_ms(snapshot, "ui_gnss_snapshot", "avg"),
            self._perf_ms(snapshot, "ui_gnss_snapshot", "max"),
            self._perf_count(snapshot, "ui_gnss_snapshot"),
            self._perf_ms(snapshot, "ui_compose_metrics", "avg"),
            self._perf_ms(snapshot, "ui_compose_metrics", "max"),
            self._perf_count(snapshot, "ui_compose_metrics"),
            self._perf_ms(snapshot, "ui_emit_callback", "avg"),
            self._perf_ms(snapshot, "ui_emit_callback", "max"),
            self._perf_count(snapshot, "ui_emit_callback"),
            self._rx_max_gap_s * 1000.0,
            int(self._overflow_count),
            int(self._overflow_streak),
            int(self._timeout_count),
        )

    # -------------------------------------------------------------------------
    # GNSS Handoff Helpers
    # -------------------------------------------------------------------------

    def _effective_gnss_weights(self, weights: np.ndarray) -> np.ndarray:
        correction_vector = self._config.phase_correction_vector
        if correction_vector is None:
            return np.zeros((0,), dtype=np.complex64)
        selected = np.asarray(weights, dtype=np.complex128).reshape(-1)
        correction = np.asarray(correction_vector, dtype=np.complex128).reshape(-1)
        if selected.size == 0 or selected.size != correction.size:
            return np.zeros((0,), dtype=np.complex64)
        # Cache the conjugated/calibrated GNSS handoff vector when weights
        # change. The RX chunk path then performs only the weighted sum.
        return np.asarray(np.conj(selected) * correction, dtype=np.complex64)

    def _store_beamformer_weights(self, weights: np.ndarray) -> np.ndarray:
        selected = np.asarray(weights, dtype=np.complex128).reshape(-1)
        if selected.size == 0:
            selected = uniform_weights(len(self._config.channels))
        self._latest_beamformer_weights = selected
        self._latest_gnss_effective_weights = self._effective_gnss_weights(selected)
        return selected

    def _set_beamformer_weights(self, weights: np.ndarray) -> np.ndarray:
        with self._beamformer_lock:
            return self._store_beamformer_weights(weights)

    def _get_beamformer_weights_copy(self) -> np.ndarray:
        with self._beamformer_lock:
            return np.array(self._latest_beamformer_weights, copy=True)

    def _get_gnss_effective_weights(self) -> np.ndarray:
        with self._beamformer_lock:
            return self._latest_gnss_effective_weights

    @staticmethod
    def _weighted_sum_complex64(source: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Fast small-array weighted sum for the GNSS FIFO hot path."""
        x = np.asarray(source, dtype=np.complex64)
        w = np.asarray(weights, dtype=np.complex64).reshape(-1)
        if x.ndim != 2 or x.shape[1] == 0 or w.size == 0:
            return np.zeros((0,), dtype=np.complex64)
        if x.shape[0] != w.size:
            raise ValueError(
                f"GNSS beamformer weight count {w.size} does not match channel count {x.shape[0]}"
            )
        out = np.empty((x.shape[1],), dtype=np.complex64)
        np.multiply(x[0], w[0], out=out)
        if x.shape[0] == 1:
            return out
        tmp = np.empty_like(out)
        for idx in range(1, x.shape[0]):
            np.multiply(x[idx], w[idx], out=tmp)
            np.add(out, tmp, out=out)
        return out

    def _gnss_output_vector(self, chunk: np.ndarray) -> np.ndarray:
        return self._gnss_beamformed_output_vector(chunk)

    def _gnss_beamformed_output_vector(self, chunk: np.ndarray) -> np.ndarray:
        if self._config.phase_correction_vector is not None:
            source = np.asarray(chunk, dtype=np.complex64)
            if source.ndim != 2 or source.shape[1] == 0:
                return np.zeros((0,), dtype=np.complex64)
            effective_weights = self._get_gnss_effective_weights()
            if effective_weights.size != source.shape[0]:
                weights = self._get_beamformer_weights_copy()
                return compute_gnss_output_vector(
                    buffer=chunk,
                    ref_channel=self._config.phase_ref_channel,
                    beamformer_weights=weights,
                    phase_correction_vector=self._config.phase_correction_vector,
                )
            # Static calibration lets the realtime GNSS handoff collapse:
            #   apply_phase_calibration(chunk) -> apply_beamformer(...)
            # into one weighted sum without allocating a full complex128
            # corrected channel matrix for every RX chunk.
            return self._weighted_sum_complex64(source, effective_weights)
        return compute_gnss_output_vector(
            buffer=chunk,
            ref_channel=self._config.phase_ref_channel,
            beamformer_weights=self._get_beamformer_weights_copy(),
            phase_correction_vector=self._config.phase_correction_vector,
        )

    def _gnss_handoff_mode_label(self) -> str:
        return "beamformed_continuous"

    # -------------------------------------------------------------------------
    # GNSS Handoff Threads
    # -------------------------------------------------------------------------

    def _gnss_beamform_loop(self) -> None:
        while True:
            raw_q = self._gnss_raw_queue
            if raw_q is None:
                return
            try:
                wait_t0 = time.monotonic()
                chunk = raw_q.get(timeout=0.25)
                self._record_runtime_timing("gnss_queue_wait", time.monotonic() - wait_t0)
            except queue.Empty:
                self._record_runtime_timing("gnss_queue_wait", time.monotonic() - wait_t0)
                if not self._running:
                    break
                continue
            if chunk is None:
                break
            bridge = self._gnss_bridge
            if bridge is None or not bridge.active:
                break
            try:
                compute_t0 = time.monotonic()
                gnss_vector = self._gnss_output_vector(chunk)
                self._record_runtime_timing(
                    "gnss_beamform_compute",
                    time.monotonic() - compute_t0,
                )
                if gnss_vector.size > 0:
                    self._update_gnss_fifo_signal_health(gnss_vector)
                    if self._gnss_fifo_health_chunk_counter >= max(
                        1, int(self._config.rx_health_log_interval_chunks)
                    ):
                        self._log_gnss_fifo_signal_health_summary(
                            samples_per_chunk=int(gnss_vector.size)
                        )
                        self._reset_gnss_fifo_signal_health()
                    write_t0 = time.monotonic()
                    if not bridge.write(gnss_vector):
                        raise RuntimeError(
                            "GNSS-SDR FIFO did not accept the contiguous IQ chunk"
                        )
                    self._record_runtime_timing("gnss_fifo_write", time.monotonic() - write_t0)
            except Exception as exc:
                self._handle_gnss_pipeline_error(exc)
                break

    # -------------------------------------------------------------------------
    # Pipeline Error Handling
    # -------------------------------------------------------------------------

    def _handle_gnss_pipeline_error(self, exc: BaseException) -> None:
        msg = f"GNSS pipeline failed: {exc}"
        status_msg = f"GNSS-SDR handoff paused; SDR stream still running ({exc})"
        with self._gnss_failure_lock:
            if self._gnss_pipeline_failed:
                return
            self._gnss_pipeline_failed = True
            raw_q = self._gnss_raw_queue
            self._gnss_raw_queue = None
            bridge = self._gnss_bridge
            self._gnss_bridge = None
            thread = self._gnss_handoff_thread

        self._loggers["errors"].error("%s", msg)
        self._loggers["transport"].error("%s", status_msg)
        self._emit_status(status_msg)
        if raw_q is not None:
            try:
                raw_q.put_nowait(None)
            except queue.Full:
                pass
        if bridge is not None:
            bridge.stop(msg)
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=0.5)
            except Exception:
                pass

    def _failed_stop(self, message: str) -> None:
        self._set_stop_reason(message)
        self._running = False
        self._signal_dsp_shutdown()
        self._emit_failed(message)
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:
                pass

    def _signal_dsp_shutdown(self) -> None:
        put_latest(self._phase_queue, None)
        put_latest(self._doa_queue, None)
        put_latest(self._lcmv_queue, None)

    def _set_stop_reason(self, reason: str) -> None:
        reason = str(reason).strip() or "normal stop"
        cleanup_reasons = {"normal stop", "GUI close", "python shutdown"}
        current = str(self._stop_reason).strip()
        if current and current not in {"not started", "normal stop"}:
            if reason in cleanup_reasons:
                return
        self._stop_reason = reason

    def _record_queue_occupancy(self, q: queue.Queue, which: str) -> None:
        qsize = int(q.qsize())
        maxsize = max(1, int(q.maxsize))
        self._gnss_raw_q_interval_highwater = max(
            int(self._gnss_raw_q_interval_highwater),
            qsize,
        )
        ratio = 100.0 * qsize / maxsize
        marks = [25, 50, 75, 90, 100]
        if qsize <= self._gnss_raw_q_highwater:
            return
        self._gnss_raw_q_highwater = qsize
        seen_marks = self._gnss_raw_q_marks_logged
        for mark in marks:
            if ratio >= mark and mark not in seen_marks:
                seen_marks.add(mark)
                self._loggers["transport"].info(
                    "GNSS %s queue high-water: %d/%d (%.0f%%).",
                    "raw",
                    qsize,
                    maxsize,
                    ratio,
                )

    def _publish_gnss_raw_chunk(self, q: queue.Queue, chunk: np.ndarray) -> None:
        # recv_chunk allocates a new array for each receive. Avoid an
        # unconditional hot-path copy; only copy if UHD ever hands us a
        # non-contiguous view.
        q.put_nowait(np.ascontiguousarray(chunk))
        self._record_queue_occupancy(q, "raw")

    def _record_gnss_raw_drop(self, q: queue.Queue) -> None:
        self._gnss_raw_drops += 1
        if self._gnss_raw_drops == 1 or self._gnss_raw_drops % 50 == 0:
            self._loggers["transport"].error(
                "GNSS raw queue full: rejected %d chunk(s) qsize=%d/%d; "
                "handoff continuity cannot be preserved.",
                self._gnss_raw_drops,
                q.qsize(),
                max(1, int(self._config.gnss_feed_queue_maxsize)),
            )

    # -------------------------------------------------------------------------
    # DSP Stage Loops
    # -------------------------------------------------------------------------

    def _phase_loop(self) -> None:
        while self._running:
            try:
                work_item = self._phase_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if work_item is None:
                break
            t0 = time.monotonic()
            phase_metrics = compute_phase_metrics(
                buffer=work_item.chunk,
                ref_channel=self._config.phase_ref_channel,
                preview_cols=self._ui_preview_cols,
                phase_correction_vector=self._config.phase_correction_vector,
                sample_rate_hz=self._config.sample_rate,
                phase_monitor_tone_offset_hz=self._config.phase_monitor_tone_offset_hz,
                phase_monitor_use_tone_bin=bool(
                    self._config.live_phase_monitor_use_tone_bin
                ),
            )
            calibrated_chunk = np.asarray(phase_metrics["calibrated_buffer"], dtype=np.complex128)
            with self._results_lock:
                self._latest_powers = np.asarray(phase_metrics["powers"], dtype=np.float64)
                self._latest_phase_offsets = np.asarray(
                    phase_metrics["phase_offsets_deg"], dtype=np.float64
                )
                self._latest_phase_offsets_raw = np.asarray(
                    phase_metrics["phase_offsets_raw_deg"], dtype=np.float64
                )
                self._latest_phase_offsets_calibrated = np.asarray(
                    phase_metrics["phase_offsets_calibrated_deg"], dtype=np.float64
                )
                self._latest_ui_raw_preview = np.asarray(
                    phase_metrics["complex_samples_raw"], dtype=np.complex64
                )
                self._latest_ui_calibrated_preview = np.asarray(
                    phase_metrics["complex_samples_calibrated"], dtype=np.complex64
                )
                self._last_phase_ts = time.monotonic()
            now = time.monotonic()
            if (now - self._last_phase_log_ts) >= self._doa_log_interval_s:
                self._loggers["phase"].info(
                    "phase offsets raw_deg=[%s] calibrated_deg=[%s] powers_db=[%s] "
                    "static_calibration=%s calibration_file=%s estimator=%s tone_hz=%.1f",
                    self._format_vector_deg(phase_metrics["phase_offsets_raw_deg"]),
                    self._format_vector_deg(phase_metrics["phase_offsets_calibrated_deg"]),
                    self._format_power_vector_db(phase_metrics["powers"]),
                    "yes" if self._config.phase_correction_vector is not None else "dynamic",
                    self._config.phase_calibration_file or "--",
                    phase_metrics.get("phase_estimator", "--"),
                    float(phase_metrics.get("phase_monitor_estimated_offset_hz", 0.0)),
                )
                self._last_phase_log_ts = now
            put_latest(
                self._doa_queue,
                PhaseResult(
                    calibrated_chunk=calibrated_chunk,
                ),
            )
            dt = time.monotonic() - t0
            self._record_runtime_timing("dsp_phase", dt)
            time.sleep(max(0.0, self._dsp_emit_interval_s - dt))

    def _doa_loop(self) -> None:
        while self._running:
            try:
                phase_result = self._doa_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if phase_result is None:
                break
            t0 = time.monotonic()
            doa_metrics = compute_doa_metrics(
                corrected_buffer=phase_result.calibrated_chunk,
                center_freq_hz=self._config.center_freq_hz,
                scan_angles_deg=self._scan_angles_deg,
                uca_radius_m=self._config.uca_radius_m,
                n_sources=max(int(self._expected_sources), 1),
                doa_method=self._doa_method.lower().strip(),
            )
            spec = np.asarray(doa_metrics["doa_spectrum"], dtype=np.float64)
            raw_spec = np.asarray(doa_metrics["doa_raw_spectrum"], dtype=np.float64)
            doa_deg = float(doa_metrics["doa_deg"])
            doa_display_deg = self._internal_angle_to_display(doa_deg)
            jammer_status = self._assess_jammer_candidate(spec, doa_deg)
            mitigation_active, mitigation_doa_deg = self._jammer_mitigation_state(
                jammer_status,
                doa_deg,
            )
            mitigation_display_deg = self._internal_angle_to_display(mitigation_doa_deg)
            jammer_status["doa_display_deg"] = doa_display_deg
            jammer_status["mitigation_doa_display_deg"] = mitigation_display_deg
            with self._results_lock:
                self._latest_doa_spectrum = spec
                self._latest_doa_raw_spectrum = raw_spec
                self._latest_doa_deg = mitigation_doa_deg if mitigation_active else doa_deg
                self._latest_jammer_status = jammer_status
                self._last_doa_ts = time.monotonic()
            put_latest(
                self._lcmv_queue,
                BeamformingWorkItem(
                    calibrated_chunk=phase_result.calibrated_chunk,
                    doa_deg=mitigation_doa_deg if mitigation_active else doa_deg,
                    jammer_detected=mitigation_active,
                ),
            )
            now = time.monotonic()
            if (now - self._last_doa_log_ts) >= self._doa_log_interval_s:
                self._loggers["doa"].info(
                    "doa method=%s nsrc=%d doa_deg_internal=%.2f doa_display_deg=%.2f "
                    "display=clockwise max_spec=%.4f",
                    doa_metrics["doa_method"],
                    int(doa_metrics["n_sources"]),
                    doa_deg,
                    doa_display_deg,
                    float(np.max(spec)),
                )
                self._log_jammer_detection_status(jammer_status, doa_deg)
                self._last_doa_log_ts = now
            dt = time.monotonic() - t0
            self._record_runtime_timing("dsp_doa", dt)
            time.sleep(max(0.0, self._dsp_emit_interval_s - dt))

    def _lcmv_loop(self) -> None:
        while self._running:
            try:
                lcmv_work = self._lcmv_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if lcmv_work is None:
                break
            t0 = time.monotonic()
            apply_null = bool(self._lcmv_force_null or lcmv_work.jammer_detected)
            if not apply_null:
                # Keep the display pattern neutral until mitigation explicitly
                # requests a null. The GNSS-SDR handoff still uses these uniform
                # beamformer weights so the receiver input type stays continuous.
                weights = uniform_weights(len(self._config.channels))
                beamformed_stream = apply_beamformer(lcmv_work.calibrated_chunk, weights)
                input_power_db = self._received_iq_power_db(lcmv_work.calibrated_chunk)
                output_power_db = self._power_db(beamformed_stream)
                look_pattern_db = beamformer_pattern_db(
                    weights=weights,
                    rf_freq_hz=self._config.center_freq_hz,
                    scan_angles_deg=self._scan_angles_deg,
                    n_channels=len(self._config.channels),
                    uca_radius_m=self._config.uca_radius_m,
                )
                self._set_beamformer_weights(weights)
                with self._results_lock:
                    self._latest_lcmv_db = np.asarray(look_pattern_db, dtype=np.float64)
                    self._latest_lcmv_null_active = False
                    self._latest_lcmv_input_power_db = input_power_db
                    self._latest_lcmv_output_power_db = output_power_db
                    self._latest_lcmv_power_delta_db = input_power_db - output_power_db
                    self._last_lcmv_ts = time.monotonic()
                self._maybe_log_lcmv_state(
                    null_active=False,
                    input_power_db=input_power_db,
                    output_power_db=output_power_db,
                    power_delta_db=input_power_db - output_power_db,
                    null_theta_deg=float("nan"),
                    pattern_null_db=float("nan"),
                    data_null_residual_db=float("nan"),
                    weight_norm=float(np.linalg.norm(weights)),
                    max_weight_abs=float(np.max(np.abs(weights))),
                )
                dt = time.monotonic() - t0
                self._record_runtime_timing("dsp_lcmv", dt)
                time.sleep(max(0.0, self._dsp_emit_interval_s - dt))
                continue
            lcmv_metrics = compute_lcmv_metrics(
                corrected_buffer=lcmv_work.calibrated_chunk,
                center_freq_hz=self._config.center_freq_hz,
                scan_angles_deg=self._scan_angles_deg,
                uca_radius_m=self._config.uca_radius_m,
                null_theta_deg=lcmv_work.doa_deg,
                ref_channel=self._config.phase_ref_channel,
            )
            with self._beamformer_lock:
                # Empty weights should not propagate into the GNSS handoff path;
                # fall back to uniform weights if the solver cannot produce them.
                selected_weights = (
                    np.asarray(lcmv_metrics["weights"], dtype=np.complex128)
                    if np.asarray(lcmv_metrics["weights"]).size > 0
                    else uniform_weights(len(self._config.channels))
                )
                weights = self._store_beamformer_weights(selected_weights)
            beamformed_stream = apply_beamformer(lcmv_work.calibrated_chunk, weights)
            input_power_db = self._received_iq_power_db(lcmv_work.calibrated_chunk)
            output_power_db = self._power_db(beamformed_stream)
            pattern_db = np.asarray(lcmv_metrics["lcmv_pattern_db"], dtype=np.float64)
            null_signature = np.asarray(lcmv_metrics.get("null_signature", []), dtype=np.complex128)
            null_index = int(np.argmin(np.abs(self._scan_angles_deg - float(lcmv_work.doa_deg))))
            pattern_null_db = float(pattern_db[null_index]) if pattern_db.size else float("nan")
            data_null_residual_db = (
                self._power_db(np.array([np.vdot(weights, null_signature)], dtype=np.complex128))
                if null_signature.size == weights.size
                else float("nan")
            )
            with self._results_lock:
                self._latest_lcmv_db = pattern_db
                self._latest_lcmv_null_active = True
                self._latest_lcmv_input_power_db = input_power_db
                self._latest_lcmv_output_power_db = output_power_db
                self._latest_lcmv_power_delta_db = input_power_db - output_power_db
                self._last_lcmv_ts = time.monotonic()
            self._maybe_log_lcmv_state(
                null_active=True,
                input_power_db=input_power_db,
                output_power_db=output_power_db,
                power_delta_db=input_power_db - output_power_db,
                null_theta_deg=lcmv_work.doa_deg,
                pattern_null_db=pattern_null_db,
                data_null_residual_db=data_null_residual_db,
                weight_norm=float(np.linalg.norm(weights)),
                max_weight_abs=float(np.max(np.abs(weights))),
            )
            dt = time.monotonic() - t0
            self._record_runtime_timing("dsp_lcmv", dt)
            time.sleep(max(0.0, self._dsp_emit_interval_s - dt))

    # -------------------------------------------------------------------------
    # Beamforming Solvers and Logging
    # -------------------------------------------------------------------------

    def _maybe_log_lcmv_state(
        self,
        *,
        null_active: bool,
        input_power_db: float,
        output_power_db: float,
        power_delta_db: float,
        null_theta_deg: float,
        pattern_null_db: float,
        data_null_residual_db: float,
        weight_norm: float,
        max_weight_abs: float,
    ) -> None:
        now = time.monotonic()
        if (now - self._last_lcmv_log_ts) < self._doa_log_interval_s:
            return
        self._last_lcmv_log_ts = now
        null_display_deg = self._internal_angle_to_display(null_theta_deg)
        self._loggers["lcmv"].info(
            "beamforming algorithm=%s state=active null_active=%s "
            "null_theta_deg_internal=%s null_display_deg=%s display=clockwise "
            "input_power_db=%s output_power_db=%s power_delta_db=%s "
            "pattern_null_db=%s data_null_residual_db=%s "
            "weight_norm=%s max_weight_abs=%s force_null=%s",
            self._algorithm_mode,
            bool(null_active),
            self._format_optional_float(null_theta_deg),
            self._format_optional_float(null_display_deg),
            self._format_optional_float(input_power_db),
            self._format_optional_float(output_power_db),
            self._format_optional_float(power_delta_db),
            self._format_optional_float(pattern_null_db),
            self._format_optional_float(data_null_residual_db),
            self._format_optional_float(weight_norm),
            self._format_optional_float(max_weight_abs),
            bool(self._lcmv_force_null),
        )

    # -------------------------------------------------------------------------
    # UI Metrics and Detection
    # -------------------------------------------------------------------------

    def _internal_angle_to_display(self, angle_deg: object) -> float:
        try:
            angle = float(angle_deg)
        except (TypeError, ValueError):
            return float("nan")
        if not np.isfinite(angle):
            return float("nan")
        return internal_angle_to_operator_bearing_deg(angle)

    def _compose_metrics_for_ui(self) -> dict:
        compose_t0 = time.monotonic()
        snapshot_t0 = time.monotonic()
        gnss_snapshot = self._gnss_bridge.snapshot() if self._gnss_bridge is not None else {}
        self._record_runtime_timing("ui_gnss_snapshot", time.monotonic() - snapshot_t0)
        self._maybe_log_gnss_snapshot(gnss_snapshot)
        with self._results_lock:
            # Copy arrays while holding the lock, then build the dict outside the
            # backend update path. This prevents GUI consumers from seeing arrays
            # mutate under them.
            powers = np.array(self._latest_powers, copy=True)
            offsets = np.array(self._latest_phase_offsets, copy=True)
            raw_offsets = np.array(self._latest_phase_offsets_raw, copy=True)
            calibrated_offsets = np.array(self._latest_phase_offsets_calibrated, copy=True)
            raw_preview = np.array(self._latest_ui_raw_preview, copy=True)
            calibrated_preview = np.array(self._latest_ui_calibrated_preview, copy=True)
            doa_spec = np.array(self._latest_doa_spectrum, copy=True)
            doa_raw_spec = np.array(self._latest_doa_raw_spectrum, copy=True)
            doa_deg = float(self._latest_doa_deg)
            doa_display_deg = self._internal_angle_to_display(doa_deg)
            lcmv_db = np.array(self._latest_lcmv_db, copy=True)
            lcmv_null_active = bool(self._latest_lcmv_null_active)
            lcmv_input_power_db = float(self._latest_lcmv_input_power_db)
            lcmv_output_power_db = float(self._latest_lcmv_output_power_db)
            lcmv_power_delta_db = float(self._latest_lcmv_power_delta_db)
            jammer_status = dict(self._latest_jammer_status)
            jammer_status["doa_display_deg"] = self._internal_angle_to_display(
                jammer_status.get("doa_deg", float("nan"))
            )
            jammer_status["mitigation_doa_display_deg"] = self._internal_angle_to_display(
                jammer_status.get("mitigation_doa_deg", float("nan"))
            )
            rx_signal_health = dict(self._latest_rx_signal_health)
        metrics = RuntimeUiMetrics(
            powers=powers,
            phase_offsets_deg=offsets,
            phase_offsets_raw_deg=raw_offsets,
            phase_offsets_calibrated_deg=calibrated_offsets,
            complex_samples=raw_preview,
            complex_samples_raw=raw_preview,
            complex_samples_calibrated=calibrated_preview,
            doa_raw_spectrum=doa_raw_spec,
            music_spectrum=doa_spec,
            doa_spectrum=doa_spec,
            doa_deg=doa_deg,
            doa_display_deg=doa_display_deg,
            algorithm_mode=self._algorithm_mode,
            lcmv_pattern_db=lcmv_db,
            lcmv_null_active=lcmv_null_active,
            lcmv_input_power_db=lcmv_input_power_db,
            lcmv_output_power_db=lcmv_output_power_db,
            lcmv_power_delta_db=lcmv_power_delta_db,
            rx_signal_health=rx_signal_health,
            jammer=jammer_status,
            gnss_snapshot=gnss_snapshot,
        ).to_dict()
        metrics.update(
            {
                "backend_monotonic_s": time.monotonic(),
                "ui_metrics_seq": self._ui_metrics_seq,
                "i_samples": np.real(raw_preview),
                "i_samples_raw": np.real(raw_preview),
                "i_samples_calibrated": np.real(calibrated_preview),
                "doa_method": self._doa_method.lower().strip(),
                "n_sources": max(int(self._expected_sources), 1),
                "jammer_detected": bool(jammer_status.get("detected", False)),
            }
        )
        self._ui_metrics_seq += 1
        self._record_runtime_timing("ui_compose_metrics", time.monotonic() - compose_t0)
        return metrics

    @staticmethod
    def _spatial_peak_db(doa_spectrum: np.ndarray) -> float:
        spectrum = np.asarray(doa_spectrum, dtype=np.float64)
        finite = spectrum[np.isfinite(spectrum) & (spectrum > 0.0)]
        if finite.size < 3:
            return float("nan")
        peak = float(np.max(finite))
        floor = float(np.median(finite))
        if peak <= 0.0 or floor <= 0.0:
            return float("nan")
        return float(10.0 * np.log10(peak / floor))

    def _assess_jammer_candidate(self, doa_spectrum: np.ndarray, doa_deg: float) -> dict[str, object]:
        return self._jammer_detector.assess(
            doa_deg=float(doa_deg),
            input_power_db=self._power_db_from_channel_powers(),
            spatial_peak_db=self._spatial_peak_db(doa_spectrum),
        )

    def _jammer_mitigation_state(
        self,
        jammer_status: dict[str, object],
        doa_deg: float,
    ) -> tuple[bool, float]:
        now = time.monotonic()
        if bool(jammer_status.get("detected", False)):
            mitigation_doa_deg = float(doa_deg)
            self._jammer_null_hold_until_s = now + _JAMMER_NULL_HOLD_S
            self._jammer_null_hold_doa_deg = mitigation_doa_deg
            jammer_status["mitigation_active"] = True
            jammer_status["mitigation_reason"] = "detected"
            jammer_status["mitigation_doa_deg"] = mitigation_doa_deg
            return True, mitigation_doa_deg

        if now < self._jammer_null_hold_until_s:
            held_doa = float(self._jammer_null_hold_doa_deg)
            jammer_status["mitigation_active"] = True
            jammer_status["mitigation_reason"] = "hold_after_detection"
            jammer_status["state"] = "suspected"
            jammer_status["doa_deg"] = held_doa
            jammer_status["mitigation_doa_deg"] = held_doa
            jammer_status["reason"] = (
                f"{jammer_status.get('reason', 'No current detector alarm')}; "
                f"holding null for {_JAMMER_NULL_HOLD_S:.1f}s after last detection"
            )
            return True, held_doa

        jammer_status["mitigation_active"] = False
        jammer_status["mitigation_reason"] = ""
        return False, float(doa_deg)

    def _log_jammer_detection_status(self, jammer_status: dict[str, object], doa_deg: float) -> None:
        self._jammer_log.info(
            "jammer_detection state=%s detected=%s candidate_doa_deg_internal=%.2f "
            "candidate_doa_display_deg=%s mitigation_doa_deg_internal=%s "
            "mitigation_doa_display_deg=%s display=clockwise "
            "input_power_db=%s baseline_db=%s power_rise_db=%s "
            "power_rise_threshold_db=%.2f spatial_peak_db=%s "
            "spatial_peak_threshold_db=%.2f raw_power_alarm_count=%d/%d "
            "spatial_alarm_count=%d/%d "
            "mitigation_active=%s mitigation_reason=%s reason=%s",
            str(jammer_status.get("state", "not_assessed")),
            bool(jammer_status.get("detected", False)),
            doa_deg,
            self._format_optional_float(
                float(jammer_status.get("doa_display_deg", float("nan")))
            ),
            self._format_optional_float(
                float(jammer_status.get("mitigation_doa_deg", float("nan")))
            ),
            self._format_optional_float(
                float(jammer_status.get("mitigation_doa_display_deg", float("nan")))
            ),
            self._format_optional_float(float(jammer_status.get("input_power_db", float("nan")))),
            self._format_optional_float(float(jammer_status.get("power_baseline_db", float("nan")))),
            self._format_optional_float(float(jammer_status.get("power_rise_db", float("nan")))),
            float(jammer_status.get("power_rise_threshold_db", float("nan"))),
            self._format_optional_float(float(jammer_status.get("spatial_peak_db", float("nan")))),
            float(jammer_status.get("spatial_peak_threshold_db", float("nan"))),
            int(jammer_status.get("raw_power_alarm_count", 0)),
            int(jammer_status.get("required_consecutive_alarms", 1)),
            int(jammer_status.get("spatial_alarm_count", 0)),
            int(jammer_status.get("required_consecutive_alarms", 1)),
            bool(jammer_status.get("mitigation_active", False)),
            str(jammer_status.get("mitigation_reason", "")),
            str(jammer_status.get("reason", "")),
        )

    # -------------------------------------------------------------------------
    # Power and Formatting Helpers
    # -------------------------------------------------------------------------

    def _power_db_from_channel_powers(self) -> float:
        with self._results_lock:
            powers = np.asarray(self._latest_powers, dtype=np.float64)
        finite = powers[np.isfinite(powers) & (powers > 0.0)]
        if finite.size == 0:
            return float("nan")
        return 10.0 * float(np.log10(float(np.mean(finite)) + 1e-30))

    def _received_iq_power_db(self, channel_samples: np.ndarray) -> float:
        return self._power_db(channel_samples)

    def _power_db(self, samples: np.ndarray) -> float:
        values = np.asarray(samples, dtype=np.complex128)
        if values.size == 0:
            return float("nan")
        power = float(np.mean(np.abs(values) ** 2))
        if power <= 0.0 or not np.isfinite(power):
            return float("nan")
        return 10.0 * float(np.log10(power + 1e-30))

    def _format_vector_deg(self, values: object) -> str:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
        return ", ".join(
            f"{float(v):+.2f}" if np.isfinite(v) else "--"
            for v in arr
        )

    def _format_power_vector_db(self, values: object) -> str:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
        powers_db = 10.0 * np.log10(np.maximum(arr, 1e-30))
        return ", ".join(
            f"{float(v):+.2f}" if np.isfinite(v) else "--"
            for v in powers_db
        )

    def _format_optional_float(self, value: object, digits: int = 2) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "--"
        if not np.isfinite(number):
            return "--"
        return f"{number:.{max(0, int(digits))}f}"

    @staticmethod
    def _format_satellite_ids(labels_obj: object, fallback_prns_obj: object = None) -> str:
        labels: list[str] = []
        if isinstance(labels_obj, (list, tuple, set)):
            for raw_label in labels_obj:
                label = str(raw_label).strip()
                if label and label != "--":
                    labels.append(label)
        if labels:
            return ",".join(labels)

        if isinstance(fallback_prns_obj, (list, tuple, set)):
            for raw_prn in fallback_prns_obj:
                try:
                    prn = int(raw_prn)
                except (TypeError, ValueError):
                    continue
                if prn > 0:
                    labels.append(f"G{prn:02d}")
        return ",".join(labels) if labels else "--"

    @classmethod
    def _format_used_pvt_satellites(cls, gnss_snapshot: dict[str, object]) -> str:
        labels = cls._format_satellite_ids(
            gnss_snapshot.get("used_in_fix_satellites"),
            gnss_snapshot.get("used_in_fix_prns"),
        )
        if labels != "--":
            return labels

        fallback_labels: list[str] = []
        for entry in gnss_snapshot.get("prns", []):
            if not isinstance(entry, dict) or not bool(entry.get("used_in_fix", False)):
                continue
            satellite_id = str(entry.get("satellite_id", "")).strip()
            if satellite_id:
                fallback_labels.append(satellite_id)
                continue
            try:
                prn = int(entry.get("prn"))
            except (TypeError, ValueError):
                continue
            if prn > 0:
                fallback_labels.append(f"G{prn:02d}")
        return ",".join(fallback_labels) if fallback_labels else "--"

    @staticmethod
    def _used_pvt_count(gnss_snapshot: dict[str, object]) -> int:
        try:
            return max(0, int(gnss_snapshot.get("used_in_fix_count", 0)))
        except (TypeError, ValueError):
            return sum(
                1
                for entry in gnss_snapshot.get("prns", [])
                if isinstance(entry, dict) and bool(entry.get("used_in_fix", False))
            )

    # -------------------------------------------------------------------------
    # GNSS Snapshot Logging
    # -------------------------------------------------------------------------

    def _maybe_log_gnss_snapshot(self, gnss_snapshot: dict[str, object]) -> None:
        if not gnss_snapshot:
            return
        self._maybe_log_gnss_pvt_transition(gnss_snapshot)
        self._maybe_log_gnss_pvt_quality_transition(gnss_snapshot)
        now = time.monotonic()
        if (now - self._last_gnss_snapshot_log_ts) < 1.0:
            return
        self._last_gnss_snapshot_log_ts = now
        raw_q = self._gnss_raw_queue
        raw_q_text = (
            f"{raw_q.qsize()}/{max(1, int(raw_q.maxsize))}" if raw_q is not None else "--"
        )
        tracking_prns = self._format_satellite_ids(
            gnss_snapshot.get("tracking_satellites"),
            gnss_snapshot.get("tracking_prns"),
        )
        stable_prns = self._format_satellite_ids(
            gnss_snapshot.get("stable_tracking_satellites"),
            gnss_snapshot.get("stable_tracking_prns"),
        )
        pending_prns = self._format_satellite_ids(
            gnss_snapshot.get("pending_tracking_satellites"),
            gnss_snapshot.get("pending_tracking_prns"),
        )
        unstable_prns = self._format_satellite_ids(
            gnss_snapshot.get("unstable_tracking_satellites"),
            gnss_snapshot.get("unstable_tracking_prns"),
        )
        used_count = self._used_pvt_count(gnss_snapshot)
        used_prns = self._format_used_pvt_satellites(gnss_snapshot)
        acquired_prns = self._format_satellite_ids(
            gnss_snapshot.get("acquired_satellites"),
            gnss_snapshot.get("acquired_prns"),
        )
        avg_cno_db_hz = gnss_snapshot.get("avg_tracking_cno_db_hz")
        avg_cno_text = (
            f"{float(avg_cno_db_hz):.2f}" if isinstance(avg_cno_db_hz, (int, float)) else "--"
        )
        receiver_log_mb = self._format_mb(gnss_snapshot.get("receiver_log_bytes"))
        receiver_log_kbps = self._format_kbps(gnss_snapshot.get("receiver_log_rate_bps"))
        accuracy_obj = gnss_snapshot.get("accuracy", {})
        accuracy = accuracy_obj if isinstance(accuracy_obj, dict) else {}
        pvt_quality = self._gnss_pvt_quality_fields(gnss_snapshot, used_count, accuracy)
        self._handoff_log.info(
            "runtime->GNSS snapshot: mode=%s beamforming=%s algorithm=%s raw_q=%s "
            "tracking=%s stable_bars=%s pending=%s unstable=%s used_pvt=%s acquired=%s "
            "receiver_time_s=%s pvt_seen=%s pvt_current=%s pvt_observations=%s avg_cno_db_hz=%s "
            "pvt_gui_status=%s pvt_gui_reason=%s pvt_evidence=%s fix_type=%s "
            "valid_sats=%s solution_status=%s solution_type=%s "
            "lat_deg=%s lon_deg=%s alt_m=%s "
            "truth_east_error_m=%s truth_north_error_m=%s truth_up_error_m=%s "
            "truth_h_error_m=%s truth_3d_error_m=%s "
            "hdop=%s vdop=%s pdop=%s gdop=%s "
            "receiver_log_mb=%s receiver_log_kbps=%s udp_pvt_packets=%s "
            "udp_observables_packets=%s udp_tracking_packets=%s udp_parse_errors=%s "
            "udp_pvt_age_s=%s udp_observables_age_s=%s udp_tracking_age_s=%s",
            self._gnss_handoff_mode_label(),
            "on",
            self._algorithm_mode,
            raw_q_text,
            tracking_prns,
            stable_prns,
            pending_prns,
            unstable_prns,
            used_prns,
            acquired_prns,
            gnss_snapshot.get("receiver_time_s", "--"),
            bool(gnss_snapshot.get("pvt_output_seen", False)),
            bool(gnss_snapshot.get("pvt_current", False)),
            gnss_snapshot.get("pvt_observation_count", "--"),
            avg_cno_text,
            pvt_quality["status"],
            pvt_quality["reason"],
            pvt_quality["evidence"],
            str(accuracy.get("fix_type", "--") or "--").replace(" ", "_"),
            self._format_optional_float(accuracy.get("valid_sats"), 0),
            self._format_optional_float(accuracy.get("solution_status"), 0),
            self._format_optional_float(accuracy.get("solution_type"), 0),
            self._format_optional_float(accuracy.get("lat_deg"), 7),
            self._format_optional_float(accuracy.get("lon_deg"), 7),
            self._format_optional_float(accuracy.get("alt_m"), 2),
            self._format_optional_float(accuracy.get("east_error_m"), 2),
            self._format_optional_float(accuracy.get("north_error_m"), 2),
            self._format_optional_float(accuracy.get("up_error_m"), 2),
            self._format_optional_float(accuracy.get("horizontal_error_m"), 2),
            self._format_optional_float(accuracy.get("three_d_error_m"), 2),
            self._format_optional_float(accuracy.get("hdop")),
            self._format_optional_float(accuracy.get("vdop")),
            self._format_optional_float(accuracy.get("pdop")),
            self._format_optional_float(accuracy.get("gdop")),
            receiver_log_mb,
            receiver_log_kbps,
            gnss_snapshot.get("udp_pvt_packets", "--"),
            gnss_snapshot.get("udp_observables_packets", "--"),
            gnss_snapshot.get("udp_tracking_packets", "--"),
            gnss_snapshot.get("udp_parse_errors", "--"),
            self._format_optional_float(gnss_snapshot.get("udp_pvt_age_s")),
            self._format_optional_float(gnss_snapshot.get("udp_observables_age_s")),
            self._format_optional_float(gnss_snapshot.get("udp_tracking_age_s")),
        )

    def _maybe_log_gnss_pvt_transition(self, gnss_snapshot: dict[str, object]) -> None:
        pvt_current = bool(gnss_snapshot.get("pvt_current", False))
        pvt_seen = bool(gnss_snapshot.get("pvt_output_seen", False))
        receiver_time_s = gnss_snapshot.get("receiver_time_s")
        changed = (
            self._last_logged_pvt_current is None
            or pvt_current != self._last_logged_pvt_current
            or pvt_seen != self._last_logged_pvt_seen
        )
        receiver_time_repeated = (
            self._last_logged_receiver_time_s is not None
            and receiver_time_s == self._last_logged_receiver_time_s
        )
        if changed:
            self._handoff_log.info(
                "GNSS PVT freshness transition: pvt_seen=%s pvt_current=%s "
                "previous_seen=%s previous_current=%s receiver_time_s=%s "
                "receiver_time_repeated=%s stale_reason=%s pvt_observations=%s "
                "udp_pvt_packets=%s udp_observables_packets=%s "
                "udp_tracking_packets=%s udp_parse_errors=%s udp_pvt_age_s=%s "
                "udp_observables_age_s=%s udp_tracking_age_s=%s receiver_log_mb=%s "
                "receiver_log_kbps=%s",
                pvt_seen,
                pvt_current,
                self._last_logged_pvt_seen,
                self._last_logged_pvt_current,
                receiver_time_s if receiver_time_s is not None else "--",
                receiver_time_repeated,
                gnss_snapshot.get("stale_reason", "--"),
                gnss_snapshot.get("pvt_observation_count", "--"),
                gnss_snapshot.get("udp_pvt_packets", "--"),
                gnss_snapshot.get("udp_observables_packets", "--"),
                gnss_snapshot.get("udp_tracking_packets", "--"),
                gnss_snapshot.get("udp_parse_errors", "--"),
                self._format_optional_float(gnss_snapshot.get("udp_pvt_age_s")),
                self._format_optional_float(gnss_snapshot.get("udp_observables_age_s")),
                self._format_optional_float(gnss_snapshot.get("udp_tracking_age_s")),
                self._format_mb(gnss_snapshot.get("receiver_log_bytes")),
                self._format_kbps(gnss_snapshot.get("receiver_log_rate_bps")),
            )
        self._last_logged_pvt_current = pvt_current
        self._last_logged_pvt_seen = pvt_seen
        self._last_logged_receiver_time_s = receiver_time_s

    def _maybe_log_gnss_pvt_quality_transition(
        self,
        gnss_snapshot: dict[str, object],
    ) -> None:
        accuracy_obj = gnss_snapshot.get("accuracy", {})
        accuracy = accuracy_obj if isinstance(accuracy_obj, dict) else {}
        used_count = self._used_pvt_count(gnss_snapshot)
        used_prns = self._format_used_pvt_satellites(gnss_snapshot)
        quality = self._gnss_pvt_quality_fields(gnss_snapshot, used_count, accuracy)
        key = (
            quality["status"],
            quality["reason"],
            quality["evidence"],
            self._optional_int(gnss_snapshot.get("pvt_observation_count")),
            used_count,
            self._optional_float(accuracy.get("pdop")),
            str(accuracy.get("fix_type", "") or ""),
        )
        if key == self._last_logged_pvt_quality_key:
            return
        self._last_logged_pvt_quality_key = key
        self._handoff_log.info(
            "GNSS PVT quality transition: status=%s reason=%s evidence=%s "
            "receiver_time_s=%s pvt_seen=%s pvt_current=%s pvt_observations=%s "
            "used_count=%d used_pvt=%s fix_type=%s valid_sats=%s "
            "solution_status=%s solution_type=%s lat_deg=%s lon_deg=%s alt_m=%s "
            "truth_east_error_m=%s truth_north_error_m=%s truth_up_error_m=%s "
            "truth_h_error_m=%s truth_3d_error_m=%s "
            "hdop=%s vdop=%s pdop=%s gdop=%s",
            quality["status"],
            quality["reason"],
            quality["evidence"],
            gnss_snapshot.get("receiver_time_s", "--"),
            bool(gnss_snapshot.get("pvt_output_seen", False)),
            bool(gnss_snapshot.get("pvt_current", False)),
            gnss_snapshot.get("pvt_observation_count", "--"),
            used_count,
            used_prns,
            str(accuracy.get("fix_type", "--") or "--").replace(" ", "_"),
            self._format_optional_float(accuracy.get("valid_sats"), 0),
            self._format_optional_float(accuracy.get("solution_status"), 0),
            self._format_optional_float(accuracy.get("solution_type"), 0),
            self._format_optional_float(accuracy.get("lat_deg"), 7),
            self._format_optional_float(accuracy.get("lon_deg"), 7),
            self._format_optional_float(accuracy.get("alt_m"), 2),
            self._format_optional_float(accuracy.get("east_error_m"), 2),
            self._format_optional_float(accuracy.get("north_error_m"), 2),
            self._format_optional_float(accuracy.get("up_error_m"), 2),
            self._format_optional_float(accuracy.get("horizontal_error_m"), 2),
            self._format_optional_float(accuracy.get("three_d_error_m"), 2),
            self._format_optional_float(accuracy.get("hdop")),
            self._format_optional_float(accuracy.get("vdop")),
            self._format_optional_float(accuracy.get("pdop")),
            self._format_optional_float(accuracy.get("gdop")),
        )

    def _gnss_pvt_quality_fields(
        self,
        gnss_snapshot: dict[str, object],
        used_count: int,
        accuracy: dict[str, object],
    ) -> dict[str, str]:
        pvt_seen = bool(gnss_snapshot.get("pvt_output_seen", False))
        pvt_current = bool(gnss_snapshot.get("pvt_current", False))
        pvt_observations = self._optional_int(gnss_snapshot.get("pvt_observation_count"))
        evidence: list[str] = []
        if pvt_observations is not None and pvt_observations <= PVT_LOW_OBSERVATION_COUNT:
            evidence.append(f"low_observations={pvt_observations}<={PVT_LOW_OBSERVATION_COUNT}")
        if used_count <= PVT_LOW_USED_SATELLITE_COUNT:
            evidence.append(f"low_used={used_count}<={PVT_LOW_USED_SATELLITE_COUNT}")
        pdop = self._optional_float(accuracy.get("pdop"))
        high_pdop = pdop is not None and pdop > PVT_DEGRADED_PDOP_THRESHOLD
        if pdop is not None and pdop > PVT_DEGRADED_PDOP_THRESHOLD:
            evidence.append(f"pdop={pdop:.2f}>{PVT_DEGRADED_PDOP_THRESHOLD:.2f}")
        fix_type = str(accuracy.get("fix_type", "") or "").strip()
        normalized_fix = fix_type.lower()
        evidence_text = ",".join(evidence) or "--"

        if not pvt_seen:
            return {"status": "NO_FIX", "reason": "no_pvt_output", "evidence": evidence_text}
        if not pvt_current:
            stale_reason = str(gnss_snapshot.get("stale_reason", "pvt_stale") or "pvt_stale")
            return {"status": "NO_FIX", "reason": stale_reason, "evidence": evidence_text}
        if not accuracy:
            return {"status": "DEGRADED", "reason": "missing_accuracy", "evidence": evidence_text}
        if "no fix" in normalized_fix or "not available" in normalized_fix:
            return {"status": "NO_FIX", "reason": "fix_type_no_fix", "evidence": evidence_text}
        if high_pdop:
            return {"status": "DEGRADED", "reason": "pdop_gt_gui_threshold", "evidence": evidence_text}
        if not normalized_fix:
            return {"status": "DEGRADED", "reason": "blank_fix_type", "evidence": evidence_text}
        return {"status": "FIX", "reason": "gui_fix_rule", "evidence": evidence_text}

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return float(number) if np.isfinite(number) else None

    def _optional_int(self, value: object) -> int | None:
        number = self._optional_float(value)
        return None if number is None else int(number)

    @staticmethod
    def _format_mb(value: object) -> str:
        if not isinstance(value, (int, float)):
            return "--"
        return f"{float(value) / (1024.0 * 1024.0):.1f}"

    @staticmethod
    def _format_kbps(value: object) -> str:
        if not isinstance(value, (int, float)):
            return "--"
        return f"{float(value) / 1024.0:.1f}"
