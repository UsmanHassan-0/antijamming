"""Runtime profile loading and JSON value coercion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from antijamming.config.paths import REPO_ROOT
from antijamming.config.schemas.runtime import DEFAULT_RUNTIME_CONFIG_PATH, StreamConfig


_REPO_ANCHORED_PATH_KEYS = (
    "log_dir",
    "gnss_sdr_runtime_dir",
    "gnss_sdr_log_dir",
    "gnss_sdr_repo_dir",
    "gnss_sdr_build_dir",
    "gnss_sdr_install_dir",
    "gnss_sdr_config_template",
    "gnss_agnss_gps_ephemeris_xml",
    "gnss_agnss_gal_ephemeris_xml",
    "gnss_agnss_gal_utc_model_xml",
    "gnss_agnss_gal_almanac_xml",
    "phase_calibration_file",
)

_JSON_DERIVED_FIELDS = {
    # Loaded from phase_calibration_file by app startup; not authored directly in
    # the product runtime JSON because complex numbers are not native JSON values.
    "phase_correction_vector",
}

_PATH_KEYS = {
    "log_dir",
    "phase_calibration_file",
    "gnss_sdr_executable",
    "gnss_sdr_repo_dir",
    "gnss_sdr_build_dir",
    "gnss_sdr_install_dir",
    "gnss_sdr_runtime_dir",
    "gnss_sdr_log_dir",
    "gnss_sdr_config_template",
    "gnss_agnss_gps_ephemeris_xml",
    "gnss_agnss_gal_ephemeris_xml",
    "gnss_agnss_gal_utc_model_xml",
    "gnss_agnss_gal_almanac_xml",
}

_TUPLE_KEYS = {
    "channels",
    "rx_antennas_by_channel",
    "rx_lo_sources_by_channel",
    "rx_lo_exports_by_channel",
}


def default_stream_config(
    config_path: Path | str | None = DEFAULT_RUNTIME_CONFIG_PATH,
) -> StreamConfig:
    """Create the runtime config from a JSON profile."""

    if config_path is None:
        raise ValueError("Runtime config path cannot be None; use a JSON profile")

    path = Path(config_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Runtime config not found: {path}")

    return _anchor_runtime_log_paths(load_stream_config_file(path))


def load_stream_config_file(path: Path) -> StreamConfig:
    """Load a complete JSON runtime profile into a StreamConfig instance."""

    payload = _read_runtime_profile(path)
    values = _coerce_runtime_profile(payload)
    return StreamConfig.from_profile_values(values)


def apply_stream_config_file(cfg: StreamConfig, path: Path) -> StreamConfig:
    """Apply a JSON runtime profile to an existing StreamConfig instance."""

    payload = _read_runtime_profile(path)
    valid_fields = _json_profile_fields()

    for key, value in payload.items():
        if str(key).startswith("_"):
            continue

        if key not in valid_fields:
            raise ValueError(f"Unknown runtime config key {key!r} in {path}")

        setattr(cfg, key, _coerce_config_value(key, value))

    return cfg


def _read_runtime_profile(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Runtime config must be a JSON object: {path}")
    return payload


def _json_profile_fields() -> set[str]:
    return set(StreamConfig.__dataclass_fields__) - _JSON_DERIVED_FIELDS


def _coerce_runtime_profile(payload: dict[str, Any]) -> dict[str, Any]:
    valid_fields = _json_profile_fields()
    values: dict[str, Any] = {}

    for key, value in payload.items():
        if str(key).startswith("_"):
            continue
        if key not in valid_fields:
            raise ValueError(f"Unknown runtime config key {key!r}")
        values[key] = _coerce_config_value(key, value)

    missing = sorted(valid_fields - set(values))
    if missing:
        raise ValueError(
            "Runtime config is missing required JSON key(s): " + ", ".join(missing)
        )

    return values


def _anchor_runtime_log_paths(cfg: StreamConfig) -> StreamConfig:
    """Resolve repo-local runtime paths under the repo root when relative."""

    for key in _REPO_ANCHORED_PATH_KEYS:
        value = getattr(cfg, key)
        if isinstance(value, Path) and not value.is_absolute():
            setattr(cfg, key, REPO_ROOT / value)
    return cfg


def _coerce_config_value(key: str, value: Any) -> Any:
    """Coerce JSON values into the Python types expected by StreamConfig."""

    if value is None:
        return None

    if key in _PATH_KEYS:
        return Path(str(value)).expanduser()

    if key in _TUPLE_KEYS and isinstance(value, list):
        return tuple(value)

    return value


__all__ = ["apply_stream_config_file", "default_stream_config", "load_stream_config_file"]
