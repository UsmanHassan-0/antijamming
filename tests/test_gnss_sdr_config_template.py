from __future__ import annotations

from pathlib import Path


def test_fifo_gnss_sdr_template_uses_ppp_static_pvt() -> None:
    template = Path("configs/gnss-sdr/fifo_gps_l1.conf.template").read_text()

    assert "PVT.implementation=RTKLIB_PVT" in template
    assert "PVT.positioning_mode={pvt_positioning_mode}" in template
    assert "PVT.log_rtklib_residuals=true" in template
    assert "PVT.rtklib_residual_log_period_ms=1000" in template
    assert "PVT.dump=false" in template
    assert "PVT.dump_mat=false" in template
    assert "PVT.dump_filename={pvt_dump_prefix}" in template
    assert "PVT.enable_monitor={pvt_monitor_enable}" in template
    assert "PVT.monitor_client_addresses={pvt_monitor_client_addresses}" in template
    assert "PVT.monitor_udp_port={pvt_monitor_udp_port}" in template
    assert "PVT.enable_protobuf={pvt_monitor_enable_protobuf}" in template
    assert "PVT.nmea_output_file_enabled={pvt_nmea_output_file_enable}" in template
    assert "PVT.nmea_rate_ms={pvt_nmea_rate_ms}" in template
    assert "PVT.flag_nmea_tty_port={pvt_nmea_tty_enable}" in template
    assert "PVT.nmea_dump_devname={pvt_nmea_tty_devname}" in template
    assert "PVT.nmea_dump_devname=/dev/pts/4" not in template
    assert "Monitor.enable_monitor={monitor_enable}" in template
    assert "Monitor.enable_protobuf={monitor_enable_protobuf}" in template
    assert "Monitor.client_addresses={monitor_client_addresses}" in template
    assert "Monitor.udp_port={monitor_udp_port}" in template
    assert "Monitor.decimation_factor={monitor_decimation_factor}" in template
    assert "TrackingMonitor.enable_monitor={tracking_monitor_enable}" in template
    assert (
        "TrackingMonitor.client_addresses={tracking_monitor_client_addresses}"
        in template
    )
    assert "TrackingMonitor.udp_port={tracking_monitor_udp_port}" in template
    assert (
        "TrackingMonitor.decimation_factor={tracking_monitor_decimation_factor}"
        in template
    )
    assert "GNSS-SDR.AGNSS_XML_enabled={agnss_xml_enable}" in template
    assert "GNSS-SDR.AGNSS_gps_ephemeris_xml={agnss_gps_ephemeris_xml}" in template
    assert "GNSS-SDR.AGNSS_gal_ephemeris_xml={agnss_gal_ephemeris_xml}" in template
    assert (
        "GNSS-SDR.AGNSS_gal_utc_model_xml={agnss_gal_utc_model_xml}"
        in template
    )
    assert "GNSS-SDR.AGNSS_gal_almanac_xml={agnss_gal_almanac_xml}" in template
    assert "GNSS-SDR.AGNSS_ref_location={agnss_ref_location}" in template
    assert "GNSS-SDR.AGNSS_ref_utc_time={agnss_ref_utc_time}" in template
    assert "GNSS-SDR.tow_to_trk={tow_to_trk}" in template
    assert "GNSS-SDR.SUPL_read_gps_assistance_xml=false" in template
    assert "Tracking_1C.enable_fll_pull_in={tracking_1c_enable_fll_pull_in}" in template
    assert (
        "Tracking_1C.enable_fll_steady_state={tracking_1c_enable_fll_steady_state}"
        in template
    )
    assert "Tracking_1C.fll_bw_hz={tracking_1c_fll_bw_hz}" in template
    assert "Tracking_1C.pull_in_time_s={tracking_1c_pull_in_time_s}" in template
    assert (
        "Tracking_1C.bit_synchronization_time_limit_s="
        "{tracking_1c_bit_synchronization_time_limit_s}"
        in template
    )
    assert "Tracking_1B.enable_fll_pull_in={tracking_1b_enable_fll_pull_in}" in template
    assert (
        "Tracking_1B.enable_fll_steady_state={tracking_1b_enable_fll_steady_state}"
        in template
    )
    assert "Tracking_1B.fll_bw_hz={tracking_1b_fll_bw_hz}" in template
    assert "Tracking_1B.pull_in_time_s={tracking_1b_pull_in_time_s}" in template
    assert (
        "Tracking_1B.bit_synchronization_time_limit_s="
        "{tracking_1b_bit_synchronization_time_limit_s}"
        in template
    )
    assert "TelemetryDecoder_1B.use_reduced_ced={telemetry_1b_use_reduced_ced}" in template
    assert (
        "TelemetryDecoder_1B.enable_reed_solomon="
        "{telemetry_1b_enable_reed_solomon}"
        in template
    )
    assert "Observables.dump=false" in template
    assert "Observables.dump_filename={observables_dump_path}" in template
    assert "PVT.positioning_mode=Single" not in template


def test_active_gnss_sdr_template_uses_gr_complex_and_pfa() -> None:
    config_paths = [Path("configs/gnss-sdr/fifo_gps_l1.conf.template")]

    for path in config_paths:
        text = path.read_text()
        assert "PVT.positioning_mode={pvt_positioning_mode}" in text, path
        assert "Acquisition_1C.pfa=" in text, path
        assert "Acquisition_1C.threshold=" not in text, path
        assert "Acquisition_1C.item_type=gr_complex" in text, path
        assert "Tracking_1C.item_type=gr_complex" in text, path
        assert "Tracking_1C.cn0_samples=" not in text, path
        assert "Tracking_1C.cn0_min=25" in text, path
        assert "Tracking_1B.cn0_min=25" in text, path
        assert "Tracking_1C.carrier_lock_th=" not in text, path
        assert "Tracking_1C.max_lock_fail=" not in text, path
        assert "Tracking_1C.max_carrier_lock_fail=" not in text, path

        if "SignalSource.item_type=" in text:
            assert "SignalSource.item_type=gr_complex" in text, path
        if "SignalSource.sample_type=" in text:
            assert "SignalSource.sample_type={sample_type}" in text, path
        if "DataTypeAdapter.item_type=" in text:
            assert "DataTypeAdapter.item_type=gr_complex" in text, path
        if "InputFilter.input_item_type=" in text:
            assert "InputFilter.input_item_type=gr_complex" in text, path
        if "InputFilter.output_item_type=" in text:
            assert "InputFilter.output_item_type=gr_complex" in text, path
        if "Resampler.item_type=" in text:
            assert "Resampler.item_type=gr_complex" in text, path
