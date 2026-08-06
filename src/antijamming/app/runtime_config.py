"""Shared product configuration construction for GUI and headless launchers."""

from __future__ import annotations

from pathlib import Path

from antijamming.config import REPO_ROOT, StreamConfig, default_stream_config
from antijamming.dsp.phase import load_calibration_correction_selection
from antijamming.radio.usrp import usrp_arg_int, with_usrp_frame_sizes

GRAPH_UI_INTERVAL_S = 0.1
REMOTE_SAFE_GNSS_WIDGET_INTERVAL_S = 0.1
REMOTE_SAFE_SKYPLOT_INTERVAL_S = 0.1


def build_runtime_config() -> StreamConfig:
    """Build the fixed realtime product configuration without touching hardware."""

    cfg = default_stream_config()
    cfg.usrp_addr = with_usrp_frame_sizes(
        cfg.usrp_addr,
        recv_frame_size=int(cfg.recv_frame_size),
        send_frame_size=int(cfg.send_frame_size),
        recv_buff_size=int(cfg.recv_buff_size),
        num_recv_frames=int(cfg.num_recv_frames),
    )
    cfg.recv_frame_size = usrp_arg_int(
        cfg.usrp_addr,
        "recv_frame_size",
        int(cfg.recv_frame_size),
    )
    cfg.send_frame_size = usrp_arg_int(
        cfg.usrp_addr,
        "send_frame_size",
        int(cfg.send_frame_size),
    )
    cfg.process_every_n_chunks = max(1, int(cfg.process_every_n_chunks))
    cfg.ui_update_interval_s = max(
        GRAPH_UI_INTERVAL_S,
        float(cfg.ui_update_interval_s),
    )
    cfg.dsp_update_interval_s = max(0.02, float(cfg.dsp_update_interval_s))
    cfg.prn_chart_update_interval_s = max(
        REMOTE_SAFE_GNSS_WIDGET_INTERVAL_S,
        float(cfg.prn_chart_update_interval_s),
    )
    cfg.skyplot_update_interval_s = max(
        REMOTE_SAFE_SKYPLOT_INTERVAL_S,
        float(cfg.skyplot_update_interval_s),
    )
    cfg.ui_points = max(32, int(cfg.ui_points))
    cfg.startup_grace_s = max(0.0, float(cfg.startup_grace_s))
    cfg.min_sample_rate = max(1e5, float(cfg.min_sample_rate))
    cfg.max_overflow_streak = max(1, int(cfg.max_overflow_streak))
    cfg.max_total_overflow = max(1, int(cfg.max_total_overflow))

    if cfg.phase_calibration_file is not None:
        calibration_file = Path(cfg.phase_calibration_file).expanduser()
        if not calibration_file.is_absolute():
            calibration_file = (REPO_ROOT / calibration_file).resolve()
        cfg.phase_calibration_file = calibration_file
        selection = load_calibration_correction_selection(
            calibration_file,
            mode=str(cfg.calibration_correction_mode),
            expected_channel_count=len(cfg.channels),
        )
        cfg.phase_correction_vector = tuple(complex(v) for v in selection.vector)
        cfg.calibration_correction_mode = selection.configured_mode
        cfg.calibration_correction_metadata = selection.metadata(
            expected_channel_count=len(cfg.channels)
        )

    return cfg


__all__ = ["build_runtime_config"]
