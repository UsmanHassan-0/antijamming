"""Reusable pyqtgraph builders for the realtime monitoring UI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QSizePolicy
import pyqtgraph as pg

from antijamming.ui.theme import (
    BG_SUBTLE,
    DOA_COLOR,
    FONT_POINT_SIZE_PLOT,
    FONT_POINT_SIZE_PLOT_LABEL,
    FG_MUTED,
    FG_TEXT,
    LCMV_COLOR,
    WARNING,
)
from antijamming.ui.specs import (
    PLOT_AXIS_BOTTOM_HEIGHT,
    PLOT_AXIS_LEFT_WIDTH,
    PLOT_AXIS_TICK_TEXT_OFFSET,
    PLOT_GRID_ALPHA_PERCENT,
    PLOT_ITEM_MARGINS,
    PLOT_LEGEND_OFFSET,
    PLOT_LEGEND_SAMPLE_WIDTH,
)


# =============================================================================
# Plot Ranges
# =============================================================================

# Axis ranges are centralized so plots that show the same physical quantity stay
# visually comparable across tabs.
DOA_Y_RANGE = (0.0, 1.02)
# The LCMV curve is a normalized theoretical array response, not measured
# jammer suppression. Keep the display floor modest so solver/null-floor values
# near -120 dB are not mistaken for real RF/IQ suppression.
LCMV_Y_RANGE = (-60.0, 5.0)
PRN_Y_RANGE = (0.0, 55.0)


# =============================================================================
# Plot Curve Models
# =============================================================================

# PlotCurveSpec keeps curve naming and pen choices near the builders rather than
# spread across MainWindow update code.

@dataclass(frozen=True, slots=True)
class PlotCurveSpec:
    """Curve styling descriptor used by reusable plot builders."""

    name: str
    color: str
    width: float = 1.8
    style: Qt.PenStyle = Qt.PenStyle.SolidLine


class _LegendItemProxy:
    # pyqtgraph legend samples are created internally. The proxy lets us adjust
    # sample pen width while preserving the item API the legend expects.
    def __init__(self, item: object, pen_width: float) -> None:
        self._item = item
        opts = dict(getattr(item, "opts", {}))
        if "pen" in opts:
            opts["pen"] = pg.mkPen(opts["pen"], width=pen_width)
        self.opts = opts

    def isVisible(self) -> bool:
        return bool(self._item.isVisible())

    def setVisible(self, visible: bool) -> None:
        self._item.setVisible(visible)


# =============================================================================
# Shared Plot Styling
# =============================================================================

# Plot widgets are intentionally non-interactive monitoring surfaces. Disabling
# mouse and focus behavior avoids accidental pan/zoom changes during live runs.

def style_plot(plot: pg.PlotWidget) -> None:
    """Apply the shared non-interactive plot appearance."""
    plot.setBackground(BG_SUBTLE)
    plot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    plot.setMenuEnabled(False)
    plot.setMouseEnabled(x=False, y=False)
    plot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    plot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    plot.hideButtons()
    plot.setClipToView(True)
    plot.setDownsampling(auto=True, mode="peak")
    item = plot.getPlotItem()
    item.disableAutoRange()
    item.layout.setContentsMargins(*PLOT_ITEM_MARGINS)
    item.getAxis("left").setTextPen(pg.mkPen(FG_TEXT))
    item.getAxis("bottom").setTextPen(pg.mkPen(FG_TEXT))
    item.getAxis("left").setPen(pg.mkPen(FG_MUTED))
    item.getAxis("bottom").setPen(pg.mkPen(FG_MUTED))
    item.getAxis("left").setWidth(PLOT_AXIS_LEFT_WIDTH)
    item.getAxis("bottom").setHeight(PLOT_AXIS_BOTTOM_HEIGHT)
    item.getAxis("left").setStyle(
        tickTextOffset=PLOT_AXIS_TICK_TEXT_OFFSET,
        autoExpandTextSpace=False,
    )
    item.getAxis("bottom").setStyle(
        tickTextOffset=PLOT_AXIS_TICK_TEXT_OFFSET,
        autoExpandTextSpace=False,
    )
    item.getAxis("left").enableAutoSIPrefix(False)
    item.getAxis("bottom").enableAutoSIPrefix(False)
    tick_font = QFont()
    tick_font.setPointSize(FONT_POINT_SIZE_PLOT)
    label_font = QFont()
    label_font.setPointSize(FONT_POINT_SIZE_PLOT_LABEL)
    label_font.setBold(True)
    for axis_name in ("left", "bottom"):
        axis = item.getAxis(axis_name)
        axis.setTickFont(tick_font)
        axis.label.setFont(label_font)
    # Avoid double borders: the plot is already inside a bordered "plotShell" card.
    item.getViewBox().setBorder(None)


def add_plot_legend(plot: pg.PlotWidget) -> None:
    """Attach a legend with the standard offset and styling."""
    plot.addLegend(
        offset=PLOT_LEGEND_OFFSET,
        brush=pg.mkBrush(255, 255, 255, 235),
        pen=pg.mkPen(FG_TEXT, width=1.1),
        labelTextColor=FG_TEXT,
    )


def style_legend_samples(
    plot: pg.PlotWidget,
    pen_width: float = float(PLOT_LEGEND_SAMPLE_WIDTH),
) -> None:
    """Normalize legend sample widths after pyqtgraph creates them."""
    legend = plot.getPlotItem().legend
    if legend is None:
        return
    for sample, _label in legend.items:
        item = getattr(sample, "item", None)
        if item is None:
            continue
        sample.item = _LegendItemProxy(item, pen_width)


def build_plot_widget(
    *,
    bottom_label: str,
    left_label: str,
    y_range: tuple[float, float] | None = None,
    x_range: tuple[float, float] | None = None,
    x_limits: tuple[float, float] | None = None,
    y_limits: tuple[float, float] | None = None,
    curves: Sequence[PlotCurveSpec] = (),
    legend: bool = True,
    grid_x: bool = True,
    grid_y: bool = True,
) -> tuple[pg.PlotWidget, list[pg.PlotDataItem]]:
    """Build a styled plot with the requested curves and axis ranges."""
    plot = pg.PlotWidget()
    style_plot(plot)
    if legend:
        add_plot_legend(plot)
    plot.showGrid(x=grid_x, y=grid_y, alpha=PLOT_GRID_ALPHA_PERCENT / 100.0)
    plot.setLabel("bottom", bottom_label)
    plot.setLabel("left", left_label)
    if x_range is not None:
        plot.setXRange(*x_range, padding=0.0)
    if y_range is not None:
        plot.setYRange(*y_range, padding=0.0)
    limits: dict[str, float] = {}
    if x_limits is not None:
        # Infinite limits mean "leave this side unconstrained" for scrolling
        # time-series plots.
        x_min, x_max = x_limits
        if x_min != float("-inf"):
            limits["xMin"] = x_min
        if x_max != float("inf"):
            limits["xMax"] = x_max
    elif x_range is not None:
        limits["xMin"], limits["xMax"] = x_range
    if y_limits is not None:
        limits["yMin"], limits["yMax"] = y_limits
    elif y_range is not None:
        limits["yMin"], limits["yMax"] = y_range
    if limits:
        plot.setLimits(**limits)
    plot_curves = [
        plot.plot(
            pen=pg.mkPen(color=curve.color, width=curve.width, style=curve.style),
            name=curve.name,
        )
        for curve in curves
    ]
    if legend:
        style_legend_samples(plot)
    return plot, plot_curves


# =============================================================================
# Algorithm Plot Builders
# =============================================================================

# Algorithm plots all share azimuth conventions and marker behavior. Specific
# builders below only choose labels, response color, and y-axis range.

def build_azimuth_response_plot(
    *,
    bottom_label: str,
    left_label: str,
    response_color: str,
    y_range: tuple[float, float],
    doa_min_deg: float,
    doa_max_deg: float,
    marker_color: str = WARNING,
) -> tuple[pg.PlotWidget, pg.PlotDataItem, pg.InfiniteLine]:
    """Build an azimuth response plot with a vertical DoA/null marker."""
    bearing_x_max = 360.0 if doa_min_deg == 0.0 and doa_max_deg >= 359.0 else doa_max_deg
    plot, curves = build_plot_widget(
        bottom_label=bottom_label,
        left_label=left_label,
        x_range=(doa_min_deg, bearing_x_max),
        y_range=y_range,
        curves=(PlotCurveSpec(name="", color=response_color, width=2.0),),
        legend=False,
    )
    curve = curves[0]
    marker = pg.InfiniteLine(pos=0.0, angle=90, pen=pg.mkPen(marker_color, width=2))
    plot.addItem(marker)
    return plot, curve, marker


def build_doa_plot(doa_min_deg: float, doa_max_deg: float) -> tuple[pg.PlotWidget, pg.PlotDataItem, pg.InfiniteLine]:
    """Build the MUSIC DoA spectrum plot."""
    return build_azimuth_response_plot(
        bottom_label="Bearing (deg)",
        left_label="Normalized spatial spectrum",
        response_color=DOA_COLOR,
        y_range=DOA_Y_RANGE,
        doa_min_deg=doa_min_deg,
        doa_max_deg=doa_max_deg,
    )


def build_lcmv_plot(doa_min_deg: float, doa_max_deg: float) -> tuple[pg.PlotWidget, pg.PlotDataItem, pg.InfiniteLine]:
    """Build the LCMV beamforming response plot."""
    return build_azimuth_response_plot(
        bottom_label="Bearing (deg)",
        left_label="Array response (dB, normalized)",
        response_color=LCMV_COLOR,
        y_range=LCMV_Y_RANGE,
        doa_min_deg=doa_min_deg,
        doa_max_deg=doa_max_deg,
    )
