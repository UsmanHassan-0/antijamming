"""Truth-free PVT spread contracts; no receiver or RF hardware is started."""

from dataclasses import fields
import json
import logging
import math

import pytest

from antijamming import jsonc
from antijamming.config import DEFAULT_RUNTIME_CONFIG_PATH, StreamConfig, load_stream_config_file
from antijamming.gnss import GnssSdrBridge
from antijamming.runtime import BackendRuntime


@pytest.fixture
def bridge(tmp_path):
    cfg = StreamConfig(gnss_sdr_runtime_dir=tmp_path / "receiver", gnss_accuracy_window_points=2)
    return GnssSdrBridge(cfg, {key: logging.getLogger(f"cep.{key}")
                             for key in ("app", "gnss", "errors")})


def point(latitude=0.0, longitude=0.0, altitude=0.0):
    return {"latitude": latitude, "longitude": longitude, "altitude": altitude}


def test_no_truth_position_fields_or_state(bridge):
    assert not any(field.name.startswith("gnss_truth") for field in fields(StreamConfig))
    assert not hasattr(bridge, "_latest_truth_position")
    assert not hasattr(bridge, "_truth_warning_logged")
    result = bridge._build_accuracy_snapshot([point(), point()])
    assert not any("truth" in key or "error_m" in key for key in result)
    assert result["cep_reference"] == "run_mean"
    assert result["cep_metric"] == "horizontal_repeatability"
    assert result["cep_ready"] is True
    assert result["cep50_m"] == result["cep95_m"] == 0.0
    evidence = BackendRuntime._runtime_accuracy_evidence(result)
    for key in ("cep_reference", "cep_metric", "cep_min_points", "cep_sample_count"):
        assert evidence[key] == result[key]


@pytest.mark.parametrize("key", ["gnss_truth_static_lat_deg", "gnss_truth_static_lon_deg",
                                 "gnss_truth_static_alt_m"])
def test_removed_truth_settings_are_not_silently_accepted(tmp_path, key):
    profile = jsonc.load(DEFAULT_RUNTIME_CONFIG_PATH)
    profile[key] = 1.0
    path = tmp_path / "retired.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="[Uu]nknown"):
        load_stream_config_file(path)
    with pytest.raises(TypeError, match="Unexpected"):
        StreamConfig(**{key: 1.0})


def test_empty_and_single_fix_do_not_report_perfect_accuracy(bridge):
    assert bridge._build_accuracy_snapshot([]) == {}
    result = bridge._build_accuracy_snapshot([point(37.0, -122.0)])
    assert result["cep_sample_count"] == 1
    assert result["cep_ready"] is False
    assert "cep50_m" not in result and "cep95_m" not in result


def test_gui_consumes_actual_bridge_spread_and_clears_stale_result(bridge, qtbot):
    from test_gui_status import DummyWorker, _plain_text
    from antijamming.ui.main_window import MainWindow

    window = MainWindow(bridge._cfg, DummyWorker())
    qtbot.addWidget(window)
    warming = bridge._build_accuracy_snapshot([point()])
    # Exercise a serialized producer payload, not an old hand-built truth shape.
    window._set_cep_metrics(json.loads(json.dumps(warming)), True)
    assert _plain_text(window._cep50_label) == "CEP50: warming"
    ready = bridge._build_accuracy_snapshot([point(), point()])
    window._set_cep_metrics(json.loads(json.dumps(ready)), True)
    assert _plain_text(window._cep50_label) == "CEP50: 0.00 m"
    assert "not absolute position accuracy" in window._cep50_label.toolTip()
    window._set_cep_metrics(ready, False)
    assert _plain_text(window._cep50_label) == "CEP50: --"


def test_receiver_log_formats_new_cep_fields(bridge, caplog):
    bridge._latest_accuracy = bridge._build_accuracy_snapshot([point(), point()])
    with caplog.at_level(logging.INFO, logger=bridge._handoff_log.name):
        bridge._log_receiver_event_once("test_cep_event")
    assert "cep_reference=run_mean cep50_m=0.00 cep95_m=0.00" in caplog.text
    assert "truth" not in caplog.text


def test_empirical_radii_recenter_all_fixes_not_just_latest_window(bridge):
    bridge._cfg.gnss_accuracy_window_points = 4
    # WGS84 equator: east = a*sin(longitude). Symmetric positions keep the
    # first-fix tangent orientation at (0,0); expected radii are independent
    # of the production ECEF implementation.
    points = [point(longitude=math.degrees(math.asin(east / 6_378_137.0)))
              for east in (0.0, -2.0, 2.0, -10.0, 10.0)]
    assert bridge._build_accuracy_snapshot(points[:3])["cep_ready"] is False
    result = bridge._build_accuracy_snapshot(points)
    assert result["cep_sample_count"] == 5
    assert result["cep50_m"] == pytest.approx(2.0, abs=1e-8)
    assert result["cep95_m"] == pytest.approx(10.0, abs=1e-8)


@pytest.mark.parametrize("lat,lon", [(0.0, 0.0), (37.0, -122.0), (90.0, 180.0),
                                     (-90.0, -180.0)])
def test_identical_horizontal_positions_have_zero_spread_at_any_site(bridge, lat, lon):
    result = bridge._build_accuracy_snapshot([point(lat, lon, 1.0), point(lat, lon, 999.0)])
    assert result["cep50_m"] == pytest.approx(0.0, abs=1e-8)
    assert result["cep95_m"] == pytest.approx(0.0, abs=1e-8)


def test_dateline_crossing_is_small_not_a_worldwide_jump(bridge):
    result = bridge._build_accuracy_snapshot([point(0.0, 179.99999), point(0.0, -179.99999)])
    assert result["cep50_m"] == pytest.approx(1.1131949, abs=1e-6)
    assert result["cep95_m"] == pytest.approx(1.1131949, abs=1e-6)


@pytest.mark.parametrize("bad", [point(float("nan")), point(longitude=float("inf")),
                                 point(91.0), point(longitude=181.0),
                                 point(altitude=float("nan"))])
def test_invalid_position_is_not_published_as_a_radius(bridge, bad):
    with pytest.raises(ValueError, match="position"):
        bridge._build_accuracy_snapshot([point(), bad])


def test_quantile_is_nearest_rank_not_gaussian_conversion(bridge):
    assert bridge._empirical_nearest_rank([0.0, 2.0, 2.0, 10.0, 10.0], 0.5) == 2.0
    assert bridge._empirical_nearest_rank([0.0, 2.0, 2.0, 10.0, 10.0], 0.95) == 10.0
