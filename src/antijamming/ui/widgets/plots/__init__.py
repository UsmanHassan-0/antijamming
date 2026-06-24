"""Reusable realtime plot builders."""

from .algorithm import (
    DOA_Y_RANGE,
    LCMV_Y_RANGE,
    PRN_Y_RANGE,
    PlotCurveSpec,
    add_plot_legend,
    build_azimuth_response_plot,
    build_doa_plot,
    build_lcmv_plot,
    build_plot_widget,
    style_legend_samples,
    style_plot,
)

__all__ = [
    "DOA_Y_RANGE",
    "LCMV_Y_RANGE",
    "PRN_Y_RANGE",
    "PlotCurveSpec",
    "add_plot_legend",
    "build_azimuth_response_plot",
    "build_doa_plot",
    "build_lcmv_plot",
    "build_plot_widget",
    "style_legend_samples",
    "style_plot",
]
