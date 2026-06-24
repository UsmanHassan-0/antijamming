"""Theme tokens and stylesheet builders for the realtime PyQt UI."""

from __future__ import annotations


# =============================================================================
# Base Scale and Typography
# =============================================================================

# UI sizing follows an 8-point grid. Derived constants should use GRID_UNIT or
# grid() so spacing remains consistent across dense controls and plot panels.
GRID_UNIT = 8
SPACE_8 = GRID_UNIT
SPACE_16 = GRID_UNIT * 2
SPACE_32 = GRID_UNIT * 4
CONTROL_H = 40
RADIUS_8 = 8
WHITE = "#FFFFFF"
FONT_FAMILY_UI = "Noto Sans"
# Operator displays should be readable at normal monitor distance. Keep a clear
# hierarchy instead of using dense, low-value micro text.
FONT_SIZE_BODY = 15
FONT_SIZE_SECONDARY = 14
FONT_SIZE_EMPHASIS = 17
FONT_SIZE_TITLE = 24
FONT_SIZE_DISPLAY = 28
FONT_POINT_SIZE_PLOT = 11
FONT_POINT_SIZE_PLOT_LABEL = 12
FONT_POINT_SIZE_MARKER = 11
FONT_POINT_SIZE_MARKER_STATIC = 12


# =============================================================================
# Color Tokens
# =============================================================================

# Neutral monochromatic base; semantic colors below should carry meaning, not decoration.
# The palette deliberately avoids one-note color families: neutral surfaces carry
# structure, while blue/green/amber/red tokens carry operational state.
BG_APP = "#F5F6F8"
BG_PANEL = "#FFFFFF"
BG_SUBTLE = "#FAFAFA"
FG_TEXT = "#0F172A"
FG_SOFT = "#475569"
FG_MUTED = "#334155"
BORDER = "#475569"
INPUT_BORDER = "#475569"
INFO = "#1E3A8A"
INFO_SOFT = "#DBEAFE"
SUCCESS = "#14532D"
WARNING = "#78350F"
ALERT = "#7F1D1D"
ALERT_SOFT = "#FEE2E2"
DISABLED_BG = "#E5E7EB"
DISABLED_TEXT = "#374151"
DOA_COLOR = "#1D4ED8"
LCMV_COLOR = "#0F766E"
BUTTON_SUCCESS_HOVER = "#166534"
BUTTON_ALERT_HOVER = "#991B1B"
# GNSS state colors are shared by PRN Monitor and Skyplot. Tracking colors are
# intentionally light; PVT-used colors are darker in the same constellation hue.
GPS_ACQUIRED = "#F59E0B"
GPS_ASSIGNED = "#64748B"
GPS_TRACKING = "#86EFAC"
GPS_TRACKING_FIX = "#166534"
GALILEO_TRACKING = "#BFDBFE"
GALILEO_TRACKING_FIX = "#1E3A8A"
BEIDOU_TRACKING = "#FDE68A"
BEIDOU_TRACKING_FIX = "#854D0E"
GLONASS_TRACKING = "#C7D2FE"
GLONASS_TRACKING_FIX = "#3730A3"


# =============================================================================
# Unit Helpers
# =============================================================================

# Stylesheet builders below return plain strings because PyQt widgets consume Qt
# stylesheet text directly. Keeping string construction centralized prevents
# small visual differences from spreading through widget files.

def px(value: int | float) -> str:
    """Format a numeric value as an integer CSS pixel value."""
    return f"{int(value)}px"


def grid(units: int = 1) -> int:
    """Convert 8-point grid units to pixels."""
    return GRID_UNIT * int(units)


def grid_tuple(*units: int) -> tuple[int, ...]:
    """Convert multiple 8-point grid units to a pixel tuple."""
    return tuple(grid(unit) for unit in units)


def spacing_pair(vertical: int = SPACE_8, horizontal: int = SPACE_16) -> str:
    """Return CSS padding/margin shorthand for vertical and horizontal spacing."""
    return f"{px(vertical)} {px(horizontal)}"


def transparent_style() -> str:
    """Return a transparent, borderless widget style."""
    return "background:transparent; border:none;"


# =============================================================================
# Text and Box Styles
# =============================================================================

# The lower-level builders are intentionally small. Higher-level styles compose
# them so semantic colors, typography, and radius changes stay centralized.

def text_style(
    *,
    color: str = FG_TEXT,
    font_size: int = FONT_SIZE_BODY,
    font_weight: int | None = None,
    background: str = "transparent",
    border: str = "none",
) -> str:
    """Build a standard text style from semantic theme tokens."""
    weight = f" font-weight:{font_weight};" if font_weight is not None else ""
    return (
        f"color:{color}; font-size:{font_size}px;{weight} border:{border}; "
        f"background:{background}; font-family:'{FONT_FAMILY_UI}';"
    )


def box_style(
    *,
    background: str = BG_PANEL,
    border_color: str = BORDER,
    radius: int = RADIUS_8,
) -> str:
    """Build a bordered box style used by panels and cards."""
    return f"background:{background}; border:1px solid {border_color}; border-radius:{px(radius)};"


def control_padding() -> str:
    """Return the shared padding used by compact controls."""
    return spacing_pair(SPACE_8, SPACE_16)


# =============================================================================
# Application Stylesheet
# =============================================================================

# The root stylesheet handles broad Qt selectors and object names. Widget modules
# still set object names, but they do not duplicate the theme values.

def build_root_stylesheet() -> str:
    """Build the application-level stylesheet applied to the main window."""
    return (
        f"background:{BG_APP}; color:{FG_TEXT}; font-family:'{FONT_FAMILY_UI}';"
        f"QLabel{{font-size:{FONT_SIZE_BODY}px; border:none; background:transparent; font-family:'{FONT_FAMILY_UI}';}}"
        f"QFrame#panel{{{panel_style()}}}"
        f"QFrame#summaryCard{{{summary_card_style()}}}"
        f"QLabel#panelTitle{{{panel_title_style()}}}"
        f"QLabel#panelSubtitle{{{panel_subtitle_style()}}}"
        f"QLabel#metricTitle{{{metric_title_style()} letter-spacing:0;}}"
        f"QLabel#metricValue{{{metric_value_style()}}}"
        f"QPushButton#startAction{{{start_button_style()}}}"
        f"QPushButton#startAction:hover{{background:{BUTTON_SUCCESS_HOVER};}}"
        f"QPushButton#stopAction{{{stop_button_style()}}}"
        f"QPushButton#stopAction:hover{{background:{BUTTON_ALERT_HOVER};}}"
        f"QPushButton:disabled{{background:{DISABLED_BG}; color:{DISABLED_TEXT}; border:1px solid {DISABLED_BG}; font-family:'{FONT_FAMILY_UI}';}}"
    )


def operator_tabs_style() -> str:
    """Return accessible view-navigation styling for the operator tabs."""
    return (
        f"QTabWidget#operatorTabs{{background:{BG_APP}; border:none;}}"
        f"QTabWidget#operatorTabs::pane{{background:{BG_APP}; border:none;}}"
        f"QTabBar#operatorNavTabs{{background:{BG_APP}; border:none;}}"
        f"QTabBar#operatorNavTabs::tab{{background:{BG_SUBTLE}; color:{FG_SOFT};"
        f" border:1px solid transparent; border-radius:{px(RADIUS_8)};"
        f" padding:{px(SPACE_8)} {px(SPACE_16)}; min-width:{px(112)};"
        f" min-height:{px(CONTROL_H - SPACE_16)}; margin:0px {px(SPACE_8 // 2)};}}"
        f"QTabBar#operatorNavTabs::tab:selected{{background:{BG_PANEL};"
        f" color:{INFO}; border:1px solid {BORDER}; font-weight:800;}}"
        f"QTabBar#operatorNavTabs::tab:hover:!selected{{background:{INFO_SOFT}; color:{INFO};}}"
        f"QTabBar#operatorNavTabs::tab:focus{{border:1px solid {INFO};"
        f" background:{BG_PANEL}; color:{INFO};}}"
    )


# =============================================================================
# Panel, Card, and Label Styles
# =============================================================================

def panel_style() -> str:
    """Return the standard panel frame style."""
    return box_style()


def summary_card_style() -> str:
    """Return the standard summary card frame style."""
    return box_style()


def panel_title_style() -> str:
    """Return the standard panel title label style."""
    return text_style(color=FG_TEXT, font_size=FONT_SIZE_TITLE, font_weight=700)


def panel_subtitle_style() -> str:
    """Return the standard panel subtitle label style."""
    return text_style(color=FG_SOFT, font_size=FONT_SIZE_BODY)


def metric_title_style() -> str:
    """Return the compact metric title label style."""
    return text_style(color=FG_MUTED, font_size=FONT_SIZE_BODY, font_weight=700)


def metric_value_style() -> str:
    """Return the compact metric value label style."""
    return text_style(color=FG_TEXT, font_size=FONT_SIZE_TITLE, font_weight=800)


# =============================================================================
# Button and Input Styles
# =============================================================================

def start_button_style() -> str:
    """Return the primary start action button style."""
    return (
        f"background:{SUCCESS}; color:{WHITE}; border:1px solid {BUTTON_SUCCESS_HOVER}; "
        f"border-radius:{px(RADIUS_8)}; font-weight:800; padding:{control_padding()}; "
        f"font-family:'{FONT_FAMILY_UI}'; font-size:{FONT_SIZE_EMPHASIS}px;"
    )


def stop_button_style() -> str:
    """Return the destructive stop action button style."""
    return (
        f"background:{ALERT}; color:{WHITE}; border:1px solid {BUTTON_ALERT_HOVER}; "
        f"border-radius:{px(RADIUS_8)}; font-weight:800; padding:{control_padding()}; "
        f"font-family:'{FONT_FAMILY_UI}'; font-size:{FONT_SIZE_EMPHASIS}px;"
    )


def disabled_button_style() -> str:
    """Return the disabled action button style."""
    return (
        f"background:{DISABLED_BG}; color:{DISABLED_TEXT}; border:1px solid {DISABLED_BG}; "
        f"border-radius:{px(RADIUS_8)}; font-weight:800; padding:{control_padding()}; "
        f"font-family:'{FONT_FAMILY_UI}'; font-size:{FONT_SIZE_EMPHASIS}px;"
    )


# =============================================================================
# Status and Navigation Styles
# =============================================================================


def status_row_style() -> str:
    """Return the inline status row label style."""
    return (
        f"{transparent_style()} padding:{px(SPACE_8)} 0px; "
        f"font-size:{FONT_SIZE_EMPHASIS}px;"
    )
