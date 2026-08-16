"""Rendered GNSS-SDR FIFO configuration helpers."""

from __future__ import annotations

import re

from .constants import (
    GNSS_INPUT_FILTER_CUTOFF_HZ,
    GNSS_INPUT_FILTER_STOPBAND_HZ,
    GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ,
    GPS_L1_CA_FREQ_HZ,
    GPS_L1_CA_PROCESSING_BANDWIDTH_HZ,
)


class ConfigRendererMixin:
    def _render_config(self) -> str:
        template_path = self._cfg.gnss_sdr_config_template.expanduser().resolve()
        template = template_path.read_text(encoding="utf-8")
        channels_1c_count = max(1, int(self._cfg.gnss_1c_channel_count))
        channels_in_acquisition = min(
            channels_1c_count,
            max(1, int(self._cfg.gnss_channels_in_acquisition)),
        )
        return template.format(
            acquisition_bit_transition_flag=str(
                bool(self._cfg.gnss_acquisition_bit_transition_flag)
            ).lower(),
            acquisition_coherent_integration_ms=max(
                1, int(self._cfg.gnss_acquisition_coherent_integration_ms)
            ),
            acquisition_doppler_max_hz=max(
                500, int(self._cfg.gnss_acquisition_doppler_max_hz)
            ),
            acquisition_doppler_step_hz=max(
                1, int(self._cfg.gnss_acquisition_doppler_step_hz)
            ),
            acquisition_max_dwells=max(1, int(self._cfg.gnss_acquisition_max_dwells)),
            acquisition_pfa=max(1e-12, float(self._cfg.gnss_acquisition_pfa)),
            channels_1c_count=channels_1c_count,
            channels_in_acquisition=channels_in_acquisition,
            channel_signal_config=self._render_channel_signal_config(),
            fifo_path=self._fifo_path,
            agnss_xml_enable=str(bool(self._cfg.gnss_agnss_xml_enable)).lower(),
            agnss_gps_ephemeris_xml=str(self._cfg.gnss_agnss_gps_ephemeris_xml),
            agnss_ref_location=str(self._cfg.gnss_agnss_ref_location),
            agnss_ref_utc_time=str(self._cfg.gnss_agnss_ref_utc_time),
            tow_to_trk=str(bool(self._cfg.gnss_tow_to_trk)).lower(),
            internal_fs_sps=int(self._cfg.sample_rate),
            output_dir="outputs",
            acquisition_dump_path="./outputs/acquisition/acq_dump.dat",
            monitor_client_addresses=str(self._cfg.gnss_monitor_client_addresses),
            monitor_decimation_factor=max(
                1, int(self._cfg.gnss_monitor_decimation_factor)
            ),
            monitor_enable=str(bool(self._cfg.gnss_monitor_enable)).lower(),
            monitor_enable_protobuf=str(
                bool(self._cfg.gnss_monitor_enable_protobuf)
            ).lower(),
            monitor_udp_port=str(self._cfg.gnss_monitor_udp_port),
            observables_dump_path="./outputs/observables/observables.dat",
            pvt_dump_prefix="pvt",
            pvt_monitor_client_addresses=str(
                self._cfg.gnss_pvt_monitor_client_addresses
            ),
            pvt_monitor_enable=str(bool(self._cfg.gnss_pvt_monitor_enable)).lower(),
            pvt_monitor_enable_protobuf=str(
                bool(self._cfg.gnss_pvt_monitor_enable_protobuf)
            ).lower(),
            pvt_monitor_udp_port=str(self._cfg.gnss_pvt_monitor_udp_port),
            pvt_nmea_output_file_enable=str(
                bool(self._cfg.gnss_pvt_nmea_output_file_enable)
            ).lower(),
            pvt_nmea_path="gnss_sdr_pvt.nmea",
            pvt_nmea_rate_ms=max(100, int(self._cfg.gnss_pvt_nmea_rate_ms)),
            pvt_nmea_tty_devname=str(
                getattr(self, "_nmea_tty_path", None) or "/dev/null"
            ),
            pvt_nmea_tty_enable=str(bool(self._cfg.gnss_pvt_nmea_tty_enable)).lower(),
            pvt_positioning_mode=str(self._cfg.gnss_pvt_positioning_mode),
            pvt_elevation_mask_deg=max(
                -90.0, min(90.0, float(self._cfg.gnss_pvt_elevation_mask_deg))
            ),
            sample_type=self._cfg.gnss_sdr_sample_type,
            signal_source_dump_path="./outputs/signal_source/signal_source.dat",
            signal_conditioner_config=self._render_signal_conditioner_config(),
            telemetry_dump_prefix="./outputs/telemetry/telemetry_decoder_1C.dat",
            tracking_1c_dll_bw_hz=max(
                0.1, float(self._cfg.gnss_tracking_1c_dll_bw_hz)
            ),
            tracking_1c_dll_bw_narrow_hz=max(
                0.1, float(self._cfg.gnss_tracking_1c_dll_bw_narrow_hz)
            ),
            tracking_1c_dll_filter_order=max(
                1, int(self._cfg.gnss_tracking_1c_dll_filter_order)
            ),
            tracking_1c_early_late_space_chips=max(
                0.01, float(self._cfg.gnss_tracking_1c_early_late_space_chips)
            ),
            tracking_1c_early_late_space_narrow_chips=max(
                0.01, float(self._cfg.gnss_tracking_1c_early_late_space_narrow_chips)
            ),
            tracking_1c_extend_correlation_symbols=max(
                1, int(self._cfg.gnss_tracking_1c_extend_correlation_symbols)
            ),
            tracking_1c_enable_fll_pull_in=str(
                bool(self._cfg.gnss_tracking_1c_enable_fll_pull_in)
            ).lower(),
            tracking_1c_enable_fll_steady_state=str(
                bool(self._cfg.gnss_tracking_1c_enable_fll_steady_state)
            ).lower(),
            tracking_1c_fll_bw_hz=max(
                0.1, float(self._cfg.gnss_tracking_1c_fll_bw_hz)
            ),
            tracking_1c_pull_in_time_s=max(
                0, int(self._cfg.gnss_tracking_1c_pull_in_time_s)
            ),
            tracking_1c_bit_synchronization_time_limit_s=max(
                1, int(self._cfg.gnss_tracking_1c_bit_synchronization_time_limit_s)
            ),
            tracking_1c_pll_bw_hz=max(
                0.1, float(self._cfg.gnss_tracking_1c_pll_bw_hz)
            ),
            tracking_1c_pll_bw_narrow_hz=max(
                0.1, float(self._cfg.gnss_tracking_1c_pll_bw_narrow_hz)
            ),
            tracking_1c_pll_filter_order=max(
                2, int(self._cfg.gnss_tracking_1c_pll_filter_order)
            ),
            tracking_output_prefix="./outputs/tracking/tracking_ch_",
            tracking_monitor_client_addresses=str(
                self._cfg.gnss_tracking_monitor_client_addresses
            ),
            tracking_monitor_decimation_factor=max(
                1, int(self._cfg.gnss_tracking_monitor_decimation_factor)
            ),
            tracking_monitor_enable=str(
                bool(self._cfg.gnss_tracking_monitor_enable)
            ).lower(),
            tracking_monitor_enable_protobuf=str(
                bool(self._cfg.gnss_tracking_monitor_enable_protobuf)
            ).lower(),
            tracking_monitor_udp_port=str(self._cfg.gnss_tracking_monitor_udp_port),
        )

    def _log_rendered_config_summary(self, rendered_config: str) -> None:
        def value_for(key: str) -> str:
            match = re.search(rf"^{re.escape(key)}=(.+)$", rendered_config, re.MULTILINE)
            return match.group(1).strip() if match else "--"

        channels_1c = max(1, int(self._cfg.gnss_1c_channel_count))
        channels_in_acquisition = min(
            channels_1c,
            max(1, int(self._cfg.gnss_channels_in_acquisition)),
        )
        summary = (
            "GNSS-SDR rendered load: "
            f"channels_1c={channels_1c} "
            f"total_channels={channels_1c} "
            f"channels_in_acquisition={channels_in_acquisition} "
            f"tracking_1c_dump={value_for('Tracking_1C.dump')} "
            f"pvt_dump={value_for('PVT.dump')} "
            f"observables_dump={value_for('Observables.dump')} "
            f"acq_1c_dump={value_for('Acquisition_1C.dump')} "
            f"agnss_xml={value_for('GNSS-SDR.AGNSS_XML_enabled')} "
            f"tow_to_trk={value_for('GNSS-SDR.tow_to_trk')} "
            f"agnss_ref_location={value_for('GNSS-SDR.AGNSS_ref_location')} "
            f"agnss_ref_utc_time={value_for('GNSS-SDR.AGNSS_ref_utc_time')} "
            f"agnss_gps_eph={value_for('GNSS-SDR.AGNSS_gps_ephemeris_xml')} "
            f"pvt_monitor={value_for('PVT.enable_monitor')}:{value_for('PVT.monitor_udp_port')} "
            f"pvt_nmea_tty={value_for('PVT.flag_nmea_tty_port')}:{value_for('PVT.nmea_dump_devname')} "
            f"pvt_nmea_file={value_for('PVT.nmea_output_file_enabled')} "
            f"pvt_nmea_rate_ms={value_for('PVT.nmea_rate_ms')} "
            f"pvt_elevation_mask_deg={value_for('PVT.elevation_mask')} "
            f"pvt_rtklib_residuals={value_for('PVT.log_rtklib_residuals')}:"
            f"{value_for('PVT.rtklib_residual_log_period_ms')} "
            f"monitor={value_for('Monitor.enable_monitor')}:{value_for('Monitor.udp_port')} "
            f"tracking_monitor={value_for('TrackingMonitor.enable_monitor')}:"
            f"{value_for('TrackingMonitor.udp_port')} "
            f"sample_rate_sps={int(self._cfg.sample_rate)} "
            f"active_signals={','.join(self._active_signal_ids())} "
            f"input_filter_bw_hz={self.input_filter_bandwidth_hz:.0f}"
        )
        self._log.info("%s", summary)
        self._handoff_log.info("%s", summary)

    def _render_channel_signal_config(self) -> str:
        gps_count = max(1, int(self._cfg.gnss_1c_channel_count))
        return "\n".join(f"Channel{idx}.signal=1C" for idx in range(gps_count))

    def _render_signal_conditioner_config(self) -> str:
        sample_rate_hz = max(1.0, float(self._cfg.sample_rate))
        sample_rate_sps = int(round(sample_rate_hz))
        # Validate that the configured rate can represent the physical GPS L1
        # pass/stop edges before asking GNU Radio to derive low-pass taps.
        self.input_filter_bandwidth_hz
        return "\n".join(
            [
                "SignalConditioner.implementation=Signal_Conditioner",
                "DataTypeAdapter.implementation=Pass_Through",
                "DataTypeAdapter.item_type=gr_complex",
                "InputFilter.implementation=Freq_Xlating_Fir_Filter",
                "InputFilter.input_item_type=gr_complex",
                "InputFilter.output_item_type=gr_complex",
                "InputFilter.taps_item_type=float",
                "InputFilter.filter_type=lowpass",
                f"InputFilter.bw={GNSS_INPUT_FILTER_CUTOFF_HZ:.0f}",
                f"InputFilter.tw={GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ:.0f}",
                f"InputFilter.sampling_frequency={sample_rate_sps}",
                "InputFilter.IF=0",
                "InputFilter.decimation_factor=1",
                "InputFilter.dump=false",
                "InputFilter.dump_filename=./outputs/signal_conditioner/input_filter.dat",
                "Resampler.implementation=Pass_Through",
                "Resampler.item_type=gr_complex",
                f"Resampler.sample_freq_in={sample_rate_sps}",
                f"Resampler.sample_freq_out={sample_rate_sps}",
                "Resampler.dump=false",
                "Resampler.dump_filename=./outputs/signal_conditioner/resampler.dat",
            ]
        )

    def _active_signal_ids(self) -> tuple[str, ...]:
        signals: list[str] = []
        if int(self._cfg.gnss_1c_channel_count) > 0:
            signals.append("1C")
        if not signals:
            raise ValueError("GNSS-SDR requires at least one enabled signal")
        return tuple(signals)

    @property
    def input_filter_bandwidth_hz(self) -> float:
        self._active_signal_ids()
        sample_rate_hz = max(1.0, float(self._cfg.sample_rate))
        if sample_rate_hz <= GNSS_INPUT_FILTER_STOPBAND_HZ:
            raise ValueError(
                f"Configured sample_rate {sample_rate_hz:.0f} Hz cannot place the "
                f"GPS L1 input-filter stopband at {GNSS_INPUT_FILTER_STOPBAND_HZ:.0f} Hz "
                "below Nyquist; increase sample_rate"
            )
        return GPS_L1_CA_PROCESSING_BANDWIDTH_HZ

    def _warn_if_gps_l1_is_outside_capture_band(self) -> None:
        half_span_hz = 0.5 * float(self._cfg.sample_rate)
        offset_hz = GPS_L1_CA_FREQ_HZ - float(self._cfg.center_freq_hz)
        if abs(offset_hz) > half_span_hz:
            self._log.warning(
                "GPS L1 (%0.3f MHz) is outside the current capture band centered at %0.3f MHz "
                "with %0.3f Msps complex sampling. GNSS-SDR may not lock until the USRP tune "
                "frequency is moved closer to the GNSS band.",
                GPS_L1_CA_FREQ_HZ / 1e6,
                self._cfg.center_freq_hz / 1e6,
                self._cfg.sample_rate / 1e6,
            )
