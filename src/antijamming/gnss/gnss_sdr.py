"""Compatibility facade for the GNSS-SDR FIFO bridge.

The implementation now lives under :mod:`antijamming.gnss.sdr_bridge`, but
callers may continue importing from ``antijamming.gnss.gnss_sdr`` during the
refactor.
"""

from __future__ import annotations

from .sdr_bridge import (
    GPS_L1_CA_FREQ_HZ,
    GnssSdrBridge,
    PRN_CARRIER_LOCK_THRESHOLD,
    PRN_CNO_MAX_PEAK_TO_PEAK_DB,
    PRN_CNO_MAX_STDEV_DB,
    PRN_CNO_MIN_STABLE_DB_HZ,
    PRN_CNO_REQUIRED_STABLE_WINDOWS,
    PRN_CNO_STABILITY_WINDOW,
    PVT_ACCURACY_TIMEOUT_S,
    PVT_DEGRADED_PDOP_THRESHOLD,
    PVT_LOW_OBSERVATION_COUNT,
    PVT_LOW_USED_SATELLITE_COUNT,
    SKY_GEOMETRY_TIMEOUT_S,
    USED_IN_FIX_TIMEOUT_S,
    _GnssSdrProcessInfo,
)
from .sdr_bridge.log_parsers import _parse_acquisition_metrics
from .sdr_bridge.models import (
    _SatKey,
    _constellation_from_token,
    _sat_constellation,
    _sat_key,
    _sat_label,
    _sat_prn,
    _sat_public_fields,
    _sat_sort_key,
)

__all__ = [
    "GPS_L1_CA_FREQ_HZ",
    "GnssSdrBridge",
    "PRN_CARRIER_LOCK_THRESHOLD",
    "PRN_CNO_MAX_PEAK_TO_PEAK_DB",
    "PRN_CNO_MAX_STDEV_DB",
    "PRN_CNO_MIN_STABLE_DB_HZ",
    "PRN_CNO_REQUIRED_STABLE_WINDOWS",
    "PRN_CNO_STABILITY_WINDOW",
    "PVT_ACCURACY_TIMEOUT_S",
    "PVT_DEGRADED_PDOP_THRESHOLD",
    "PVT_LOW_OBSERVATION_COUNT",
    "PVT_LOW_USED_SATELLITE_COUNT",
    "SKY_GEOMETRY_TIMEOUT_S",
    "USED_IN_FIX_TIMEOUT_S",
    "_GnssSdrProcessInfo",
    "_SatKey",
    "_constellation_from_token",
    "_parse_acquisition_metrics",
    "_sat_constellation",
    "_sat_key",
    "_sat_label",
    "_sat_prn",
    "_sat_public_fields",
    "_sat_sort_key",
]
