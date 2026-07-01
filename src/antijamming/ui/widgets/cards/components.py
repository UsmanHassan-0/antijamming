"""Reusable PyQt layout, panel, card, and plot builders."""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from antijamming.ui.theme import (
    FONT_SIZE_EMPHASIS,
    FONT_SIZE_SECONDARY,
    FG_TEXT,
    SPACE_8,
    metric_title_style,
    metric_value_style,
    panel_style,
    panel_subtitle_style,
    panel_title_style,
    summary_card_style,
    text_style,
    transparent_style,
)
from antijamming.ui.specs import (
    CARD_INNER_SPACING,
    PANEL_MARGINS,
    PLOT_TILE_MARGINS,
    ROW_SPACING,
    SECTION_SPACING,
    SUMMARY_CARD_MAX_WIDTH,
    SUMMARY_CARD_MIN_WIDTH,
    ZERO_MARGINS,
)


# =============================================================================
# Panel and Card Builders
# =============================================================================

# Component builders own object names, margins, and style application. MainWindow
# should compose these pieces instead of repeating Qt layout details.

def make_panel(title: str, subtitle: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """Create a standard titled panel and return its content layout."""
    frame = QFrame()
    frame.setObjectName("panel")
    frame.setStyleSheet(panel_style())
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    panel_layout = QVBoxLayout(frame)
    panel_layout.setContentsMargins(*PANEL_MARGINS)
    panel_layout.setSpacing(CARD_INNER_SPACING)
    if title:
        # Object names connect this label to the root stylesheet.
        title_label = QLabel(title)
        title_label.setFrameShape(QFrame.Shape.NoFrame)
        title_label.setObjectName("panelTitle")
        title_label.setStyleSheet(panel_title_style())
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(title_label)
    if subtitle:
        subtitle_label = QLabel(subtitle)
        subtitle_label.setFrameShape(QFrame.Shape.NoFrame)
        subtitle_label.setObjectName("panelSubtitle")
        subtitle_label.setStyleSheet(panel_subtitle_style())
        subtitle_label.setWordWrap(True)
        panel_layout.addWidget(subtitle_label)
    return frame, panel_layout


def make_metric_card(
    title: str,
    value_label: QLabel,
    *,
    min_width: int = SUMMARY_CARD_MIN_WIDTH,
    max_width: int = SUMMARY_CARD_MAX_WIDTH,
) -> QFrame:
    """Create a metric card with constrained width for dashboard rows."""
    frame = QFrame()
    frame.setObjectName("summaryCard")
    frame.setStyleSheet(summary_card_style())
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    frame.setMinimumWidth(min_width)
    frame.setMaximumWidth(max_width)
    frame_layout = QVBoxLayout(frame)
    frame_layout.setContentsMargins(*PANEL_MARGINS)
    frame_layout.setSpacing(CARD_INNER_SPACING)
    title_label = QLabel(title.upper())
    title_label.setFrameShape(QFrame.Shape.NoFrame)
    title_label.setObjectName("metricTitle")
    title_label.setStyleSheet(metric_title_style())
    title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    frame_layout.addWidget(title_label)
    value_label.setWordWrap(True)
    value_label.setFrameShape(QFrame.Shape.NoFrame)
    value_label.setObjectName("metricValue")
    value_label.setStyleSheet(metric_value_style())
    value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    frame_layout.addWidget(value_label)
    return frame


def make_summary_card(
    title: str,
    value_label: QLabel,
    *,
    min_width: int = SUMMARY_CARD_MIN_WIDTH,
    max_width: int = SUMMARY_CARD_MAX_WIDTH,
) -> QFrame:
    """Create a compact status card whose value text does not repeat the title."""
    frame = make_metric_card(title, value_label, min_width=min_width, max_width=max_width)
    value_label.setObjectName("summaryValue")
    value_label.setStyleSheet(
        text_style(color=FG_TEXT, font_size=FONT_SIZE_SECONDARY, font_weight=700)
    )
    value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_label = frame.findChild(QLabel, "metricTitle")
    if title_label is not None:
        title_label.setStyleSheet(metric_title_style())
        title_label.setToolTip("")
    frame.setToolTip(title)
    return frame


def _make_plot_tile(title: str, plot_widget: QWidget) -> QFrame:
    frame, frame_layout = make_panel(title)
    frame.setStyleSheet(transparent_style())
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    frame_layout.setContentsMargins(*PLOT_TILE_MARGINS)
    frame_layout.setSpacing(CARD_INNER_SPACING)
    title_label = frame.findChild(QLabel, "panelTitle")
    if title_label is not None:
        title_label.setStyleSheet(
            text_style(color=FG_TEXT, font_size=FONT_SIZE_EMPHASIS, font_weight=700)
        )
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    frame_layout.addWidget(plot_widget, stretch=1)
    return frame


# =============================================================================
# Compact Rows and Grids
# =============================================================================


def make_card_grid(cards: Sequence[QWidget], columns: int = 2) -> QGridLayout:
    """Arrange cards into an evenly stretched grid layout."""
    grid = QGridLayout()
    grid.setContentsMargins(*ZERO_MARGINS)
    grid.setHorizontalSpacing(ROW_SPACING)
    grid.setVerticalSpacing(ROW_SPACING)
    cols = max(1, int(columns))
    for idx, card in enumerate(cards):
        row = idx // cols
        col = idx % cols
        grid.addWidget(card, row, col)
    for col in range(cols):
        grid.setColumnStretch(col, 1)
    rows = max(1, (len(cards) + cols - 1) // cols)
    for row in range(rows):
        grid.setRowStretch(row, 1)
    return grid


def make_stretch_row(
    items: Sequence[tuple[QWidget, int]],
    *,
    spacing: int = ROW_SPACING,
) -> QHBoxLayout:
    """Create a horizontal row from widgets with explicit stretch factors."""
    row = QHBoxLayout()
    row.setContentsMargins(*ZERO_MARGINS)
    row.setSpacing(spacing)
    for widget, stretch in items:
        row.addWidget(widget, stretch)
    return row


# =============================================================================
# Plot Containers
# =============================================================================

# Plot containers intentionally wrap plot widgets in styled tiles. The plot
# builders in plots.py only know pyqtgraph; this module owns Qt panel layout.

def make_plot_container(
    title: str,
    cards: Sequence[tuple[str, QWidget]],
    *,
    row_spacing: int = ROW_SPACING,
    section_spacing: int = SECTION_SPACING,
) -> QFrame:
    """Create a plot section containing one or more plot tiles."""
    container, container_layout = make_panel(title, "")
    container_layout.setSpacing(section_spacing)
    if len(cards) == 1:
        plot_title, body = cards[0]
        container_layout.addWidget(_make_plot_tile(plot_title, body), stretch=1)
        return container

    row_items = [(_make_plot_tile(plot_title, body), 1) for plot_title, body in cards]
    container_layout.addLayout(make_stretch_row(row_items, spacing=row_spacing))
    return container


def make_plot_grid_container(
    title: str,
    rows: Sequence[Sequence[tuple[str, QWidget]]],
    *,
    row_spacing: int = ROW_SPACING,
    section_spacing: int = SECTION_SPACING,
) -> QFrame:
    """Create a plot section with explicit horizontal plot rows."""
    container, container_layout = make_panel(title, "")
    container_layout.setSpacing(section_spacing)
    for row_cards in rows:
        row_items = [(_make_plot_tile(plot_title, body), 1) for plot_title, body in row_cards]
        if len(row_items) == 1:
            container_layout.addWidget(row_items[0][0], stretch=1)
        elif row_items:
            container_layout.addLayout(make_stretch_row(row_items, spacing=row_spacing), stretch=1)
    return container


def make_plot_body(plot_widget: QWidget, *, spacing: int = SPACE_8) -> QWidget:
    """Wrap a plot widget in a transparent layout body."""
    body = QWidget()
    body.setStyleSheet(transparent_style())
    layout = QVBoxLayout(body)
    layout.setContentsMargins(*ZERO_MARGINS)
    layout.setSpacing(spacing)
    layout.addWidget(plot_widget)
    return body
