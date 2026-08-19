"""Threaded backend runtime for SDR capture, DSP, and GNSS handoff."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

import numpy as np

from antijamming.config import StreamConfig
from antijamming.config.schemas.runtime import VALID_LCMV_METHODS
from antijamming.gnss import (
    GnssSdrBridge,
    SharedU1DesiredVectorMonitor,
    SharedU1PhaseCompensationBank,
    apply_shared_phase_fanout,
)
from antijamming.gnss.sdr_bridge.constants import (
    PVT_DEGRADED_PDOP_THRESHOLD,
    PVT_LOW_OBSERVATION_COUNT,
    PVT_LOW_USED_SATELLITE_COUNT,
)
from antijamming.radio.transport import collect_host_transport_report
from antijamming.logging import (
    RuntimeLogSession,
    finalize_session_logs,
    record_event,
    reset_session_logs,
)
from .latest_queue import put_latest
from .ui_metrics import RuntimeUiMetrics
from .work_items import PhaseResult, PhaseWorkItem
from antijamming.radio.usrp import UsrpRxDevice
from antijamming.dsp.beamforming import (
    apply_beamformer,
    covariance_lcmv_ideal_null_weights,
    covariance_lcmv_vector_null_weights,
    lcmv_model_response,
    uniform_weights,
)
from antijamming.dsp.diagnostics import (
    channel_power_metrics,
    complex_vector_payload,
    component_power_after_beamformer,
    covariance_output_power,
    cross_channel_delay_metrics,
    normalize_complex_vector,
    output_reduction_metrics,
    phase_align_complex_vector,
    power_db,
    ratio_db,
    signal_power_metrics,
    spatial_vector_coherence_metrics,
)
from antijamming.dsp.doa import (
    covariance_eigendecomposition_from_matrix,
    music_spectrum,
    spatial_covariance,
    steering_vector,
)
from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
    operator_bearing_to_internal_angle_deg,
)
from antijamming.dsp.pipeline import (
    compute_doa_metrics,
    compute_gnss_output_vector,
    compute_phase_metrics,
)
from antijamming.rf import compute_rf_budget, manifest_from_config

_RUNTIME_POWER_AGGREGATE_KEYS = frozenset(
    {
        "raw_channel_powers_linear",
        "raw_channel_powers_db",
        "raw_avg_channel_power_linear",
        "raw_sum_channel_power_linear",
        "raw_power_spread_db",
        "cal_channel_powers_linear",
        "cal_channel_powers_db",
        "cal_avg_channel_power_linear",
        "cal_sum_channel_power_linear",
        "cal_power_spread_db",
        "measured_output_reduction_vs_uniform_db",
        "measured_output_reduction_vs_raw_avg_channel_db",
        "measured_output_reduction_vs_raw_sum_channels_db",
        "fifo_output_source",
        "fifo_matches_lcmv_output",
        "fifo_matches_uniform_output",
    }
)
_RUNTIME_POWER_SCALAR_KEYS = frozenset(
    f"{prefix}{suffix}"
    for prefix in (
        *(f"raw_ch{index}" for index in range(4)),
        *(f"cal_ch{index}" for index in range(4)),
        "uniform_output",
        "lcmv_output",
        "fifo_output",
    )
    for suffix in (
        "_power_linear",
        "_rms_complex",
        "_peak_component",
        "_near_full_scale_pct",
        "_mean_i",
        "_mean_q",
        "_i_to_q_power_imbalance_db",
        "_iq_correlation_coefficient",
        "_noncircularity_abs",
    )
)

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
        self._lcmv_log = loggers.get("lcmv", loggers["app"])
        self._analysis_log = loggers.get("analysis", loggers["doa"])
        self._lcmv_pattern_log = loggers.get("lcmv_pattern", self._analysis_log)
        self._spatial_vector_log = loggers.get("spatial_vector", self._analysis_log)
        self._runtime_evidence_log = loggers.get("runtime_evidence", self._analysis_log)
        self._experiment_manifest = manifest_from_config(config)
        self._rf_budget = compute_rf_budget(self._experiment_manifest)
        self._phase_calibration_metadata = self._load_phase_calibration_metadata()
        self._log_session: RuntimeLogSession | None = None
        self._session_event_lock = threading.Lock()
        self._stream_stop_event_recorded = False
        self._operator_rf_state: dict[str, object] = {
            "jammer": "unknown",
            "bladeRF": "unknown",
            "attenuation_db": None,
            "bladeRF_gain_db": None,
        }
        self._running = False
        self._thread: threading.Thread | None = None
        self._angle_scan = config.angle_scan_spec()
        self._scan_angles_deg = self._angle_scan.values()
        self._expected_sources = config.expected_sources
        self._lcmv_test_enabled = bool(config.lcmv_test_enabled)
        self._lcmv_auto_arm_after_pvt = bool(
            getattr(config, "lcmv_auto_arm_after_pvt", True)
        )
        self._lcmv_auto_arm_suppressed_by_operator = False
        self._lcmv_test_null_method = self._normalized_lcmv_null_method(
            getattr(config, "lcmv_test_null_method", "covariance_lcmv_ideal")
        )
        self._config.lcmv_test_null_method = self._lcmv_test_null_method
        self._doa_log_interval_s = max(0.0, float(config.doa_log_interval_s))
        # Log timestamps throttle high-rate DSP state so logs remain useful
        # during long streams.
        self._last_phase_log_ts = 0.0
        self._last_doa_log_ts = 0.0
        self._last_lcmv_log_ts = 0.0
        self._spatial_diag_seq = 0
        self._last_lcmv_heavy_diag_ts: float | None = None
        self._last_full_angle_analysis_ts: float | None = None
        self._last_runtime_evidence_log_ts = 0.0
        self._last_automatic_state_key: tuple[object, ...] | None = None
        self._latest_lcmv_heavy_diag_payload: dict[str, object] = {
            "heavy_diagnostics_interval_s": self._json_float(
                getattr(config, "lcmv_heavy_diagnostics_interval_s", 1.0)
            ),
            "heavy_diagnostics_emitted": False,
            "heavy_diagnostics_skipped_due_to_throttle": False,
            "last_heavy_diagnostics_age_s": None,
        }
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
        self._gnss_handoff_thread: threading.Thread | None = None
        self._gnss_raw_queue: queue.Queue | None = None
        self._shared_u1_phase_monitor: SharedU1DesiredVectorMonitor | None = None
        self._shared_u1_phase_bank: SharedU1PhaseCompensationBank | None = None
        self._last_shared_u1_phase_status_log_ts = 0.0
        self._shared_u1_desired_vectors_cache: dict[str, dict[str, object]] = {}
        self._shared_u1_source_satellites_cache: tuple[int | None, ...] = ()
        self._shared_u1_scalar_fanout_chunks = 0
        self._shared_u1_matrix_fallback_chunks = 0
        self._gnss_fifo_samples_written: int = 0
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
        self._latest_raw_power_metrics: dict[str, object] = {}
        self._latest_cal_power_metrics: dict[str, object] = {}
        self._latest_output_power_metrics: dict[str, object] = {}
        self._latest_spatial_vector_diagnostics: dict[str, object] = {}
        self._healthy_reference_vector: np.ndarray | None = None
        self._healthy_reference_covariance: np.ndarray | None = None
        self._healthy_reference_internal_angle_deg: float | None = None
        self._healthy_reference_display_bearing_deg: float | None = None
        self._healthy_reference_updated_monotonic_s: float | None = None
        self._healthy_reference_confidence: float = 0.0
        self._healthy_reference_update_reason: str = ""
        self._healthy_reference_freeze_reason: str = "not yet assessed"
        self._healthy_reference_raw_power_linear: float | None = None
        self._healthy_reference_cal_power_linear: float | None = None
        preserve_window = max(
            1,
            int(getattr(config, "lcmv_realtime_preserve_window_samples", 40)),
        )
        self._realtime_preserve_angle_history: deque[float] = deque(
            maxlen=preserve_window
        )
        self._realtime_preserve_center_internal_deg: float | None = None
        self._realtime_preserve_center_display_deg: float | None = None
        self._realtime_preserve_circular_std_deg: float | None = None
        self._realtime_preserve_concentration: float = 0.0
        self._realtime_preserve_stable: bool = False
        self._realtime_preserve_last_update_reason: str = "not yet assessed"
        self._realtime_preserve_rejected_streak: int = 0
        self._realtime_preserve_frozen_internal_deg: float | None = None
        self._realtime_preserve_frozen_display_deg: float | None = None
        self._realtime_preserve_frozen_vector: np.ndarray | None = None
        self._realtime_preserve_frozen_covariance: np.ndarray | None = None
        self._realtime_preserve_frozen_raw_power_linear: float | None = None
        self._realtime_preserve_frozen_cal_power_linear: float | None = None
        self._realtime_preserve_frozen_reference_age_s: float | None = None
        self._realtime_preserve_frozen_reference_angle_error_deg: float | None = None
        self._lcmv_jammer_detected_latched: bool = False
        self._latest_source_count_diagnostics: dict[str, object] = {
            "n_sources": max(int(self._expected_sources), 1),
            "peak_count": None,
        }
        self._dsp_chunk_counter = 0

        self._results_lock = threading.Lock()
        self._beamformer_lock = threading.Lock()
        # Latest-result fields are copied into RuntimeUiMetrics. The locks keep
        # UI emission consistent while worker stages update independently.
        initial_weights = uniform_weights(len(config.channels))
        self._latest_beamformer_weights = initial_weights
        self._latest_gnss_effective_weights = self._effective_gnss_weights(initial_weights)
        self._target_beamformer_weights = np.array(initial_weights, copy=True)
        self._shared_measured_u1_protection_weights = np.array(
            initial_weights, copy=True
        )
        self._shared_measured_u1_protection_available = False
        self._latest_shared_u1_phase_logical_weights = np.empty(
            (0, len(config.channels)), dtype=np.complex128
        )
        self._beamformer_transition_start_weights = np.array(initial_weights, copy=True)
        self._beamformer_transition_total_chunks = 0
        self._beamformer_transition_completed_chunks = 0
        self._beamformer_transition_reason = "initial uniform weights"
        self._latest_lcmv_test = self._lcmv_status_snapshot(
            enabled=self._lcmv_test_enabled,
            mode="fallback" if self._lcmv_test_enabled else "off",
            reason="waiting_for_music_peak" if self._lcmv_test_enabled else "",
        )
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
        self._latest_doa_raw_spectrum = np.zeros((config.doa_points,), dtype=np.float64)
        self._latest_doa_deg = float(config.doa_min_deg)
        self._last_phase_ts = 0.0
        self._last_doa_ts = 0.0
        self._last_gnss_snapshot_log_ts = 0.0
        self._last_logged_pvt_current: bool | None = None
        self._last_logged_pvt_seen: bool | None = None
        self._last_logged_receiver_time_s: object = None
        self._last_logged_pvt_quality_key: tuple[object, ...] | None = None
        self._perf_lock = threading.Lock()
        self._perf_stats: dict[str, dict[str, float]] = {}
        self._last_perf_log_ts = 0.0
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
        self._last_lcmv_heavy_diag_ts = None
        self._last_full_angle_analysis_ts = None
        self._last_runtime_evidence_log_ts = 0.0
        self._last_automatic_state_key = None
        self._latest_lcmv_heavy_diag_payload = {
            "heavy_diagnostics_interval_s": self._json_float(
                getattr(self._config, "lcmv_heavy_diagnostics_interval_s", 1.0)
            ),
            "heavy_diagnostics_emitted": False,
            "heavy_diagnostics_skipped_due_to_throttle": False,
            "last_heavy_diagnostics_age_s": None,
        }
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
        self._raw_chunk_count = 0
        self._processed_emit_count = 0
        self._ui_metrics_seq = 0
        self._rx_clipping_suspected_count = 0
        self._reset_rx_signal_health()
        self._phase_queue = queue.Queue(maxsize=self._dsp_stage_queue_maxsize)
        self._doa_queue = queue.Queue(maxsize=self._dsp_stage_queue_maxsize)
        with self._results_lock:
            self._latest_powers = np.zeros((len(self._config.channels),), dtype=np.float64)
            self._latest_raw_power_metrics = {}
            self._latest_cal_power_metrics = {}
            self._latest_output_power_metrics = {}
            self._latest_spatial_vector_diagnostics = {}
            self._healthy_reference_vector = None
            self._healthy_reference_covariance = None
            self._healthy_reference_internal_angle_deg = None
            self._healthy_reference_display_bearing_deg = None
            self._healthy_reference_updated_monotonic_s = None
            self._healthy_reference_confidence = 0.0
            self._healthy_reference_update_reason = ""
            self._healthy_reference_freeze_reason = "run reset"
            self._healthy_reference_raw_power_linear = None
            self._healthy_reference_cal_power_linear = None
            self._reset_realtime_preserve_tracker_locked("run reset")
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
            self._latest_doa_raw_spectrum = np.zeros((self._config.doa_points,), dtype=np.float64)
            self._latest_doa_deg = float(self._config.doa_min_deg)
            self._last_phase_ts = 0.0
            self._last_doa_ts = 0.0
        if self._lcmv_auto_arm_after_pvt:
            # Each run must begin with a genuinely uniform, learnable
            # baseline. The automatic transition is evaluated only after the
            # new run has its own healthy PVT/U1/covariance evidence.
            self._lcmv_test_enabled = False
            self._config.lcmv_test_enabled = False
            self._lcmv_auto_arm_suppressed_by_operator = False
        self._set_beamformer_weights(uniform_weights(len(self._config.channels)))
        self._reset_lcmv_test_for_run()
        try:
            self._emit_status("Preparing session logs")
            self._log_session = reset_session_logs(
                self._config.log_dir,
                self._loggers,
            )
            self._stream_stop_event_recorded = False
            self._log_experiment_startup_context()
            self._record_runtime_event(
                "stream_start",
                source="backend",
                notes="Start command accepted; hardware and GNSS-SDR initialization begins",
            )
            self._loggers["app"].info(
                "Runtime startup: gnss_combiner=uniform_array_sum"
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
            gnss_ok = False
            if self._config.gnss_sdr_enable:
                self._emit_status("Starting GNSS-SDR")
                self._gnss_bridge = GnssSdrBridge(
                    self._config,
                    self._loggers,
                    session_id=(
                        self._log_session.session_id
                        if self._log_session is not None
                        else None
                    ),
                    session_dir=(
                        self._log_session.session_dir
                        if self._log_session is not None
                        else None
                    ),
                )
                gnss_ok = self._gnss_bridge.start()
            else:
                self._emit_status("GNSS-SDR disabled")
                self._loggers["gnss"].info("GNSS-SDR bridge disabled by configuration.")
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
                    float(self._gnss_bridge.input_filter_bandwidth_hz) / 1e6,
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
                self._gnss_fifo_samples_written = 0
                shared_phase_fanout = self._shared_phase_fanout_enabled()
                satellites = tuple(
                    int(value)
                    for value in self._config.gnss_shared_u1_phase_satellites
                )
                shared_phase_source_count = (
                    len(satellites)
                    if satellites
                    else max(1, int(self._config.gnss_1c_channel_count))
                )
                shared_phase_transition_s = float(
                    self._config.gnss_shared_u1_phase_transition_s
                )
                if shared_phase_fanout:
                    self._shared_u1_phase_bank = SharedU1PhaseCompensationBank(
                        satellites=satellites,
                        channel_count=len(self._config.channels),
                        sample_rate_hz=float(self._config.sample_rate),
                        samples_per_chunk=int(self._config.samples_per_chunk),
                        transition_s=shared_phase_transition_s,
                        source_count=shared_phase_source_count,
                        max_weight_norm=self._lcmv_weight_norm_limit(),
                    )
                    self._handoff_log.info(
                        "Shared measured-U1 phase fanout enabled: mapping=%s satellites=%s "
                        "sources=%d transition_s=%.3f min_suppression_db=%.1f "
                        "independent_per_prn_lcmv=false",
                        "pinned" if satellites else "dynamic_channel_to_prn",
                        (
                            ",".join(f"G{value:02d}" for value in satellites)
                            if satellites
                            else "acquired_at_runtime"
                        ),
                        shared_phase_source_count,
                        shared_phase_transition_s,
                        float(
                            self._config.lcmv_min_predicted_jammer_suppression_db
                        ),
                    )
                else:
                    self._shared_u1_phase_bank = None
                self._last_shared_u1_phase_status_log_ts = 0.0
                self._shared_u1_desired_vectors_cache = {}
                self._shared_u1_source_satellites_cache = tuple(
                    satellites
                    if satellites
                    else (None for _ in range(shared_phase_source_count))
                )
                self._shared_u1_scalar_fanout_chunks = 0
                self._shared_u1_matrix_fallback_chunks = 0
                if shared_phase_fanout:
                    if self._log_session is None:
                        raise RuntimeError(
                            "shared-U1 phase monitoring requires an active log session"
                        )
                    self._shared_u1_phase_monitor = SharedU1DesiredVectorMonitor(
                        sample_rate_hz=float(self._config.sample_rate),
                        channel_count=len(self._config.channels),
                        phase_correction_vector=self._config.phase_correction_vector,
                        tracking_snapshot=self._gnss_bridge.snapshot,
                        session_dir=self._log_session.session_dir,
                        session_id=self._log_session.session_id,
                        logger=self._handoff_log,
                        min_quality_measurements=int(
                            self._config.gnss_shared_u1_phase_min_quality_measurements
                        ),
                        satellites=satellites,
                        source_count=shared_phase_source_count,
                    )
                    self._shared_u1_phase_monitor.start()
                else:
                    self._shared_u1_phase_monitor = None
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
                array_spacing_m=self._config.array_spacing_m,
                n_sources=max(int(self._expected_sources), 1),
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
            self._phase_thread.start()
            self._doa_thread.start()

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
            if self._shared_u1_phase_monitor is not None:
                self._shared_u1_phase_monitor.stop()
                self._shared_u1_phase_monitor = None
            self._shared_u1_phase_bank = None
            self._shared_u1_desired_vectors_cache = {}
            self._shared_u1_source_satellites_cache = ()
            if self._config.gnss_sdr_enable:
                self._loggers["transport"].info(
                    "GNSS queue summary: raw_highwater=%d/%d raw_rejections=%d",
                    self._gnss_raw_q_highwater,
                    max(1, int(self._config.gnss_feed_queue_maxsize)),
                    self._gnss_raw_drops,
                )
            for t in [self._phase_thread, self._doa_thread]:
                if t is None:
                    continue
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass
            self._phase_thread = None
            self._doa_thread = None
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
            self._record_stream_stop_event_once(source="backend_finalizer")
            for logger in self._loggers.values():
                for h in list(logger.handlers):
                    try:
                        h.flush()
                    except Exception:
                        pass
            finalize_session_logs(
                self._config.log_dir,
                self._log_session,
                self._loggers,
                stop_reason=self._stop_reason,
                outcome=(
                    "failed" if self._stop_reason.startswith("exception:") else "stopped"
                ),
            )

    def _log_experiment_startup_context(self) -> None:
        manifest = dict(self._experiment_manifest)
        budget = dict(self._rf_budget)
        manifest_payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        budget_payload = json.dumps(budget, sort_keys=True, separators=(",", ":"))
        for logger in (
            self._loggers["app"],
            self._lcmv_log,
            self._loggers["health"],
            self._analysis_log,
        ):
            logger.info("experiment_manifest %s", manifest_payload)
            logger.info("rf_budget %s", budget_payload)
            logger.info(
                "array_geometry_manifest %s",
                json.dumps(
                    self._array_geometry_manifest(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            logger.info(
                "angle_convention_manifest %s",
                json.dumps(
                    self._angle_convention_manifest(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            logger.info(
                "lcmv_runtime_manifest %s",
                json.dumps(
                    self._lcmv_runtime_manifest(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            logger.info(
                "phase_calibration_runtime_manifest %s",
                json.dumps(
                    self._phase_calibration_runtime_manifest(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            logger.info(
                "calibration_manifest %s",
                json.dumps(
                    self._calibration_manifest_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )

        calibration_manifest = self._calibration_manifest_payload()
        if bool(calibration_manifest.get("fallback_used", False)):
            warning = (
                "calibration_manifest warning=fallback_used "
                f"configured={calibration_manifest.get('calibration_correction_mode_configured')} "
                f"applied={calibration_manifest.get('calibration_correction_mode_applied')} "
                f"reason={calibration_manifest.get('fallback_reason')!r}"
            )
            self._loggers["app"].warning("%s", warning)
            self._analysis_log.warning("%s", warning)
            self._loggers["health"].warning("%s", warning)

        rx_chain = str(manifest.get("rx_chain") or "").lower()
        bpf_index = rx_chain.find("bpf")
        lna_index = rx_chain.find("lna")
        if bpf_index < 0 or lna_index < 0 or bpf_index > lna_index:
            warning = (
                "experiment_manifest warning=rx_chain_not_bpf_before_lna "
                f"rx_chain={manifest.get('rx_chain')!r}"
            )
            self._loggers["app"].warning("%s", warning)
            self._lcmv_log.warning("%s", warning)
            self._loggers["health"].warning("%s", warning)

    def _array_geometry_manifest(self) -> dict[str, object]:
        spacing = float(self._config.array_spacing_m)
        half = spacing / 2.0
        return {
            "event": "array_geometry_manifest",
            "channels": list(self._config.channels),
            "rx_antennas_by_channel": list(self._config.rx_antennas_by_channel),
            "layout": "square_ura",
            "channel_positions_m": {
                "ch0_ant1": [-half, -half],
                "ch1_ant2": [-half, +half],
                "ch2_ant3": [+half, +half],
                "ch3_ant4": [+half, -half],
            },
            "axis_x_positive": "ch0_to_ch3",
            "axis_y_positive": "ch0_to_ch1",
            "array_spacing_m": self._json_float(spacing),
            "center_freq_hz": self._json_float(self._config.center_freq_hz),
            "array_design_freq_hz": self._json_float(self._config.array_design_freq_hz),
        }

    def _angle_convention_manifest(self) -> dict[str, object]:
        return {
            "event": "angle_convention_manifest",
            "internal_angle_deg": "+x axis at ch0_to_ch3, increasing counterclockwise",
            "display_bearing_deg": "top_zero_clockwise",
            "display_bearing_formula": "(90 - internal_angle_deg) % 360",
            "internal_0_display_bearing_deg": 90.0,
            "internal_90_display_bearing_deg": 0.0,
            "internal_180_display_bearing_deg": 270.0,
            "internal_270_display_bearing_deg": 180.0,
        }

    def _lcmv_runtime_manifest(self) -> dict[str, object]:
        return {
            "event": "lcmv_runtime_manifest",
            "lcmv_test_enabled": bool(self._lcmv_test_enabled),
            "lcmv_auto_arm_after_pvt": bool(self._lcmv_auto_arm_after_pvt),
            "lcmv_test_null_method": self._lcmv_test_null_method,
            "lcmv_test_null_method_allowed": sorted(VALID_LCMV_METHODS),
            "lcmv_preserve_constraint_mode": str(
                getattr(self._config, "lcmv_preserve_constraint_mode", "uniform")
            ),
            "lcmv_target_selection_mode": str(
                getattr(self._config, "lcmv_target_selection_mode", "strongest_music_peak")
            ),
            "lcmv_realtime_preserve_window_samples": int(
                getattr(self._config, "lcmv_realtime_preserve_window_samples", 40)
            ),
            "lcmv_realtime_preserve_min_samples": int(
                getattr(self._config, "lcmv_realtime_preserve_min_samples", 20)
            ),
            "lcmv_realtime_preserve_max_circular_std_deg": self._json_float(
                getattr(
                    self._config,
                    "lcmv_realtime_preserve_max_circular_std_deg",
                    15.0,
                )
            ),
            "lcmv_realtime_preserve_max_step_deg": self._json_float(
                getattr(self._config, "lcmv_realtime_preserve_max_step_deg", 30.0)
            ),
            "lcmv_realtime_preserve_guard_deg": self._json_float(
                getattr(self._config, "lcmv_realtime_preserve_guard_deg", 20.0)
            ),
            "lcmv_realtime_preserve_max_reference_age_s": self._json_float(
                getattr(
                    self._config,
                    "lcmv_realtime_preserve_max_reference_age_s",
                    2.0,
                )
            ),
            "lcmv_jammer_activation_min_input_power_jump_db": self._json_float(
                getattr(
                    self._config,
                    "lcmv_jammer_activation_min_input_power_jump_db",
                    3.0,
                )
            ),
            "lcmv_jammer_activation_min_generalized_gain_db": self._json_float(
                getattr(
                    self._config,
                    "lcmv_jammer_activation_min_generalized_gain_db",
                    6.0,
                )
            ),
            "lcmv_weight_transition_s": self._json_float(
                getattr(self._config, "lcmv_weight_transition_s", 1.0)
            ),
            "lcmv_jammer_activation_angle_only_forbidden": True,
            "lcmv_jammer_activation_latches_until_disable": True,
            "lcmv_test_max_weight_norm": self._json_float(
                self._config.lcmv_test_max_weight_norm
            ),
            "lcmv_test_condition_number_limit": self._json_float(
                self._config.lcmv_test_condition_number_limit
            ),
            "lcmv_candidate_methods_enabled": bool(
                getattr(self._config, "lcmv_candidate_methods_enabled", True)
            ),
            "lcmv_covariance_diagonal_loading_rel": self._json_float(
                getattr(self._config, "lcmv_covariance_diagonal_loading_rel", 0.001)
            ),
            "lcmv_covariance_diagonal_loading_abs": self._json_float(
                getattr(self._config, "lcmv_covariance_diagonal_loading_abs", 0.0)
            ),
            "lcmv_max_weight_norm": self._json_float(
                getattr(self._config, "lcmv_max_weight_norm", self._config.lcmv_test_max_weight_norm)
            ),
            "lcmv_max_white_noise_gain_db": self._json_float(
                getattr(self._config, "lcmv_max_white_noise_gain_db", 15.0)
            ),
            "lcmv_min_predicted_jammer_suppression_db": self._json_float(
                getattr(self._config, "lcmv_min_predicted_jammer_suppression_db", 3.0)
            ),
            "lcmv_heavy_diagnostics_interval_s": self._json_float(
                getattr(self._config, "lcmv_heavy_diagnostics_interval_s", 1.0)
            ),
            "default_active_method": self._lcmv_test_null_method,
            "covariance_lcmv_measured_u1_is_diagnostic_by_default": (
                self._lcmv_test_null_method != "covariance_lcmv_measured_u1"
            ),
        }

    def _phase_calibration_runtime_manifest(self) -> dict[str, object]:
        correction = self._config.phase_correction_vector
        return {
            "event": "phase_calibration_runtime_manifest",
            "phase_calibration_file": (
                str(self._config.phase_calibration_file)
                if self._config.phase_calibration_file is not None
                else None
            ),
            "phase_correction_available": correction is not None,
            "phase_correction_vector": complex_vector_payload(
                correction if correction is not None else []
            ),
            "calibration_reference_channel": self._calibration_reference_channel(),
            "calibration_gain_db": self._calibration_gain_db(),
            "runtime_usrp_gain_db": self._json_float(self._config.gain_db),
            "metadata": self._json_ready_mapping(self._phase_calibration_metadata),
        }

    def _calibration_manifest_payload(self) -> dict[str, object]:
        metadata = self._calibration_metadata_payload()
        return {
            "event": "calibration_manifest",
            **metadata,
        }

    def _calibration_metadata_payload(self) -> dict[str, object]:
        metadata = (
            dict(self._config.calibration_correction_metadata)
            if isinstance(self._config.calibration_correction_metadata, dict)
            else {}
        )
        expected_channels = len(self._config.channels)
        correction = np.asarray(
            self._config.phase_correction_vector
            if self._config.phase_correction_vector is not None
            else np.ones((expected_channels,), dtype=np.complex128),
            dtype=np.complex128,
        ).reshape(-1)
        magnitudes = np.abs(correction)
        phases_deg = np.degrees(np.angle(correction))
        power_gain_db = 20.0 * np.log10(
            np.maximum(magnitudes, np.finfo(np.float64).tiny)
        )
        defaults: dict[str, object] = {
            "calibration_file_path": (
                str(self._config.phase_calibration_file)
                if self._config.phase_calibration_file is not None
                else None
            ),
            "calibration_correction_mode_configured": str(
                getattr(self._config, "calibration_correction_mode", "complex_gain")
            ),
            "calibration_correction_mode_applied": str(
                getattr(self._config, "calibration_correction_mode", "complex_gain")
            )
            if self._config.phase_correction_vector is not None
            else "none",
            "complex_gain_vector_available": (
                "complex_gain_phase_correction_vector" in self._phase_calibration_metadata
            ),
            "phase_only_vector_available": (
                "correction_vector" in self._phase_calibration_metadata
                or "phase_offsets_deg" in self._phase_calibration_metadata
            ),
            "fallback_used": self._config.phase_correction_vector is None,
            "fallback_reason": (
                "" if self._config.phase_correction_vector is not None else "no applied correction vector"
            ),
            "reference_channel": self._calibration_reference_channel(),
            "correction_vector_length": int(correction.size),
            "expected_channel_count": expected_channels,
        }
        payload = {**defaults, **metadata}
        payload["applied_correction_vector_real"] = [
            self._json_float(np.real(value)) for value in correction
        ]
        payload["applied_correction_vector_imag"] = [
            self._json_float(np.imag(value)) for value in correction
        ]
        payload["applied_correction_magnitudes"] = self._json_float_list(magnitudes)
        payload["applied_correction_phases_deg"] = self._json_float_list(phases_deg)
        payload["applied_correction_power_gain_db"] = self._json_float_list(power_gain_db)
        return payload

    def _calibration_context_payload(self) -> dict[str, object]:
        metadata = self._calibration_metadata_payload()
        return {
            "calibration_correction_mode_applied": metadata.get(
                "calibration_correction_mode_applied"
            ),
            "applied_correction_magnitudes": metadata.get(
                "applied_correction_magnitudes", []
            ),
            "applied_correction_phases_deg": metadata.get(
                "applied_correction_phases_deg", []
            ),
            "applied_correction_power_gain_db": metadata.get(
                "applied_correction_power_gain_db", []
            ),
        }

    def _load_phase_calibration_metadata(self) -> dict[str, object]:
        path = self._config.phase_calibration_file
        if path is None:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def _phase_diagnostics_payload(self, phase_metrics: dict[str, object]) -> dict[str, object]:
        raw_buffer = np.asarray(phase_metrics.get("raw_buffer"), dtype=np.complex128)
        calibrated_buffer = np.asarray(
            phase_metrics.get("calibrated_buffer"),
            dtype=np.complex128,
        )
        component_threshold = float(self._config.rx_clipping_component_threshold)
        raw_metrics = channel_power_metrics(
            raw_buffer,
            prefix="raw",
            component_threshold=component_threshold,
        )
        cal_metrics = channel_power_metrics(
            calibrated_buffer,
            prefix="cal",
            component_threshold=component_threshold,
        )
        correction_vector = self._config.phase_correction_vector
        correction_payload = complex_vector_payload(
            correction_vector if correction_vector is not None else []
        )
        calibration_context = self._calibration_context_payload()
        gain_effect_db = list(
            calibration_context.get("applied_correction_power_gain_db", [])
            if isinstance(calibration_context.get("applied_correction_power_gain_db"), list)
            else []
        )
        calibration_gain_db = self._calibration_gain_db()
        runtime_gain_db = float(self._config.gain_db)
        calibration_gain_mismatch_db = (
            runtime_gain_db - calibration_gain_db
            if calibration_gain_db is not None
            else None
        )
        payload: dict[str, object] = {
            "event": "phase_channel_diagnostics",
            "phase_offsets_raw_deg": self._json_float_list(
                phase_metrics.get("phase_offsets_raw_deg", [])
            ),
            "phase_offsets_calibrated_deg": self._json_float_list(
                phase_metrics.get("phase_offsets_calibrated_deg", [])
            ),
            "phase_estimator": phase_metrics.get("phase_estimator", "--"),
            "phase_monitor_estimated_offset_hz": self._json_float(
                phase_metrics.get("phase_monitor_estimated_offset_hz")
            ),
            "calibration_file": (
                str(self._config.phase_calibration_file)
                if self._config.phase_calibration_file is not None
                else None
            ),
            "calibration_reference_channel": self._calibration_reference_channel(),
            "calibration_gain_db": calibration_gain_db,
            "runtime_usrp_gain_db": runtime_gain_db,
            "calibration_gain_mismatch_db": calibration_gain_mismatch_db,
            "correction_vector": correction_payload,
        }
        payload.update(calibration_context)
        payload["calibration_gain_effect_db"] = gain_effect_db
        for channel, gain_db in enumerate(gain_effect_db):
            payload[f"calibration_gain_effect_ch{channel}_db"] = gain_db
        payload.update(raw_metrics)
        payload.update(cal_metrics)
        payload.update(
            cross_channel_delay_metrics(
                calibrated_buffer,
                sample_rate_hz=float(self._config.sample_rate),
                reference_channel=0,
            )
        )
        return payload

    def _calibration_gain_db(self) -> float | None:
        for key in (
            "gain_db",
            "usrp_rx_gain_db",
            "runtime_usrp_gain_db",
            "calibration_gain_db",
        ):
            value = self._phase_calibration_metadata.get(key)
            number = self._finite_metric_float(value)
            if number is not None:
                return number
        number = self._finite_metric_float(
            self._experiment_manifest.get("calibration_file_expected_gain_db")
        )
        return number

    def _calibration_reference_channel(self) -> object:
        for key in ("reference_channel", "calibration_reference_channel", "ref_channel"):
            if key in self._phase_calibration_metadata:
                return self._phase_calibration_metadata.get(key)
        return None

    # -------------------------------------------------------------------------
    # Runtime Control Mutators
    # -------------------------------------------------------------------------

    def stop(self, reason: str = "normal stop") -> None:
        self._set_stop_reason(reason)
        # Capture the last live GNSS/LCMV/power snapshot before the bridge and
        # device are detached. The finalizer calls the same guarded helper for
        # exception paths that never enter this method.
        self._record_stream_stop_event_once(source="backend_control")
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

    def set_expected_sources(self, count: int) -> None:
        max_sources = max(len(self._config.channels) - 1, 1)
        normalized = min(max(1, int(count)), max_sources)
        self._expected_sources = normalized
        self._config.expected_sources = normalized
        self._loggers["app"].info("Runtime action: expected_sources=%d", normalized)
        self._record_runtime_event(
            "expected_sources_changed",
            source="backend_control",
            notes=f"MUSIC expected sources set to {normalized}",
        )
        self._emit_status(f"MUSIC sources: {normalized}")

    def mark_rf_event(
        self,
        event: str,
        *,
        attenuation_db: float | None = None,
        bladeRF_gain_db: float | None = None,
        notes: str = "",
        source: str = "operator",
    ) -> dict[str, object]:
        """Record operator-confirmed physical RF state with runtime context."""

        normalized = str(event).strip()
        if normalized == "jammer_on":
            self._operator_rf_state["jammer"] = "on"
        elif normalized == "jammer_off":
            self._operator_rf_state["jammer"] = "off"
        elif normalized == "bladeRF_on":
            self._operator_rf_state["bladeRF"] = "on"
        elif normalized == "bladeRF_off":
            self._operator_rf_state["bladeRF"] = "off"
        if attenuation_db is not None:
            self._operator_rf_state["attenuation_db"] = float(attenuation_db)
        if bladeRF_gain_db is not None:
            self._operator_rf_state["bladeRF_gain_db"] = float(bladeRF_gain_db)
        payload = self._record_runtime_event(
            normalized,
            source=source,
            attenuation_db=attenuation_db,
            bladeRF_gain_db=bladeRF_gain_db,
            notes=notes,
        )
        self._loggers["app"].info(
            "Operator RF marker: event=%s jammer=%s bladeRF=%s attenuation_db=%s "
            "bladeRF_gain_db=%s source=%s notes=%s",
            normalized,
            self._operator_rf_state.get("jammer"),
            self._operator_rf_state.get("bladeRF"),
            self._operator_rf_state.get("attenuation_db"),
            self._operator_rf_state.get("bladeRF_gain_db"),
            source,
            notes or "--",
        )
        return payload

    def _event_context_snapshot(
        self,
        gnss_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if gnss_snapshot is not None:
            gnss = dict(gnss_snapshot)
        else:
            bridge = self._gnss_bridge
            try:
                gnss = bridge.snapshot() if bridge is not None else {}
            except Exception as exc:
                gnss = {"snapshot_error": str(exc)}
        with self._results_lock:
            spatial = dict(self._latest_spatial_vector_diagnostics)
            raw_power = dict(self._latest_raw_power_metrics)
            calibrated_power = dict(self._latest_cal_power_metrics)
            output_power = dict(self._latest_output_power_metrics)
            phase_offsets_raw = np.array(self._latest_phase_offsets_raw, copy=True)
            phase_offsets_calibrated = np.array(
                self._latest_phase_offsets_calibrated,
                copy=True,
            )
            source_count = dict(self._latest_source_count_diagnostics)
            doa_internal = self._json_float(self._latest_doa_deg)
        lcmv = self._lcmv_status_copy()
        beamformer = self._beamformer_evidence_payload()
        inference = self._automatic_rf_inference(spatial)
        context = {
            "operator_rf_state": dict(self._operator_rf_state),
            "configured_receiver": {
                "usrp_rx_gain_db": self._json_float(self._config.gain_db),
                "center_freq_hz": self._json_float(self._config.center_freq_hz),
                "sample_rate_sps": self._json_float(self._config.sample_rate),
                "rx_bandwidth_hz": self._json_float(
                    self._config.usrp_rx_bandwidth_hz
                ),
                "channels": [int(channel) for channel in self._config.channels],
            },
            "stream_running": bool(self._running),
            "stream_elapsed_s": self._json_float(
                time.monotonic() - self._stream_start_ts
                if self._stream_start_ts > 0.0
                else None
            ),
            "raw_chunk_count": int(self._raw_chunk_count),
            "physical_state_inference": inference,
            "lcmv_enabled": bool(self._lcmv_test_enabled),
            "lcmv_mode": lcmv.get("mode"),
            "active_lcmv_method": lcmv.get("active_lcmv_method"),
            "active_lcmv_weights_source": lcmv.get("active_lcmv_weights_source"),
            "lcmv_fallback_reason": lcmv.get("fallback_reason"),
            "beamformer": beamformer,
            "array_alignment": {
                "phase_offsets_raw_deg": self._json_float_list(phase_offsets_raw),
                "phase_offsets_calibrated_deg": self._json_float_list(
                    phase_offsets_calibrated
                ),
                "calibration_correction_mode": str(
                    self._config.calibration_correction_mode
                ),
            },
            "source_count": source_count,
            "doa_internal_deg": doa_internal,
            "doa_display_deg": self._json_float(
                self._internal_angle_to_display(doa_internal)
                if doa_internal is not None
                else None
            ),
            "spatial": {
                key: spatial.get(key)
                for key in (
                    "sequence",
                    "run_state_label",
                    "run_state_reason",
                    "run_state_confidence",
                    "jammer_confidence_score",
                    "healthy_confidence_score",
                    "music_internal_angle_deg",
                    "music_display_bearing_deg",
                    "lcmv_jammer_detected_latched",
                    "lcmv_jammer_activation_armed",
                    "lcmv_jammer_activation_evidence_now",
                    "lcmv_jammer_activation_input_power_jump_db",
                    "lcmv_jammer_activation_generalized_gain_db",
                    "realtime_bladerf_angle_frozen",
                    "realtime_bladerf_tracker_stable",
                    "realtime_bladerf_frozen_display_deg",
                    "jammer_only_suppression_estimate_available",
                    "jammer_only_suppression_db",
                    "jammer_only_power_before_uniform_linear",
                    "jammer_only_power_after_applied_linear",
                )
            },
            "powers": self._runtime_power_evidence(
                raw_power,
                calibrated_power,
                output_power,
            ),
            "gnss": {
                key: gnss.get(key)
                for key in (
                    "receiver_time_s",
                    "pvt_output_seen",
                    "pvt_current",
                    "pvt_observation_count",
                    "tracking_count",
                    "tracking_satellites",
                    "stable_tracking_satellites",
                    "used_in_fix_count",
                    "used_in_fix_satellites",
                    "avg_tracking_cno_db_hz",
                    "tracking_state_archive_path",
                    "tracking_state_archive_rows",
                    "udp_tracking_age_s",
                    "stale_reason",
                )
            }
            | {
                "accuracy": self._runtime_accuracy_evidence(
                    gnss.get("accuracy", {})
                )
            },
        }
        return self._json_ready_mapping(context)

    @staticmethod
    def _runtime_power_evidence(
        raw_power: dict[str, object],
        calibrated_power: dict[str, object],
        output_power: dict[str, object],
    ) -> dict[str, object]:
        """Keep synchronized IQ evidence without copying every derived alias."""

        metrics = {**raw_power, **calibrated_power, **output_power}
        return {
            key: value
            for key, value in metrics.items()
            if key in _RUNTIME_POWER_AGGREGATE_KEYS
            or key in _RUNTIME_POWER_SCALAR_KEYS
        }

    @staticmethod
    def _runtime_accuracy_evidence(accuracy: object) -> dict[str, object]:
        """Retain PVT/CEP facts while omitting the duplicated protobuf dump."""

        if not isinstance(accuracy, dict):
            return {}
        keys = (
            "fix_count",
            "accuracy_scope",
            "cep_sample_count",
            "cep_scope",
            "cep_ready",
            "fix_type",
            "lat_deg",
            "lon_deg",
            "alt_m",
            "truth_available",
            "hdop",
            "vdop",
            "pdop",
            "gdop",
            "east_error_m",
            "north_error_m",
            "up_error_m",
            "horizontal_error_m",
            "three_d_error_m",
            "cep50_m",
            "cep95_m",
            "accuracy_source",
            "valid_sats",
            "solution_status",
            "solution_type",
        )
        return {key: accuracy.get(key) for key in keys if key in accuracy}

    def _automatic_rf_inference(
        self,
        spatial: dict[str, object],
    ) -> dict[str, object]:
        """Describe receiver evidence without claiming physical switch truth."""

        run_state = str(spatial.get("run_state_label", "unknown"))
        jammer_latched = bool(spatial.get("lcmv_jammer_detected_latched", False))
        activation_now = bool(
            spatial.get("lcmv_jammer_activation_evidence_now", False)
        )
        if activation_now:
            jammer_state = "likely_present"
            jammer_basis = "LCMV power-plus-generalized-covariance activation gate"
        elif run_state == "jammer_like_event":
            jammer_state = "jammer_like_change"
            jammer_basis = "one-run covariance/power/GNSS inference"
        elif jammer_latched:
            jammer_state = "no_current_evidence_latch_retained"
            jammer_basis = (
                "the activation latch records an earlier event, but the current "
                "power-plus-covariance gates no longer both pass"
            )
        elif run_state == "healthy_baseline":
            jammer_state = "not_detected"
            jammer_basis = "healthy uniform-combiner baseline"
        else:
            jammer_state = "unknown"
            jammer_basis = "insufficient or mixed receiver evidence"

        desired_signature = bool(
            spatial.get("realtime_bladerf_tracker_stable", False)
            or spatial.get("realtime_bladerf_angle_frozen", False)
            or spatial.get("healthy_reference_available", False)
        )
        return {
            "jammer_inferred_state": jammer_state,
            "jammer_inference_basis": jammer_basis,
            "bladeRF_inferred_state": (
                "desired_spatial_signature_available"
                if desired_signature
                else "unknown"
            ),
            "physical_switch_truth_available": False,
            "warning": (
                "Receiver inference cannot uniquely identify an external emitter or "
                "prove a physical jammer/bladeRF switch position"
            ),
        }

    def _maybe_log_runtime_evidence(
        self,
        gnss_snapshot: dict[str, object],
    ) -> None:
        """Persist synchronized automatic evidence at the GUI/DSP cadence."""

        now = time.monotonic()
        interval_s = max(0.1, float(self._config.ui_update_interval_s))
        if (now - self._last_runtime_evidence_log_ts) < interval_s:
            return
        self._last_runtime_evidence_log_ts = now
        wall_time_ns = time.time_ns()
        session = self._log_session
        context = self._event_context_snapshot(gnss_snapshot)
        common = {
            "schema_version": 1,
            "timestamp_utc": datetime.fromtimestamp(
                wall_time_ns / 1e9,
                timezone.utc,
            ).isoformat(),
            "timestamp_local": datetime.fromtimestamp(
                wall_time_ns / 1e9,
            ).astimezone().isoformat(),
            "wall_time_unix_ns": wall_time_ns,
            "monotonic_ns": time.monotonic_ns(),
            "session_id": session.session_id if session is not None else None,
            "session_elapsed_s": session.elapsed_s() if session is not None else None,
        }
        snapshot = {
            **common,
            "event": "automatic_runtime_evidence_snapshot",
            "context": context,
        }
        self._runtime_evidence_log.info(
            "%s",
            json.dumps(snapshot, separators=(",", ":"), allow_nan=False),
        )

        inference = context.get("physical_state_inference", {})
        beamformer = context.get("beamformer", {})
        state_key = (
            context.get("lcmv_enabled"),
            context.get("lcmv_mode"),
            context.get("active_lcmv_method"),
            inference.get("jammer_inferred_state")
            if isinstance(inference, dict)
            else None,
            context.get("spatial", {}).get("run_state_label")
            if isinstance(context.get("spatial"), dict)
            else None,
            beamformer.get("weight_transition_active")
            if isinstance(beamformer, dict)
            else None,
            beamformer.get("weight_transition_reason")
            if isinstance(beamformer, dict)
            else None,
        )
        if state_key != self._last_automatic_state_key:
            transition = {
                **common,
                "event": "automatic_runtime_state_transition",
                "previous_state_key": self._last_automatic_state_key,
                "current_state_key": state_key,
                "context": context,
            }
            self._runtime_evidence_log.info(
                "%s",
                json.dumps(transition, separators=(",", ":"), allow_nan=False),
            )
            self._last_automatic_state_key = state_key

    def _record_runtime_event(
        self,
        event: str,
        *,
        source: str,
        attenuation_db: float | None = None,
        bladeRF_gain_db: float | None = None,
        notes: str = "",
    ) -> dict[str, object]:
        session = self._log_session
        return record_event(
            self._config.log_dir,
            event,
            source=source,
            attenuation_db=attenuation_db,
            bladeRF_gain_db=bladeRF_gain_db,
            notes=notes,
            context=self._event_context_snapshot(),
            session_id=session.session_id if session is not None else None,
            session_elapsed_s=session.elapsed_s() if session is not None else None,
            # Unit fixtures and pre-start backend objects have no owned session.
            # They may exercise controls, but must not append synthetic events
            # into an unrelated active hardware run.
            append_current_session=session is not None,
        )

    def _record_stream_stop_event_once(self, *, source: str) -> None:
        with self._session_event_lock:
            if self._stream_stop_event_recorded or self._log_session is None:
                return
            self._stream_stop_event_recorded = True
        self._record_runtime_event(
            "stream_stop",
            source=source,
            notes=self._stop_reason,
        )

    def _reset_realtime_preserve_tracker_locked(self, reason: str) -> None:
        self._realtime_preserve_angle_history.clear()
        self._realtime_preserve_center_internal_deg = None
        self._realtime_preserve_center_display_deg = None
        self._realtime_preserve_circular_std_deg = None
        self._realtime_preserve_concentration = 0.0
        self._realtime_preserve_stable = False
        self._realtime_preserve_last_update_reason = str(reason)
        self._realtime_preserve_rejected_streak = 0
        self._realtime_preserve_frozen_internal_deg = None
        self._realtime_preserve_frozen_display_deg = None
        self._realtime_preserve_frozen_vector = None
        self._realtime_preserve_frozen_covariance = None
        self._realtime_preserve_frozen_raw_power_linear = None
        self._realtime_preserve_frozen_cal_power_linear = None
        self._realtime_preserve_frozen_reference_age_s = None
        self._realtime_preserve_frozen_reference_angle_error_deg = None
        self._lcmv_jammer_detected_latched = False

    def _realtime_preserve_tracker_payload_locked(self) -> dict[str, object]:
        return {
            "realtime_bladerf_tracker_sample_count": len(
                self._realtime_preserve_angle_history
            ),
            "realtime_bladerf_tracker_window_samples": int(
                self._realtime_preserve_angle_history.maxlen or 0
            ),
            "realtime_bladerf_tracker_min_samples": int(
                getattr(self._config, "lcmv_realtime_preserve_min_samples", 20)
            ),
            "realtime_bladerf_tracker_center_internal_deg": self._json_float(
                self._realtime_preserve_center_internal_deg
            ),
            "realtime_bladerf_tracker_center_display_deg": self._json_float(
                self._realtime_preserve_center_display_deg
            ),
            "realtime_bladerf_tracker_circular_std_deg": self._json_float(
                self._realtime_preserve_circular_std_deg
            ),
            "realtime_bladerf_tracker_max_circular_std_deg": self._json_float(
                getattr(
                    self._config,
                    "lcmv_realtime_preserve_max_circular_std_deg",
                    15.0,
                )
            ),
            "realtime_bladerf_tracker_concentration": self._json_float(
                self._realtime_preserve_concentration
            ),
            "realtime_bladerf_tracker_stable": bool(
                self._realtime_preserve_stable
            ),
            "realtime_bladerf_tracker_rejected_streak": int(
                self._realtime_preserve_rejected_streak
            ),
            "realtime_bladerf_tracker_reason": self._realtime_preserve_last_update_reason,
            "realtime_bladerf_frozen_internal_deg": self._json_float(
                self._realtime_preserve_frozen_internal_deg
            ),
            "realtime_bladerf_frozen_display_deg": self._json_float(
                self._realtime_preserve_frozen_display_deg
            ),
            "realtime_bladerf_angle_frozen": bool(
                self._realtime_preserve_frozen_internal_deg is not None
                and self._realtime_preserve_frozen_vector is not None
            ),
            "realtime_bladerf_frozen_measured_u1": complex_vector_payload(
                self._realtime_preserve_frozen_vector
                if self._realtime_preserve_frozen_vector is not None
                else np.zeros((0,), dtype=np.complex128)
            ),
            "realtime_bladerf_frozen_reference_age_s": self._json_float(
                self._realtime_preserve_frozen_reference_age_s
            ),
            "realtime_bladerf_frozen_reference_angle_error_deg": self._json_float(
                self._realtime_preserve_frozen_reference_angle_error_deg
            ),
            "lcmv_jammer_detected_latched": bool(
                self._lcmv_jammer_detected_latched
            ),
        }

    def _realtime_preserve_tracker_payload(self) -> dict[str, object]:
        with self._results_lock:
            return self._realtime_preserve_tracker_payload_locked()

    def _update_realtime_bladerf_angle_tracker(
        self,
        doa_metrics: dict[str, object],
        *,
        primary_internal_deg: float,
    ) -> dict[str, object]:
        """Track a live jammer-off bladeRF bearing cluster without authored ranges."""

        primary = self._finite_metric_float(primary_internal_deg)
        with self._results_lock:
            if self._lcmv_test_enabled:
                self._realtime_preserve_last_update_reason = (
                    "tracker frozen while LCMV is enabled"
                )
                return self._realtime_preserve_tracker_payload_locked()
            if primary is None:
                self._realtime_preserve_last_update_reason = "primary MUSIC angle unavailable"
                return self._realtime_preserve_tracker_payload_locked()

            candidates: list[float] = []
            peaks = doa_metrics.get("doa_peaks", [])
            if isinstance(peaks, list):
                for peak in peaks:
                    if not isinstance(peak, dict):
                        continue
                    angle = self._finite_metric_float(peak.get("angle_deg"))
                    if angle is not None:
                        candidates.append(float(angle) % 360.0)
            if not candidates:
                candidates.append(float(primary) % 360.0)

            reference = self._realtime_preserve_center_internal_deg
            selected = float(primary) % 360.0
            step_deg = None
            if reference is not None:
                selected = min(
                    candidates,
                    key=lambda angle: self._angle_distance_deg(angle, reference),
                )
                step_deg = self._angle_distance_deg(selected, reference)
                max_step = max(
                    0.0,
                    float(
                        getattr(
                            self._config,
                            "lcmv_realtime_preserve_max_step_deg",
                            30.0,
                        )
                    ),
                )
                if step_deg > max_step:
                    self._realtime_preserve_rejected_streak += 1
                    self._realtime_preserve_last_update_reason = (
                        f"rejected MUSIC step {step_deg:.2f} deg above {max_step:.2f} deg"
                    )
                    if self._realtime_preserve_rejected_streak < 5:
                        return self._realtime_preserve_tracker_payload_locked()
                    self._realtime_preserve_angle_history.clear()
                    self._realtime_preserve_rejected_streak = 0
                    selected = float(primary) % 360.0

            self._realtime_preserve_rejected_streak = 0
            self._realtime_preserve_angle_history.append(selected)
            angles = np.asarray(
                self._realtime_preserve_angle_history,
                dtype=np.float64,
            )
            unit = np.exp(1j * np.deg2rad(angles))
            mean_unit = complex(np.mean(unit))
            concentration = float(abs(mean_unit))
            center = float(np.rad2deg(np.angle(mean_unit)) % 360.0)
            concentration_for_log = min(max(concentration, 1e-12), 1.0)
            circular_std = float(
                np.rad2deg(np.sqrt(max(0.0, -2.0 * np.log(concentration_for_log))))
            )
            min_samples = max(
                1,
                int(getattr(self._config, "lcmv_realtime_preserve_min_samples", 20)),
            )
            max_std = max(
                0.0,
                float(
                    getattr(
                        self._config,
                        "lcmv_realtime_preserve_max_circular_std_deg",
                        15.0,
                    )
                ),
            )
            stable = len(angles) >= min_samples and circular_std <= max_std
            self._realtime_preserve_center_internal_deg = center
            self._realtime_preserve_center_display_deg = (
                internal_angle_to_operator_bearing_deg(center)
            )
            self._realtime_preserve_circular_std_deg = circular_std
            self._realtime_preserve_concentration = concentration
            self._realtime_preserve_stable = stable
            self._realtime_preserve_last_update_reason = (
                "stable live bladeRF angle cluster"
                if stable
                else (
                    f"collecting live angle cluster: n={len(angles)}/{min_samples} "
                    f"circular_std={circular_std:.2f}/{max_std:.2f} deg"
                )
            )
            return self._realtime_preserve_tracker_payload_locked()

    def set_lcmv_test_enabled(
        self,
        enabled: bool,
        *,
        source: str = "operator",
    ) -> None:
        active = bool(enabled)
        normalized_source = str(source).strip().lower() or "operator"
        if normalized_source == "operator":
            self._lcmv_auto_arm_suppressed_by_operator = not active
        self._set_shared_measured_u1_protection_weights(
            uniform_weights(len(self._config.channels)), available=False
        )
        preserve_mode = str(
            getattr(self._config, "lcmv_preserve_constraint_mode", "uniform")
        ).strip().lower()
        frozen_reason = ""
        with self._results_lock:
            if active and preserve_mode == "realtime_bladerf_measured_u1":
                center = self._realtime_preserve_center_internal_deg
                healthy_vector = (
                    normalize_complex_vector(self._healthy_reference_vector)
                    if self._healthy_reference_vector is not None
                    else np.zeros((0,), dtype=np.complex128)
                )
                healthy_covariance = (
                    np.asarray(self._healthy_reference_covariance, dtype=np.complex128)
                    if self._healthy_reference_covariance is not None
                    else np.zeros((0, 0), dtype=np.complex128)
                )
                reference_age_s = (
                    time.monotonic() - self._healthy_reference_updated_monotonic_s
                    if self._healthy_reference_updated_monotonic_s is not None
                    else None
                )
                reference_angle_error_deg = (
                    self._angle_distance_deg(
                        center,
                        self._healthy_reference_internal_angle_deg,
                    )
                    if center is not None
                    and self._healthy_reference_internal_angle_deg is not None
                    else None
                )
                max_reference_age_s = max(
                    0.0,
                    float(
                        getattr(
                            self._config,
                            "lcmv_realtime_preserve_max_reference_age_s",
                            2.0,
                        )
                    ),
                )
                max_reference_angle_error_deg = max(
                    0.0,
                    float(
                        getattr(
                            self._config,
                            "lcmv_realtime_preserve_guard_deg",
                            20.0,
                        )
                    ),
                )
                reference_ready = (
                    healthy_vector.size == len(self._config.channels)
                    and healthy_covariance.shape
                    == (len(self._config.channels), len(self._config.channels))
                    and reference_age_s is not None
                    and reference_age_s <= max_reference_age_s
                    and reference_angle_error_deg is not None
                    and reference_angle_error_deg <= max_reference_angle_error_deg
                    and self._healthy_reference_confidence >= 0.8
                )
                if self._realtime_preserve_stable and center is not None and reference_ready:
                    self._realtime_preserve_frozen_internal_deg = float(
                        center
                    )
                    self._realtime_preserve_frozen_display_deg = (
                        internal_angle_to_operator_bearing_deg(
                            self._realtime_preserve_frozen_internal_deg
                        )
                    )
                    self._realtime_preserve_frozen_vector = np.array(
                        healthy_vector,
                        copy=True,
                    )
                    self._realtime_preserve_frozen_covariance = np.array(
                        healthy_covariance,
                        copy=True,
                    )
                    self._realtime_preserve_frozen_raw_power_linear = (
                        self._healthy_reference_raw_power_linear
                    )
                    self._realtime_preserve_frozen_cal_power_linear = (
                        self._healthy_reference_cal_power_linear
                    )
                    self._realtime_preserve_frozen_reference_age_s = reference_age_s
                    self._realtime_preserve_frozen_reference_angle_error_deg = (
                        reference_angle_error_deg
                    )
                    self._lcmv_jammer_detected_latched = False
                    frozen_reason = (
                        "armed on uniform weights with frozen measured bladeRF U1 "
                        f"internal={self._realtime_preserve_frozen_internal_deg:.2f} deg "
                        f"display={self._realtime_preserve_frozen_display_deg:.2f} deg "
                        f"reference_age={reference_age_s:.2f} s "
                        f"angle_error={reference_angle_error_deg:.2f} deg; "
                        "waiting for jammer evidence"
                    )
                else:
                    self._realtime_preserve_frozen_internal_deg = None
                    self._realtime_preserve_frozen_display_deg = None
                    self._realtime_preserve_frozen_vector = None
                    self._realtime_preserve_frozen_covariance = None
                    self._realtime_preserve_frozen_raw_power_linear = None
                    self._realtime_preserve_frozen_cal_power_linear = None
                    self._realtime_preserve_frozen_reference_age_s = reference_age_s
                    self._realtime_preserve_frozen_reference_angle_error_deg = (
                        reference_angle_error_deg
                    )
                    self._lcmv_jammer_detected_latched = False
                    blockers: list[str] = []
                    if not self._realtime_preserve_stable or center is None:
                        blockers.append("live angle cluster is not stable")
                    if healthy_vector.size != len(self._config.channels):
                        blockers.append("measured bladeRF U1 is unavailable")
                    if healthy_covariance.shape != (
                        len(self._config.channels),
                        len(self._config.channels),
                    ):
                        blockers.append("jammer-off covariance is unavailable")
                    if reference_age_s is None or reference_age_s > max_reference_age_s:
                        blockers.append("measured bladeRF U1 is stale")
                    if (
                        reference_angle_error_deg is None
                        or reference_angle_error_deg > max_reference_angle_error_deg
                    ):
                        blockers.append("measured U1 does not match the live angle cluster")
                    if self._healthy_reference_confidence < 0.8:
                        blockers.append("healthy reference confidence is below 0.8")
                    frozen_reason = "; ".join(blockers) or "bladeRF reference unavailable"
            elif not active and preserve_mode == "realtime_bladerf_measured_u1":
                self._reset_realtime_preserve_tracker_locked(
                    "LCMV disabled; collecting a new jammer-off bladeRF angle cluster"
                )
        self._lcmv_test_enabled = active
        self._config.lcmv_test_enabled = active
        if not active:
            self._schedule_beamformer_weights(
                uniform_weights(len(self._config.channels)),
                reason="operator disabled LCMV; smooth return to uniform",
            )
            self._set_lcmv_status(
                enabled=False,
                mode="off",
                reason="",
            )
            self._lcmv_log.info(
                "lcmv_test action=%s enabled=False mode=off "
                "weights=uniform_array_sum",
                normalized_source,
            )
            self._record_runtime_event(
                "lcmv_off",
                source=f"backend_control:{normalized_source}",
                notes="LCMV disabled; smooth transition to uniform weights scheduled",
            )
            self._emit_status("LCMV Test Nulling: OFF")
            return

        self._set_beamformer_weights(uniform_weights(len(self._config.channels)))
        enable_diag: dict[str, object] = {}
        if preserve_mode == "realtime_bladerf_measured_u1":
            enable_diag = self._realtime_preserve_tracker_payload()
            enable_diag["lcmv_jammer_activation_armed"] = bool(
                enable_diag.get("realtime_bladerf_angle_frozen", False)
            )
        self._set_lcmv_status(
            enabled=True,
            mode="fallback",
            reason=(
                frozen_reason
                if preserve_mode == "realtime_bladerf_measured_u1"
                else "waiting_for_music_peak"
            ),
            spatial_vector_diagnostics=enable_diag,
        )
        self._lcmv_log.info(
            "lcmv_test action=%s enabled=True mode=fallback "
            "reason=%s weights=uniform_array_sum "
            "weight_transition=armed_uniform_waiting_for_valid_target",
            normalized_source,
            frozen_reason or "waiting_for_music_peak",
        )
        self._record_runtime_event(
            "lcmv_on",
            source=f"backend_control:{normalized_source}",
            notes=(
                frozen_reason
                or "LCMV armed in uniform fallback while waiting for a valid target"
            ),
        )
        self._emit_status(
            "LCMV armed automatically after healthy PVT"
            if normalized_source == "auto_after_pvt"
            else "LCMV Test Nulling: ON"
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
        with self._results_lock:
            output_metrics = dict(self._latest_output_power_metrics)
        self._loggers["health"].info(
            "gnss fifo iq: chunks=%d samples/chunk=%d "
            "iq_peak_component=%.4f iq_peak_magnitude=%.4f iq_rms_magnitude=%.4f "
            "near_full_scale_pct=%.4f threshold_component=%.3f "
            "fifo_output_source=%s fifo_output_power_linear=%s fifo_output_power_db=%s "
            "fifo_output_rms_complex=%s fifo_output_near_full_scale_pct=%s",
            self._gnss_fifo_health_chunk_counter,
            samples_per_chunk,
            self._gnss_fifo_health_peak_component,
            self._gnss_fifo_health_peak_magnitude,
            rms_magnitude,
            100.0 * near_full_scale_fraction,
            float(self._config.rx_clipping_component_threshold),
            output_metrics.get("fifo_output_source", "--"),
            self._format_optional_float(output_metrics.get("fifo_output_power_linear"), 6),
            self._format_optional_float(output_metrics.get("fifo_output_power_db")),
            self._format_optional_float(output_metrics.get("fifo_output_rms_complex"), 6),
            self._format_optional_float(output_metrics.get("fifo_output_near_full_scale_pct"), 4),
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
            "gnss_combiner_compute",
            "gnss_fifo_write",
            "dsp_phase",
            "dsp_doa",
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
            "gnss_combiner_compute_avg_ms=%.2f gnss_combiner_compute_max_ms=%.2f "
            "gnss_combiner_compute_n=%d gnss_fifo_write_avg_ms=%.2f "
            "gnss_fifo_write_max_ms=%.2f gnss_fifo_write_n=%d "
            "dsp_phase_avg_ms=%.2f dsp_phase_max_ms=%.2f dsp_phase_n=%d "
            "dsp_doa_avg_ms=%.2f dsp_doa_max_ms=%.2f dsp_doa_n=%d "
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
            self._perf_ms(snapshot, "gnss_combiner_compute", "avg"),
            self._perf_ms(snapshot, "gnss_combiner_compute", "max"),
            self._perf_count(snapshot, "gnss_combiner_compute"),
            self._perf_ms(snapshot, "gnss_fifo_write", "avg"),
            self._perf_ms(snapshot, "gnss_fifo_write", "max"),
            self._perf_count(snapshot, "gnss_fifo_write"),
            self._perf_ms(snapshot, "dsp_phase", "avg"),
            self._perf_ms(snapshot, "dsp_phase", "max"),
            self._perf_count(snapshot, "dsp_phase"),
            self._perf_ms(snapshot, "dsp_doa", "avg"),
            self._perf_ms(snapshot, "dsp_doa", "max"),
            self._perf_count(snapshot, "dsp_doa"),
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
        self._target_beamformer_weights = np.array(selected, copy=True)
        self._beamformer_transition_start_weights = np.array(selected, copy=True)
        self._beamformer_transition_total_chunks = 0
        self._beamformer_transition_completed_chunks = 0
        self._beamformer_transition_reason = "immediate weight update"
        return selected

    def _set_beamformer_weights(self, weights: np.ndarray) -> np.ndarray:
        with self._beamformer_lock:
            return self._store_beamformer_weights(weights)

    def _schedule_beamformer_weights(
        self,
        weights: np.ndarray,
        *,
        reason: str,
        preempt_active_transition: bool = True,
    ) -> np.ndarray:
        selected = np.asarray(weights, dtype=np.complex128).reshape(-1)
        if selected.size == 0:
            selected = uniform_weights(len(self._config.channels))
        transition_s = max(
            0.0,
            float(getattr(self._config, "lcmv_weight_transition_s", 0.0)),
        )
        with self._beamformer_lock:
            if transition_s <= 0.0:
                applied = self._store_beamformer_weights(selected)
                self._beamformer_transition_reason = f"{reason}; transition disabled"
                return applied
            if (
                self._target_beamformer_weights.size == selected.size
                and np.allclose(
                    self._target_beamformer_weights,
                    selected,
                    rtol=1e-7,
                    atol=1e-9,
                )
            ):
                return np.array(self._target_beamformer_weights, copy=True)
            transition_active = (
                self._beamformer_transition_total_chunks > 0
                and self._beamformer_transition_completed_chunks
                < self._beamformer_transition_total_chunks
            )
            if transition_active and not preempt_active_transition:
                # Covariance/DoA updates arrive several times per second. A
                # fresh full-duration ramp for every update can keep the
                # applied weights permanently near the start of the ramp.
                # Finish the current target first; the next DoA update after
                # completion will schedule the newest covariance target.
                return np.array(self._target_beamformer_weights, copy=True)
            chunk_duration_s = float(self._config.samples_per_chunk) / max(
                float(self._config.sample_rate),
                1.0,
            )
            total_chunks = max(
                1,
                int(np.ceil(transition_s / max(chunk_duration_s, 1e-9))),
            )
            self._beamformer_transition_start_weights = np.array(
                self._latest_beamformer_weights,
                copy=True,
            )
            self._target_beamformer_weights = np.array(selected, copy=True)
            self._beamformer_transition_total_chunks = total_chunks
            self._beamformer_transition_completed_chunks = 0
            self._beamformer_transition_reason = str(reason)
            return np.array(selected, copy=True)

    def _advance_beamformer_transition(self) -> np.ndarray:
        with self._beamformer_lock:
            total = int(self._beamformer_transition_total_chunks)
            completed = int(self._beamformer_transition_completed_chunks)
            if total <= 0 or completed >= total:
                return np.array(self._latest_beamformer_weights, copy=True)
            completed += 1
            alpha = min(max(completed / float(total), 0.0), 1.0)
            applied = (
                (1.0 - alpha) * self._beamformer_transition_start_weights
                + alpha * self._target_beamformer_weights
            )
            self._latest_beamformer_weights = np.asarray(
                applied,
                dtype=np.complex128,
            )
            self._latest_gnss_effective_weights = self._effective_gnss_weights(
                self._latest_beamformer_weights
            )
            self._beamformer_transition_completed_chunks = completed
            if completed >= total:
                self._latest_beamformer_weights = np.array(
                    self._target_beamformer_weights,
                    copy=True,
                )
                self._latest_gnss_effective_weights = self._effective_gnss_weights(
                    self._latest_beamformer_weights
                )
                self._beamformer_transition_total_chunks = 0
                self._beamformer_transition_completed_chunks = 0
            return np.array(self._latest_beamformer_weights, copy=True)

    def _beamformer_transition_payload(self) -> dict[str, object]:
        with self._beamformer_lock:
            total = int(self._beamformer_transition_total_chunks)
            completed = int(self._beamformer_transition_completed_chunks)
            active = total > 0 and completed < total
            progress = completed / float(total) if total > 0 else 1.0
            current = np.array(self._latest_beamformer_weights, copy=True)
            target = np.array(self._target_beamformer_weights, copy=True)
            return {
                "weight_transition_active": bool(active),
                "weight_transition_duration_s": self._json_float(
                    getattr(self._config, "lcmv_weight_transition_s", 0.0)
                ),
                "weight_transition_total_chunks": total,
                "weight_transition_completed_chunks": completed,
                "weight_transition_progress": self._json_float(progress),
                "weight_transition_reason": self._beamformer_transition_reason,
                "weight_transition_current_weights": complex_vector_payload(current),
                "weight_transition_target_weights": complex_vector_payload(target),
            }

    def _beamformer_evidence_payload(self) -> dict[str, object]:
        """Return the exact logical and effective GNSS combiner state."""

        with self._beamformer_lock:
            total = int(self._beamformer_transition_total_chunks)
            completed = int(self._beamformer_transition_completed_chunks)
            active = total > 0 and completed < total
            progress = completed / float(total) if total > 0 else 1.0
            current = np.array(self._latest_beamformer_weights, copy=True)
            target = np.array(self._target_beamformer_weights, copy=True)
            start = np.array(self._beamformer_transition_start_weights, copy=True)
            effective = np.array(self._latest_gnss_effective_weights, copy=True)
            reason = str(self._beamformer_transition_reason)
        uniform = uniform_weights(len(self._config.channels))
        return {
            "combiner_equation": "y[n] = w^H C x[n]",
            "uniform_combiner_convention": "raw channel sum; logical weights are all 1+0j",
            "uniform_logical_weights": complex_vector_payload(uniform),
            "current_logical_weights": complex_vector_payload(current),
            "target_logical_weights": complex_vector_payload(target),
            "transition_start_logical_weights": complex_vector_payload(start),
            "effective_gnss_multiply_coefficients_conj_w_times_calibration": (
                complex_vector_payload(effective)
            ),
            "weight_transition_active": bool(active),
            "weight_transition_duration_s": self._json_float(
                getattr(self._config, "lcmv_weight_transition_s", 0.0)
            ),
            "weight_transition_total_chunks": total,
            "weight_transition_completed_chunks": completed,
            "weight_transition_progress": self._json_float(progress),
            "weight_transition_reason": reason,
            "weight_transition_current_weights": complex_vector_payload(current),
            "weight_transition_target_weights": complex_vector_payload(target),
        }

    def _get_beamformer_weights_copy(self) -> np.ndarray:
        with self._beamformer_lock:
            return np.array(self._latest_beamformer_weights, copy=True)

    def _get_beamformer_target_weights_copy(self) -> np.ndarray:
        """Return the latest complete common-LCMV target, not ramp position."""

        with self._beamformer_lock:
            return np.array(self._target_beamformer_weights, copy=True)

    def _set_shared_measured_u1_protection_weights(
        self,
        weights: np.ndarray,
        *,
        available: bool = True,
    ) -> None:
        """Publish the one shared covariance-LCMV measured-U1 target."""

        selected = np.asarray(weights, dtype=np.complex128).reshape(-1)
        if selected.size != len(self._config.channels) or not np.all(
            np.isfinite(selected)
        ):
            return
        with self._beamformer_lock:
            self._shared_measured_u1_protection_weights = np.array(
                selected, copy=True
            )
            self._shared_measured_u1_protection_available = bool(available)

    def _get_shared_measured_u1_protection_weights_copy(self) -> np.ndarray:
        with self._beamformer_lock:
            return np.array(
                self._shared_measured_u1_protection_weights,
                copy=True,
            )

    def _shared_measured_u1_protection_is_available(self) -> bool:
        with self._beamformer_lock:
            return bool(self._shared_measured_u1_protection_available)

    def _get_gnss_monitor_logical_weights_copy(self) -> np.ndarray:
        """Return the weights that produced the IQ streams GNSS-SDR received."""

        with self._beamformer_lock:
            if self._latest_shared_u1_phase_logical_weights.size:
                return np.array(
                    self._latest_shared_u1_phase_logical_weights,
                    copy=True,
                )
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
        # The fanout bank is created only after the GNSS bridge has started.
        # Keep the ordinary beamformer usable during construction, teardown,
        # and failed/partial startup instead of raising from the realtime path.
        if (
            self._shared_phase_fanout_enabled()
            and self._shared_u1_phase_bank is not None
        ):
            return self._gnss_shared_u1_phase_output_matrix(chunk)
        return self._gnss_beamformed_output_vector(chunk)

    def _shared_phase_fanout_enabled(self) -> bool:
        return bool(
            getattr(
                self._config,
                "gnss_shared_u1_phase_compensation_enabled",
                False,
            )
        )

    def _shared_phase_source_count(self) -> int:
        satellites = tuple(
            int(value)
            for value in self._config.gnss_shared_u1_phase_satellites
        )
        return (
            len(satellites)
            if satellites
            else max(1, int(self._config.gnss_1c_channel_count))
        )

    def _tracking_source_satellites(
        self,
        snapshot: dict[str, object],
    ) -> tuple[int | None, ...]:
        pinned = tuple(
            int(value)
            for value in self._config.gnss_shared_u1_phase_satellites
        )
        if pinned:
            return tuple(pinned)
        source_count = self._shared_phase_source_count()
        mapped: list[int | None] = [None] * source_count
        # ``tracking_monitor`` is an archive-like latest-by-PRN collection.  A
        # PRN remains in it after its channel has moved to another PRN, so
        # iterating that collection can let a stale, numerically-later PRN
        # overwrite the live channel assignment.  The ``prns`` collection has
        # already reconciled receiver state with the current UDP
        # channel->PRN map.  Require both views to agree before routing a
        # phase-compensated output row.
        entries = snapshot.get("prns", [])
        if not isinstance(entries, list):
            return tuple(mapped)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                channel = int(entry.get("channel", -1))
                prn = int(entry.get("prn", 0) or 0)
                tracking_monitor_prn = int(
                    entry.get("tracking_monitor_prn", 0) or 0
                )
            except (TypeError, ValueError):
                continue
            system = str(entry.get("system", "G")).upper()
            signal = str(entry.get("signal", ""))
            if (
                0 <= channel < source_count
                and 1 <= prn <= 32
                and tracking_monitor_prn == prn
                and str(entry.get("state", "")).lower() == "tracking"
                and system in {"G", "GPS"}
                and signal == "1C"
            ):
                mapped[channel] = prn
        return tuple(mapped)

    def _gnss_shared_u1_phase_output_matrix(self, chunk: np.ndarray) -> np.ndarray:
        """Return phase-aligned copies of one shared beam for GPS source slots."""

        self._advance_beamformer_transition()
        source = np.asarray(chunk, dtype=np.complex64)
        if source.ndim != 2 or source.shape[1] == 0:
            return np.zeros((0, 0), dtype=np.complex64)
        bank = self._shared_u1_phase_bank
        if bank is None:
            raise RuntimeError("shared-U1 phase compensation bank is unavailable")
        now = time.monotonic()
        emit_status = bool(
            now - self._last_shared_u1_phase_status_log_ts >= 1.0
        )
        monitor = self._shared_u1_phase_monitor
        if emit_status and monitor is not None:
            self._shared_u1_desired_vectors_cache = (
                monitor.desired_vectors_snapshot()
            )
            bridge = self._gnss_bridge
            if bridge is not None:
                self._shared_u1_source_satellites_cache = (
                    self._tracking_source_satellites(bridge.snapshot())
                )
        with self._results_lock:
            jammer_latched = bool(self._lcmv_jammer_detected_latched)
        enabled_now = bool(self._lcmv_test_enabled and jammer_latched)
        common = self._get_beamformer_target_weights_copy()
        protection = self._get_shared_measured_u1_protection_weights_copy()
        logical, status = bank.advance(
            shared_common_weights=common,
            shared_measured_u1_weights=protection,
            shared_measured_u1_available=(
                self._shared_measured_u1_protection_is_available()
            ),
            # The monitor itself updates at 1 Hz. Re-reading and normalizing
            # the same ten vectors on every 8.2 ms IQ chunk is pure overhead.
            desired_vectors=(
                self._shared_u1_desired_vectors_cache if emit_status else {}
            ),
            source_satellites=self._shared_u1_source_satellites_cache,
            enabled_now=enabled_now,
            now_monotonic=now,
            emit_status=emit_status,
        )
        # Retain the exact phase-aligned shared-beam rows.  The spatial monitor
        # must correlate each PRN through the same row GNSS-SDR received.
        with self._beamformer_lock:
            self._latest_shared_u1_phase_logical_weights = np.array(
                logical,
                copy=True,
            )
        correction_vector = self._config.phase_correction_vector
        correction = (
            np.asarray(correction_vector, dtype=np.complex64).reshape(-1)
            if correction_vector is not None
            else np.ones((source.shape[0],), dtype=np.complex64)
        )
        if correction.size != source.shape[0]:
            raise ValueError("shared-U1 phase correction length mismatch")
        outputs, scalar_fast_path = apply_shared_phase_fanout(
            source,
            logical,
            correction,
        )
        if scalar_fast_path:
            self._shared_u1_scalar_fanout_chunks += 1
        else:
            self._shared_u1_matrix_fallback_chunks += 1
        if emit_status:
            self._last_shared_u1_phase_status_log_ts = now
            bridge_count = sum(
                1
                for payload in status.values()
                if bool(payload.get("shared_protection_bridge_applied", False))
            )
            transitioning_count = sum(
                1
                for payload in status.values()
                if bool(payload.get("transition_active", False))
            )
            self._handoff_log.info(
                "shared_u1_phase_compensation_status %s",
                json.dumps(
                    {
                        "event": "shared_u1_phase_compensation_status",
                        "applied_to_gnss_sdr": True,
                        "common_pvt_solver": True,
                        "jammer_latched": jammer_latched,
                        "enabled_now": enabled_now,
                        "independent_per_prn_lcmv": False,
                        "active_per_prn_source_count": 0,
                        "phase_compensated_shared_protection_count": bridge_count,
                        "transitioning_source_count": transitioning_count,
                        "phase_compensation": "per_prn_complex_response_continuity",
                        "scalar_fanout_chunks": self._shared_u1_scalar_fanout_chunks,
                        "matrix_fallback_chunks": (
                            self._shared_u1_matrix_fallback_chunks
                        ),
                        "latest_output_path": (
                            "one_shared_beam_plus_prn_scalars"
                            if scalar_fast_path
                            else "general_transition_matrix"
                        ),
                        "dynamic_source_satellites": [
                            (f"G{value:02d}" if value is not None else None)
                            for value in self._shared_u1_source_satellites_cache
                        ],
                        "sources": status,
                    },
                    allow_nan=False,
                    separators=(",", ":"),
                ),
            )
        return outputs

    def _gnss_beamformed_output_vector(self, chunk: np.ndarray) -> np.ndarray:
        self._advance_beamformer_transition()
        if self._config.phase_correction_vector is not None:
            source = np.asarray(chunk, dtype=np.complex64)
            if source.ndim != 2 or source.shape[1] == 0:
                return np.zeros((0,), dtype=np.complex64)
            effective_weights = self._get_gnss_effective_weights()
            if effective_weights.size != source.shape[0]:
                weights = self._get_beamformer_weights_copy()
                try:
                    return compute_gnss_output_vector(
                        buffer=chunk,
                        beamformer_weights=weights,
                        phase_correction_vector=self._config.phase_correction_vector,
                    )
                except Exception as exc:
                    if not self._lcmv_test_enabled:
                        raise
                    self._activate_lcmv_test_fallback(
                        f"GNSS output uniform fallback after phase/weight error: {exc}",
                        music_internal_deg=None,
                        music_bearing_deg=None,
                    )
                    return apply_beamformer(
                        source.astype(np.complex128, copy=False),
                        uniform_weights(source.shape[0]),
                    )
            # Static calibration lets the realtime GNSS handoff collapse:
            #   apply_phase_calibration(chunk) -> apply_beamformer(...)
            # into one weighted sum without allocating a full complex128
            # corrected channel matrix for every RX chunk.
            try:
                return self._weighted_sum_complex64(source, effective_weights)
            except Exception as exc:
                if not self._lcmv_test_enabled:
                    raise
                self._activate_lcmv_test_fallback(
                    f"GNSS output uniform fallback after weighted-sum error: {exc}",
                    music_internal_deg=None,
                    music_bearing_deg=None,
                )
                return apply_beamformer(
                    source.astype(np.complex128, copy=False),
                    uniform_weights(source.shape[0]),
                )
        try:
            return compute_gnss_output_vector(
                buffer=chunk,
                beamformer_weights=self._get_beamformer_weights_copy(),
                phase_correction_vector=self._config.phase_correction_vector,
            )
        except Exception as exc:
            if not self._lcmv_test_enabled:
                raise
            source = np.asarray(chunk, dtype=np.complex128)
            self._activate_lcmv_test_fallback(
                f"GNSS output uniform fallback after beamformer error: {exc}",
                music_internal_deg=None,
                music_bearing_deg=None,
            )
            return apply_beamformer(source, uniform_weights(source.shape[0]))

    def _gnss_handoff_mode_label(self) -> str:
        if self._shared_phase_fanout_enabled():
            # This names the fixed transport architecture. The current
            # uniform/measured-U1 state is recorded separately by
            # _fifo_output_source_label().
            return "shared_prn_phase_continuity_fanout"
        if self._lcmv_test_enabled:
            status = self._lcmv_status_copy()
            if str(status.get("mode", "")).lower() == "on":
                return "lcmv_test_nulling_continuous"
            return "lcmv_test_uniform_fallback_continuous"
        return "uniform_array_sum_continuous"

    # -------------------------------------------------------------------------
    # Operator-Controlled LCMV Test Mode
    # -------------------------------------------------------------------------

    def _reset_lcmv_test_for_run(self) -> None:
        self._spatial_diag_seq = 0
        self._set_shared_measured_u1_protection_weights(
            uniform_weights(len(self._config.channels)),
            available=False,
        )
        with self._beamformer_lock:
            self._latest_shared_u1_phase_logical_weights = np.empty(
                (0, len(self._config.channels)),
                dtype=np.complex128,
            )
        self._last_lcmv_heavy_diag_ts = None
        self._latest_lcmv_heavy_diag_payload = {
            "heavy_diagnostics_interval_s": self._json_float(
                getattr(self._config, "lcmv_heavy_diagnostics_interval_s", 1.0)
            ),
            "heavy_diagnostics_emitted": False,
            "heavy_diagnostics_skipped_due_to_throttle": False,
            "last_heavy_diagnostics_age_s": None,
        }
        with self._results_lock:
            self._latest_spatial_vector_diagnostics = {}
        if self._lcmv_test_enabled:
            self._set_lcmv_status(
                enabled=True,
                mode="fallback",
                reason="waiting_for_music_peak",
            )
            return
        self._set_lcmv_status(enabled=False, mode="off", reason="")

    def _lcmv_status_snapshot(
        self,
        *,
        enabled: bool,
        mode: str,
        reason: str = "",
        music_internal_deg: float | None = None,
        music_bearing_deg: float | None = None,
        null_internal_deg: float | None = None,
        null_bearing_deg: float | None = None,
        weight_norm: float | None = None,
        max_weight_abs: float | None = None,
        condition_number: float | None = None,
        preserve_residual_abs: float | None = None,
        null_residual_abs: float | None = None,
        uniform_rms: float | None = None,
        lcmv_rms: float | None = None,
        lcmv_response_db: np.ndarray | None = None,
        lcmv_response_abs: np.ndarray | None = None,
        lcmv_response_power: np.ndarray | None = None,
        lcmv_response_power_db: np.ndarray | None = None,
        lcmv_model_summary: dict[str, object] | None = None,
        output_metrics: dict[str, object] | None = None,
        active_lcmv_null_method: str | None = None,
        active_lcmv_weights_source: str | None = None,
        active_lcmv_fallback_reason: str | None = None,
        active_lcmv_method: str | None = None,
        active_lcmv_fallback_used: bool | None = None,
        candidate_methods_computed: list[str] | None = None,
        candidate_methods_valid: list[str] | None = None,
        candidate_methods_rejected: dict[str, object] | None = None,
        run_state_label: str | None = None,
        jammer_confidence_score: float | None = None,
        healthy_confidence_score: float | None = None,
        spatial_vector_diagnostics: dict[str, object] | None = None,
        heavy_diagnostics_interval_s: float | None = None,
        heavy_diagnostics_emitted: bool | None = None,
        heavy_diagnostics_skipped_due_to_throttle: bool | None = None,
        last_heavy_diagnostics_age_s: float | None = None,
    ) -> dict[str, object]:
        normalized_mode = str(mode).strip().lower() or "off"
        if not bool(enabled):
            normalized_mode = "off"
        if normalized_mode == "on":
            status = "ON"
            description = "Covariance LCMV null active"
        elif normalized_mode == "fallback":
            status = "FALLBACK"
            description = "Uniform fallback"
        else:
            status = "OFF"
            description = "Uniform beamformer"
        return {
            "enabled": bool(enabled),
            "mode": normalized_mode,
            "status": status,
            "description": description,
            "fallback_reason": str(reason or ""),
            "music_internal_deg": self._finite_metric_float(music_internal_deg),
            "music_bearing_deg": self._finite_metric_float(music_bearing_deg),
            "null_internal_deg": self._finite_metric_float(null_internal_deg),
            "null_bearing_deg": self._finite_metric_float(null_bearing_deg),
            "weight_norm": self._finite_metric_float(weight_norm),
            "max_weight_abs": self._finite_metric_float(max_weight_abs),
            "condition_number": self._finite_metric_float(condition_number),
            "preserve_residual_abs": self._finite_metric_float(preserve_residual_abs),
            "null_residual_abs": self._finite_metric_float(null_residual_abs),
            "uniform_rms": self._finite_metric_float(uniform_rms),
            "lcmv_rms": self._finite_metric_float(lcmv_rms),
            "lcmv_response_db": (
                np.asarray(lcmv_response_db, dtype=np.float64)
                if lcmv_response_db is not None
                else np.zeros((0,), dtype=np.float64)
            ),
            "lcmv_response_abs": (
                np.asarray(lcmv_response_abs, dtype=np.float64)
                if lcmv_response_abs is not None
                else np.zeros((0,), dtype=np.float64)
            ),
            "lcmv_response_power": (
                np.asarray(lcmv_response_power, dtype=np.float64)
                if lcmv_response_power is not None
                else np.zeros((0,), dtype=np.float64)
            ),
            "lcmv_response_power_db": (
                np.asarray(lcmv_response_power_db, dtype=np.float64)
                if lcmv_response_power_db is not None
                else np.zeros((0,), dtype=np.float64)
            ),
            "lcmv_model_summary": dict(lcmv_model_summary or {}),
            "output_metrics": dict(output_metrics or {}),
            "active_lcmv_null_method": str(
                active_lcmv_null_method or self._lcmv_test_null_method
            ),
            "active_lcmv_method": str(active_lcmv_method or active_lcmv_null_method or self._lcmv_test_null_method),
            "active_lcmv_weights_source": str(
                active_lcmv_weights_source
                or (
                    self._lcmv_test_null_method
                    if normalized_mode == "on"
                    else (
                        "uniform_fallback"
                        if normalized_mode == "fallback"
                        else "uniform_array_sum"
                    )
                )
            ),
            "active_lcmv_fallback_used": bool(
                active_lcmv_fallback_used
                if active_lcmv_fallback_used is not None
                else normalized_mode == "fallback"
            ),
            "active_lcmv_fallback_reason": str(active_lcmv_fallback_reason or ""),
            "candidate_methods_computed": list(candidate_methods_computed or []),
            "candidate_methods_valid": list(candidate_methods_valid or []),
            "candidate_methods_rejected": dict(candidate_methods_rejected or {}),
            "run_state_label": str(run_state_label or "unknown"),
            "jammer_confidence_score": self._finite_metric_float(jammer_confidence_score),
            "healthy_confidence_score": self._finite_metric_float(healthy_confidence_score),
            "heavy_diagnostics_interval_s": self._finite_metric_float(
                heavy_diagnostics_interval_s
                if heavy_diagnostics_interval_s is not None
                else self._latest_lcmv_heavy_diag_payload.get(
                    "heavy_diagnostics_interval_s"
                )
            ),
            "heavy_diagnostics_emitted": bool(
                heavy_diagnostics_emitted
                if heavy_diagnostics_emitted is not None
                else self._latest_lcmv_heavy_diag_payload.get(
                    "heavy_diagnostics_emitted", False
                )
            ),
            "heavy_diagnostics_skipped_due_to_throttle": bool(
                heavy_diagnostics_skipped_due_to_throttle
                if heavy_diagnostics_skipped_due_to_throttle is not None
                else self._latest_lcmv_heavy_diag_payload.get(
                    "heavy_diagnostics_skipped_due_to_throttle", False
                )
            ),
            "last_heavy_diagnostics_age_s": self._finite_metric_float(
                last_heavy_diagnostics_age_s
                if last_heavy_diagnostics_age_s is not None
                else self._latest_lcmv_heavy_diag_payload.get(
                    "last_heavy_diagnostics_age_s"
                )
            ),
            "spatial_vector_diagnostics": dict(spatial_vector_diagnostics or {}),
        }

    def _set_lcmv_status(self, **kwargs: object) -> None:
        snapshot = self._lcmv_status_snapshot(**kwargs)
        with self._results_lock:
            self._latest_lcmv_test = snapshot

    def _lcmv_status_copy(self) -> dict[str, object]:
        with self._results_lock:
            snapshot = dict(self._latest_lcmv_test)
        snapshot.update(self._beamformer_transition_payload())
        return snapshot

    def _lcmv_heavy_diagnostics_interval_s(self) -> float:
        return max(
            0.0,
            float(getattr(self._config, "lcmv_heavy_diagnostics_interval_s", 1.0)),
        )

    def _lcmv_heavy_diagnostics_decision(self) -> dict[str, object]:
        interval_s = self._lcmv_heavy_diagnostics_interval_s()
        now = time.monotonic()
        last_ts = self._last_lcmv_heavy_diag_ts
        age_s = None if last_ts is None else now - float(last_ts)
        emit = last_ts is None or interval_s <= 0.0 or (
            age_s is not None and age_s >= interval_s
        )
        if emit:
            self._last_lcmv_heavy_diag_ts = now
        payload = {
            "heavy_diagnostics_interval_s": self._json_float(interval_s),
            "heavy_diagnostics_emitted": bool(emit),
            "heavy_diagnostics_skipped_due_to_throttle": not bool(emit),
            "last_heavy_diagnostics_age_s": self._json_float(age_s),
        }
        self._latest_lcmv_heavy_diag_payload = dict(payload)
        return payload

    @staticmethod
    def _normalized_lcmv_null_method(value: object) -> str:
        method = str(value or "covariance_lcmv_ideal").strip().lower()
        if method not in VALID_LCMV_METHODS:
            return "covariance_lcmv_ideal"
        return method

    @staticmethod
    def _finite_metric_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(number):
            return None
        return number

    def _lcmv_weight_norm_limit(self) -> float:
        return float(
            getattr(
                self._config,
                "lcmv_max_weight_norm",
                getattr(self._config, "lcmv_test_max_weight_norm", 8.0),
            )
        )

    @staticmethod
    def _weight_power(weights: object) -> float | None:
        w = np.asarray(weights, dtype=np.complex128).reshape(-1)
        if w.size == 0 or not np.all(np.isfinite(w)):
            return None
        power = float(np.vdot(w, w).real)
        return power if np.isfinite(power) else None

    def _method_safety_rejection_reason(
        self,
        *,
        result: object | None,
        target_suppression_db: float | None = None,
        enforce_white_noise_gain: bool = True,
    ) -> str:
        if result is None:
            return "candidate unavailable"
        weights = np.asarray(getattr(result, "weights", []), dtype=np.complex128).reshape(-1)
        if weights.size != len(self._config.channels):
            return f"weight length {weights.size} != channel count {len(self._config.channels)}"
        if not np.all(np.isfinite(weights)):
            return "weights contain NaN or Inf"
        weight_norm = float(np.linalg.norm(weights))
        max_norm = self._lcmv_weight_norm_limit()
        if not np.isfinite(weight_norm) or weight_norm > max_norm:
            return f"weight_norm {weight_norm:.3f} exceeds {max_norm:.3f}"
        weight_power = self._weight_power(weights)
        white_noise_gain_db = power_db(weight_power)
        max_wng = self._finite_metric_float(
            getattr(self._config, "lcmv_max_white_noise_gain_db", 15.0)
        )
        if (
            enforce_white_noise_gain
            and white_noise_gain_db is not None
            and max_wng is not None
            and white_noise_gain_db > max_wng
        ):
            return f"white_noise_gain_db {white_noise_gain_db:.2f} exceeds {max_wng:.2f}"
        min_suppression = self._finite_metric_float(
            getattr(self._config, "lcmv_min_predicted_jammer_suppression_db", 3.0)
        )
        if (
            target_suppression_db is not None
            and min_suppression is not None
            and target_suppression_db < min_suppression
        ):
            return (
                f"predicted_target_suppression_db {target_suppression_db:.2f} "
                f"below {min_suppression:.2f}"
            )
        return ""

    def _candidate_method_payload(
        self,
        *,
        prefix: str,
        result: object | None,
        error: str,
        covariance: np.ndarray,
        reference_weights: np.ndarray,
        uniform_sum_weights: np.ndarray,
        uniform_average_weights: np.ndarray,
        ideal_vector: np.ndarray,
        u1_vector: np.ndarray,
        healthy_reference_vector: np.ndarray,
    ) -> tuple[dict[str, object], str]:
        payload: dict[str, object] = {
            f"{prefix}_valid": False,
            f"{prefix}_rejected_reason": str(error or ""),
        }
        if result is None:
            if not payload[f"{prefix}_rejected_reason"]:
                payload[f"{prefix}_rejected_reason"] = "not computed"
            return payload, str(payload[f"{prefix}_rejected_reason"])

        weights = np.asarray(getattr(result, "weights", []), dtype=np.complex128).reshape(-1)
        output_power = covariance_output_power(covariance=covariance, weights=weights)
        reference_output_power = covariance_output_power(
            covariance=covariance,
            weights=reference_weights,
        )
        weight_power = self._weight_power(weights)
        ref_weight_power = self._weight_power(reference_weights)
        uniform_sum_power = self._weight_power(uniform_sum_weights)
        uniform_average_power = self._weight_power(uniform_average_weights)
        ideal_ref_power = self._vector_response_power(reference_weights, ideal_vector)
        ideal_method_power = self._vector_response_power(weights, ideal_vector)
        u1_ref_power = self._vector_response_power(reference_weights, u1_vector)
        u1_method_power = self._vector_response_power(weights, u1_vector)
        healthy_ref_power = self._vector_response_power(reference_weights, healthy_reference_vector)
        healthy_method_power = self._vector_response_power(weights, healthy_reference_vector)
        desired_loss_db = ratio_db(healthy_ref_power, healthy_method_power)
        ideal_reduction_db = ratio_db(ideal_ref_power, ideal_method_power)
        u1_reduction_db = ratio_db(u1_ref_power, u1_method_power)
        noise_gain_vs_ref_db = ratio_db(weight_power, ref_weight_power)
        white_noise_gain_db = power_db(weight_power)
        max_wng = self._finite_metric_float(
            getattr(self._config, "lcmv_max_white_noise_gain_db", 15.0)
        )
        wng_warning = ""
        if (
            white_noise_gain_db is not None
            and max_wng is not None
            and white_noise_gain_db > max_wng
        ):
            wng_warning = (
                f"white_noise_gain_db {white_noise_gain_db:.2f} exceeds {max_wng:.2f}"
            )
        effective_js = (
            u1_reduction_db - desired_loss_db
            if u1_reduction_db is not None and desired_loss_db is not None
            else None
        )
        effective_receiver = (
            effective_js - max(0.0, noise_gain_vs_ref_db or 0.0)
            if effective_js is not None
            else None
        )
        targets_measured_u1 = "u1" in prefix or "measured" in prefix
        target_suppression_db = u1_reduction_db if targets_measured_u1 else ideal_reduction_db
        rejection = self._method_safety_rejection_reason(
            result=result,
            target_suppression_db=target_suppression_db,
            enforce_white_noise_gain=False,
        )

        payload.update(
            {
                f"{prefix}_valid": rejection == "",
                f"{prefix}_rejected_reason": rejection,
                f"{prefix}_weight_norm": self._json_float(getattr(result, "weight_norm", None)),
                f"{prefix}_weight_norm_squared": self._json_float(weight_power),
                f"{prefix}_white_noise_gain_db": white_noise_gain_db,
                f"{prefix}_white_noise_gain_threshold_db": max_wng,
                f"{prefix}_white_noise_gain_warning": wng_warning,
                f"{prefix}_noise_gain_vs_reference_db": noise_gain_vs_ref_db,
                f"{prefix}_noise_gain_vs_uniform_sum_db": ratio_db(
                    weight_power,
                    uniform_sum_power,
                ),
                f"{prefix}_noise_gain_vs_uniform_average_db": ratio_db(
                    weight_power,
                    uniform_average_power,
                ),
                f"{prefix}_total_output_power_from_R": self._json_float(output_power),
                f"{prefix}_output_power_from_R": self._json_float(output_power),
                f"{prefix}_total_output_reduction_vs_reference_db": ratio_db(
                    reference_output_power,
                    output_power,
                ),
                f"{prefix}_response_to_ideal_abs": self._json_float(
                    None if ideal_method_power is None else np.sqrt(max(ideal_method_power, 0.0))
                ),
                f"{prefix}_response_to_ideal_power": self._json_float(ideal_method_power),
                f"{prefix}_response_to_u1_abs": self._json_float(
                    None if u1_method_power is None else np.sqrt(max(u1_method_power, 0.0))
                ),
                f"{prefix}_response_to_u1_power": self._json_float(u1_method_power),
                f"{prefix}_ideal_component_suppression_db": ideal_reduction_db,
                f"{prefix}_u1_component_suppression_db": u1_reduction_db,
                f"{prefix}_dominant_vector_suppression_db": u1_reduction_db,
                f"{prefix}_predicted_target_suppression_db": target_suppression_db,
                f"{prefix}_ideal_component_reduction_vs_reference_db": ideal_reduction_db,
                f"{prefix}_u1_component_reduction_vs_reference_db": u1_reduction_db,
                f"{prefix}_response_to_healthy_reference_abs": self._json_float(
                    None if healthy_method_power is None else np.sqrt(max(healthy_method_power, 0.0))
                ),
                f"{prefix}_response_to_healthy_reference_power": self._json_float(
                    healthy_method_power
                ),
                f"{prefix}_desired_loss_vs_reference_db": desired_loss_db,
                f"{prefix}_effective_js_improvement_u1_db": self._json_float(
                    effective_js
                ),
                f"{prefix}_effective_receiver_improvement_u1_db": self._json_float(
                    effective_receiver
                ),
                f"{prefix}_condition_number": self._json_float(
                    getattr(result, "condition_number", None)
                ),
                f"{prefix}_condition_number_constraint": self._json_float(
                    getattr(result, "condition_number", None)
                ),
                f"{prefix}_condition_number_R": self._json_float(
                    getattr(result, "condition_number_R", None)
                ),
                f"{prefix}_diagonal_loading": self._json_float(
                    getattr(result, "diagonal_loading", None)
                ),
            }
        )
        return payload, rejection

    def _vector_response_power(
        self,
        weights: object,
        vector: object,
    ) -> float | None:
        w = np.asarray(weights, dtype=np.complex128).reshape(-1)
        v = normalize_complex_vector(vector)
        if w.size == 0 or v.size == 0 or w.size != v.size:
            return None
        if not np.all(np.isfinite(w)):
            return None
        response = complex(np.vdot(v, w))
        power = float(abs(response) ** 2)
        return power if np.isfinite(power) else None

    def _jammer_excess_covariance_payload(
        self,
        *,
        current_covariance: np.ndarray,
        uniform_weights_vector: np.ndarray,
        target_weights: np.ndarray,
        jammer_latched: bool,
    ) -> dict[str, object]:
        """Estimate added-scene output power from the arm-time covariance change.

        The frozen covariance is measured with bladeRF/PVT healthy during the
        automatically classified uniform baseline. Once the automatic activation
        gate is latched, the positive semidefinite part of
        ``R_current - R_arm`` estimates spatial power added after arming.
        Applying uniform, current-applied, and target weights to that same
        excess covariance gives comparable added-power estimates without
        treating total output reduction as jammer suppression.  A separate
        operator marker is required only to call the added power physically
        confirmed jammer-only; without it the result is added-scene suppression.
        """

        payload: dict[str, object] = {
            "jammer_only_suppression_estimate_available": False,
            "jammer_only_suppression_db": None,
            "jammer_only_suppression_unavailable_reason": "",
            "jammer_only_estimator": (
                "PSD projection of current covariance minus frozen jammer-off covariance"
            ),
            "jammer_only_estimate_warning": (
                "The covariance difference contains every spatial component that changed "
                "after arming; interpret it as jammer-only only inside a separately "
                "confirmed physical jammer-on marker window while the bladeRF scene is "
                "otherwise unchanged. The automatic activation gate is not physical "
                "jammer truth."
            ),
        }
        if not jammer_latched:
            payload["jammer_only_suppression_unavailable_reason"] = (
                "automatic jammer activation gate is not latched"
            )
            return payload

        with self._results_lock:
            baseline_covariance = (
                np.array(self._realtime_preserve_frozen_covariance, copy=True)
                if self._realtime_preserve_frozen_covariance is not None
                else np.zeros((0, 0), dtype=np.complex128)
            )
        current = np.asarray(current_covariance, dtype=np.complex128)
        if (
            current.ndim != 2
            or current.shape[0] != current.shape[1]
            or baseline_covariance.shape != current.shape
        ):
            payload["jammer_only_suppression_unavailable_reason"] = (
                "frozen jammer-off and current covariance shapes do not match"
            )
            return payload

        difference = 0.5 * (
            (current - baseline_covariance)
            + (current - baseline_covariance).conj().T
        )
        try:
            eigenvalues, eigenvectors = np.linalg.eigh(difference)
        except np.linalg.LinAlgError as exc:
            payload["jammer_only_suppression_unavailable_reason"] = (
                f"jammer-excess covariance eigendecomposition failed: {exc}"
            )
            return payload
        positive = np.maximum(eigenvalues.real, 0.0)
        negative = np.maximum(-eigenvalues.real, 0.0)
        excess_trace = float(np.sum(positive))
        current_trace = max(float(np.trace(current).real), 0.0)
        numerical_floor = max(1e-12, current_trace * 1e-9)
        if not np.isfinite(excess_trace) or excess_trace <= numerical_floor:
            payload.update(
                {
                    "jammer_excess_covariance_trace_linear": self._json_float(
                        excess_trace
                    ),
                    "jammer_only_suppression_unavailable_reason": (
                        "positive jammer-excess covariance power is below the numerical floor"
                    ),
                }
            )
            return payload

        excess_covariance = (
            eigenvectors
            @ np.diag(positive.astype(np.complex128))
            @ eigenvectors.conj().T
        )
        uniform_w = np.asarray(uniform_weights_vector, dtype=np.complex128).reshape(-1)
        target_w = np.asarray(target_weights, dtype=np.complex128).reshape(-1)
        applied_w = self._get_beamformer_weights_copy()
        before_power = covariance_output_power(
            covariance=excess_covariance,
            weights=uniform_w,
        )
        applied_power = covariance_output_power(
            covariance=excess_covariance,
            weights=applied_w,
        )
        target_power = covariance_output_power(
            covariance=excess_covariance,
            weights=target_w,
        )
        applied_suppression_db = ratio_db(before_power, applied_power)
        target_suppression_db = ratio_db(before_power, target_power)
        if applied_suppression_db is None or target_suppression_db is None:
            payload["jammer_only_suppression_unavailable_reason"] = (
                "jammer-excess output power is not finite"
            )
            return payload

        payload.update(
            {
                "jammer_only_suppression_estimate_available": True,
                "jammer_only_suppression_db": applied_suppression_db,
                "jammer_only_target_suppression_db": target_suppression_db,
                "jammer_only_power_before_uniform_linear": self._json_float(
                    before_power
                ),
                "jammer_only_power_before_uniform_db": power_db(before_power),
                "jammer_only_power_after_applied_linear": self._json_float(
                    applied_power
                ),
                "jammer_only_power_after_applied_db": power_db(applied_power),
                "jammer_only_power_after_target_linear": self._json_float(
                    target_power
                ),
                "jammer_only_power_after_target_db": power_db(target_power),
                "jammer_excess_covariance_trace_linear": self._json_float(
                    excess_trace
                ),
                "jammer_excess_covariance_removed_negative_trace_linear": self._json_float(
                    float(np.sum(negative))
                ),
                "jammer_excess_covariance_positive_eigenvalues_linear": (
                    self._json_float_list(positive[::-1])
                ),
                "jammer_only_suppression_unavailable_reason": "",
                "jammer_only_applied_weights": complex_vector_payload(applied_w),
                "jammer_only_target_weights": complex_vector_payload(target_w),
            }
        )
        return payload

    def _healthy_reference_payload(self, current_u1: np.ndarray) -> dict[str, object]:
        healthy = (
            normalize_complex_vector(self._healthy_reference_vector)
            if self._healthy_reference_vector is not None
            else np.zeros((0,), dtype=np.complex128)
        )
        current = normalize_complex_vector(current_u1)
        coherence = None
        if healthy.size and current.size == healthy.size:
            coherence = abs(complex(np.vdot(healthy, current)))
        age_s = None
        if self._healthy_reference_updated_monotonic_s is not None:
            age_s = time.monotonic() - float(self._healthy_reference_updated_monotonic_s)
        return {
            "healthy_reference_available": bool(healthy.size),
            "healthy_reference_age_s": self._json_float(age_s),
            "healthy_reference_internal_angle_deg": self._json_float(
                self._healthy_reference_internal_angle_deg
            ),
            "healthy_reference_display_bearing_deg": self._json_float(
                self._healthy_reference_display_bearing_deg
            ),
            "healthy_reference_confidence": self._json_float(
                self._healthy_reference_confidence
            ),
            "healthy_reference_coherence_with_current_u1": self._json_float(coherence),
            "healthy_reference_update_reason": self._healthy_reference_update_reason,
            "healthy_reference_freeze_reason": self._healthy_reference_freeze_reason,
        }

    def _update_healthy_reference_from_chunk(
        self,
        *,
        corrected_chunk: np.ndarray,
        music_internal_deg: float,
        music_bearing_deg: float,
        u1: np.ndarray,
        raw_power_metrics: dict[str, object] | None = None,
        cal_power_metrics: dict[str, object] | None = None,
        covariance_matrix: np.ndarray | None = None,
    ) -> dict[str, object]:
        if not bool(getattr(self._config, "healthy_reference_capture_enabled", True)):
            self._healthy_reference_freeze_reason = "disabled by config"
            return {
                "run_state_label": "unknown",
                "run_state_confidence": 0.0,
                "state_reason": "healthy reference capture disabled",
                "run_state_reason": "healthy reference capture disabled",
                "jammer_confidence_score": 0.0,
                "healthy_confidence_score": 0.0,
                "healthy_reference_updated": False,
                "healthy_reference_update_allowed": False,
                "healthy_reference_freeze_reason": self._healthy_reference_freeze_reason,
                "healthy_reference_freeze_reasons": ["disabled_by_config"],
                "lcmv_safe_baseline": False,
                "pvt_healthy": False,
                "cn0_healthy": False,
                "observations_healthy": False,
                "large_angle_jump": False,
            }
        bridge = self._gnss_bridge
        gnss_snapshot: dict[str, object] = {}
        if bridge is not None:
            try:
                gnss_snapshot = bridge.snapshot()
            except Exception:
                gnss_snapshot = {}
        pvt_status = str(gnss_snapshot.get("pvt_gui_status", "")).upper()
        status_has_fix = "FIX" in pvt_status and "NO_FIX" not in pvt_status
        pvt_current = bool(gnss_snapshot.get("pvt_current", False)) or status_has_fix
        observations = self._optional_int(gnss_snapshot.get("pvt_observation_count"))
        if observations is None:
            observations = self._optional_int(gnss_snapshot.get("pvt_observations"))
        # Some GNSS-SDR PVT monitor packets report valid_sats=0 even while the
        # bridge has current per-satellite used-in-fix evidence.  Do not let
        # that monitor-field defect veto an otherwise healthy jammer-off
        # preservation reference.  The bridge count is derived from the same
        # current PVT/tracking snapshot and is therefore valid corroborating
        # evidence, not a configured or assumed satellite count.
        used_in_fix_count = self._optional_int(
            gnss_snapshot.get("used_in_fix_count")
        )
        if used_in_fix_count is not None and (
            observations is None or used_in_fix_count > observations
        ):
            observations = used_in_fix_count
        avg_cno = self._finite_metric_float(gnss_snapshot.get("avg_tracking_cno_db_hz"))
        if avg_cno is None:
            avg_cno = self._finite_metric_float(gnss_snapshot.get("avg_cno_db_hz"))
        observations_ok = observations is not None and observations >= 6
        cno_ok = avg_cno is not None and avg_cno >= 32.0
        pvt_ok = pvt_current and (status_has_fix or pvt_status == "")
        lcmv_status = self._lcmv_status_copy()
        lcmv_safe_baseline = (
            not self._lcmv_test_enabled
            and str(lcmv_status.get("mode", "off")) == "off"
        )
        healthy_score = float(
            0.35 * int(pvt_ok)
            + 0.25 * int(observations_ok)
            + 0.25 * int(cno_ok)
            + 0.15 * int(lcmv_safe_baseline)
        )
        current_u1 = normalize_complex_vector(u1)
        healthy_vec = (
            normalize_complex_vector(self._healthy_reference_vector)
            if self._healthy_reference_vector is not None
            else np.zeros((0,), dtype=np.complex128)
        )
        coherence = None
        if healthy_vec.size and current_u1.size == healthy_vec.size:
            coherence = abs(complex(np.vdot(healthy_vec, current_u1)))
        jammer_confidence = 0.0
        if healthy_vec.size and coherence is not None:
            jammer_confidence = max(0.0, min(1.0, 1.0 - float(coherence)))
        if avg_cno is not None and avg_cno < 28.0:
            jammer_confidence = max(jammer_confidence, 0.35)
        if observations is not None and observations <= 4:
            jammer_confidence = max(jammer_confidence, 0.35)
        angle_jump_deg = None
        if self._healthy_reference_internal_angle_deg is not None:
            angle_jump_deg = self._angle_distance_deg(
                float(music_internal_deg),
                float(self._healthy_reference_internal_angle_deg),
            )
        large_angle_jump = angle_jump_deg is not None and angle_jump_deg > 45.0
        raw_metrics = raw_power_metrics if isinstance(raw_power_metrics, dict) else {}
        cal_metrics = cal_power_metrics if isinstance(cal_power_metrics, dict) else {}
        raw_power = self._finite_metric_float(
            raw_metrics.get("raw_avg_channel_power_linear")
        )
        cal_power = self._finite_metric_float(
            cal_metrics.get("cal_avg_channel_power_linear")
        )
        raw_power_jump_db = ratio_db(raw_power, self._healthy_reference_raw_power_linear)
        cal_power_jump_db = ratio_db(cal_power, self._healthy_reference_cal_power_linear)
        large_power_jump = any(
            value is not None and value > 6.0
            for value in (raw_power_jump_db, cal_power_jump_db)
        )
        source_diag = dict(self._latest_source_count_diagnostics)
        peak_count = self._optional_int(source_diag.get("peak_count"))
        if large_angle_jump:
            jammer_confidence = max(jammer_confidence, 0.35)
        if large_power_jump:
            jammer_confidence = max(jammer_confidence, 0.55)

        freeze_reasons: list[str] = []
        if not lcmv_safe_baseline:
            freeze_reasons.append("lcmv_active_or_not_safe")
        if not pvt_ok:
            freeze_reasons.append("pvt_not_healthy")
        if not observations_ok:
            freeze_reasons.append(f"observations_unhealthy:{observations}")
        if not cno_ok:
            freeze_reasons.append(f"cn0_unhealthy:{avg_cno}")
        if jammer_confidence >= 0.25:
            freeze_reasons.append(f"jammer_confidence_high:{jammer_confidence:.3f}")
        if large_angle_jump:
            freeze_reasons.append(f"large_angle_jump:{angle_jump_deg:.2f}")
        if large_power_jump:
            freeze_reasons.append("large_power_jump")
        if not current_u1.size:
            freeze_reasons.append("dominant_vector_unavailable")
        healthy_reference_update_allowed = (
            bool(current_u1.size)
            and lcmv_safe_baseline
            and pvt_ok
            and observations_ok
            and cno_ok
            and jammer_confidence < 0.25
            and not large_angle_jump
            and not large_power_jump
        )

        if healthy_reference_update_allowed:
            alpha = 0.10
            if healthy_vec.size == current_u1.size:
                aligned_u1 = phase_align_complex_vector(healthy_vec, current_u1)
                updated = normalize_complex_vector(
                    (1.0 - alpha) * healthy_vec + alpha * aligned_u1
                )
            else:
                updated = current_u1
            self._healthy_reference_vector = updated
            self._healthy_reference_covariance = np.array(
                spatial_covariance(corrected_chunk)
                if covariance_matrix is None
                else covariance_matrix,
                dtype=np.complex128,
                copy=True,
            )
            self._healthy_reference_internal_angle_deg = music_internal_deg
            self._healthy_reference_display_bearing_deg = music_bearing_deg
            self._healthy_reference_updated_monotonic_s = time.monotonic()
            self._healthy_reference_confidence = healthy_score
            if raw_power is not None:
                previous = self._healthy_reference_raw_power_linear
                self._healthy_reference_raw_power_linear = (
                    raw_power if previous is None else (1.0 - alpha) * previous + alpha * raw_power
                )
            if cal_power is not None:
                previous = self._healthy_reference_cal_power_linear
                self._healthy_reference_cal_power_linear = (
                    cal_power if previous is None else (1.0 - alpha) * previous + alpha * cal_power
                )
            self._healthy_reference_update_reason = (
                "PVT FIX, observations, C/N0, and uniform LCMV-OFF baseline looked healthy"
            )
            self._healthy_reference_freeze_reason = ""
            run_state = "healthy_baseline"
            reason = self._healthy_reference_update_reason
            healthy_reference_updated = True
        else:
            self._healthy_reference_freeze_reason = (
                "; ".join(freeze_reasons) or "not healthy enough"
            )
            healthy_reference_updated = False
            if jammer_confidence >= 0.55:
                run_state = "jammer_like_event"
            elif (not lcmv_safe_baseline) and pvt_ok and observations_ok and cno_ok and jammer_confidence < 0.25:
                run_state = "lcmv_on_no_jammer"
            elif healthy_vec.size and healthy_score >= 0.45:
                run_state = "recovery"
            elif not healthy_vec.size:
                run_state = "startup"
            else:
                run_state = "unknown"
            reason = self._healthy_reference_freeze_reason
        return {
            "run_state_label": run_state,
            "run_state_confidence": self._json_float(max(healthy_score, jammer_confidence)),
            "state_reason": reason,
            "run_state_reason": reason,
            "jammer_confidence_score": self._json_float(jammer_confidence),
            "healthy_confidence_score": self._json_float(healthy_score),
            "healthy_score": self._json_float(healthy_score),
            "healthy_reference_updated": bool(healthy_reference_updated),
            "healthy_reference_update_allowed": bool(healthy_reference_update_allowed),
            "healthy_reference_freeze_reason": self._healthy_reference_freeze_reason,
            "healthy_reference_freeze_reasons": freeze_reasons,
            "lcmv_safe_baseline": bool(lcmv_safe_baseline),
            "pvt_healthy": bool(pvt_ok),
            "cn0_healthy": bool(cno_ok),
            "observations_healthy": bool(observations_ok),
            "healthy_reference_observations": observations,
            "healthy_reference_avg_cno_db_hz": self._json_float(avg_cno),
            "healthy_reference_angle_jump_deg": self._json_float(angle_jump_deg),
            "peak_count": peak_count,
            "large_angle_jump": bool(large_angle_jump),
            "large_power_jump": bool(large_power_jump),
            "raw_avg_channel_power_linear": self._json_float(raw_power),
            "cal_avg_channel_power_linear": self._json_float(cal_power),
            "raw_power_jump_db": self._json_float(raw_power_jump_db),
            "cal_power_jump_db": self._json_float(cal_power_jump_db),
            "warning_lcmv_on_while_jammer_confidence_low": bool(
                self._lcmv_test_enabled and jammer_confidence < 0.25
            ),
            "warning_active_null_may_target_healthy_reference": bool(
                healthy_vec.size and coherence is not None and coherence > 0.85
            ),
        }

    def _run_state_payload(
        self,
        *,
        corrected_chunk: np.ndarray,
        music_internal_deg: float,
        music_bearing_deg: float,
        u1: np.ndarray,
        raw_power_metrics: dict[str, object] | None,
        cal_power_metrics: dict[str, object] | None,
        covariance_matrix: np.ndarray | None = None,
    ) -> dict[str, object]:
        if not bool(getattr(self._config, "one_run_segmentation_enabled", True)):
            return {
                "run_state_label": "unknown",
                "run_state_confidence": 0.0,
                "state_reason": "one-run segmentation disabled",
                "run_state_reason": "one-run segmentation disabled",
                "jammer_confidence_score": 0.0,
                "healthy_confidence_score": 0.0,
            }
        return self._update_healthy_reference_from_chunk(
            corrected_chunk=corrected_chunk,
            music_internal_deg=music_internal_deg,
            music_bearing_deg=music_bearing_deg,
            u1=u1,
            raw_power_metrics=raw_power_metrics,
            cal_power_metrics=cal_power_metrics,
            covariance_matrix=covariance_matrix,
        )

    def _lcmv_jammer_activation_evidence(
        self,
        *,
        covariance: np.ndarray,
        raw_power_metrics: dict[str, object] | None,
        cal_power_metrics: dict[str, object] | None,
    ) -> dict[str, object]:
        """Detect a jammer-on change against the exact frozen arm-time baseline.

        Angular movement alone is intentionally not activation evidence.  The
        latch is set only when both input power and a covariance spatial mode
        rise above the frozen jammer-off baseline; once set it remains set until
        the operator disables LCMV.
        """

        with self._results_lock:
            baseline_covariance = (
                np.array(self._realtime_preserve_frozen_covariance, copy=True)
                if self._realtime_preserve_frozen_covariance is not None
                else np.zeros((0, 0), dtype=np.complex128)
            )
            baseline_raw_power = self._realtime_preserve_frozen_raw_power_linear
            baseline_cal_power = self._realtime_preserve_frozen_cal_power_linear
            latched_before = bool(self._lcmv_jammer_detected_latched)

        current = np.asarray(covariance, dtype=np.complex128)
        raw_metrics = raw_power_metrics if isinstance(raw_power_metrics, dict) else {}
        cal_metrics = cal_power_metrics if isinstance(cal_power_metrics, dict) else {}
        current_raw_power = self._finite_metric_float(
            raw_metrics.get("raw_avg_channel_power_linear")
        )
        current_cal_power = self._finite_metric_float(
            cal_metrics.get("cal_avg_channel_power_linear")
        )
        raw_power_jump_db = ratio_db(current_raw_power, baseline_raw_power)
        cal_power_jump_db = ratio_db(current_cal_power, baseline_cal_power)
        covariance_total_power_jump_db = None
        generalized_gain_db = None
        if baseline_covariance.shape == current.shape and current.ndim == 2:
            baseline = 0.5 * (baseline_covariance + baseline_covariance.conj().T)
            current_h = 0.5 * (current + current.conj().T)
            baseline_trace = float(np.trace(baseline).real)
            current_trace = float(np.trace(current_h).real)
            covariance_total_power_jump_db = ratio_db(current_trace, baseline_trace)
            try:
                eigenvalues, eigenvectors = np.linalg.eigh(baseline)
                mean_baseline_power = max(
                    baseline_trace / max(baseline.shape[0], 1),
                    1e-12,
                )
                floor = max(1e-3 * mean_baseline_power, 1e-12)
                inv_sqrt = eigenvectors @ np.diag(
                    1.0 / np.sqrt(np.maximum(eigenvalues.real, floor))
                ) @ eigenvectors.conj().T
                generalized = inv_sqrt @ current_h @ inv_sqrt.conj().T
                max_generalized_gain = float(
                    np.max(np.linalg.eigvalsh(0.5 * (generalized + generalized.conj().T))).real
                )
                generalized_gain_db = power_db(max_generalized_gain)
            except np.linalg.LinAlgError:
                generalized_gain_db = None

        input_jump_candidates = [
            value
            for value in (
                raw_power_jump_db,
                cal_power_jump_db,
                covariance_total_power_jump_db,
            )
            if value is not None
        ]
        input_power_jump_db = (
            max(input_jump_candidates) if input_jump_candidates else None
        )
        min_input_jump_db = float(
            getattr(
                self._config,
                "lcmv_jammer_activation_min_input_power_jump_db",
                3.0,
            )
        )
        min_generalized_gain_db = float(
            getattr(
                self._config,
                "lcmv_jammer_activation_min_generalized_gain_db",
                6.0,
            )
        )
        evidence_now = bool(
            input_power_jump_db is not None
            and generalized_gain_db is not None
            and input_power_jump_db >= min_input_jump_db
            and generalized_gain_db >= min_generalized_gain_db
        )
        if evidence_now and not latched_before:
            with self._results_lock:
                self._lcmv_jammer_detected_latched = True
        latched_after = latched_before or evidence_now
        return {
            "lcmv_jammer_activation_armed": True,
            "lcmv_jammer_activation_evidence_now": evidence_now,
            "lcmv_jammer_detected_latched": latched_after,
            "lcmv_jammer_activation_raw_power_jump_db": self._json_float(
                raw_power_jump_db
            ),
            "lcmv_jammer_activation_cal_power_jump_db": self._json_float(
                cal_power_jump_db
            ),
            "lcmv_jammer_activation_covariance_total_power_jump_db": self._json_float(
                covariance_total_power_jump_db
            ),
            "lcmv_jammer_activation_input_power_jump_db": self._json_float(
                input_power_jump_db
            ),
            "lcmv_jammer_activation_generalized_gain_db": self._json_float(
                generalized_gain_db
            ),
            "lcmv_jammer_activation_min_input_power_jump_db": self._json_float(
                min_input_jump_db
            ),
            "lcmv_jammer_activation_min_generalized_gain_db": self._json_float(
                min_generalized_gain_db
            ),
            "lcmv_jammer_activation_angle_only_forbidden": True,
        }

    def _update_healthy_reference_tracking_from_music(
        self,
        *,
        corrected_chunk: np.ndarray,
        music_internal_deg: float,
        music_bearing_deg: float,
        raw_power_metrics: dict[str, object] | None = None,
        cal_power_metrics: dict[str, object] | None = None,
        covariance_matrix: np.ndarray | None = None,
        covariance_eigenvectors: np.ndarray | None = None,
    ) -> dict[str, object]:
        """Track and log the healthy reference while the active combiner is uniform."""

        if self._lcmv_test_enabled:
            return {}
        corrected = np.asarray(corrected_chunk, dtype=np.complex128)
        covariance = (
            spatial_covariance(corrected)
            if covariance_matrix is None
            else np.asarray(covariance_matrix, dtype=np.complex128)
        )
        if covariance_eigenvectors is None:
            _, eigenvectors = covariance_eigendecomposition_from_matrix(covariance)
        else:
            eigenvectors = np.asarray(
                covariance_eigenvectors,
                dtype=np.complex128,
            )
        u1 = (
            np.asarray(eigenvectors[:, 0], dtype=np.complex128).reshape(-1)
            if eigenvectors.ndim == 2 and eigenvectors.shape[1] > 0
            else np.zeros((0,), dtype=np.complex128)
        )
        run_state = self._run_state_payload(
            corrected_chunk=corrected,
            music_internal_deg=music_internal_deg,
            music_bearing_deg=music_bearing_deg,
            u1=u1,
            raw_power_metrics=raw_power_metrics,
            cal_power_metrics=cal_power_metrics,
            covariance_matrix=covariance,
        )
        heavy_diag = self._lcmv_heavy_diagnostics_decision()
        uniform_w = uniform_weights(corrected.shape[0])
        current_output_power = covariance_output_power(
            covariance=covariance,
            weights=uniform_w,
        )
        healthy_baseline_output_power = covariance_output_power(
            covariance=self._healthy_reference_covariance,
            weights=uniform_w,
        )
        payload = {
            "event": "spatial_vector_diagnostics",
            "sequence": int(self._spatial_diag_seq),
            "sample_count": int(corrected.shape[1]) if corrected.ndim == 2 else 0,
            "channel_count": int(corrected.shape[0]) if corrected.ndim == 2 else 0,
            "music_internal_angle_deg": self._json_float(music_internal_deg),
            "music_display_bearing_deg": self._json_float(music_bearing_deg),
            "active_lcmv_method": "uniform_array_sum",
            "active_lcmv_null_method": "none",
            "active_lcmv_weights_source": "uniform_array_sum",
            "active_method_requested": self._lcmv_test_null_method,
            "active_method_applied": "uniform_array_sum",
            "candidate_methods_computed": [],
            "candidate_methods_valid": [],
            "candidate_methods_rejected": {},
            "active_total_output_power_from_R": self._json_float(
                current_output_power
            ),
            "active_healthy_baseline_output_power_from_R": self._json_float(
                healthy_baseline_output_power
            ),
            "measured_dominant_eigenvector_u1_norm": complex_vector_payload(
                normalize_complex_vector(u1)
            ),
            **self._calibration_context_payload(),
            **heavy_diag,
            **self._realtime_preserve_tracker_payload(),
            **self._healthy_reference_payload(u1),
            **run_state,
        }
        self._spatial_diag_seq += 1
        if bool(heavy_diag.get("heavy_diagnostics_emitted", False)):
            self._log_spatial_vector_diagnostics(payload)
        self._set_lcmv_status(
            enabled=False,
            mode="off",
            run_state_label=str(run_state.get("run_state_label", "unknown")),
            jammer_confidence_score=run_state.get("jammer_confidence_score"),
            healthy_confidence_score=run_state.get("healthy_confidence_score"),
            spatial_vector_diagnostics=payload,
            heavy_diagnostics_interval_s=heavy_diag.get("heavy_diagnostics_interval_s"),
            heavy_diagnostics_emitted=bool(
                heavy_diag.get("heavy_diagnostics_emitted", False)
            ),
            heavy_diagnostics_skipped_due_to_throttle=bool(
                heavy_diag.get("heavy_diagnostics_skipped_due_to_throttle", False)
            ),
            last_heavy_diagnostics_age_s=heavy_diag.get(
                "last_heavy_diagnostics_age_s"
            ),
        )
        with self._results_lock:
            self._latest_spatial_vector_diagnostics = dict(payload)
        auto_armed = self._maybe_auto_arm_lcmv_after_pvt(run_state)
        payload["lcmv_auto_arm_after_pvt"] = bool(
            self._lcmv_auto_arm_after_pvt
        )
        payload["lcmv_auto_arm_suppressed_by_operator"] = bool(
            self._lcmv_auto_arm_suppressed_by_operator
        )
        payload["lcmv_auto_arm_triggered"] = bool(auto_armed)
        if auto_armed:
            with self._results_lock:
                self._latest_spatial_vector_diagnostics = dict(payload)
        return payload

    def _maybe_auto_arm_lcmv_after_pvt(
        self,
        run_state: dict[str, object],
    ) -> bool:
        """Freeze the healthy reference and arm LCMV once per live run.

        Arming leaves uniform weights on the GNSS stream. The existing jammer
        evidence latch remains solely responsible for allowing null weights to
        become active.
        """

        if (
            not self._lcmv_auto_arm_after_pvt
            or self._lcmv_auto_arm_suppressed_by_operator
            or self._lcmv_test_enabled
            or not self._shared_phase_fanout_enabled()
            or str(
                getattr(
                    self._config,
                    "lcmv_preserve_constraint_mode",
                    "uniform",
                )
            ).strip().lower()
            != "realtime_bladerf_measured_u1"
            or not bool(run_state.get("healthy_reference_updated", False))
            or not bool(run_state.get("pvt_healthy", False))
            or not bool(run_state.get("observations_healthy", False))
            or not bool(run_state.get("cn0_healthy", False))
        ):
            return False

        now = time.monotonic()
        channel_count = len(self._config.channels)
        max_age_s = max(
            0.0,
            float(
                getattr(
                    self._config,
                    "lcmv_realtime_preserve_max_reference_age_s",
                    2.0,
                )
            ),
        )
        max_angle_error_deg = max(
            0.0,
            float(
                getattr(
                    self._config,
                    "lcmv_realtime_preserve_guard_deg",
                    20.0,
                )
            ),
        )
        with self._results_lock:
            center = self._realtime_preserve_center_internal_deg
            reference_angle = self._healthy_reference_internal_angle_deg
            reference_age_s = (
                now - self._healthy_reference_updated_monotonic_s
                if self._healthy_reference_updated_monotonic_s is not None
                else None
            )
            angle_error_deg = (
                self._angle_distance_deg(center, reference_angle)
                if center is not None and reference_angle is not None
                else None
            )
            vector_ready = bool(
                self._healthy_reference_vector is not None
                and np.asarray(self._healthy_reference_vector).size == channel_count
            )
            covariance_ready = bool(
                self._healthy_reference_covariance is not None
                and np.asarray(self._healthy_reference_covariance).shape
                == (channel_count, channel_count)
            )
            ready = bool(
                self._realtime_preserve_stable
                and center is not None
                and vector_ready
                and covariance_ready
                and reference_age_s is not None
                and 0.0 <= reference_age_s <= max_age_s
                and angle_error_deg is not None
                and angle_error_deg <= max_angle_error_deg
                and self._healthy_reference_confidence >= 0.8
            )
        if not ready:
            return False

        self._lcmv_log.info(
            "lcmv_auto_arm decision=arm_after_healthy_pvt "
            "output_before_arm=uniform_array_sum pvt_healthy=true "
            "observations_healthy=true cn0_healthy=true reference_age_s=%.3f "
            "reference_angle_error_deg=%.3f",
            float(reference_age_s),
            float(angle_error_deg),
        )
        self.set_lcmv_test_enabled(True, source="auto_after_pvt")
        return bool(self._lcmv_test_enabled)

    def _spatial_vector_diagnostics_payload(
        self,
        *,
        corrected_chunk: np.ndarray,
        music_internal_deg: float,
        music_bearing_deg: float,
        ideal_result: object,
        candidate_entries: dict[str, dict[str, object]] | None,
        active_weights: np.ndarray,
        active_lcmv_method: str,
        active_lcmv_null_method: str,
        active_lcmv_weights_source: str,
        active_lcmv_fallback_reason: str,
        active_lcmv_fallback_used: bool,
        target_angle_policy: dict[str, object] | None = None,
        run_state_payload: dict[str, object] | None = None,
        heavy_diagnostics_payload: dict[str, object] | None = None,
        raw_power_metrics: dict[str, object] | None = None,
        cal_power_metrics: dict[str, object] | None = None,
        covariance_matrix: np.ndarray | None = None,
        covariance_eigenvalues: np.ndarray | None = None,
        covariance_eigenvectors: np.ndarray | None = None,
    ) -> dict[str, object]:
        corrected = np.asarray(corrected_chunk, dtype=np.complex128)
        covariance = (
            spatial_covariance(corrected)
            if covariance_matrix is None
            else np.asarray(covariance_matrix, dtype=np.complex128)
        )
        if covariance_eigenvalues is None or covariance_eigenvectors is None:
            eigenvalues, eigenvectors = covariance_eigendecomposition_from_matrix(
                covariance
            )
        else:
            eigenvalues = np.asarray(covariance_eigenvalues, dtype=np.float64)
            eigenvectors = np.asarray(covariance_eigenvectors, dtype=np.complex128)
        if eigenvectors.ndim == 2 and eigenvectors.shape[1] > 0:
            u1 = np.asarray(eigenvectors[:, 0], dtype=np.complex128).reshape(-1)
        else:
            u1 = np.zeros((0,), dtype=np.complex128)
        ideal_vector = np.asarray(
            steering_vector(
                np.asarray([music_internal_deg], dtype=np.float64),
                self._config.center_freq_hz,
                self._config.array_spacing_m,
            ),
            dtype=np.complex128,
        ).reshape(-1)
        ideal_norm = normalize_complex_vector(ideal_vector)
        u1_norm = normalize_complex_vector(u1)
        uniform_w = uniform_weights(corrected.shape[0])
        ideal_w = np.asarray(getattr(ideal_result, "weights", []), dtype=np.complex128)
        active_w = np.asarray(active_weights, dtype=np.complex128).reshape(-1)
        uniform_avg_w = uniform_w / float(max(uniform_w.size, 1))
        raw_metrics = raw_power_metrics if isinstance(raw_power_metrics, dict) else {}
        cal_metrics = cal_power_metrics if isinstance(cal_power_metrics, dict) else {}
        healthy_norm = (
            normalize_complex_vector(self._healthy_reference_vector)
            if self._healthy_reference_vector is not None
            else np.zeros((0,), dtype=np.complex128)
        )
        entries = candidate_entries if isinstance(candidate_entries, dict) else {}
        candidate_methods_computed = list(entries)
        candidate_methods_valid: list[str] = []
        candidate_methods_rejected: dict[str, object] = {}

        lambda1 = (
            self._json_float(eigenvalues[0])
            if np.asarray(eigenvalues).size
            else None
        )
        ideal_component_power = covariance_output_power(
            covariance=covariance,
            weights=ideal_norm,
        )
        uniform_output_power = covariance_output_power(
            covariance=covariance,
            weights=uniform_w,
        )
        ideal_output_power = covariance_output_power(
            covariance=covariance,
            weights=ideal_w,
        )
        active_output_power = covariance_output_power(
            covariance=covariance,
            weights=active_w,
        )
        active_healthy_baseline_output_power = covariance_output_power(
            covariance=self._healthy_reference_covariance,
            weights=active_w,
        )

        payload: dict[str, object] = {
            "event": "spatial_vector_diagnostics",
            "sequence": int(self._spatial_diag_seq),
            "sample_count": int(corrected.shape[1]) if corrected.ndim == 2 else 0,
            "channel_count": int(corrected.shape[0]) if corrected.ndim == 2 else 0,
            "center_freq_hz": self._json_float(self._config.center_freq_hz),
            "array_spacing_m": self._json_float(self._config.array_spacing_m),
            "music_internal_angle_deg": self._json_float(music_internal_deg),
            "music_display_bearing_deg": self._json_float(music_bearing_deg),
            "null_internal_angle_deg": self._json_float(music_internal_deg),
            "null_display_bearing_deg": self._json_float(music_bearing_deg),
            "steering_vector_angle_used_internal_deg": self._json_float(music_internal_deg),
            "steering_vector_angle_used_display_deg": self._json_float(music_bearing_deg),
            "display_bearing_formula": "(90 - internal_angle_deg) % 360",
            "active_lcmv_method": active_lcmv_method,
            "active_lcmv_null_method": active_lcmv_null_method,
            "active_lcmv_weights_source": active_lcmv_weights_source,
            "active_lcmv_fallback_used": bool(active_lcmv_fallback_used),
            "active_lcmv_fallback_reason": active_lcmv_fallback_reason,
            **dict(heavy_diagnostics_payload or {}),
            "active_method_requested": self._lcmv_test_null_method,
            "active_method_applied": active_lcmv_method,
            "active_method_rejected": bool(active_lcmv_fallback_used),
            "active_method_rejection_reason": active_lcmv_fallback_reason,
            "fallback_method": "uniform_array_sum" if active_lcmv_fallback_used else "",
            **dict(target_angle_policy or {}),
            "configured_lcmv_null_method": self._lcmv_test_null_method,
            "preserve_convention": str(
                (target_angle_policy or {}).get(
                    "lcmv_preserve_constraint_mode",
                    "uniform",
                )
            ),
            "preserve_reference": (
                "uniform_combiner_complex_response_at_frozen_measured_bladerf_u1"
                if str(
                    (target_angle_policy or {}).get(
                        "lcmv_preserve_constraint_mode",
                        "uniform",
                    )
                )
                == "realtime_bladerf_measured_u1"
                else "uniform_combiner_complex_response"
            ),
            "active_preserve_vector": complex_vector_payload(
                getattr(ideal_result, "preserve_vector", np.zeros((0,), dtype=np.complex128))
            ),
            "common_signal_model": (
                "x[n]=desired/SOI + jammer + real sky GNSS + noise + multipath + receiver artifacts"
            ),
            "covariance_warning": "R is not jammer-only; u1 is not always jammer",
            "u1_suppression_warning": (
                "u1 suppression means jammer-like suppression only during jammer-like windows; "
                "during healthy/no-jammer windows u1 suppression may represent desired/SOI suppression"
            ),
            "total_output_reduction_warning": (
                "Total output reduction is not jammer-only suppression"
            ),
            **self._calibration_context_payload(),
            "raw_power_spread_db": self._json_float(raw_metrics.get("raw_power_spread_db")),
            "cal_power_spread_db": self._json_float(cal_metrics.get("cal_power_spread_db")),
            "ideal_steering_vector": complex_vector_payload(ideal_vector),
            "ideal_steering_vector_norm": complex_vector_payload(ideal_norm),
            "measured_dominant_eigenvector_u1": complex_vector_payload(u1),
            "measured_dominant_eigenvector_u1_norm": complex_vector_payload(u1_norm),
            "eigenvalues_linear": self._json_float_list(eigenvalues),
            "eigenvalues_db": self._json_float_list(
                10.0 * np.log10(np.maximum(eigenvalues, 1e-300))
                if np.asarray(eigenvalues).size
                else []
            ),
            "lambda1_power_linear": lambda1,
            "lambda1_power_db": power_db(lambda1),
            "ideal_steering_component_power_linear": self._json_float(
                ideal_component_power
            ),
            "ideal_steering_component_power_db": power_db(ideal_component_power),
            "uniform_weights": complex_vector_payload(uniform_w),
            "uniform_sum_weights": complex_vector_payload(uniform_w),
            "uniform_average_weights": complex_vector_payload(uniform_avg_w),
            "ideal_lcmv_weights": complex_vector_payload(ideal_w),
            "active_lcmv_weights": complex_vector_payload(active_w),
            "measured_covariance_output_power_uniform_linear": self._json_float(
                uniform_output_power
            ),
            "measured_covariance_output_power_uniform_db": power_db(uniform_output_power),
            "measured_covariance_output_power_ideal_lcmv_linear": self._json_float(
                ideal_output_power
            ),
            "measured_covariance_output_power_ideal_lcmv_db": power_db(ideal_output_power),
            "measured_covariance_output_power_active_lcmv_linear": self._json_float(
                active_output_power
            ),
            "measured_covariance_output_power_active_lcmv_db": power_db(active_output_power),
            "active_total_output_power_from_R": self._json_float(active_output_power),
            "active_healthy_baseline_output_power_from_R": self._json_float(
                active_healthy_baseline_output_power
            ),
            "measured_covariance_reduction_uniform_to_ideal_lcmv_db": ratio_db(
                uniform_output_power,
                ideal_output_power,
            ),
            "measured_covariance_reduction_uniform_to_active_lcmv_db": ratio_db(
                uniform_output_power,
                active_output_power,
            ),
        }
        payload.update(self._healthy_reference_payload(u1_norm))
        if isinstance(run_state_payload, dict):
            payload.update(run_state_payload)
        jammer_latched = bool(
            payload.get("lcmv_jammer_detected_latched", False)
            or (target_angle_policy or {}).get(
                "lcmv_target_confirmed_jammer_bearing",
                False,
            )
        )
        payload.update(
            self._jammer_excess_covariance_payload(
                current_covariance=covariance,
                uniform_weights_vector=uniform_w,
                target_weights=active_w,
                jammer_latched=jammer_latched,
            )
        )
        active_method_payload: dict[str, object] = {}
        active_method_prefix = ""
        for method_name, entry in entries.items():
            prefix = str(entry.get("prefix") or f"candidate_{method_name}")
            result = entry.get("result")
            error = str(entry.get("error") or "")
            method_payload, rejection = self._candidate_method_payload(
                prefix=prefix,
                result=result,
                error=error,
                covariance=covariance,
                reference_weights=uniform_w,
                uniform_sum_weights=uniform_w,
                uniform_average_weights=uniform_avg_w,
                ideal_vector=ideal_norm,
                u1_vector=u1_norm,
                healthy_reference_vector=healthy_norm,
            )
            payload.update(method_payload)
            if bool(method_payload.get(f"{prefix}_valid")):
                candidate_methods_valid.append(method_name)
            else:
                candidate_methods_rejected[method_name] = rejection or error or "invalid"
            if method_name == active_lcmv_method:
                active_method_payload = dict(method_payload)
                active_method_prefix = prefix
        payload["candidate_methods_computed"] = candidate_methods_computed
        payload["candidate_methods_valid"] = candidate_methods_valid
        payload["candidate_methods_rejected"] = candidate_methods_rejected
        covariance_ideal = entries.get("covariance_lcmv_ideal", {})
        covariance_ideal_result = covariance_ideal.get("result")
        payload["covariance_lcmv_ideal_preserve_residual_abs"] = self._json_float(
            abs(getattr(covariance_ideal_result, "preserve_residual", np.nan))
        )
        payload["covariance_lcmv_ideal_null_residual_abs"] = self._json_float(
            abs(getattr(covariance_ideal_result, "null_residual", np.nan))
        )
        if active_method_payload and active_method_prefix:
            for metric in (
                "valid",
                "rejected_reason",
                "ideal_component_reduction_vs_reference_db",
                "u1_component_reduction_vs_reference_db",
                "dominant_vector_suppression_db",
                "total_output_reduction_vs_reference_db",
                "desired_loss_vs_reference_db",
                "white_noise_gain_db",
                "noise_gain_vs_reference_db",
                "effective_js_improvement_u1_db",
                "effective_receiver_improvement_u1_db",
            ):
                payload[f"active_{metric}"] = active_method_payload.get(
                    f"{active_method_prefix}_{metric}"
                )
            payload["dominant_vector_suppression_db"] = active_method_payload.get(
                f"{active_method_prefix}_dominant_vector_suppression_db"
            )
        payload.update(spatial_vector_coherence_metrics(ideal_norm, u1_norm))
        payload.update(
            self._vector_response_payload(
                prefix="uniform_to_ideal_steering",
                weights=uniform_w,
                vector=ideal_norm,
                component_power_before=ideal_component_power,
            )
        )
        payload.update(
            self._vector_response_payload(
                prefix="ideal_lcmv_to_ideal_steering",
                weights=ideal_w,
                vector=ideal_norm,
                component_power_before=ideal_component_power,
            )
        )
        payload.update(
            self._vector_response_payload(
                prefix="active_lcmv_to_ideal_steering",
                weights=active_w,
                vector=ideal_norm,
                component_power_before=ideal_component_power,
            )
        )
        payload.update(
            self._vector_response_payload(
                prefix="uniform_to_u1",
                weights=uniform_w,
                vector=u1_norm,
                component_power_before=lambda1,
            )
        )
        payload.update(
            self._vector_response_payload(
                prefix="ideal_lcmv_to_u1",
                weights=ideal_w,
                vector=u1_norm,
                component_power_before=lambda1,
            )
        )
        payload.update(
            self._vector_response_payload(
                prefix="active_lcmv_to_u1",
                weights=active_w,
                vector=u1_norm,
                component_power_before=lambda1,
            )
        )
        payload.update(
            self._lcmv_result_payload(prefix="ideal_lcmv", result=ideal_result)
        )
        return payload

    def _vector_response_payload(
        self,
        *,
        prefix: str,
        weights: np.ndarray,
        vector: np.ndarray,
        component_power_before: float | None,
    ) -> dict[str, object]:
        metrics = component_power_after_beamformer(
            component_power_before=component_power_before,
            weights=weights,
            vector=vector,
        )
        return {f"{prefix}_{key}": value for key, value in metrics.items()}

    def _lcmv_result_payload(self, *, prefix: str, result: object) -> dict[str, object]:
        return {
            f"{prefix}_condition_number": self._json_float(
                getattr(result, "condition_number", None)
            ),
            f"{prefix}_weight_norm": self._json_float(getattr(result, "weight_norm", None)),
            f"{prefix}_max_weight_abs": self._json_float(
                getattr(result, "max_weight_abs", None)
            ),
            f"{prefix}_preserve_target": self._complex_scalar_payload(
                getattr(result, "preserve_target", None)
            ),
            f"{prefix}_preserve_response": self._complex_scalar_payload(
                getattr(result, "preserve_response", None)
            ),
            f"{prefix}_null_response": self._complex_scalar_payload(
                getattr(result, "null_response", None)
            ),
            f"{prefix}_preserve_residual_abs": self._json_float(
                abs(getattr(result, "preserve_residual", np.nan))
            ),
            f"{prefix}_null_residual_abs": self._json_float(
                abs(getattr(result, "null_residual", np.nan))
            ),
        }

    def _log_spatial_vector_diagnostics(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        self._spatial_vector_log.info("%s", line)

    def _update_lcmv_test_from_music(
        self,
        calibrated_chunk: np.ndarray,
        music_internal_deg: float,
        music_bearing_deg: float,
        raw_power_metrics: dict[str, object] | None = None,
        cal_power_metrics: dict[str, object] | None = None,
        target_selection_source: str = "strongest_music_peak",
        covariance_matrix: np.ndarray | None = None,
        covariance_eigenvalues: np.ndarray | None = None,
        covariance_eigenvectors: np.ndarray | None = None,
    ) -> None:
        if not self._lcmv_test_enabled:
            return

        music_internal = self._finite_metric_float(music_internal_deg)
        music_bearing = self._finite_metric_float(music_bearing_deg)
        if music_internal is None or music_bearing is None:
            self._activate_lcmv_test_fallback(
                "no valid MUSIC bearing available",
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
            )
            return

        target_angle_policy = self._lcmv_target_angle_policy(music_bearing)
        preserve_mode = str(
            getattr(self._config, "lcmv_preserve_constraint_mode", "uniform")
        ).strip().lower()
        target_angle_policy.update(
            {
                "lcmv_target_selection_mode": str(
                    getattr(
                        self._config,
                        "lcmv_target_selection_mode",
                        "strongest_music_peak",
                    )
                ),
                "lcmv_target_selection_source": str(target_selection_source),
                "lcmv_preserve_constraint_mode": preserve_mode,
            }
        )
        if preserve_mode == "realtime_bladerf_measured_u1":
            tracker_payload = self._realtime_preserve_tracker_payload()
            target_angle_policy.update(tracker_payload)
            frozen_internal = self._finite_metric_float(
                tracker_payload.get("realtime_bladerf_frozen_internal_deg")
            )
            frozen_display = self._finite_metric_float(
                tracker_payload.get("realtime_bladerf_frozen_display_deg")
            )
            guard_deg = max(
                0.0,
                float(
                    getattr(
                        self._config,
                        "lcmv_realtime_preserve_guard_deg",
                        20.0,
                    )
                ),
            )
            separation = (
                self._angle_distance_deg(music_internal, frozen_internal)
                if frozen_internal is not None
                else None
            )
            target_angle_policy.update(
                {
                    "expected_bladeRF_bearing_min": None,
                    "expected_bladeRF_bearing_max": None,
                    "lcmv_preserve_internal_angle_deg": self._json_float(
                        frozen_internal
                    ),
                    "lcmv_preserve_display_bearing_deg": self._json_float(
                        frozen_display
                    ),
                    "lcmv_realtime_preserve_guard_deg": self._json_float(guard_deg),
                    "lcmv_target_separation_from_preserve_deg": self._json_float(
                        separation
                    ),
                    "lcmv_target_classification": (
                        "realtime_non_preserve_peak"
                        if separation is not None and separation >= guard_deg
                        else "realtime_preserve_guard"
                    ),
                    "lcmv_target_confirmed_jammer_bearing": bool(
                        separation is not None and separation >= guard_deg
                    ),
                    "lcmv_target_protected_bladeRF_bearing": bool(
                        separation is None or separation < guard_deg
                    ),
                }
            )
        if bool(target_angle_policy.get("lcmv_target_protected_bladeRF_bearing", False)):
            if preserve_mode == "realtime_bladerf_measured_u1":
                self._activate_lcmv_test_fallback(
                    (
                        "MUSIC null target is inside the frozen realtime bladeRF "
                        f"guard ({target_angle_policy.get('lcmv_realtime_preserve_guard_deg')} deg)"
                    ),
                    music_internal_deg=music_internal,
                    music_bearing_deg=music_bearing,
                )
                return
            blade_min = target_angle_policy.get("expected_bladeRF_bearing_min")
            blade_max = target_angle_policy.get("expected_bladeRF_bearing_max")
            self._activate_lcmv_test_fallback(
                (
                    f"MUSIC target display bearing {music_bearing:.2f} is inside protected "
                    f"bladeRF range {blade_min}-{blade_max} and outside expected jammer range"
                ),
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
            )
            return

        try:
            corrected = np.asarray(calibrated_chunk, dtype=np.complex128)
            expected_channels = len(self._config.channels)
            if corrected.ndim != 2:
                raise ValueError(f"LCMV test chunk shape is not [channels, samples]: {corrected.shape}")
            if corrected.shape[0] != expected_channels:
                raise ValueError(
                    "wrong channel count for LCMV test: "
                    f"chunk={corrected.shape[0]} expected={expected_channels}"
                )
            if expected_channels != 4:
                raise ValueError(
                    f"wrong channel count for LCMV test: expected fixed 4-channel array, got {expected_channels}"
                )
            correction_vector = self._config.phase_correction_vector
            if correction_vector is not None:
                correction = np.asarray(correction_vector, dtype=np.complex128).reshape(-1)
                if correction.size != expected_channels:
                    raise ValueError(
                        "phase correction length mismatch for LCMV test: "
                        f"correction={correction.size} channels={expected_channels}"
                    )

            covariance = (
                spatial_covariance(corrected)
                if covariance_matrix is None
                else np.asarray(covariance_matrix, dtype=np.complex128)
            )
            if covariance_eigenvalues is None or covariance_eigenvectors is None:
                eigenvalues, eigenvectors = covariance_eigendecomposition_from_matrix(
                    covariance
                )
            else:
                eigenvalues = np.asarray(covariance_eigenvalues, dtype=np.float64)
                eigenvectors = np.asarray(covariance_eigenvectors, dtype=np.complex128)
            u1 = (
                np.asarray(eigenvectors[:, 0], dtype=np.complex128).reshape(-1)
                if eigenvectors.ndim == 2 and eigenvectors.shape[1] > 0
                else np.zeros((0,), dtype=np.complex128)
            )
            run_state_payload = self._run_state_payload(
                corrected_chunk=corrected,
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
                u1=u1,
                raw_power_metrics=raw_power_metrics,
                cal_power_metrics=cal_power_metrics,
                covariance_matrix=covariance,
            )
            if preserve_mode == "realtime_bladerf_measured_u1":
                activation_payload = self._lcmv_jammer_activation_evidence(
                    covariance=covariance,
                    raw_power_metrics=raw_power_metrics,
                    cal_power_metrics=cal_power_metrics,
                )
                run_state_payload.update(activation_payload)
                target_angle_policy.update(activation_payload)
                jammer_latched = bool(
                    activation_payload.get("lcmv_jammer_detected_latched", False)
                )
                target_angle_policy["lcmv_target_confirmed_jammer_bearing"] = (
                    jammer_latched
                )
                if not jammer_latched:
                    target_angle_policy["lcmv_target_classification"] = (
                        "unconfirmed_non_preserve_peak_while_armed"
                    )
                    input_jump = self._format_optional_float(
                        activation_payload.get(
                            "lcmv_jammer_activation_input_power_jump_db"
                        )
                    )
                    generalized_gain = self._format_optional_float(
                        activation_payload.get(
                            "lcmv_jammer_activation_generalized_gain_db"
                        )
                    )
                    reason = (
                        "armed with frozen measured bladeRF U1; uniform output while "
                        "waiting for jammer evidence "
                        f"(input_power_jump_db={input_jump}, "
                        f"generalized_gain_db={generalized_gain})"
                    )
                    armed_diag = {
                        "event": "spatial_vector_diagnostics",
                        "sequence": int(self._spatial_diag_seq),
                        "sample_count": int(corrected.shape[1]),
                        "channel_count": int(corrected.shape[0]),
                        "music_internal_angle_deg": self._json_float(music_internal),
                        "music_display_bearing_deg": self._json_float(music_bearing),
                        "active_lcmv_method": "uniform_array_sum",
                        "active_lcmv_null_method": "none_armed_waiting_for_jammer",
                        "active_lcmv_weights_source": "uniform_array_sum",
                        "active_method_requested": self._lcmv_test_null_method,
                        "active_method_applied": "uniform_array_sum",
                        "active_method_rejection_reason": reason,
                        **target_angle_policy,
                        **self._healthy_reference_payload(u1),
                        **run_state_payload,
                    }
                    self._spatial_diag_seq += 1
                    self._activate_lcmv_test_fallback(
                        reason,
                        music_internal_deg=music_internal,
                        music_bearing_deg=music_bearing,
                        spatial_vector_diagnostics=armed_diag,
                        run_state_label=str(
                            run_state_payload.get("run_state_label", "armed")
                        ),
                        jammer_confidence_score=run_state_payload.get(
                            "jammer_confidence_score"
                        ),
                        healthy_confidence_score=run_state_payload.get(
                            "healthy_confidence_score"
                        ),
                    )
                    return
            max_weight_norm = self._lcmv_weight_norm_limit()
            condition_limit = float(self._config.lcmv_test_condition_number_limit)
            covariance_loading_rel = float(
                getattr(self._config, "lcmv_covariance_diagonal_loading_rel", 0.001)
            )
            covariance_loading_abs = float(
                getattr(self._config, "lcmv_covariance_diagonal_loading_abs", 0.0)
            )
            candidate_entries: dict[str, dict[str, object]] = {}
            candidate_methods_enabled = bool(
                getattr(self._config, "lcmv_candidate_methods_enabled", True)
            )
            healthy_norm = (
                normalize_complex_vector(self._healthy_reference_vector)
                if self._healthy_reference_vector is not None
                else np.zeros((0,), dtype=np.complex128)
            )
            if preserve_mode == "healthy_reference":
                if healthy_norm.size != expected_channels:
                    raise ValueError(
                        "healthy-reference preserve constraint unavailable; "
                        "capture a jammer-off uniform PVT baseline before enabling LCMV"
                    )
                preserve_vector = healthy_norm
            elif preserve_mode == "uniform":
                preserve_vector = np.ones((expected_channels,), dtype=np.complex128)
            elif preserve_mode == "realtime_bladerf_measured_u1":
                with self._results_lock:
                    preserve_internal = self._realtime_preserve_frozen_internal_deg
                    frozen_preserve_vector = (
                        np.array(self._realtime_preserve_frozen_vector, copy=True)
                        if self._realtime_preserve_frozen_vector is not None
                        else np.zeros((0,), dtype=np.complex128)
                    )
                if (
                    preserve_internal is None
                    or frozen_preserve_vector.size != expected_channels
                ):
                    raise ValueError(
                        "measured bladeRF preserve vector unavailable; keep LCMV off "
                        "until the jammer-off angle cluster, PVT, and U1 are stable"
                    )
                preserve_vector = frozen_preserve_vector
            else:
                raise ValueError(f"unsupported LCMV preserve mode: {preserve_mode}")

            covariance_ideal_result = None
            covariance_ideal_error = ""
            compute_covariance_ideal = (
                candidate_methods_enabled
                or self._lcmv_test_null_method == "covariance_lcmv_ideal"
            )
            if compute_covariance_ideal:
                try:
                    covariance_ideal_result = covariance_lcmv_ideal_null_weights(
                        covariance=covariance,
                        n_channels=expected_channels,
                        null_angle_deg=music_internal,
                        rf_freq_hz=self._config.center_freq_hz,
                        array_spacing_m=self._config.array_spacing_m,
                        preserve_vector=preserve_vector,
                        diagonal_loading_rel=covariance_loading_rel,
                        diagonal_loading_abs=covariance_loading_abs,
                        condition_number_limit=condition_limit,
                        max_weight_norm=max_weight_norm,
                    )
                except Exception as exc:
                    covariance_ideal_error = str(exc)
                candidate_entries["covariance_lcmv_ideal"] = {
                    "prefix": "candidate_covariance_lcmv_ideal",
                    "result": covariance_ideal_result,
                    "error": covariance_ideal_error,
                }

            covariance_u1_result = None
            covariance_u1_error = ""
            compute_covariance_u1 = (
                candidate_methods_enabled
                or self._lcmv_test_null_method == "covariance_lcmv_measured_u1"
            )
            if compute_covariance_u1 and u1.size:
                try:
                    covariance_u1_result = covariance_lcmv_vector_null_weights(
                        covariance=covariance,
                        null_vector=u1,
                        preserve_vector=preserve_vector,
                        diagonal_loading_rel=covariance_loading_rel,
                        diagonal_loading_abs=covariance_loading_abs,
                        condition_number_limit=condition_limit,
                        max_weight_norm=max_weight_norm,
                    )
                except Exception as exc:
                    covariance_u1_error = str(exc)
            elif compute_covariance_u1:
                covariance_u1_error = "dominant covariance eigenvector unavailable"
            if compute_covariance_u1:
                candidate_entries["covariance_lcmv_measured_u1"] = {
                    "prefix": "candidate_covariance_lcmv_measured_u1",
                    "result": covariance_u1_result,
                    "error": covariance_u1_error,
                }

            ideal_vector = np.asarray(
                steering_vector(
                    np.asarray([music_internal], dtype=np.float64),
                    self._config.center_freq_hz,
                    self._config.array_spacing_m,
                ),
                dtype=np.complex128,
            ).reshape(-1)
            ideal_norm = normalize_complex_vector(ideal_vector)
            u1_norm = normalize_complex_vector(u1)
            uniform_w = uniform_weights(expected_channels)
            def _preflight_rejection(
                method_name: str,
                result: object | None,
                *,
                enforce_white_noise_gain: bool = True,
            ) -> str:
                weights = np.asarray(
                    getattr(result, "weights", []),
                    dtype=np.complex128,
                ).reshape(-1)
                healthy_ref_power = self._vector_response_power(uniform_w, healthy_norm)
                healthy_method_power = self._vector_response_power(weights, healthy_norm)
                ideal_ref_power = self._vector_response_power(uniform_w, ideal_norm)
                ideal_method_power = self._vector_response_power(weights, ideal_norm)
                u1_ref_power = self._vector_response_power(uniform_w, u1_norm)
                u1_method_power = self._vector_response_power(weights, u1_norm)
                if "u1" in method_name or "measured" in method_name:
                    target_suppression_db = ratio_db(u1_ref_power, u1_method_power)
                else:
                    target_suppression_db = ratio_db(ideal_ref_power, ideal_method_power)
                return self._method_safety_rejection_reason(
                    result=result,
                    target_suppression_db=target_suppression_db,
                    enforce_white_noise_gain=enforce_white_noise_gain,
                )

            # Publish every accepted measured-U1 covariance update.  Each PRN
            # receives this one spatial row with a scalar that preserves its
            # previous complex response; no PRN-specific LCMV solve exists.
            if covariance_u1_result is not None:
                protection_rejection = _preflight_rejection(
                    "covariance_lcmv_measured_u1",
                    covariance_u1_result,
                    enforce_white_noise_gain=False,
                )
                if not protection_rejection:
                    self._set_shared_measured_u1_protection_weights(
                        np.asarray(
                            getattr(covariance_u1_result, "weights", []),
                            dtype=np.complex128,
                        )
                    )

            active_lcmv_null_method = self._lcmv_test_null_method
            active_lcmv_method = self._lcmv_test_null_method
            active_lcmv_weights_source = active_lcmv_method
            active_lcmv_fallback_reason = ""
            active_lcmv_fallback_used = False
            requested_entry = candidate_entries.get(active_lcmv_method)
            if requested_entry is not None and requested_entry.get("result") is not None:
                active_result = requested_entry["result"]
                active_weights = np.asarray(
                    getattr(active_result, "weights", []),
                    dtype=np.complex128,
                ).reshape(-1)
            else:
                raise ValueError(
                    f"active method {active_lcmv_method} unavailable: "
                    f"{requested_entry.get('error') if requested_entry else 'unknown method'}"
                )
            active_safety_reason = _preflight_rejection(
                active_lcmv_method,
                active_result,
                enforce_white_noise_gain=False,
            )
            if active_safety_reason:
                raise ValueError(f"active LCMV method rejected: {active_safety_reason}")

            output_diag = self._lcmv_test_output_diagnostics(
                corrected,
                active_weights,
                raw_power_metrics=raw_power_metrics,
            )
            self._schedule_beamformer_weights(
                active_weights,
                reason="covariance LCMV target update",
                preempt_active_transition=False,
            )
            heavy_diag = self._lcmv_heavy_diagnostics_decision()
            model = lcmv_model_response(
                weights=active_weights,
                scan_angles_deg=self._scan_angles_deg,
                rf_freq_hz=self._config.center_freq_hz,
                array_spacing_m=self._config.array_spacing_m,
                selected_null_angle_deg=music_internal,
            )
            spatial_diag = self._spatial_vector_diagnostics_payload(
                corrected_chunk=corrected,
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
                ideal_result=covariance_ideal_result,
                candidate_entries=candidate_entries,
                active_weights=active_weights,
                active_lcmv_method=active_lcmv_method,
                active_lcmv_null_method=active_lcmv_null_method,
                active_lcmv_weights_source=active_lcmv_weights_source,
                active_lcmv_fallback_reason=active_lcmv_fallback_reason,
                active_lcmv_fallback_used=active_lcmv_fallback_used,
                target_angle_policy=target_angle_policy,
                run_state_payload=run_state_payload,
                heavy_diagnostics_payload=heavy_diag,
                raw_power_metrics=raw_power_metrics,
                cal_power_metrics=cal_power_metrics,
                covariance_matrix=covariance,
                covariance_eigenvalues=eigenvalues,
                covariance_eigenvectors=eigenvectors,
            )
            spatial_diag.update(self._beamformer_transition_payload())
            self._spatial_diag_seq += 1
            if bool(heavy_diag.get("heavy_diagnostics_emitted", False)):
                self._log_spatial_vector_diagnostics(spatial_diag)

            self._set_lcmv_status(
                enabled=True,
                mode="on",
                reason=active_lcmv_fallback_reason,
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
                null_internal_deg=music_internal,
                null_bearing_deg=music_bearing,
                weight_norm=getattr(active_result, "weight_norm", None),
                max_weight_abs=getattr(active_result, "max_weight_abs", None),
                condition_number=getattr(active_result, "condition_number", None),
                preserve_residual_abs=abs(
                    getattr(active_result, "preserve_residual", 0.0)
                ),
                null_residual_abs=abs(getattr(active_result, "null_residual", 0.0)),
                uniform_rms=output_diag.get("uniform_output_rms_complex"),
                lcmv_rms=output_diag.get("lcmv_output_rms_complex"),
                lcmv_response_db=model.response_db,
                lcmv_response_abs=model.response_abs,
                lcmv_response_power=model.response_power,
                lcmv_response_power_db=model.response_power_db,
                lcmv_model_summary=self._lcmv_model_summary_payload(model),
                output_metrics=output_diag,
                active_lcmv_null_method=active_lcmv_null_method,
                active_lcmv_weights_source=active_lcmv_weights_source,
                active_lcmv_fallback_reason=active_lcmv_fallback_reason,
                active_lcmv_method=active_lcmv_method,
                active_lcmv_fallback_used=active_lcmv_fallback_used,
                candidate_methods_computed=list(candidate_entries),
                candidate_methods_valid=list(spatial_diag.get("candidate_methods_valid", [])),
                candidate_methods_rejected=dict(
                    spatial_diag.get("candidate_methods_rejected", {})
                ),
                run_state_label=str(run_state_payload.get("run_state_label", "unknown")),
                jammer_confidence_score=run_state_payload.get("jammer_confidence_score"),
                healthy_confidence_score=run_state_payload.get("healthy_confidence_score"),
                spatial_vector_diagnostics=spatial_diag,
                heavy_diagnostics_interval_s=heavy_diag.get("heavy_diagnostics_interval_s"),
                heavy_diagnostics_emitted=bool(
                    heavy_diag.get("heavy_diagnostics_emitted", False)
                ),
                heavy_diagnostics_skipped_due_to_throttle=bool(
                    heavy_diag.get("heavy_diagnostics_skipped_due_to_throttle", False)
                ),
                last_heavy_diagnostics_age_s=heavy_diag.get("last_heavy_diagnostics_age_s"),
            )
            with self._results_lock:
                self._latest_output_power_metrics = dict(output_diag)
                self._latest_spatial_vector_diagnostics = dict(spatial_diag)
            self._maybe_log_lcmv_active(
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
                result=active_result,
                output_diag=output_diag,
                model=model,
                spatial_diag=spatial_diag,
                active_lcmv_method=active_lcmv_method,
                active_lcmv_null_method=active_lcmv_null_method,
                active_lcmv_weights_source=active_lcmv_weights_source,
                active_lcmv_fallback_reason=active_lcmv_fallback_reason,
                active_lcmv_fallback_used=active_lcmv_fallback_used,
            )
        except Exception as exc:
            self._activate_lcmv_test_fallback(
                str(exc),
                music_internal_deg=music_internal,
                music_bearing_deg=music_bearing,
            )

    def _lcmv_test_output_diagnostics(
        self,
        corrected_chunk: np.ndarray,
        lcmv_weights: np.ndarray,
        *,
        raw_power_metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        expected_shape = (int(corrected_chunk.shape[1]),)
        uniform_w = uniform_weights(corrected_chunk.shape[0])
        uniform_out = apply_beamformer(corrected_chunk, uniform_w)
        lcmv_out = apply_beamformer(corrected_chunk, lcmv_weights)
        if uniform_out.shape != expected_shape or lcmv_out.shape != expected_shape:
            raise ValueError(
                "LCMV test output shape mismatch: "
                f"uniform={uniform_out.shape} lcmv={lcmv_out.shape} expected={expected_shape}"
            )
        if uniform_out.dtype != np.complex64 or lcmv_out.dtype != np.complex64:
            raise ValueError(
                "LCMV test output dtype mismatch: "
                f"uniform={uniform_out.dtype} lcmv={lcmv_out.dtype}"
            )

        uniform_power = float(np.mean(np.abs(uniform_out.astype(np.complex128)) ** 2))
        lcmv_power = float(np.mean(np.abs(lcmv_out.astype(np.complex128)) ** 2))
        if not np.isfinite(uniform_power) or not np.isfinite(lcmv_power):
            raise ValueError("LCMV test output RMS is not finite")
        output_diag: dict[str, object] = {}
        output_diag.update(
            signal_power_metrics(
                uniform_out,
                prefix="uniform_output",
                component_threshold=float(self._config.rx_clipping_component_threshold),
            )
        )
        output_diag.update(
            signal_power_metrics(
                lcmv_out,
                prefix="lcmv_output",
                component_threshold=float(self._config.rx_clipping_component_threshold),
            )
        )
        raw_metrics = raw_power_metrics if isinstance(raw_power_metrics, dict) else {}
        raw_powers = []
        for channel in range(corrected_chunk.shape[0]):
            raw_powers.append(raw_metrics.get(f"raw_ch{channel}_power_linear"))
        output_diag.update(
            output_reduction_metrics(
                raw_channel_powers_linear=raw_powers,
                uniform_output_power_linear=uniform_power,
                lcmv_output_power_linear=lcmv_power,
            )
        )
        output_diag["uniform_weights"] = complex_vector_payload(uniform_w)
        output_diag["lcmv_weights"] = complex_vector_payload(lcmv_weights)
        return output_diag

    def _lcmv_model_summary_payload(self, model: object) -> dict[str, object]:
        return {
            "response_db_epsilon": self._json_float(
                getattr(model, "response_db_epsilon", None)
            ),
            "closest_grid_bearing_to_selected_null_deg": self._json_float(
                getattr(model, "closest_grid_bearing_to_selected_null_deg", None)
            ),
            "selected_null_grid_error_deg": self._json_float(
                getattr(model, "selected_null_grid_error_deg", None)
            ),
            "model_response_at_selected_null_abs": self._json_float(
                getattr(model, "model_response_at_selected_null_abs", None)
            ),
            "model_response_at_selected_null_db": self._json_float(
                getattr(model, "model_response_at_selected_null_db", None)
            ),
            "model_response_power_at_selected_null_db": self._json_float(
                getattr(model, "model_response_power_at_selected_null_db", None)
            ),
            "model_min_response_abs": self._json_float(
                getattr(model, "model_min_response_abs", None)
            ),
            "model_min_response_db": self._json_float(
                getattr(model, "model_min_response_db", None)
            ),
            "model_min_response_bearing_deg": self._json_float(
                getattr(model, "model_min_response_bearing_deg", None)
            ),
            "model_max_response_abs": self._json_float(
                getattr(model, "model_max_response_abs", None)
            ),
            "model_max_response_db": self._json_float(
                getattr(model, "model_max_response_db", None)
            ),
            "model_max_response_bearing_deg": self._json_float(
                getattr(model, "model_max_response_bearing_deg", None)
            ),
        }

    def _activate_lcmv_test_fallback(
        self,
        reason: str,
        *,
        music_internal_deg: float | None,
        music_bearing_deg: float | None,
        spatial_vector_diagnostics: dict[str, object] | None = None,
        run_state_label: str | None = None,
        jammer_confidence_score: float | None = None,
        healthy_confidence_score: float | None = None,
    ) -> None:
        if spatial_vector_diagnostics is None:
            preserve_mode = str(
                getattr(self._config, "lcmv_preserve_constraint_mode", "uniform")
            ).strip().lower()
            if preserve_mode == "realtime_bladerf_measured_u1":
                spatial_vector_diagnostics = self._realtime_preserve_tracker_payload()
                spatial_vector_diagnostics["lcmv_jammer_activation_armed"] = bool(
                    spatial_vector_diagnostics.get(
                        "realtime_bladerf_angle_frozen",
                        False,
                    )
                    and not spatial_vector_diagnostics.get(
                        "lcmv_jammer_detected_latched",
                        False,
                    )
                )
        self._schedule_beamformer_weights(
            uniform_weights(len(self._config.channels)),
            reason=f"LCMV fallback to uniform: {reason}",
        )
        self._set_lcmv_status(
            enabled=True,
            mode="fallback",
            reason=reason,
            music_internal_deg=music_internal_deg,
            music_bearing_deg=music_bearing_deg,
            active_lcmv_fallback_reason=reason,
            active_lcmv_fallback_used=True,
            spatial_vector_diagnostics=spatial_vector_diagnostics,
            run_state_label=run_state_label,
            jammer_confidence_score=jammer_confidence_score,
            healthy_confidence_score=healthy_confidence_score,
        )
        if isinstance(spatial_vector_diagnostics, dict):
            with self._results_lock:
                self._latest_spatial_vector_diagnostics = dict(
                    spatial_vector_diagnostics
                )
        now = time.monotonic()
        if (now - self._last_lcmv_log_ts) >= self._doa_log_interval_s:
            self._last_lcmv_log_ts = now
            self._lcmv_log.warning(
                "lcmv_test mode=fallback action=uniform_fallback reason=%s "
                "music_internal_deg=%s music_bearing_deg=%s weights=uniform_array_sum",
                reason,
                self._format_optional_float(music_internal_deg),
                self._format_optional_float(music_bearing_deg),
            )

    def _maybe_log_lcmv_active(
        self,
        *,
        music_internal_deg: float,
        music_bearing_deg: float,
        result: object,
        output_diag: dict[str, object],
        model: object,
        spatial_diag: dict[str, object],
        active_lcmv_method: str,
        active_lcmv_null_method: str,
        active_lcmv_weights_source: str,
        active_lcmv_fallback_reason: str,
        active_lcmv_fallback_used: bool,
    ) -> None:
        now = time.monotonic()
        if (now - self._last_lcmv_log_ts) < self._doa_log_interval_s:
            return
        self._last_lcmv_log_ts = now
        measured_vs_uniform = self._finite_metric_float(
            output_diag.get("measured_output_reduction_vs_uniform_db")
        )
        measured_vs_raw_avg = self._finite_metric_float(
            output_diag.get("measured_output_reduction_vs_raw_avg_channel_db")
        )
        null_internal = self._finite_metric_float(
            getattr(result, "null_angle_deg", music_internal_deg)
        )
        if null_internal is None:
            null_internal = music_internal_deg
        null_display = self._internal_angle_to_display(null_internal)
        coherence_abs = self._finite_metric_float(
            spatial_diag.get("ideal_measured_coherence_abs")
        )
        principal_angle = self._finite_metric_float(
            spatial_diag.get("ideal_measured_principal_angle_deg")
        )
        predicted_gain = self._finite_metric_float(
            spatial_diag.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db")
        )
        raw_spread = self._finite_metric_float(spatial_diag.get("raw_power_spread_db"))
        cal_spread = self._finite_metric_float(spatial_diag.get("cal_power_spread_db"))
        calibration_mode = str(
            spatial_diag.get("calibration_correction_mode_applied", "--")
        )
        applied_magnitudes = spatial_diag.get("applied_correction_magnitudes", [])
        self._lcmv_log.info(
            "lcmv_test mode=on action=nulling_strongest_music_peak "
            "configured_lcmv_null_method=%s active_lcmv_null_method=%s "
            "active_lcmv_method=%s active_lcmv_weights_source=%s "
            "active_lcmv_fallback_used=%s active_lcmv_fallback_reason=%s "
            "candidate_methods_computed=%s candidate_methods_valid=%s "
            "candidate_methods_rejected=%s "
            "target_classification=%s target_angle_change_from_healthy_deg=%s "
            "active_desired_loss_db=%s "
            "heavy_diagnostics_interval_s=%s heavy_diagnostics_emitted=%s "
            "heavy_diagnostics_skipped_due_to_throttle=%s last_heavy_diagnostics_age_s=%s "
            "calibration_correction_mode_applied=%s "
            "applied_correction_magnitudes=[%s] "
            "raw_power_spread_db=%s cal_power_spread_db=%s "
            "music_internal_deg=%.2f music_bearing_deg=%.2f "
            "null_internal_deg=%.2f null_bearing_deg=%.2f "
            "ideal_measured_coherence_abs=%s "
            "ideal_measured_principal_angle_deg=%s "
            "predicted_u1_lcmv_output_gain_over_ideal_lcmv_db=%s "
            "uniform_weights=[%s] lcmv_weights=[%s] "
            "weight_norm=%.4f max_weight_abs=%.4f cond=%.3e "
            "preserve_residual_abs=%.3e null_residual_abs=%.3e "
            "uniform_rms=%.6e lcmv_rms=%.6e "
            "measured_output_reduction_vs_uniform_db=%.2f "
            "measured_output_reduction_vs_raw_avg_channel_db=%s "
            "model_response_at_selected_null_db=%s "
            "model_min_response_db=%s model_max_response_db=%s "
            "weight_transition=complex_linear_chunk_ramp",
            self._lcmv_test_null_method,
            active_lcmv_null_method,
            active_lcmv_method,
            active_lcmv_weights_source,
            int(bool(active_lcmv_fallback_used)),
            active_lcmv_fallback_reason or "--",
            ",".join(str(x) for x in spatial_diag.get("candidate_methods_computed", [])),
            ",".join(str(x) for x in spatial_diag.get("candidate_methods_valid", [])),
            json.dumps(spatial_diag.get("candidate_methods_rejected", {}), separators=(",", ":")),
            str(spatial_diag.get("lcmv_target_classification", "unclassified")),
            self._format_optional_float(
                spatial_diag.get("lcmv_target_angle_change_from_healthy_deg")
            ),
            self._format_optional_float(
                spatial_diag.get("active_desired_loss_vs_reference_db")
            ),
            self._format_optional_float(
                spatial_diag.get("heavy_diagnostics_interval_s")
            ),
            int(bool(spatial_diag.get("heavy_diagnostics_emitted", False))),
            int(bool(spatial_diag.get("heavy_diagnostics_skipped_due_to_throttle", False))),
            self._format_optional_float(
                spatial_diag.get("last_heavy_diagnostics_age_s")
            ),
            calibration_mode,
            self._format_float_vector(applied_magnitudes, digits=4),
            self._format_optional_float(raw_spread),
            self._format_optional_float(cal_spread),
            music_internal_deg,
            music_bearing_deg,
            float(null_internal),
            float(null_display),
            self._format_optional_float(coherence_abs, 5),
            self._format_optional_float(principal_angle),
            self._format_optional_float(predicted_gain),
            self._format_complex_vector(uniform_weights(len(self._config.channels))),
            self._format_complex_vector(result.weights),
            float(result.weight_norm),
            float(result.max_weight_abs),
            float(result.condition_number),
            abs(result.preserve_residual),
            abs(result.null_residual),
            float(output_diag.get("uniform_output_rms_complex") or float("nan")),
            float(output_diag.get("lcmv_output_rms_complex") or float("nan")),
            float(measured_vs_uniform if measured_vs_uniform is not None else float("nan")),
            self._format_optional_float(measured_vs_raw_avg),
            self._format_optional_float(
                getattr(model, "model_response_at_selected_null_db", None)
            ),
            self._format_optional_float(getattr(model, "model_min_response_db", None)),
            self._format_optional_float(getattr(model, "model_max_response_db", None)),
        )
        if not bool(spatial_diag.get("heavy_diagnostics_emitted", True)):
            return
        payload = self._lcmv_pattern_payload(
            music_internal_deg=music_internal_deg,
            music_bearing_deg=music_bearing_deg,
            result=result,
            output_diag=output_diag,
            model=model,
            spatial_diag=spatial_diag,
            active_lcmv_method=active_lcmv_method,
            active_lcmv_null_method=active_lcmv_null_method,
            active_lcmv_weights_source=active_lcmv_weights_source,
            active_lcmv_fallback_reason=active_lcmv_fallback_reason,
            active_lcmv_fallback_used=active_lcmv_fallback_used,
        )
        line = json.dumps(payload, separators=(",", ":"))
        self._lcmv_pattern_log.info("%s", line)

    def _lcmv_pattern_payload(
        self,
        *,
        music_internal_deg: float,
        music_bearing_deg: float,
        result: object,
        output_diag: dict[str, object],
        model: object,
        spatial_diag: dict[str, object],
        active_lcmv_method: str,
        active_lcmv_null_method: str,
        active_lcmv_weights_source: str,
        active_lcmv_fallback_reason: str,
        active_lcmv_fallback_used: bool,
    ) -> dict[str, object]:
        scan_internal = np.asarray(
            getattr(model, "scan_internal_angles_deg", []),
            dtype=np.float64,
        )
        scan_display = np.asarray(
            getattr(model, "scan_display_bearings_deg", []),
            dtype=np.float64,
        )
        response_abs = np.asarray(getattr(model, "response_abs", []), dtype=np.float64)
        response_power = np.asarray(getattr(model, "response_power", []), dtype=np.float64)
        response_db = np.asarray(getattr(model, "response_db", []), dtype=np.float64)
        response_power_db = np.asarray(
            getattr(model, "response_power_db", []),
            dtype=np.float64,
        )
        null_internal = self._finite_metric_float(
            getattr(result, "null_angle_deg", music_internal_deg)
        )
        if null_internal is None:
            null_internal = music_internal_deg
        null_display = self._internal_angle_to_display(null_internal)
        payload: dict[str, object] = {
            "event": "lcmv_model_response_absolute",
            "lcmv_enabled": True,
            "lcmv_mode": "on",
            "null_method": active_lcmv_null_method,
            "configured_lcmv_null_method": self._lcmv_test_null_method,
            "active_lcmv_method": active_lcmv_method,
            "active_lcmv_null_method": active_lcmv_null_method,
            "active_lcmv_weights_source": active_lcmv_weights_source,
            "active_lcmv_fallback_used": bool(active_lcmv_fallback_used),
            "active_lcmv_fallback_reason": active_lcmv_fallback_reason,
            "candidate_methods_computed": list(
                spatial_diag.get("candidate_methods_computed", [])
            ),
            "candidate_methods_valid": list(spatial_diag.get("candidate_methods_valid", [])),
            "candidate_methods_rejected": dict(
                spatial_diag.get("candidate_methods_rejected", {})
            ),
            "selected_null_internal_angle_deg": self._json_float(null_internal),
            "selected_null_display_bearing_deg": self._json_float(null_display),
            "music_primary_internal_angle_deg": self._json_float(music_internal_deg),
            "music_primary_display_bearing_deg": self._json_float(music_bearing_deg),
            "weights": complex_vector_payload(result.weights),
            "weight_norm": self._json_float(result.weight_norm),
            "max_weight_abs": self._json_float(result.max_weight_abs),
            "condition_number": self._json_float(result.condition_number),
            "preserve_target_complex": {
                "real": self._json_float(np.real(result.preserve_target)),
                "imag": self._json_float(np.imag(result.preserve_target)),
            },
            "preserve_response_complex": {
                "real": self._json_float(np.real(result.preserve_response)),
                "imag": self._json_float(np.imag(result.preserve_response)),
            },
            "null_response_complex": {
                "real": self._json_float(np.real(result.null_response)),
                "imag": self._json_float(np.imag(result.null_response)),
            },
            "preserve_residual_abs": self._json_float(abs(result.preserve_residual)),
            "null_residual_abs": self._json_float(abs(result.null_residual)),
            "lcmv_model_scan_internal_angles_deg": self._json_float_list(scan_internal),
            "lcmv_model_scan_display_bearings_deg": self._json_float_list(scan_display),
            "lcmv_model_response_abs": self._json_float_list(response_abs),
            "lcmv_model_response_power": self._json_float_list(response_power),
            "lcmv_model_response_db": self._json_float_list(response_db),
            "lcmv_model_response_power_db": self._json_float_list(response_power_db),
            "output_metrics": self._json_ready_mapping(output_diag),
            "ideal_measured_coherence_abs": self._json_float(
                spatial_diag.get("ideal_measured_coherence_abs")
            ),
            "ideal_measured_principal_angle_deg": self._json_float(
                spatial_diag.get("ideal_measured_principal_angle_deg")
            ),
            "predicted_u1_lcmv_output_gain_over_ideal_lcmv_db": self._json_float(
                spatial_diag.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db")
            ),
            **self._lcmv_model_summary_payload(model),
        }
        payload.update(self._expected_mid_response_payload(scan_display, response_abs, response_db))
        return payload

    def _expected_mid_response_payload(
        self,
        display_bearings_deg: np.ndarray,
        response_abs: np.ndarray,
        response_db: np.ndarray,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for label, min_key, max_key in (
            (
                "expected_jammer",
                "jammer_expected_bearing_deg_min",
                "jammer_expected_bearing_deg_max",
            ),
            (
                "expected_bladeRF",
                "bladeRF_expected_bearing_deg_min",
                "bladeRF_expected_bearing_deg_max",
            ),
        ):
            bearing_min = self._finite_metric_float(self._experiment_manifest.get(min_key))
            bearing_max = self._finite_metric_float(self._experiment_manifest.get(max_key))
            if (
                bearing_min is None
                or bearing_max is None
                or display_bearings_deg.size == 0
                or response_abs.size != display_bearings_deg.size
                or response_db.size != display_bearings_deg.size
            ):
                payload[f"model_response_at_{label}_mid_abs"] = None
                payload[f"model_response_at_{label}_mid_db"] = None
                continue
            midpoint = (bearing_min + self._angle_delta_clockwise(bearing_min, bearing_max) / 2.0) % 360.0
            distance = np.abs((display_bearings_deg - midpoint + 180.0) % 360.0 - 180.0)
            idx = int(np.argmin(distance))
            payload[f"model_response_at_{label}_mid_abs"] = self._json_float(response_abs[idx])
            payload[f"model_response_at_{label}_mid_db"] = self._json_float(response_db[idx])
        return payload

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
                    "gnss_combiner_compute",
                    time.monotonic() - compute_t0,
                )
                if gnss_vector.size > 0:
                    health_vector = (
                        gnss_vector[0]
                        if gnss_vector.ndim == 2
                        else gnss_vector
                    )
                    fifo_diag = signal_power_metrics(
                        health_vector,
                        prefix="fifo_output",
                        component_threshold=float(self._config.rx_clipping_component_threshold),
                    )
                    fifo_source = self._fifo_output_source_label()
                    fifo_diag["fifo_output_source"] = fifo_source
                    fifo_diag["fifo_matches_lcmv_output"] = fifo_source == "lcmv"
                    fifo_diag["fifo_matches_uniform_output"] = fifo_source == "uniform"
                    with self._results_lock:
                        self._latest_output_power_metrics = {
                            **dict(self._latest_output_power_metrics),
                            **fifo_diag,
                        }
                    self._update_gnss_fifo_signal_health(health_vector)
                    if self._gnss_fifo_health_chunk_counter >= max(
                        1, int(self._config.rx_health_log_interval_chunks)
                    ):
                        self._log_gnss_fifo_signal_health_summary(
                            samples_per_chunk=int(gnss_vector.size)
                        )
                        self._reset_gnss_fifo_signal_health()
                    write_t0 = time.monotonic()
                    sample_start = int(self._gnss_fifo_samples_written)
                    if not bridge.write(gnss_vector):
                        raise RuntimeError(
                            "GNSS-SDR FIFO did not accept the contiguous IQ chunk"
                        )
                    monitor = self._shared_u1_phase_monitor
                    if monitor is not None:
                        monitor.submit(
                            sample_start,
                            chunk,
                            self._get_gnss_monitor_logical_weights_copy(),
                        )
                    stream_sample_count = int(
                        gnss_vector.shape[-1]
                        if gnss_vector.ndim == 2
                        else gnss_vector.size
                    )
                    self._gnss_fifo_samples_written = (
                        sample_start + stream_sample_count
                    )
                    self._record_runtime_timing("gnss_fifo_write", time.monotonic() - write_t0)
            except Exception as exc:
                self._handle_gnss_pipeline_error(exc)
                break

    def _fifo_output_source_label(self) -> str:
        if self._shared_phase_fanout_enabled():
            if self._lcmv_test_enabled and self._lcmv_jammer_detected_latched:
                return "shared_measured_u1_phase_compensated_prn_fanout"
            return "shared_uniform_phase_reference_prn_fanout"
        if bool(self._beamformer_transition_payload().get("weight_transition_active")):
            return "weight_transition"
        mode = self._gnss_handoff_mode_label()
        if mode == "lcmv_test_nulling_continuous":
            return "lcmv"
        if mode in {"uniform_array_sum_continuous", "lcmv_test_uniform_fallback_continuous"}:
            return "uniform"
        return "unknown"

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
                preview_cols=self._ui_preview_cols,
                phase_correction_vector=self._config.phase_correction_vector,
                sample_rate_hz=self._config.sample_rate,
                phase_monitor_tone_offset_hz=self._config.phase_monitor_tone_offset_hz,
                phase_monitor_use_tone_bin=bool(
                    self._config.live_phase_monitor_use_tone_bin
                ),
            )
            calibrated_chunk = np.asarray(phase_metrics["calibrated_buffer"], dtype=np.complex128)
            phase_diag = self._phase_diagnostics_payload(phase_metrics)
            with self._results_lock:
                self._latest_powers = np.asarray(phase_metrics["powers"], dtype=np.float64)
                self._latest_raw_power_metrics = {
                    key: value for key, value in phase_diag.items() if key.startswith("raw_")
                }
                self._latest_cal_power_metrics = {
                    key: value for key, value in phase_diag.items() if key.startswith("cal_")
                }
                self._latest_rx_signal_health = {
                    **dict(self._latest_rx_signal_health),
                    "raw_power_spread_db": phase_diag.get("raw_power_spread_db"),
                    "raw_strongest_channel": phase_diag.get("raw_strongest_channel"),
                    "raw_weakest_channel": phase_diag.get("raw_weakest_channel"),
                    "cal_power_spread_db": phase_diag.get("cal_power_spread_db"),
                    "cal_strongest_channel": phase_diag.get("cal_strongest_channel"),
                    "cal_weakest_channel": phase_diag.get("cal_weakest_channel"),
                }
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
                payload = json.dumps(phase_diag, sort_keys=True, separators=(",", ":"))
                self._loggers["phase"].info("phase_channel_diagnostics %s", payload)
                self._loggers["health"].info("phase_channel_diagnostics %s", payload)
                self._last_phase_log_ts = now
            put_latest(
                self._doa_queue,
                PhaseResult(
                    calibrated_chunk=calibrated_chunk,
                    raw_chunk=np.asarray(work_item.chunk, dtype=np.complex128),
                    raw_power_metrics={
                        key: value for key, value in phase_diag.items() if key.startswith("raw_")
                    },
                    cal_power_metrics={
                        key: value for key, value in phase_diag.items() if key.startswith("cal_")
                    },
                ),
            )
            dt = time.monotonic() - t0
            self._record_runtime_timing("dsp_phase", dt)
            time.sleep(max(0.0, self._dsp_emit_interval_s - dt))

    def _select_lcmv_target_from_doa_metrics(
        self,
        doa_metrics: dict[str, object],
        *,
        primary_internal_deg: float,
        primary_display_deg: float,
    ) -> tuple[float, float, str]:
        mode = str(
            getattr(
                self._config,
                "lcmv_target_selection_mode",
                "strongest_music_peak",
            )
        ).strip().lower()
        if mode == "realtime_non_preserve_peak":
            with self._results_lock:
                preserve_internal = self._realtime_preserve_frozen_internal_deg
            if preserve_internal is None:
                return (
                    float("nan"),
                    float("nan"),
                    "no_frozen_realtime_bladerf_angle",
                )
            guard_deg = max(
                0.0,
                float(
                    getattr(
                        self._config,
                        "lcmv_realtime_preserve_guard_deg",
                        20.0,
                    )
                ),
            )
            candidates: list[tuple[float, float]] = []
            peaks = doa_metrics.get("doa_peaks", [])
            if isinstance(peaks, list):
                for peak in peaks:
                    if not isinstance(peak, dict):
                        continue
                    internal = self._finite_metric_float(peak.get("angle_deg"))
                    if internal is None:
                        continue
                    if self._angle_distance_deg(internal, preserve_internal) < guard_deg:
                        continue
                    candidates.append(
                        (float(internal), self._internal_angle_to_display(internal))
                    )
            if not candidates:
                return (
                    float("nan"),
                    float("nan"),
                    "no_music_peak_outside_frozen_bladerf_guard",
                )
            internal, display = candidates[0]
            return internal, display, "strongest_music_peak_outside_frozen_bladerf_guard"
        if mode != "expected_jammer_range_peak_or_center":
            return (
                float(primary_internal_deg),
                float(primary_display_deg),
                "strongest_music_peak",
            )

        jammer_start = self._finite_metric_float(
            self._experiment_manifest.get("jammer_expected_bearing_deg_min")
        )
        jammer_stop = self._finite_metric_float(
            self._experiment_manifest.get("jammer_expected_bearing_deg_max")
        )
        if jammer_start is None or jammer_stop is None:
            return (
                float(primary_internal_deg),
                float(primary_display_deg),
                "strongest_music_peak_missing_expected_jammer_range",
            )

        peaks = doa_metrics.get("doa_peaks", [])
        if isinstance(peaks, list):
            for peak in peaks:
                if not isinstance(peak, dict):
                    continue
                internal = self._finite_metric_float(peak.get("angle_deg"))
                if internal is None:
                    continue
                display = self._internal_angle_to_display(internal)
                if self._bearing_in_range(display, jammer_start, jammer_stop):
                    return internal, display, "music_peak_inside_expected_jammer_range"

        span = (float(jammer_stop) - float(jammer_start)) % 360.0
        center_display = (float(jammer_start) + span / 2.0) % 360.0
        center_internal = operator_bearing_to_internal_angle_deg(center_display)
        return center_internal, center_display, "expected_jammer_range_center"

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
                array_spacing_m=self._config.array_spacing_m,
                n_sources=max(int(self._expected_sources), 1),
            )
            raw_spec = np.asarray(doa_metrics["doa_raw_spectrum"], dtype=np.float64)
            doa_deg = float(doa_metrics["doa_deg"])
            doa_display_deg = self._internal_angle_to_display(doa_deg)
            with self._results_lock:
                self._latest_doa_raw_spectrum = raw_spec
                self._latest_doa_deg = doa_deg
                self._last_doa_ts = time.monotonic()
                self._latest_source_count_diagnostics = {
                    "n_sources": int(doa_metrics.get("n_sources", max(int(self._expected_sources), 1))),
                    "peak_count": doa_metrics.get("doa_peak_count"),
                    "noise_tail_assumed_sources": doa_metrics.get("noise_tail_assumed_sources"),
                    "noise_tail_count": doa_metrics.get("noise_tail_count"),
                    "noise_tail_white_like": doa_metrics.get("noise_tail_white_like"),
                }
            self._update_realtime_bladerf_angle_tracker(
                doa_metrics,
                primary_internal_deg=doa_deg,
            )
            self._update_healthy_reference_tracking_from_music(
                corrected_chunk=phase_result.calibrated_chunk,
                music_internal_deg=doa_deg,
                music_bearing_deg=doa_display_deg,
                raw_power_metrics=phase_result.raw_power_metrics,
                cal_power_metrics=phase_result.cal_power_metrics,
                covariance_matrix=np.asarray(
                    doa_metrics["covariance_matrix"],
                    dtype=np.complex128,
                ),
                covariance_eigenvectors=np.asarray(
                    doa_metrics["covariance_eigenvectors"],
                    dtype=np.complex128,
                ),
            )
            lcmv_internal_deg, lcmv_display_deg, lcmv_target_source = (
                self._select_lcmv_target_from_doa_metrics(
                    doa_metrics,
                    primary_internal_deg=doa_deg,
                    primary_display_deg=doa_display_deg,
                )
            )
            self._update_lcmv_test_from_music(
                phase_result.calibrated_chunk,
                lcmv_internal_deg,
                lcmv_display_deg,
                raw_power_metrics=phase_result.raw_power_metrics,
                cal_power_metrics=phase_result.cal_power_metrics,
                target_selection_source=lcmv_target_source,
                covariance_matrix=np.asarray(
                    doa_metrics["covariance_matrix"],
                    dtype=np.complex128,
                ),
                covariance_eigenvalues=np.asarray(
                    doa_metrics["covariance_eigenvalues"],
                    dtype=np.float64,
                ),
                covariance_eigenvectors=np.asarray(
                    doa_metrics["covariance_eigenvectors"],
                    dtype=np.complex128,
                ),
            )
            now = time.monotonic()
            if (now - self._last_doa_log_ts) >= self._doa_log_interval_s:
                doa_peaks = doa_metrics.get("doa_peaks", [])
                bartlett_deg = self._finite_metric_float(doa_metrics.get("bartlett_deg"))
                bartlett_display_deg = (
                    self._internal_angle_to_display(bartlett_deg)
                    if bartlett_deg is not None
                    else float("nan")
                )
                bartlett_peaks = doa_metrics.get("bartlett_peaks", [])
                self._loggers["doa"].info(
                    "doa method=%s nsrc=%d doa_deg_internal=%.2f doa_display_deg=%.2f "
                    "display=top_zero_clockwise max_raw_spec=%.4f peak_count=%d "
                    "peaks_internal=[%s] peaks_display=[%s] "
                    "bartlett_deg_internal=%.2f bartlett_display_deg=%.2f "
                    "bartlett_peak_count=%d bartlett_peaks_internal=[%s] "
                    "bartlett_peaks_display=[%s] "
                    "eig_db=[%s] eig_rel_db=[%s] eig_gap_db=[%s] "
                    "noise_tail_k=%d noise_tail_count=%d noise_tail_rel_db=[%s] "
                    "noise_tail_spread_db=%.2f noise_tail_flatness_db=%.2f "
                    "noise_tail_white_like=%d",
                    "music",
                    int(doa_metrics["n_sources"]),
                    doa_deg,
                    doa_display_deg,
                    float(np.max(raw_spec)),
                    int(doa_metrics.get("doa_peak_count", 0)),
                    self._format_doa_peaks(doa_peaks, display_angles=False),
                    self._format_doa_peaks(doa_peaks, display_angles=True),
                    float(bartlett_deg) if bartlett_deg is not None else float("nan"),
                    float(bartlett_display_deg),
                    int(doa_metrics.get("bartlett_peak_count", 0)),
                    self._format_doa_peaks(bartlett_peaks, display_angles=False),
                    self._format_doa_peaks(bartlett_peaks, display_angles=True),
                    self._format_float_vector(doa_metrics.get("covariance_eigenvalues_db", [])),
                    self._format_float_vector(
                        doa_metrics.get("covariance_eigenvalues_rel_db", [])
                    ),
                    self._format_float_vector(doa_metrics.get("covariance_eigen_gap_db", [])),
                    int(doa_metrics.get("noise_tail_assumed_sources", 0)),
                    int(doa_metrics.get("noise_tail_count", 0)),
                    self._format_float_vector(
                        doa_metrics.get("noise_tail_eigenvalues_rel_db", [])
                    ),
                    float(doa_metrics.get("noise_tail_spread_db", 0.0)),
                    float(doa_metrics.get("noise_tail_flatness_db", 0.0)),
                    int(bool(doa_metrics.get("noise_tail_white_like", False))),
                )
                heavy_interval_s = self._lcmv_heavy_diagnostics_interval_s()
                full_angle_age_s = (
                    None
                    if self._last_full_angle_analysis_ts is None
                    else now - self._last_full_angle_analysis_ts
                )
                if (
                    full_angle_age_s is None
                    or heavy_interval_s <= 0.0
                    or full_angle_age_s >= heavy_interval_s
                ):
                    self._last_full_angle_analysis_ts = now
                    self._log_full_angle_analysis(
                        doa_metrics=doa_metrics,
                        raw_spec=raw_spec,
                        doa_internal_deg=doa_deg,
                        doa_display_deg=doa_display_deg,
                        calibrated_chunk=phase_result.calibrated_chunk,
                        raw_power_metrics=phase_result.raw_power_metrics,
                        cal_power_metrics=phase_result.cal_power_metrics,
                    )
                self._last_doa_log_ts = now
            dt = time.monotonic() - t0
            self._record_runtime_timing("dsp_doa", dt)
            time.sleep(max(0.0, self._dsp_emit_interval_s - dt))

    def _log_full_angle_analysis(
        self,
        *,
        doa_metrics: dict[str, object],
        raw_spec: np.ndarray,
        doa_internal_deg: float,
        doa_display_deg: float,
        calibrated_chunk: np.ndarray,
        raw_power_metrics: dict[str, object] | None = None,
        cal_power_metrics: dict[str, object] | None = None,
    ) -> None:
        scan_internal = np.asarray(self._scan_angles_deg, dtype=np.float64).reshape(-1)
        raw = np.asarray(raw_spec, dtype=np.float64).reshape(-1)
        if scan_internal.size != raw.size or raw.size == 0:
            return

        max_raw = max(float(np.nanmax(raw)), 1e-300)
        music_db = 10.0 * np.log10(np.maximum(raw, 1e-300))
        music_rel_db = 10.0 * np.log10(np.maximum(raw, 1e-300) / max_raw)
        bartlett_raw = np.asarray(
            doa_metrics.get("bartlett_raw_spectrum", np.zeros_like(raw)),
            dtype=np.float64,
        ).reshape(-1)
        if bartlett_raw.size != raw.size:
            bartlett_raw = np.zeros_like(raw)
        max_bartlett = max(float(np.nanmax(bartlett_raw)), 1e-300)
        bartlett_db = 10.0 * np.log10(np.maximum(bartlett_raw, 1e-300))
        bartlett_rel_db = 10.0 * np.log10(
            np.maximum(bartlett_raw, 1e-300) / max_bartlett
        )
        finite_music_rel = music_rel_db[np.isfinite(music_rel_db)]
        music_rel_db_polar_radius = (
            music_rel_db - float(np.min(finite_music_rel))
            if finite_music_rel.size
            else np.zeros_like(music_rel_db)
        )
        display_scan = np.asarray((90.0 - scan_internal) % 360.0, dtype=np.float64)
        status = self._lcmv_status_copy()
        lcmv_response = np.asarray(
            status.get("lcmv_response_db", np.zeros((0,), dtype=np.float64)),
            dtype=np.float64,
        ).reshape(-1)
        if lcmv_response.size != raw.size:
            lcmv_response = np.zeros((0,), dtype=np.float64)
        lcmv_response_abs = np.asarray(
            status.get("lcmv_response_abs", np.zeros((0,), dtype=np.float64)),
            dtype=np.float64,
        ).reshape(-1)
        if lcmv_response_abs.size != raw.size:
            lcmv_response_abs = np.zeros((0,), dtype=np.float64)
        lcmv_response_power = np.asarray(
            status.get("lcmv_response_power", np.zeros((0,), dtype=np.float64)),
            dtype=np.float64,
        ).reshape(-1)
        if lcmv_response_power.size != raw.size:
            lcmv_response_power = np.zeros((0,), dtype=np.float64)
        lcmv_response_power_db = np.asarray(
            status.get("lcmv_response_power_db", np.zeros((0,), dtype=np.float64)),
            dtype=np.float64,
        ).reshape(-1)
        if lcmv_response_power_db.size != raw.size:
            lcmv_response_power_db = np.zeros((0,), dtype=np.float64)
        corrected = np.asarray(calibrated_chunk, dtype=np.complex128)
        covariance = np.asarray(
            doa_metrics.get("covariance_matrix", np.zeros((0, 0))),
            dtype=np.complex128,
        )
        eigenvalues = np.asarray(
            doa_metrics.get("covariance_eigenvalues", np.zeros((0,))),
            dtype=np.float64,
        )
        eigenvectors = np.asarray(
            doa_metrics.get("covariance_eigenvectors", np.zeros((0, 0))),
            dtype=np.complex128,
        )
        eigenvalues_safe = np.maximum(eigenvalues, 1e-300)
        eigenvalues_db = 10.0 * np.log10(eigenvalues_safe)
        eigenvalues_relative_db = (
            eigenvalues_db - float(eigenvalues_db[0])
            if eigenvalues_db.size
            else eigenvalues_db
        )
        peaks = doa_metrics.get("doa_peaks", [])
        peak_internal = self._peak_angles(peaks, display_angles=False)
        peak_display = self._peak_angles(peaks, display_angles=True)
        peak_relative_db = self._peak_relative_db(peaks)
        bartlett_peaks = doa_metrics.get("bartlett_peaks", [])
        bartlett_peak_internal = self._peak_angles(bartlett_peaks, display_angles=False)
        bartlett_peak_display = self._peak_angles(bartlett_peaks, display_angles=True)
        bartlett_peak_relative_db = self._peak_relative_db(bartlett_peaks)

        doa_index = self._nearest_angle_index(scan_internal, doa_internal_deg)
        bartlett_internal = self._finite_metric_float(doa_metrics.get("bartlett_deg"))
        if bartlett_internal is None:
            bartlett_internal = float(scan_internal[int(np.argmax(bartlett_raw))])
        bartlett_display = self._internal_angle_to_display(bartlett_internal)
        bartlett_index = self._nearest_angle_index(scan_internal, bartlett_internal)
        null_internal = self._finite_metric_float(status.get("null_internal_deg"))
        null_display = self._finite_metric_float(status.get("null_bearing_deg"))
        if null_internal is None:
            null_internal = self._finite_metric_float(status.get("music_internal_deg"))
        if null_display is None:
            null_display = self._finite_metric_float(status.get("music_bearing_deg"))
        null_index = (
            self._nearest_angle_index(scan_internal, null_internal)
            if null_internal is not None
            else None
        )
        raw_metrics = raw_power_metrics if isinstance(raw_power_metrics, dict) else {}
        cal_metrics = cal_power_metrics if isinstance(cal_power_metrics, dict) else {}

        payload: dict[str, object] = {
            "event": "full_angle_analysis",
            "schema_version": 2,
            **self._calibration_context_payload(),
            "raw_power_spread_db": self._json_float(raw_metrics.get("raw_power_spread_db")),
            "cal_power_spread_db": self._json_float(cal_metrics.get("cal_power_spread_db")),
            "sample_count": int(raw.size),
            "channel_count": int(corrected.shape[0]) if corrected.ndim == 2 else 0,
            "sample_count_used": int(corrected.shape[1]) if corrected.ndim == 2 else 0,
            "display_convention": "top_zero_clockwise",
            "configured_nsrc": int(doa_metrics.get("n_sources", 0)),
            "expected_sources": int(self._expected_sources),
            "scan_start_deg": self._json_float(float(scan_internal[0])),
            "scan_stop_deg": self._json_float(float(scan_internal[-1])),
            "scan_step_deg": self._json_float(
                float(scan_internal[1] - scan_internal[0])
                if scan_internal.size > 1
                else 0.0
            ),
            "center_freq_hz": self._json_float(self._config.center_freq_hz),
            "array_spacing_m": self._json_float(self._config.array_spacing_m),
            "scan_internal_deg": self._json_float_list(scan_internal),
            "scan_display_deg": self._json_float_list(display_scan),
            "music_spectrum_linear": self._json_float_list(raw),
            "music_spectrum_db": self._json_float_list(music_db),
            "music_spectrum_relative_db": self._json_float_list(music_rel_db),
            "music_rel_db_polar_radius": self._json_float_list(music_rel_db_polar_radius),
            "bartlett_spectrum_linear": self._json_float_list(bartlett_raw),
            "bartlett_spectrum_db": self._json_float_list(bartlett_db),
            "bartlett_spectrum_relative_db": self._json_float_list(bartlett_rel_db),
            "doa": {
                "index": int(doa_index),
                "internal_deg": self._json_float(doa_internal_deg),
                "display_deg": self._json_float(doa_display_deg),
                "raw_spectrum": self._json_float(raw[doa_index]),
                "rel_db": self._json_float(music_rel_db[doa_index]),
                "rel_db_polar_radius": self._json_float(
                    music_rel_db_polar_radius[doa_index]
                ),
            },
            "primary_internal_angle_deg": self._json_float(doa_internal_deg),
            "primary_display_bearing_deg": self._json_float(doa_display_deg),
            "all_peak_internal_angles_deg": peak_internal,
            "all_peak_display_bearings_deg": peak_display,
            "all_peak_relative_db": peak_relative_db,
            "peak_count": int(doa_metrics.get("doa_peak_count", 0)),
            "peaks": peaks,
            "bartlett": {
                "index": int(bartlett_index),
                "internal_deg": self._json_float(bartlett_internal),
                "display_deg": self._json_float(bartlett_display),
                "raw_spectrum": self._json_float(bartlett_raw[bartlett_index]),
                "rel_db": self._json_float(bartlett_rel_db[bartlett_index]),
                "peak_count": int(doa_metrics.get("bartlett_peak_count", 0)),
                "peaks": bartlett_peaks,
                "all_peak_internal_angles_deg": bartlett_peak_internal,
                "all_peak_display_bearings_deg": bartlett_peak_display,
                "all_peak_relative_db": bartlett_peak_relative_db,
            },
            "bartlett_peak_internal_angle_deg": self._json_float(bartlett_internal),
            "bartlett_peak_display_bearing_deg": self._json_float(bartlett_display),
            "covariance_matrix": self._json_complex_matrix(covariance),
            "eigenvalues_linear": self._json_float_list(eigenvalues),
            "eigenvalues_db": self._json_float_list(eigenvalues_db),
            "eigenvalues_relative_db": self._json_float_list(eigenvalues_relative_db),
            "eigenvectors": self._json_complex_matrix(eigenvectors),
            "noise_tail": {
                "assumed_sources": int(
                    doa_metrics.get("noise_tail_assumed_sources", 0)
                ),
                "count": int(doa_metrics.get("noise_tail_count", 0)),
                "eigenvalues_relative_db": self._json_float_list(
                    doa_metrics.get("noise_tail_eigenvalues_rel_db", [])
                ),
                "spread_db": self._json_float(
                    doa_metrics.get("noise_tail_spread_db", 0.0)
                ),
                "flatness_db": self._json_float(
                    doa_metrics.get("noise_tail_flatness_db", 0.0)
                ),
                "testable": bool(doa_metrics.get("noise_tail_testable", False)),
                "white_like": bool(doa_metrics.get("noise_tail_white_like", False)),
                "spread_threshold_db": self._json_float(
                    doa_metrics.get("noise_tail_spread_threshold_db", 0.0)
                ),
                "flatness_threshold_db": self._json_float(
                    doa_metrics.get("noise_tail_flatness_threshold_db", 0.0)
                ),
            },
            "eigenvalue_gap_1_2_db": self._json_float(
                np.asarray(doa_metrics.get("covariance_eigen_gap_db", []))[0]
                if np.asarray(doa_metrics.get("covariance_eigen_gap_db", [])).size
                else None
            ),
            "classification_hints": self._classification_hint_payload(
                primary_bearing=doa_display_deg,
                peak_display_bearings=peak_display,
            ),
            "lcmv": {
                "enabled": bool(status.get("enabled", False)),
                "mode": str(status.get("mode", "off")),
                "null_method": str(
                    status.get("active_lcmv_null_method", self._lcmv_test_null_method)
                ),
                "active_lcmv_method": str(
                    status.get("active_lcmv_method", self._lcmv_test_null_method)
                ),
                "active_lcmv_null_method": str(
                    status.get("active_lcmv_null_method", self._lcmv_test_null_method)
                ),
                "active_lcmv_weights_source": str(
                    status.get("active_lcmv_weights_source", "--")
                ),
                "active_lcmv_fallback_used": bool(
                    status.get("active_lcmv_fallback_used", False)
                ),
                "active_lcmv_fallback_reason": str(
                    status.get("active_lcmv_fallback_reason", "")
                ),
                "candidate_methods_computed": list(
                    status.get("candidate_methods_computed", [])
                ),
                "candidate_methods_valid": list(status.get("candidate_methods_valid", [])),
                "candidate_methods_rejected": dict(
                    status.get("candidate_methods_rejected", {})
                ),
                "run_state_label": str(status.get("run_state_label", "unknown")),
                "jammer_confidence_score": self._json_float(
                    status.get("jammer_confidence_score")
                ),
                "healthy_confidence_score": self._json_float(
                    status.get("healthy_confidence_score")
                ),
                "null_internal_deg": self._json_float(null_internal),
                "null_display_deg": self._json_float(null_display),
                "weight_norm": self._json_float(status.get("weight_norm")),
                "max_weight_abs": self._json_float(status.get("max_weight_abs")),
                "condition_number": self._json_float(status.get("condition_number")),
                "preserve_residual_abs": self._json_float(
                    status.get("preserve_residual_abs")
                ),
                "null_residual_abs": self._json_float(status.get("null_residual_abs")),
                "uniform_rms": self._json_float(status.get("uniform_rms")),
                "lcmv_rms": self._json_float(status.get("lcmv_rms")),
                "output_metrics": self._json_ready_mapping(
                    status.get("output_metrics", {})
                ),
                "lcmv_model_response_abs": self._json_float_list(lcmv_response_abs),
                "lcmv_model_response_power": self._json_float_list(lcmv_response_power),
                "lcmv_model_response_db": self._json_float_list(lcmv_response),
                "lcmv_model_response_power_db": self._json_float_list(
                    lcmv_response_power_db
                ),
                "lcmv_model_summary": self._json_ready_mapping(
                    status.get("lcmv_model_summary", {})
                ),
                "null_match": self._lcmv_null_match_payload(
                    scan_internal=scan_internal,
                    display_scan=display_scan,
                    music_rel_db=music_rel_db,
                    music_raw=raw,
                    lcmv_response_db=lcmv_response,
                    null_index=null_index,
                    doa_index=doa_index,
                ),
            },
            "spatial_vector_diagnostics_sequence": self._json_float(
                status.get("spatial_vector_diagnostics", {}).get("sequence")
                if isinstance(status.get("spatial_vector_diagnostics"), dict)
                else None
            ),
        }
        self._analysis_log.info("%s", json.dumps(payload, separators=(",", ":")))

    def _lcmv_null_match_payload(
        self,
        *,
        scan_internal: np.ndarray,
        display_scan: np.ndarray,
        music_rel_db: np.ndarray,
        music_raw: np.ndarray,
        lcmv_response_db: np.ndarray,
        null_index: int | None,
        doa_index: int,
    ) -> dict[str, object]:
        if null_index is None or null_index < 0 or null_index >= scan_internal.size:
            return {
                "available": False,
                "reason": "no_lcmv_null_angle",
                "doa_index": int(doa_index),
            }
        delta = self._angle_distance_deg(scan_internal[doa_index], scan_internal[null_index])
        return {
            "available": True,
            "doa_index": int(doa_index),
            "null_index": int(null_index),
            "delta_deg": self._json_float(delta),
            "internal_deg": self._json_float(scan_internal[null_index]),
            "display_deg": self._json_float(display_scan[null_index]),
            "music_raw_spectrum": self._json_float(music_raw[null_index]),
            "music_rel_db": self._json_float(music_rel_db[null_index]),
            "lcmv_response_db": self._json_float(
                lcmv_response_db[null_index] if lcmv_response_db.size else None
            ),
        }

    @staticmethod
    def _nearest_angle_index(scan_angles_deg: np.ndarray, angle_deg: float) -> int:
        scan = np.asarray(scan_angles_deg, dtype=np.float64).reshape(-1)
        if scan.size == 0:
            return 0
        angle = float(angle_deg)
        distance = np.abs((scan - angle + 180.0) % 360.0 - 180.0)
        return int(np.argmin(distance))

    @staticmethod
    def _angle_distance_deg(a: float, b: float) -> float:
        return float(abs((float(a) - float(b) + 180.0) % 360.0 - 180.0))

    @staticmethod
    def _angle_delta_clockwise(start_deg: float, stop_deg: float) -> float:
        return float((float(stop_deg) - float(start_deg)) % 360.0)

    @classmethod
    def _json_float_list(cls, values: object) -> list[float | None]:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
        return [cls._json_float(value) for value in arr]

    @classmethod
    def _json_complex_matrix(cls, values: object) -> dict[str, list[list[float | None]]]:
        arr = np.asarray(values, dtype=np.complex128)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return {"real": [], "imag": []}
        return {
            "real": [
                [cls._json_float(value) for value in row]
                for row in np.real(arr)
            ],
            "imag": [
                [cls._json_float(value) for value in row]
                for row in np.imag(arr)
            ],
        }

    @classmethod
    def _complex_scalar_payload(cls, value: object) -> dict[str, float | None]:
        try:
            scalar = complex(value)
        except (TypeError, ValueError):
            return {"real": None, "imag": None, "magnitude": None, "phase_deg": None}
        if not (np.isfinite(np.real(scalar)) and np.isfinite(np.imag(scalar))):
            return {"real": None, "imag": None, "magnitude": None, "phase_deg": None}
        return {
            "real": cls._json_float(np.real(scalar)),
            "imag": cls._json_float(np.imag(scalar)),
            "magnitude": cls._json_float(abs(scalar)),
            "phase_deg": cls._json_float(np.degrees(np.angle(scalar))),
        }

    @classmethod
    def _json_ready_mapping(cls, values: object) -> dict[str, object]:
        if not isinstance(values, dict):
            return {}
        ready: dict[str, object] = {}
        for key, value in values.items():
            if isinstance(value, bool):
                ready[str(key)] = value
            elif isinstance(value, dict):
                ready[str(key)] = cls._json_ready_mapping(value)
            elif isinstance(value, (list, tuple)):
                ready[str(key)] = [
                    cls._json_ready_mapping(item)
                    if isinstance(item, dict)
                    else item
                    if isinstance(item, bool)
                    else cls._json_float(item)
                    if isinstance(item, (int, float, np.floating, np.integer))
                    else item
                    for item in value
                ]
            elif isinstance(value, np.ndarray):
                if np.iscomplexobj(value):
                    ready[str(key)] = complex_vector_payload(value)
                else:
                    ready[str(key)] = cls._json_float_list(value)
            elif isinstance(value, (int, float, np.floating, np.integer)):
                ready[str(key)] = cls._json_float(value)
            else:
                ready[str(key)] = value
        return ready

    @staticmethod
    def _json_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(number):
            return None
        return number

    # -------------------------------------------------------------------------
    # UI Metrics
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
        self._maybe_log_runtime_evidence(gnss_snapshot)
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
            doa_raw_spec = np.array(self._latest_doa_raw_spectrum, copy=True)
            doa_deg = float(self._latest_doa_deg)
            doa_display_deg = self._internal_angle_to_display(doa_deg)
            source_count = dict(self._latest_source_count_diagnostics)
            lcmv_test = dict(self._latest_lcmv_test)
            lcmv_test["output_metrics"] = {
                **dict(lcmv_test.get("output_metrics", {})),
                **dict(self._latest_output_power_metrics),
            }
            rx_signal_health = dict(self._latest_rx_signal_health)
        lcmv_test.update(self._beamformer_transition_payload())
        metrics = RuntimeUiMetrics(
            powers=powers,
            phase_offsets_deg=offsets,
            phase_offsets_raw_deg=raw_offsets,
            phase_offsets_calibrated_deg=calibrated_offsets,
            complex_samples=raw_preview,
            complex_samples_raw=raw_preview,
            complex_samples_calibrated=calibrated_preview,
            doa_raw_spectrum=doa_raw_spec,
            doa_deg=doa_deg,
            doa_display_deg=doa_display_deg,
            lcmv_test=lcmv_test,
            rx_signal_health=rx_signal_health,
            gnss_snapshot=gnss_snapshot,
        ).to_dict()
        metrics.update(
            {
                "backend_monotonic_s": time.monotonic(),
                "ui_metrics_seq": self._ui_metrics_seq,
                "i_samples": np.real(raw_preview),
                "i_samples_raw": np.real(raw_preview),
                "i_samples_calibrated": np.real(calibrated_preview),
                "n_sources": max(int(self._expected_sources), 1),
                "source_count": source_count,
            }
        )
        self._ui_metrics_seq += 1
        self._record_runtime_timing("ui_compose_metrics", time.monotonic() - compose_t0)
        return metrics

    # -------------------------------------------------------------------------
    # Power and Formatting Helpers
    # -------------------------------------------------------------------------

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

    def _format_float_vector(self, values: object, digits: int = 2) -> str:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
        return ", ".join(
            f"{float(v):+.{max(0, int(digits))}f}" if np.isfinite(v) else "--"
            for v in arr
        )

    def _format_complex_vector(self, values: object, digits: int = 6) -> str:
        arr = np.asarray(values, dtype=np.complex128).reshape(-1)
        places = max(0, int(digits))
        return ", ".join(
            (
                f"{float(np.real(v)):+.{places}f}"
                f"{float(np.imag(v)):+.{places}f}j"
            )
            if np.isfinite(np.real(v)) and np.isfinite(np.imag(v))
            else "--"
            for v in arr
        )

    def _format_doa_peaks(self, peaks: object, *, display_angles: bool) -> str:
        if not isinstance(peaks, list):
            return ""
        formatted: list[str] = []
        for peak in peaks:
            if not isinstance(peak, dict):
                continue
            try:
                angle = float(peak.get("angle_deg", float("nan")))
                height = float(peak.get("height", float("nan")))
                rel_db = float(peak.get("rel_db", float("nan")))
            except (TypeError, ValueError):
                continue
            if display_angles:
                angle = self._internal_angle_to_display(angle)
            if not (np.isfinite(angle) and np.isfinite(height) and np.isfinite(rel_db)):
                continue
            formatted.append(f"{angle:.2f}:{height:.3f}/{rel_db:+.1f}dB")
        return ", ".join(formatted)

    def _peak_angles(self, peaks: object, *, display_angles: bool) -> list[float | None]:
        if not isinstance(peaks, list):
            return []
        values: list[float | None] = []
        for peak in peaks:
            if not isinstance(peak, dict):
                continue
            angle = self._json_float(peak.get("angle_deg"))
            if angle is not None and display_angles:
                angle = self._internal_angle_to_display(angle)
            values.append(self._json_float(angle))
        return values

    def _peak_relative_db(self, peaks: object) -> list[float | None]:
        if not isinstance(peaks, list):
            return []
        values: list[float | None] = []
        for peak in peaks:
            if isinstance(peak, dict):
                values.append(self._json_float(peak.get("rel_db")))
        return values

    def _classification_hint_payload(
        self,
        *,
        primary_bearing: float,
        peak_display_bearings: list[float | None],
    ) -> dict[str, object]:
        jammer_min = self._finite_metric_float(
            self._experiment_manifest.get("jammer_expected_bearing_deg_min")
        )
        jammer_max = self._finite_metric_float(
            self._experiment_manifest.get("jammer_expected_bearing_deg_max")
        )
        blade_min = self._finite_metric_float(
            self._experiment_manifest.get("bladeRF_expected_bearing_deg_min")
        )
        blade_max = self._finite_metric_float(
            self._experiment_manifest.get("bladeRF_expected_bearing_deg_max")
        )
        secondary = [
            value for value in peak_display_bearings[1:] if value is not None
        ]
        return {
            "expected_jammer_bearing_min": jammer_min,
            "expected_jammer_bearing_max": jammer_max,
            "expected_bladeRF_bearing_min": blade_min,
            "expected_bladeRF_bearing_max": blade_max,
            "primary_peak_inside_expected_jammer_range": self._bearing_in_range_or_none(
                primary_bearing,
                jammer_min,
                jammer_max,
            ),
            "primary_peak_inside_expected_bladeRF_range": self._bearing_in_range_or_none(
                primary_bearing,
                blade_min,
                blade_max,
            ),
            "secondary_peak_inside_expected_jammer_range": self._any_bearing_in_range_or_none(
                secondary,
                jammer_min,
                jammer_max,
            ),
            "secondary_peak_inside_expected_bladeRF_range": self._any_bearing_in_range_or_none(
                secondary,
                blade_min,
                blade_max,
            ),
        }

    def _lcmv_target_angle_policy(self, display_bearing_deg: float) -> dict[str, object]:
        bearing = float(display_bearing_deg) % 360.0
        hints = self._classification_hint_payload(
            primary_bearing=bearing,
            peak_display_bearings=[bearing],
        )
        in_jammer_range = hints.get("primary_peak_inside_expected_jammer_range") is True
        in_bladerf_range = hints.get("primary_peak_inside_expected_bladeRF_range") is True
        confirmed_jammer = in_jammer_range and not in_bladerf_range
        protected_bladerf = in_bladerf_range and not in_jammer_range
        if confirmed_jammer:
            classification = "expected_jammer"
        elif protected_bladerf:
            classification = "expected_bladeRF"
        elif in_jammer_range and in_bladerf_range:
            classification = "ambiguous_expected_ranges"
        else:
            classification = "unclassified"
        healthy_angle_change = None
        if self._healthy_reference_display_bearing_deg is not None:
            healthy_angle_change = self._angle_distance_deg(
                bearing,
                float(self._healthy_reference_display_bearing_deg),
            )
        return {
            **hints,
            "lcmv_target_classification": classification,
            "lcmv_target_confirmed_jammer_bearing": bool(confirmed_jammer),
            "lcmv_target_protected_bladeRF_bearing": bool(protected_bladerf),
            "lcmv_target_angle_change_from_healthy_deg": self._json_float(
                healthy_angle_change
            ),
        }

    def _any_bearing_in_range_or_none(
        self,
        bearings: list[float],
        start_deg: float | None,
        stop_deg: float | None,
    ) -> bool | None:
        if start_deg is None or stop_deg is None:
            return None
        return any(self._bearing_in_range(value, start_deg, stop_deg) for value in bearings)

    def _bearing_in_range_or_none(
        self,
        bearing_deg: float,
        start_deg: float | None,
        stop_deg: float | None,
    ) -> bool | None:
        if start_deg is None or stop_deg is None:
            return None
        return self._bearing_in_range(bearing_deg, start_deg, stop_deg)

    @staticmethod
    def _bearing_in_range(bearing_deg: float, start_deg: float, stop_deg: float) -> bool:
        bearing = float(bearing_deg) % 360.0
        start = float(start_deg) % 360.0
        stop = float(stop_deg) % 360.0
        if start <= stop:
            return start <= bearing <= stop
        return bearing >= start or bearing <= stop

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
        lcmv_status = self._lcmv_status_copy()
        with self._results_lock:
            output_metrics = dict(self._latest_output_power_metrics)
            raw_power_metrics = dict(self._latest_raw_power_metrics)
            cal_power_metrics = dict(self._latest_cal_power_metrics)
        spatial_diag = (
            lcmv_status.get("spatial_vector_diagnostics", {})
            if isinstance(lcmv_status.get("spatial_vector_diagnostics", {}), dict)
            else {}
        )
        fifo_source = output_metrics.get("fifo_output_source")
        if not fifo_source:
            fifo_source = self._fifo_output_source_label()
        self._handoff_log.info(
            "runtime->GNSS snapshot: mode=%s fifo_source=%s raw_q=%s "
            "lcmv_enabled=%s lcmv_mode=%s active_lcmv_method=%s active_lcmv_null_method=%s "
            "active_lcmv_weights_source=%s active_lcmv_null_internal_angle_deg=%s "
            "active_lcmv_null_display_bearing_deg=%s active_lcmv_fallback_reason=%s "
            "selected_null_display_bearing_deg=%s music_primary_display_bearing_deg=%s "
            "latest_ideal_measured_coherence_abs=%s "
            "latest_predicted_u1_lcmv_output_gain_over_ideal_lcmv_db=%s "
            "measured_output_reduction_vs_uniform_db=%s "
            "measured_output_reduction_vs_raw_avg_channel_db=%s "
            "raw_power_spread_db=%s cal_power_spread_db=%s "
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
            fifo_source,
            raw_q_text,
            bool(lcmv_status.get("enabled", False)),
            str(lcmv_status.get("mode", "off")),
            str(lcmv_status.get("active_lcmv_method", self._lcmv_test_null_method)),
            str(lcmv_status.get("active_lcmv_null_method", self._lcmv_test_null_method)),
            str(lcmv_status.get("active_lcmv_weights_source", "--")),
            self._format_optional_float(lcmv_status.get("null_internal_deg")),
            self._format_optional_float(lcmv_status.get("null_bearing_deg")),
            str(lcmv_status.get("active_lcmv_fallback_reason", "") or "--"),
            self._format_optional_float(lcmv_status.get("null_bearing_deg")),
            self._format_optional_float(lcmv_status.get("music_bearing_deg")),
            self._format_optional_float(
                spatial_diag.get("ideal_measured_coherence_abs"), 5
            ),
            self._format_optional_float(
                spatial_diag.get("predicted_u1_lcmv_output_gain_over_ideal_lcmv_db")
            ),
            self._format_optional_float(
                output_metrics.get("measured_output_reduction_vs_uniform_db")
            ),
            self._format_optional_float(
                output_metrics.get("measured_output_reduction_vs_raw_avg_channel_db")
            ),
            self._format_optional_float(raw_power_metrics.get("raw_power_spread_db")),
            self._format_optional_float(cal_power_metrics.get("cal_power_spread_db")),
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
