"""Small helper models and satellite-key helpers for GNSS-SDR state."""

from __future__ import annotations

from dataclasses import dataclass

from antijamming.gnss.constellations import (
    normalize_constellation,
    satellite_label,
    satellite_sort_key,
)

_SatKey = tuple[str, int]

def _constellation_from_token(token: object) -> str:
    """Normalize GNSS-SDR/NMEA constellation names to snapshot names."""

    return normalize_constellation(token) or "gps"

def _sat_key(constellation: object, prn: int) -> _SatKey:
    """Return a constellation-qualified key without cross-system PRN collisions."""

    return (_constellation_from_token(constellation), int(prn))

def _sat_constellation(key: _SatKey) -> str:
    return key[0]

def _sat_prn(key: _SatKey) -> int:
    return int(key[1])

def _sat_label(key: _SatKey) -> str:
    return satellite_label(_sat_constellation(key), _sat_prn(key)) or f"G{_sat_prn(key):02d}"

def _sat_sort_key(key: _SatKey) -> tuple[int, int]:
    order, prn, _label = satellite_sort_key(_sat_label(key))
    return (order, prn)

def _sat_public_fields(key: _SatKey) -> dict[str, object]:
    fields: dict[str, object] = {"prn": _sat_prn(key)}
    if _sat_constellation(key) != "gps":
        fields["constellation"] = _sat_constellation(key)
        fields["satellite_id"] = _sat_label(key)
    return fields


@dataclass(frozen=True)
class _GnssSdrProcessInfo:
    """Small process descriptor used for scoped stale-session cleanup."""

    pid: int
    cmdline: tuple[str, ...]


__all__ = [
    "_GnssSdrProcessInfo",
    "_SatKey",
    "_constellation_from_token",
    "_sat_constellation",
    "_sat_key",
    "_sat_label",
    "_sat_prn",
    "_sat_public_fields",
    "_sat_sort_key",
]
