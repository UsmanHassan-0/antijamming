from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

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
from antijamming.app.main import (
    _cleanup_gui_owners,
    _reap_backend_process,
    _runtime_config,
    parse_args,
)
from antijamming.app.runtime_config import build_runtime_config
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
    assert cfg.logging_enabled is True
    assert cfg.gnss_1c_channel_count == 10
    assert cfg.gnss_channels_in_acquisition == 10
    assert cfg.ui_update_interval_s == 0.1
    assert cfg.dsp_update_interval_s == 0.1
    assert cfg.prn_chart_update_interval_s == 0.1
    assert cfg.skyplot_update_interval_s == 0.1
    assert cfg.lcmv_test_enabled is False
    assert cfg.lcmv_condition_number_limit == 100_000_000.0
    assert cfg.lcmv_realtime_preserve_window_samples == 40
    assert cfg.lcmv_realtime_preserve_min_samples == 20
    assert cfg.lcmv_realtime_preserve_max_circular_std_deg == 15.0
    assert cfg.lcmv_realtime_preserve_max_step_deg == 30.0
    assert cfg.lcmv_realtime_preserve_guard_deg == 20.0
    assert cfg.lcmv_realtime_preserve_max_reference_age_s == 2.0
    assert cfg.lcmv_jammer_activation_min_input_power_jump_db == 3.0
    assert cfg.lcmv_jammer_activation_min_generalized_gain_db == 6.0
    assert cfg.lcmv_weight_transition_s == 1.0
    assert cfg.lcmv_covariance_diagonal_loading_rel == 0.001
    assert cfg.lcmv_covariance_diagonal_loading_abs == 0.0
    assert cfg.lcmv_max_weight_norm == 8.0
    assert cfg.lcmv_max_white_noise_gain_db == 15.0
    assert cfg.lcmv_min_predicted_jammer_suppression_db == 18.0
    assert cfg.lcmv_heavy_diagnostics_interval_s == 1.0
    assert cfg.one_run_segmentation_enabled is True
    assert cfg.lcmv_auto_arm_after_pvt is True
    assert cfg.process_every_n_chunks == 15
    assert cfg.samples_per_chunk == 32768
    assert cfg.gnss_feed_queue_maxsize == 512
    assert cfg.min_sample_rate == cfg.sample_rate
    assert cfg.stop_on_overflow is True
    assert cfg.rx_clipping_component_threshold == 0.98
    assert cfg.rx_clipping_fraction_threshold == 0.001
    assert "/tmp" not in cfg.gnss_sdr_runtime_dir.as_posix()
    assert "/tmp" not in cfg.gnss_sdr_log_dir.as_posix()
    assert cfg.gnss_1c_channel_count == 10
    assert cfg.gnss_pvt_elevation_mask_deg == 15.0
    assert cfg.gnss_channels_in_acquisition == 10
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
    assert cfg.expected_sources == 1
    assert cfg.gnss_accuracy_window_points == 1


def test_launcher_uses_runtime_logging_switch_for_all_diagnostic_persistence() -> None:
    launcher = Path("run_realtime.sh").read_text(encoding="utf-8")
    sidecar = Path("tools/run_realtime_sidecar.sh").read_text(encoding="utf-8")

    assert '"logging_enabled"' in launcher
    assert 'if [[ "${RUNTIME_LOGGING_ENABLED}" == "1" ]]' in launcher
    assert "ANTIJAM_SIDECAR:-" not in launcher
    assert "unset UHD_LOG_FILE" in launcher
    assert "/home/qvise/antijamming" not in sidecar


def test_runtime_config_rejects_stale_experiment_section(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["experiment"] = {"name": "legacy_bench_manifest"}
    path = tmp_path / "runtime_with_stale_experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key.*experiment"):
        load_stream_config_file(path)


def test_runtime_profile_authors_sample_rate_once_and_derives_followers(
    tmp_path,
) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "usrp_rx_bandwidth_hz" not in payload
    assert "gnss_sdr_if_bandwidth_hz" not in payload
    assert "min_sample_rate" not in payload

    payload["sample_rate"] = 6_250_000
    path = tmp_path / "runtime_with_one_sample_rate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_stream_config_file(path)
    assert cfg.sample_rate == 6_250_000.0
    assert cfg.usrp_rx_bandwidth_hz == 6_250_000.0
    assert cfg.min_sample_rate == 6_250_000.0


@pytest.mark.parametrize(
    "field",
    ["usrp_rx_bandwidth_hz", "min_sample_rate"],
)
def test_runtime_profile_rejects_authored_sample_rate_followers(
    tmp_path, field
) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload[field] = payload["sample_rate"]
    path = tmp_path / f"runtime_with_duplicate_{field}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be authored"):
        load_stream_config_file(path)


def test_runtime_config_requires_explicit_calibration_mode(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("calibration_correction_mode", None)
    path = tmp_path / "runtime_without_calibration_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required JSON key.*calibration_correction_mode"):
        load_stream_config_file(path)


def test_runtime_config_rejects_unknown_calibration_mode(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["calibration_correction_mode"] = "typo_mode"
    path = tmp_path / "runtime_with_unknown_calibration_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="calibration_correction_mode.*must be one of"):
        load_stream_config_file(path)


def test_runtime_config_requires_explicit_gnss_startup_timeout(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.pop("gnss_sdr_startup_timeout_s", None)
    path = tmp_path / "runtime_without_gnss_startup_timeout.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required JSON key.*gnss_sdr_startup_timeout_s"):
        load_stream_config_file(path)


def test_runtime_config_rejects_removed_lcmv_method_selector(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["lcmv_test_null_method"] = "covariance_lcmv_measured_u1"
    path = tmp_path / "runtime_with_removed_lcmv_method_selector.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key"):
        load_stream_config_file(path)

def test_runtime_config_rejects_removed_lcmv_target_selector(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["lcmv_target_selection_mode"] = (
        "expected_jammer_range_peak_or_center"
    )
    path = tmp_path / "runtime_with_legacy_expected_bearing_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key"):
        load_stream_config_file(path)


def test_runtime_config_rejects_removed_usrp_preservation_mode(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["preserve_usrp_session_on_stop"] = True
    path = tmp_path / "runtime_with_removed_usrp_preservation_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key"):
        load_stream_config_file(path)


def test_runtime_config_rejects_removed_optional_local_gnss_mode(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["gnss_sdr_require_local"] = False
    path = tmp_path / "runtime_with_optional_local_gnss_mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key"):
        load_stream_config_file(path)


def test_runtime_config_rejects_removed_second_nmea_persistence_switch(tmp_path) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["gnss_pvt_nmea_output_file_enable"] = True
    path = tmp_path / "runtime_with_removed_nmea_persistence_switch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown runtime config key"):
        load_stream_config_file(path)


def test_runtime_config_rejects_duplicate_json_keys(tmp_path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"sample_rate": 4000000, "sample_rate": 8000000}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="duplicate JSON key 'sample_rate'"):
        load_stream_config_file(path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_runtime_config_rejects_non_finite_json_numbers(tmp_path, constant) -> None:
    path = tmp_path / "nonfinite.json"
    path.write_text(
        f'{{"sample_rate": {constant}}}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="non-finite JSON number"):
        load_stream_config_file(path)


@pytest.mark.parametrize("sample_rate", ["NaN", "Infinity", "-Infinity"])
def test_runtime_config_rejects_non_finite_string_sample_rate(
    tmp_path,
    sample_rate,
) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["sample_rate"] = sample_rate
    path = tmp_path / "nonfinite_string_sample_rate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="sample_rate.*JSON number"):
        load_stream_config_file(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("logging_enabled", "false", "logging_enabled.*JSON boolean"),
        ("samples_per_chunk", True, "samples_per_chunk.*JSON integer"),
        ("gain_db", "45", "gain_db.*JSON number"),
        ("channels", [0, 1, 2], "channels.*exactly 4 items"),
        ("channels", [0, 1, 2, "3"], r"channels\[3\].*JSON integer"),
        ("antenna", "RX2", "Unknown runtime config key.*antenna"),
        ("twinrx_lo_sharing", False, "Unknown runtime config key.*twinrx_lo_sharing"),
        ("phase_calibration_file", 7, "phase_calibration_file.*path string"),
    ],
)
def test_runtime_config_rejects_wrong_json_types(
    tmp_path,
    field,
    value,
    message,
) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload[field] = value
    path = tmp_path / f"runtime_with_wrong_{field}_type.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_stream_config_file(path)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"samples_per_chunk": 0}, "samples_per_chunk.*greater than zero"),
        (
            {"lcmv_min_predicted_jammer_suppression_db": -1.0},
            "lcmv_min_predicted_jammer_suppression_db.*nonnegative",
        ),
        ({"gnss_pvt_nmea_rate_ms": 99}, "gnss_pvt_nmea_rate_ms.*at least 100"),
        (
            {"gnss_pvt_elevation_mask_deg": 91.0},
            r"gnss_pvt_elevation_mask_deg.*\[-90, 90\]",
        ),
        (
            {"gnss_tracking_1c_pll_filter_order": 1},
            "gnss_tracking_1c_pll_filter_order.*at least 2",
        ),
        ({"channels": [1, 0, 2, 3]}, "channels.*exactly.*calibrated stream order"),
        (
            {"rx_antennas_by_channel": ["RX1", "RX2", "RX3", "RX2"]},
            "rx_antennas_by_channel.*RX1 or RX2",
        ),
        (
            {"rx_lo_sources_by_channel": ["internal", "companion", "guess", "reimport"]},
            "rx_lo_sources_by_channel.*unsupported",
        ),
        (
            {
                "lcmv_realtime_preserve_window_samples": 10,
                "lcmv_realtime_preserve_min_samples": 11,
            },
            "minimum samples must not exceed",
        ),
        ({"gnss_acquisition_pfa": 1.0}, "gnss_acquisition_pfa.*between 0 and 1"),
        (
            {"gnss_1c_channel_count": 4, "gnss_channels_in_acquisition": 5},
            "concurrency must not exceed",
        ),
        (
            {"gnss_monitor_udp_port": "1111"},
            "monitor UDP ports must be distinct",
        ),
        (
            {"gnss_sdr_sample_type": "short"},
            "gnss_sdr_sample_type.*gr_complex",
        ),
    ],
)
def test_runtime_config_rejects_inconsistent_values(
    tmp_path,
    updates,
    message,
) -> None:
    payload = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.update(updates)
    path = tmp_path / "runtime_with_inconsistent_values.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_stream_config_file(path)


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
    numbered_docs = (
        "00_system_architecture.md",
        "01_rf_hardware_boundary.md",
        "02_calibration.md",
        "03_steering_model_and_angles.md",
        "04_doa_music_bartlett.md",
        "05_beamforming_algorithms.md",
        "06_diagnostics_and_metrics.md",
        "07_one_run_test_method.md",
        "08_known_failure_modes.md",
    )
    assert (
        tuple(
            path.name for path in sorted((REPO_ROOT / "docs").glob("[0-9][0-9]_*.md"))
        )
        == numbered_docs
    )
    for name in numbered_docs:
        assert (REPO_ROOT / "docs" / name).exists()
    assert (REPO_ROOT / "docs/progress_tracker.md").exists()
    assert (REPO_ROOT / "docs/implementation_provenance.md").exists()
    assert (REPO_ROOT / "docs/audits/README.md").exists()


def test_runtime_logs_are_repo_anchored_from_other_working_directory(
    monkeypatch, tmp_path
) -> None:
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


def test_master_logging_switch_disables_and_reenables_all_named_file_loggers(
    tmp_path,
) -> None:
    log_dir = tmp_path / "disabled"
    disabled = setup_logging(log_dir, enabled=False)

    assert not log_dir.exists()
    assert all(logger.disabled for logger in disabled.values())
    assert all(not logger.handlers for logger in disabled.values())

    enabled = setup_logging(log_dir, enabled=True)
    assert log_dir.is_dir()
    assert all(not logger.disabled for logger in enabled.values())
    assert all(
        any(isinstance(handler, ImmediateFileHandler) for handler in logger.handlers)
        for logger in enabled.values()
    )


def test_repeated_logging_setup_closes_replaced_file_handlers(tmp_path) -> None:
    first = setup_logging(tmp_path / "first")
    replaced_handlers = [
        handler
        for logger in first.values()
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
    ]

    second = setup_logging(tmp_path / "second")

    assert len(replaced_handlers) == len(LOGGER_DEFS)
    assert all(handler.stream is None for handler in replaced_handlers)
    for logger in second.values():
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()


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


def test_uhd_console_marker_monitor_does_not_discard_live_thread_or_flush(
    tmp_path,
    monkeypatch,
) -> None:
    loggers = setup_logging(tmp_path)
    monitor = UhdConsoleMarkerMonitor(tmp_path / "uhd_console.log", loggers)
    entered = threading.Event()
    release = threading.Event()
    flush_calls: list[bool] = []

    def blocked_run() -> None:
        entered.set()
        assert release.wait(2.0)

    monkeypatch.setattr(monitor, "_run", blocked_run)
    monkeypatch.setattr(
        monitor._scanner,
        "flush",
        lambda: flush_calls.append(True) or [],
    )
    monitor.start()
    worker = monitor._thread
    assert worker is not None
    assert entered.wait(1.0)

    assert monitor.stop(timeout_s=0.01) is False
    assert monitor._thread is worker
    assert flush_calls == []

    release.set()
    worker.join(timeout=1.0)
    assert monitor.stop(timeout_s=0.1) is True
    assert monitor._thread is None
    assert flush_calls == [True]


def test_parse_args_accepts_no_runtime_flags(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["antijamming.app.main"])
    args = parse_args()

    assert args.auto_start is False
    assert args.auto_stop_after_s is None
    assert args.quit_after_stop is False


def test_backend_process_reaper_waits_after_sigkill(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 12345

        def __init__(self) -> None:
            self.wait_timeouts: list[float] = []

        def wait(self, *, timeout: float) -> int:
            self.wait_timeouts.append(timeout)
            if len(self.wait_timeouts) < 3:
                raise subprocess.TimeoutExpired("backend", timeout)
            return -signal.SIGKILL

    process = FakeProcess()
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        "antijamming.app.main.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )
    loggers = setup_logging(tmp_path)
    assert _reap_backend_process(
        process,  # type: ignore[arg-type]
        loggers,
        initial_wait_s=0.0,
    )

    assert process.wait_timeouts == [0.0, 5.0, 2.0]
    assert signals == [
        (12345, signal.SIGTERM),
        (12345, signal.SIGKILL),
    ]


def test_gui_cleanup_attempt_reports_incomplete_owners_for_retry(tmp_path) -> None:
    class RetryingMonitor:
        def __init__(self) -> None:
            self.results = iter((False, True))

        def stop(self) -> bool:
            return next(self.results)

    class RetryingWorker:
        def __init__(self) -> None:
            self.close_results = iter((False, True))
            self.shutdown_calls = 0

        @staticmethod
        def isRunning() -> bool:
            return False

        def shutdown_service(self, _reason: str) -> None:
            self.shutdown_calls += 1

        def close(self) -> bool:
            return next(self.close_results)

    class ExitedProcess:
        pid = 12345

        @staticmethod
        def wait(*, timeout: float) -> int:
            assert timeout == 18.0
            return 0

    loggers = setup_logging(tmp_path)
    monitor = RetryingMonitor()
    worker = RetryingWorker()
    process = ExitedProcess()

    assert not _cleanup_gui_owners(
        uhd_marker_monitor=monitor,
        worker=worker,
        backend_process=process,  # type: ignore[arg-type]
        loggers=loggers,
    )
    assert _cleanup_gui_owners(
        uhd_marker_monitor=monitor,
        worker=worker,
        backend_process=process,  # type: ignore[arg-type]
        loggers=loggers,
    )
    assert worker.shutdown_calls == 2


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
    monkeypatch.setattr(
        sys, "argv", ["antijamming.app.main", "--sample-rate", "4000000"]
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_runtime_config_builds_product_profile_without_cli_overrides() -> None:
    profile = json.loads(DEFAULT_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = _runtime_config()

    assert cfg.sample_rate == float(profile["sample_rate"])


def test_product_profile_uses_dynamic_shared_u1_phase_fanout() -> None:
    cfg = build_runtime_config()

    assert cfg.gnss_1c_channel_count == 10
    assert cfg.gnss_channels_in_acquisition == 10
    assert cfg.gain_db == 45.0
    assert cfg.sample_rate == 4_000_000.0
    assert cfg.center_freq_hz == 1_575_420_000.0
