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
    def _rendered_nmea_tty_path(self) -> str:
        if not bool(self._cfg.gnss_pvt_nmea_tty_enable):
            return "/dev/null"
        tty_path = getattr(self, "_nmea_tty_path", None)
        if tty_path in {None, "", "/dev/null"}:
            raise RuntimeError(
                "GNSS-SDR NMEA PTY is enabled but was not prepared before config render"
            )
        return str(tty_path)

    def _render_config(self) -> str:
        template_path = self._cfg.gnss_sdr_config_template.expanduser().resolve()
        template = template_path.read_text(encoding="utf-8")
        channels_1c_count = self._shared_u1_phase_source_count()
        channels_in_acquisition = int(self._cfg.gnss_channels_in_acquisition)
        return template.format(
            acquisition_bit_transition_flag=str(
                bool(self._cfg.gnss_acquisition_bit_transition_flag)
            ).lower(),
            acquisition_coherent_integration_ms=int(
                self._cfg.gnss_acquisition_coherent_integration_ms
            ),
            acquisition_doppler_max_hz=int(self._cfg.gnss_acquisition_doppler_max_hz),
            acquisition_doppler_step_hz=int(self._cfg.gnss_acquisition_doppler_step_hz),
            acquisition_max_dwells=int(self._cfg.gnss_acquisition_max_dwells),
            acquisition_pfa=float(self._cfg.gnss_acquisition_pfa),
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
            monitor_decimation_factor=int(self._cfg.gnss_monitor_decimation_factor),
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
            # Live NMEA is consumed from a PTY. The product never asks
            # GNSS-SDR to create a second NMEA persistence path.
            pvt_nmea_output_file_enable="false",
            pvt_nmea_path="gnss_sdr_pvt.nmea",
            pvt_nmea_rate_ms=int(self._cfg.gnss_pvt_nmea_rate_ms),
            pvt_nmea_tty_devname=self._rendered_nmea_tty_path(),
            pvt_nmea_tty_enable=str(bool(self._cfg.gnss_pvt_nmea_tty_enable)).lower(),
            pvt_positioning_mode=str(self._cfg.gnss_pvt_positioning_mode),
            pvt_elevation_mask_deg=float(self._cfg.gnss_pvt_elevation_mask_deg),
            pvt_log_rtklib_residuals=str(bool(self._cfg.logging_enabled)).lower(),
            sample_type=self._cfg.gnss_sdr_sample_type,
            signal_source_config=self._render_signal_source_config(),
            signal_source_dump_path="./outputs/signal_source/signal_source.dat",
            signal_conditioner_config=self._render_signal_conditioner_config(),
            telemetry_dump_prefix="./outputs/telemetry/telemetry_decoder_1C.dat",
            tracking_1c_dll_bw_hz=float(self._cfg.gnss_tracking_1c_dll_bw_hz),
            tracking_1c_dll_bw_narrow_hz=float(
                self._cfg.gnss_tracking_1c_dll_bw_narrow_hz
            ),
            tracking_1c_dll_filter_order=int(
                self._cfg.gnss_tracking_1c_dll_filter_order
            ),
            tracking_1c_early_late_space_chips=float(
                self._cfg.gnss_tracking_1c_early_late_space_chips
            ),
            tracking_1c_early_late_space_narrow_chips=float(
                self._cfg.gnss_tracking_1c_early_late_space_narrow_chips
            ),
            tracking_1c_extend_correlation_symbols=int(
                self._cfg.gnss_tracking_1c_extend_correlation_symbols
            ),
            tracking_1c_enable_fll_pull_in=str(
                bool(self._cfg.gnss_tracking_1c_enable_fll_pull_in)
            ).lower(),
            tracking_1c_enable_fll_steady_state=str(
                bool(self._cfg.gnss_tracking_1c_enable_fll_steady_state)
            ).lower(),
            tracking_1c_fll_bw_hz=float(self._cfg.gnss_tracking_1c_fll_bw_hz),
            tracking_1c_pull_in_time_s=int(self._cfg.gnss_tracking_1c_pull_in_time_s),
            tracking_1c_bit_synchronization_time_limit_s=int(
                self._cfg.gnss_tracking_1c_bit_synchronization_time_limit_s
            ),
            tracking_1c_pll_bw_hz=float(self._cfg.gnss_tracking_1c_pll_bw_hz),
            tracking_1c_pll_bw_narrow_hz=float(
                self._cfg.gnss_tracking_1c_pll_bw_narrow_hz
            ),
            tracking_1c_pll_filter_order=int(
                self._cfg.gnss_tracking_1c_pll_filter_order
            ),
            tracking_output_prefix="./outputs/tracking/tracking_ch_",
            tracking_monitor_client_addresses=str(
                self._cfg.gnss_tracking_monitor_client_addresses
            ),
            tracking_monitor_decimation_factor=int(
                self._cfg.gnss_tracking_monitor_decimation_factor
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

        source_count = self._shared_u1_phase_source_count()
        channels_1c = source_count
        channels_in_acquisition = int(self._cfg.gnss_channels_in_acquisition)
        summary = (
            "GNSS-SDR rendered load: "
            f"channels_1c={channels_1c} "
            f"total_channels={channels_1c} "
            f"channels_in_acquisition={channels_in_acquisition} "
            f"rf_sources={source_count} "
            "shared_u1_phase_compensation=True "
            "source_mapping=dynamic_channel_to_prn "
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
        rows: list[str] = []
        for idx in range(self._shared_u1_phase_source_count()):
            rows.append(f"Channel{idx}.signal=1C")
            rows.append(f"Channel{idx}.RF_channel_ID={idx}")
        return "\n".join(rows)

    def _render_signal_source_config(self) -> str:
        source_count = self._shared_u1_phase_source_count()
        rows = [
            f"GNSS-SDR.num_sources={source_count}",
            # The producer writes identical sample counts to every FIFO.  Keep
            # the GNSS-SDR receiver clock on conditioner zero, as in its normal
            # multi-source flowgraph.  Making the sample-counter decimator
            # consume all sources couples all ten scheduler branches: one
            # temporarily unscheduled channel then blocks the clock, every FIFO
            # reader, and finally the upstream raw queue.
            "GNSS-SDR.synchronize_signal_sources=false",
        ]
        for idx, path in enumerate(self._fifo_paths):
            role = f"SignalSource{idx}"
            source_name = f"channel_{idx:02d}"
            rows.extend(
                [
                    f"{role}.implementation=Fifo_Signal_Source",
                    f"{role}.filename={path}",
                    f"{role}.sample_type={self._cfg.gnss_sdr_sample_type}",
                    f"{role}.dump=false",
                    f"{role}.dump_filename=./outputs/signal_source/signal_source_{source_name}.dat",
                ]
            )
        return "\n".join(rows)

    def _render_signal_conditioner_config(self) -> str:
        sample_rate_hz = float(self._cfg.sample_rate)
        sample_rate_sps = int(round(sample_rate_hz))
        # Validate that the configured rate can represent the physical GPS L1
        # pass/stop edges before asking GNU Radio to derive low-pass taps.
        self._validate_input_filter_configuration()
        count = self._shared_u1_phase_source_count()
        rows: list[str] = []
        for idx in range(count):
            # Every shared-phase source is a distinct synchronized GNU Radio
            # branch, whether its PRN is pinned or acquired dynamically.
            suffix = str(idx)
            output_suffix = f"_channel_{idx:02d}"
            conditioner = f"SignalConditioner{suffix}"
            adapter = f"DataTypeAdapter{suffix}"
            input_filter = f"InputFilter{suffix}"
            resampler = f"Resampler{suffix}"
            rows.extend(
                [
                    f"{conditioner}.implementation=Signal_Conditioner",
                    f"{adapter}.implementation=Pass_Through",
                    f"{adapter}.item_type=gr_complex",
                    f"{input_filter}.implementation=Freq_Xlating_Fir_Filter",
                    f"{input_filter}.input_item_type=gr_complex",
                    f"{input_filter}.output_item_type=gr_complex",
                    f"{input_filter}.taps_item_type=float",
                    f"{input_filter}.filter_type=lowpass",
                    f"{input_filter}.bw={GNSS_INPUT_FILTER_CUTOFF_HZ:.0f}",
                    f"{input_filter}.tw={GNSS_INPUT_FILTER_TRANSITION_WIDTH_HZ:.0f}",
                    f"{input_filter}.sampling_frequency={sample_rate_sps}",
                    f"{input_filter}.IF=0",
                    f"{input_filter}.decimation_factor=1",
                    f"{input_filter}.dump=false",
                    f"{input_filter}.dump_filename=./outputs/signal_conditioner/input_filter{output_suffix}.dat",
                    f"{resampler}.implementation=Pass_Through",
                    f"{resampler}.item_type=gr_complex",
                    f"{resampler}.sample_freq_in={sample_rate_sps}",
                    f"{resampler}.sample_freq_out={sample_rate_sps}",
                    f"{resampler}.dump=false",
                    f"{resampler}.dump_filename=./outputs/signal_conditioner/resampler{output_suffix}.dat",
                ]
            )
        return "\n".join(rows)

    def _shared_u1_phase_source_count(self) -> int:
        return int(self._cfg.gnss_1c_channel_count)

    def _active_signal_ids(self) -> tuple[str, ...]:
        signals: list[str] = []
        if int(self._cfg.gnss_1c_channel_count) > 0:
            signals.append("1C")
        if not signals:
            raise ValueError("GNSS-SDR requires at least one enabled signal")
        return tuple(signals)

    @property
    def input_filter_bandwidth_hz(self) -> float:
        self._validate_input_filter_configuration()
        return GPS_L1_CA_PROCESSING_BANDWIDTH_HZ

    def _validate_input_filter_configuration(self) -> None:
        self._active_signal_ids()
        sample_rate_hz = float(self._cfg.sample_rate)
        center_freq_hz = float(self._cfg.center_freq_hz)
        if abs(center_freq_hz - GPS_L1_CA_FREQ_HZ) > 1.0:
            raise ValueError(
                f"Configured center_freq_hz {center_freq_hz:.0f} Hz must equal the "
                f"GPS L1 C/A carrier {GPS_L1_CA_FREQ_HZ:.0f} Hz because the rendered "
                "GNSS-SDR conditioner uses zero IF"
            )
        if sample_rate_hz <= GNSS_INPUT_FILTER_STOPBAND_HZ:
            raise ValueError(
                f"Configured sample_rate {sample_rate_hz:.0f} Hz cannot place the "
                f"GPS L1 input-filter stopband at {GNSS_INPUT_FILTER_STOPBAND_HZ:.0f} Hz "
                "below Nyquist; increase sample_rate"
            )
