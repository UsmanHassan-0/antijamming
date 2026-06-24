from __future__ import annotations

import io
import logging
import re

import numpy as np
import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QGuiApplication
from PyQt6.QtWidgets import QLabel, QScrollArea, QSizePolicy

from antijamming.config import StreamConfig
from antijamming.ui.main_window import MainWindow
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
    _skyplot_view_limit_for_side,
    _skyplot_view_range_for_side,
    _skyplot_xy,
)
from antijamming.ui.theme import (
    ALERT,
    BEIDOU_TRACKING_FIX,
    BG_APP,
    BG_PANEL,
    FG_TEXT,
    GALILEO_TRACKING,
    GALILEO_TRACKING_FIX,
    GLONASS_TRACKING,
    GPS_TRACKING,
    GPS_TRACKING_FIX,
    INPUT_BORDER,
    INFO,
    WARNING,
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
        self.algorithm_mode = "lcmv"
        self.jammer_detection_enabled = False

    def start(self) -> None:
        self.started = True
        self.status.emit("USRP stream started")

    def stop(self, reason: str = "normal stop") -> None:
        self.stopped = True
        self.stop_reasons.append(reason)
        self.status.emit("USRP stream stopped")

    def set_algorithm_mode(self, mode: str) -> None:
        self.algorithm_mode = str(mode)

    def set_jammer_detection_enabled(self, enabled: bool) -> None:
        self.jammer_detection_enabled = bool(enabled)
        self.status.emit(
            "Jammer detection enabled" if enabled else "Jammer detection disabled"
        )


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


def _assert_empty_curve(curve) -> None:
    x_values, y_values = curve.getData()
    assert x_values is None or len(x_values) == 0
    assert y_values is None or len(y_values) == 0


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
    assert skyplot._static_label_positions["30°"][1] == pytest.approx(2.0 / 3.0)
    assert skyplot._static_label_positions["60°"][1] == pytest.approx(1.0 / 3.0)
    assert skyplot._static_label_positions["30°"][0] > 0.0
    assert skyplot._static_label_positions["60°"][0] > 0.0
    x_min, x_max, y_min, y_max = skyplot._view_range
    assert x_min <= -1.15
    assert x_max >= 1.15
    assert y_min <= -1.15
    assert y_max >= 1.15
    assert all(
        x_min < x < x_max and y_min < y < y_max
        for x, y in skyplot._static_label_positions.values()
    )
    assert {"N", "E", "S", "W"}.issubset(skyplot._static_label_positions)
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


def test_skyplot_view_range_tightens_as_plot_gets_larger() -> None:
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
        unplaced_tracking_prns=[12, 14],
    )

    assert skyplot._plotted_prns == [5, 7, 9]
    assert skyplot._unplaced_tracking_prns == [12, 14]
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
                "prn": 12,
                "constellation": "galileo",
                "satellite_id": "E12",
                "state": "tracking",
                "az_deg": 40.0,
                "el_deg": 50.0,
                "used_in_fix": True,
            },
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

    assert [item.toPlainText() for item in skyplot._marker_label_items] == ["G12", "E12", "C07", "R03"]
    assert skyplot._marker_items[0].opts["pen"].color().name().upper() == GPS_TRACKING.upper()
    assert skyplot._marker_items[1].opts["brush"].color().name().upper() == GALILEO_TRACKING_FIX.upper()
    assert skyplot._marker_items[2].opts["brush"].color().name().upper() == BEIDOU_TRACKING_FIX.upper()
    assert skyplot._marker_items[3].opts["pen"].color().name().upper() == GLONASS_TRACKING.upper()


def test_skyplot_groups_constellations_and_styles_galileo_separately(qtbot) -> None:
    skyplot = SkyplotMonitor()
    qtbot.addWidget(skyplot)

    skyplot.update_snapshot(
        [
            {
                "prn": 12,
                "constellation": "galileo",
                "state": "tracking",
                "az_deg": 40.0,
                "el_deg": 50.0,
            },
            {"prn": 5, "constellation": "gps", "state": "tracking", "az_deg": 20.0, "el_deg": 45.0},
            {
                "prn": 5,
                "constellation": "galileo",
                "state": "tracking",
                "az_deg": 80.0,
                "el_deg": 40.0,
                "used_in_fix": True,
            },
            {"prn": 12, "constellation": "gps", "state": "tracking", "az_deg": 100.0, "el_deg": 35.0},
        ]
    )

    assert [item.toPlainText() for item in skyplot._marker_label_items] == [
        "G05",
        "G12",
        "E05",
        "E12",
    ]
    assert skyplot._marker_items[0].opts["pen"].color().name().upper() == GPS_TRACKING.upper()
    assert skyplot._marker_items[0].opts["brush"].color().name().upper() == BG_PANEL.upper()
    assert skyplot._marker_items[1].opts["pen"].color().name().upper() == GPS_TRACKING.upper()
    assert skyplot._marker_items[1].opts["brush"].color().name().upper() == BG_PANEL.upper()
    assert skyplot._marker_items[2].opts["brush"].color().name().upper() == GALILEO_TRACKING_FIX.upper()
    assert skyplot._marker_items[3].opts["pen"].color().name().upper() == GALILEO_TRACKING.upper()
    assert skyplot._marker_items[3].opts["brush"].color().name().upper() == BG_PANEL.upper()


def test_prn_chart_labels_bars_with_cno_and_axis_with_satellites(qtbot) -> None:
    monitor = PocketPrnMonitor()
    qtbot.addWidget(monitor)

    monitor.update_snapshot(
        [
            {
                "prn": 12,
                "constellation": "E",
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

    assert monitor._displayed_prns == [5, 12, 7]
    assert monitor._x_tick_labels == ["G05", "E12", "C07"]
    assert [label.toPlainText() for label in monitor._label_items] == [
        "42.4",
        "36.7",
        "38.1",
    ]
    assert monitor._bar_colors == [GPS_TRACKING, GALILEO_TRACKING, BEIDOU_TRACKING_FIX]
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


def test_prn_chart_does_not_treat_observable_validity_as_cno_stability(qtbot) -> None:
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


def test_gui_single_run_button_toggles_start_stop(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert window._run_btn.text() == "▶ Start"
    assert not hasattr(window, "_mode_toggle_btn")
    assert not hasattr(window, "_lcmv_toggle_btn")
    assert not hasattr(window, "_algorithm_mode_btn")
    assert not hasattr(window, "_algorithm_status_label")
    assert not hasattr(window, "_beamforming_status_label")
    assert not hasattr(window, "_gnss_feed_status_label")
    assert not hasattr(window, "_tabs")
    assert not hasattr(window, "_main_scroll")
    assert window.centralWidget().findChildren(QScrollArea) == []
    assert _plain_text(window._output_path_label) == "Output: GNSS IQ -> GNSS-SDR"
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


def test_gui_metrics_timer_follows_configured_ui_interval(qtbot) -> None:
    worker = DummyWorker()
    cfg = StreamConfig(ui_update_interval_s=0.05)
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    assert window._metrics_timer.interval() == 50


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

    def metrics(prn: int, cno: float, az_deg: float) -> dict[str, object]:
        return {
            "gnss_snapshot": {
                "pvt_output_seen": True,
                "pvt_current": False,
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

    window._refresh_gnss_monitors(metrics(5, 41.0, 20.0))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [5]

    now = 10.1
    window._refresh_gnss_monitors(metrics(5, 35.0, 80.0))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [5]

    now = 10.25
    window._refresh_gnss_monitors(metrics(9, 35.0, 80.0))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [41.0]
    assert window._skyplot_monitor._plotted_prns == [9]

    now = 10.55
    window._refresh_gnss_monitors(metrics(5, 35.0, 80.0))
    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_heights == [35.0]
    assert window._skyplot_monitor._plotted_prns == [5]


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
    assert window._jammer_detection_checkbox.isChecked() is False
    assert _plain_text(window._output_path_label) == "Output: GNSS IQ -> GNSS-SDR"
    assert not _is_descendant(window._output_path_label, window._main_view)
    assert not _is_descendant(window._output_path_label, window._receiver_card)
    assert "GNSS feed:" not in _plain_text(window._system_info_label)
    assert "Input feed:" not in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: GNSS Beamformed Continuous" in (
        _plain_text(window._system_info_label)
    )

    window._run_btn.click()
    assert worker.started is True
    assert cfg.gnss_sdr_enable is True


def test_gui_has_fixed_beamformed_gnss_handoff(qtbot) -> None:
    worker = DummyWorker()
    window = MainWindow(StreamConfig(), worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert not hasattr(window, "_lcmv_toggle_btn")
    assert not hasattr(window, "_gnss_source_combo")
    assert not hasattr(window, "_gnss_source_label")
    assert _plain_text(window._jammer_chip) == "Idle"
    assert _plain_text(window._jammer_state_label) == "Jammer status: Idle"
    assert "Beamforming: Active" in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: GNSS Beamformed Continuous" in _plain_text(
        window._system_info_label
    )

    window._refresh_system_info()
    assert "Beamforming: Active" in _plain_text(window._system_info_label)
    assert "GNSS-SDR handoff: GNSS Beamformed Continuous" in _plain_text(
        window._system_info_label
    )


def test_gui_jammer_detection_toggle_is_opt_in(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    assert cfg.jammer_detection_enabled is False
    assert worker.jammer_detection_enabled is False
    assert window._jammer_detection_checkbox.isChecked() is False

    window._jammer_detection_checkbox.setChecked(True)

    assert cfg.jammer_detection_enabled is True
    assert worker.jammer_detection_enabled is True
    assert _is_descendant(window._jammer_detection_checkbox, window._receiver_card)
    assert not _is_descendant(window._jammer_detection_checkbox, window._operator_header)


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
    assert not _is_descendant(window._jammer_detection_checkbox, window._operator_header)
    assert not _is_descendant(window._run_btn, window._operator_tabs)
    assert window._operator_tabs.tabText(0) == "Receiver"
    assert window._operator_tabs.tabText(1) == "Anti-Jam"
    assert _is_descendant(window._receiver_card, window._receiver_tab)
    assert not hasattr(window, "_receiver_status_card")
    assert _is_descendant(window._jammer_state_label, window._receiver_card)
    assert _is_descendant(window._position_status_label, window._receiver_card)
    assert not _is_descendant(window._jammer_state_label, window._antijam_tab)
    assert _is_descendant(window._skyplot_monitor, window._receiver_card)
    assert not _is_descendant(window._receiver_summary_label, window._receiver_card)
    assert not hasattr(window, "_stream_card")
    assert not hasattr(window, "_skyplot_card")
    assert not _is_descendant(window._stream_summary_label, window._main_view)
    assert not _is_descendant(window._jammer_chip, window._operator_tabs)
    assert not _is_descendant(window._null_chip, window._operator_tabs)
    assert not _is_descendant(window._suppression_chip, window._operator_tabs)
    assert _plain_text(window._fix_chip) == "NO FIX"
    assert _plain_text(window._accuracy_chip) == "--"
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._latitude_label) == "lat/long: -- / --"
    assert _plain_text(window._altitude_label) == "altitude: --"
    assert _plain_text(window._receiver_time_label) == "Time: --"
    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: -- / -- / -- / --"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 0"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert _plain_text(window._position_error_label) == "PVT accuracy: --"
    assert _plain_text(window._enu_label) == "UTM east/north: -- / --"
    assert "Fix:" not in _plain_text(window._receiver_summary_label)
    operator_text = "\n".join(_plain_text(label) for label in window._main_view.findChildren(QLabel))
    all_visible_text = "\n".join(_plain_text(label) for label in window.findChildren(QLabel))
    assert "Receiver Overview" in operator_text
    assert "Satellite Sky View" in operator_text
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
    assert "Satellites tracked:" in operator_text
    assert "Satellites used for PVT:" in operator_text
    assert "PVT accuracy:" in operator_text
    assert "position 3D:" not in operator_text
    assert "UTM east/north:" in operator_text
    assert "UTM east/north/up:" not in operator_text
    assert "East: -- / North: -- / Up: --" not in operator_text
    assert "Local East:" not in operator_text
    assert "Local North:" not in operator_text
    assert "Local Up:" not in operator_text
    assert "horizontal 2D:" not in operator_text
    assert "3D displacement:" not in operator_text
    assert "C/N0 used for PVT:" not in operator_text
    assert "Jammer status:" in operator_text
    assert "Direction candidate:" in operator_text
    assert "Power rise:" in operator_text
    assert "Detector confidence:" not in operator_text
    assert "Raw IQ power:" in operator_text
    assert "RX clipping:" in operator_text
    assert "IQ peak:" in operator_text
    assert "IQ RMS:" in operator_text
    assert "Near full scale:" in operator_text
    assert "Nulling:" in operator_text
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
    assert WARNING in window._jammer_state_label.text()


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


def test_gui_clears_fix_and_hides_accuracy_without_pvt(qtbot) -> None:
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
    assert _plain_text(window._accuracy_chip) == "1.20 m"
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._position_error_label) == "PVT accuracy: 1.20 m"

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
    assert _plain_text(window._accuracy_chip) == "--"
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._position_error_label) == "PVT accuracy: --"


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
    assert window._mode_chip.text() == ""
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
    assert _is_descendant(window._doa_plot, window._main_view)
    assert not hasattr(window, "_doa_raw_plot")
    assert not hasattr(window, "_doa_compass_plot")
    assert _is_descendant(window._lcmv_plot, window._main_view)
    assert not hasattr(window, "_rf_spectrum_plot")
    assert not hasattr(window, "_gnss_quality_plot")
    assert _is_descendant(window._skyplot_monitor, window._receiver_card)
    assert _is_descendant(window._jammer_state_label, window._receiver_card)
    assert _is_descendant(window._position_status_label, window._receiver_card)
    assert not _is_descendant(window._jammer_state_label, window._antijam_tab)
    main_layout = window._main_view.layout()
    assert main_layout is not None
    assert main_layout.indexOf(window._operator_header) >= 0
    assert main_layout.indexOf(window._operator_tabs) >= 0
    assert main_layout.indexOf(window._operator_header) < main_layout.indexOf(window._operator_tabs)
    assert _is_descendant(window._algorithm_plots_container, window._antijam_tab)
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

    assert window._prn_monitor._displayed_prns == [5, 9]
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
        ]
    )
    assert window._prn_monitor._bar_heights == [42.4, 36.4]
    assert window._prn_monitor._bar_colors == [
        GPS_TRACKING,
        GPS_TRACKING_FIX,
    ]
    assert _plain_text(window._stable_prns_chip) == "2"
    assert _plain_text(window._used_in_pvt_chip) == "1"
    assert _plain_text(window._position_status_label) == "PVT fix: DEGRADED"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 2"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    assert _plain_text(window._position_error_label) == "PVT accuracy: --"
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
    assert window._skyplot_monitor._unplaced_tracking_prns == [12, 14]
    assert window._current_tracking_prns == [5, 9, 12, 13, 14]
    assert window._stable_prns == [5, 9]
    assert window._current_used_in_pvt_prns == [9]
    assert window._fresh_geometry_prns == [5, 7, 9, 13]
    assert window._tracking_without_geometry == [12, 14]
    assert window._prn_monitor._bar_item.zValue() == pytest.approx(10.0)
    assert [
        brush.color().alpha()
        for brush in window._prn_monitor._bar_item.opts["brushes"]
    ] == [255, 255]
    assert all(
        brush.style() == Qt.BrushStyle.SolidPattern
        for brush in window._prn_monitor._bar_item.opts["brushes"]
    )
    assert [
        pen.color().alpha()
        for pen in window._prn_monitor._bar_item.opts["pens"]
    ] == [255, 255]
    assert window._prn_monitor._x_tick_labels == ["G05", "G09"]
    assert [label.toPlainText() for label in window._prn_monitor._label_items] == [
        "42.4",
        "36.4",
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
    assert window._stable_prns == []
    assert window._current_used_in_pvt_prns == []
    assert window._fresh_geometry_prns == [11]

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
    assert window._prn_monitor._pending_tracking_prns == [8]
    assert window._prn_monitor._pending_tracking_reasons == {8: "missing_cno"}
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

    assert window._prn_monitor._displayed_prns == []
    assert window._skyplot_monitor._plotted_prns == []
    assert window._stable_prns == []
    assert window._current_used_in_pvt_prns == []
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 0"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert _plain_text(window._position_error_label) == "PVT accuracy: --"
    assert _plain_text(window._accuracy_chip) == "--"


def test_prn_chart_keeps_gps_and_galileo_with_same_prn_number(qtbot) -> None:
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
                        "constellation": "galileo",
                        "satellite_id": "E05",
                        "state": "tracking",
                        "cno_db_hz": 38.7,
                        "cno_stable": True,
                    },
                ],
                "sky_prns": [],
            }
        }
    )

    assert window._prn_monitor._x_tick_labels == ["G05", "E05"]
    assert window._prn_monitor._bar_heights == [41.2, 38.7]
    assert window._prn_monitor._bar_colors == [GPS_TRACKING, GALILEO_TRACKING]
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

    assert window._prn_monitor._displayed_prns == [5]
    assert window._prn_monitor._bar_colors == [GPS_TRACKING]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert window._stable_prns == [5]
    assert window._current_used_in_pvt_prns == []
    assert window._raw_used_in_fix_prns == [9]
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 1"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    assert _plain_text(window._position_error_label) == "PVT accuracy: 1.40 m"
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

    assert window._prn_monitor._displayed_prns == [5]
    assert window._skyplot_monitor._plotted_prns == [5]
    assert window._current_used_in_pvt_prns == []
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 1"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"
    assert _plain_text(window._position_error_label) == "PVT accuracy: --"
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
    assert window._stable_prns == []
    assert window._current_used_in_pvt_prns == []
    assert _plain_text(window._position_status_label) == "PVT fix: NO FIX"
    assert _plain_text(window._satellites_tracked_label) == "Satellites tracked: 0"
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 0"


def test_receiver_projection_holds_stable_prn_during_transient_tracking_monitor_gap(qtbot) -> None:
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
    assert window._stable_prns == [9]
    assert window._current_used_in_pvt_prns == [9]

    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == [9]
    assert window._stable_prns == [9]
    assert window._current_used_in_pvt_prns == [9]
    assert window._skyplot_monitor._plotted_prns == [9]
    assert _plain_text(window._satellites_used_label) == "Satellites used for PVT: 1 (G09)"
    held_entry, _ = window._prn_display_hold["G09"]
    window._prn_display_hold["G09"] = (held_entry, 0.0)
    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == [9]
    assert window._stable_prns == [9]
    assert window._current_used_in_pvt_prns == [9]


def test_receiver_projection_expires_non_pvt_prn_display_hold(qtbot) -> None:
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
    held_entry, _ = window._prn_display_hold["G09"]
    window._prn_display_hold["G09"] = (held_entry, 0.0)

    window._on_data_ready(transient_gap_snapshot)

    assert window._prn_monitor._displayed_prns == []
    assert window._stable_prns == []
    assert window._current_used_in_pvt_prns == []


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
    assert window._stream_stopping is False
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
                }
            }
        }
    )

    assert _plain_text(window._fix_chip) == "FIX"
    assert _plain_text(window._position_status_label) == "PVT fix: 3D Fix"
    assert _plain_text(window._accuracy_chip) == "1.92 m"
    assert _plain_text(window._latitude_label) == "lat/long: 33.6412345° / 73.0712345°"
    assert _plain_text(window._altitude_label) == "altitude: 542.4 m"
    assert _plain_text(window._receiver_time_label) == "Time: 01:01:01"
    assert _plain_text(window._dop_label) == "HDOP/VDOP/PDOP/GDOP: 0.82 / 1.22 / 1.47 / 1.51"
    assert _plain_text(window._enu_label) == (
        "UTM east/north: 321124.222 / 3724046.252 (43N)"
    )
    assert _plain_text(window._position_error_label) == "PVT accuracy: 1.92 m"

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
    assert _plain_text(window._enu_label) == "UTM east/north: -- / --"

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
    assert _plain_text(window._accuracy_chip) == "--"
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
    assert _plain_text(window._accuracy_chip) == "12.13 m"

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
    assert _plain_text(window._accuracy_chip) == "1030.29 m"


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


def test_gui_shows_doa_and_lcmv_suppression(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    pattern = np.zeros((cfg.doa_points,), dtype=np.float64)
    scan = np.linspace(cfg.doa_min_deg, cfg.doa_max_deg, cfg.doa_points)
    pattern[int(np.argmin(np.abs(scan - 88.75)))] = -37.5
    normalized_doa = np.exp(-0.5 * ((scan - 88.75) / 12.0) ** 2)

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "doa_spectrum": normalized_doa,
            "lcmv_null_active": True,
            "lcmv_pattern_db": pattern,
            "lcmv_input_power_db": -63.0,
            "lcmv_output_power_db": -65.25,
            "lcmv_power_delta_db": 2.25,
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
            "jammer": {
                "state": "detected",
                "detected": True,
                "input_power_db": -63.0,
                "min_power_db": -120.0,
                "power_rise_db": 12.0,
                "power_rise_threshold_db": 8.0,
                "doa_deg": 88.75,
                "reason": "Raw IQ power rise 12.0 dB above baseline -75.0 dB",
            },
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._jammer_chip) == "Detected"
    assert _plain_text(window._jammer_state_label) == "Jammer status: Detected"
    assert _plain_text(window._jammer_power_label) == "Raw IQ power: -63.0 / -120.0 dB"
    assert _plain_text(window._jammer_power_rise_label) == "Power rise: 12.0 / 8.0 dB"
    assert _plain_text(window._rx_clipping_label) == "RX clipping: OK"
    assert _plain_text(window._rx_peak_label) == "IQ peak: 0.0032 / 0.980 mag 0.0035"
    assert _plain_text(window._rx_rms_label) == "IQ RMS: 0.0007"
    assert _plain_text(window._rx_near_full_scale_label) == "Near full scale: 0.0000 / 0.1000%"
    expected_bearing = (360.0 - 88.75) % 360.0
    assert _plain_text(window._jammer_doa_label) == f"Direction candidate: {expected_bearing:.1f}°"
    assert _plain_text(window._jammer_null_label) == "Nulling: On"
    assert (
        _plain_text(window._jammer_suppression_label)
        == "Beamformed IQ reduction: 2.2 dB (-63.0 to -65.2 dB)"
    )
    assert not hasattr(window, "_jammer_reason_label")
    assert _plain_text(window._doa_chip) == f"{expected_bearing:.1f}°"
    assert _plain_text(window._suppression_chip) == "2.2 dB"
    assert _plain_text(window._null_chip) == "Active"
    assert not hasattr(window, "_doa_raw_curve")
    assert not hasattr(window, "_doa_compass_curve")
    assert window._lcmv_marker.isVisible()
    expected_marker = (360.0 - scan[np.argmin(np.abs(scan - 88.75))]) % 360.0
    assert window._lcmv_marker.value() == pytest.approx(expected_marker)
    lcmv_x, lcmv_y = window._lcmv_curve.getData()
    assert lcmv_x is not None and len(lcmv_x) == cfg.doa_points
    assert np.all(np.diff(lcmv_x) >= 0.0)
    assert lcmv_y is not None and np.nanmin(lcmv_y) == -37.5

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "lcmv_pattern_db": pattern,
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._jammer_chip) == "Monitoring"
    assert _plain_text(window._suppression_chip) == "--"

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "lcmv_null_active": False,
            "lcmv_pattern_db": pattern,
            "lcmv_power_delta_db": 5.5,
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._jammer_chip) == "Monitoring"
    assert _plain_text(window._suppression_chip) == "--"
    assert _plain_text(window._jammer_suppression_label) == "Beamformed IQ reduction: --"
    assert not window._lcmv_marker.isVisible()
    _assert_empty_curve(window._lcmv_curve)

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "lcmv_null_active": True,
            "lcmv_pattern_db": pattern,
            "jammer": {"state": "not_detected", "detected": False},
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._jammer_chip) == "Not detected"
    assert _plain_text(window._suppression_chip) == "--"
    assert _plain_text(window._jammer_suppression_label) == "Beamformed IQ reduction: --"
    _assert_empty_curve(window._lcmv_curve)


def test_gui_displays_internal_doa_as_clockwise_bearing(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)

    assert not hasattr(window, "_doa_angle_combo")

    window._on_data_ready(
        {
            "doa_deg": 88.75,
            "doa_spectrum": np.ones((cfg.doa_points,), dtype=np.float64),
            "lcmv_null_active": False,
            "jammer": {
                "state": "detected",
                "detected": True,
                "doa_deg": 88.75,
            },
            "gnss_snapshot": {},
        }
    )

    assert _plain_text(window._jammer_doa_label) == "Direction candidate: 271.2°"
    assert _plain_text(window._doa_chip) == "271.2°"


def test_gui_lcmv_plot_stays_empty_without_detected_jammer(qtbot) -> None:
    cfg = StreamConfig(doa_points=181)
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    pattern = np.full((cfg.doa_points,), -24.0, dtype=np.float64)
    pattern[0] = -120.0
    pattern[90] = 0.0

    window._on_data_ready(
        {
            "doa_deg": 90.0,
            "lcmv_null_active": False,
            "lcmv_pattern_db": pattern,
            "gnss_snapshot": {},
        }
    )

    assert not window._lcmv_marker.isVisible()
    _assert_empty_curve(window._lcmv_curve)


def test_gui_shows_backend_jammer_detection_state(qtbot) -> None:
    cfg = StreamConfig()
    worker = DummyWorker()
    window = MainWindow(cfg, worker)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()

    window._on_data_ready(
        {
            "jammer": {"state": "not_detected", "detected": False},
            "gnss_snapshot": {},
        }
    )
    assert _plain_text(window._jammer_state_label) == "Jammer status: Not detected"
    assert _plain_text(window._jammer_power_label) == "Raw IQ power: --"
    assert _plain_text(window._jammer_power_rise_label) == "Power rise: --"
    assert _plain_text(window._jammer_doa_label) == "Direction candidate: --"
    assert _plain_text(window._jammer_null_label) == "Nulling: Off"
    assert _plain_text(window._jammer_suppression_label) == "Beamformed IQ reduction: --"
    assert not hasattr(window, "_jammer_reason_label")

    window._on_data_ready(
        {
            "doa_deg": 42.0,
            "jammer": {
                "state": "not_detected",
                "detected": False,
                "detector_power_db": -82.0,
                "power_threshold_db": -70.0,
                "power_rise_db": 2.0,
                "power_rise_threshold_db": 8.0,
            },
            "gnss_snapshot": {},
        }
    )
    assert _plain_text(window._jammer_chip) == "Not detected"
    assert _plain_text(window._jammer_power_label) == "Raw IQ power: -82.0 / -70.0 dB"
    assert _plain_text(window._jammer_power_rise_label) == "Power rise: 2.0 / 8.0 dB"

    window._on_data_ready(
        {
            "doa_deg": 42.0,
            "jammer": {"state": "disabled", "detected": False},
            "gnss_snapshot": {},
        }
    )
    assert _plain_text(window._jammer_chip) == "Detection off"
