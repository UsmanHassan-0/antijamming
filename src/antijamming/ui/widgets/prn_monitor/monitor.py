"""PRN C/N0 monitor showing stable tracked satellites as solid bars."""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtGui import QBrush, QColor, QFont, QPen
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from antijamming.ui.widgets.plots import PRN_Y_RANGE, style_plot
from antijamming.ui.theme import (
    BEIDOU_TRACKING,
    BEIDOU_TRACKING_FIX,
    BG_SUBTLE,
    FG_TEXT,
    FONT_POINT_SIZE_MARKER,
    GALILEO_TRACKING,
    GALILEO_TRACKING_FIX,
    GLONASS_TRACKING,
    GLONASS_TRACKING_FIX,
    GPS_TRACKING,
    GPS_TRACKING_FIX,
    transparent_style,
)
from antijamming.ui.specs import (
    PRN_PLOT_MIN_HEIGHT,
    PRN_PLOT_AXIS_BOTTOM_HEIGHT,
    PRN_STATE_BAR_HEIGHT,
    ZERO_MARGINS,
)
from antijamming.ui.state.receiver import satellite_id, satellite_sort_key


# =============================================================================
# PRN Bar Geometry
# =============================================================================

# PRN chart packing is explicit: bars keep one chosen width, and adjacent bars
# consume only the required gap instead of a full unused x-axis unit.
PRN_SINGLE_BAR_WIDTH = 0.1
PRN_BAR_GAP = 0.14
# Keep the first/last bar offset equal to the gap between adjacent bars so the
# chart reads as one packed row rather than a plot with arbitrary side gutters.
PRN_BAR_OUTER_MARGIN = PRN_BAR_GAP
PRN_MIN_VISUAL_RANGE_SPAN = 4.0
def _bar_width_for_count(visible_count: int) -> float:
    del visible_count
    return PRN_SINGLE_BAR_WIDTH


def _bar_position_for_index(index: int) -> float:
    return (
        PRN_BAR_OUTER_MARGIN
        + (PRN_SINGLE_BAR_WIDTH / 2.0)
        + (max(0, int(index)) * (PRN_SINGLE_BAR_WIDTH + PRN_BAR_GAP))
    )


def _bar_content_span(visible_count: int) -> float:
    count = max(1, int(visible_count))
    return (
        (2.0 * PRN_BAR_OUTER_MARGIN)
        + (count * PRN_SINGLE_BAR_WIDTH)
        + ((count - 1) * PRN_BAR_GAP)
    )


def _opaque_color(color: str) -> QColor:
    # PRN bars must be visually solid; keep alpha explicit so future theme edits
    # cannot accidentally reintroduce translucent bars.
    qcolor = QColor(color)
    qcolor.setAlpha(255)
    return qcolor


def _solid_brush(color: str) -> QBrush:
    return pg.mkBrush(_opaque_color(color))


def _solid_pen(color: str, width: float) -> QPen:
    return pg.mkPen(_opaque_color(color), width=width)


# =============================================================================
# Snapshot Parsing Helpers
# =============================================================================

# PRN Monitor displays entries that pass the bridge's tracking-monitor stability
# gate. Observables C/N0 may be present as a fallback value elsewhere, but valid
# pseudorange alone is not treated as C/N0 stability.

def _entry_prn(entry: dict[str, object]) -> int | None:
    try:
        prn = int(entry.get("prn", 0))
    except (TypeError, ValueError):
        return None
    return prn if prn > 0 else None


def _entry_cno(entry: dict[str, object]) -> float | None:
    # Prefer tracking-monitor C/N0 because it has channel/PRN freshness and native
    # carrier-lock checks. Observables C/N0 is a display fallback only.
    raw_value = entry.get("cno_db_hz", entry.get("observable_cno_db_hz"))
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if value <= 0.0:
        return None
    return min(max(value, PRN_Y_RANGE[0]), PRN_Y_RANGE[1])


def _entry_has_tracking_cno(entry: dict[str, object]) -> bool:
    return _entry_cno({"cno_db_hz": entry.get("cno_db_hz")}) is not None


def _entry_bar_height(entry: dict[str, object]) -> float:
    cno = _entry_cno(entry)
    if cno is not None:
        return cno
    return PRN_STATE_BAR_HEIGHT


def _entry_stable(entry: dict[str, object]) -> bool:
    return bool(entry.get("cno_stable", False))


# =============================================================================
# Pocket PRN Monitor Widget
# =============================================================================

class PocketPrnMonitor(QWidget):
    """Render tracking PRNs with raw C/N0 bar heights and fix-state colors."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(transparent_style())
        self._displayed_prns: list[int] = []
        self._bar_positions: list[float] = []
        self._bar_heights: list[float] = []
        self._bar_colors: list[str] = []
        self._x_tick_labels: list[str] = []
        self._bar_width = _bar_width_for_count(0)
        self._x_range: tuple[float, float] = self._equal_empty_gap_x_range(0)
        self._label_items: list[pg.TextItem] = []
        self._pending_tracking_prns: list[int] = []
        self._pending_tracking_reasons: dict[int, str] = {}
        self._unstable_tracking_prns: list[int] = []
        self._unstable_tracking_reasons: dict[int, str] = {}

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(*ZERO_MARGINS)
        root_layout.setSpacing(0)

        self._plot = pg.PlotWidget()
        self._plot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._plot.setMinimumHeight(PRN_PLOT_MIN_HEIGHT)
        style_plot(self._plot)
        bottom_axis = self._plot.getPlotItem().getAxis("bottom")
        bottom_axis.setHeight(PRN_PLOT_AXIS_BOTTOM_HEIGHT)
        bottom_axis.label.hide()
        self._plot.showGrid(x=False, y=False)
        # Gridlines are disabled for this plot so they do not visually cross the
        # solid bars and make them appear transparent.
        self._plot.setLabel("left", "C/N0")
        self._plot.setXRange(*self._x_range, padding=0.0)
        self._plot.setYRange(*PRN_Y_RANGE, padding=0.0)
        self._plot.setLimits(
            xMin=self._x_range[0],
            xMax=self._x_range[1],
            yMin=PRN_Y_RANGE[0],
            yMax=PRN_Y_RANGE[1],
        )

        self._bar_item = pg.BarGraphItem(
            x=[_bar_position_for_index(0)],
            height=[0.0],
            width=self._bar_width,
            brushes=[_solid_brush(BG_SUBTLE)],
            pens=[_solid_pen(BG_SUBTLE, width=1.0)],
        )
        # Bars sit above the background; labels are raised higher when created.
        self._bar_item.setZValue(10)
        self._plot.addItem(self._bar_item)

        root_layout.addWidget(self._plot, stretch=1)

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot

    # -------------------------------------------------------------------------
    # Snapshot Rendering
    # -------------------------------------------------------------------------

    def update_snapshot(self, prn_entries: list[dict[str, object]]) -> None:
        entries_by_satellite: dict[str, tuple[int, dict[str, object]]] = {}
        self._pending_tracking_prns = []
        self._pending_tracking_reasons = {}
        self._unstable_tracking_prns = []
        self._unstable_tracking_reasons = {}
        for entry in prn_entries:
            prn = _entry_prn(entry)
            if prn is None:
                continue
            if self._visual_state(str(entry.get("state", "idle")).lower()) == "idle":
                continue
            if _entry_cno(entry) is None:
                self._pending_tracking_prns.append(prn)
                self._pending_tracking_reasons[prn] = "missing_cno"
                continue
            stable = _entry_stable(entry)
            if not stable:
                reason = str(entry.get("cno_unstable_reason", "not_stable"))
                if reason in {
                    "missing_cno",
                    "too_few_samples",
                    "not_enough_stable_windows",
                    "awaiting_nav",
                }:
                    self._pending_tracking_prns.append(prn)
                    self._pending_tracking_reasons[prn] = reason
                else:
                    self._unstable_tracking_prns.append(prn)
                    self._unstable_tracking_reasons[prn] = reason
                continue
            satellite_label = satellite_id(entry)
            entries_by_satellite[satellite_label] = (prn, entry)
        self._pending_tracking_prns.sort()
        self._unstable_tracking_prns.sort()
        visible_satellites = sorted(entries_by_satellite, key=satellite_sort_key)
        self._displayed_prns = [entries_by_satellite[label][0] for label in visible_satellites]
        self._clear_labels()
        self._bar_positions = []
        self._bar_heights = []
        self._bar_colors = []
        self._x_tick_labels = []

        label_font = QFont()
        label_font.setPointSize(FONT_POINT_SIZE_MARKER)
        label_font.setBold(True)

        for x_index, satellite_label in enumerate(visible_satellites):
            prn, entry = entries_by_satellite[satellite_label]
            state = str(entry.get("state", "idle")).lower()
            visual_state = self._visual_state(state)
            used_in_fix = bool(entry.get("used_in_fix", False))
            bar_color = self._bar_color(
                visual_state,
                used_in_fix,
                satellite_label,
                stable=_entry_stable(entry),
            )
            bar_height = _entry_bar_height(entry)
            bar_position = _bar_position_for_index(x_index)
            self._bar_positions.append(bar_position)
            self._bar_heights.append(bar_height)
            self._bar_colors.append(bar_color)
            self._x_tick_labels.append(satellite_label)

            label_y = min(
                max(bar_height - 1.0, PRN_STATE_BAR_HEIGHT + 2.0),
                PRN_Y_RANGE[1] - 2.0,
            )
            label = pg.TextItem(
                text=f"{bar_height:.1f}" if _entry_cno(entry) is not None else "--",
                color=self._bar_text_color(used_in_fix),
                anchor=(0.5, 1.25),
            )
            label.setFont(label_font)
            label.setPos(bar_position, label_y)
            label.setZValue(20)
            self._plot.addItem(label)
            self._label_items.append(label)

        self._bar_width = _bar_width_for_count(len(visible_satellites))
        self._x_range = self._equal_empty_gap_x_range(len(visible_satellites))
        self._plot.setXRange(*self._x_range, padding=0.0)
        self._plot.setLimits(
            xMin=self._x_range[0],
            xMax=self._x_range[1],
            yMin=PRN_Y_RANGE[0],
            yMax=PRN_Y_RANGE[1],
        )
        bottom_axis = self._plot.getPlotItem().getAxis("bottom")
        bottom_axis.setTicks(
            [
                [
                    (position, label)
                    for position, label in zip(
                        self._bar_positions,
                        self._x_tick_labels,
                        strict=True,
                    )
                ]
            ]
        )
        self._bar_item.setOpts(
            x=self._bar_positions or [_bar_position_for_index(0)],
            height=self._bar_heights or [0.0],
            width=self._bar_width,
            brushes=[_solid_brush(color) for color in (self._bar_colors or [BG_SUBTLE])],
            pens=[
                _solid_pen(self._bar_pen_color(color), width=1.1)
                for color in (self._bar_colors or [BG_SUBTLE])
            ],
        )

    def _equal_empty_gap_x_range(self, visible_count: int) -> tuple[float, float]:
        populated_span = _bar_content_span(visible_count)
        if int(visible_count) <= 0:
            return (0.0, max(PRN_MIN_VISUAL_RANGE_SPAN, populated_span))
        if populated_span < PRN_MIN_VISUAL_RANGE_SPAN:
            extra = (PRN_MIN_VISUAL_RANGE_SPAN - populated_span) / 2.0
            return (-extra, populated_span + extra)
        # Dense PRN sets expand the view only once the fixed sparse viewport
        # cannot contain them without clipping.
        return (0.0, populated_span)

    # -------------------------------------------------------------------------
    # Bar State Styling
    # -------------------------------------------------------------------------

    def _bar_color(
        self,
        state: str,
        used_in_fix: bool,
        satellite_label: str = "G",
        *,
        stable: bool = True,
    ) -> str:
        tracking_color, fix_color = _constellation_colors(satellite_label)
        if state == "tracking" and used_in_fix:
            return fix_color
        if state == "tracking":
            return tracking_color
        return tracking_color

    def _bar_pen_color(self, color: str) -> str:
        return color

    def _bar_text_color(self, used_in_fix: bool) -> str:
        del used_in_fix
        # C/N0 labels sit above both tracking and used-in-PVT bars on the light
        # plot background, so they must use the dark foreground text token.
        return FG_TEXT

    def _visual_state(self, state: str) -> str:
        if state == "tracking":
            return state
        return "idle"

    def _clear_labels(self) -> None:
        for label in self._label_items:
            self._plot.removeItem(label)
        self._label_items.clear()


def _constellation_colors(satellite_label: str) -> tuple[str, str]:
    prefix = satellite_label[:1].upper()
    if prefix == "E":
        return GALILEO_TRACKING, GALILEO_TRACKING_FIX
    if prefix == "C":
        return BEIDOU_TRACKING, BEIDOU_TRACKING_FIX
    if prefix == "R":
        return GLONASS_TRACKING, GLONASS_TRACKING_FIX
    return GPS_TRACKING, GPS_TRACKING_FIX
