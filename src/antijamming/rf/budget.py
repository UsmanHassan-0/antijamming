"""RF budget calculations for the current GNSS anti-jam lab chain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import math


C0_M_PER_S = 299_792_458.0

EXPECTED_EXPERIMENT_FIELDS = (
    "name",
    "rx_chain",
    "center_freq_hz",
    "sample_rate_sps",
    "rx_bandwidth_hz",
    "usrp_rx_gain_db",
    "calibration_file_expected_gain_db",
    "bladeRF_tx_gain_db",
    "bladeRF_tx_power_dbm_est",
    "bladeRF_expected_bearing_deg_min",
    "bladeRF_expected_bearing_deg_max",
    "jammer_attenuation_db",
    "jammer_l1_4mhz_avg_dbm",
    "jammer_peak_dbm",
    "jammer_fullband_avg_dbm",
    "jammer_fullband_low_hz",
    "jammer_fullband_high_hz",
    "jammer_expected_bearing_deg_min",
    "jammer_expected_bearing_deg_max",
    "horizontal_distance_m",
    "height_difference_m",
    "slant_distance_m",
    "bpf_part",
    "bpf_l1_loss_db",
    "pre_bpf_cable_loss_db",
    "lna_part",
    "lna_gain_db",
    "lna_output_p1db_dbm",
    "lna_input_p1db_dbm",
    "dc_block_loss_db",
    "post_lna_cable_loss_db",
    "twinrx_max_rf_input_dbm",
    "bladeRF_sw_gain_db",
    "jammer_power_basis_dbm",
    "jammer_power_basis_description",
    "distance_m",
    "tx_antenna_gain_dbi",
    "rx_antenna_gain_dbi",
    "chain_loss_db",
    "pre_lna_loss_db",
    "post_lna_loss_db",
    "bandwidth_hz",
    "calibration_correction_mode",
    "lcmv_test_null_method",
)


def manifest_from_config(config: object) -> dict[str, object]:
    """Return a JSON-safe experiment manifest with optional config values filled."""

    raw = getattr(config, "experiment", None)
    experiment = dict(raw) if isinstance(raw, Mapping) else {}
    manifest: dict[str, object] = {key: None for key in EXPECTED_EXPERIMENT_FIELDS}
    for key, value in experiment.items():
        manifest[str(key)] = _json_safe(value)

    runtime_defaults = {
        "center_freq_hz": getattr(config, "center_freq_hz", None),
        "sample_rate_sps": getattr(config, "sample_rate", None),
        "rx_bandwidth_hz": getattr(config, "usrp_rx_bandwidth_hz", None),
        "usrp_rx_gain_db": getattr(config, "gain_db", None),
        "bandwidth_hz": getattr(config, "usrp_rx_bandwidth_hz", None),
        "calibration_correction_mode": getattr(
            config, "calibration_correction_mode", None
        ),
        "lcmv_test_null_method": getattr(config, "lcmv_test_null_method", None),
    }
    for key, value in runtime_defaults.items():
        if manifest.get(key) is None:
            manifest[key] = _json_safe(value)
    aliases = {
        "bladeRF_sw_gain_db": manifest.get("bladeRF_tx_gain_db"),
        "jammer_power_basis_dbm": manifest.get("jammer_l1_4mhz_avg_dbm"),
        "jammer_power_basis_description": (
            "jammer L1 4 MHz average power"
            if manifest.get("jammer_l1_4mhz_avg_dbm") is not None
            else None
        ),
        "distance_m": manifest.get("slant_distance_m")
        or manifest.get("horizontal_distance_m"),
    }
    for key, value in aliases.items():
        if manifest.get(key) is None:
            manifest[key] = _json_safe(value)
    pre_lna_loss = _sum_if_known(
        _number(manifest.get("pre_bpf_cable_loss_db")),
        _number(manifest.get("bpf_l1_loss_db")),
    )
    post_lna_loss = _sum_if_known(
        _number(manifest.get("dc_block_loss_db")),
        _number(manifest.get("post_lna_cable_loss_db")),
    )
    if manifest.get("pre_lna_loss_db") is None:
        manifest["pre_lna_loss_db"] = pre_lna_loss
    if manifest.get("post_lna_loss_db") is None:
        manifest["post_lna_loss_db"] = post_lna_loss
    if manifest.get("chain_loss_db") is None:
        manifest["chain_loss_db"] = _sum_if_known(pre_lna_loss, post_lna_loss)
    return manifest


def compute_rf_budget(values: Mapping[str, object]) -> dict[str, object]:
    """Compute expected RF levels for the corrected BPF-before-LNA chain."""

    center_freq_hz = _number(values.get("center_freq_hz"))
    distance_m, distance_source = _distance(values)
    fspl_db = _fspl_db(center_freq_hz, distance_m)

    pre_bpf_cable_loss_db = _number(values.get("pre_bpf_cable_loss_db"))
    bpf_l1_loss_db = _number(values.get("bpf_l1_loss_db"))
    dc_block_loss_db = _number(values.get("dc_block_loss_db"))
    post_lna_cable_loss_db = _number(values.get("post_lna_cable_loss_db"))
    lna_gain_db = _number(values.get("lna_gain_db"))
    lna_input_p1db_dbm = _number(values.get("lna_input_p1db_dbm"))
    twinrx_max_rf_input_dbm = _number(values.get("twinrx_max_rf_input_dbm"))

    pre_lna_loss_db = _sum_if_known(pre_bpf_cable_loss_db, bpf_l1_loss_db)
    post_lna_loss_db = _sum_if_known(dc_block_loss_db, post_lna_cable_loss_db)
    chain_gain_to_usrp_rf_db = _chain_gain(
        lna_gain_db,
        pre_lna_loss_db,
        post_lna_loss_db,
    )

    budget: dict[str, object] = {
        "fspl_db": fspl_db,
        "distance_used_m": distance_m,
        "distance_source": distance_source,
        "pre_lna_loss_db": pre_lna_loss_db,
        "post_lna_loss_db": post_lna_loss_db,
        "chain_gain_to_usrp_rf_db": chain_gain_to_usrp_rf_db,
    }
    budget.update(
        _source_budget(
            prefix="jammer_avg",
            tx_dbm=_number(values.get("jammer_l1_4mhz_avg_dbm")),
            attenuation_db=_number(values.get("jammer_attenuation_db")),
            fspl_db=fspl_db,
            pre_bpf_cable_loss_db=pre_bpf_cable_loss_db,
            pre_lna_loss_db=pre_lna_loss_db,
            post_lna_loss_db=post_lna_loss_db,
            lna_gain_db=lna_gain_db,
            lna_input_p1db_dbm=lna_input_p1db_dbm,
            twinrx_max_rf_input_dbm=twinrx_max_rf_input_dbm,
        )
    )
    budget.update(
        _source_budget(
            prefix="jammer_peak",
            tx_dbm=_number(values.get("jammer_peak_dbm")),
            attenuation_db=_number(values.get("jammer_attenuation_db")),
            fspl_db=fspl_db,
            pre_bpf_cable_loss_db=pre_bpf_cable_loss_db,
            pre_lna_loss_db=pre_lna_loss_db,
            post_lna_loss_db=post_lna_loss_db,
            lna_gain_db=lna_gain_db,
            lna_input_p1db_dbm=lna_input_p1db_dbm,
            twinrx_max_rf_input_dbm=twinrx_max_rf_input_dbm,
        )
    )
    budget.update(
        _source_budget(
            prefix="bladeRF",
            tx_dbm=_number(values.get("bladeRF_tx_power_dbm_est")),
            attenuation_db=0.0,
            fspl_db=fspl_db,
            pre_bpf_cable_loss_db=pre_bpf_cable_loss_db,
            pre_lna_loss_db=pre_lna_loss_db,
            post_lna_loss_db=post_lna_loss_db,
            lna_gain_db=lna_gain_db,
            lna_input_p1db_dbm=lna_input_p1db_dbm,
            twinrx_max_rf_input_dbm=twinrx_max_rf_input_dbm,
        )
    )
    return budget


def _source_budget(
    *,
    prefix: str,
    tx_dbm: float | None,
    attenuation_db: float | None,
    fspl_db: float | None,
    pre_bpf_cable_loss_db: float | None,
    pre_lna_loss_db: float | None,
    post_lna_loss_db: float | None,
    lna_gain_db: float | None,
    lna_input_p1db_dbm: float | None,
    twinrx_max_rf_input_dbm: float | None,
) -> dict[str, object]:
    at_antenna = _subtract_if_known(tx_dbm, attenuation_db, fspl_db)
    bpf_input = _subtract_if_known(at_antenna, pre_bpf_cable_loss_db)
    lna_input = _subtract_if_known(at_antenna, pre_lna_loss_db)
    lna_output = _add_if_known(lna_input, lna_gain_db)
    usrp_rf_input = _subtract_if_known(lna_output, post_lna_loss_db)
    return {
        f"{prefix}_tx_dbm": tx_dbm,
        f"{prefix}_at_antenna_dbm": at_antenna,
        f"{prefix}_bpf_input_dbm": bpf_input,
        f"{prefix}_lna_input_dbm": lna_input,
        f"{prefix}_lna_output_ideal_dbm": lna_output,
        f"{prefix}_usrp_rf_input_ideal_dbm": usrp_rf_input,
        f"{prefix}_lna_p1db_margin_db": _subtract_if_known(
            lna_input,
            lna_input_p1db_dbm,
        ),
        f"{prefix}_twinrx_margin_db": _subtract_if_known(
            usrp_rf_input,
            twinrx_max_rf_input_dbm,
        ),
    }


def _distance(values: Mapping[str, object]) -> tuple[float | None, str]:
    slant = _number(values.get("slant_distance_m"))
    if slant is not None and slant > 0.0:
        return slant, "slant_distance_m"
    horizontal = _number(values.get("horizontal_distance_m"))
    if horizontal is not None and horizontal > 0.0:
        return horizontal, "horizontal_distance_m_fallback"
    return None, "unknown"


def _fspl_db(center_freq_hz: float | None, distance_m: float | None) -> float | None:
    if center_freq_hz is None or distance_m is None:
        return None
    if center_freq_hz <= 0.0 or distance_m <= 0.0:
        return None
    return 20.0 * math.log10(4.0 * math.pi * distance_m * center_freq_hz / C0_M_PER_S)


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _sum_if_known(*values: float | None) -> float | None:
    if any(value is None for value in values):
        return None
    return float(sum(float(value) for value in values if value is not None))


def _add_if_known(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left + right)


def _subtract_if_known(left: float | None, *rights: float | None) -> float | None:
    if left is None or any(value is None for value in rights):
        return None
    result = float(left)
    for value in rights:
        if value is not None:
            result -= float(value)
    return result


def _chain_gain(
    lna_gain_db: float | None,
    pre_lna_loss_db: float | None,
    post_lna_loss_db: float | None,
) -> float | None:
    if lna_gain_db is None or pre_lna_loss_db is None or post_lna_loss_db is None:
        return None
    return float(lna_gain_db - pre_lna_loss_db - post_lna_loss_db)


def _json_safe(value: object) -> object:
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "EXPECTED_EXPERIMENT_FIELDS",
    "compute_rf_budget",
    "manifest_from_config",
]
