from __future__ import annotations

import os
import subprocess
import sys

import pytest
from threadpoolctl import threadpool_info

from antijamming.config import DEFAULT_RUNTIME_CONFIG_PATH, REPO_ROOT, default_stream_config
from antijamming.logging import LOGGER_DEFS, reset_session_logs, setup_logging
from antijamming.logging.setup import ImmediateFileHandler
from antijamming.app.main import _runtime_config, parse_args
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
    cfg = default_stream_config()

    assert DEFAULT_RUNTIME_CONFIG_PATH.exists()
    assert DEFAULT_RUNTIME_CONFIG_PATH.as_posix().endswith(
        "configs/antijamming/x300_realtime.json"
    )
    assert cfg.array_spacing_m > 0.0
    assert cfg.usrp_addr == "addr=192.168.40.2"
    assert cfg.recv_frame_size == 8000
    assert cfg.send_frame_size == 8000
    assert cfg.recv_buff_size == 50_000_000
    assert cfg.num_recv_frames == 4096
    assert cfg.rx_antennas_by_channel == ("RX1", "RX2", "RX1", "RX2")
    assert cfg.sample_rate == 4_000_000.0
    assert cfg.center_freq_hz == 1_575_420_000.0
    assert cfg.usrp_rx_bandwidth_hz == 4_000_000.0
    assert cfg.gnss_sdr_if_bandwidth_hz == 4_000_000.0
    assert cfg.log_dir == REPO_ROOT / "logs"
    assert cfg.gnss_sdr_runtime_dir == REPO_ROOT / "logs/gnss-sdr/runtime"
    assert cfg.gnss_sdr_log_dir == REPO_ROOT / "logs/gnss-sdr/glog"
    assert cfg.gnss_agnss_xml_enable is False
    assert cfg.gnss_agnss_gps_ephemeris_xml == (
        REPO_ROOT / "configs/gnss-sdr/assistance/gps_ephemeris.xml"
    )
    assert cfg.gnss_agnss_gal_ephemeris_xml == (
        REPO_ROOT / "configs/gnss-sdr/assistance/gal_ephemeris.xml"
    )
    assert cfg.gnss_agnss_gal_utc_model_xml == (
        REPO_ROOT / "configs/gnss-sdr/assistance/gal_utc_model.xml"
    )
    assert cfg.gnss_agnss_gal_almanac_xml == (
        REPO_ROOT / "configs/gnss-sdr/assistance/gal_almanac.xml"
    )
    assert cfg.gnss_agnss_gps_ephemeris_xml.exists()
    assert cfg.gnss_agnss_gal_ephemeris_xml.exists()
    assert cfg.gnss_agnss_gal_utc_model_xml.exists()
    assert cfg.gnss_agnss_gal_almanac_xml.exists()
    assert cfg.gnss_agnss_ref_location == ""
    assert cfg.gnss_agnss_ref_utc_time == ""
    assert cfg.gnss_tow_to_trk is True
    assert cfg.gnss_truth_static_lat_deg == 33.6844
    assert cfg.gnss_truth_static_lon_deg == 73.0479
    assert cfg.gnss_truth_static_alt_m == 540.0
    assert cfg.gnss_sdr_echo_stdout is False
    assert cfg.ui_update_interval_s == 0.3333333333333333
    assert cfg.dsp_update_interval_s == 0.3333333333333333
    assert cfg.prn_chart_update_interval_s == 0.3333333333333333
    assert cfg.skyplot_update_interval_s == 0.3333333333333333
    assert cfg.process_every_n_chunks == 24
    assert cfg.samples_per_chunk == 32768
    assert cfg.gnss_feed_queue_maxsize == 512
    assert cfg.auto_rate_backoff is False
    assert cfg.min_sample_rate == cfg.sample_rate
    assert cfg.stop_on_overflow is True
    assert cfg.rx_clipping_component_threshold == 0.98
    assert cfg.rx_clipping_fraction_threshold == 0.001
    assert "/tmp" not in cfg.gnss_sdr_runtime_dir.as_posix()
    assert "/tmp" not in cfg.gnss_sdr_log_dir.as_posix()
    assert cfg.gnss_1c_channel_count == 15
    assert cfg.gnss_1b_channel_count == 15
    assert cfg.gnss_channels_in_acquisition == 30
    assert cfg.gnss_pvt_monitor_enable is True
    assert cfg.gnss_pvt_monitor_client_addresses == "127.0.0.1"
    assert cfg.gnss_pvt_monitor_udp_port == "1111"
    assert cfg.gnss_pvt_monitor_enable_protobuf is True
    assert cfg.gnss_monitor_enable is True
    assert cfg.gnss_monitor_client_addresses == "127.0.0.1"
    assert cfg.gnss_monitor_udp_port == "1112"
    assert cfg.gnss_monitor_enable_protobuf is True
    assert cfg.gnss_monitor_decimation_factor == 1
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
    assert cfg.gnss_tracking_1b_pll_bw_hz == 35.0
    assert cfg.gnss_tracking_1b_dll_bw_hz == 2.0
    assert cfg.gnss_tracking_1b_early_late_space_chips == 0.25
    assert cfg.gnss_tracking_1b_very_early_late_space_chips == 0.5
    assert cfg.gnss_tracking_1b_extend_correlation_symbols == 1
    assert cfg.gnss_tracking_1b_enable_fll_pull_in is True
    assert cfg.gnss_tracking_1b_enable_fll_steady_state is False
    assert cfg.gnss_tracking_1b_fll_bw_hz == 35.0
    assert cfg.gnss_tracking_1b_pull_in_time_s == 1
    assert cfg.gnss_tracking_1b_bit_synchronization_time_limit_s == 30
    assert cfg.gnss_telemetry_1b_use_reduced_ced is True
    assert cfg.gnss_telemetry_1b_enable_reed_solomon is False
    assert cfg.gnss_pvt_positioning_mode == "PPP_Static"
    assert cfg.algorithm_mode == "lcmv"
    assert cfg.phase_calibration_file == (
        REPO_ROOT / "configs/calibration/x300_phase_offsets_added_hw_100khz.json"
    )
    assert cfg.phase_calibration_file.exists()
    assert cfg.rx_lo_sources_by_channel == (
        "internal",
        "companion",
        "reimport",
        "reimport",
    )


def test_product_shell_entrypoints_are_parseable() -> None:
    subprocess.run(
        ["bash", "-n", "run_realtime.sh", "setup.sh", "run_tests.sh"],
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



def test_jammer_detection_has_dedicated_log_file() -> None:
    assert LOGGER_DEFS["doa"][1] == "doa.log"
    assert LOGGER_DEFS["jammer"][1] == "jammer_detection.log"
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
    unknown_log = tmp_path / "uhd_console.log"
    unknown_log.write_text("old uhd log", encoding="utf-8")
    nested_log = tmp_path / "gnss-sdr" / "runtime" / "console.log"
    nested_log.parent.mkdir(parents=True)
    nested_log.write_text("old console log", encoding="utf-8")
    output_file = tmp_path / "gnss-sdr" / "runtime" / "outputs" / "pvt-output.txt"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("keep output", encoding="utf-8")

    reset_session_logs(tmp_path, loggers)

    assert (tmp_path / "gnss_sdr.log").read_text(encoding="utf-8") == ""
    assert unknown_log.read_text(encoding="utf-8") == ""
    assert nested_log.read_text(encoding="utf-8") == ""
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
    cfg = _runtime_config()

    assert cfg.sample_rate == 4_000_000.0
    assert cfg.center_freq_hz == 1_575_420_000.0
    assert cfg.algorithm_mode == "lcmv"
    assert cfg.auto_rate_backoff is False
    assert cfg.stop_on_overflow is True
    assert cfg.rx_clipping_component_threshold == 0.98
    assert cfg.rx_clipping_fraction_threshold == 0.001
    assert cfg.ui_update_interval_s == 0.3333333333333333
    assert cfg.dsp_update_interval_s == 0.3333333333333333
    assert cfg.prn_chart_update_interval_s == 0.3333333333333333
    assert cfg.skyplot_update_interval_s == 0.3333333333333333
    assert cfg.process_every_n_chunks == 24
    assert cfg.samples_per_chunk == 32768
    assert cfg.gnss_feed_queue_maxsize == 512
    assert cfg.usrp_addr.startswith("addr=192.168.40.2")
    assert "recv_frame_size=8000" in cfg.usrp_addr
    assert "send_frame_size=8000" in cfg.usrp_addr
    assert cfg.recv_frame_size == 8000
    assert cfg.send_frame_size == 8000
    assert "recv_buff_size=50000000" in cfg.usrp_addr
    assert "num_recv_frames=4096" in cfg.usrp_addr
    assert cfg.gnss_sdr_echo_stdout is False
    assert cfg.gnss_1c_channel_count == 15
    assert cfg.gnss_1b_channel_count == 15
    assert cfg.gnss_channels_in_acquisition == 30
    assert cfg.phase_calibration_file is not None
    assert cfg.phase_calibration_file.is_absolute()
    assert cfg.phase_correction_vector is not None
