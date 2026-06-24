"""Layout geometry, plot sizing, and UI structure constants."""

from __future__ import annotations

from antijamming.ui.theme import grid, grid_tuple


# =============================================================================
# Shared Layout Spacing
# =============================================================================

# Margins are expressed in 8-point grid units through theme.grid_tuple. Widgets
# should import these values instead of hard-coding pixel margins.
ZERO_MARGINS = (0, 0, 0, 0)
PAGE_MARGINS = grid_tuple(3, 3, 3, 3)
PANEL_MARGINS = grid_tuple(3, 3, 3, 3)
PLOT_TILE_MARGINS = grid_tuple(2, 2, 2, 2)
CARD_INNER_SPACING = grid(2)
SECTION_SPACING = grid(3)
ROW_SPACING = grid(3)
COMPACT_SPACING = grid(1)


# =============================================================================
# Plot Geometry
# =============================================================================

# Plot axis dimensions are fixed so pyqtgraph label and tick layout does not
# shift as values update during a run.
PLOT_ITEM_MARGINS = grid_tuple(2, 2, 2, 2)
PLOT_AXIS_TICK_TEXT_OFFSET = grid(2)
PLOT_AXIS_LEFT_WIDTH = grid(10)
PLOT_AXIS_BOTTOM_HEIGHT = grid(8)
PLOT_LEGEND_OFFSET = grid_tuple(2, 2)
PLOT_LEGEND_SAMPLE_WIDTH = grid(2)
SKYPLOT_MARKER_SIZE = grid(4)
PLOT_GRID_ALPHA_PERCENT = 35
PLOT_DENSE_GRID_ALPHA_PERCENT = 30


# =============================================================================
# Main Window Geometry
# =============================================================================

# Window and card dimensions are intentionally centralized here rather than in
# MainWindow, keeping layout changes independent from update/rendering logic.
WINDOW_DEFAULT_WIDTH = grid(160)
WINDOW_DEFAULT_HEIGHT = grid(110)
SUMMARY_CARD_MIN_WIDTH = grid(28)
SUMMARY_CARD_MAX_WIDTH = grid(38)
SCROLLBAR_THICKNESS = grid(2)
SCROLLBAR_HANDLE_MIN_LENGTH = grid(4)


# =============================================================================
# GNSS Monitor Geometry
# =============================================================================

# GNSS widgets have fixed-format content: 32 possible GPS L1 C/A PRNs and a
# square skyplot. Stable dimensions prevent labels and bars from resizing panels.
PRN_COUNT = 32
PRN_STATE_BAR_HEIGHT = 8.0
PRN_PLOT_MIN_HEIGHT = grid(34)
PRN_PLOT_AXIS_BOTTOM_HEIGHT = grid(4)
PRN_PLOT_COMPACT_MIN_HEIGHT = grid(8)
SKYPLOT_MIN_SIZE = grid(40)
SKYPLOT_MAX_SIZE = grid(80)
SKYPLOT_COMPACT_MIN_SIZE = grid(14)

# =============================================================================
# Realtime Plot Defaults
# =============================================================================

REALTIME_ALGORITHM_PLOT_MIN_HEIGHT = grid(30)
REALTIME_ALGORITHM_PLOT_COMPACT_MIN_HEIGHT = grid(10)
