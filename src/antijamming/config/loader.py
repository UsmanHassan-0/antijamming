"""Runtime profile loading and JSON value coercion."""

from __future__ import annotations

import json
import math
import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from antijamming.config.paths import REPO_ROOT
from antijamming.config.schemas.runtime import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    StreamConfig,
)
from antijamming.dsp.phase import VALID_CALIBRATION_CORRECTION_MODES


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
}

_STREAM_CONFIG_TYPES = get_type_hints(StreamConfig)
_NONE_TYPE = type(None)


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

    _derive_sample_rate_followers(values)

    missing = sorted(valid_fields - set(values))
    if missing:
        raise ValueError(
            "Runtime config is missing required JSON key(s): " + ", ".join(missing)
        )

    _validate_runtime_values(values)
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

    if key in _PATH_KEYS:
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"Runtime config {key!r} must be a path string or null")
            value = Path(value).expanduser()

    if key in _TUPLE_KEYS:
        if not isinstance(value, list):
            raise ValueError(f"Runtime config {key!r} must be a JSON array")
        value = tuple(value)

    annotation = _STREAM_CONFIG_TYPES[key]
    return _coerce_annotated_value(key, value, annotation)


def _coerce_annotated_value(key: str, value: Any, annotation: object) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in {Union, types.UnionType}:
        if value is None and _NONE_TYPE in args:
            return None
        failures: list[ValueError] = []
        for option in args:
            if option is _NONE_TYPE:
                continue
            try:
                return _coerce_annotated_value(key, value, option)
            except ValueError as exc:
                failures.append(exc)
        raise ValueError(
            f"Runtime config {key!r} has value {value!r} incompatible with {annotation}"
        ) from (failures[-1] if failures else None)

    if value is None:
        raise ValueError(f"Runtime config {key!r} must not be null")

    if origin is tuple:
        if not isinstance(value, tuple):
            raise ValueError(f"Runtime config {key!r} must be a JSON array")
        if len(args) == 2 and args[1] is Ellipsis:
            item_types = tuple(args[0] for _ in value)
        else:
            if len(value) != len(args):
                raise ValueError(
                    f"Runtime config {key!r} must contain exactly {len(args)} items"
                )
            item_types = args
        return tuple(
            _coerce_annotated_value(f"{key}[{index}]", item, item_type)
            for index, (item, item_type) in enumerate(
                zip(value, item_types, strict=True)
            )
        )

    if annotation is bool:
        if type(value) is not bool:
            raise ValueError(f"Runtime config {key!r} must be a JSON boolean")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ValueError(f"Runtime config {key!r} must be a JSON integer")
        return value
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Runtime config {key!r} must be a JSON number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Runtime config {key!r} must be finite")
        return number
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError(f"Runtime config {key!r} must be a JSON string")
        return value
    if annotation is Path:
        if not isinstance(value, Path):
            raise ValueError(f"Runtime config {key!r} must be a path string")
        return value

    if not isinstance(value, annotation):
        raise ValueError(
            f"Runtime config {key!r} has value {value!r} incompatible with {annotation}"
        )
    return value


def _validate_runtime_values(values: dict[str, Any]) -> None:
    """Reject internally inconsistent values before any hardware is touched."""

    def require_positive(*keys: str) -> None:
        for key in keys:
            if float(values[key]) <= 0.0:
                raise ValueError(f"Runtime config {key!r} must be greater than zero")

    def require_nonnegative(*keys: str) -> None:
        for key in keys:
            if float(values[key]) < 0.0:
                raise ValueError(f"Runtime config {key!r} must be nonnegative")

    require_positive(
        "recv_buff_size",
        "num_recv_frames",
        "sample_rate",
        "center_freq_hz",
        "usrp_rx_bandwidth_hz",
        "array_design_freq_hz",
        "array_spacing_m",
        "lo_lock_timeout_s",
        "samples_per_chunk",
        "process_every_n_chunks",
        "ui_update_interval_s",
        "dsp_update_interval_s",
        "prn_chart_update_interval_s",
        "skyplot_update_interval_s",
        "ui_points",
        "lcmv_condition_number_limit",
        "lcmv_realtime_preserve_window_samples",
        "lcmv_realtime_preserve_min_samples",
        "lcmv_realtime_preserve_max_reference_age_s",
        "lcmv_max_weight_norm",
        "gnss_shared_u1_phase_min_quality_measurements",
        "gnss_1c_channel_count",
        "gnss_channels_in_acquisition",
        "gnss_accuracy_window_points",
        "gnss_monitor_decimation_factor",
        "gnss_tracking_monitor_decimation_factor",
        "gnss_pvt_nmea_rate_ms",
        "gnss_acquisition_coherent_integration_ms",
        "gnss_acquisition_doppler_max_hz",
        "gnss_acquisition_doppler_step_hz",
        "gnss_acquisition_max_dwells",
        "gnss_tracking_1c_pll_bw_hz",
        "gnss_tracking_1c_dll_bw_hz",
        "gnss_tracking_1c_pll_filter_order",
        "gnss_tracking_1c_dll_filter_order",
        "gnss_tracking_1c_early_late_space_chips",
        "gnss_tracking_1c_early_late_space_narrow_chips",
        "gnss_tracking_1c_pll_bw_narrow_hz",
        "gnss_tracking_1c_dll_bw_narrow_hz",
        "gnss_tracking_1c_extend_correlation_symbols",
        "gnss_tracking_1c_fll_bw_hz",
        "gnss_tracking_1c_bit_synchronization_time_limit_s",
        "gnss_feed_queue_maxsize",
        "rx_health_log_interval_chunks",
        "max_overflow_streak",
        "max_total_overflow",
        "doa_points",
    )
    require_nonnegative(
        "recv_frame_size",
        "send_frame_size",
        "startup_grace_s",
        "lcmv_realtime_preserve_max_circular_std_deg",
        "lcmv_realtime_preserve_max_step_deg",
        "lcmv_realtime_preserve_guard_deg",
        "lcmv_jammer_activation_min_input_power_jump_db",
        "lcmv_jammer_activation_min_generalized_gain_db",
        "lcmv_min_predicted_jammer_suppression_db",
        "lcmv_weight_transition_s",
        "lcmv_covariance_diagonal_loading_rel",
        "lcmv_covariance_diagonal_loading_abs",
        "lcmv_heavy_diagnostics_interval_s",
        "gnss_shared_u1_phase_transition_s",
        "gnss_tracking_1c_pull_in_time_s",
        "doa_log_interval_s",
    )

    channels = tuple(values["channels"])
    if channels != (0, 1, 2, 3):
        raise ValueError(
            "Runtime config 'channels' must be exactly [0, 1, 2, 3] in calibrated "
            "stream order"
        )
    for key in (
        "rx_antennas_by_channel",
        "rx_lo_sources_by_channel",
        "rx_lo_exports_by_channel",
    ):
        if len(values[key]) != len(channels):
            raise ValueError(f"Runtime config {key!r} must match the channel count")

    valid_antennas = {"RX1", "RX2"}
    antennas = tuple(str(value).strip().upper() for value in values["rx_antennas_by_channel"])
    if any(value not in valid_antennas for value in antennas):
        raise ValueError(
            "Runtime config 'rx_antennas_by_channel' entries must be RX1 or RX2"
        )
    values["rx_antennas_by_channel"] = antennas

    valid_lo_sources = {"internal", "external", "companion", "reimport"}
    lo_sources = tuple(
        str(value).strip().lower() for value in values["rx_lo_sources_by_channel"]
    )
    if any(value not in valid_lo_sources for value in lo_sources):
        raise ValueError(
            "Runtime config 'rx_lo_sources_by_channel' contains an unsupported "
            "TwinRX LO source"
        )
    values["rx_lo_sources_by_channel"] = lo_sources

    if int(values["doa_points"]) < 2:
        raise ValueError("Runtime config 'doa_points' must be at least 2")
    if float(values["doa_max_deg"]) <= float(values["doa_min_deg"]):
        raise ValueError("Runtime config DoA maximum must be greater than its minimum")
    if not 1 <= int(values["expected_sources"]) < len(channels):
        raise ValueError("Runtime config 'expected_sources' must be in 1..channels-1")
    if int(values["lcmv_realtime_preserve_min_samples"]) > int(
        values["lcmv_realtime_preserve_window_samples"]
    ):
        raise ValueError(
            "Runtime config LCMV preserve minimum samples must not exceed its window"
        )
    if not 0.0 < float(values["gnss_acquisition_pfa"]) < 1.0:
        raise ValueError("Runtime config 'gnss_acquisition_pfa' must be between 0 and 1")
    if int(values["gnss_pvt_nmea_rate_ms"]) < 100:
        raise ValueError("Runtime config 'gnss_pvt_nmea_rate_ms' must be at least 100")
    if not -90.0 <= float(values["gnss_pvt_elevation_mask_deg"]) <= 90.0:
        raise ValueError(
            "Runtime config 'gnss_pvt_elevation_mask_deg' must be in [-90, 90]"
        )
    if int(values["gnss_tracking_1c_pll_filter_order"]) < 2:
        raise ValueError(
            "Runtime config 'gnss_tracking_1c_pll_filter_order' must be at least 2"
        )
    if int(values["gnss_channels_in_acquisition"]) > int(
        values["gnss_1c_channel_count"]
    ):
        raise ValueError(
            "Runtime config acquisition concurrency must not exceed GNSS channel count"
        )
    if not 0.0 < float(values["rx_clipping_component_threshold"]) <= 1.0:
        raise ValueError(
            "Runtime config 'rx_clipping_component_threshold' must be in (0, 1]"
        )
    if not 0.0 <= float(values["rx_clipping_fraction_threshold"]) <= 1.0:
        raise ValueError(
            "Runtime config 'rx_clipping_fraction_threshold' must be in [0, 1]"
        )

    latitude = values["gnss_truth_static_lat_deg"]
    longitude = values["gnss_truth_static_lon_deg"]
    if latitude is not None and not -90.0 <= float(latitude) <= 90.0:
        raise ValueError("Runtime config truth latitude must be in [-90, 90]")
    if longitude is not None and not -180.0 <= float(longitude) <= 180.0:
        raise ValueError("Runtime config truth longitude must be in [-180, 180]")

    ports = (
        "gnss_pvt_monitor_udp_port",
        "gnss_monitor_udp_port",
        "gnss_tracking_monitor_udp_port",
    )
    parsed_ports: list[int] = []
    for key in ports:
        try:
            port = int(values[key])
        except ValueError as exc:
            raise ValueError(f"Runtime config {key!r} must contain a UDP port") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"Runtime config {key!r} must be in 1..65535")
        parsed_ports.append(port)
    if len(set(parsed_ports)) != len(parsed_ports):
        raise ValueError("Runtime config GNSS monitor UDP ports must be distinct")

    if values["gnss_sdr_sample_type"] != "gr_complex":
        raise ValueError(
            "Runtime config 'gnss_sdr_sample_type' must be 'gr_complex' for complex64 FIFO IQ"
        )
    if values["calibration_correction_mode"] not in VALID_CALIBRATION_CORRECTION_MODES:
        allowed = ", ".join(sorted(VALID_CALIBRATION_CORRECTION_MODES))
        raise ValueError(
            "Runtime config 'calibration_correction_mode' must be one of " + allowed
        )
    for enabled_key, protobuf_key in (
        ("gnss_pvt_monitor_enable", "gnss_pvt_monitor_enable_protobuf"),
        ("gnss_monitor_enable", "gnss_monitor_enable_protobuf"),
        ("gnss_tracking_monitor_enable", "gnss_tracking_monitor_enable_protobuf"),
    ):
        if values[enabled_key] and not values[protobuf_key]:
            raise ValueError(
                f"Runtime config {protobuf_key!r} must be true when {enabled_key!r} is enabled"
            )


__all__ = [
    "default_stream_config",
    "load_stream_config_file",
]
