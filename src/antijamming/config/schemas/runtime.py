"""Typed schema for JSON runtime profiles.

Product runtime values are loaded from
``configs/antijamming/x300_realtime.json``. This module defines the Python
object shape used by the realtime application after loading that JSON profile.
The schema covers the USRP/X300 RF front-end, TwinRX coherent LO setup, DSP
update pacing, phase calibration, DoA estimation, beamforming controls, and
GNSS-SDR FIFO/runtime integration.

Unknown JSON keys fail fast so spelling mistakes or stale config fields are
detected immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from pathlib import Path

from antijamming.config.paths import REPO_ROOT
from antijamming.dsp.models import AlgorithmMode, AngleScanSpec


# =============================================================================
# Runtime Config File
# =============================================================================

# Default product profile used by the realtime X300 application.
DEFAULT_RUNTIME_CONFIG_PATH = REPO_ROOT / "configs/antijamming/x300_realtime.json"


# =============================================================================
# Stream Runtime Schema
# =============================================================================

@dataclass(slots=True, init=False)
class StreamConfig:
    """Typed runtime configuration loaded from a JSON product profile.

    Existing runtime values should be changed in the JSON profile. This class
    defines the allowed field names, Python types, and derived helper views.
    """

    def __init__(self, **overrides: object) -> None:
        """Load the default JSON profile, then apply explicit overrides.

        Tests and narrow call sites still use StreamConfig(...) as a convenient
        fixture constructor, but the source values come from the JSON profile.
        """

        from antijamming.config.loader import default_stream_config

        loaded = default_stream_config()
        field_names = {field.name for field in dataclass_fields(type(self))}
        for field_name in field_names:
            setattr(self, field_name, getattr(loaded, field_name))

        unknown = sorted(set(overrides) - field_names)
        if unknown:
            names = ", ".join(repr(name) for name in unknown)
            raise TypeError(f"Unexpected StreamConfig override(s): {names}")

        for key, value in overrides.items():
            setattr(self, key, value)

    @classmethod
    def from_profile_values(cls, values: dict[str, object]) -> "StreamConfig":
        """Construct a config from already validated/coerced JSON values."""

        cfg = cls.__new__(cls)
        for field in dataclass_fields(cls):
            if field.name == "phase_correction_vector":
                setattr(cfg, field.name, values.get(field.name))
                continue
            setattr(cfg, field.name, values[field.name])
        return cfg

    # -------------------------------------------------------------------------
    # UHD / RF Front-End
    # -------------------------------------------------------------------------

    # Fixed UHD device address for the X300/XG 10GbE product profile.
    usrp_addr: str

    # UHD transport frame sizes. Zero means choose from selected route MTU/speed.
    recv_frame_size: int
    send_frame_size: int

    # UHD host receive buffering. These are passed as device args when absent
    # from an explicit usrp_addr string, so product runs get extra slack against
    # short host scheduling stalls without changing the RF sample rate.
    recv_buff_size: int
    num_recv_frames: int

    # Default physical/mental model: Ch0..Ch3 in order.
    # If antenna element order differs from UHD channel numbering, adjust this.
    channels: tuple[int, int, int, int]

    # Complex baseband sample rate used by the realtime receive pipeline.
    sample_rate: float

    # GPS L1 center frequency.
    center_freq_hz: float

    # USRP analog RX bandwidth. A value of 0.0 lets UHD/device defaults apply.
    usrp_rx_bandwidth_hz: float

    # Frequency used for array geometry/steering-vector calculations.
    array_design_freq_hz: float

    # Physical inter-element spacing used by the array model.
    array_spacing_m: float

    # Receiver gain applied to all configured channels unless overridden
    # elsewhere by the runtime.
    gain_db: float

    # Empty string means use rx_antennas_by_channel.
    # Set to "RX1" or "RX2" to force the same antenna port on every channel.
    antenna: str

    # -------------------------------------------------------------------------
    # TwinRX Coherent LO Configuration
    # -------------------------------------------------------------------------

    # TwinRX physical input mapping on this X300:
    # Ch0/Ch2 use RX1, Ch1/Ch3 use RX2.
    rx_antennas_by_channel: tuple[str, str, str, str]

    # Requires physical TwinRX LO-sharing MMCX cables between daughterboards.
    twinrx_lo_sharing: bool

    # Validated two-board coherent LO map for this X310 + 2x TwinRX setup:
    # - Ch0 is the A-board master/export source.
    # - Ch1 uses companion LO.
    # - Ch2/Ch3 use reimported LO.
    #
    # Ch2 export must be enabled for reimport. Ch3 must not export.
    rx_lo_sources_by_channel: tuple[str, str, str, str]
    rx_lo_exports_by_channel: tuple[bool, bool, bool, bool]

    # Maximum time allowed for LO lock before the runtime treats setup as failed.
    lo_lock_timeout_s: float

    # -------------------------------------------------------------------------
    # Runtime Pacing and Overflow Policy
    # -------------------------------------------------------------------------

    # Number of complex samples per receive/GNSS handoff chunk. The product
    # profile uses 32768 samples, which is 8.192 ms at 4 Msps.
    samples_per_chunk: int

    # Process one out of N chunks for heavier DSP work. The product value keeps
    # that cadence near 0.2 s while GNSS handoff receives every RX chunk.
    process_every_n_chunks: int

    # Minimum spacing between GUI and DSP refreshes.
    ui_update_interval_s: float
    dsp_update_interval_s: float

    # Heavy GNSS operator widgets are throttled separately from the main GUI
    # metrics flush so status text can remain responsive without repainting
    # satellite charts on every backend update.
    prn_chart_update_interval_s: float
    skyplot_update_interval_s: float

    # Startup grace period before strict runtime health checks are enforced.
    startup_grace_s: float

    # Keep the configured receive/GNSS rates fixed during product runs.
    # Overflows are logged as transport health events instead of silently
    # changing sample rate.
    auto_rate_backoff: bool
    min_sample_rate: float

    # Fail-fast overflow controls. Sustained overflows mean the receive path is
    # no longer trustworthy, so stop the run instead of silently continuing.
    stop_on_overflow: bool
    max_overflow_streak: int
    max_total_overflow: int

    # Normalized complex-float IQ clipping monitor. UHD fc32 samples are
    # expected to stay inside approximately +/-1.0 per I/Q component; sustained
    # samples near that limit indicate likely RF/ADC/full-scale clipping.
    rx_clipping_component_threshold: float
    rx_clipping_fraction_threshold: float

    # Root log directory for realtime run artifacts.
    log_dir: Path

    # When true, shutdown avoids aggressively tearing down the USRP session.
    preserve_usrp_session_on_stop: bool

    # -------------------------------------------------------------------------
    # GUI History and Phase Calibration
    # -------------------------------------------------------------------------

    # Number of points retained in GUI history plots.
    ui_points: int

    # Reference channel for relative phase calculations.
    phase_ref_channel: int

    # Static hardware phase correction measured from a conducted calibration
    # source. None preserves legacy dynamic per-chunk alignment behavior.
    phase_correction_vector: tuple[complex, ...] | None
    phase_calibration_file: Path | None

    # Tone offset used by the live phase monitor when tone-bin estimation is on.
    phase_monitor_tone_offset_hz: float

    # When true, the live phase monitor uses a fixed tone-bin estimator instead
    # of whole-chunk cross-correlation.
    live_phase_monitor_use_tone_bin: bool

    # -------------------------------------------------------------------------
    # DoA Estimation and Jammer Detection
    # -------------------------------------------------------------------------

    # Angular scan range used by MUSIC spatial spectrum estimation.
    doa_min_deg: float
    doa_max_deg: float
    doa_points: int

    # Default DoA estimator and expected number of spatial sources. Internal
    # steering/scanning angles remain CCW; operator display bearings are fixed
    # to clockwise.
    doa_method: str
    expected_sources: int

    # Radius for the uniform circular array model.
    uca_radius_m: float

    # Jammer detector gates. The product decision is driven by raw IQ power
    # rising above a learned quiet baseline.
    jammer_detection_enabled: bool
    jammer_detection_min_power_db: float
    jammer_detection_power_rise_db: float
    jammer_detection_power_baseline_alpha: float
    jammer_detection_consecutive_alarms: int

    # Default adaptive beamforming algorithm mode.
    algorithm_mode: AlgorithmMode

    # Safety default: do not null the strongest DoA unless it is explicitly
    # forced or gated as a jammer by the selected logic.
    lcmv_force_null: bool

    # -------------------------------------------------------------------------
    # GNSS-SDR Process and Runtime Paths
    # -------------------------------------------------------------------------

    # GNSS-SDR launcher and executable resolution.
    # Enable GNSS-SDR runtime integration and optional local-build enforcement.
    gnss_sdr_enable: bool
    gnss_sdr_require_local: bool

    # Optional explicit GNSS-SDR executable path. None lets runtime resolve it.
    gnss_sdr_executable: Path | None

    # GNSS-SDR repository/build/install locations used by the runtime launcher.
    gnss_sdr_repo_dir: Path
    gnss_sdr_build_dir: Path
    gnss_sdr_install_dir: Path

    # Runtime FIFO/output and GNSS-SDR diagnostics stay under the application
    # logs tree, even if the GNSS-SDR source/build/install repo lives elsewhere.
    gnss_sdr_runtime_dir: Path
    gnss_sdr_log_dir: Path

    # Optional live terminal echo for GNSS-SDR's clean console stream. Product
    # GUI runs keep this quiet by default; clean receiver status is captured in
    # runtime/console.log and GNSS-SDR diagnostics in gnss_sdr_log_dir/receiver.log.
    gnss_sdr_echo_stdout: bool

    # Static truth data used for PVT error display in the GUI/logs.
    gnss_truth_static_lat_deg: float | None
    gnss_truth_static_lon_deg: float | None
    gnss_truth_static_alt_m: float | None

    # PVT truth-error display parameters.
    gnss_accuracy_window_points: int
    gnss_accuracy_log_interval_s: float

    # Template used to render the GNSS-SDR FIFO receiver config.
    gnss_sdr_config_template: Path

    # GNSS-SDR PVT solver mode rendered into the RTKLIB_PVT block.
    gnss_pvt_positioning_mode: str

    # Local GNSS-SDR assistance XML loaded before receiver startup. These files
    # are kept outside the runtime output tree because that tree is cleared at
    # every stream start.
    gnss_agnss_xml_enable: bool
    gnss_agnss_gps_ephemeris_xml: Path
    gnss_agnss_gal_ephemeris_xml: Path
    gnss_agnss_gal_utc_model_xml: Path
    gnss_agnss_gal_almanac_xml: Path
    gnss_agnss_ref_location: str
    gnss_agnss_ref_utc_time: str
    gnss_tow_to_trk: bool

    # -------------------------------------------------------------------------
    # GNSS-SDR GPS L1 C/A Receiver Profile
    # -------------------------------------------------------------------------

    # Sample representation expected by the GNSS-SDR SignalSource.
    gnss_sdr_sample_type: str

    # Signal conditioner.
    #
    # IF filter bandwidth rendered into the GNSS-SDR config.
    # A value of 0.0 lets GNSS-SDR/default configuration behavior apply.
    gnss_sdr_if_bandwidth_hz: float

    # Channel allocation and acquisition.
    #
    # GPS L1 C/A and Galileo E1B channel allocation. Keep explicit pools so the
    # rendered GNSS-SDR config gives GPS and Galileo enough independent tracking
    # channels for the realtime receiver view.
    gnss_1c_channel_count: int
    gnss_1b_channel_count: int
    gnss_channels_in_acquisition: int

    # GNSS-SDR UDP monitor streams. GNSS-SDR dump outputs are rendered off in
    # the FIFO template; realtime receiver state comes from localhost UDP,
    # NMEA PTY, stdout, and glog only.
    gnss_pvt_monitor_enable: bool
    gnss_pvt_monitor_client_addresses: str
    gnss_pvt_monitor_udp_port: str
    gnss_pvt_monitor_enable_protobuf: bool
    gnss_monitor_enable: bool
    gnss_monitor_client_addresses: str
    gnss_monitor_udp_port: str
    gnss_monitor_enable_protobuf: bool
    gnss_monitor_decimation_factor: int
    gnss_tracking_monitor_enable: bool
    gnss_tracking_monitor_client_addresses: str
    gnss_tracking_monitor_udp_port: str
    gnss_tracking_monitor_enable_protobuf: bool
    gnss_tracking_monitor_decimation_factor: int

    # GNSS-SDR NMEA tty stream. MonitorPvt UDP gives the PVT satellite count,
    # but the per-satellite used-in-fix list comes from GSA sentences. Keep NMEA
    # on a PTY so realtime runs do not poll or grow NMEA files.
    gnss_pvt_nmea_tty_enable: bool
    gnss_pvt_nmea_output_file_enable: bool
    gnss_pvt_nmea_rate_ms: int

    # Acquisition settings tuned for robust lab/realtime GPS L1 C/A startup.
    gnss_acquisition_coherent_integration_ms: int
    gnss_acquisition_pfa: float
    gnss_acquisition_doppler_max_hz: int
    gnss_acquisition_doppler_step_hz: int
    gnss_acquisition_bit_transition_flag: bool
    gnss_acquisition_max_dwells: int

    # Tracking loop settings rendered explicitly so realtime runs do not depend
    # on GNSS-SDR build defaults. GPS values start from GNSS-SDR's TTFF/system
    # tests; Galileo values start from the documented E1B example.
    gnss_tracking_1c_pll_bw_hz: float
    gnss_tracking_1c_dll_bw_hz: float
    gnss_tracking_1c_pll_filter_order: int
    gnss_tracking_1c_dll_filter_order: int
    gnss_tracking_1c_early_late_space_chips: float
    gnss_tracking_1c_early_late_space_narrow_chips: float
    gnss_tracking_1c_pll_bw_narrow_hz: float
    gnss_tracking_1c_dll_bw_narrow_hz: float
    gnss_tracking_1c_extend_correlation_symbols: int
    gnss_tracking_1c_enable_fll_pull_in: bool
    gnss_tracking_1c_enable_fll_steady_state: bool
    gnss_tracking_1c_fll_bw_hz: float
    gnss_tracking_1c_pull_in_time_s: int
    gnss_tracking_1c_bit_synchronization_time_limit_s: int
    gnss_tracking_1b_pll_bw_hz: float
    gnss_tracking_1b_dll_bw_hz: float
    gnss_tracking_1b_pll_filter_order: int
    gnss_tracking_1b_dll_filter_order: int
    gnss_tracking_1b_early_late_space_chips: float
    gnss_tracking_1b_very_early_late_space_chips: float
    gnss_tracking_1b_extend_correlation_symbols: int
    gnss_tracking_1b_enable_fll_pull_in: bool
    gnss_tracking_1b_enable_fll_steady_state: bool
    gnss_tracking_1b_fll_bw_hz: float
    gnss_tracking_1b_pull_in_time_s: int
    gnss_tracking_1b_bit_synchronization_time_limit_s: int
    gnss_telemetry_1b_use_reduced_ced: bool
    gnss_telemetry_1b_enable_reed_solomon: bool

    # -------------------------------------------------------------------------
    # Internal Queues and Log Throttling
    # -------------------------------------------------------------------------

    # GNSS pipeline: recv thread -> raw queue -> ordered handoff worker -> FIFO.
    # The product queue holds about 4.19 s of four-channel complex64 IQ at the
    # 8.192 ms chunk cadence, absorbing measured FIFO stalls without skipping
    # contiguous IQ or blocking the USRP receive loop immediately.
    gnss_feed_queue_maxsize: int

    # Log receive pacing every N raw chunks with samples.
    rx_health_log_interval_chunks: int

    # Limit expensive/high-volume DoA log emissions.
    doa_log_interval_s: float

    # -------------------------------------------------------------------------
    # Derived Config Views
    # -------------------------------------------------------------------------

    def angle_scan_spec(self) -> AngleScanSpec:
        """Return the DoA scan range in the algorithm-layer model format."""

        return AngleScanSpec(
            min_deg=float(self.doa_min_deg),
            max_deg=float(self.doa_max_deg),
            points=int(self.doa_points),
        )

__all__ = ["DEFAULT_RUNTIME_CONFIG_PATH", "StreamConfig"]
