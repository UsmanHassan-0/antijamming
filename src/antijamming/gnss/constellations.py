"""Supported GNSS constellation helpers."""

from __future__ import annotations

SUPPORTED_CONSTELLATIONS = ("gps", "galileo", "beidou", "glonass")

_ALIASES = {
    "g": "gps",
    "gp": "gps",
    "gps": "gps",
    "e": "galileo",
    "ga": "galileo",
    "gal": "galileo",
    "galileo": "galileo",
    "c": "beidou",
    "b": "beidou",
    "bd": "beidou",
    "gb": "beidou",
    "bds": "beidou",
    "beidou": "beidou",
    "r": "glonass",
    "gl": "glonass",
    "glo": "glonass",
    "glonass": "glonass",
}
_PREFIXES = {
    "gps": "G",
    "galileo": "E",
    "beidou": "C",
    "glonass": "R",
}
_ORDER = {name: index for index, name in enumerate(SUPPORTED_CONSTELLATIONS)}


def normalize_constellation(value: object, *, default: str | None = "gps") -> str | None:
    """Return a supported normalized constellation name or None."""

    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return _ALIASES.get(raw)


def constellation_prefix(constellation: object) -> str | None:
    normalized = normalize_constellation(constellation, default=None)
    return None if normalized is None else _PREFIXES[normalized]


def satellite_label(constellation: object, prn: int) -> str | None:
    prefix = constellation_prefix(constellation)
    if prefix is None or int(prn) <= 0:
        return None
    return f"{prefix}{int(prn):02d}"


def satellite_label_constellation(label: object) -> str | None:
    text = str(label or "").strip()
    if not text:
        return None
    prefix = text[:1].lower()
    return normalize_constellation(prefix, default=None)


def satellite_sort_key(label: str) -> tuple[int, int, str]:
    constellation = satellite_label_constellation(label)
    try:
        prn = int(str(label)[1:])
    except ValueError:
        prn = 0
    return (_ORDER.get(constellation or "", 99), prn, str(label))


__all__ = [
    "SUPPORTED_CONSTELLATIONS",
    "constellation_prefix",
    "normalize_constellation",
    "satellite_label",
    "satellite_label_constellation",
    "satellite_sort_key",
]
