"""Reusable pyqtgraph builders for the realtime monitoring UI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QGraphicsEllipseItem, QSizePolicy
import numpy as np
import pyqtgraph as pg

from antijamming.ui.theme import (
    BG_SUBTLE,
    DOA_COLOR,
    FONT_POINT_SIZE_PLOT,
    FONT_POINT_SIZE_PLOT_LABEL,
    FG_MUTED,
    FG_TEXT,
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
PRN_Y_RANGE = (0.0, 55.0)
POLAR_RING_RADII = (0.25, 0.5, 0.75, 1.0)
POLAR_MAJOR_SPOKE_DEG = 90
POLAR_SPOKE_STEP_DEG = 30
POLAR_LABEL_RADIUS = 1.16
POLAR_VIEW_EDGE_MARGIN_PX = 8
POLAR_DATA_RADIUS_ATTR = "_antijam_polar_data_radius"
POLAR_RADIAL_LABELS_ATTR = "_antijam_polar_radial_labels"


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


class _PolarPlotWidget(pg.PlotWidget):
    """PlotWidget that keeps polar labels visible as the box aspect changes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._deferred_resize_timer = QTimer(self)
        self._deferred_resize_timer.setSingleShot(True)
        self._deferred_resize_timer.timeout.connect(self._apply_deferred_view_range)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        _apply_polar_view_range(self)
        timer = getattr(self, "_deferred_resize_timer", None)
        if timer is not None:
            timer.start(0)

    def _apply_deferred_view_range(self) -> None:
        _apply_polar_view_range(self)


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


def _polar_xy(radius: float, bearing_deg: float) -> tuple[float, float]:
    bearing_rad = np.deg2rad(float(bearing_deg))
    return float(radius * np.sin(bearing_rad)), float(radius * np.cos(bearing_rad))


def _polar_view_limits_for_plot(plot: pg.PlotWidget) -> tuple[float, float]:
    data_radius = max(0.0, float(getattr(plot, POLAR_DATA_RADIUS_ATTR, 1.0)))
    try:
        plot_item = plot.getPlotItem()
    except RuntimeError:
        return _polar_view_limits_for_box(
            float(plot.width()),
            float(plot.height()),
            data_radius=data_radius,
        )
    if plot_item is None:
        return _polar_view_limits_for_box(
            float(plot.width()),
            float(plot.height()),
            data_radius=data_radius,
        )
    view_box = plot_item.getViewBox()
    try:
        rect = view_box.sceneBoundingRect()
    except RuntimeError:
        return _polar_view_limits_for_box(
            float(plot.width()),
            float(plot.height()),
            data_radius=data_radius,
        )
    width = float(rect.width()) if rect.width() > 0 else float(plot.width())
    height = float(rect.height()) if rect.height() > 0 else float(plot.height())
    return _polar_view_limits_for_box(width, height, data_radius=data_radius)


def _polar_view_limits_for_box(
    width_px: float,
    height_px: float,
    *,
    data_radius: float = 1.0,
) -> tuple[float, float]:
    width = max(float(width_px), 1.0)
    height = max(float(height_px), 1.0)
    required_x = max(max(float(radius) for radius in POLAR_RING_RADII), float(data_radius))
    required_y = required_x
    for bearing_deg in range(0, 360, POLAR_SPOKE_STEP_DEG):
        x, y = _polar_xy(POLAR_LABEL_RADIUS, bearing_deg)
        font = _polar_label_font(bearing_deg)
        width_label_px, height_label_px = _text_item_size_px(f"{bearing_deg}°", font)
        required_x = max(
            required_x,
            _plot_label_axis_limit(
                center=float(x),
                text_px=width_label_px,
                axis_px=width,
                margin_px=POLAR_VIEW_EDGE_MARGIN_PX,
            ),
        )
        required_y = max(
            required_y,
            _plot_label_axis_limit(
                center=float(y),
                text_px=height_label_px,
                axis_px=height,
                margin_px=POLAR_VIEW_EDGE_MARGIN_PX,
            ),
        )
    data_per_px = max(required_x / width, required_y / height)
    return float(data_per_px * width), float(data_per_px * height)


def _plot_label_axis_limit(
    *,
    center: float,
    text_px: float,
    axis_px: float,
    margin_px: float,
) -> float:
    occupied_fraction = min(
        0.95,
        max(0.0, (float(text_px) + 2.0 * float(margin_px)) / max(float(axis_px), 1.0)),
    )
    return float(abs(center)) / max(1.0 - occupied_fraction, 1e-9)


def _text_item_size_px(text: str, font: QFont) -> tuple[int, int]:
    item = pg.TextItem(text=text, anchor=(0.5, 0.5))
    item.setFont(font)
    rect = item.boundingRect()
    return int(np.ceil(rect.width())), int(np.ceil(rect.height()))


def _polar_label_font(bearing_deg: int) -> QFont:
    font = QFont()
    if int(bearing_deg) % POLAR_MAJOR_SPOKE_DEG == 0:
        font.setPointSize(FONT_POINT_SIZE_PLOT)
        font.setBold(True)
    else:
        font.setPointSize(max(1, FONT_POINT_SIZE_PLOT - 1))
    return font


def _apply_polar_view_range(plot: pg.PlotWidget) -> None:
    try:
        plot_item = plot.getPlotItem()
    except RuntimeError:
        return
    if plot_item is None:
        return
    x_limit, y_limit = _polar_view_limits_for_plot(plot)
    try:
        plot_item.setRange(
            xRange=(-x_limit, x_limit),
            yRange=(-y_limit, y_limit),
            padding=0.0,
            disableAutoRange=True,
        )
        plot.setLimits(
            xMin=-x_limit,
            xMax=x_limit,
            yMin=-y_limit,
            yMax=y_limit,
        )
    except RuntimeError:
        return


def set_polar_data_radius(plot: pg.PlotWidget, radius: float) -> None:
    """Resize the polar view so the current unnormalized trace remains visible."""
    try:
        value = float(radius)
    except (TypeError, ValueError):
        value = 1.0
    if not np.isfinite(value):
        value = 1.0
    setattr(plot, POLAR_DATA_RADIUS_ATTR, max(1.0, abs(value)))
    _apply_polar_view_range(plot)


def _format_polar_scale_value(value: float, suffix: str) -> str:
    if not np.isfinite(value):
        value = 0.0
    if suffix.strip() == "dB":
        return f"{value:.0f} {suffix.strip()}"
    value_abs = abs(float(value))
    if value_abs >= 100.0:
        text = f"{value:.0f}"
    elif value_abs >= 10.0:
        text = f"{value:.1f}"
    elif value_abs >= 1.0:
        text = f"{value:.2f}"
    else:
        text = f"{value:.3g}"
    return f"{text}{suffix}"


def set_polar_radial_scale(
    plot: pg.PlotWidget,
    *,
    radial_min: float,
    radial_max: float,
    suffix: str = "",
) -> None:
    """Update radial ring labels for the current value-to-radius scale."""
    labels = getattr(plot, POLAR_RADIAL_LABELS_ATTR, ())
    span = max(float(radial_max) - float(radial_min), 1e-12)
    for radius, label in zip(POLAR_RING_RADII, labels, strict=False):
        value = float(radial_min) + span * float(radius)
        label.setText(_format_polar_scale_value(value, suffix))


def _add_polar_grid(plot: pg.PlotWidget) -> None:
    """Draw static polar axes for top-zero clockwise bearing coordinates."""

    grid_pen = pg.mkPen(FG_MUTED, width=0.75)
    major_pen = pg.mkPen(FG_MUTED, width=1.15)
    radial_labels: list[pg.TextItem] = []
    for radius in POLAR_RING_RADII:
        ring = QGraphicsEllipseItem(-radius, -radius, 2.0 * radius, 2.0 * radius)
        ring.setPen(major_pen if radius == 1.0 else grid_pen)
        ring.setBrush(pg.mkBrush(255, 255, 255, 0))
        plot.addItem(ring)
        label_x, label_y = _polar_xy(radius, 75.0)
        label = pg.TextItem(
            text="",
            color=FG_MUTED,
            anchor=(0.0, 0.5),
            fill=pg.mkBrush(255, 255, 255, 0),
            border=None,
        )
        font = QFont()
        font.setPointSize(max(1, FONT_POINT_SIZE_PLOT - 1))
        label.setFont(font)
        label.setPos(label_x, label_y)
        plot.addItem(label)
        radial_labels.append(label)
    setattr(plot, POLAR_RADIAL_LABELS_ATTR, tuple(radial_labels))

    for bearing_deg in range(0, 360, POLAR_SPOKE_STEP_DEG):
        x, y = _polar_xy(1.0, bearing_deg)
        pen = (
            major_pen
            if bearing_deg % POLAR_MAJOR_SPOKE_DEG == 0
            else grid_pen
        )
        spoke = pg.PlotDataItem([0.0, x], [0.0, y], pen=pen)
        spoke.setClipToView(False)
        spoke.setDownsampling(auto=False)
        plot.addItem(spoke)

        label_x, label_y = _polar_xy(POLAR_LABEL_RADIUS, bearing_deg)
        label = pg.TextItem(
            text=f"{bearing_deg}°",
            color=FG_TEXT if bearing_deg % POLAR_MAJOR_SPOKE_DEG == 0 else FG_MUTED,
            anchor=(0.5, 0.5),
            fill=pg.mkBrush(255, 255, 255, 0),
            border=None,
        )
        label.setFont(_polar_label_font(bearing_deg))
        label.setPos(label_x, label_y)
        plot.addItem(label)


def build_doa_polar_plot() -> tuple[pg.PlotWidget, pg.PlotDataItem, pg.PlotDataItem]:
    """Build a polar MUSIC response plot using display bearing coordinates."""

    plot = _PolarPlotWidget()
    style_plot(plot)
    plot.setClipToView(False)
    plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    plot.setAspectLocked(True, ratio=1.0)
    plot.hideAxis("left")
    plot.hideAxis("bottom")
    plot.getPlotItem().getViewBox().setDefaultPadding(0.0)
    _apply_polar_view_range(plot)
    plot.showGrid(x=False, y=False)
    _add_polar_grid(plot)

    curve = plot.plot(pen=pg.mkPen(DOA_COLOR, width=2.0))
    marker = plot.plot(pen=pg.mkPen(WARNING, width=2.0))
    return plot, curve, marker


def build_lcmv_response_plot(
    *,
    doa_min_deg: float,
    doa_max_deg: float,
) -> tuple[pg.PlotWidget, pg.PlotDataItem, pg.InfiniteLine]:
    """Build the LCMV angular response plot using display bearing coordinates."""
    plot, curve, marker = build_azimuth_response_plot(
        bottom_label="DoA bearing (deg)",
        left_label="Ideal steering-vector model response dB",
        response_color=DOA_COLOR,
        y_range=(-80.0, 5.0),
        doa_min_deg=doa_min_deg,
        doa_max_deg=doa_max_deg,
    )
    plot.setTitle(
        "Active LCMV ideal steering-vector model response",
        color=FG_TEXT,
    )
    return plot, curve, marker
