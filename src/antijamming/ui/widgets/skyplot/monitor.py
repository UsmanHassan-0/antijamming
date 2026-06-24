"""Skyplot widget for satellite azimuth/elevation geometry display."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QGraphicsEllipseItem, QSizePolicy, QVBoxLayout, QWidget

from antijamming.ui.theme import (
    ALERT,
    BG_PANEL,
    BG_SUBTLE,
    FG_MUTED,
    FG_TEXT,
    FONT_POINT_SIZE_MARKER,
    FONT_POINT_SIZE_MARKER_STATIC,
    BEIDOU_TRACKING,
    BEIDOU_TRACKING_FIX,
    GALILEO_TRACKING,
    GALILEO_TRACKING_FIX,
    GLONASS_TRACKING,
    GLONASS_TRACKING_FIX,
    GPS_ACQUIRED,
    GPS_TRACKING,
    GPS_TRACKING_FIX,
    INPUT_BORDER,
    WHITE,
    transparent_style,
)
from antijamming.ui.specs import SKYPLOT_MARKER_SIZE, SKYPLOT_MAX_SIZE, SKYPLOT_MIN_SIZE, ZERO_MARGINS
from antijamming.ui.specs import SKYPLOT_COMPACT_MIN_SIZE
from antijamming.ui.state.receiver import satellite_id, satellite_sort_key

# Explicit z-levels keep rings below markers and PRN text above markers. This is
# important because skyplot geometry can be visually dense.
_STATIC_Z = 0
_STATIC_FILL_Z = -10
_STATIC_LABEL_Z = 5
_MARKER_Z = 10
_MARKER_LABEL_Z = 20
_CARDINAL_LABEL_MIN_RADIUS = 1.08
_CARDINAL_LABEL_GAP_PX = 3
_ELEVATION_LABEL_X_OFFSET = 0.18
_RING_RADII = (1.0, 2.0 / 3.0, 1.0 / 3.0)

@dataclass(frozen=True, slots=True)
class _SkyplotRenderPoint:
    prn: int
    label: str
    x: float
    y: float
    theta_rad: float
    radius: float
    brush: str
    pen: str
    text_color: str


# =============================================================================
# Skyplot Monitor Widget
# =============================================================================

# Skyplot shows only satellites with azimuth/elevation geometry. Tracking PRNs
# without geometry are retained in inspectable state, not placed at fake positions.

class SkyplotMonitor(QWidget):
    """Render visible satellite geometry and track PRNs missing sky coordinates."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(transparent_style())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(SKYPLOT_COMPACT_MIN_SIZE, SKYPLOT_COMPACT_MIN_SIZE)
        self._plotted_prns: list[int] = []
        self._unplaced_tracking_prns: list[int] = []
        self._static_label_positions: dict[str, tuple[float, float]] = {}
        self._static_label_items_by_text: dict[str, pg.TextItem] = {}
        self._ring_radii: tuple[float, ...] = _RING_RADII
        self._band_items: list[QGraphicsEllipseItem] = []
        self._ring_items: list[QGraphicsEllipseItem] = []
        self._spoke_items: list[pg.PlotDataItem] = []
        self._static_label_items: list[pg.TextItem] = []
        self._marker_items: list[pg.ScatterPlotItem] = []
        self._marker_label_items: list[pg.TextItem] = []
        self._view_range = _skyplot_view_range_for_side(SKYPLOT_MIN_SIZE)
        self._managed_plot_side: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*ZERO_MARGINS)
        layout.setSpacing(0)
        layout.addStretch(1)

        self._plot = pg.PlotWidget()
        self._plot.setMinimumSize(SKYPLOT_COMPACT_MIN_SIZE, SKYPLOT_COMPACT_MIN_SIZE)
        self._plot.setMaximumSize(SKYPLOT_MAX_SIZE, SKYPLOT_MAX_SIZE)
        self._plot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._plot.setFrameShape(QFrame.Shape.NoFrame)
        self._plot.setBackground(BG_PANEL)
        self._plot.setMenuEnabled(False)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._plot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._plot.hideButtons()
        self._plot.setClipToView(False)
        plot_item = self._plot.getPlotItem()
        plot_item.disableAutoRange()
        plot_item.layout.setContentsMargins(0, 0, 0, 0)
        plot_item.hideAxis("left")
        plot_item.hideAxis("bottom")
        plot_item.getViewBox().setBorder(None)
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=False, y=False)
        self._plot.setXRange(self._view_range[0], self._view_range[1], padding=0.0)
        self._plot.setYRange(self._view_range[2], self._view_range[3], padding=0.0)
        self._plot.setLimits(
            xMin=self._view_range[0],
            xMax=self._view_range[1],
            yMin=self._view_range[2],
            yMax=self._view_range[3],
        )
        self._build_static_geometry()
        layout.addWidget(self._plot, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot

    # -------------------------------------------------------------------------
    # Static Geometry
    # -------------------------------------------------------------------------

    def _build_static_geometry(self) -> None:
        self._static_label_positions = {}
        self._band_items = []
        self._ring_items = []
        self._spoke_items = []
        self._static_label_items = []
        self._static_label_items_by_text = {}

        # Unit radius is the horizon. Inner rings are elevation contours using
        # radius=(90-elevation)/90, so 30 deg -> 2/3 and 60 deg -> 1/3.
        for radius, fill in zip(
            self._ring_radii,
            (BG_SUBTLE, BG_PANEL, BG_SUBTLE),
            strict=True,
        ):
            band = QGraphicsEllipseItem(-radius, -radius, 2.0 * radius, 2.0 * radius)
            band.setPen(pg.mkPen(None))
            band.setBrush(pg.mkBrush(fill))
            band.setZValue(_STATIC_FILL_Z)
            self._plot.addItem(band)
            self._band_items.append(band)

        for idx, radius in enumerate(self._ring_radii):
            ring = QGraphicsEllipseItem(-radius, -radius, 2.0 * radius, 2.0 * radius)
            ring.setPen(pg.mkPen(INPUT_BORDER, width=1.4 if idx == 0 else 1.0))
            ring.setBrush(pg.mkBrush(255, 255, 255, 0))
            ring.setZValue(_STATIC_Z)
            self._plot.addItem(ring)
            self._ring_items.append(ring)

        for angle_deg in (0, 90, 180, 270):
            rad = math.radians(angle_deg)
            x = math.sin(rad)
            y = math.cos(rad)
            spoke = pg.PlotDataItem([0.0, x], [0.0, y], pen=pg.mkPen(INPUT_BORDER, width=1.0))
            spoke.setClipToView(False)
            spoke.setDownsampling(auto=False)
            spoke.setZValue(_STATIC_Z)
            self._plot.addItem(spoke)
            self._spoke_items.append(spoke)

        labels = _skyplot_static_label_positions(self._current_plot_side())
        self._static_label_positions = labels
        font = QFont()
        font.setPointSize(FONT_POINT_SIZE_MARKER_STATIC)
        font.setBold(True)
        for text, (x, y) in labels.items():
            item = pg.TextItem(
                text=text,
                color=FG_MUTED,
                anchor=(0.5, 0.5),
                fill=pg.mkBrush(255, 255, 255, 0),
                border=None,
            )
            item.setFont(font)
            item.setPos(x, y)
            item.setZValue(_STATIC_LABEL_Z)
            self._plot.addItem(item)
            self._static_label_items.append(item)
            self._static_label_items_by_text[text] = item

    # -------------------------------------------------------------------------
    # Snapshot Rendering
    # -------------------------------------------------------------------------

    def update_snapshot(
        self,
        sat_entries: list[dict[str, object]],
        unplaced_tracking_prns: list[int] | None = None,
    ) -> None:
        self._plotted_prns = []
        self._unplaced_tracking_prns = sorted(set(unplaced_tracking_prns or []))
        self._marker_items = []
        self._marker_label_items = []
        self._plot.clear()
        self._build_static_geometry()
        font = QFont()
        font.setPointSize(FONT_POINT_SIZE_MARKER)
        font.setBold(True)

        for sat in _skyplot_render_points(sat_entries):
            point = pg.ScatterPlotItem(
                [sat.x],
                [sat.y],
                size=SKYPLOT_MARKER_SIZE,
                pen=pg.mkPen(sat.pen, width=1.6),
                brush=pg.mkBrush(sat.brush),
            )
            point.setZValue(_MARKER_Z)
            self._plot.addItem(point)
            self._marker_items.append(point)
            # TODO: add label collision avoidance when multiple satellites share a tight az/el cluster.
            label = pg.TextItem(
                text=sat.label,
                color=sat.text_color,
                anchor=(0.5, 0.5),
            )
            label.setFont(font)
            label.setPos(sat.x, sat.y)
            label.setZValue(_MARKER_LABEL_Z)
            self._plot.addItem(label)
            self._marker_label_items.append(label)
            self._plotted_prns.append(sat.prn)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_layout()

    def refresh_layout(self) -> None:
        """Resize the square plot and view padding from the current window size."""
        if self._managed_plot_side is None:
            local_side = min(max(1, self.width()), max(1, self.height()))
            side = max(SKYPLOT_COMPACT_MIN_SIZE, min(local_side, SKYPLOT_MAX_SIZE))
        else:
            side = self._managed_plot_side
        self._plot.setFixedSize(side, side)
        self._set_view_range(_skyplot_view_range_for_side(side))
        self._update_static_label_positions(side)

    def set_plot_side(self, side_px: int) -> None:
        """Set the square plot side from the owning window layout budget."""
        self._managed_plot_side = max(
            SKYPLOT_COMPACT_MIN_SIZE,
            min(int(side_px), SKYPLOT_MAX_SIZE),
        )
        self.refresh_layout()

    def _set_view_range(self, view_range: tuple[float, float, float, float]) -> None:
        if self._view_range == view_range:
            return
        self._view_range = view_range
        self._plot.setXRange(view_range[0], view_range[1], padding=0.0)
        self._plot.setYRange(view_range[2], view_range[3], padding=0.0)
        self._plot.setLimits(
            xMin=view_range[0],
            xMax=view_range[1],
            yMin=view_range[2],
            yMax=view_range[3],
        )

    def _current_plot_side(self) -> int:
        side = self._managed_plot_side
        if side is None:
            side = min(max(1, self._plot.width()), max(1, self._plot.height()))
        return max(SKYPLOT_COMPACT_MIN_SIZE, min(int(side), SKYPLOT_MAX_SIZE))

    def _update_static_label_positions(self, side_px: int) -> None:
        labels = _skyplot_static_label_positions(side_px)
        self._static_label_positions = labels
        for text, position in labels.items():
            item = self._static_label_items_by_text.get(text)
            if item is not None:
                item.setPos(*position)


# =============================================================================
# Skyplot Coordinate and Style Helpers
# =============================================================================

def _skyplot_xy(az_deg: float, el_deg: float) -> tuple[float, float]:
    radius = max(0.0, min(1.0, (90.0 - el_deg) / 90.0))
    az_rad = math.radians(az_deg)
    return radius * math.sin(az_rad), radius * math.cos(az_rad)


def _skyplot_render_points(sat_entries: list[dict[str, object]]) -> list[_SkyplotRenderPoint]:
    points: list[_SkyplotRenderPoint] = []
    for entry in sorted(sat_entries, key=_skyplot_entry_sort_key):
        az_deg = entry.get("az_deg")
        el_deg = entry.get("el_deg")
        try:
            prn = int(entry.get("prn", 0))
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(az_deg, (int, float))
            or not isinstance(el_deg, (int, float))
            or prn <= 0
            or float(el_deg) < 0.0
        ):
            continue

        radius = max(0.0, min(1.0, (90.0 - float(el_deg)) / 90.0))
        theta_rad = math.radians(float(az_deg))
        label = satellite_id(entry)
        if label == "--":
            continue
        brush, pen, text_color = _skyplot_style(
            str(entry.get("state", "visible")).lower(),
            bool(entry.get("used_in_fix", False)),
            label,
        )
        points.append(
            _SkyplotRenderPoint(
                prn=prn,
                label=label,
                x=radius * math.sin(theta_rad),
                y=radius * math.cos(theta_rad),
                theta_rad=theta_rad,
                radius=radius,
                brush=brush,
                pen=pen,
                text_color=text_color,
            )
        )
    return points


def _skyplot_view_range_for_side(side_px: int) -> tuple[float, float, float, float]:
    limit = _skyplot_view_limit_for_side(side_px)
    return (-limit, limit, -limit, limit)


def _skyplot_view_limit_for_side(side_px: int) -> float:
    side = max(SKYPLOT_COMPACT_MIN_SIZE, min(int(side_px), SKYPLOT_MAX_SIZE))
    label_padding_px = max(
        1,
        int(
            min(
                FONT_POINT_SIZE_MARKER_STATIC * 2.0,
                side * 0.12,
            )
        ),
    )
    drawable_side_px = max(1, side - 2 * label_padding_px)
    geometry_radius = max(
        _skyplot_cardinal_label_radius_for_side(side),
        *_RING_RADII,
    )
    return geometry_radius * (side / float(drawable_side_px))


def _skyplot_static_label_positions(side_px: int) -> dict[str, tuple[float, float]]:
    cardinal_radius = _skyplot_cardinal_label_radius_for_side(side_px)
    return {
        "N": (0.0, cardinal_radius),
        "E": (cardinal_radius, 0.0),
        "S": (0.0, -cardinal_radius),
        "W": (-cardinal_radius, 0.0),
        "30°": (_ELEVATION_LABEL_X_OFFSET, 2.0 / 3.0),
        "60°": (_ELEVATION_LABEL_X_OFFSET, 1.0 / 3.0),
    }


def _skyplot_cardinal_label_radius_for_side(side_px: int) -> float:
    side = max(SKYPLOT_COMPACT_MIN_SIZE, min(int(side_px), SKYPLOT_MAX_SIZE))
    label_half_height_px = FONT_POINT_SIZE_MARKER_STATIC * 0.8
    view_limit_estimate = 1.4
    min_data_gap = (
        (label_half_height_px + _CARDINAL_LABEL_GAP_PX)
        * 2.0
        * view_limit_estimate
        / float(side)
    )
    return max(_CARDINAL_LABEL_MIN_RADIUS, 1.0 + min_data_gap)


def _skyplot_entry_sort_key(entry: dict[str, object]) -> tuple[int, int, str]:
    return satellite_sort_key(satellite_id(entry))


def _skyplot_style(
    state: str,
    used_in_fix: bool,
    satellite_label: str = "G",
) -> tuple[str, str, str]:
    tracking_color, fix_color = _constellation_colors(satellite_label)
    if state == "tracking":
        if used_in_fix:
            return (fix_color, fix_color, WHITE)
        return (BG_PANEL, tracking_color, FG_TEXT)
    if state == "acquired":
        return (BG_PANEL, GPS_ACQUIRED, FG_TEXT)
    if state == "assigned":
        return (BG_PANEL, INPUT_BORDER, FG_TEXT)
    if state == "lost":
        return (BG_PANEL, ALERT, FG_TEXT)
    return (BG_PANEL, INPUT_BORDER, FG_TEXT)


def _constellation_colors(satellite_label: str) -> tuple[str, str]:
    prefix = satellite_label[:1].upper()
    if prefix == "E":
        return GALILEO_TRACKING, GALILEO_TRACKING_FIX
    if prefix == "C":
        return BEIDOU_TRACKING, BEIDOU_TRACKING_FIX
    if prefix == "R":
        return GLONASS_TRACKING, GLONASS_TRACKING_FIX
    return GPS_TRACKING, GPS_TRACKING_FIX
