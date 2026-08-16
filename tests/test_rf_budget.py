from __future__ import annotations

import pytest

from antijamming.rf import compute_rf_budget


def _manifest() -> dict[str, object]:
    return {
        "center_freq_hz": 1_575_420_000.0,
        "horizontal_distance_m": 4.0,
        "slant_distance_m": None,
        "jammer_attenuation_db": 60.0,
        "jammer_l1_4mhz_avg_dbm": 9.51,
        "jammer_peak_dbm": 21.4,
        "bladeRF_tx_power_dbm_est": -32.63,
        "pre_bpf_cable_loss_db": 0.55,
        "bpf_l1_loss_db": 2.0,
        "lna_gain_db": 50.0,
        "lna_input_p1db_dbm": -30.2,
        "dc_block_loss_db": 0.5,
        "post_lna_cable_loss_db": 0.55,
        "twinrx_max_rf_input_dbm": 10.0,
        "jammer_tx_antenna_gain_dbi": 0.0,
        "bladeRF_tx_antenna_gain_dbi": 0.0,
        "rx_antenna_gain_dbi": 0.0,
        "jammer_tx_cable_loss_db": 0.0,
        "bladeRF_tx_cable_loss_db": 0.0,
    }


def test_rf_budget_matches_60db_jammer_at_4m() -> None:
    budget = compute_rf_budget(_manifest())

    assert budget["fspl_db"] == pytest.approx(48.43, abs=0.01)
    assert budget["distance_used_m"] == pytest.approx(4.0)
    assert budget["distance_source"] == "horizontal_distance_m_fallback"
    assert budget["pre_lna_loss_db"] == pytest.approx(2.55)
    assert budget["post_lna_loss_db"] == pytest.approx(1.05)
    assert budget["chain_gain_to_usrp_rf_db"] == pytest.approx(46.40)
    assert budget["jammer_avg_lna_input_dbm"] == pytest.approx(-101.47, abs=0.02)
    assert budget["jammer_avg_usrp_rf_input_ideal_dbm"] == pytest.approx(-52.52, abs=0.02)
    assert budget["jammer_peak_lna_input_dbm"] == pytest.approx(-89.58, abs=0.02)
    assert budget["jammer_peak_usrp_rf_input_ideal_dbm"] == pytest.approx(-40.63, abs=0.02)


def test_rf_budget_matches_bladerf_estimate_at_4m() -> None:
    budget = compute_rf_budget(_manifest())

    assert budget["bladeRF_lna_input_dbm"] == pytest.approx(-83.61, abs=0.02)
    assert budget["bladeRF_usrp_rf_input_ideal_dbm"] == pytest.approx(-34.66, abs=0.02)


def test_rf_budget_tolerates_missing_optional_values() -> None:
    budget = compute_rf_budget({})

    assert budget["fspl_db"] is None
    assert budget["jammer_avg_lna_input_dbm"] is None
    assert budget["bladeRF_usrp_rf_input_ideal_dbm"] is None


def test_rf_budget_uses_source_specific_distances_gains_and_safety_flags() -> None:
    manifest = {
        "center_freq_hz": 1_575_420_000.0,
        "jammer_distance_m": 4.2672,
        "bladeRF_distance_m": 3.4798,
        "jammer_attenuation_db": 30.0,
        "jammer_l1_4mhz_avg_dbm": 9.51,
        "jammer_peak_dbm": 21.4,
        "bladeRF_tx_power_dbm_est": -42.2691,
        "jammer_tx_antenna_gain_dbi": 2.0,
        "bladeRF_tx_antenna_gain_dbi": 2.0,
        "rx_antenna_gain_dbi": 5.0,
        "jammer_tx_cable_loss_db": 0.0,
        "bladeRF_tx_cable_loss_db": 0.0,
        "pre_bpf_cable_loss_db": 1.0,
        "bpf_l1_loss_db": 2.0,
        "lna_gain_db": 50.0,
        "lna_input_p1db_dbm": -30.2,
        "dc_block_loss_db": 0.5,
        "post_lna_cable_loss_db": 1.0,
        "twinrx_max_rf_input_dbm": 10.0,
    }

    budget = compute_rf_budget(manifest)

    assert budget["jammer_fspl_db"] == pytest.approx(48.999, abs=0.002)
    assert budget["bladeRF_fspl_db"] == pytest.approx(47.227, abs=0.002)
    assert budget["jammer_avg_lna_input_dbm"] == pytest.approx(-65.489, abs=0.002)
    assert budget["jammer_avg_usrp_rf_input_ideal_dbm"] == pytest.approx(
        -16.989, abs=0.002
    )
    assert budget["jammer_peak_usrp_rf_input_ideal_dbm"] == pytest.approx(
        -5.099, abs=0.002
    )
    assert budget["bladeRF_lna_input_dbm"] == pytest.approx(-85.496, abs=0.002)
    assert budget["bladeRF_usrp_rf_input_ideal_dbm"] == pytest.approx(
        -36.996, abs=0.002
    )
    assert budget["jammer_peak_lna_input_above_p1db"] is False
    assert budget["jammer_peak_usrp_above_twinrx_max"] is False
    assert budget["jammer_peak_lna_p1db_headroom_db"] == pytest.approx(23.399, abs=0.002)
    assert budget["jammer_peak_twinrx_headroom_db"] == pytest.approx(15.099, abs=0.002)
