"""Runtime profile loading and JSON value coercion."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from antijamming.config.paths import REPO_ROOT
from antijamming.config.schemas.runtime import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    VALID_LCMV_METHODS,
    VALID_LCMV_PRESERVE_MODES,
    VALID_LCMV_TARGET_MODES,
    StreamConfig,
)


_REPO_ANCHORED_PATH_KEYS = (
    "log_dir",
    "gnss_sdr_runtime_dir",
    "gnss_sdr_log_dir",
    "gnss_sdr_repo_dir",
    "gnss_sdr_build_dir",
    "gnss_sdr_install_dir",
    "gnss_sdr_config_template",
    "gnss_agnss_gps_ephemeris_xml",
    "phase_calibration_file",
)

_JSON_DERIVED_FIELDS = {
    # Loaded from phase_calibration_file by app startup; not authored directly in
    # the product runtime JSON because complex numbers are not native JSON values.
    "phase_correction_vector",
    "calibration_correction_metadata",
}

_SAMPLE_RATE_DERIVED_FIELDS = (
    "usrp_rx_bandwidth_hz",
    "min_sample_rate",
)

_OPTIONAL_JSON_DEFAULTS: dict[str, Any] = {
    "calibration_correction_mode": "complex_gain",
    "lcmv_test_null_method": "covariance_lcmv_ideal",
    "lcmv_preserve_constraint_mode": "uniform",
    "lcmv_target_selection_mode": "strongest_music_peak",
    "lcmv_realtime_preserve_window_samples": 40,
    "lcmv_realtime_preserve_min_samples": 20,
    "lcmv_realtime_preserve_max_circular_std_deg": 15.0,
    "lcmv_realtime_preserve_max_step_deg": 30.0,
    "lcmv_realtime_preserve_guard_deg": 20.0,
    "lcmv_realtime_preserve_max_reference_age_s": 2.0,
    "lcmv_jammer_activation_min_input_power_jump_db": 3.0,
    "lcmv_jammer_activation_min_generalized_gain_db": 6.0,
    "lcmv_weight_transition_s": 1.0,
    "lcmv_candidate_methods_enabled": True,
    "lcmv_covariance_diagonal_loading_rel": 0.001,
    "lcmv_covariance_diagonal_loading_abs": 0.0,
    "lcmv_max_weight_norm": 8.0,
    "lcmv_max_white_noise_gain_db": 15.0,
    "lcmv_min_predicted_jammer_suppression_db": 3.0,
    "lcmv_heavy_diagnostics_interval_s": 1.0,
    "one_run_segmentation_enabled": True,
    "healthy_reference_capture_enabled": True,
    "lcmv_auto_arm_after_pvt": True,
    "gnss_sdr_startup_timeout_s": 0.0,
    "gnss_pvt_elevation_mask_deg": 15.0,
    "gnss_shared_u1_phase_compensation_enabled": False,
    "gnss_shared_u1_phase_satellites": (),
    "gnss_shared_u1_phase_transition_s": 1.0,
    "gnss_shared_u1_phase_min_quality_measurements": 3,
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
}

_TUPLE_KEYS = {
    "channels",
    "rx_antennas_by_channel",
    "rx_lo_sources_by_channel",
    "rx_lo_exports_by_channel",
    "gnss_shared_u1_phase_satellites",
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


def _read_runtime_profile(path: Path) -> dict[str, Any]:
    def reject_nonstandard_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid runtime config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Runtime config must be a JSON object: {path}")
    _reject_non_finite_numbers(payload)
    return payload


def _reject_non_finite_numbers(value: object, *, location: str = "root") -> None:
    """Reject non-finite numbers recursively in runtime configuration data."""

    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"Runtime config number at {location} must be finite")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_numbers(item, location=f"{location}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_non_finite_numbers(item, location=f"{location}[{index}]")


def _json_profile_fields() -> set[str]:
    return set(StreamConfig.__dataclass_fields__) - _JSON_DERIVED_FIELDS


def _coerce_runtime_profile(payload: dict[str, Any]) -> dict[str, Any]:
    _reject_authored_sample_rate_followers(payload)
    valid_fields = _json_profile_fields()
    values: dict[str, Any] = {}

    for key, value in payload.items():
        if str(key).startswith("_"):
            continue
        if key not in valid_fields:
            raise ValueError(f"Unknown runtime config key {key!r}")
        values[key] = _coerce_config_value(key, value)

    for key, value in _OPTIONAL_JSON_DEFAULTS.items():
        values.setdefault(key, value)

    _derive_sample_rate_followers(values)

    missing = sorted(valid_fields - set(values))
    if missing:
        raise ValueError(
            "Runtime config is missing required JSON key(s): " + ", ".join(missing)
        )

    return values


def _reject_authored_sample_rate_followers(payload: dict[str, Any]) -> None:
    authored = sorted(set(_SAMPLE_RATE_DERIVED_FIELDS).intersection(payload))
    if authored:
        names = ", ".join(authored)
        raise ValueError(
            f"Runtime sample-rate follower field(s) must not be authored: {names}. "
            "Set sample_rate once; the loader derives the USRP bandwidth, minimum "
            "rate. The GNSS renderer separately "
            "normalizes its signal-specific input-filter edges for that rate."
        )


def _derive_sample_rate_followers(values: dict[str, Any]) -> None:
    if "sample_rate" not in values:
        return
    sample_rate = _positive_finite_sample_rate(values["sample_rate"])
    values["sample_rate"] = sample_rate
    for key in _SAMPLE_RATE_DERIVED_FIELDS:
        values[key] = sample_rate


def _positive_finite_sample_rate(value: Any) -> float:
    try:
        sample_rate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("sample_rate must be a positive finite number") from exc
    if not math.isfinite(sample_rate) or sample_rate <= 0.0:
        raise ValueError("sample_rate must be a positive finite number")
    return sample_rate


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

    if key == "lcmv_test_null_method":
        method = str(value).strip().lower()
        if method not in VALID_LCMV_METHODS:
            allowed = ", ".join(sorted(VALID_LCMV_METHODS))
            raise ValueError(
                f"Invalid lcmv_test_null_method {value!r}; allowed values: {allowed}"
            )
        return method

    if key == "lcmv_preserve_constraint_mode":
        mode = str(value).strip().lower()
        if mode not in VALID_LCMV_PRESERVE_MODES:
            allowed = ", ".join(sorted(VALID_LCMV_PRESERVE_MODES))
            raise ValueError(
                f"Invalid lcmv_preserve_constraint_mode {value!r}; allowed values: {allowed}"
            )
        return mode

    if key == "lcmv_target_selection_mode":
        mode = str(value).strip().lower()
        if mode not in VALID_LCMV_TARGET_MODES:
            allowed = ", ".join(sorted(VALID_LCMV_TARGET_MODES))
            raise ValueError(
                f"Invalid lcmv_target_selection_mode {value!r}; allowed values: {allowed}"
            )
        return mode

    return value


__all__ = [
    "default_stream_config",
    "load_stream_config_file",
]
