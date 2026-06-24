from __future__ import annotations

from pathlib import Path
import re

from antijamming.ui.accessibility import (
    WCAG_AA_TEXT_CONTRAST,
    WCAG_NON_TEXT_CONTRAST,
)
from antijamming.ui import specs
from antijamming.ui import theme
from antijamming.ui.widgets.skyplot import _skyplot_style

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "antijamming" / "ui"


def _rgb_from_hex(color: str) -> tuple[float, float, float]:
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", color), color
    return tuple(int(color[index : index + 2], 16) / 255.0 for index in (1, 3, 5))


def _linear_channel(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(color: str) -> float:
    red, green, blue = (_linear_channel(channel) for channel in _rgb_from_hex(color))
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_ratio_helpers_match_wcag_reference_values() -> None:
    assert _rgb_from_hex("#FFFFFF") == (1.0, 1.0, 1.0)
    assert _relative_luminance("#000000") == 0.0
    assert _relative_luminance("#FFFFFF") == 1.0
    assert _contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert round(_contrast_ratio("#777777", "#FFFFFF"), 2) == 4.48


def test_theme_text_contrast_meets_wcag_aa() -> None:
    for foreground in (theme.FG_TEXT, theme.FG_SOFT, theme.FG_MUTED):
        for background in (theme.BG_PANEL, theme.BG_APP, theme.BG_SUBTLE):
            assert _contrast_ratio(foreground, background) >= WCAG_AA_TEXT_CONTRAST


def test_theme_action_and_status_contrast() -> None:
    assert _contrast_ratio(theme.WHITE, theme.SUCCESS) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.ALERT) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.ALERT, theme.ALERT_SOFT) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.INFO, theme.INFO_SOFT) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.DISABLED_TEXT, theme.DISABLED_BG) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_SOFT, theme.BG_APP) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.INFO, theme.BG_APP) >= WCAG_AA_TEXT_CONTRAST


def test_non_text_ui_boundaries_meet_wcag_contrast() -> None:
    for foreground in (theme.BORDER, theme.INPUT_BORDER):
        for background in (theme.BG_PANEL, theme.BG_APP, theme.BG_SUBTLE):
            assert _contrast_ratio(foreground, background) >= WCAG_NON_TEXT_CONTRAST


def test_plot_and_status_marks_meet_wcag_non_text_contrast() -> None:
    plot_marks = [
        theme.DOA_COLOR,
        theme.LCMV_COLOR,
        theme.GPS_ASSIGNED,
        theme.GPS_TRACKING_FIX,
        theme.GALILEO_TRACKING_FIX,
        theme.BEIDOU_TRACKING_FIX,
        theme.GLONASS_TRACKING_FIX,
    ]
    for foreground in plot_marks:
        assert _contrast_ratio(foreground, theme.BG_SUBTLE) >= WCAG_NON_TEXT_CONTRAST
        assert _contrast_ratio(foreground, theme.BG_PANEL) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GPS_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GALILEO_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.BEIDOU_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GLONASS_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST


def test_gnss_state_colors_are_distinct_and_readable_with_intended_text() -> None:
    assert theme.GPS_ACQUIRED == "#F59E0B"
    assert theme.GPS_TRACKING == "#86EFAC"
    assert theme.GPS_TRACKING_FIX == "#166534"
    assert theme.GALILEO_TRACKING == "#BFDBFE"
    assert theme.GALILEO_TRACKING_FIX == "#1E3A8A"
    assert theme.BEIDOU_TRACKING == "#FDE68A"
    assert theme.BEIDOU_TRACKING_FIX == "#854D0E"
    assert theme.GLONASS_TRACKING == "#C7D2FE"
    assert theme.GLONASS_TRACKING_FIX == "#3730A3"
    assert theme.GPS_ASSIGNED != theme.GPS_TRACKING
    assert len(
        {
            theme.GPS_ACQUIRED,
            theme.GPS_ASSIGNED,
            theme.GPS_TRACKING,
            theme.GPS_TRACKING_FIX,
            theme.GALILEO_TRACKING,
            theme.GALILEO_TRACKING_FIX,
            theme.BEIDOU_TRACKING,
            theme.BEIDOU_TRACKING_FIX,
            theme.GLONASS_TRACKING,
            theme.GLONASS_TRACKING_FIX,
        }
    ) == 10
    assert _contrast_ratio(theme.FG_TEXT, theme.GPS_ACQUIRED) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_TEXT, theme.GPS_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GPS_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_TEXT, theme.GALILEO_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GALILEO_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_TEXT, theme.BEIDOU_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.BEIDOU_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_TEXT, theme.GLONASS_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GLONASS_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.GPS_TRACKING_FIX, theme.BG_PANEL) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.GALILEO_TRACKING_FIX, theme.BG_PANEL) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.BEIDOU_TRACKING_FIX, theme.BG_PANEL) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.GLONASS_TRACKING_FIX, theme.BG_PANEL) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.GPS_TRACKING, theme.GPS_TRACKING_FIX) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.GALILEO_TRACKING, theme.GALILEO_TRACKING_FIX) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.BEIDOU_TRACKING, theme.BEIDOU_TRACKING_FIX) >= WCAG_NON_TEXT_CONTRAST
    assert _contrast_ratio(theme.GLONASS_TRACKING, theme.GLONASS_TRACKING_FIX) >= WCAG_NON_TEXT_CONTRAST


def test_skyplot_tracking_text_matches_marker_brightness() -> None:
    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=False)
    assert (brush, pen, text_color) == (
        theme.BG_PANEL,
        theme.GPS_TRACKING,
        theme.FG_TEXT,
    )

    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=True)
    assert (brush, pen, text_color) == (
        theme.GPS_TRACKING_FIX,
        theme.GPS_TRACKING_FIX,
        theme.WHITE,
    )
    assert _contrast_ratio(theme.FG_TEXT, theme.GPS_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GPS_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.FG_TEXT, theme.BG_PANEL) >= WCAG_AA_TEXT_CONTRAST

    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=False, satellite_label="E12")
    assert (brush, pen, text_color) == (
        theme.BG_PANEL,
        theme.GALILEO_TRACKING,
        theme.FG_TEXT,
    )

    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=True, satellite_label="E12")
    assert (brush, pen, text_color) == (
        theme.GALILEO_TRACKING_FIX,
        theme.GALILEO_TRACKING_FIX,
        theme.WHITE,
    )
    assert _contrast_ratio(theme.FG_TEXT, theme.GALILEO_TRACKING) >= WCAG_AA_TEXT_CONTRAST
    assert _contrast_ratio(theme.WHITE, theme.GALILEO_TRACKING_FIX) >= WCAG_AA_TEXT_CONTRAST

    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=False, satellite_label="C07")
    assert (brush, pen, text_color) == (
        theme.BG_PANEL,
        theme.BEIDOU_TRACKING,
        theme.FG_TEXT,
    )

    brush, pen, text_color = _skyplot_style("tracking", used_in_fix=True, satellite_label="R03")
    assert (brush, pen, text_color) == (
        theme.GLONASS_TRACKING_FIX,
        theme.GLONASS_TRACKING_FIX,
        theme.WHITE,
    )


def test_disabled_button_style_keeps_shape_tokens() -> None:
    style = theme.disabled_button_style()
    assert "border-radius:8px" in style
    assert "padding:8px 16px" in style
    assert "font-weight:800" in style


def test_typography_uses_readable_operator_scale() -> None:
    assert theme.FONT_SIZE_BODY >= 15
    assert theme.FONT_SIZE_SECONDARY >= 14
    assert theme.FONT_SIZE_EMPHASIS >= 17
    assert theme.FONT_SIZE_TITLE >= 24
    assert theme.FONT_POINT_SIZE_PLOT >= 11
    assert theme.FONT_POINT_SIZE_PLOT_LABEL >= 12
    assert theme.FONT_POINT_SIZE_MARKER >= 11
    assert theme.FONT_POINT_SIZE_MARKER_STATIC >= 12
    assert (
        theme.FONT_SIZE_SECONDARY
        < theme.FONT_SIZE_BODY
        < theme.FONT_SIZE_EMPHASIS
        < theme.FONT_SIZE_TITLE
        < theme.FONT_SIZE_DISPLAY
    )


def test_ui_color_tokens_are_centralized_in_theme() -> None:
    hex_color = re.compile(r"#[0-9A-Fa-f]{6}")
    offenders: list[str] = []
    for path in UI_DIR.rglob("*.py"):
        if path.relative_to(UI_DIR).as_posix() in {"theme/tokens.py"}:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if hex_color.search(line):
                offenders.append(f"{path.relative_to(UI_DIR)}:{line_number}:{line.strip()}")
    assert offenders == []


def test_theme_avoids_unbounded_pill_radii() -> None:
    theme_source = (UI_DIR / "theme" / "tokens.py").read_text(encoding="utf-8")
    assert "border-radius:999" not in theme_source


def test_ui_spacing_and_dimensions_follow_8_point_grid() -> None:
    scalar_names = (
        "PLOT_AXIS_BOTTOM_HEIGHT",
        "PLOT_AXIS_LEFT_WIDTH",
        "PLOT_AXIS_TICK_TEXT_OFFSET",
        "PLOT_LEGEND_SAMPLE_WIDTH",
        "PRN_PLOT_MIN_HEIGHT",
        "SCROLLBAR_HANDLE_MIN_LENGTH",
        "SCROLLBAR_THICKNESS",
        "SKYPLOT_MARKER_SIZE",
        "SKYPLOT_MAX_SIZE",
        "SKYPLOT_MIN_SIZE",
        "SUMMARY_CARD_MAX_WIDTH",
        "SUMMARY_CARD_MIN_WIDTH",
        "WINDOW_DEFAULT_HEIGHT",
        "WINDOW_DEFAULT_WIDTH",
        "CARD_INNER_SPACING",
        "COMPACT_SPACING",
        "ROW_SPACING",
        "SECTION_SPACING",
    )
    for name in scalar_names:
        value = getattr(specs, name)
        assert isinstance(value, int), name
        assert value % theme.GRID_UNIT == 0, name

    tuple_names = (
        "PANEL_MARGINS",
        "PAGE_MARGINS",
        "PLOT_ITEM_MARGINS",
        "PLOT_LEGEND_OFFSET",
        "PLOT_TILE_MARGINS",
    )
    for name in tuple_names:
        values = getattr(specs, name)
        assert all(value % theme.GRID_UNIT == 0 for value in values), name


def test_operator_spacing_uses_readable_dashboard_rhythm() -> None:
    assert min(specs.PAGE_MARGINS) >= theme.grid(3)
    assert min(specs.PANEL_MARGINS) >= theme.grid(3)
    assert specs.ROW_SPACING >= theme.grid(3)
    assert specs.SECTION_SPACING >= theme.grid(3)
    assert specs.CARD_INNER_SPACING >= theme.grid(2)
