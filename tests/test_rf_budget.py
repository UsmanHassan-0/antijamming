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
