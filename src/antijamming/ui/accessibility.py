"""WCAG contrast helpers for the PyQt theme and tests."""

from __future__ import annotations

WCAG_AA_TEXT_CONTRAST = 4.5
WCAG_AAA_TEXT_CONTRAST = 7.0
WCAG_LARGE_TEXT_CONTRAST = 3.0
WCAG_NON_TEXT_CONTRAST = 3.0
# Tests import these thresholds directly so theme changes can be checked against
# WCAG expectations without bringing up a Qt application.


# =============================================================================
# Color Conversion
# =============================================================================

# Theme colors are stored as hex strings. The helpers below stay framework-free
# so they can be used in unit tests without Qt imports.

def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """Convert a 6-digit hex color into normalized RGB channels."""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Expected 6-digit hex color, got {value!r}")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


# =============================================================================
# Contrast Metrics
# =============================================================================

def relative_luminance(value: str) -> float:
    """Return WCAG relative luminance for a hex color."""
    red, green, blue = hex_to_rgb(value)
    return (
        0.2126 * _to_linear(red)
        + 0.7152 * _to_linear(green)
        + 0.0722 * _to_linear(blue)
    )


def contrast_ratio(foreground: str, background: str) -> float:
    """Return WCAG contrast ratio between foreground and background colors."""
    lighter, darker = sorted(
        [relative_luminance(foreground), relative_luminance(background)],
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)
