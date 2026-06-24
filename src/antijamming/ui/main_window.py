"""Main PyQt window composition for realtime anti-jamming controls."""

from __future__ import annotations

from html import escape
import logging
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from antijamming.dsp.models import (
    internal_angle_to_operator_bearing_deg,
    normalize_algorithm_mode,
    operator_bearing_axis_for_internal_scan,
)
from antijamming.dsp.phase import load_phase_correction_vector
from antijamming.config import REPO_ROOT, StreamConfig
from antijamming.gnss.sdr_bridge.constants import PVT_DEGRADED_PDOP_THRESHOLD
from antijamming.runtime import StreamWorker

from antijamming.ui.widgets.cards import (
    make_panel,
    make_plot_body,
    make_stretch_row,
)
from antijamming.ui.widgets.plots import (
    build_doa_plot,
    build_lcmv_plot,
    LCMV_Y_RANGE,
)
from antijamming.ui.widgets.prn_monitor import PocketPrnMonitor
from antijamming.ui.widgets.skyplot import SkyplotMonitor
from antijamming.ui.theme import (
    ALERT,
    BG_APP,
    BG_PANEL,
    BORDER,
    CONTROL_H,
    FG_SOFT,
    FG_TEXT,
    FONT_SIZE_EMPHASIS,
    FONT_SIZE_TITLE,
    INFO,
    SUCCESS,
    WARNING,
    build_root_stylesheet,
    disabled_button_style,
    operator_tabs_style,
    start_button_style,
    stop_button_style,
    text_style,
    transparent_style,
)
from antijamming.ui.specs import (
    CARD_INNER_SPACING,
    COMPACT_SPACING,
    PAGE_MARGINS,
    PLOT_TILE_MARGINS,
    PRN_PLOT_COMPACT_MIN_HEIGHT,
    PRN_PLOT_MIN_HEIGHT,
    REALTIME_ALGORITHM_PLOT_COMPACT_MIN_HEIGHT,
    REALTIME_ALGORITHM_PLOT_MIN_HEIGHT,
    ROW_SPACING,
    SECTION_SPACING,
    SKYPLOT_COMPACT_MIN_SIZE,
    SKYPLOT_MAX_SIZE,
    SKYPLOT_MIN_SIZE,
    ZERO_MARGINS,
)
from antijamming.ui.state import (
    ReceiverProjection,
    satellite_id,
    satellite_sort_key,
    valid_float,
    valid_prn,
)

_OPERATOR_UI_REFRESH_MIN_MS = 50
_UI_HEARTBEAT_LOG_INTERVAL_S = 5.0
_UI_TIMER_STALL_WARN_S = 1.0
_UI_SLOW_REFRESH_WARN_MS = 150.0
_RESPONSIVE_COMPACT_WIDTH = 1000
_RESPONSIVE_STACK_WIDTH = 700
_RESPONSIVE_COMPACT_HEIGHT = 1100


# =============================================================================
# Realtime Main Window
# =============================================================================

# MainWindow is a composition layer. It owns labels, plots, and button state, but
# it does not own SDR hardware, GNSS-SDR processes, or DSP algorithms.

class MainWindow(QMainWindow):
    """Compose controls, plots, and GNSS status views for the realtime GUI."""

    def __init__(self, config: StreamConfig, worker: StreamWorker) -> None:
        super().__init__()
        self._cfg = config
        self._cfg.algorithm_mode = normalize_algorithm_mode(self._cfg.algorithm_mode)
        self._worker = worker
        self._log = logging.getLogger("antijamming.app")
        self._ui_log = logging.getLogger("antijamming.ui")
        self._stream_running = False
        self._stream_stopping = False
        self._stream_status_state = "idle"

        # Summary labels are operator-facing state. Detailed detector rows and
        # system labels derive from the same state.
        self._operator_title_label = QLabel("Anti-Jam GNSS Receiver")
        self._receiver_summary_label = QLabel(
            "Position not available | Satellites tracked 0 | Used for PVT 0 | Position --"
        )
        self._position_status_label = QLabel("PVT fix: NO FIX")
        self._latitude_label = QLabel("lat/long: -- / --")
        self._altitude_label = QLabel("altitude: --")
        self._receiver_time_label = QLabel("Time: --")
        self._dop_label = QLabel("HDOP/VDOP/PDOP/GDOP: -- / -- / -- / --")
        self._satellites_tracked_label = QLabel("Satellites tracked: 0")
        self._satellites_used_label = QLabel("Satellites used for PVT: 0")
        self._position_error_label = QLabel("PVT accuracy: --")
        self._enu_label = QLabel("UTM east/north: -- / --")
        self._system_health_label = QLabel("System health: Idle")
        self._antijam_summary_label = QLabel("Idle | Null inactive | IQ Δ --")
        self._stream_summary_label = QLabel("Idle\nOutput: GNSS IQ -> GNSS-SDR")
        self._jammer_detection_checkbox = QCheckBox("Jammer detection")
        self._stream_status_text = "Idle"
        self._receiver_fix_text = "Not available"
        self._receiver_accuracy_text = "--"
        self._stable_prn_count = 0
        self._used_in_pvt_count = 0
        self._current_tracking_prns: list[int] = []
        self._stable_prns: list[int] = []
        self._current_used_in_pvt_prns: list[int] = []
        self._raw_used_in_fix_prns: list[int] = []
        self._fresh_geometry_prns: list[int] = []
        self._tracking_without_geometry: list[int] = []
        self._latest_gnss_snapshot: dict[str, object] = {}
        self._receiver_projection = ReceiverProjection()
        self._prn_display_hold = self._receiver_projection.display_hold
        self._latest_pending_metrics: dict | None = None
        self._metrics_refresh_in_progress = False
        self._pending_metrics_received_monotonic_s = 0.0
        self._last_metrics_received_monotonic_s = 0.0
        self._last_metrics_applied_monotonic_s = 0.0
        self._last_ui_timer_monotonic_s = 0.0
        self._last_ui_heartbeat_log_s = 0.0
        self._metrics_received_count = 0
        self._metrics_applied_count = 0
        self._metrics_coalesced_drop_count = 0
        self._last_metrics_seq: int | None = None
        self._metrics_seq_gap_count = 0
        self._last_refresh_duration_ms = 0.0
        self._last_refresh_breakdown_ms: dict[str, float] = {}
        self._prn_chart_update_interval_s = max(
            0.0, float(self._cfg.prn_chart_update_interval_s)
        )
        self._skyplot_update_interval_s = max(
            0.0, float(self._cfg.skyplot_update_interval_s)
        )
        self._last_prn_chart_update_s = 0.0
        self._last_skyplot_update_s = 0.0
        self._last_prn_chart_signature: tuple[object, ...] | None = None
        self._last_skyplot_signature: tuple[object, ...] | None = None
        self._last_lcmv_plot_signature: tuple[object, ...] | None = None
        self._jammer_summary_text = "Idle"
        self._null_summary_text = "inactive"
        self._suppression_summary_text = "--"
        self._status_chip = QLabel("Stream: Idle")
        self._mode_chip = QLabel("")
        self._fix_chip = QLabel("Not available")
        self._jammer_chip = QLabel("Idle")
        self._doa_chip = QLabel("--")
        self._suppression_chip = QLabel("--")
        self._accuracy_chip = QLabel("--")
        self._stable_prns_chip = QLabel("0")
        self._used_in_pvt_chip = QLabel("0")
        self._null_chip = QLabel("Inactive")
        self._phase_chip = QLabel("Not checked")
        self._gnss_chip = QLabel("")
        self._jammer_state_label = QLabel("Interference: Idle")
        self._jammer_power_label = QLabel("Raw IQ power: --")
        self._jammer_power_rise_label = QLabel("Power rise: --")
        self._rx_clipping_label = QLabel("RX clipping: --")
        self._rx_peak_label = QLabel("IQ peak: --")
        self._rx_rms_label = QLabel("IQ RMS: --")
        self._rx_near_full_scale_label = QLabel("Near full scale: --")
        self._jammer_doa_label = QLabel("Direction candidate: --")
        self._jammer_null_label = QLabel("Nulling: Off")
        self._jammer_suppression_label = QLabel("Beamformed IQ reduction: --")
        self._output_path_label = QLabel("Output: GNSS IQ -> GNSS-SDR")
        self._system_info_label = QLabel("")
        self._prn_monitor: PocketPrnMonitor | None = None
        self._skyplot_monitor: SkyplotMonitor | None = None
        self._skyplot_section: QWidget | None = None
        self._skyplot_heading_label: QLabel | None = None
        self._skyplot_legend_label: QLabel | None = None
        self._receiver_overview_row: QBoxLayout | None = None
        self._operator_nav_bar: QTabBar | None = None

        self._run_btn = QPushButton("")
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(self._operator_ui_refresh_ms())
        self._metrics_timer.timeout.connect(self._flush_pending_metrics)

        self.setWindowTitle("Anti-Jamming Control")
        self._build_ui()
        self.maximize_to_available_screen()
        self._connect_signals()
        self._metrics_timer.start()
        self._refresh_system_info()
        self._set_fix_chip("Not available", ALERT)
        self._clear_antijam_operator_state()
        self._set_direction_chip("--", INFO)
        self._set_suppression_chip("--", INFO)
        self._set_accuracy_chip("--", INFO)
        self._set_phase_chip("Not checked", INFO)
        self._gnss_chip.hide()
        self._set_status_state("idle", "Idle")

    # -------------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(build_root_stylesheet())
        layout = QVBoxLayout(root)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(SECTION_SPACING)

        self._run_btn.setMinimumHeight(CONTROL_H)
        self._run_btn.setMinimumWidth(112)
        self._run_btn.clicked.connect(self._toggle_run)
        self._run_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._configure_jammer_detection_toggle()

        self._load_configured_phase_calibration()
        layout.addWidget(self._build_main_view(), stretch=1)

        self.setCentralWidget(root)
        self.statusBar().setStyleSheet(
            f"background:{BG_PANEL}; color:{FG_SOFT}; border-top:1px solid {BORDER};"
            "QStatusBar::item{border:none;}"
            "QLabel{border:none; background:transparent;}"
        )
        self.statusBar().hide()
        self._apply_responsive_layout(self.width(), self.height())

    # -------------------------------------------------------------------------
    # Status Panel Builders
    # -------------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        size = event.size()
        self._apply_responsive_layout(size.width(), size.height())

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self.maximize_to_available_screen)

    def maximize_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.setMaximumSize(available.size())
        self.setGeometry(available)
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        self._apply_responsive_layout(available.width(), available.height())

    def _apply_responsive_layout(self, width: int, height: int) -> None:
        compact = (
            int(width) < _RESPONSIVE_COMPACT_WIDTH
            or int(height) < _RESPONSIVE_COMPACT_HEIGHT
        )
        root_layout = self.centralWidget().layout() if self.centralWidget() is not None else None
        if root_layout is not None:
            root_layout.setContentsMargins(
                *(ZERO_MARGINS if compact else PAGE_MARGINS)
            )
            root_layout.setSpacing(COMPACT_SPACING if compact else SECTION_SPACING)
        row_direction = (
            QBoxLayout.Direction.TopToBottom
            if int(width) < _RESPONSIVE_STACK_WIDTH
            else QBoxLayout.Direction.LeftToRight
        )
        for row_name in (
            "_receiver_overview_row",
            "_algorithm_plots_row",
        ):
            row = getattr(self, row_name, None)
            if row is not None and row.direction() != row_direction:
                row.setDirection(row_direction)
        spacing = COMPACT_SPACING if compact else SECTION_SPACING
        for widget_name in ("_main_view", "_receiver_tab", "_antijam_tab"):
            widget = getattr(self, widget_name, None)
            layout = widget.layout() if widget is not None else None
            if layout is not None:
                layout.setSpacing(spacing)
        if self._prn_monitor is not None:
            self._prn_monitor.plot_widget.setMinimumHeight(
                max(
                    PRN_PLOT_COMPACT_MIN_HEIGHT,
                    min(PRN_PLOT_MIN_HEIGHT, int(max(1, height) * 0.14)),
                )
            )
        skyplot_size = 0
        if self._skyplot_monitor is not None:
            skyplot_size = self._skyplot_plot_side_for_layout(width, height, compact)
            for monitor in self._skyplot_monitors():
                monitor.setMinimumSize(skyplot_size, skyplot_size)
                monitor.setMaximumSize(skyplot_size, skyplot_size)
                monitor.set_plot_side(skyplot_size)
        self._constrain_receiver_card_to_content()
        plot_height = (
            REALTIME_ALGORITHM_PLOT_COMPACT_MIN_HEIGHT
            if compact
            else REALTIME_ALGORITHM_PLOT_MIN_HEIGHT
        )
        for plot_name in ("_doa_plot", "_lcmv_plot"):
            plot = getattr(self, plot_name, None)
            if plot is not None:
                plot.setMinimumHeight(
                    max(
                        REALTIME_ALGORITHM_PLOT_COMPACT_MIN_HEIGHT,
                        min(plot_height, int(max(1, height) * 0.16)),
                    )
                )
        main_view = getattr(self, "_main_view", None)
        if main_view is not None:
            main_view.setMinimumHeight(0)

    def _build_main_view(self) -> QWidget:
        self._main_view = QWidget()
        self._main_view.setObjectName("mainView")
        self._main_view.setStyleSheet(f"background:{BG_APP}; border:none;")
        self._main_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self._main_view)
        layout.setContentsMargins(*ZERO_MARGINS)
        layout.setSpacing(SECTION_SPACING)

        layout.addWidget(self._build_operator_header())
        layout.addWidget(self._build_operator_tab_switcher())
        layout.addWidget(self._build_operator_tabs(), stretch=1)
        return self._main_view

    def _build_operator_tab_switcher(self) -> QWidget:
        container = QWidget()
        container.setObjectName("operatorTabSwitcher")
        container.setStyleSheet(transparent_style())
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        layout.setContentsMargins(*ZERO_MARGINS)
        layout.setSpacing(0)
        container.setLayout(layout)

        self._operator_nav_bar = QTabBar()
        self._operator_nav_bar.setObjectName("operatorNavTabs")
        self._operator_nav_bar.setDocumentMode(True)
        self._operator_nav_bar.setDrawBase(False)
        self._operator_nav_bar.setExpanding(False)
        self._operator_nav_bar.setUsesScrollButtons(False)
        self._operator_nav_bar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._operator_nav_bar.setStyleSheet(operator_tabs_style())
        self._operator_nav_bar.addTab("Receiver")
        self._operator_nav_bar.addTab("Anti-Jam")
        self._operator_nav_bar.setCurrentIndex(0)

        layout.addStretch(1)
        layout.addWidget(self._operator_nav_bar, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        return container

    def _build_operator_tabs(self) -> QTabWidget:
        self._operator_tabs = QTabWidget()
        self._operator_tabs.setObjectName("operatorTabs")
        self._operator_tabs.setStyleSheet(operator_tabs_style())
        self._operator_tabs.setDocumentMode(True)
        self._operator_tabs.setUsesScrollButtons(False)
        self._operator_tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._operator_tabs.setAccessibleName("Operator views")
        self._operator_tabs.tabBar().hide()
        # Let the display height constrain the active page. Individual cards
        # already own their compact minimums; propagating the sum through the
        # tab widget can otherwise make the maximized window taller than the
        # available screen.
        self._operator_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self._operator_tabs.addTab(self._build_receiver_tab(), "Receiver")
        self._operator_tabs.addTab(self._build_antijam_tab(), "Anti-Jam")
        if self._operator_nav_bar is not None:
            self._operator_nav_bar.currentChanged.connect(
                self._operator_tabs.setCurrentIndex
            )
            self._operator_tabs.currentChanged.connect(
                self._operator_nav_bar.setCurrentIndex
            )
        return self._operator_tabs

    def _build_receiver_tab(self) -> QWidget:
        self._receiver_tab = QWidget()
        self._receiver_tab.setObjectName("receiverTab")
        self._receiver_tab.setAccessibleName("Receiver view")
        self._receiver_tab.setStyleSheet(f"background:{BG_APP}; border:none;")
        layout = QVBoxLayout(self._receiver_tab)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(SECTION_SPACING)
        layout.addWidget(
            self._build_receiver_overview_card(),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        layout.addWidget(self._build_prn_monitor_card(), stretch=1)
        return self._receiver_tab

    def _build_antijam_tab(self) -> QWidget:
        self._antijam_tab = QWidget()
        self._antijam_tab.setObjectName("antijamTab")
        self._antijam_tab.setAccessibleName("Anti-Jam processing view")
        self._antijam_tab.setStyleSheet(f"background:{BG_APP}; border:none;")
        layout = QVBoxLayout(self._antijam_tab)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(SECTION_SPACING)
        layout.addWidget(self._build_realtime_algorithm_plots(), stretch=1)
        return self._antijam_tab

    def _build_operator_header(self) -> QWidget:
        self._operator_header = QWidget()
        self._operator_header.setObjectName("operatorHeader")
        self._operator_header.setStyleSheet(transparent_style())
        self._operator_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._operator_title_label.setFrameShape(QLabel.Shape.NoFrame)
        self._operator_title_label.setStyleSheet(
            text_style(color=FG_TEXT, font_size=FONT_SIZE_TITLE, font_weight=800)
        )
        self._operator_title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._status_chip.setFrameShape(QLabel.Shape.NoFrame)
        self._status_chip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_chip.setStyleSheet(
            text_style(color=FG_TEXT, font_size=FONT_SIZE_EMPHASIS, font_weight=700)
        )
        header_layout = make_stretch_row(
            (
                (self._operator_title_label, 1),
                (self._status_chip, 0),
                (self._run_btn, 0),
            ),
            spacing=ROW_SPACING,
        )
        wrapper_layout = QVBoxLayout(self._operator_header)
        wrapper_layout.setContentsMargins(0, 0, 0, CARD_INNER_SPACING)
        wrapper_layout.setSpacing(CARD_INNER_SPACING)
        wrapper_layout.addLayout(header_layout)
        return self._operator_header

    # -------------------------------------------------------------------------
    # Plot and Monitor Builders
    # -------------------------------------------------------------------------

    def _build_realtime_algorithm_plots(self) -> QWidget:
        doa_plot, self._doa_curve, self._doa_marker = build_doa_plot(
            float(self._cfg.doa_min_deg),
            float(self._cfg.doa_max_deg),
        )
        lcmv_plot, self._lcmv_curve, self._lcmv_marker = build_lcmv_plot(
            float(self._cfg.doa_min_deg),
            float(self._cfg.doa_max_deg),
        )
        doa_plot.setMinimumHeight(REALTIME_ALGORITHM_PLOT_MIN_HEIGHT)
        lcmv_plot.setMinimumHeight(REALTIME_ALGORITHM_PLOT_MIN_HEIGHT)
        self._doa_plot = doa_plot
        self._lcmv_plot = lcmv_plot
        self._algorithm_plots_container, container_layout = make_panel("Signal Processing", "")
        self._algorithm_plots_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._algorithm_plots_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._algorithm_plots_row.setContentsMargins(*ZERO_MARGINS)
        self._algorithm_plots_row.setSpacing(ROW_SPACING)
        self._algorithm_plots_row.addWidget(
            self._build_plot_tile("DoA / MUSIC Spectrum", make_plot_body(doa_plot)),
            stretch=1,
        )
        self._algorithm_plots_row.addWidget(
            self._build_plot_tile("LCMV Array Pattern", make_plot_body(lcmv_plot)),
            stretch=1,
        )
        container_layout.addLayout(self._algorithm_plots_row, stretch=1)
        return self._algorithm_plots_container

    def _build_plot_tile(self, title: str, body: QWidget) -> QWidget:
        tile, layout = make_panel(title)
        tile.setStyleSheet(transparent_style())
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.setContentsMargins(*PLOT_TILE_MARGINS)
        layout.setSpacing(CARD_INNER_SPACING)
        layout.addWidget(body, stretch=1)
        return tile

    def _build_receiver_overview_card(self) -> QWidget:
        self._skyplot_monitor = SkyplotMonitor()
        self._receiver_card, layout = make_panel("Receiver Overview")
        self._receiver_card.setObjectName("operatorReceiverOverviewCard")
        self._receiver_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        receiver_labels = (
            self._position_status_label,
            self._latitude_label,
            self._altitude_label,
            self._receiver_time_label,
            self._dop_label,
            self._satellites_tracked_label,
            self._satellites_used_label,
            self._position_error_label,
            self._enu_label,
        )
        antijam_labels = (
            self._jammer_detection_checkbox,
            self._jammer_state_label,
            self._jammer_doa_label,
            self._jammer_power_rise_label,
            self._jammer_power_label,
            self._rx_clipping_label,
            self._rx_peak_label,
            self._rx_rms_label,
            self._rx_near_full_scale_label,
            self._jammer_null_label,
            self._jammer_suppression_label,
            self._system_health_label,
        )

        self._receiver_overview_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._receiver_overview_row.setContentsMargins(*ZERO_MARGINS)
        self._receiver_overview_row.setSpacing(ROW_SPACING)
        self._receiver_overview_row.addWidget(
            self._build_skyplot_section(),
            stretch=1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        self._receiver_overview_row.addWidget(
            self._build_status_section("Receiver", receiver_labels),
            stretch=1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        self._receiver_overview_row.addWidget(
            self._build_status_section("Anti-Jam", antijam_labels),
            stretch=1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        layout.addLayout(self._receiver_overview_row)
        return self._receiver_card

    def _build_skyplot_section(self) -> QWidget:
        self._skyplot_section = QWidget()
        self._skyplot_section.setStyleSheet(transparent_style())
        self._skyplot_section.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        self._skyplot_section.setMinimumWidth(SKYPLOT_MIN_SIZE)
        layout = QVBoxLayout(self._skyplot_section)
        layout.setContentsMargins(*ZERO_MARGINS)
        layout.setSpacing(CARD_INNER_SPACING)

        self._skyplot_heading_label = QLabel("Satellite Sky View")
        self._skyplot_heading_label.setFrameShape(QLabel.Shape.NoFrame)
        self._skyplot_heading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._skyplot_heading_label.setStyleSheet(
            text_style(color=FG_TEXT, font_weight=800)
        )
        layout.addWidget(self._skyplot_heading_label)
        layout.addWidget(
            self._skyplot_monitor,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self._skyplot_legend_label = QLabel(
            "Legend: solid green = used for PVT; green outline = tracked"
        )
        self._skyplot_legend_label.setFrameShape(QLabel.Shape.NoFrame)
        self._skyplot_legend_label.setWordWrap(True)
        self._skyplot_legend_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._skyplot_legend_label.setStyleSheet(text_style(color=FG_SOFT))
        layout.addWidget(self._skyplot_legend_label)
        return self._skyplot_section

    def _skyplot_plot_side_for_layout(
        self,
        window_width: int,
        window_height: int,
        compact: bool,
    ) -> int:
        minimum = SKYPLOT_COMPACT_MIN_SIZE if compact else SKYPLOT_MIN_SIZE
        fallback = int(min(max(1, window_width), max(1, window_height)) * 0.20)
        section = self._skyplot_section
        if section is None:
            return max(minimum, min(SKYPLOT_MAX_SIZE, fallback))

        card_layout = (
            self._receiver_card.layout() if self._receiver_card is not None else None
        )
        if card_layout is not None:
            card_layout.activate()

        section_width = (
            section.width() if section.width() > 0 else section.sizeHint().width()
        )
        section_width = max(1, section_width)
        row_height = (
            self._receiver_overview_row.geometry().height()
            if self._receiver_overview_row is not None
            and self._receiver_overview_row.geometry().height() > 0
            else 0
        )
        section_height = row_height if row_height > 0 else section.height()
        if section_height <= 0:
            section_height = section.sizeHint().height()
        section_height = max(1, section_height)

        heading_h = self._label_height_for_width(
            self._skyplot_heading_label,
            section_width,
        )
        legend_h = self._label_height_for_width(
            self._skyplot_legend_label,
            section_width,
        )
        layout = section.layout()
        spacing = layout.spacing() if layout is not None else COMPACT_SPACING
        spacing_count = max(0, (layout.count() - 1) if layout is not None else 2)
        vertical_reserved = heading_h + legend_h + max(0, spacing) * spacing_count
        available_height = max(1, section_height - vertical_reserved)
        side = min(max(1, section_width), available_height, SKYPLOT_MAX_SIZE)
        return max(minimum, min(SKYPLOT_MAX_SIZE, int(side)))

    @staticmethod
    def _label_height_for_width(label: QLabel | None, width: int) -> int:
        if label is None:
            return 0
        width = max(1, int(width))
        if label.hasHeightForWidth():
            height = label.heightForWidth(width)
            if height > 0:
                return height
        return label.sizeHint().height()

    def _style_status_labels(self, labels: tuple[QWidget, ...], headings: tuple[QLabel, ...]) -> None:
        for label in labels:
            if isinstance(label, QLabel):
                label.setFrameShape(QLabel.Shape.NoFrame)
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet(text_style(color=FG_TEXT))
        for label in headings:
            label.setStyleSheet(text_style(color=FG_TEXT, font_weight=800))

    def _build_status_section(self, heading_text: str, labels: tuple[QWidget, ...]) -> QWidget:
        section = QWidget()
        section.setStyleSheet(transparent_style())
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        heading = QLabel(heading_text)
        self._style_status_labels(labels, (heading,))
        column = QVBoxLayout(section)
        column.setContentsMargins(*ZERO_MARGINS)
        column.setSpacing(COMPACT_SPACING)
        column.addWidget(heading)
        for label in labels:
            column.addWidget(label)
        return section

    def _constrain_receiver_card_to_content(self) -> None:
        """Keep the receiver overview at its natural content height."""
        card = getattr(self, "_receiver_card", None)
        if card is None:
            return
        card.layout().activate()
        card.setMinimumHeight(0)
        card.setMaximumHeight(max(1, card.sizeHint().height()))

    def _build_prn_monitor_card(self) -> QWidget:
        if self._prn_monitor is None:
            self._prn_monitor = PocketPrnMonitor()
        self._prn_card, layout = make_panel("Stable Tracking C/N0")
        self._prn_card.setObjectName("operatorPrnMonitorCard")
        self._prn_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(make_plot_body(self._prn_monitor), stretch=1)
        return self._prn_card

    # -------------------------------------------------------------------------
    # Signal Wiring and Status Labels
    # -------------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.status.connect(self._on_status)
        self._worker.failed.connect(self._on_status)
        finished = getattr(self._worker, "finished", None)
        if finished is not None and hasattr(finished, "connect"):
            finished.connect(self._on_worker_finished)

    def _set_status_row(self, label: QLabel, title: str, value: str, color: str) -> None:
        label.setText(
            f"{escape(title)}: "
            f"<span style=\"color:{color}; font-weight:800;\">{escape(value)}</span>"
        )

    def _set_status_chip(self, message: str, color: str) -> None:
        self._stream_status_text = message
        self._set_status_row(self._status_chip, "Stream", message, color)
        self._refresh_system_health_row()
        self._refresh_operator_summaries()
        self._refresh_system_info()

    def _set_summary_chip(
        self,
        summary_attr: str,
        chip: QLabel,
        detail_label: QLabel,
        detail_title: str,
        value: str,
        color: str,
    ) -> None:
        setattr(self, summary_attr, value)
        chip.setText(value)
        self._set_status_row(detail_label, detail_title, value, color)
        self._refresh_operator_summaries()

    def _set_fix_chip(
        self,
        fix_type: str,
        color: str,
        detail_value: str | None = None,
    ) -> None:
        setattr(self, "_receiver_fix_text", fix_type)
        self._fix_chip.setText(fix_type)
        self._set_status_row(
            self._position_status_label,
            "PVT fix",
            detail_value if detail_value is not None else fix_type,
            color,
        )
        self._refresh_operator_summaries()

    def _set_accuracy_chip(
        self,
        value: str,
        color: str,
        title: str = "PVT accuracy",
    ) -> None:
        self._set_summary_chip(
            "_receiver_accuracy_text", self._accuracy_chip, self._position_error_label,
            title, value, color
        )

    def _set_jammer_chip(self, value: str, color: str) -> None:
        self._set_summary_chip(
            "_jammer_summary_text", self._jammer_chip, self._jammer_state_label, "Jammer status", value, color
        )

    def _set_suppression_chip(self, value: str, color: str) -> None:
        self._set_summary_chip(
            "_suppression_summary_text", self._suppression_chip, self._jammer_suppression_label,
            "Beamformed IQ reduction", value, color
        )

    def _set_direction_chip(self, value: str, color: str = INFO) -> None:
        del color
        self._doa_chip.setText(value)

    def _set_phase_chip(self, value: str, color: str) -> None:
        del color
        self._phase_chip.setText(value)

    def _set_prn_counts(
        self,
        stable_count: int,
        used_in_pvt_count: int,
        used_in_pvt_satellites: list[str] | None = None,
    ) -> None:
        self._stable_prn_count = max(0, int(stable_count))
        self._used_in_pvt_count = max(0, int(used_in_pvt_count))
        self._stable_prns_chip.setText(str(self._stable_prn_count))
        self._used_in_pvt_chip.setText(str(self._used_in_pvt_count))
        self._set_status_row(
            self._satellites_tracked_label,
            "Satellites tracked",
            str(self._stable_prn_count),
            SUCCESS if self._stable_prn_count > 0 else INFO,
        )
        self._set_status_row(
            self._satellites_used_label,
            "Satellites used for PVT",
            self._format_pvt_satellite_value(
                self._used_in_pvt_count,
                used_in_pvt_satellites or [],
            ),
            SUCCESS if self._used_in_pvt_count > 0 else INFO,
        )
        self._refresh_operator_summaries()

    def _format_pvt_satellite_value(self, count: int, satellites: list[str]) -> str:
        count = max(0, int(count))
        unique = sorted(
            {str(sat).strip() for sat in satellites if str(sat).strip()},
            key=satellite_sort_key,
        )
        if not unique:
            return str(count)
        return f"{count} ({', '.join(unique)})"

    def _refresh_system_health_row(self) -> None:
        if self._stream_status_state == "running":
            value = "OK"
            color = SUCCESS
        elif self._stream_status_state == "degraded":
            value = "Degraded"
            color = WARNING
        elif self._stream_status_state == "error":
            value = "Error"
            color = ALERT
        elif self._stream_status_state in {"starting", "stopping"}:
            value = self._stream_status_state.capitalize()
            color = WARNING
        else:
            value = "Idle"
            color = INFO
        self._set_status_row(self._system_health_label, "System health", value, color)

    def _set_null_summary(self, active: bool) -> None:
        self._null_summary_text = "active" if active else "inactive"
        self._null_chip.setText("Active" if active else "Inactive")
        self._refresh_operator_summaries()

    def _refresh_operator_summaries(self) -> None:
        self._receiver_summary_label.setText(
            f"Position {self._receiver_fix_text.lower()} | "
            f"Satellites tracked {self._stable_prn_count} | "
            f"Used for PVT {self._used_in_pvt_count} | "
            f"Error {self._receiver_accuracy_text}"
        )
        self._antijam_summary_label.setText(
            f"{self._jammer_summary_text} | Null {self._null_summary_text} | "
            f"IQ Δ {self._suppression_summary_text}"
        )
        self._stream_summary_label.setText(
            f"{self._stream_status_text}\nOutput: {self._configured_feed_label()} -> GNSS-SDR"
        )

    def _refresh_system_info(self) -> None:
        self._system_info_label.setText(
            f"Stream: {self._stream_status_text}\n"
            "Algorithm: LCMV\n"
            "Beamforming: Active\n"
            f"GNSS-SDR handoff: {self._configured_feed_label()}\n"
            f"{self._gnss_sdr_status_text()}"
        )

    def _set_status_state(self, state: str, message: str) -> None:
        self._stream_status_state = state
        if state == "starting":
            self._stream_running = True
            self._stream_stopping = False
            self._run_btn.setText("Starting...")
            self._run_btn.setEnabled(False)
            self._run_btn.setStyleSheet(disabled_button_style())
            self._set_status_chip(message, INFO)
        elif state == "running":
            self._stream_running = True
            self._stream_stopping = False
            self._run_btn.setText("■ Stop")
            self._run_btn.setEnabled(True)
            self._run_btn.setStyleSheet(stop_button_style())
            self._set_status_chip(message, SUCCESS)
        elif state == "degraded":
            self._stream_running = True
            self._stream_stopping = False
            self._run_btn.setText("■ Stop")
            self._run_btn.setEnabled(True)
            self._run_btn.setStyleSheet(stop_button_style())
            self._set_status_chip(message, WARNING)
        elif state == "stopping":
            self._stream_running = True
            self._stream_stopping = True
            self._run_btn.setText("Stopping...")
            self._run_btn.setEnabled(False)
            self._run_btn.setStyleSheet(disabled_button_style())
            self._set_status_chip(message, WARNING)
        elif state == "error":
            self._stream_running = False
            self._stream_stopping = False
            self._run_btn.setText("▶ Start")
            self._run_btn.setEnabled(True)
            self._run_btn.setStyleSheet(start_button_style())
            self._set_status_chip(message, ALERT)
        else:
            self._stream_running = False
            self._stream_stopping = False
            self._run_btn.setText("▶ Start")
            self._run_btn.setEnabled(True)
            self._run_btn.setStyleSheet(start_button_style())
            self._set_status_chip(message, INFO)
        if state in {"idle", "error"}:
            self._clear_gnss_operator_state()
            self._clear_antijam_operator_state()

    def _clear_antijam_operator_state(self) -> None:
        self._set_jammer_chip("Idle", WARNING)
        self._set_direction_chip("--", INFO)
        self._set_suppression_chip("--", INFO)
        self._set_null_summary(False)
        self._set_status_row(self._jammer_doa_label, "Direction candidate", "--", INFO)
        self._set_status_row(self._jammer_null_label, "Nulling", "Off", INFO)
        self._set_status_row(self._jammer_power_rise_label, "Power rise", "--", INFO)
        self._set_status_row(self._jammer_power_label, "Raw IQ power", "--", INFO)
        self._set_status_row(self._rx_clipping_label, "RX clipping", "--", INFO)
        self._set_status_row(self._rx_peak_label, "IQ peak", "--", INFO)
        self._set_status_row(self._rx_rms_label, "IQ RMS", "--", INFO)
        self._set_status_row(self._rx_near_full_scale_label, "Near full scale", "--", INFO)

    # -------------------------------------------------------------------------
    # Fixed Product Mode Status
    # -------------------------------------------------------------------------

    def _configure_jammer_detection_toggle(self) -> None:
        self._jammer_detection_checkbox.setObjectName("jammerDetectionToggle")
        self._jammer_detection_checkbox.setAccessibleName("Jammer detection")
        self._jammer_detection_checkbox.setMinimumHeight(CONTROL_H)
        self._jammer_detection_checkbox.setChecked(bool(self._cfg.jammer_detection_enabled))
        self._jammer_detection_checkbox.setStyleSheet(
            text_style(color=FG_TEXT, font_weight=700)
        )
        self._jammer_detection_checkbox.toggled.connect(
            self._on_jammer_detection_toggled
        )

    def _on_jammer_detection_toggled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        self._cfg.jammer_detection_enabled = normalized
        setter = getattr(self._worker, "set_jammer_detection_enabled", None)
        if callable(setter):
            setter(normalized)
        self._log.info("UI action: jammer_detection_enabled=%s", normalized)
        self._refresh_system_info()

    def _configured_feed_label(self) -> str:
        if not bool(self._cfg.gnss_sdr_enable):
            return "GNSS-SDR disabled"
        return "GNSS Beamformed Continuous"

    # -------------------------------------------------------------------------
    # Run Control and Calibration Loading
    # -------------------------------------------------------------------------

    def _toggle_run(self) -> None:
        if self._stream_running:
            self._log.info("UI action: stop requested")
            self._stop_worker()
            return
        self._reset_ui_health_counters()
        self._set_status_state("starting", "Starting")
        self._refresh_system_info()
        self._log.info(
            "UI action: start requested gnss_feed=%s gnss_sdr=%s "
            "beamforming=%s jammer_detection=%s algorithm_mode=%s force_null=%s sample_rate=%.3f_msps "
            "center_freq=%.3f_mhz",
            self._configured_feed_label(),
            bool(self._cfg.gnss_sdr_enable),
            True,
            bool(self._cfg.jammer_detection_enabled),
            str(self._cfg.algorithm_mode),
            bool(self._cfg.lcmv_force_null),
            float(self._cfg.sample_rate) / 1e6,
            float(self._cfg.center_freq_hz) / 1e6,
        )
        self._worker.start()

    def start_stream(self) -> None:
        """Start the realtime stream from launcher automation."""
        if self._stream_running or self._worker.isRunning():
            self._log.info("UI automation: start skipped because stream is already running")
            return
        self._log.info("UI automation: start requested")
        self._toggle_run()

    def stop_stream(self, reason: str = "automated stop") -> None:
        """Stop the realtime stream from launcher automation."""
        if not self._stream_running and not self._worker.isRunning():
            self._log.info("UI automation: stop skipped because stream is not running")
            return
        self._log.info("UI automation: stop requested: %s", reason)
        self._stop_worker()

    def _reset_ui_health_counters(self) -> None:
        self._latest_pending_metrics = None
        self._pending_metrics_received_monotonic_s = 0.0
        self._last_metrics_received_monotonic_s = 0.0
        self._last_metrics_applied_monotonic_s = 0.0
        self._last_ui_timer_monotonic_s = 0.0
        self._last_ui_heartbeat_log_s = 0.0
        self._metrics_received_count = 0
        self._metrics_applied_count = 0
        self._metrics_coalesced_drop_count = 0
        self._last_metrics_seq = None
        self._metrics_seq_gap_count = 0
        self._last_refresh_duration_ms = 0.0
        self._last_prn_chart_update_s = 0.0
        self._last_skyplot_update_s = 0.0
        self._last_prn_chart_signature = None
        self._last_skyplot_signature = None
        self._last_lcmv_plot_signature = None
        self._ui_log.info(
            "ui health counters reset: refresh_interval_ms=%d heartbeat_interval_s=%.1f",
            self._metrics_timer.interval(),
            _UI_HEARTBEAT_LOG_INTERVAL_S,
        )

    def _operator_ui_refresh_ms(self) -> int:
        configured_ms = int(round(max(0.0, float(self._cfg.ui_update_interval_s)) * 1000.0))
        return max(_OPERATOR_UI_REFRESH_MIN_MS, configured_ms)

    def _load_configured_phase_calibration(self) -> None:
        path = self._cfg.phase_calibration_file
        if path is None:
            return
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (REPO_ROOT / resolved).resolve()
        if not resolved.exists():
            self._log.warning("Configured phase correction file does not exist: %s", resolved)
            return
        try:
            correction = load_phase_correction_vector(resolved)
        except Exception as exc:
            self._log.warning("Could not load configured phase correction %s: %s", resolved, exc)
        else:
            self._cfg.phase_calibration_file = resolved
            self._cfg.phase_correction_vector = tuple(complex(v) for v in correction)

    def _stop_worker(self) -> None:
        self._set_status_state("stopping", "Stopping")
        self.statusBar().showMessage("Stopping stream...", 2000)
        if hasattr(self._worker, "stop"):
            self._worker.stop("normal stop")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._log.info("GUI close requested")
        if hasattr(self._worker, "stop"):
            try:
                self._worker.stop("GUI close")
            except TypeError:
                self._worker.stop()
        super().closeEvent(event)

    # -------------------------------------------------------------------------
    # Worker Event Handlers
    # -------------------------------------------------------------------------

    def _on_status(self, message: str) -> None:
        lower = message.lower()
        if self._stream_status_state == "error" and (
            "stopping" in lower or "finalizing" in lower or "stopped" in lower
        ):
            self.statusBar().showMessage(message, 5000)
            return
        if "handoff paused" in lower:
            self._set_status_state("degraded", message)
        elif "started" in lower:
            self._set_status_state("running", "Streaming")
        elif (
            "preparing" in lower
            or "initializing" in lower
            or lower.startswith("starting ")
            or "resuming" in lower
        ):
            self._set_status_state("starting", message)
        elif "stopping" in lower or "finalizing" in lower:
            self._set_status_state("stopping", message)
        elif "stopped" in lower:
            if self._worker_is_running():
                self._set_status_state("stopping", "Finalizing stop")
            else:
                self._set_status_state("idle", "Stopped")
        elif "failed" in lower or "overflow" in lower or "error" in lower:
            self._set_status_state("error", message)
        self.statusBar().showMessage(message, 5000)

    def _worker_is_running(self) -> bool:
        is_running = getattr(self._worker, "isRunning", None)
        if not callable(is_running):
            return False
        try:
            return bool(is_running())
        except RuntimeError:
            return False

    def _on_worker_finished(self) -> None:
        if self._stream_status_state != "error":
            self._set_status_state("idle", "Stopped")

    def _on_data_ready(self, metrics: dict) -> None:
        if not self._stream_running:
            self._apply_metrics(metrics)
            return
        now = time.monotonic()
        self._metrics_received_count += 1
        self._last_metrics_received_monotonic_s = now
        if self._latest_pending_metrics is not None:
            self._metrics_coalesced_drop_count += 1
        self._record_metrics_sequence(metrics)
        self._latest_pending_metrics = metrics
        self._pending_metrics_received_monotonic_s = now

    def _flush_pending_metrics(self) -> None:
        now = time.monotonic()
        self._log_ui_timer_gap(now)
        metrics = self._latest_pending_metrics
        if metrics is None:
            self._maybe_log_ui_heartbeat(now)
            return
        if self._metrics_refresh_in_progress:
            self._ui_log.warning(
                "ui refresh skipped: previous refresh still running pending_age_ms=%.1f",
                (now - self._pending_metrics_received_monotonic_s) * 1000.0,
            )
            return
        self._latest_pending_metrics = None
        pending_age_ms = (now - self._pending_metrics_received_monotonic_s) * 1000.0
        self._apply_metrics(metrics)
        self._last_metrics_applied_monotonic_s = time.monotonic()
        self._metrics_applied_count += 1
        if self._last_refresh_duration_ms >= _UI_SLOW_REFRESH_WARN_MS:
            self._ui_log.warning(
                "ui refresh slow: duration_ms=%.1f pending_age_ms=%.1f "
                "received=%d applied=%d coalesced=%d seq_gaps=%d %s",
                self._last_refresh_duration_ms,
                pending_age_ms,
                self._metrics_received_count,
                self._metrics_applied_count,
                self._metrics_coalesced_drop_count,
                self._metrics_seq_gap_count,
                self._format_ui_refresh_breakdown(),
            )
        self._maybe_log_ui_heartbeat(time.monotonic())

    def _apply_metrics(self, metrics: dict) -> None:
        t0 = time.monotonic()
        self._metrics_refresh_in_progress = True
        try:
            self._apply_metrics_unchecked(metrics)
        finally:
            self._metrics_refresh_in_progress = False
            self._last_refresh_duration_ms = (time.monotonic() - t0) * 1000.0

    def _apply_metrics_unchecked(self, metrics: dict) -> None:
        breakdown: dict[str, float] = {}
        t0 = time.monotonic()
        self._refresh_phase_monitor(metrics)
        breakdown["phase_ms"] = (time.monotonic() - t0) * 1000.0
        t0 = time.monotonic()
        self._refresh_algorithm_plots(metrics)
        breakdown["algorithm_ms"] = (time.monotonic() - t0) * 1000.0
        t0 = time.monotonic()
        self._refresh_gnss_monitors(metrics)
        breakdown["gnss_ms"] = (time.monotonic() - t0) * 1000.0
        t0 = time.monotonic()
        self._refresh_doa_chips(metrics)
        breakdown["chips_ms"] = (time.monotonic() - t0) * 1000.0
        t0 = time.monotonic()
        self._refresh_rx_signal_health(metrics)
        breakdown["rx_health_ms"] = (time.monotonic() - t0) * 1000.0
        self._last_refresh_breakdown_ms = breakdown

    def _record_metrics_sequence(self, metrics: dict) -> None:
        seq_raw = metrics.get("ui_metrics_seq") if isinstance(metrics, dict) else None
        if not isinstance(seq_raw, (int, np.integer)):
            return
        seq = int(seq_raw)
        if self._last_metrics_seq is not None and seq > self._last_metrics_seq + 1:
            self._metrics_seq_gap_count += seq - self._last_metrics_seq - 1
        self._last_metrics_seq = seq

    def _log_ui_timer_gap(self, now: float) -> None:
        last = self._last_ui_timer_monotonic_s
        self._last_ui_timer_monotonic_s = now
        if last <= 0.0:
            return
        gap_s = now - last
        expected_gap_s = self._operator_ui_refresh_ms() / 1000.0
        stall_warn_s = max(_UI_TIMER_STALL_WARN_S, expected_gap_s * 2.0)
        if gap_s >= stall_warn_s:
            self._ui_log.warning(
                "ui timer stall: gap_ms=%.1f pending=%s received=%d applied=%d "
                "coalesced=%d last_refresh_ms=%.1f state=%s",
                gap_s * 1000.0,
                self._latest_pending_metrics is not None,
                self._metrics_received_count,
                self._metrics_applied_count,
                self._metrics_coalesced_drop_count,
                self._last_refresh_duration_ms,
                self._stream_status_state,
            )

    def _maybe_log_ui_heartbeat(self, now: float) -> None:
        if (now - self._last_ui_heartbeat_log_s) < _UI_HEARTBEAT_LOG_INTERVAL_S:
            return
        self._last_ui_heartbeat_log_s = now
        pending_age_ms = (
            (now - self._pending_metrics_received_monotonic_s) * 1000.0
            if self._latest_pending_metrics is not None and self._pending_metrics_received_monotonic_s > 0.0
            else 0.0
        )
        since_received_ms = (
            (now - self._last_metrics_received_monotonic_s) * 1000.0
            if self._last_metrics_received_monotonic_s > 0.0
            else -1.0
        )
        since_applied_ms = (
            (now - self._last_metrics_applied_monotonic_s) * 1000.0
            if self._last_metrics_applied_monotonic_s > 0.0
            else -1.0
        )
        prn_bars = (
            len(getattr(self._prn_monitor, "_displayed_prns", []))
            if self._prn_monitor is not None
            else 0
        )
        sky_markers = (
            len(getattr(self._skyplot_monitor, "_plotted_prns", []))
            if self._skyplot_monitor is not None
            else 0
        )
        self._ui_log.info(
            "ui heartbeat: state=%s running=%s pending=%s pending_age_ms=%.1f "
            "since_received_ms=%.1f since_applied_ms=%.1f received=%d applied=%d "
            "coalesced=%d seq=%s seq_gaps=%d last_refresh_ms=%.1f "
            "stable_prns=%d used_pvt=%d prn_bars=%d sky_markers=%d %s",
            self._stream_status_state,
            self._stream_running,
            self._latest_pending_metrics is not None,
            pending_age_ms,
            since_received_ms,
            since_applied_ms,
            self._metrics_received_count,
            self._metrics_applied_count,
            self._metrics_coalesced_drop_count,
            "--" if self._last_metrics_seq is None else self._last_metrics_seq,
            self._metrics_seq_gap_count,
            self._last_refresh_duration_ms,
            self._stable_prn_count,
            self._used_in_pvt_count,
            prn_bars,
            sky_markers,
            self._format_ui_refresh_breakdown(),
        )

    def _format_ui_refresh_breakdown(self) -> str:
        if not self._last_refresh_breakdown_ms:
            return "breakdown=--"
        return "breakdown_" + " ".join(
            f"{key}={value:.1f}" for key, value in self._last_refresh_breakdown_ms.items()
        )

    # -------------------------------------------------------------------------
    # Phase and Algorithm Plot Refresh
    # -------------------------------------------------------------------------

    def _refresh_phase_monitor(self, metrics: dict) -> None:
        if "phase_offsets_calibrated_deg" not in metrics:
            return
        offsets = self._finite_vector(metrics.get("phase_offsets_calibrated_deg"))
        if offsets.size <= 1:
            return
        finite = offsets[1:][np.isfinite(offsets[1:])]
        if finite.size == 0:
            return
        residual = float(np.max(np.abs(finite)))
        self._set_phase_chip(
            f"After max {residual:.1f}°",
            SUCCESS if self._cfg.phase_correction_vector is not None else WARNING,
        )

    def _refresh_algorithm_plots(self, metrics: dict) -> None:
        doa_spectrum = self._finite_vector(metrics.get("doa_spectrum"))
        if doa_spectrum.size == 0:
            doa_spectrum = self._finite_vector(metrics.get("music_spectrum"))
        if doa_spectrum.size > 1:
            scan = self._scan_angles_for_size(doa_spectrum.size)
            bearing_scan, order = self._bearing_axis_for_internal_scan(scan)
            self._doa_curve.setData(bearing_scan, doa_spectrum[order])

        doa_deg = valid_float(metrics.get("doa_deg"))
        if doa_deg is not None:
            self._doa_marker.setValue(self._internal_angle_to_bearing(doa_deg))

        jammer = metrics.get("jammer")
        jammer_detected = isinstance(jammer, dict) and bool(jammer.get("detected", False))
        lcmv_null_active = bool(metrics.get("lcmv_null_active", False))
        lcmv_pattern = self._finite_vector(metrics.get("lcmv_pattern_db"))
        if jammer_detected and lcmv_pattern.size > 1:
            display_pattern = np.maximum(lcmv_pattern, LCMV_Y_RANGE[0])
            scan = self._scan_angles_for_size(display_pattern.size)
            bearing_scan, order = self._bearing_axis_for_internal_scan(scan)
            display_pattern_bearing = display_pattern[order]
            signature = (
                True,
                lcmv_null_active,
                self._rounded_float(self._metric_doa_bearing(metrics), 2),
                self._rounded_float(float(np.nanmin(display_pattern_bearing)), 2),
                self._rounded_float(float(np.nanmax(display_pattern_bearing)), 2),
            )
            if signature != self._last_lcmv_plot_signature:
                self._lcmv_curve.setData(bearing_scan, display_pattern_bearing)
                self._last_lcmv_plot_signature = signature
            finite = np.isfinite(display_pattern_bearing)
            if lcmv_null_active and np.any(finite):
                null_index = int(np.nanargmin(display_pattern_bearing))
                self._lcmv_marker.setValue(float(bearing_scan[null_index]))
                self._lcmv_marker.setVisible(True)
            else:
                self._lcmv_marker.setVisible(False)
        else:
            self._lcmv_marker.setVisible(False)
            signature = (False,)
            if signature != self._last_lcmv_plot_signature:
                self._lcmv_curve.setData([], [])
                self._last_lcmv_plot_signature = signature

    # -------------------------------------------------------------------------
    # GNSS Monitor Refresh
    # -------------------------------------------------------------------------

    def _skyplot_monitors(self) -> tuple[QWidget, ...]:
        return tuple(
            monitor
            for monitor in (self._skyplot_monitor,)
            if monitor is not None
        )

    def _update_skyplot_monitors(
        self,
        sky_entries: list[dict[str, object]],
        unplaced_tracking_prns: list[int] | None = None,
    ) -> None:
        for monitor in self._skyplot_monitors():
            monitor.update_snapshot(sky_entries, unplaced_tracking_prns)

    def _clear_gnss_operator_state(self) -> None:
        self._latest_gnss_snapshot = {}
        self._receiver_projection.clear_display_hold()
        if self._prn_monitor is not None:
            self._prn_monitor.update_snapshot([])
        self._update_skyplot_monitors([])
        self._current_tracking_prns = []
        self._stable_prns = []
        self._current_used_in_pvt_prns = []
        self._raw_used_in_fix_prns = []
        self._fresh_geometry_prns = []
        self._tracking_without_geometry = []
        self._set_prn_counts(0, 0)
        self._set_fix_chip("NO FIX", ALERT)
        self._set_accuracy_chip("--", INFO)
        self._set_receiver_pvt_details({}, False)
        self._last_prn_chart_signature = None
        self._last_skyplot_signature = None
        self._refresh_system_info()

    def _display_update_due(self, last_update_s: float, interval_s: float, now: float) -> bool:
        if not self._stream_running:
            return True
        if float(interval_s) <= 0.0:
            return True
        if float(last_update_s) <= 0.0:
            return True
        return (now - float(last_update_s)) >= float(interval_s)

    def _refresh_gnss_monitors(self, metrics: dict) -> None:
        if self._prn_monitor is None or not self._skyplot_monitors():
            return
        gnss_snapshot = metrics.get("gnss_snapshot", {}) if isinstance(metrics, dict) else {}
        if not isinstance(gnss_snapshot, dict) or not gnss_snapshot:
            self._latest_gnss_snapshot = {}
            self._receiver_projection.clear_display_hold()
            self._prn_monitor.update_snapshot([])
            self._update_skyplot_monitors([])
            self._last_prn_chart_signature = None
            self._last_skyplot_signature = None
            self._set_prn_counts(0, 0)
            self._set_fix_chip("NO FIX", ALERT)
            self._set_accuracy_chip("--", INFO)
            self._set_receiver_pvt_details({}, False)
            self._refresh_system_info()
            return

        self._latest_gnss_snapshot = dict(gnss_snapshot)
        now = time.monotonic()
        receiver_state = self._receiver_projection.build_view_state(
            gnss_snapshot,
            now=now,
        )
        self._current_tracking_prns = receiver_state.current_tracking_prns
        self._stable_prns = receiver_state.stable_prns
        self._current_used_in_pvt_prns = receiver_state.current_used_in_pvt_prns
        self._raw_used_in_fix_prns = receiver_state.raw_used_in_fix_prns
        self._fresh_geometry_prns = receiver_state.fresh_geometry_prns
        self._tracking_without_geometry = receiver_state.tracking_without_geometry

        if self._display_update_due(
            self._last_prn_chart_update_s,
            self._prn_chart_update_interval_s,
            now,
        ):
            signature = self._prn_chart_signature(receiver_state.prn_entries)
            if signature != self._last_prn_chart_signature:
                self._prn_monitor.update_snapshot(receiver_state.prn_entries)
                self._last_prn_chart_signature = signature
            self._last_prn_chart_update_s = now
        if self._display_update_due(
            self._last_skyplot_update_s,
            self._skyplot_update_interval_s,
            now,
        ):
            signature = self._skyplot_signature(
                receiver_state.sky_entries,
                receiver_state.tracking_without_geometry,
            )
            if signature != self._last_skyplot_signature:
                self._update_skyplot_monitors(
                    receiver_state.sky_entries,
                    receiver_state.tracking_without_geometry,
                )
                self._last_skyplot_signature = signature
            self._last_skyplot_update_s = now
        self._set_prn_counts(
            len(receiver_state.stable_satellite_ids),
            receiver_state.used_for_pvt_count,
            receiver_state.raw_used_in_fix_satellites,
        )
        self._refresh_receiver_fix_and_accuracy(
            gnss_snapshot,
            receiver_state.pvt_current,
            receiver_state.used_for_pvt_count,
        )
        self._set_receiver_pvt_details(
            gnss_snapshot,
            receiver_state.pvt_current,
        )
        self._refresh_system_info()

    def _refresh_receiver_fix_and_accuracy(
        self,
        gnss_snapshot: dict[str, object],
        pvt_current: bool,
        used_for_pvt_count: int,
    ) -> None:
        if gnss_snapshot.get("pvt_output_seen") is False or not pvt_current:
            self._set_fix_chip("NO FIX", ALERT)
            self._set_accuracy_chip("--", INFO)
            return
        accuracy = gnss_snapshot.get("accuracy", {})
        if not isinstance(accuracy, dict) or not accuracy:
            self._set_fix_chip("DEGRADED", WARNING)
            self._set_accuracy_chip("--", INFO)
            return

        horizontal_error = valid_float(accuracy.get("horizontal_error_m"))
        three_d_error = valid_float(accuracy.get("three_d_error_m"))
        fix_type = str(accuracy.get("fix_type", ""))
        fix_type_lower = fix_type.strip().lower()
        is_three_d = "3d" in fix_type_lower
        title = "PVT accuracy"
        if is_three_d and three_d_error is not None:
            text = f"{three_d_error:.2f} m"
        elif horizontal_error is not None:
            text = f"{horizontal_error:.2f} m"
        elif three_d_error is not None:
            text = f"{three_d_error:.2f} m"
        else:
            text = "--"
        pvt_status, pvt_color = self._format_pvt_status(
            str(accuracy.get("fix_type", "")),
            used_for_pvt_count,
            accuracy,
        )
        self._set_fix_chip(pvt_status, pvt_color, self._format_fix_value(fix_type))
        accuracy_color = pvt_color if pvt_status == "DEGRADED" else SUCCESS
        self._set_accuracy_chip(text, accuracy_color if text != "--" else INFO, title)

    def _set_receiver_pvt_details(
        self,
        gnss_snapshot: dict[str, object],
        pvt_current: bool,
    ) -> None:
        accuracy = gnss_snapshot.get("accuracy", {}) if pvt_current else {}
        if not isinstance(accuracy, dict):
            accuracy = {}
        lat = valid_float(accuracy.get("lat_deg"))
        lon = valid_float(accuracy.get("lon_deg"))
        alt = valid_float(accuracy.get("alt_m"))
        utm_easting = valid_float(accuracy.get("utm_easting_m"))
        utm_northing = valid_float(accuracy.get("utm_northing_m"))
        utm_zone = str(accuracy.get("utm_zone") or "").strip()
        hdop = valid_float(accuracy.get("hdop"))
        vdop = valid_float(accuracy.get("vdop"))
        pdop = valid_float(accuracy.get("pdop"))
        gdop = valid_float(accuracy.get("gdop"))
        dop_values = (hdop, vdop, pdop, gdop)
        dop_text = " / ".join(self._format_ratio_value(value) for value in dop_values)
        dop_has_value = any(value is not None for value in dop_values)
        receiver_time = self._format_receiver_time(gnss_snapshot.get("receiver_time_s"))
        receiver_time_available = receiver_time != "--"
        lat_lon_available = pvt_current and lat is not None and lon is not None
        lat_lon_text = f"{lat:.7f}° / {lon:.7f}°" if lat_lon_available else "-- / --"
        altitude_available = pvt_current and alt is not None
        altitude_text = f"{alt:.1f} m" if altitude_available else "--"
        utm_available = (
            pvt_current
            and utm_easting is not None
            and utm_northing is not None
            and bool(utm_zone)
        )
        utm_text = (
            f"{utm_easting:.3f} / {utm_northing:.3f} ({utm_zone})"
            if utm_available
            else "-- / --"
        )
        rows = (
            (
                self._latitude_label,
                "lat/long",
                lat_lon_text,
                True if lat_lon_available else None,
            ),
            (
                self._altitude_label,
                "altitude",
                altitude_text,
                True if altitude_available else None,
            ),
            (
                self._receiver_time_label,
                "Time",
                receiver_time,
                receiver_time_available,
            ),
            (
                self._dop_label,
                "HDOP/VDOP/PDOP/GDOP",
                dop_text if pvt_current else "-- / -- / -- / --",
                True if dop_has_value else None,
            ),
            (
                self._enu_label,
                "UTM east/north",
                utm_text,
                True if utm_available else None,
            ),
        )
        for label, title, value, valid in rows:
            self._set_status_row(
                label,
                title,
                value,
                SUCCESS if pvt_current and valid is not None else INFO,
            )

    @staticmethod
    def _format_meter_value(value: float | None) -> str:
        return f"{value:.2f} m" if value is not None else "--"

    def _prn_chart_signature(self, prn_entries: list[dict[str, object]]) -> tuple[object, ...]:
        return tuple(
            sorted(
                (
                    satellite_id(entry),
                    str(entry.get("state", "")),
                    bool(entry.get("used_in_fix", False)),
                    bool(entry.get("cno_stable", False)),
                    str(entry.get("cno_unstable_reason", "")),
                    self._rounded_float(valid_float(entry.get("cno_db_hz")), 1),
                    self._rounded_float(valid_float(entry.get("observable_cno_db_hz")), 1),
                )
                for entry in prn_entries
            )
        )

    def _skyplot_signature(
        self,
        sky_entries: list[dict[str, object]],
        unplaced_tracking_prns: list[int],
    ) -> tuple[object, ...]:
        placed = tuple(
            sorted(
                (
                    satellite_id(entry),
                    str(entry.get("state", "")),
                    bool(entry.get("used_in_fix", False)),
                    self._rounded_float(valid_float(entry.get("az_deg")), 1),
                    self._rounded_float(valid_float(entry.get("el_deg")), 1),
                )
                for entry in sky_entries
            )
        )
        return placed + (("unplaced", tuple(sorted(set(unplaced_tracking_prns)))),)

    @staticmethod
    def _rounded_float(value: float | None, digits: int) -> float | None:
        if value is None:
            return None
        return round(float(value), int(digits))

    @staticmethod
    def _finite_angle(angle_deg: float | None) -> float | None:
        if angle_deg is None:
            return None
        angle = float(angle_deg)
        if not np.isfinite(angle):
            return None
        return angle

    def _internal_angle_to_bearing(self, angle_deg: float | None) -> float | None:
        angle = self._finite_angle(angle_deg)
        if angle is None:
            return None
        return internal_angle_to_operator_bearing_deg(angle)

    def _bearing_axis_for_internal_scan(self, scan: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return operator_bearing_axis_for_internal_scan(scan)

    def _metric_doa_bearing(self, metrics: dict) -> float | None:
        doa_deg = valid_float(metrics.get("doa_display_deg"))
        if doa_deg is not None:
            return doa_deg
        doa_deg = valid_float(metrics.get("doa_deg"))
        jammer = metrics.get("jammer", {}) if isinstance(metrics, dict) else {}
        if doa_deg is None and isinstance(jammer, dict):
            jammer_display = valid_float(jammer.get("doa_display_deg"))
            if jammer_display is not None:
                return jammer_display
            doa_deg = valid_float(jammer.get("doa_deg"))
        return self._internal_angle_to_bearing(doa_deg)

    # -------------------------------------------------------------------------
    # Status Chip Refresh
    # -------------------------------------------------------------------------

    def _refresh_doa_chips(self, metrics: dict) -> None:
        doa_bearing = self._metric_doa_bearing(metrics)
        jammer = metrics.get("jammer", {}) if isinstance(metrics, dict) else {}
        self._refresh_jammer_status(metrics)
        if doa_bearing is None:
            self._set_direction_chip("--", INFO)
            self._set_suppression_chip("--", INFO)
            self._set_null_summary(False)
            self._set_status_row(self._jammer_doa_label, "Direction candidate", "--", INFO)
            self._set_status_row(self._jammer_null_label, "Nulling", "Off", INFO)
            return

        self._set_direction_chip(f"{doa_bearing:.1f}°", INFO)
        null_active = bool(metrics.get("lcmv_null_active", False))
        jammer_detected = (
            bool(jammer.get("detected", False)) if isinstance(jammer, dict) else False
        )
        mitigation_active = (
            bool(jammer.get("mitigation_active", False)) if isinstance(jammer, dict) else False
        )
        suppression_db = self._measured_suppression_db(metrics)
        if (
            null_active
            and (jammer_detected or mitigation_active)
            and suppression_db is not None
        ):
            suppression_text = f"{suppression_db:.1f} dB"
            self._set_suppression_chip(suppression_text, WARNING)
            self._set_status_row(
                self._jammer_suppression_label,
                "Beamformed IQ reduction",
                self._format_suppression_detail(metrics, suppression_text),
                WARNING,
            )
        else:
            self._set_suppression_chip("--", INFO)
        null_text = "On" if null_active else "Off"
        self._set_null_summary(null_active)
        self._set_status_row(
            self._jammer_doa_label,
            "Direction candidate",
            f"{doa_bearing:.1f}°",
            INFO,
        )
        self._set_status_row(
            self._jammer_null_label,
            "Nulling",
            null_text,
            WARNING if null_active else INFO,
        )

    def _refresh_jammer_status(self, metrics: dict) -> None:
        jammer = metrics.get("jammer", {}) if isinstance(metrics, dict) else {}
        if not isinstance(jammer, dict) or not jammer:
            self._set_jammer_chip("Monitoring", WARNING)
            self._set_jammer_detector_metrics({})
            return
        state = str(jammer.get("state", "")).strip().lower()
        if state == "detected" or bool(jammer.get("detected", False)):
            self._set_jammer_chip("Detected", ALERT)
        elif state == "suspected":
            self._set_jammer_chip("Suspected", WARNING)
        elif state == "not_detected":
            self._set_jammer_chip("Not detected", SUCCESS)
        elif state == "disabled":
            self._set_jammer_chip("Detection off", INFO)
        else:
            self._set_jammer_chip("Monitoring", WARNING)
        self._set_jammer_detector_metrics(jammer)

    def _set_jammer_detector_metrics(self, jammer: dict[str, object]) -> None:
        input_power_db = valid_float(
            jammer.get("input_power_db", jammer.get("detector_power_db"))
        )
        min_power_db = valid_float(
            jammer.get("min_power_db", jammer.get("power_threshold_db"))
        )
        power_rise_db = valid_float(jammer.get("power_rise_db"))
        power_rise_threshold_db = valid_float(jammer.get("power_rise_threshold_db"))
        rise_text, rise_color = self._format_db_threshold(
            power_rise_db,
            power_rise_threshold_db,
            alert_on_threshold=True,
        )
        power_text, power_color = self._format_db_threshold(input_power_db, min_power_db)
        self._set_status_row(self._jammer_power_rise_label, "Power rise", rise_text, rise_color)
        self._set_status_row(self._jammer_power_label, "Raw IQ power", power_text, power_color)

        doa_bearing = valid_float(jammer.get("doa_display_deg"))
        if doa_bearing is None:
            doa_deg = valid_float(jammer.get("doa_deg"))
            doa_bearing = self._internal_angle_to_bearing(doa_deg)
        if doa_bearing is not None:
            self._set_status_row(
                self._jammer_doa_label,
                "Direction candidate",
                f"{doa_bearing:.1f}°",
                INFO,
            )

    def _refresh_rx_signal_health(self, metrics: dict) -> None:
        health = metrics.get("rx_signal_health", {}) if isinstance(metrics, dict) else {}
        if not isinstance(health, dict) or not bool(health.get("assessed", False)):
            self._set_status_row(self._rx_clipping_label, "RX clipping", "--", INFO)
            self._set_status_row(self._rx_peak_label, "IQ peak", "--", INFO)
            self._set_status_row(self._rx_rms_label, "IQ RMS", "--", INFO)
            self._set_status_row(self._rx_near_full_scale_label, "Near full scale", "--", INFO)
            return

        clipping_suspected = bool(health.get("clipping_suspected", False))
        clipping_count = health.get("clipping_suspected_count", 0)
        try:
            count_text = int(clipping_count)
        except (TypeError, ValueError):
            count_text = 0
        clipping_text = "Suspected" if clipping_suspected else "OK"
        if count_text > 0:
            clipping_text = f"{clipping_text} ({count_text})"
        self._set_status_row(
            self._rx_clipping_label,
            "RX clipping",
            clipping_text,
            ALERT if clipping_suspected else SUCCESS,
        )

        peak_component = valid_float(health.get("iq_peak_component"))
        threshold_component = valid_float(health.get("threshold_component"))
        peak_magnitude = valid_float(health.get("iq_peak_magnitude"))
        rms_magnitude = valid_float(health.get("iq_rms_magnitude"))
        near_full_scale_pct = valid_float(health.get("near_full_scale_pct"))
        threshold_pct = valid_float(health.get("threshold_pct"))

        if peak_component is None:
            peak_text = "--"
        elif threshold_component is None:
            peak_text = f"{peak_component:.4f}"
        else:
            peak_text = f"{peak_component:.4f} / {threshold_component:.3f}"
        if peak_magnitude is not None:
            peak_text = f"{peak_text} mag {peak_magnitude:.4f}"
        self._set_status_row(
            self._rx_peak_label,
            "IQ peak",
            peak_text,
            ALERT if clipping_suspected else INFO,
        )

        rms_text = f"{rms_magnitude:.4f}" if rms_magnitude is not None else "--"
        self._set_status_row(self._rx_rms_label, "IQ RMS", rms_text, INFO)

        if near_full_scale_pct is None:
            near_full_scale_text = "--"
        elif threshold_pct is None:
            near_full_scale_text = f"{near_full_scale_pct:.4f}%"
        else:
            near_full_scale_text = f"{near_full_scale_pct:.4f} / {threshold_pct:.4f}%"
        self._set_status_row(
            self._rx_near_full_scale_label,
            "Near full scale",
            near_full_scale_text,
            ALERT if clipping_suspected else INFO,
        )


    def _format_db_threshold(
        self,
        value: float | None,
        threshold: float | None,
        *,
        alert_on_threshold: bool = False,
    ) -> tuple[str, str]:
        if value is None:
            return "--", INFO
        if threshold is None:
            return f"{value:.1f} dB", INFO
        color = ALERT if alert_on_threshold and value >= threshold else INFO
        return f"{value:.1f} / {threshold:.1f} dB", color

    def _format_fix_value(self, fix_type: str) -> str:
        normalized = fix_type.strip().lower()
        if "3d" in normalized:
            return "3D Fix"
        if "2d" in normalized:
            return "2D Fix"
        if "no fix" in normalized:
            return "NO FIX"
        if "not available" in normalized:
            return "Not available"
        if normalized in {"available", "fix", "fixed"}:
            return "Available"
        if normalized:
            return fix_type
        return "Available"

    def _format_pvt_status(
        self,
        fix_type: str,
        used_for_pvt_count: int,
        accuracy: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        normalized = fix_type.strip().lower()
        if "no fix" in normalized or "not available" in normalized:
            return "NO FIX", ALERT
        quality = accuracy if isinstance(accuracy, dict) else {}
        del used_for_pvt_count
        pdop = valid_float(quality.get("pdop"))
        if pdop is not None and pdop > PVT_DEGRADED_PDOP_THRESHOLD:
            return "DEGRADED", WARNING
        if "2d" in normalized or "3d" in normalized:
            return "FIX", SUCCESS
        if not normalized:
            return "DEGRADED", WARNING
        return "FIX", SUCCESS

    def _format_ratio_value(self, value: float | None) -> str:
        return "--" if value is None else f"{value:.2f}"

    # -------------------------------------------------------------------------
    # GNSS-SDR Diagnostics
    # -------------------------------------------------------------------------

    def _gnss_sdr_status_text(self) -> str:
        snapshot = self._latest_gnss_snapshot
        receiver_time = self._format_receiver_time(snapshot.get("receiver_time_s"))
        tracking_prns = self._format_satellite_list(
            snapshot.get("tracking_satellites"),
            snapshot.get("tracking_prns"),
        )
        tracking_channels = self._format_tracking_channels(snapshot.get("prns"))
        pvt_status = self._pvt_output_status(snapshot)
        geometry_count = self._format_count(snapshot.get("sky_geometry_count"))
        used_count = self._format_count(snapshot.get("used_in_fix_count"))
        used_satellites = self._format_satellite_list(
            snapshot.get("used_in_fix_satellites"),
            snapshot.get("used_in_fix_prns"),
        )
        used_text = used_count if used_satellites == "--" else f"{used_count} ({used_satellites})"
        observables_count = self._format_count(snapshot.get("valid_observables_count"))
        avg_cno = self._format_db_hz(snapshot.get("avg_tracking_cno_db_hz"))
        runtime_dir = Path(self._cfg.gnss_sdr_runtime_dir)
        receiver_log = Path(self._cfg.gnss_sdr_log_dir) / "receiver.log"
        udp_packets = (
            f"pvt={self._format_count(snapshot.get('udp_pvt_packets'))}, "
            f"observables={self._format_count(snapshot.get('udp_observables_packets'))}, "
            f"tracking={self._format_count(snapshot.get('udp_tracking_packets'))}, "
            f"errors={self._format_count(snapshot.get('udp_parse_errors'))}"
        )
        return (
            f"GNSS-SDR receiver time: {receiver_time}\n"
            f"GNSS-SDR tracking PRNs: {tracking_prns}\n"
            f"GNSS-SDR tracking channels: {tracking_channels}\n"
            f"PVT output: {pvt_status}\n"
            f"Sky geometry: {geometry_count}\n"
            f"Used for PVT: {used_text}\n"
            f"Valid observables: {observables_count}\n"
            f"Tracking C/N0 average: {avg_cno}\n"
            f"GNSS-SDR receiver log: {receiver_log}\n"
            f"GNSS-SDR console log: {runtime_dir / 'console.log'}\n"
            f"PVT UDP monitor: 127.0.0.1:{self._cfg.gnss_pvt_monitor_udp_port}\n"
            f"Observables UDP monitor: 127.0.0.1:{self._cfg.gnss_monitor_udp_port}\n"
            f"Tracking UDP monitor: 127.0.0.1:{self._cfg.gnss_tracking_monitor_udp_port}\n"
            f"UDP packets: {udp_packets}"
        )

    def _format_receiver_time(self, value: object) -> str:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return "--"
        if seconds < 0:
            return "--"
        minutes, rem_seconds = divmod(seconds, 60)
        hours, rem_minutes = divmod(minutes, 60)
        return f"{hours:02d}:{rem_minutes:02d}:{rem_seconds:02d}"

    def _format_prn_list(self, value: object) -> str:
        if not isinstance(value, (list, tuple, set)):
            return "--"
        prns = sorted(
            prn
            for raw_prn in value
            for prn in [valid_prn(raw_prn)]
            if prn is not None
        )
        if not prns:
            return "--"
        return ", ".join(f"G{prn:02d}" for prn in prns)

    def _format_satellite_list(self, labels_obj: object, fallback_prns_obj: object = None) -> str:
        labels: list[str] = []
        if isinstance(labels_obj, (list, tuple, set)):
            for raw_label in labels_obj:
                label = str(raw_label).strip()
                if label and label != "--":
                    labels.append(label)
        if not labels and isinstance(fallback_prns_obj, (list, tuple, set)):
            labels = [
                f"G{prn:02d}"
                for raw_prn in fallback_prns_obj
                for prn in [valid_prn(raw_prn)]
                if prn is not None
            ]
        if not labels:
            return "--"
        return ", ".join(labels)

    def _format_tracking_channels(self, value: object) -> str:
        if not isinstance(value, list):
            return "--"
        channel_labels: list[str] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("state", "")).lower() != "tracking":
                continue
            prn = valid_prn(entry.get("prn"))
            if prn is None:
                continue
            try:
                channel = int(entry.get("channel", -1))
            except (TypeError, ValueError):
                channel = -1
            if channel >= 0:
                channel_labels.append(f"ch{channel}:{satellite_id(entry)}")
            else:
                channel_labels.append(satellite_id(entry))
        return ", ".join(channel_labels) if channel_labels else "--"

    def _pvt_output_status(self, snapshot: dict[str, object]) -> str:
        if not snapshot:
            return "--"
        if bool(snapshot.get("pvt_current", False)):
            return "current"
        if bool(snapshot.get("pvt_output_seen", False)):
            return "stale"
        return "not seen"

    def _format_count(self, value: object) -> str:
        try:
            count = int(value)
        except (TypeError, ValueError):
            return "0"
        return str(max(0, count))

    def _format_db_hz(self, value: object) -> str:
        number = valid_float(value)
        if number is None:
            return "--"
        return f"{number:.1f} dB-Hz"

    # -------------------------------------------------------------------------
    # Numeric Helpers
    # -------------------------------------------------------------------------

    def _measured_suppression_db(self, metrics: dict) -> float | None:
        delta_db = valid_float(metrics.get("lcmv_power_delta_db"))
        if delta_db is None or delta_db <= 0.0:
            return None
        return delta_db

    def _format_suppression_detail(self, metrics: dict, suppression_text: str) -> str:
        input_power_db = valid_float(metrics.get("lcmv_input_power_db"))
        output_power_db = valid_float(metrics.get("lcmv_output_power_db"))
        if input_power_db is None or output_power_db is None:
            return suppression_text
        return f"{suppression_text} ({input_power_db:.1f} to {output_power_db:.1f} dB)"

    def _set_positive_dynamic_y_range(self, plot, values: np.ndarray) -> None:
        finite = np.asarray(values, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return
        ymax = max(float(np.max(finite)) * 1.05, 1.0)
        plot.setLimits(yMin=0.0, yMax=ymax)
        plot.setYRange(0.0, ymax, padding=0.0)

    def _finite_vector(self, value: object) -> np.ndarray:
        try:
            vector = np.asarray(value, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError):
            return np.zeros((0,), dtype=np.float64)
        if vector.size == 0:
            return np.zeros((0,), dtype=np.float64)
        return np.nan_to_num(vector, nan=0.0, posinf=0.0, neginf=0.0)

    def _scan_angles_for_size(self, size: int) -> np.ndarray:
        return self._cfg.angle_scan_spec().values_for_size(size)
