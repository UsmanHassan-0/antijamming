from __future__ import annotations

import io
import logging
import re

import numpy as np
import pytest
import pyqtgraph as pg
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QFontMetrics, QGuiApplication
from PyQt6.QtWidgets import QLabel, QCheckBox, QScrollArea, QSizePolicy, QSpinBox

from antijamming.config import StreamConfig
from antijamming.ui.main_window import MainWindow
from antijamming.ui.specs import SKYPLOT_MIN_SIZE
from antijamming.ui.widgets.prn_monitor import (
    PRN_BAR_GAP,
    PRN_BAR_OUTER_MARGIN,
    PRN_MIN_VISUAL_RANGE_SPAN,
    PRN_SINGLE_BAR_WIDTH,
    PocketPrnMonitor,
    _bar_position_for_index,
)
from antijamming.ui.widgets.skyplot import (
    SkyplotMonitor,
    _skyplot_marker_font_size_for_side,
    _skyplot_marker_size_for_side,
    _skyplot_view_limit_for_side,
    _skyplot_view_range_for_side,
    _skyplot_xy,
)
from antijamming.ui.widgets.skyplot.monitor import _skyplot_static_label_positions
from antijamming.ui.theme import (
    ALERT,
    BEIDOU_TRACKING,
    BEIDOU_TRACKING_FIX,
    BG_APP,
    BG_PANEL,
    FG_TEXT,
    GLONASS_TRACKING,
    GPS_TRACKING,
    GPS_TRACKING_FIX,
    INPUT_BORDER,
    INFO,
    WHITE,
    operator_tabs_style,
)


class DummyWorker(QObject):
    data_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.stopped = False
        self.stop_reasons: list[str] = []
        self.expected_sources = 1
        self.lcmv_test_enabled = False
        self.rf_events: list[tuple[str, dict[str, object]]] = []

    def start(self) -> None:
        self.started = True
        self.status.emit("USRP stream started")

    def stop(self, reason: str = "normal stop") -> None:
        self.stopped = True
        self.stop_reasons.append(reason)
        self.status.emit("USRP stream stopped")

    def set_expected_sources(self, count: int) -> None:
        self.expected_sources = int(count)
        self.status.emit(f"MUSIC sources: {int(count)}")

    def set_lcmv_test_enabled(self, enabled: bool) -> None:
        self.lcmv_test_enabled = bool(enabled)
        self.status.emit(f"LCMV Test Nulling: {'ON' if enabled else 'OFF'}")

    def mark_rf_event(self, event: str, **kwargs: object) -> None:
        self.rf_events.append((str(event), dict(kwargs)))


class SlowFinishWorker(DummyWorker):
    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.running = False

    def start(self) -> None:
        self.started = True
        self.running = True
        self.status.emit("USRP stream started")

    def stop(self, reason: str = "normal stop") -> None:
        self.stopped = True
        self.stop_reasons.append(reason)
        self.status.emit("USRP stream stopped")

    def isRunning(self) -> bool:
        return self.running

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


def _plain_text(label) -> str:
    return re.sub(r"<[^>]+>", "", label.text()).strip()


def _is_descendant(child, parent) -> bool:
    widget = child
    while widget is not None:
        if widget is parent:
            return True
        widget = widget.parentWidget()
    return False


def test_skyplot_coordinate_mapping_and_static_labels(qtbot) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)

    assert skyplot._plot.getPlotItem().getAxis("left").isVisible() is False
    assert skyplot._plot.getPlotItem().getAxis("bottom").isVisible() is False
    assert bool(skyplot._plot.getPlotItem().getViewBox().state["aspectLocked"]) is True
    assert _skyplot_xy(0.0, 90.0) == pytest.approx((0.0, 0.0))
    assert _skyplot_xy(0.0, 0.0) == pytest.approx((0.0, 1.0))
    east_x, east_y = _skyplot_xy(90.0, 0.0)
    assert east_x == pytest.approx(1.0)
    assert east_y == pytest.approx(0.0, abs=1e-12)
    assert skyplot._ring_radii == pytest.approx((1.0, 2.0 / 3.0, 1.0 / 3.0))
    label_positions = _skyplot_static_label_positions(skyplot._current_plot_side())
    assert label_positions["30°"][1] == pytest.approx(2.0 / 3.0)
    assert label_positions["60°"][1] == pytest.approx(1.0 / 3.0)
    assert label_positions["30°"][0] > 0.0
    assert label_positions["60°"][0] > 0.0
    x_min, x_max, y_min, y_max = skyplot._view_range
    assert x_min <= -1.15
    assert x_max >= 1.15
    assert y_min <= -1.15
    assert y_max >= 1.15
    assert all(
        x_min < x < x_max and y_min < y < y_max
        for x, y in label_positions.values()
    )
    assert {"N", "E", "S", "W"}.issubset(label_positions)
    assert len(skyplot._band_items) == 3
    assert all(band.zValue() < skyplot._ring_items[0].zValue() for band in skyplot._band_items)
    assert len(skyplot._ring_items) == 3
    assert len(skyplot._spoke_items) == 4
    assert all(ring.zValue() == pytest.approx(0.0) for ring in skyplot._ring_items)
    for radius, ring in zip(skyplot._ring_radii, skyplot._ring_items, strict=True):
        rect = ring.rect()
        assert rect.center().x() == pytest.approx(0.0)
        assert rect.center().y() == pytest.approx(0.0)
        assert rect.width() / 2.0 == pytest.approx(radius)
        assert rect.height() / 2.0 == pytest.approx(radius)


def test_skyplot_view_range_tightens_as_plot_gets_larger(qtbot) -> None:
    del qtbot
    compact = _skyplot_view_range_for_side(112)
    large = _skyplot_view_range_for_side(640)
    compact_limit = _skyplot_view_limit_for_side(112)
    large_limit = _skyplot_view_limit_for_side(640)

    assert large_limit < compact_limit
    assert large == pytest.approx((-large_limit, large_limit, -large_limit, large_limit))
    assert compact == pytest.approx(
        (-compact_limit, compact_limit, -compact_limit, compact_limit)
    )
    assert compact_limit > 1.0
    assert large_limit > 1.0


def test_skyplot_refresh_layout_uses_window_scaled_size(qtbot) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)
    skyplot.resize(640, 640)
    skyplot.refresh_layout()

    assert skyplot.plot_widget.width() == 640
    assert skyplot.plot_widget.height() == 640
    limit = _skyplot_view_limit_for_side(640)
    assert skyplot._view_range == pytest.approx((-limit, limit, -limit, limit))


@pytest.mark.parametrize(
    "side_px",
    (
        SKYPLOT_MIN_SIZE,
        SKYPLOT_MIN_SIZE * 2,
        SKYPLOT_MIN_SIZE * 3,
    ),
)
def test_skyplot_static_labels_fit_inside_viewbox(qtbot, side_px: int) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)
    skyplot.set_plot_side(side_px)
    skyplot.update_snapshot(
        [
            {"prn": 5, "state": "tracking", "az_deg": 0.0, "el_deg": 0.0},
        ]
    )
    skyplot.show()
    qtbot.waitUntil(
        lambda: all(
            skyplot.plot_widget.getPlotItem()
            .getViewBox()
            .sceneBoundingRect()
            .contains(item.mapRectToScene(item.boundingRect()))
            for item in skyplot._static_label_items
        ),
        timeout=1000,
    )

    view_box = skyplot.plot_widget.getPlotItem().getViewBox()
    scene_rect = view_box.sceneBoundingRect()
    limit = _skyplot_view_limit_for_side(side_px)
    assert view_box.viewRange()[0] == pytest.approx([-limit, limit])
    assert all(
        scene_rect.contains(item.mapRectToScene(item.boundingRect()))
        for item in skyplot._static_label_items
    )


def test_skyplot_marker_size_and_text_follow_plot_size(qtbot) -> None:
    compact_side = SKYPLOT_MIN_SIZE
    large_side = SKYPLOT_MIN_SIZE * 3

    assert _skyplot_marker_size_for_side(compact_side) < _skyplot_marker_size_for_side(large_side)
    assert _skyplot_marker_font_size_for_side(compact_side) < _skyplot_marker_font_size_for_side(large_side)
    compact_horizon_px = compact_side / _skyplot_view_limit_for_side(compact_side)
    large_horizon_px = large_side / _skyplot_view_limit_for_side(large_side)
    compact_marker_ratio = _skyplot_marker_size_for_side(compact_side) / compact_horizon_px
    large_marker_ratio = _skyplot_marker_size_for_side(large_side) / large_horizon_px
    assert compact_marker_ratio >= large_marker_ratio
    assert large_marker_ratio >= 0.11
    for side in (compact_side, large_side):
        font = QFont()
        font.setPointSize(_skyplot_marker_font_size_for_side(side))
        font.setBold(True)
        metrics = QFontMetrics(font)
        assert _skyplot_marker_size_for_side(side) > metrics.horizontalAdvance("G05")
        assert _skyplot_marker_size_for_side(side) > metrics.height()

    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)
    skyplot.set_plot_side(compact_side)
    skyplot.update_snapshot(
        [
            {"prn": 5, "state": "tracking", "az_deg": 45.0, "el_deg": 50.0},
        ]
    )

    marker = skyplot._marker_items[0]
    label = skyplot._marker_label_items[0]
    assert marker.opts["size"] == pytest.approx(_skyplot_marker_size_for_side(compact_side))
    assert label.textItem.font().pointSize() == _skyplot_marker_font_size_for_side(compact_side)

    skyplot.set_plot_side(large_side)

    assert marker.opts["size"] == pytest.approx(_skyplot_marker_size_for_side(large_side))
    assert label.textItem.font().pointSize() == _skyplot_marker_font_size_for_side(large_side)


def test_skyplot_skips_missing_geometry_and_tracks_unplaced_prns(qtbot) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)

    skyplot.update_snapshot(
        [
            {"prn": 5, "state": "tracking", "az_deg": 45.0, "el_deg": 50.0},
            {"prn": 7, "state": "visible", "az_deg": 180.0, "el_deg": 30.0},
            {"prn": 9, "state": "tracking", "az_deg": 270.0, "el_deg": 35.0, "used_in_fix": True},
            {"prn": 12, "state": "tracking"},
            {"prn": 14, "state": "tracking", "az_deg": 90.0},
        ],
    )

    assert skyplot._plotted_prns == [5, 7, 9]
    assert skyplot._marker_items
    assert all(item.zValue() > skyplot._ring_items[0].zValue() for item in skyplot._marker_items)
    assert all(
        item.zValue() > marker.zValue()
        for item, marker in zip(skyplot._marker_label_items, skyplot._marker_items, strict=True)
    )
    tracking_marker, visible_marker, fix_marker = skyplot._marker_items
    assert tracking_marker.opts["brush"].color().name().upper() == BG_PANEL.upper()
    assert tracking_marker.opts["pen"].color().name().upper() == GPS_TRACKING.upper()
    assert tracking_marker.opts["brush"].color().name().upper() != GPS_TRACKING_FIX.upper()
    assert visible_marker.opts["brush"].color().name().upper() == BG_PANEL.upper()
    assert visible_marker.opts["pen"].color().name().upper() == INPUT_BORDER.upper()
    assert fix_marker.opts["brush"].color().name().upper() == GPS_TRACKING_FIX.upper()
    assert fix_marker.opts["pen"].color().name().upper() == GPS_TRACKING_FIX.upper()
    assert [item.toPlainText() for item in skyplot._marker_label_items] == ["G05", "G07", "G09"]
    assert skyplot._marker_label_items[0].color.name().upper() == FG_TEXT.upper()
    assert skyplot._marker_label_items[2].color.name().upper() == WHITE.upper()


def test_skyplot_uses_supported_constellation_prefixes(qtbot) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)

    skyplot.update_snapshot(
        [
            {"prn": 12, "constellation": "gps", "state": "tracking", "az_deg": 20.0, "el_deg": 45.0},
            {
                "prn": 7,
                "constellation": "beidou",
                "state": "tracking",
                "az_deg": 60.0,
                "el_deg": 40.0,
                "used_in_fix": True,
            },
            {"prn": 3, "constellation": "glonass", "state": "tracking", "az_deg": 80.0, "el_deg": 35.0},
            {"prn": 1, "constellation": "sbas", "state": "tracking", "az_deg": 100.0, "el_deg": 30.0},
        ]
    )

    assert [item.toPlainText() for item in skyplot._marker_label_items] == ["G12", "C07", "R03"]
    assert skyplot._marker_items[0].opts["pen"].color().name().upper() == GPS_TRACKING.upper()
    assert skyplot._marker_items[1].opts["brush"].color().name().upper() == BEIDOU_TRACKING_FIX.upper()
    assert skyplot._marker_items[2].opts["pen"].color().name().upper() == GLONASS_TRACKING.upper()


def test_prn_chart_labels_bars_with_cno_and_axis_with_satellites(qtbot) -> None:
    monitor = PocketPrnMonitor()
    qtbot.addWidget(monitor)

    monitor.update_snapshot(
        [
            {
                "prn": 12,
                "constellation": "glonass",
                "state": "tracking",
                "cno_db_hz": 36.7,
                "cno_stable": True,
            },
            {
                "prn": 5,
                "state": "tracking",
                "cno_db_hz": 42.4,
                "cno_stable": True,
            },
            {
                "prn": 7,
                "system": "beidou",
                "state": "tracking",
                "cno_db_hz": 38.1,
                "cno_stable": True,
                "used_in_fix": True,
            },
        ]
    )

    assert monitor._displayed_prns == [5, 7, 12]
    assert monitor._x_tick_labels == ["G05", "C07", "R12"]
    assert [label.toPlainText() for label in monitor._label_items] == [
        "42.4",
        "38.1",
        "36.7",
    ]
    assert monitor._bar_colors == [GPS_TRACKING, BEIDOU_TRACKING_FIX, GLONASS_TRACKING]
    assert monitor._plot.getPlotItem().getAxis("bottom").labelText == ""
    assert [(label.anchor.x(), label.anchor.y()) for label in monitor._label_items] == [
        (0.5, 1.25),
        (0.5, 1.25),
        (0.5, 1.25),
    ]
    assert [label.color.name().upper() for label in monitor._label_items] == [
        FG_TEXT.upper(),
        FG_TEXT.upper(),
        FG_TEXT.upper(),
    ]


def test_prn_chart_does_not_render_unstable_tracking_placeholder(qtbot) -> None:
    monitor = PocketPrnMonitor()
    qtbot.addWidget(monitor)

    monitor.update_snapshot(
        [
            {
                "prn": 9,
                "state": "tracking",
                "observable_cno_db_hz": 43.25,
                "observable_valid_pseudorange": True,
            }
        ]
    )

    assert monitor._displayed_prns == []
    assert monitor._unstable_tracking_prns == [9]
    assert monitor._bar_heights == []
    assert [label.toPlainText() for label in monitor._label_items] == []


def test_gui_single_run_button_toggles_start_stop(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert window._run_btn.text() == "▶ Start"
    assert not hasattr(window, "_mode_toggle_btn")
    assert not hasattr(window, "_algorithm_status_label")
    assert not hasattr(window, "_beamforming_status_label")
    assert not hasattr(window, "_gnss_feed_status_label")
    assert not hasattr(window, "_tabs")
    assert not hasattr(window, "_main_scroll")
    scroll_areas = window.centralWidget().findChildren(QScrollArea)
    assert [scroll.objectName() for scroll in scroll_areas] == ["antijamScrollArea"]
    assert scroll_areas[0].verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll_areas[0].horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert _plain_text(window._output_path_label) == "Output: Uniform Array IQ -> GNSS-SDR"
    assert _plain_text(window._system_health_label) == "System health: Idle"

    window._run_btn.click()
    assert worker.started is True
    assert window._run_btn.text() == "■ Stop"
    assert _plain_text(window._system_health_label) == "System health: OK"

    window._run_btn.click()
    assert worker.stopped is True
    assert worker.stop_reasons[-1] == "normal stop"
    assert window._run_btn.text() == "▶ Start"


def test_gui_main_window_uses_maximized_screen_geometry(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window.maximize_to_available_screen()

    assert bool(window.windowState() & Qt.WindowState.WindowMaximized)
    screen = window.screen() or QGuiApplication.primaryScreen()
    assert screen is not None
    assert window.maximumSize() == screen.availableGeometry().size()


def test_gui_waits_for_worker_finish_before_restart_and_shows_stream_phase(qtbot) -> None:
    worker = SlowFinishWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._run_btn.click()
    assert window._run_btn.text() == "■ Stop"
    assert _plain_text(window._status_chip) == "Stream: Streaming"

    window._run_btn.click()
    assert worker.stopped is True
    assert window._run_btn.text() == "Stopping..."
    assert window._run_btn.isEnabled() is False
    assert _plain_text(window._status_chip) == "Stream: Finalizing stop"

    worker.finish()
    assert window._run_btn.text() == "▶ Start"
    assert window._run_btn.isEnabled() is True
    assert _plain_text(window._status_chip) == "Stream: Stopped"


def test_gui_coalesces_live_metrics_to_latest_refresh(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()
    assert window._prn_monitor is not None

    window._on_status("USRP stream started")
    first = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 4,
                    "state": "tracking",
                    "cno_db_hz": 31.0,
                    "cno_stable": True,
                }
            ],
            "sky_prns": [],
        }
    }
    latest = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 8,
                    "state": "tracking",
                    "cno_db_hz": 39.0,
                    "cno_stable": True,
                }
            ],
            "sky_prns": [],
        }
    }

    window._on_data_ready(first)
    window._on_data_ready(latest)

    assert window._prn_monitor._displayed_prns == []
    assert window._latest_pending_metrics is latest

    window._flush_pending_metrics()

    assert window._latest_pending_metrics is None
    assert window._prn_monitor._displayed_prns == [8]
    assert window._prn_monitor._bar_heights == [39.0]
    assert window._metrics_received_count == 2
    assert window._metrics_applied_count == 1
    assert window._metrics_coalesced_drop_count == 1


def test_gui_rf_markers_include_current_attenuation_and_bladerf_gain(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    assert window._jammer_on_button.isEnabled() is False
    assert window._jammer_attenuation_spin.isEnabled() is True
    window._on_status("USRP stream started")
    assert worker.rf_events[:2] == [
        (
            "attenuation_db",
            {
                "attenuation_db": 50.0,
                "notes": (
                    "Automatically recorded GUI configured attenuation; "
                    "this is not a measured RF power"
                ),
            },
        ),
        (
            "bladeRF_gain_db",
            {
                "bladeRF_gain_db": 50.0,
                "notes": (
                    "Automatically recorded GUI selected bladeRF software gain; "
                    "the GUI does not query or command an external bladeRF process"
                ),
            },
        ),
    ]
    window._jammer_attenuation_spin.setValue(50.0)
    window._bladerf_gain_spin.setValue(50.0)
    window._jammer_on_button.click()

    assert worker.rf_events[-1] == (
        "jammer_on",
        {"attenuation_db": 50.0, "bladeRF_gain_db": 50.0},
    )
    assert "jammer ON" in _plain_text(window._rf_event_status_label)

    window._jammer_attenuation_spin.setValue(40.0)
    window._record_rf_setting("attenuation_db")
    assert worker.rf_events[-1] == ("attenuation_db", {"attenuation_db": 40.0})


def test_gui_metrics_timer_clamps_aggressive_ui_interval(qtbot) -> None:
    worker = DummyWorker()
    cfg = StreamConfig(ui_update_interval_s=0.05)
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    assert window._metrics_timer.interval() == 100


def test_gnss_operator_widgets_have_independent_display_throttles(qtbot, monkeypatch) -> None:
    worker = DummyWorker()
    cfg = StreamConfig(
        ui_update_interval_s=0.05,
        prn_chart_update_interval_s=0.5,
        skyplot_update_interval_s=0.2,
    )
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window._stream_running = True

    now = 10.0
    monkeypatch.setattr(
        "antijamming.ui.main_window.time.monotonic",
        lambda: now,
    )

    def metrics(prn: int, cno: float, az_deg: float, receiver_time_s: int) -> dict[str, object]:
        return {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": False,
                "receiver_time_s": receiver_time_s,
                "prns": [
                    {
                        "prn": prn,
                        "state": "tracking",
                        "cno_db_hz": cno,
                        "cno_stable": True,
                        "used_in_fix": False,
                    }
                ],
                "sky_prns": [
                    {
                        "prn": prn,
                        "state": "tracking",
                        "az_deg": az_deg,
                        "el_deg": 45.0,
                        "used_in_fix": False,
                    }
                ],
            }
        }

    window._refresh_gnss_monitors(metrics(5, 41.0, 20.0, 101))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert _plain_text(window._receiver_time_label) == "Time: 00:01:41"

    now = 10.1
    window._refresh_gnss_monitors(metrics(5, 35.0, 80.0, 102))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert _plain_text(window._receiver_time_label) == "Time: 00:01:41"

    now = 10.25
    window._refresh_gnss_monitors(metrics(9, 35.0, 80.0, 103))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [9]
    assert _plain_text(window._receiver_time_label) == "Time: 00:01:43"

    now = 10.55
    window._refresh_gnss_monitors(metrics(5, 35.0, 80.0, 104))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [35.0]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert _plain_text(window._receiver_time_label) == "Time: 00:01:44"


def test_gui_logs_ui_health_heartbeat(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()
    window._on_status("USRP stream started")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("antijamming.ui")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        window._latest_pending_metrics = None
        window._last_ui_heartbeat_log_s = 0.0
        window._maybe_log_ui_heartbeat(10.0)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    text = stream.getvalue()
    assert "ui heartbeat:" in text
    assert "received=0" in text
    assert "prn_bars=0" in text


def test_gui_close_requests_worker_cleanup(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window.closeEvent(QCloseEvent())

    assert worker.stopped is True
    assert worker.stop_reasons[-1] == "GUI close"
    assert window._metrics_timer.isActive() is False
    assert window._startup_screen_timer.isActive() is False


def test_gui_shows_output_path_and_system_feed_info(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert cfg.gnss_sdr_enable is True
    assert not hasattr(window, "_gnss_source_combo")
    assert not hasattr(window, "_gnss_source_label")
    assert not hasattr(window, "_doa_angle_combo")
    assert not hasattr(window, "_jammer_detection_checkbox")
    assert _plain_text(window._output_path_label) == "Output: Uniform Array IQ -> GNSS-SDR"
    assert not _is_descendant(window._output_path_label, window._main_view)
    assert not _is_descendant(window._output_path_label, window._receiver_card)
    assert "GNSS feed:" not in _plain_text(window._system_info_label)
    assert "Input feed:" not in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: Uniform Array IQ" in (
        _plain_text(window._system_info_label)
    )

    window._run_btn.click()
    assert worker.started is True
    assert cfg.gnss_sdr_enable is True


def test_gui_has_fixed_uniform_gnss_handoff(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert not hasattr(window, "_gnss_source_combo")
    assert not hasattr(window, "_gnss_source_label")
    assert "MUSIC sources:" in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: Uniform Array IQ" in _plain_text(
        window._system_info_label
    )

    window._refresh_system_info()
    assert "MUSIC sources:" in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: Uniform Array IQ" in _plain_text(
        window._system_info_label
    )


def test_gui_expected_sources_control_updates_runtime(qtbot) -> None:
    cfg = StreamConfig(expected_sources=1)
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert isinstance(window._expected_sources_spin, QSpinBox)
    assert window._expected_sources_spin.minimum() == 1
    assert window._expected_sources_spin.maximum() == len(cfg.channels) - 1
    assert _is_descendant(window._expected_sources_control, window._antijam_tab)
    assert not _is_descendant(window._expected_sources_control, window._receiver_card)

    window._expected_sources_spin.setValue(3)

    assert cfg.expected_sources == 3
    assert worker.expected_sources == 3


def test_gui_lcmv_test_toggle_defaults_off_and_updates_runtime(qtbot) -> None:
    cfg = StreamConfig(lcmv_test_enabled=False)
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert isinstance(window._lcmv_test_checkbox, QCheckBox)
    assert window._lcmv_test_checkbox.isChecked() is False
    assert cfg.lcmv_test_enabled is False
    assert _is_descendant(window._lcmv_test_control, window._antijam_tab)
    assert _plain_text(window._lcmv_test_status_label) == (
        "LCMV Test Nulling: OFF: Uniform beamformer"
    )
    assert _plain_text(window._lcmv_null_bearing_label) == (
        "Null bearing / MUSIC peak: --"
    )

    window._lcmv_test_checkbox.setChecked(True)

    assert cfg.lcmv_test_enabled is True
    assert worker.lcmv_test_enabled is True
    assert "LCMV Test IQ" in _plain_text(window._system_info_label)
    assert _plain_text(window._lcmv_test_status_label) == (
        "LCMV Test Nulling: FALLBACK: Uniform fallback, waiting for stable bladerf reference"
    )


def test_gui_lcmv_status_distinguishes_armed_and_transitioning(qtbot) -> None:
    window = MainWindow(StreamConfig(), DummyWorker())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._refresh_lcmv_test_status(
        {
            "lcmv_test": {
                "enabled": True,
                "mode": "fallback",
                "fallback_reason": "waiting for jammer evidence",
                "music_bearing_deg": 120.0,
                "spatial_vector_diagnostics": {
                    "lcmv_jammer_activation_armed": True,
                    "lcmv_jammer_detected_latched": False,
                },
            }
        }
    )
    assert "ARMED: Uniform output" in _plain_text(window._lcmv_test_status_label)
    assert "not applied (armed uniform)" in _plain_text(
        window._lcmv_null_bearing_label
    )

    window._refresh_lcmv_test_status(
        {
            "lcmv_test": {
                "enabled": True,
                "mode": "on",
                "active_lcmv_method": "covariance_lcmv_ideal",
                "weight_transition_active": True,
                "weight_transition_progress": 0.5,
                "spatial_vector_diagnostics": {
                    "lcmv_jammer_detected_latched": True,
                },
            }
        }
    )
    assert "ACTIVATING: Smooth weight transition 50%" in _plain_text(
        window._lcmv_test_status_label
    )


def test_gui_labels_jammer_excess_suppression_separately_from_total_output(qtbot) -> None:
    window = MainWindow(StreamConfig(), DummyWorker())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._refresh_lcmv_test_status(
        {
            "lcmv_test": {
                "enabled": True,
                "mode": "on",
                "null_bearing_deg": 145.0,
                "weight_transition_active": True,
                "spatial_vector_diagnostics": {
                    "lcmv_jammer_detected_latched": True,
                    "jammer_only_suppression_estimate_available": True,
                    "jammer_only_suppression_db": 12.25,
                    "jammer_only_target_suppression_db": 31.75,
                },
                "output_metrics": {
                    "measured_output_reduction_vs_uniform_db": 7.5,
                    "measured_output_reduction_vs_raw_avg_channel_db": 4.0,
                },
            }
        }
    )

    text = _plain_text(window._lcmv_null_bearing_label)
    assert "jammer-excess suppression 12.2 dB applied / 31.8 dB target" in text
    assert "total out reduction 7.5 dB (wanted included)" in text


def test_gui_idle_state_hides_redundant_detail_rows(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert not hasattr(window, "_runtime_label")
    assert not hasattr(window, "_array_summary_label")
    assert window._gnss_chip.text() == ""
    assert window._gnss_chip.isHidden() is True
    assert _is_descendant(window._operator_title_label, window._operator_header)
    assert _is_descendant(window._run_btn, window._operator_header)
    assert not hasattr(window, "_jammer_detection_checkbox")
    assert not _is_descendant(window._run_btn, window._operator_tabs)
    assert window._operator_tabs.tabText(0) == "Receiver"
    assert window._operator_tabs.tabText(1) == "Anti-Jam"
    assert _is_descendant(window._receiver_card, window._receiver_tab)
    assert not hasattr(window, "_receiver_status_card")
    assert not _is_descendant(window._doa_status_label, window._receiver_card)
    assert _is_descendant(window._doa_status_label, window._antijam_tab)
    assert _is_descendant(window._position_status_label, window._receiver_card)
    assert _is_descendant(window._skyplot_monitor, window._receiver_card)
    receiver_status_layout = window._receiver_status_section.layout()
    assert receiver_status_layout.itemAt(0).spacerItem() is not None
    assert (
        receiver_status_layout.itemAt(receiver_status_layout.count() - 1).spacerItem()
        is not None
    )
    assert not _is_descendant(window._receiver_summary_label, window._receiver_card)
    assert not hasattr(window, "_stream_card")
    assert not hasattr(window, "_skyplot_card")
    assert not _is_descendant(window._stream_summary_label, window._main_view)
    assert _plain_text(window._fix_chip) == "NO FIX"
    assert not hasattr(window, "_accuracy_chip")
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._latitude_label) == "lat/long: -- / --"
    assert _plain_text(window._altitude_label) == "altitude: --"
    assert _plain_text(window._receiver_time_label) == "Time: --"
    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: -- / -- / -- / --"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 0"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert not hasattr(window, "_position_error_label")
    assert not hasattr(window, "_enu_label")
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"
    assert "Fix:" not in _plain_text(window._receiver_summary_label)
    operator_text = "\n".join(_plain_text(label) for label in window._main_view.findChildren(QLabel))
    all_visible_text = "\n".join(_plain_text(label) for label in window.findChildren(QLabel))
    assert "Receiver Overview" not in operator_text
    assert "Satellite Sky View" in operator_text
    assert "Legend:" not in operator_text
    assert "Receiver Status" not in operator_text
    assert "Anti-Jam Status" not in operator_text
    assert "Anti-Jam" in operator_text
    assert not any(_plain_text(label) == "System" for label in window._main_view.findChildren(QLabel))
    assert "PVT fix:" in operator_text
    assert "PVT status:" not in operator_text
    assert "lat/long:" in operator_text
    assert "lat/long/alt:" not in operator_text
    assert "altitude:" in operator_text
    assert "Latitude:" not in operator_text
    assert "Longitude:" not in operator_text
    assert "Height:" not in operator_text
    assert "Time:" in operator_text
    assert "HDOP/VDOP/PDOP/GDOP:" in operator_text
    assert "DOP HDOP/VDOP/PDOP/GDOP:" not in operator_text
    assert "Satellites tracked:" not in operator_text
    assert "Satellites used for PVT:" not in operator_text
    assert "PVT accuracy:" not in operator_text
    assert "CEP50:" in operator_text
    assert "CEP95:" in operator_text
    assert "position 3D:" not in operator_text
    assert "UTM east/north:" not in operator_text
    assert "UTM east/north/up:" not in operator_text
    assert "East: -- / North: -- / Up: --" not in operator_text
    assert "Local East:" not in operator_text
    assert "Local North:" not in operator_text
    assert "Local Up:" not in operator_text
    assert "horizontal 2D:" not in operator_text
    assert "3D displacement:" not in operator_text
    assert "C/N0 used for PVT:" not in operator_text
    assert "DoA:" in operator_text
    assert "MUSIC sources:" in operator_text
    assert "Detector confidence:" not in operator_text
    assert "Jammer status:" not in operator_text
    assert "Direction candidate:" not in operator_text
    assert "Power rise:" not in operator_text
    assert "Raw IQ power:" not in operator_text
    assert "RX clipping:" in operator_text
    assert "IQ peak:" in operator_text
    assert "IQ RMS:" in operator_text
    assert "Near full scale:" in operator_text
    assert "LCMV Test Nulling:" in operator_text
    assert "Nulling strongest MUSIC peak" not in operator_text
    assert "System health:" in operator_text
    assert "Reason:" not in operator_text
    assert "PVT 6" not in operator_text
    assert "Acc" not in operator_text
    assert "State: Not detected" not in operator_text
    assert "3D | Stable" not in operator_text
    assert "GNSS feed: configured profile" not in all_visible_text
    assert "GNSS feed:" not in all_visible_text
    assert not hasattr(window, "_accuracy_summary_label")
    assert _is_descendant(window._status_chip, window._operator_header)
    assert ALERT in window._position_status_label.text()
    assert INFO in window._doa_status_label.text()


def test_receiver_tab_uses_one_content_height_overview_and_expanding_prn_card(
    qtbot,
) -> None:
    window = MainWindow(StreamConfig(), DummyWorker())  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()
    window._apply_responsive_layout(1440, 1080)

    assert f"background:{BG_APP}" in window._operator_tabs.styleSheet()
    assert f"background:{BG_APP}" in window._receiver_tab.styleSheet()
    assert f"background:{BG_APP}" in window._antijam_tab.styleSheet()
    assert (
        window._operator_tabs.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Ignored
    )
    assert (
        window._receiver_card.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Maximum
    )
    assert (
        window._prn_card.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Expanding
    )
    assert (
        window._antijam_tab.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Expanding
    )
    assert (
        window._algorithm_plots_container.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Expanding
    )
    assert window._receiver_card.maximumHeight() < 16777215
    assert window._prn_card.maximumHeight() == 16777215
    receiver_layout = window._receiver_tab.layout()
    assert receiver_layout is not None
    prn_index = receiver_layout.indexOf(window._prn_card)
    assert prn_index >= 0
    assert receiver_layout.stretch(prn_index) == 1


def test_operator_tabs_style_centers_tabs_and_removes_separator_line() -> None:
    style = operator_tabs_style()

    assert "QTabBar#operatorNavTabs{" in style
    assert "border-top:1px solid" not in style
    assert f"background:{BG_PANEL}" in style
    assert f"color:{INFO}" in style


def test_gui_clears_fix_and_cep_without_pvt(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "pvt_observation_count": 10,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "three_d_error_m": 1.2,
                    "valid_sats": 10,
                }
            }
        }
    )
    assert _plain_text(window._fix_chip) == "FIX"
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": False,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "three_d_error_m": 1.2,
                },
            }
        }
    )

    assert _plain_text(window._fix_chip) == "NO FIX"
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"


def test_gui_keeps_gnss_sdr_runtime_status_in_main_view(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "tracking_count": 2,
                "tracking_prns": [5, 9],
                "acquired_count": 0,
                "pvt_output_seen": True,
                "pvt_current": True,
                "receiver_time_s": 125,
                "sky_geometry_count": 3,
                "used_in_fix_count": 2,
                "udp_pvt_packets": 5,
                "udp_observables_packets": 7,
                "udp_tracking_packets": 11,
                "udp_parse_errors": 0,
                "avg_tracking_cno_db_hz": 38.25,
                "prns": [
                    {"channel": 0, "prn": 5, "state": "tracking"},
                    {"channel": 1, "prn": 9, "state": "tracking"},
                ],
            }
        }
    )
    assert window._gnss_chip.text() == ""
    assert window._gnss_chip.isHidden() is True
    system_text = _plain_text(window._system_info_label)
    assert "GNSS-SDR receiver time: 00:02:05" in system_text
    assert "GNSS-SDR tracking PRNs: G05, G09" in system_text
    assert "GNSS-SDR tracking channels: ch0:G05, ch1:G09" in system_text
    assert "PVT output: current" in system_text
    assert "Sky geometry: 3" in system_text
    assert "Used for PVT: 2" in system_text
    assert "Tracking C/N0 average: 38.2 dB-Hz" in system_text
    assert f"GNSS-SDR receiver log: {cfg.gnss_sdr_log_dir / 'receiver.log'}" in (
        system_text
    )
    assert f"GNSS-SDR console log: {cfg.gnss_sdr_runtime_dir / 'console.log'}" in (
        system_text
    )
    assert f"PVT UDP monitor: 127.0.0.1:{cfg.gnss_pvt_monitor_udp_port}" in system_text
    assert f"Observables UDP monitor: 127.0.0.1:{cfg.gnss_monitor_udp_port}" in system_text
    assert (
        f"Tracking UDP monitor: 127.0.0.1:{cfg.gnss_tracking_monitor_udp_port}"
        in system_text
    )
    assert "UDP packets: pvt=5, observables=7, tracking=11, errors=0" in system_text

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "tracking_count": 2,
                "acquired_count": 0,
                "pvt_output_seen": False,
            }
        }
    )
    assert window._gnss_chip.text() == ""
    assert "PVT output: not seen" in _plain_text(window._system_info_label)
    assert window._gnss_chip.isHidden() is True

    window._on_data_ready({"gnss_snapshot": {}})
    assert not hasattr(window, "_mode_toggle_btn")
    assert not hasattr(window, "_gnss_feed_status_label")
    assert cfg.gnss_sdr_enable is True
    assert window._gnss_chip.text() == ""
    assert "GNSS-SDR receiver time: --" in _plain_text(window._system_info_label)
    assert window._gnss_chip.isHidden() is True


def test_realtime_gui_shows_prn_monitor_and_skyplot(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert window._prn_monitor is not None
    assert window._skyplot_monitor is not None
    assert window._prn_monitor.isHidden() is False
    assert window._skyplot_monitor.isHidden() is False
    assert _is_descendant(window._skyplot_monitor, window._main_view)
    assert _is_descendant(window._prn_monitor, window._main_view)
    assert not hasattr(window, "_doa_plot")
    assert not hasattr(window, "_doa_curve")
    assert not hasattr(window, "_doa_marker")
    assert not hasattr(window, "_doa_raw_plot")
    assert not hasattr(window, "_doa_raw_curve")
    assert not hasattr(window, "_doa_raw_marker")
    assert not hasattr(window, "_doa_raw_tile")
    assert not hasattr(window, "_doa_polar_plot")
    assert not hasattr(window, "_doa_polar_curve")
    assert not hasattr(window, "_doa_polar_marker")
    assert not hasattr(window, "_doa_polar_tile")
    assert _is_descendant(window._doa_polar_db_plot, window._main_view)
    assert not hasattr(window, "_doa_compass_plot")
    assert not hasattr(window, "_rf_spectrum_plot")
    assert not hasattr(window, "_gnss_quality_plot")
    assert _is_descendant(window._skyplot_monitor, window._receiver_card)
    assert not _is_descendant(window._doa_status_label, window._receiver_card)
    assert _is_descendant(window._doa_status_label, window._antijam_tab)
    assert _is_descendant(window._position_status_label, window._receiver_card)
    main_layout = window._main_view.layout()
    assert main_layout is not None
    assert main_layout.indexOf(window._operator_header) >= 0
    assert main_layout.indexOf(window._operator_tabs) >= 0
    assert main_layout.indexOf(window._operator_header) < main_layout.indexOf(window._operator_tabs)
    assert _is_descendant(window._algorithm_plots_container, window._antijam_tab)
    assert _is_descendant(window._antijam_status_card, window._antijam_tab)
    assert _is_descendant(window._lcmv_response_container, window._antijam_tab)
    assert _is_descendant(window._lcmv_response_container, window._algorithm_plots_container)
    assert _is_descendant(window._lcmv_response_plot, window._lcmv_response_container)
    assert not hasattr(window, "_lcmv_rms_plot")
    assert not hasattr(window, "_lcmv_rms_bars")
    assert _is_descendant(window._doa_polar_db_plot, window._antijam_tab)
    polar_text_items = [
        item.toPlainText()
        for item in window._doa_polar_db_plot.getPlotItem().items
        if isinstance(item, pg.TextItem)
    ]
    for label in ("0°", "90°", "180°", "270°", "330°"):
        assert label in polar_text_items
    for label in ("0.25", "0.5", "0.75"):
        assert label not in polar_text_items
    window._operator_tabs.setCurrentIndex(0)
    assert _is_descendant(window._prn_card, window._receiver_tab)
    assert not _is_descendant(window._prn_card, window._antijam_tab)
    assert not _is_descendant(window._prn_card, window._receiver_card)

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "cno_db_hz": 42.4,
                        "cno_smoothed_db_hz": 42.0,
                        "cno_sample_count": 20,
                        "cno_stdev_db": 0.4,
                        "cno_peak_to_peak_db": 1.0,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                    },
                    {"prn": 7, "state": "acquired", "cno_db_hz": 31.5},
                    {
                        "prn": 9,
                        "state": "tracking",
                        "cno_db_hz": 36.4,
                        "cno_smoothed_db_hz": 36.0,
                        "cno_sample_count": 20,
                        "cno_stdev_db": 0.5,
                        "cno_peak_to_peak_db": 1.2,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                        "used_in_fix": True,
                    },
                    {
                        "prn": 12,
                        "state": "tracking",
                        "cno_db_hz": 34.0,
                        "cno_smoothed_db_hz": 34.0,
                        "cno_sample_count": 2,
                        "cno_stdev_db": 0.2,
                        "cno_peak_to_peak_db": 0.4,
                        "cno_stable": False,
                        "cno_unstable_reason": "too_few_samples",
                    },
                    {
                        "prn": 13,
                        "state": "tracking",
                        "cno_db_hz": 34.5,
                        "cno_smoothed_db_hz": 34.0,
                        "cno_sample_count": 20,
                        "cno_stdev_db": 1.2,
                        "cno_peak_to_peak_db": 2.0,
                        "cno_stable": False,
                        "cno_unstable_reason": "high_variance",
                    },
                    {
                        "prn": 14,
                        "state": "tracking",
                        "cno_db_hz": 39.0,
                        "cno_smoothed_db_hz": 39.0,
                        "cno_sample_count": 20,
                        "cno_stdev_db": 0.2,
                        "cno_peak_to_peak_db": 0.5,
                        "cno_stable": False,
                        "cno_unstable_reason": "awaiting_nav",
                        "cno_history_stable": True,
                    },
                ],
                "sky_prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "az_deg": 45.0,
                        "el_deg": 50.0,
                    },
                    {"prn": 7, "state": "acquired", "az_deg": 180.0, "el_deg": 20.0},
                    {
                        "prn": 13,
                        "state": "tracking",
                        "az_deg": 20.0,
                        "el_deg": 45.0,
                    },
                    {
                        "prn": 9,
                        "state": "tracking",
                        "az_deg": 270.0,
                        "el_deg": 35.0,
                        "used_in_fix": True,
                    },
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == [5, 9, 12, 13, 14]
    assert window._prn_monitor._pending_tracking_prns == [12, 14]
    assert window._prn_monitor._pending_tracking_reasons == {
        12: "too_few_samples",
        14: "awaiting_nav",
    }
    assert window._prn_monitor._unstable_tracking_prns == [13]
    assert window._prn_monitor._unstable_tracking_reasons == {13: "high_variance"}
    assert not hasattr(window._prn_monitor, "_tracking_status_label")
    assert window._prn_monitor._bar_positions == pytest.approx(
        [
            _bar_position_for_index(0),
            _bar_position_for_index(1),
            _bar_position_for_index(2),
            _bar_position_for_index(3),
            _bar_position_for_index(4),
        ]
    )
    assert window._prn_monitor._bar_heights == [42.4, 36.4, 34.0, 34.5, 39.0]
    assert window._prn_monitor._bar_colors == [
        GPS_TRACKING,
        GPS_TRACKING_FIX,
        GPS_TRACKING,
        GPS_TRACKING,
        GPS_TRACKING,
    ]
    assert _plain_text(window._stable_prns_chip) == "2"
    assert _plain_text(window._used_in_pvt_chip) == "1"
    assert _plain_text(window._position_status_label) == "PVT fix: DEGRADED"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 5"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"
    assert window._prn_monitor._bar_width == pytest.approx(PRN_SINGLE_BAR_WIDTH)
    assert PRN_BAR_OUTER_MARGIN == pytest.approx(PRN_BAR_GAP)
    assert (
        window._prn_monitor._bar_positions[1] - window._prn_monitor._bar_positions[0]
    ) == pytest.approx(PRN_SINGLE_BAR_WIDTH + PRN_BAR_GAP)
    assert (
        window._prn_monitor._x_range[1] - window._prn_monitor._x_range[0]
    ) == pytest.approx(PRN_MIN_VISUAL_RANGE_SPAN)
    assert (
        window._prn_monitor._bar_positions[0]
        - (window._prn_monitor._bar_width / 2.0)
        - window._prn_monitor._x_range[0]
    ) == pytest.approx(
        window._prn_monitor._x_range[1]
        - window._prn_monitor._bar_positions[-1]
        - (window._prn_monitor._bar_width / 2.0)
    )
    assert window._skyplot_monitor._plotted_prns == [5, 9]
    assert (
        window._skyplot_monitor._marker_items[0].opts["brush"].color().name().upper()
        == BG_PANEL.upper()
    )
    assert (
        window._skyplot_monitor._marker_items[0].opts["pen"].color().name().upper()
        == GPS_TRACKING.upper()
    )
    assert (
        window._skyplot_monitor._marker_items[1].opts["brush"].color().name().upper()
        == GPS_TRACKING_FIX.upper()
    )
    assert (
        window._skyplot_monitor._marker_items[1].opts["pen"].color().name().upper()
        == GPS_TRACKING_FIX.upper()
    )
    assert window._prn_monitor._bar_item.zValue() == pytest.approx(10.0)
    assert [
        brush.color().alpha()
        for brush in window._prn_monitor._bar_item.opts["brushes"]
    ] == [255, 255, 255, 255, 255]
    assert all(
        brush.style() == Qt.BrushStyle.SolidPattern
        for brush in window._prn_monitor._bar_item.opts["brushes"]
    )
    assert [
        pen.color().alpha()
        for pen in window._prn_monitor._bar_item.opts["pens"]
    ] == [255, 255, 255, 255, 255]
    assert window._prn_monitor._x_tick_labels == ["G05", "G09", "G12", "G13", "G14"]
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == [
        "42.4",
        "36.4",
        "34.0",
        "34.5",
        "39.0",
    ]
    assert all(
        label.zValue() > window._prn_monitor._bar_item.zValue()
        for label in window._prn_monitor._label_items
    )
    assert len(window._skyplot_monitor._plot.getPlotItem().items) > 8

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [],
                "sky_prns": [
                    {
                        "prn": 11,
                        "state": "visible",
                        "az_deg": 10.0,
                        "el_deg": 30.0,
                        "snr_db_hz": 37.0,
                    },
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._bar_positions == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []
    assert window._skyplot_monitor._plotted_prns == []

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {"prn": 1, "state": "searched"},
                    {"prn": 2, "state": "assigned"},
                    {"prn": 3, "state": "lost"},
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._bar_positions == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {"prn": 14, "state": "acquired"},
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._bar_positions == []
    assert window._prn_monitor._bar_heights == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {"prn": 2, "state": "searched"},
                    {"prn": 3, "state": "assigned"},
                    {
                        "prn": 8,
                        "state": "tracking",
                        "cno_smoothed_db_hz": 33.0,
                        "cno_sample_count": 20,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                    },
                    {"prn": 9, "state": "acquired"},
                ],
                "sky_prns": [
                    {
                        "prn": 12,
                        "state": "visible",
                        "az_deg": 25.0,
                        "el_deg": 45.0,
                    },
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._pending_tracking_prns == []
    assert window._prn_monitor._pending_tracking_reasons == {}
    assert window._prn_monitor._bar_positions == []
    assert window._prn_monitor._bar_heights == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []
    assert window._skyplot_monitor._plotted_prns == []

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "acquired_prns": [14],
                "tracking_prns": [21],
            }
        }
    )

    # Summary-only PRN lists are not chart entries; the chart uses per-PRN
    # GNSS-SDR tracking records so it can label and size each bar honestly.
    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._bar_positions == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {"prn": 8, "state": "assigned"},
                    {
                        "prn": 9,
                        "state": "tracking",
                        "cno_db_hz": 35.6,
                        "cno_smoothed_db_hz": 35.0,
                        "cno_sample_count": 20,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                    },
                    {"prn": 10, "state": "lost"},
                    {"prn": 11, "state": "acquired", "used_in_fix": True},
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == [9]
    assert window._prn_monitor._bar_heights == [35.6]
    assert window._prn_monitor._bar_colors == [
        GPS_TRACKING,
    ]

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {
                        "prn": prn,
                        "state": "tracking",
                        "cno_db_hz": 31.0 + prn,
                        "cno_smoothed_db_hz": 30.0 + prn,
                        "cno_sample_count": 20,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                    }
                    for prn in range(1, 11)
                ],
            }
        }
    )
    assert window._prn_monitor._displayed_prns == list(range(1, 11))
    assert window._prn_monitor._bar_positions == pytest.approx(
        [_bar_position_for_index(index) for index in range(10)]
    )
    assert window._prn_monitor._bar_width == pytest.approx(PRN_SINGLE_BAR_WIDTH)
    assert (
        window._prn_monitor._x_range[1] - window._prn_monitor._x_range[0]
    ) == pytest.approx(PRN_MIN_VISUAL_RANGE_SPAN)

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "prns": [
                    {
                        "prn": 4,
                        "state": "tracking",
                        "cno_db_hz": 37.8,
                        "cno_smoothed_db_hz": 37.0,
                        "cno_sample_count": 20,
                        "cno_stable_window_count": 3,
                        "cno_stable": True,
                    },
                ],
            }
        }
    )
    assert window._prn_monitor._displayed_prns == [4]
    assert window._prn_monitor._bar_positions == pytest.approx([_bar_position_for_index(0)])
    assert window._prn_monitor._bar_width == pytest.approx(PRN_SINGLE_BAR_WIDTH)
    assert (
        window._prn_monitor._x_range[1] - window._prn_monitor._x_range[0]
    ) == pytest.approx(PRN_MIN_VISUAL_RANGE_SPAN)
    assert window._prn_monitor._bar_positions[0] == pytest.approx(
        sum(window._prn_monitor._x_range) / 2.0
    )
    assert window._prn_monitor._bar_heights == [37.8]
    assert window._prn_monitor._x_tick_labels == ["G04"]
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == ["37.8"]


def test_receiver_projection_prevents_skyplot_tracking_contradictions(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": False,
                "pvt_current": False,
                "accuracy": {"fix_type": "3D Fix", "three_d_error_m": 0.8},
                "prns": [
                    {
                        "prn": 12,
                        "state": "tracking",
                        "cno_db_hz": 39.0,
                        "cno_stable": False,
                        "cno_unstable_reason": "high_variance",
                        "used_in_fix": True,
                    }
                ],
                "sky_prns": [
                    {
                        "prn": 12,
                        "state": "tracking",
                        "az_deg": 120.0,
                        "el_deg": 40.0,
                        "used_in_fix": True,
                    }
                ],
            }
        }
    )

    # The C/N0 chart shows the current measured value immediately; the skyplot
    # still withholds an unstable channel without fresh PVT geometry.
    assert window._prn_monitor._displayed_prns == [12]
    assert window._skyplot_monitor._plotted_prns == []
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 1"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"


def test_prn_chart_keeps_gps_and_beidou_with_same_prn_number(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "satellite_id": "G05",
                        "cno_db_hz": 41.2,
                        "cno_stable": True,
                    },
                    {
                        "prn": 5,
                        "constellation": "beidou",
                        "satellite_id": "C05",
                        "state": "tracking",
                        "cno_db_hz": 38.7,
                        "cno_stable": True,
                    },
                ],
                "sky_prns": [],
            }
        }
    )

    assert window._prn_monitor._x_tick_labels == ["G05", "C05"]
    assert window._prn_monitor._bar_heights == [41.2, 38.7]
    assert window._prn_monitor._bar_colors == [GPS_TRACKING, BEIDOU_TRACKING]
    assert len(window._prn_monitor._displayed_prns) == 2
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 2"


def test_receiver_projection_styles_stable_and_fresh_pvt_skyplot_markers(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "three_d_error_m": 1.4,
                },
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "cno_db_hz": 41.2,
                        "cno_stable": True,
                    },
                    {
                        "prn": 9,
                        "state": "tracking",
                        "cno_db_hz": 20.0,
                        "cno_stable": False,
                        "cno_unstable_reason": "low_cno",
                        "used_in_fix": True,
                    },
                ],
                "sky_prns": [
                    {"prn": 5, "state": "tracking", "az_deg": 20.0, "el_deg": 55.0},
                    {
                        "prn": 9,
                        "state": "tracking",
                        "az_deg": 200.0,
                        "el_deg": 35.0,
                        "used_in_fix": True,
                    },
                    {"prn": 11, "state": "visible", "az_deg": 80.0, "el_deg": 25.0},
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == [5, 9]
    assert window._prn_monitor._bar_colors == [GPS_TRACKING, GPS_TRACKING]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 2"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"
    tracking_marker = window._skyplot_monitor._marker_items[0]
    assert tracking_marker.opts["brush"].color().name().upper() == BG_PANEL.upper()
    assert tracking_marker.opts["pen"].color().name().upper() == GPS_TRACKING.upper()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": False,
                "accuracy": {"fix_type": "3D Fix", "three_d_error_m": 1.4},
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "cno_db_hz": 41.2,
                        "cno_stable": True,
                    },
                    {
                        "prn": 9,
                        "state": "tracking",
                        "cno_db_hz": 20.0,
                        "cno_stable": False,
                        "cno_unstable_reason": "low_cno",
                        "used_in_fix": True,
                    },
                ],
                "sky_prns": [
                    {"prn": 5, "state": "tracking", "az_deg": 20.0, "el_deg": 55.0},
                    {
                        "prn": 9,
                        "state": "tracking",
                        "az_deg": 200.0,
                        "el_deg": 35.0,
                        "used_in_fix": True,
                    },
                ],
            }
        }
    )

    assert window._prn_monitor._displayed_prns == [5, 9]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 2"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"
    assert window._skyplot_monitor._marker_items[0].opts["brush"].color().name().upper() == (
        BG_PANEL.upper()
    )
    assert window._skyplot_monitor._marker_items[0].opts["pen"].color().name().upper() == (
        GPS_TRACKING.upper()
    )


def test_receiver_projection_clears_operator_state_on_error(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "accuracy": {"fix_type": "3D Fix", "three_d_error_m": 1.4},
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "cno_db_hz": 41.2,
                        "cno_stable": True,
                    }
                ],
                "sky_prns": [
                    {"prn": 5, "state": "tracking", "az_deg": 20.0, "el_deg": 55.0},
                ],
            }
        }
    )
    assert window._prn_monitor._displayed_prns == [5]
    assert window._skyplot_monitor._plotted_prns == [5]

    window._on_status("Backend runtime failed: test")

    assert window._prn_monitor._displayed_prns == []
    assert window._skyplot_monitor._plotted_prns == []
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 0"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"


def test_receiver_projection_clears_stable_prn_during_tracking_monitor_gap(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    locked_snapshot = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "cno_db_hz": 38.6,
                    "carrier_lock_test": 0.95,
                    "cno_stable": True,
                    "used_in_fix": True,
                }
            ],
            "sky_prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "az_deg": 270.0,
                    "el_deg": 35.0,
                    "used_in_fix": True,
                }
            ],
        }
    }
    transient_gap_snapshot = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "cno_stable": False,
                    "cno_unstable_reason": "missing_cno",
                    "used_in_fix": True,
                }
            ],
            "sky_prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "az_deg": 270.0,
                    "el_deg": 35.0,
                    "used_in_fix": True,
                }
            ],
        }
    }

    window._on_data_ready(locked_snapshot)
    assert window._prn_monitor._displayed_prns == [9]

    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == []
    assert window._skyplot_monitor._plotted_prns == []
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == []


def test_receiver_projection_does_not_hold_tracked_placeholder(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    locked_snapshot = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "cno_db_hz": 38.6,
                    "carrier_lock_test": 0.95,
                    "cno_stable": True,
                }
            ],
            "sky_prns": [
                {"prn": 9, "state": "tracking", "az_deg": 270.0, "el_deg": 35.0}
            ],
        }
    }
    transient_gap_snapshot = {
        "gnss_snapshot": {
            "pvt_output_seen": True,
            "pvt_current": True,
            "prns": [
                {
                    "prn": 9,
                    "state": "tracking",
                    "cno_stable": False,
                    "cno_unstable_reason": "missing_cno",
                }
            ],
            "sky_prns": [
                {"prn": 9, "state": "tracking", "az_deg": 270.0, "el_deg": 35.0}
            ],
        }
    }

    window._on_data_ready(locked_snapshot)
    assert window._prn_monitor._displayed_prns == [9]

    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == []
    assert window._prn_monitor._pending_tracking_prns == []
    assert window._prn_monitor._bar_heights == []
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == []


def test_gui_latches_stream_failure_through_cleanup_statuses(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    failure = "RX recv failed: EnvironmentError: IOError: socket closed"
    window._on_status(failure)
    window._on_status("Stopping GNSS-SDR")
    window._on_status("USRP stream stopped")
    window._on_worker_finished()

    assert window._stream_status_state == "error"
    assert _plain_text(window._status_chip) == f"Stream: {failure}"
    assert window._run_btn.text() == "▶ Start"


def test_gui_marks_gnss_handoff_pause_as_degraded_not_stopping(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    message = (
        "GNSS-SDR handoff paused; SDR stream still running "
        "(GNSS raw queue full; paused handoff instead of dropping contiguous IQ)"
    )
    window._on_status("USRP stream started")
    window._on_status(message)

    assert window._stream_status_state == "degraded"
    assert window._stream_running is True
    assert window._run_btn.text() == "■ Stop"
    assert window._run_btn.isEnabled() is True
    assert _plain_text(window._status_chip) == f"Stream: {message}"
    assert _plain_text(window._system_health_label) == "System health: Degraded"


def test_gui_shows_gnss_fix_accuracy_from_snapshot(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "receiver_time_s": 3661,
                "pvt_observation_count": 12,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "utm_easting_m": 321124.221634667,
                    "utm_northing_m": 3724046.2518229913,
                    "utm_zone": "43N",
                    "three_d_error_m": 1.92,
                    "horizontal_error_m": 0.51,
                    "east_error_m": -0.12,
                    "north_error_m": 0.49,
                    "up_error_m": 1.85,
                    "lat_deg": 33.6412345,
                    "lon_deg": 73.0712345,
                    "alt_m": 542.4,
                    "hdop": 0.82,
                    "vdop": 1.22,
                    "pdop": 1.47,
                    "gdop": 1.51,
                    "valid_sats": 12,
                    "cep50_m": 0.48,
                    "cep95_m": 1.27,
                    "cep_sample_count": 30,
                    "cep_min_points": 30,
                    "cep_ready": True,
                    "truth_available": True,
                }
            }
        }
    )

    assert _plain_text(window._fix_chip) == "FIX"
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._latitude_label) == "lat/long: 33.6412345° / 73.0712345°"
    assert _plain_text(window._altitude_label) == "altitude: 542.4 m"
    assert _plain_text(window._receiver_time_label) == "Time: 01:01:01"
    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: 0.82 / 1.22 / 1.47 / 1.51"
    assert _plain_text(window._cep50_label) == "CEP50: 0.48 m"
    assert _plain_text(window._cep95_label) == "CEP95: 1.27 m"

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": False,
                "receiver_time_s": 3662,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "lat_deg": 33.6412345,
                    "lon_deg": 73.0712345,
                    "alt_m": 542.4,
                    "hdop": 0.82,
                    "vdop": 1.22,
                    "pdop": 1.47,
                    "gdop": 1.51,
                },
            }
        }
    )

    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._latitude_label) == "lat/long: -- / --"
    assert _plain_text(window._altitude_label) == "altitude: --"
    assert _plain_text(window._receiver_time_label) == "Time: 01:01:02"
    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: -- / -- / -- / --"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "receiver_time_s": 3663,
                "pvt_observation_count": 12,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "lat_deg": 33.6412345,
                    "lon_deg": 73.0712345,
                    "alt_m": 542.4,
                    "hdop": 0.92,
                    "vdop": 2.82,
                    "pdop": 2.97,
                    "valid_sats": 12,
                },
            }
        }
    )

    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: 0.92 / 2.82 / 2.97 / --"

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "pvt_observation_count": 10,
                "accuracy": {
                    "fix_type": "2D Fix",
                    "valid_sats": 10,
                }
            }
        }
    )

    assert _plain_text(window._fix_chip) == "FIX"
    assert _plain_text(window._position_status_label) == "PVT fix: 2D Fix"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"
    assert not hasattr(window, "_accuracy_summary_label")


def test_gui_marks_current_high_dop_pvt_as_degraded(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "three_d_error_m": 12.13,
                    "pdop": 10.97,
                },
            }
        }
    )
    assert _plain_text(window._fix_chip) == "DEGRADED"
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "pvt_observation_count": 10,
                "accuracy": {
                    "fix_type": "3D Fix",
                    "three_d_error_m": 1030.29,
                    "pdop": 2.10,
                    "valid_sats": 10,
                },
            }
        }
    )
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._cep50_label) == "CEP50: --"
    assert _plain_text(window._cep95_label) == "CEP95: --"


def test_gui_uses_gnss_sdr_pvt_observation_count_for_used_count(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    window._on_data_ready(
        {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": True,
                "pvt_observation_count": 8,
                "prns": [
                    {
                        "prn": 5,
                        "state": "tracking",
                        "cno_db_hz": 41.0,
                        "cno_stable": True,
                    }
                ],
                "sky_prns": [],
            }
        }
    )

    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 1"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 8"


def test_main_view_has_no_phase_calibration_tab_or_controls(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    metrics = {
        "phase_offsets_raw_deg": [0.0, 10.0, -20.0, 30.0],
        "phase_offsets_calibrated_deg": [0.0, 1.0, -2.0, 3.0],
        "complex_samples_raw": np.ones((4, 64), dtype=np.complex64),
        "complex_samples_calibrated": np.ones((4, 64), dtype=np.complex64),
        "gnss_snapshot": {},
    }

    window._on_data_ready(metrics)

    assert not hasattr(window, "_tabs")
    assert not hasattr(window, "_phase_before_values_label")
    assert not hasattr(window, "_phase_after_values_label")


def test_phase_calibration_tab_has_no_calibration_selector(qtbot) -> None:
    cfg = StreamConfig(phase_calibration_file=None)
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert not hasattr(window, "_gnss_handoff_combo")
    assert not hasattr(window, "_calibration_profile_combo")


def test_gui_shows_doa_and_rx_health(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()
    window._operator_tabs.setCurrentIndex(1)

    scan = np.linspace(cfg.doa_min_deg, cfg.doa_max_deg, cfg.doa_points)
    raw_doa = 12.5 * np.exp(-0.5 * ((scan - 88.75) / 12.0) ** 2)
    lcmv_response_db = -20.0 * np.exp(-0.5 * ((scan - 88.75) / 8.0) ** 2)
    expected_bearing = (90.0 - 88.75) % 360.0

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "doa_raw_spectrum": raw_doa,
            "lcmv_test": {
                "enabled": True,
                "mode": "on",
                "null_bearing_deg": expected_bearing,
                "lcmv_response_db": lcmv_response_db,
            },
            "rx_signal_health": {
                "assessed": True,
                "clipping_suspected": False,
                "clipping_suspected_count": 0,
                "iq_peak_component": 0.0032,
                "iq_peak_magnitude": 0.0035,
                "iq_rms_magnitude": 0.0007,
                "near_full_scale_pct": 0.0,
                "threshold_component": 0.98,
                "threshold_pct": 0.1,
            },
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._doa_status_label) == f"DoA: {expected_bearing:.1f}°"
    assert _plain_text(window._doa_chip) == f"{expected_bearing:.1f}°"
    assert _plain_text(window._rx_clipping_label) == "RX clipping: OK"
    assert _plain_text(window._rx_peak_label) == "IQ peak: 0.0032 / 0.980 mag 0.0035"
    assert _plain_text(window._rx_rms_label) == "IQ RMS: 0.0007"
    assert _plain_text(window._rx_near_full_scale_label) == "Near full scale: 0.0000 / 0.1000%"
    assert not hasattr(window, "_doa_compass_curve")
    assert not hasattr(window, "_array_pattern_curve")

    polar_db_x, polar_db_y = window._doa_polar_db_curve.getData()
    assert polar_db_x is not None and polar_db_y is not None
    assert len(polar_db_x) == cfg.doa_points + 1
    assert len(polar_db_y) == cfg.doa_points + 1
    rel_db = 10.0 * np.log10(np.maximum(raw_doa, 1e-300) / float(np.nanmax(raw_doa)))
    rel_db_min = float(np.min(rel_db[np.isfinite(rel_db)]))
    rel_db_max = float(np.max(rel_db[np.isfinite(rel_db)]))
    expected_db_radius = (rel_db - rel_db_min) / (rel_db_max - rel_db_min)
    assert np.nanmax(np.hypot(polar_db_x, polar_db_y)) == pytest.approx(
        float(np.nanmax(expected_db_radius))
    )
    polar_db_marker_x, polar_db_marker_y = window._doa_polar_db_marker.getData()
    assert polar_db_marker_x is not None and polar_db_marker_y is not None
    assert polar_db_marker_x[-1] == pytest.approx(
        float(np.nanmax(expected_db_radius)) * np.sin(np.deg2rad(expected_bearing))
    )
    assert polar_db_marker_y[-1] == pytest.approx(
        float(np.nanmax(expected_db_radius)) * np.cos(np.deg2rad(expected_bearing))
    )
    lcmv_x, lcmv_y = window._lcmv_response_curve.getData()
    assert lcmv_x is not None and lcmv_y is not None
    assert len(lcmv_x) == cfg.doa_points
    assert len(lcmv_y) == cfg.doa_points
    bearing_scan, order = window._bearing_axis_for_internal_scan(scan)
    assert np.allclose(lcmv_x, bearing_scan)
    assert np.allclose(lcmv_y, lcmv_response_db[order])
    assert window._lcmv_response_marker.value() == pytest.approx(expected_bearing)

    # The offscreen Qt backend can retain a paint event for pyqtgraph's axes
    # after this test returns.  Drain it while the window and its AxisItems are
    # still alive; otherwise pytest-qt may delete the plot first and process
    # the queued paint at the beginning of the next test.
    qtbot.wait(1)


def test_gui_displays_current_source_count_estimators(qtbot) -> None:
    cfg = StreamConfig(expected_sources=1)
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "n_sources": 1,
            "source_estimate_gap": 2.5,
            "source_effective_rank": 1.42,
            "gnss_snapshot": {},
        }
    )

    expected = "set 1 | eig-gap 2.5 | eff-rank 1.42"
    assert _plain_text(window._music_sources_label) == f"MUSIC sources: {expected}"
    assert f"MUSIC sources: {expected}" in _plain_text(window._system_info_label)
    assert f"Sources {expected}" in _plain_text(window._antijam_summary_label)


def test_gui_displays_internal_doa_as_top_zero_clockwise_bearing(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    assert not hasattr(window, "_doa_angle_combo")

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._doa_status_label) == "DoA: 1.2°"
    assert _plain_text(window._doa_chip) == "1.2°"
