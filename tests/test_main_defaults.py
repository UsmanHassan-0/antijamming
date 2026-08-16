from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from threadpoolctl import threadpool_info

from antijamming.config import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    REPO_ROOT,
    default_stream_config,
    load_stream_config_file,
)
from antijamming.logging import LOGGER_DEFS, reset_session_logs, setup_logging
from antijamming.logging.setup import ImmediateFileHandler
from antijamming.app.main import _runtime_config, parse_args
from antijamming.app.runtime_config import build_runtime_config
from antijamming.config.schemas.runtime import VALID_LCMV_METHODS
from antijamming.rf.budget import manifest_from_config
from antijamming.radio.usrp.uhd_events import (
    UhdConsoleMarkerMonitor,
    UhdConsoleMarkerScanner,
    UhdMarkerEvent,
)


def test_product_runtime_limits_native_numeric_thread_pools() -> None:
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        assert os.environ[name] == "1"
    assert all(int(pool["num_threads"]) == 1 for pool in threadpool_info())


def test_default_runtime_spec_file_supplies_hardware_defaults() -> None:
    profile = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = default_stream_config()

    assert DEFAULT_RUNTIME_CONFIG_PATH.exists()
    assert DEFAULT_RUNTIME_CONFIG_PATH.as_posix().endswith(
        "configs/antijamming/x300_realtime.json"
    )
    assert cfg.array_spacing_m > 0.0
    assert cfg.usrp_addr == profile["usrp_addr"]
    assert cfg.recv_frame_size == 8000
    assert cfg.send_frame_size == 8000
    assert cfg.recv_buff_size == 50_000_000
    assert cfg.num_recv_frames == 4096
    assert cfg.rx_antennas_by_channel == ("RX1", "RX2", "RX1", "RX2")
    assert cfg.sample_rate == float(profile["sample_rate"])
    assert cfg.center_freq_hz == 1_575_420_000.0
    assert cfg.usrp_rx_bandwidth_hz == float(profile["sample_rate"])
    assert cfg.log_dir == REPO_ROOT / "logs"
    assert cfg.gnss_sdr_runtime_dir == REPO_ROOT / "logs/gnss-sdr/runtime"
    assert cfg.gnss_sdr_log_dir == REPO_ROOT / "logs/gnss-sdr/glog"
    assert cfg.gnss_agnss_xml_enable is False
    assert cfg.gnss_agnss_gps_ephemeris_xml == (
        REPO_ROOT / "configs/gnss-sdr/assistance/gps_ephemeris.xml"
    )
    assert cfg.gnss_agnss_gps_ephemeris_xml.exists()
    assert cfg.gnss_agnss_ref_location == ""
    assert cfg.gnss_agnss_ref_utc_time == ""
    assert cfg.gnss_tow_to_trk is True
    assert cfg.gnss_truth_static_lat_deg == 37.352721
    assert cfg.gnss_truth_static_lon_deg == -121.915773
    assert cfg.gnss_truth_static_alt_m == 100.0
    assert cfg.gnss_sdr_echo_stdout is False
    assert cfg.ui_update_interval_s == 0.1
    assert cfg.dsp_update_interval_s == 0.1
    assert cfg.prn_chart_update_interval_s == 0.1
    assert cfg.skyplot_update_interval_s == 0.1
    assert cfg.lcmv_test_enabled is False
    assert cfg.lcmv_test_max_weight_norm == 8.0
    assert cfg.lcmv_test_condition_number_limit == 100_000_000.0
    assert cfg.lcmv_test_null_method == "covariance_lcmv_ideal"
    assert cfg.lcmv_preserve_constraint_mode == "realtime_bladerf_measured_u1"
    assert cfg.lcmv_target_selection_mode == "realtime_non_preserve_peak"
    assert cfg.lcmv_realtime_preserve_window_samples == 40
    assert cfg.lcmv_realtime_preserve_min_samples == 20
    assert cfg.lcmv_realtime_preserve_max_circular_std_deg == 15.0
    assert cfg.lcmv_realtime_preserve_max_step_deg == 30.0
    assert cfg.lcmv_realtime_preserve_guard_deg == 20.0
    assert cfg.lcmv_realtime_preserve_max_reference_age_s == 2.0
    assert cfg.lcmv_jammer_activation_min_input_power_jump_db == 3.0
    assert cfg.lcmv_jammer_activation_min_generalized_gain_db == 6.0
    assert cfg.lcmv_weight_transition_s == 1.0
    assert VALID_LCMV_METHODS == {
        "covariance_lcmv_ideal",
        "covariance_lcmv_measured_u1",
    }
    assert cfg.lcmv_candidate_methods_enabled is True
    assert cfg.lcmv_covariance_diagonal_loading_rel == 0.001
    assert cfg.lcmv_covariance_diagonal_loading_abs == 0.0
    assert cfg.lcmv_max_weight_norm == 8.0
    assert cfg.lcmv_max_white_noise_gain_db == 15.0
    assert cfg.lcmv_min_predicted_jammer_suppression_db == 18.0
    assert cfg.lcmv_heavy_diagnostics_interval_s == 1.0
    assert cfg.one_run_segmentation_enabled is True
    assert cfg.healthy_reference_capture_enabled is True
    assert cfg.process_every_n_chunks == 15
    assert cfg.samples_per_chunk == 32768
    assert cfg.gnss_feed_queue_maxsize == 512
    assert cfg.auto_rate_backoff is False
    assert cfg.min_sample_rate == cfg.sample_rate
    assert cfg.stop_on_overflow is True
    assert cfg.rx_clipping_component_threshold == 0.98
    assert cfg.rx_clipping_fraction_threshold == 0.001
    assert "/tmp" not in cfg.gnss_sdr_runtime_dir.as_posix()
    assert "/tmp" not in cfg.gnss_sdr_log_dir.as_posix()
    assert cfg.gnss_1c_channel_count == 9
    assert cfg.gnss_pvt_elevation_mask_deg == 15.0
    assert cfg.gnss_channels_in_acquisition == 1
    assert cfg.gnss_pvt_monitor_enable is True
    assert cfg.gnss_pvt_monitor_client_addresses == "127.0.0.1"
    assert cfg.gnss_pvt_monitor_udp_port == "1111"
    assert cfg.gnss_pvt_monitor_enable_protobuf is True
    assert cfg.gnss_monitor_enable is True
    assert cfg.gnss_monitor_client_addresses == "127.0.0.1"
    assert cfg.gnss_monitor_udp_port == "1112"
    assert cfg.gnss_monitor_enable_protobuf is True
    assert cfg.gnss_monitor_decimation_factor == 1700
    assert cfg.gnss_tracking_monitor_enable is True
    assert cfg.gnss_tracking_monitor_client_addresses == "127.0.0.1"
    assert cfg.gnss_tracking_monitor_udp_port == "1236"
    assert cfg.gnss_tracking_monitor_enable_protobuf is True
    assert cfg.gnss_tracking_monitor_decimation_factor == 10
    assert cfg.gnss_pvt_nmea_tty_enable is True
    assert cfg.gnss_pvt_nmea_output_file_enable is False
    assert cfg.gnss_pvt_nmea_rate_ms == 1000
    assert cfg.gnss_acquisition_pfa == 0.01
    assert cfg.gnss_acquisition_doppler_max_hz == 10000
    assert cfg.gnss_acquisition_doppler_step_hz == 250
    assert cfg.gnss_acquisition_max_dwells == 1
    assert cfg.gnss_tracking_1c_pll_bw_hz == 35.0
    assert cfg.gnss_tracking_1c_dll_bw_hz == 0.5
    assert cfg.gnss_tracking_1c_early_late_space_chips == 0.25
    assert cfg.gnss_tracking_1c_early_late_space_narrow_chips == 0.15
    assert cfg.gnss_tracking_1c_pll_bw_narrow_hz == 5.0
    assert cfg.gnss_tracking_1c_dll_bw_narrow_hz == 0.75
    assert cfg.gnss_tracking_1c_extend_correlation_symbols == 1
    assert cfg.gnss_tracking_1c_enable_fll_pull_in is True
    assert cfg.gnss_tracking_1c_enable_fll_steady_state is False
    assert cfg.gnss_tracking_1c_fll_bw_hz == 10.0
    assert cfg.gnss_tracking_1c_pull_in_time_s == 2
    assert cfg.gnss_tracking_1c_bit_synchronization_time_limit_s == 30
    assert cfg.gnss_pvt_positioning_mode == "PPP_Static"
    assert cfg.phase_calibration_file == (
        REPO_ROOT / "configs/calibration/x300_phase_offsets_added_hw_100khz.json"
    )
    assert cfg.phase_calibration_file.exists()
    assert cfg.calibration_correction_mode == "complex_gain"
    assert cfg.rx_lo_sources_by_channel == (
        "internal",
        "companion",
        "reimport",
        "reimport",
    )
    assert cfg.experiment["rx_chain"] == "antenna->cable->BPF->LNA->DC_block->cable->TwinRX"
    assert cfg.expected_sources == 1
    assert cfg.gnss_accuracy_window_points == 1
    assert cfg.experiment["jammer_attenuation_db"] == 50.0
    assert cfg.experiment["jammer_attenuation_db_min"] == 0.0
    assert cfg.experiment["jammer_attenuation_db_max"] == 90.0
    assert cfg.experiment["bladeRF_tx_gain_db"] == 50.0
    assert cfg.experiment["bladeRF_distance_m"] == pytest.approx(3.4798)
    assert cfg.experiment["jammer_distance_m"] == pytest.approx(2.7432)


def test_runtime_config_experiment_section_is_optional(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("experiment", None)
    path = tmp_path / "runtime_without_experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)

    assert cfg.experiment == {}


def test_runtime_profile_authors_sample_rate_once_and_derives_followers(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "usrp_rx_bandwidth_hz" not in payload
    assert "gnss_sdr_if_bandwidth_hz" not in payload
    assert "min_sample_rate" not in payload
    assert "sample_rate_sps" not in payload["experiment"]
    assert "rx_bandwidth_hz" not in payload["experiment"]

    payload["sample_rate"] = 6_250_000
    path = tmp_path / "runtime_with_one_sample_rate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)
    manifest = manifest_from_config(cfg)

    assert cfg.sample_rate == 6_250_000.0
    assert cfg.usrp_rx_bandwidth_hz == 6_250_000.0
    assert cfg.min_sample_rate == 6_250_000.0
    assert manifest["sample_rate_sps"] == 6_250_000.0
    assert manifest["rx_bandwidth_hz"] == 6_250_000.0
    assert manifest["bandwidth_hz"] == 6_250_000.0


@pytest.mark.parametrize(
    "field",
    ["usrp_rx_bandwidth_hz", "min_sample_rate"],
)
def test_runtime_profile_rejects_authored_sample_rate_followers(tmp_path, field) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload[field] = payload["sample_rate"]
    path = tmp_path / f"runtime_with_duplicate_{field}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be authored"):
        load_stream_config_file(path)


@pytest.mark.parametrize("field", ["sample_rate_sps", "rx_bandwidth_hz"])
def test_runtime_profile_rejects_experiment_rate_duplicates(tmp_path, field) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["experiment"][field] = payload["sample_rate"]
    path = tmp_path / f"runtime_with_duplicate_experiment_{field}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be authored"):
        load_stream_config_file(path)


def test_runtime_config_calibration_mode_defaults_to_complex_gain(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("calibration_correction_mode", None)
    path = tmp_path / "runtime_without_calibration_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)

    assert cfg.calibration_correction_mode == "complex_gain"


def test_runtime_config_gnss_startup_timeout_defaults_to_unlimited(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("gnss_sdr_startup_timeout_s", None)
    path = tmp_path / "runtime_without_gnss_startup_timeout.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)

    assert cfg.gnss_sdr_startup_timeout_s == 0.0


def test_runtime_config_lcmv_method_defaults_to_covariance_ideal(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("lcmv_test_null_method", None)
    path = tmp_path / "runtime_without_lcmv_method.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)

    assert cfg.lcmv_test_null_method == "covariance_lcmv_ideal"


@pytest.mark.parametrize(
    "method",
    ["ideal_steering", "ideal_angle_fan", "measured_dominant_eigenvector"],
)
def test_runtime_config_rejects_legacy_lcmv_methods(tmp_path, method) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["lcmv_test_null_method"] = method
    path = tmp_path / f"runtime_with_{method}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid lcmv_test_null_method"):
        load_stream_config_file(path)


def test_runtime_config_loads_operator_experiment_section(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["experiment"] = {
        "name": "unit_test",
        "jammer_attenuation_db": 80.0,
    }
    path = tmp_path / "runtime_with_experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)

    assert cfg.experiment["name"] == "unit_test"
    assert cfg.experiment["jammer_attenuation_db"] == 80.0


def test_product_shell_entrypoints_are_parseable() -> None:
    subprocess.run(
        [
            "bash",
            "-n",
            "run_realtime.sh",
            "setup.sh",
            "run_tests.sh",
        ],
        cwd=REPO_ROOT,
        check=True,
    )


def test_project_docs_live_under_docs_directory() -> None:
    root_markdown = sorted(
        path.name
        for path in REPO_ROOT.glob("*.md")
        if path.name.lower() not in {"license.md", "agents.md"}
    )

    assert root_markdown == []
    assert (REPO_ROOT / "docs/realtime_gui.md").exists()
    assert (REPO_ROOT / "docs/hardware.md").exists()
    assert (REPO_ROOT / "docs/architecture_refactor_notes.md").exists()
    for name in (
        "00_system_architecture.md",
        "01_rf_hardware_and_link_budget.md",
        "02_calibration.md",
        "03_steering_model_and_angles.md",
        "04_doa_music_bartlett.md",
        "05_beamforming_algorithms.md",
        "06_diagnostics_and_metrics.md",
        "07_one_run_test_method.md",
        "08_progress_tracker.md",
        "09_known_failure_modes.md",
        "10_next_steps_and_open_questions.md",
    ):
        assert (REPO_ROOT / "docs" / name).exists()


def test_runtime_logs_are_repo_anchored_from_other_working_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    cfg = default_stream_config()

    assert cfg.log_dir == REPO_ROOT / "logs"
    assert cfg.gnss_sdr_runtime_dir == REPO_ROOT / "logs/gnss-sdr/runtime"
    assert cfg.gnss_sdr_log_dir == REPO_ROOT / "logs/gnss-sdr/glog"


def test_default_runtime_config_requires_json_profile(tmp_path) -> None:
    missing = tmp_path / "missing_runtime_profile.json"

    with pytest.raises(FileNotFoundError):
        default_stream_config(missing)


def test_default_stream_config_rejects_missing_json_profile_path() -> None:
    with pytest.raises(ValueError):
        default_stream_config(None)



def test_runtime_has_core_signal_processing_logs() -> None:
    assert LOGGER_DEFS["doa"][1] == "doa.log"
    assert LOGGER_DEFS["lcmv"][1] == "lcmv.log"
    assert LOGGER_DEFS["analysis"][1] == "analysis.log"
    assert LOGGER_DEFS["lcmv_pattern"][1] == "lcmv_pattern_absolute.jsonl"
    assert LOGGER_DEFS["spatial_vector"][1] == "spatial_vector_diagnostics.jsonl"
    assert LOGGER_DEFS["runtime_evidence"][1] == "runtime_evidence.jsonl"
    assert "jammer" not in LOGGER_DEFS
    assert LOGGER_DEFS["ui"][1] == "ui_health.log"


def test_runtime_file_logs_use_immediate_handlers(tmp_path) -> None:
    loggers = setup_logging(tmp_path)

    assert all(
        any(isinstance(handler, ImmediateFileHandler) for handler in logger.handlers)
        for logger in loggers.values()
    )


def test_session_log_reset_removes_rotated_backups(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    rotated = tmp_path / "gnss_sdr.log.1"
    rotated.write_text("old rotated log", encoding="utf-8")
    helper_log = tmp_path / "remote_gui_access" / "rdp_autostart_gui.log"
    helper_log.parent.mkdir(parents=True)
    helper_log.write_text("old helper log", encoding="utf-8")
    nested_log = tmp_path / "gnss-sdr" / "runtime" / "console.log"
    nested_log.parent.mkdir(parents=True)
    nested_log.write_text("old console log", encoding="utf-8")
    output_file = tmp_path / "gnss-sdr" / "runtime" / "outputs" / "pvt-output.txt"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("keep output", encoding="utf-8")

    reset_session_logs(tmp_path, loggers)

    assert (tmp_path / "gnss_sdr.log").read_text(encoding="utf-8") == ""
    assert helper_log.read_text(encoding="utf-8") == "old helper log"
    assert nested_log.read_text(encoding="utf-8") == "old console log"
    assert output_file.read_text(encoding="utf-8") == "keep output"
    assert not rotated.exists()


def test_uhd_console_marker_scanner_timestamps_marker_prefixes() -> None:
    scanner = UhdConsoleMarkerScanner()

    events = scanner.feed(
        "2026-Jun-11 13:03:32.091648,UHD startup\n"
        "DD2026-06-11 14:28:44,527 | INFO | runtime timing\n"
        "O\n"
        "DEBUG line should not count as a marker\n"
    )

    assert [(event.marker, event.count) for event in events] == [("D", 2), ("O", 1)]


def test_uhd_console_marker_scanner_handles_split_writes() -> None:
    scanner = UhdConsoleMarkerScanner()

    assert scanner.feed("D") == []
    events = scanner.feed("D")
    events.extend(scanner.feed("2026-06-11 next log line"))

    assert [(event.marker, event.count) for event in events] == [("D", 2)]


def test_uhd_console_marker_scanner_ignores_single_marker_glued_to_qt_warning() -> None:
    scanner = UhdConsoleMarkerScanner()

    events = scanner.feed("DThis plugin does not support propagateSizeHints()\n")

    assert events == []


def test_uhd_console_marker_monitor_logs_window_counts(tmp_path) -> None:
    loggers = setup_logging(tmp_path)
    monitor = UhdConsoleMarkerMonitor(
        tmp_path / "uhd_console.log",
        loggers,
        sample_rate_hz=4_000_000.0,
        channel_count=4,
        samples_per_chunk=32768,
    )
    monitor._started_monotonic = 1.0
    monitor._window_started_monotonic = 1.0

    monitor._log_event(UhdMarkerEvent(marker="D", count=2, byte_offset=123))
    monitor._log_summary(force=True)

    transport_log = (tmp_path / "transport.log").read_text(encoding="utf-8")
    assert "UHD console marker: marker=D count=2" in transport_log
    assert "UHD console marker window:" in transport_log
    assert "D=2 O=0" in transport_log
    assert "sample_rate_hz=4000000.000" in transport_log
    assert "channel_count=4" in transport_log
    assert "samples_per_chunk=32768" in transport_log
    assert "samples_per_D=unknown" in transport_log


def test_parse_args_accepts_no_runtime_flags(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["antijamming.app.main"])
    args = parse_args()

    assert args.auto_start is False
    assert args.auto_stop_after_s is None
    assert args.quit_after_stop is False


def test_parse_args_accepts_diagnostic_control_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "antijamming.app.main",
            "--auto-start",
            "--auto-stop-after-s",
            "30",
            "--quit-after-stop",
        ],
    )
    args = parse_args()

    assert args.auto_start is True
    assert args.auto_stop_after_s == 30.0
    assert args.quit_after_stop is True


def test_parse_args_rejects_runtime_flags(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["antijamming.app.main", "--sample-rate", "4000000"])

    with pytest.raises(SystemExit):
        parse_args()


def test_runtime_config_builds_product_profile_without_cli_overrides() -> None:
    profile = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = _runtime_config()

    assert cfg.sample_rate == float(profile["sample_rate"])


def test_runtime_config_applies_opt_in_partial_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    overlay = tmp_path / "runtime_test.json"
    overlay.write_text(
        json.dumps(
            {
                "gnss_pvt_elevation_mask_deg": 5.0,
                "expected_sources": 2,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTIJAM_RUNTIME_OVERLAY", str(overlay))

    cfg = build_runtime_config()

    assert cfg.gnss_pvt_elevation_mask_deg == 5.0
    assert cfg.expected_sources == 2
    assert cfg.sample_rate == 4_000_000.0
    assert cfg.center_freq_hz == 1_575_420_000.0
    assert cfg.auto_rate_backoff is False
    assert cfg.stop_on_overflow is True
    assert cfg.rx_clipping_component_threshold == 0.98
    assert cfg.rx_clipping_fraction_threshold == 0.001
    assert cfg.ui_update_interval_s == 0.1
    assert cfg.dsp_update_interval_s == 0.1
    assert cfg.prn_chart_update_interval_s == 0.1
    assert cfg.skyplot_update_interval_s == 0.1
    assert cfg.process_every_n_chunks == 15
    assert cfg.samples_per_chunk == 32768
    assert cfg.gnss_feed_queue_maxsize == 512
    assert cfg.usrp_addr.startswith(str(profile["usrp_addr"]))
    assert "recv_frame_size=8000" in cfg.usrp_addr
    assert "send_frame_size=8000" in cfg.usrp_addr
    assert cfg.recv_frame_size == 8000
    assert cfg.send_frame_size == 8000
    assert "recv_buff_size=50000000" in cfg.usrp_addr
    assert "num_recv_frames=4096" in cfg.usrp_addr
    assert cfg.gnss_sdr_echo_stdout is False
    assert cfg.gnss_1c_channel_count == 9
    assert cfg.gnss_channels_in_acquisition == 1
    assert cfg.phase_calibration_file is not None
    assert cfg.phase_calibration_file.is_absolute()
    assert cfg.phase_correction_vector is not None


def test_shared_u1_g10_lab_overlay_builds_complete_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = (
        REPO_ROOT
        / "configs/antijamming/shared_u1_phase_g10_5deg_atten50_gain29_test.json"
    )
    monkeypatch.setenv("ANTIJAM_RUNTIME_OVERLAY", str(overlay))

    cfg = build_runtime_config()

    assert cfg.gnss_shared_u1_phase_compensation_enabled is True
    assert cfg.gnss_shared_u1_phase_satellites == (
        5,
        10,
        13,
        15,
        16,
        18,
        23,
        25,
        26,
        29,
    )
    assert cfg.gnss_1c_channel_count == 10
    assert cfg.gnss_channels_in_acquisition == 10
    assert cfg.gnss_pvt_elevation_mask_deg == 5.0
    assert cfg.gain_db == 45.0
    assert cfg.experiment["bladeRF_tx_gain_db"] == 29.0
    assert cfg.experiment["jammer_attenuation_db"] == 50.0
    assert cfg.sample_rate == 4_000_000.0
    assert cfg.center_freq_hz == 1_575_420_000.0
