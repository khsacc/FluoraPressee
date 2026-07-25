import os
import time
import threading
from copy import deepcopy
from datetime import datetime
import numpy as np
from PyQt6.QtWidgets import (QMainWindow, QPushButton, QVBoxLayout,
                             QHBoxLayout, QWidget, QLabel, QRadioButton, QGroupBox,
                             QStackedWidget,
                             QScrollArea, QFileDialog, QButtonGroup, QGridLayout,
                             QDialog, QTextEdit, QCheckBox, QMessageBox)
from PyQt6.QtCore import Qt, QTimer
import pyqtgraph as pg

# ---- Imports from the split-out modules ----
from src.hardware.camera import CameraThread
from src.hardware.spectrometer import SpectrometerController, SpectrometerMoveThread
from src.core.analysis import DataAnalyzer
from src.ui.calibration_ui import CalibrationWindow
from src.core.configuration_catalog import ConfigurationCatalog
from src.core.pressureCalc import PressureCalculator
from src.ui.pressureCalc_ui import PressureCalculatorWindow
from src.core.file_io import DataFileIO
from src.ui.ui_widgets import CustomSpinBox, CustomDoubleSpinBox, CustomComboBox
from src.ui.theme import colored_button_style
from src.ui.window_title import live_window_title
from src.ui.fitting_config_widget import FittingConfigWidget
from src.ui.fit_range_context_menu import FitRangeContextMenu
from src.ui.ui_mixins.config_mixin import ConfigMixin
from src.ui.ui_mixins.file_io_mixin import FileIOMixin
from src.ui.ui_mixins.spectrometer_control_mixin import SpectrometerControlMixin
from src.ui.ui_mixins.sequential_mixin import SequentialMixin
from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
from src.ui.ui_mixins.display_mixin import DisplayMixin
from src.ui.ui_mixins.pressure_dialog_mixin import PressureDialogMixin
from src.ui.ui_mixins.api_mixin import ApiMixin
# ----------------------------------------

BUTTON_STYLE_BLUE = "background-color: #2196F3; color: white; font-weight: bold;"
BUTTON_STYLE_GREEN = "background-color: #4CAF50; color: white; font-weight: bold;"
BUTTON_STYLE_RED = "background-color: #f44336; color: white; font-weight: bold;"
BUTTON_STYLE_ORANGE = "background-color: #FF9800; color: white; font-weight: bold;"
BUTTON_STYLE_PURPLE = "background-color: #673AB7; color: white; font-weight: bold;"
BUTTON_STYLE_PINK = "font-weight: bold; padding: 10px; background-color: #E91E63; color: white;"
BUTTON_STYLE_LINK = "font-weight: bold; color: #2196F3;"
BUTTON_STYLE_DISABLED = "background-color: #A0A0A0; color: white; font-weight: bold;"

CONTROL_DISABLED_STYLE_SHEET = """
QPushButton:disabled {
    background-color: #A0A0A0;
    color: white;
    font-weight: bold;
}
QAbstractSpinBox:disabled,
QComboBox:disabled,
QLineEdit:disabled,
QTextEdit:disabled {
    background-color: #E0E0E0;
    color: #666666;
    border: 1px solid #BDBDBD;
}
QCheckBox:disabled,
QRadioButton:disabled {
    color: #777777;
}
"""

class SpectrometerGUI(QMainWindow, ConfigMixin, FileIOMixin, SpectrometerControlMixin, SequentialMixin, AcquisitionMixin, DisplayMixin, PressureDialogMixin, ApiMixin):
    BUTTON_STYLE_BLUE = BUTTON_STYLE_BLUE
    BUTTON_STYLE_GREEN = BUTTON_STYLE_GREEN
    BUTTON_STYLE_RED = BUTTON_STYLE_RED
    BUTTON_STYLE_ORANGE = BUTTON_STYLE_ORANGE
    BUTTON_STYLE_PURPLE = BUTTON_STYLE_PURPLE
    BUTTON_STYLE_PINK = BUTTON_STYLE_PINK
    BUTTON_STYLE_LINK = BUTTON_STYLE_LINK
    BUTTON_STYLE_DISABLED = BUTTON_STYLE_DISABLED

    def __init__(self, debug=False):
        super().__init__()
        self.debug = debug
        self.setWindowTitle("FluoRaPressée: Spectrometer Live View" + (" [DEBUG MODE]" if self.debug else ""))
        self.resize(1400, 900)

        self.config = self.load_spectrometer_config()
        # Hardware/connection keys are consumed only when the controller objects
        # are constructed. Keep the startup snapshot so GET /config can distinguish
        # currently active values from changes saved for the next restart.
        self._startup_config = deepcopy(self.config)
        _cache = self._load_local_cache()
        self._api_key = self.get_or_create_api_key()

        self.raw_1d_data = None 
        self.raw_2d_data = None
        self.latest_1d_data = None
        self.latest_2d_data = None
        
        self.calib_coeffs = None
        self.calib_unit = 'Wavelength'   # 'Wavelength' (pixel→nm) or 'Raman shift' (pixel→cm⁻¹)
        self.calib_laser_wl = None       # excitation wavelength (nm) used when calib_unit=='Raman shift'
        self.configuration_label = "None"
        self.active_configuration_id = None
        self.active_configuration_slot_id = None
        # Physical grating/centre/ROI may correspond to a configuration even
        # when its calibration is deliberately not applied (API pixel mode).
        self.positioned_configuration_id = None
        self.positioned_configuration_slot_id = None
        self.axis_source = "pixel"
        self._latest_hardware_capture = None
        self._hardware_capture_by_mode = {}
        self._camera_identity = {"model": None, "serial_number": None}
        self._spectrometer_identity = {"model": None, "serial_number": None}
        self._last_temperature_c = None
        self._last_temperature_status = None
        self.current_w_peak1 = None
        
        self.loaded_bg_data = None
        self.loaded_bg_metadata = None
        self.is_single_shot = False
        self._ignore_next_frames = False

        # Exclusive gate ensuring only one of "manual GUI measurement / calibration dialog /
        # (future) API-triggered measurement" can hold actual acquisition rights at a time.
        # This is a layer independent of widget enabled/disabled state.
        self._acquisition_gate = threading.Lock()
        self._gate_held_by_me = False

        self.latest_fit_res = None
        self.latest_fit_func = None

        self.current_accum_count = 0
        self.accumulated_data = None
        self.accum_frames = None
        self._accum_use_rejection = False
        # Accumulation count that overrides spin_accumulate.value(), set only for API-triggered
        # acquisitions. When None, the widget value is always used (existing GUI behaviour is unchanged).
        self._active_target_accum = None
        # Future that api_acquire() waits on for a single-shot acquisition to complete (set from the
        # API worker thread via GuiBridge, resolved on the GUI thread by _process_completed_data()).
        self._api_pending_future = None
        # Future used only by GET /hardware/camera?refresh=true. The camera owns
        # its SDK and reports the live snapshot asynchronously via status_ready.
        self._api_camera_status_future = None
        self._pending_calib_coeffs = None
        self._pending_configuration_label = None
        self._pending_calib_unit = None
        self._pending_calib_laser_wl = None
        self._pending_axis_source = None
        self._pending_configuration_id = None
        self._pending_configuration_slot_id = None
        self._pending_configuration_axis_mode = "calibrated"
        self._pending_configuration_future = None
        # Set of "reasons the measurement controls are locked" (sequential run in progress,
        # API server running, etc.). Re-enabled only once every reason has been cleared
        # (see _lock_ui/_unlock_ui in sequential_mixin.py).
        self._ui_lock_reasons = set()

        self.seq_dir = _cache.get("last_seq_dir", "")
        self.is_sequential_running = False
        self.seq_count = 0
        self.current_skip_count = 0
        self.seq_start_time_dt = None
        self.seq_log_data = []
        self._seq_fit_failed = False
        self.seq_fitting_summary_path = None

        self.spec_ctrl = SpectrometerController(config=self.config, debug=self.debug)
        self.analyzer = DataAnalyzer()
        self.file_io = DataFileIO()
        self.configuration_catalog = ConfigurationCatalog()
        self._last_save_dir = _cache.get("last_save_dir", "")

        first_grating = self.config.get("grating", [{}])[0].get("grooves", 600)
        self.physical_grating = str(first_grating)
        self.physical_center_wl = 694.0

        self.pressure_window = None
        self.instrument_status_window = None
        self.analysis_window = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        plot_layout = QVBoxLayout()
        
        status_coord_layout = QHBoxLayout()
        self.status_label = QLabel("Initialising camera...")
        self.coord_label = QLabel("Cursor Position: --")
        self.coord_label.setStyleSheet("font-weight: bold; color: #0078D7; font-size: 14px;")
        status_coord_layout.addWidget(self.status_label)
        status_coord_layout.addStretch()
        status_coord_layout.addWidget(self.coord_label)
        plot_layout.addLayout(status_coord_layout)

        self.lbl_accum_status = QLabel("")
        self.lbl_accum_status.setStyleSheet("color: #E91E63; font-weight: bold; font-size: 14px;")
        self.lbl_accum_status.setVisible(False)
        plot_layout.addWidget(self.lbl_accum_status)

        # Shown only while displaying Ocean Optics' native (factory-calibrated) wavelength
        # axis with no FluoRaPressée calibration loaded - see
        # SpectrometerControlMixin.update_plot_labels() (work/work_OceanOptics.md Step 6).
        self.lbl_axis_warning = QLabel(
            "X-axis: uncalibrated (Ocean Optics factory-calibrated values)"
        )
        self.lbl_axis_warning.setStyleSheet("color: #F03511; font-weight: bold; font-size: 14px;")
        self.lbl_axis_warning.setVisible(False)
        plot_layout.addWidget(self.lbl_axis_warning)

        self.plot_content_layout = QHBoxLayout()

        self.fitting_panel = QGroupBox("Fitting Results")
        self.fitting_panel.setFixedWidth(240)
        self.fitting_text = QTextEdit()
        self.fitting_text.setReadOnly(True)
        self.fitting_text.setStyleSheet("font-family: Consolas; font-size: 11px; border: none;")
        fit_p_layout = QVBoxLayout()
        fit_p_layout.addWidget(self.fitting_text)
        self.fitting_panel.setLayout(fit_p_layout)
        self.fitting_panel.setVisible(False)
        self.plot_content_layout.addWidget(self.fitting_panel)

        self.stacked_widget = QStackedWidget()
        
        self.plot_widget = pg.PlotWidget(title="1D Spectrum")
        self.plot_widget.setLabel('left', 'Intensity (Counts)')
        self.plot_widget.setLabel('bottom', 'Pixel')
        self.plot_widget.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        
        self.plot_scatter = self.plot_widget.plot(pen=None, symbol='o', symbolSize=4, symbolBrush='w')
        self.plot_line = self.plot_widget.plot(pen=pg.mkPen('w', width=1))
        self.fit_baseline_curve = self.plot_widget.plot(
            pen=pg.mkPen('#9E9E9E', width=1, style=Qt.PenStyle.DashLine)
        )
        self.fit_curve = self.plot_widget.plot(pen=pg.mkPen('y', width=2))
        self.fit_curve_sub1 = self.plot_widget.plot(pen=pg.mkPen('y', width=1, style=Qt.PenStyle.DashLine))
        self.fit_curve_sub2 = self.plot_widget.plot(pen=pg.mkPen('y', width=1, style=Qt.PenStyle.DashLine))
        self.edge_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen('#00E5FF', width=2, style=Qt.PenStyle.DashLine)
        )
        self.edge_marker.hide()
        self.plot_widget.addItem(self.edge_marker)
        
        self.stacked_widget.addWidget(self.plot_widget) 

        self.image_view = pg.ImageView(view=pg.PlotItem())
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.image_view.getImageItem().setBorder(pg.mkPen('w', width=1))
        # pg.ImageView forces setAspectLocked(True) on the view it's given; with the
        # zero-margin setLimits() applied per-frame in DisplayMixin.update_display,
        # aspect-locked autoRange can't letterbox to fit non-square sensor images, so
        # "View All" crops part of the image instead of showing it all. Unlock aspect
        # so the full image always fits the panel.
        self.image_view.getView().setAspectLocked(False)
        self.image_view.getView().setDefaultPadding(0)
        self.image_view.getView().setLabel('bottom', 'Pixel')
        self.image_view.getView().setLabel('left', 'Pixel')
        self.stacked_widget.addWidget(self.image_view)

        self.plot_content_layout.addWidget(self.stacked_widget)
        plot_layout.addLayout(self.plot_content_layout)
        
        plot_controls_layout = QHBoxLayout()
        self.radio_plot_line = QRadioButton("Line")
        self.radio_plot_scatter = QRadioButton("Scatter")
        self.radio_plot_line.setChecked(True)
        self._plot_style_group = QButtonGroup(self)
        self._plot_style_group.addButton(self.radio_plot_line)
        self._plot_style_group.addButton(self.radio_plot_scatter)
        self.chk_rescale_x = QCheckBox("Rescale X automatically")
        self.chk_rescale_y = QCheckBox("Rescale Y automatically")
        self.chk_rescale_x.setChecked(True)
        self.chk_rescale_y.setChecked(True)
        plot_controls_layout.addWidget(QLabel("Plot style:"))
        plot_controls_layout.addWidget(self.radio_plot_line)
        plot_controls_layout.addWidget(self.radio_plot_scatter)
        plot_controls_layout.addStretch()
        plot_controls_layout.addWidget(self.chk_rescale_x)
        plot_controls_layout.addWidget(self.chk_rescale_y)
        plot_layout.addLayout(plot_controls_layout)

        self.plot_widget.scene().sigMouseMoved.connect(self.on_mouse_moved)
        self.image_view.getView().scene().sigMouseMoved.connect(self.on_mouse_moved)

        main_layout.addLayout(plot_layout, stretch=3)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumWidth(400)
        
        controls_widget = QWidget()
        controls_widget.setStyleSheet(CONTROL_DISABLED_STYLE_SHEET)
        controls_layout = QVBoxLayout(controls_widget)

        meas_group = QGroupBox("Measurement")
        meas_layout = QVBoxLayout()
        
        self.btn_single = QPushButton("Take single spectrum")
        self._set_button_style(self.btn_single, self.BUTTON_STYLE_BLUE)
        
        self.btn_commence = QPushButton("Commence Measurement")
        self.btn_commence.setEnabled(False)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_terminate = QPushButton("Terminate Measurement")
        self.btn_terminate.setEnabled(False)
        self._set_button_style(self.btn_terminate, self.BUTTON_STYLE_RED)
        
        self.btn_save_data = QPushButton("Save data")
        self._set_button_style(self.btn_save_data, self.BUTTON_STYLE_ORANGE)
        
        self.chk_save_fitting = QCheckBox("Save fitting results")
        self.chk_save_fitting.setEnabled(False)
        
        meas_layout.addWidget(self.btn_single)
        meas_layout.addWidget(self.btn_commence)
        meas_layout.addWidget(self.btn_terminate)
        meas_layout.addWidget(self.btn_save_data)
        meas_layout.addWidget(self.chk_save_fitting)

        det_sub_group = QGroupBox("Detector Configurations")
        det_layout = QGridLayout()
        self.spin_acq_time = CustomDoubleSpinBox()
        self.spin_acq_time.setRange(0.001, 3600)
        self.spin_acq_time.setValue(0.1)
        self.spin_acq_time.setDecimals(3)

        # PICam reports at runtime whether the connected camera actually has
        # an EM Gain parameter. Keep this row hidden until exists=True is received.
        self.label_em_gain = QLabel("EM Gain (×):")
        self.spin_em_gain = CustomSpinBox()
        self.label_em_gain.setVisible(False)
        self.spin_em_gain.setVisible(False)
        self.spin_em_gain.setEnabled(False)
        self._em_gain_available = False
        
        self.spin_accumulate = CustomSpinBox()
        self.spin_accumulate.setRange(1, 99999)
        self.spin_accumulate.setValue(1)

        self.chk_cosmic_ray_removal = QCheckBox("Cosmic ray removal")
        self.chk_cosmic_ray_removal.setChecked(False)
        self.chk_cosmic_ray_removal.setToolTip(
            "During accumulation (Accumulations ≥ 5), detect and remove cosmic-ray\n"
            "spikes that hit only a single frame before summing. For each pixel, frames\n"
            "far above the per-pixel median (relative to the other frames' spread) are\n"
            "replaced by that median before summing. Has no effect below 5 accumulations\n"
            "or in 2D image mode."
        )

        self.spin_spike_threshold = CustomDoubleSpinBox()
        self.spin_spike_threshold.setRange(1.0, 20.0)
        self.spin_spike_threshold.setDecimals(1)
        # A conservative default: at the realistic Accumulations counts this app
        # sees (roughly 5-30 frames), a low threshold flags many ordinary noise
        # fluctuations as "spikes" (verified empirically - see src/accumulation.py).
        # Real cosmic-ray hits are typically orders of magnitude larger than
        # detector noise, so a high threshold still catches them reliably.
        self.spin_spike_threshold.setValue(10.0)
        self.spin_spike_threshold.setEnabled(False)
        self.spin_spike_threshold.setToolTip(
            "Spike detection threshold, in multiples of the per-pixel noise (σ) estimated\n"
            "across the accumulated frames. A frame's pixel value is treated as a cosmic-ray\n"
            "spike if it exceeds the median of that pixel across all frames by more than this\n"
            "many σ.\n\n"
            "Higher = more conservative (fewer false positives, but may miss weaker spikes).\n"
            "Lower = more aggressive (catches weaker spikes, but flags more ordinary noise).\n\n"
            "Real cosmic-ray hits are typically far larger than detector noise, so values\n"
            "around 8-15 are usually safe. With fewer accumulated frames (close to 5), consider\n"
            "raising this further to reduce false positives."
        )

        # The camera thread reports at connect time whether this hardware actually has
        # temperature control (see on_temperature_capability_ready). Keep this group
        # hidden until has_control=True is received, same as the EM Gain row above.
        self.label_cooler_target = QLabel("Cooler target temp (°C):")
        self.spin_cooler_temp = CustomSpinBox()
        self.spin_cooler_temp.setRange(-100, 20)
        self.spin_cooler_temp.setValue(self.config.get("default_temperature", -65))
        self.btn_read_temp = QPushButton("Read current temperature")
        self.label_current_temp = QLabel("-- °C")
        self.label_current_temp.setStyleSheet("font-weight: bold; color: #E91E63;")
        self.label_cooler_target.setVisible(False)
        self.spin_cooler_temp.setVisible(False)
        self.btn_read_temp.setVisible(False)
        self.label_current_temp.setVisible(False)
        self._temp_status_supported = False
        self._temp_history = []
        self._temp_accepted_setpoint = None
        # Set by on_temperature_capability_ready; used instead of querying widget
        # .isVisible() elsewhere, since isVisible() reflects actual on-screen
        # visibility (requires the whole window to be shown), not just this group's
        # own setVisible() state.
        self._temp_control_available = False
        self._temp_capability_known = False
        # Whether the 5s poll timer is allowed to keep/resume running. Only
        # on_temperature_capability_ready and on_temperature_set_finished may set this
        # True; a manual "Read current temperature" click while stopped must never
        # re-enable it on its own (see on_temperature_read / add-temperature-status work).
        self._temp_auto_poll_enabled = False

        det_layout.addWidget(QLabel("Acquisition time (s):"), 0, 0)
        det_layout.addWidget(self.spin_acq_time, 0, 1)
        det_layout.addWidget(self.label_em_gain, 1, 0)
        det_layout.addWidget(self.spin_em_gain, 1, 1)
        det_layout.addWidget(QLabel("Accumulations:"), 2, 0)
        det_layout.addWidget(self.spin_accumulate, 2, 1)
        det_layout.addWidget(self.chk_cosmic_ray_removal, 3, 0)
        det_layout.addWidget(self.spin_spike_threshold, 3, 1)
        det_layout.addWidget(self.label_cooler_target, 4, 0)
        det_layout.addWidget(self.spin_cooler_temp, 4, 1)
        det_layout.addWidget(self.btn_read_temp, 5, 0)
        det_layout.addWidget(self.label_current_temp, 5, 1)
        det_sub_group.setLayout(det_layout)
        meas_layout.addWidget(det_sub_group)

        self.seq_toggle_btn = QPushButton("▶ Sequential measurements")
        self.seq_toggle_btn.setCheckable(True)
        self.seq_toggle_btn.setStyleSheet("text-align: left; font-weight: bold; border: none; padding: 5px;")
        
        self.seq_content = QWidget()
        self.seq_content.setVisible(False)
        seq_layout = QGridLayout(self.seq_content)
        
        self.btn_choose_dir = QPushButton("Choose directory")
        self.lbl_seq_dir = QLabel("Dir: Not selected")
        self.lbl_seq_dir.setStyleSheet("color: #666; font-size: 11px;")
        
        skip_label_layout = QHBoxLayout()
        skip_label = QLabel("Skip frames:")
        self.link_how_skip_works = QLabel('<a href="#how" style="color: #2196F3; text-decoration: none;">how this works?</a>')
        self.link_how_skip_works.linkActivated.connect(self.show_skip_frames_info)
        skip_label_layout.addWidget(skip_label)
        skip_label_layout.addWidget(self.link_how_skip_works)
        skip_label_layout.addStretch()
        
        self.spin_skip_frames = CustomSpinBox()
        self.spin_skip_frames.setRange(0, 99999)
        self.spin_skip_frames.setValue(0)
        self.spin_max_num = CustomSpinBox()
        self.spin_max_num.setRange(1, 99999)
        self.spin_max_num.setValue(9999)
        
        self.lbl_seq_progress = QLabel("")
        self.lbl_seq_progress.setStyleSheet("color: #2196F3; font-weight: bold;")
        self.lbl_seq_progress.setVisible(False)
        
        self.btn_start_seq = QPushButton("Start Sequential")
        self.btn_start_seq.setEnabled(False)
        self._set_button_style(self.btn_start_seq, self.BUTTON_STYLE_BLUE)
        self.btn_stop_seq = QPushButton("Stop Sequential")
        self.btn_stop_seq.setEnabled(False)
        self._set_button_style(self.btn_stop_seq, self.BUTTON_STYLE_RED)
        
        seq_layout.addWidget(self.btn_choose_dir, 0, 0, 1, 2)
        seq_layout.addWidget(self.lbl_seq_dir, 1, 0, 1, 2)
        seq_layout.addLayout(skip_label_layout, 2, 0)
        seq_layout.addWidget(self.spin_skip_frames, 2, 1)
        seq_layout.addWidget(QLabel("Max. number:"), 3, 0)
        seq_layout.addWidget(self.spin_max_num, 3, 1)
        seq_layout.addWidget(self.lbl_seq_progress, 4, 0, 1, 2)
        seq_layout.addWidget(self.btn_start_seq, 5, 0)
        seq_layout.addWidget(self.btn_stop_seq, 5, 1)
        
        meas_layout.addWidget(self.seq_toggle_btn)
        meas_layout.addWidget(self.seq_content)

        meas_group.setLayout(meas_layout)
        controls_layout.addWidget(meas_group)

        bg_group = QGroupBox("Background")
        bg_layout = QGridLayout()

        bg_radio_layout = QHBoxLayout()
        self.radio_bg_on = QRadioButton("ON")
        self.radio_bg_off = QRadioButton("OFF")
        self.radio_bg_off.setChecked(True)
        bg_radio_layout.addWidget(self.radio_bg_on)
        bg_radio_layout.addWidget(self.radio_bg_off)

        self.btn_acq_bg = QPushButton("Acquire and save background")
        self.btn_load_bg = QPushButton("Load background")
        self.lbl_loaded_bg = QLabel("Loaded: None")
        self.lbl_loaded_bg.setStyleSheet("color: #666; font-size: 11px;")

        bg_layout.addWidget(QLabel("Subtract background:"), 0, 0)
        bg_layout.addLayout(bg_radio_layout, 0, 1)
        bg_layout.addWidget(self.btn_acq_bg, 1, 0, 1, 2)
        bg_layout.addWidget(self.btn_load_bg, 2, 0, 1, 2)
        bg_layout.addWidget(self.lbl_loaded_bg, 3, 0, 1, 2)
        bg_group.setLayout(bg_layout)
        controls_layout.addWidget(bg_group)

        roi_group = QGroupBox("Display ROI Settings")
        roi_layout = QVBoxLayout()
        self.radio_2d = QRadioButton("2D Image View")
        self.radio_1d_full = QRadioButton("1D Spectrum (Full Range Binning)")
        self.radio_1d_roi = QRadioButton("1D Spectrum (Custom ROI)")
        self.radio_1d_roi.setChecked(True)
        roi_layout.addWidget(self.radio_2d)
        roi_layout.addWidget(self.radio_1d_full)
        roi_layout.addWidget(self.radio_1d_roi)

        self.chk_flip_x = QCheckBox("Flip X-axis")
        self.chk_flip_x.setChecked(self.config.get("flip_x", False))
        roi_layout.addWidget(self.chk_flip_x)

        roi_spin_layout = QHBoxLayout()
        self.lbl_roi_start = QLabel("Start Row:")
        roi_spin_layout.addWidget(self.lbl_roi_start)
        self.spin_vstart = CustomSpinBox()
        self.spin_vstart.setMaximum(4000)
        roi_spin_layout.addWidget(self.spin_vstart)

        self.lbl_roi_end = QLabel("End Row:")
        roi_spin_layout.addWidget(self.lbl_roi_end)
        self.spin_vend = CustomSpinBox()
        self.spin_vend.setMaximum(4000)
        roi_spin_layout.addWidget(self.spin_vend)
        roi_layout.addLayout(roi_spin_layout)
        roi_group.setLayout(roi_layout)
        controls_layout.addWidget(roi_group)

        spec_group = QGroupBox("Spectrometer Configurations")
        spec_layout = QGridLayout()

        self.btn_load_configuration = QPushButton("Load previous configuration")
        self._set_button_style(self.btn_load_configuration, self.BUTTON_STYLE_PURPLE)
        self.lbl_notes_calib_loading = QLabel('Loading a configuration will change the grating, centre, ROI, and x-axis calibration.')
        self.lbl_notes_calib_loading.setStyleSheet("font-style: italic;")
        self.lbl_notes_calib_loading.setWordWrap(True) # Enable word wrap to prevent layout overflow

        self.lbl_loaded_configuration = QLabel("Loaded: None")
        self.lbl_loaded_configuration.setStyleSheet("color: #333; font-size: 12px; font-weight: bold; margin-bottom: 10px;")
        self.lbl_loaded_configuration.setWordWrap(True)

        self.grating_list = [str(g.get("grooves")) for g in self.config.get("grating", [])]
        self.combo_grating = CustomComboBox()
        self.combo_grating.addItems(self.grating_list)

        spec_radio_layout = QHBoxLayout()
        self.radio_spec_mode_wl = QRadioButton("Wavelength")
        self.radio_spec_mode_raman = QRadioButton("Raman shift")
        self.radio_spec_mode_wl.setChecked(True)
        spec_radio_layout.addWidget(self.radio_spec_mode_wl)
        spec_radio_layout.addWidget(self.radio_spec_mode_raman)

        self.lbl_centre = QLabel("Centre (nm):")
        self.spin_centre_wl = CustomDoubleSpinBox()
        self.spin_centre_wl.setRange(-10000, 20000)
        self.spin_centre_wl.setValue(694.0)

        self.lbl_exc_wl = QLabel("Excitation wavelength (nm):")
        self.spin_exc_wl = CustomDoubleSpinBox()
        self.spin_exc_wl.setRange(0.01, 2000)
        self.spin_exc_wl.setDecimals(3)
        self.spin_exc_wl.setValue(532.0)
        self.spin_exc_wl.setEnabled(False)

        self.btn_apply_spec = QPushButton("Apply")
        self.btn_apply_spec.setEnabled(False)
        self._set_button_style(self.btn_apply_spec, self.BUTTON_STYLE_LINK)

        self.btn_calib_neon = QPushButton("Calibrate x-axis")

        spec_layout.addWidget(self.btn_load_configuration, 0, 0, 1, 2)
        spec_layout.addWidget(self.lbl_notes_calib_loading, 1, 0, 1, 2)
        spec_layout.addWidget(self.lbl_loaded_configuration, 2, 0, 1, 2)

        self.lbl_grating = QLabel("Grating (grooves/mm):")
        spec_layout.addWidget(self.lbl_grating, 3, 0)
        spec_layout.addWidget(self.combo_grating, 3, 1)
        spec_layout.addLayout(spec_radio_layout, 4, 0, 1, 2)
        spec_layout.addWidget(self.lbl_centre, 5, 0)
        spec_layout.addWidget(self.spin_centre_wl, 5, 1)
        spec_layout.addWidget(self.lbl_exc_wl, 6, 0)
        spec_layout.addWidget(self.spin_exc_wl, 6, 1)
        spec_layout.addWidget(self.btn_apply_spec, 7, 0, 1, 2)
        spec_layout.addWidget(self.btn_calib_neon, 8, 0, 1, 2)

        spec_group.setLayout(spec_layout)
        controls_layout.addWidget(spec_group)

        self.fitting_config = FittingConfigWidget(fitting_enabled=False)
        self.fitting_config.expose_controls_on(self)
        controls_layout.addWidget(self.fitting_config)

        self.pressure_window = None  # Holds the pressure calculator dialog instance once opened

        self.press_group = QGroupBox("Pressure Calculation")
        press_layout = QVBoxLayout()
        
        self.btn_open_pressure = QPushButton("Open Pressure Calculator")
        self._set_button_style(self.btn_open_pressure, self.BUTTON_STYLE_PINK)
        self.btn_open_pressure.clicked.connect(self.open_pressure_calculator)
        
        press_layout.addWidget(self.btn_open_pressure)
        self.press_group.setLayout(press_layout)

        controls_layout.addWidget(self.press_group)

        api_group = QGroupBox("API Server")
        api_layout = QVBoxLayout()

        api_port_layout = QHBoxLayout()
        api_port_layout.addWidget(QLabel("Port:"))
        self.spin_api_port = CustomSpinBox()
        self.spin_api_port.setRange(1, 65535)
        self.spin_api_port.setValue(8765)
        api_port_layout.addWidget(self.spin_api_port)
        api_layout.addLayout(api_port_layout)

        self.btn_start_api = QPushButton("Start API Server")
        self._set_button_style(self.btn_start_api, self.BUTTON_STYLE_GREEN)
        self.btn_stop_api = QPushButton("Stop API Server")
        self.btn_stop_api.setEnabled(False)
        self._set_button_style(self.btn_stop_api, self.BUTTON_STYLE_RED)
        api_layout.addWidget(self.btn_start_api)
        api_layout.addWidget(self.btn_stop_api)

        self.lbl_api_status = QLabel("Not running")
        self.lbl_api_status.setWordWrap(True)
        self.lbl_api_status.setStyleSheet("color: #666; font-size: 11px;")
        api_layout.addWidget(self.lbl_api_status)

        api_group.setLayout(api_layout)
        controls_layout.addWidget(api_group)

        controls_layout.addStretch()
        scroll_area.setWidget(controls_widget)
        main_layout.addWidget(scroll_area, stretch=1)

        self.btn_single.clicked.connect(self.check_bg_and_take_single)
        self.btn_save_data.clicked.connect(self.on_save_data_clicked)
        self.btn_commence.clicked.connect(self.check_bg_and_start_meas)
        self.btn_start_seq.clicked.connect(self.check_bg_and_start_seq)
        
        self.radio_spec_mode_wl.toggled.connect(self.on_spec_mode_changed)
        self.radio_spec_mode_raman.toggled.connect(self.on_spec_mode_changed)
        
        self.seq_toggle_btn.toggled.connect(self.toggle_sequential)
        self.btn_choose_dir.clicked.connect(self.on_choose_seq_dir)
        self.btn_stop_seq.clicked.connect(self.stop_sequential)
        
        self.radio_2d.toggled.connect(self.apply_roi_settings)
        self.radio_1d_full.toggled.connect(self.apply_roi_settings)
        self.radio_1d_roi.toggled.connect(self.apply_roi_settings)
        self.spin_vstart.valueChanged.connect(self.on_roi_spin_changed)
        self.spin_vend.valueChanged.connect(self.on_roi_spin_changed)
        
        self.btn_acq_bg.clicked.connect(self.on_acq_bg_clicked)
        self.btn_load_bg.clicked.connect(self.on_load_bg_clicked)
        self.radio_bg_on.toggled.connect(self.on_fit_settings_changed)
        self.radio_bg_off.toggled.connect(self.on_fit_settings_changed)
        
        self.btn_terminate.clicked.connect(self.stop_measurement)
        
        self.chk_flip_x.toggled.connect(self.on_flip_x_changed)
        
        self.chk_rescale_x.toggled.connect(self.on_fit_settings_changed)
        self.chk_rescale_y.toggled.connect(self.on_fit_settings_changed)

        self.radio_plot_line.toggled.connect(self.on_fit_settings_changed)
        self.radio_plot_scatter.toggled.connect(self.on_fit_settings_changed)

        self.btn_read_temp.clicked.connect(self.request_temperature_read)

        self.chk_cosmic_ray_removal.toggled.connect(self.on_cosmic_ray_removal_toggled)

        self.radio_fit_on.toggled.connect(self.toggle_fitting_panel)
        self.radio_fit_off.toggled.connect(self.toggle_fitting_panel)
        self.combo_fit_func.currentTextChanged.connect(self.on_fit_settings_changed)
        self.combo_fit_peak_count.currentIndexChanged.connect(self.on_fit_peak_count_changed)
        self.combo_peak_sort.currentIndexChanged.connect(self.on_fit_settings_changed)
        self.combo_baseline_model.currentIndexChanged.connect(self.on_fit_settings_changed)
        self.spin_fit_start.valueChanged.connect(self.on_fit_settings_changed)
        self.spin_fit_end.valueChanged.connect(self.on_fit_settings_changed)
        self.fit_range_context_menu = FitRangeContextMenu(
            self.plot_widget,
            self.spin_fit_start,
            self.spin_fit_end,
            lambda: getattr(self, "raw_1d_data", None) is not None,
        )
        
        
        
        


        self.spin_acq_time.editingFinished.connect(self.on_exposure_changed)
        self.spin_em_gain.editingFinished.connect(self.on_em_gain_changed)
        self.spin_cooler_temp.editingFinished.connect(self.on_temperature_changed)
        
        self.combo_grating.currentIndexChanged.connect(self.check_spectrometer_changes)
        self.spin_centre_wl.valueChanged.connect(self.check_spectrometer_changes)
        self.spin_exc_wl.valueChanged.connect(self.on_exc_wl_changed)

        self.btn_apply_spec.clicked.connect(self.on_apply_spectrometer)
        self.btn_calib_neon.clicked.connect(self.on_calibrate_neon)
        self.btn_load_configuration.clicked.connect(self.on_load_configuration)

        self.btn_start_api.clicked.connect(self.on_start_api_server_clicked)
        self.btn_stop_api.clicked.connect(self.on_stop_api_server_clicked)

        hardware_menu = self.menuBar().addMenu("Hardware")
        self.action_hardware_config = hardware_menu.addAction("Hardware Configuration...")
        self.action_hardware_config.triggered.connect(self.on_open_hardware_config_clicked)

        self.action_camera_status = hardware_menu.addAction("Instrument Status...")
        self.action_camera_status.triggered.connect(self.on_open_camera_status_clicked)

        self.action_manage_configurations = hardware_menu.addAction(
            "Manage Configuration Files..."
        )
        self.action_manage_configurations.triggered.connect(
            self.on_open_configuration_manager_clicked
        )

        api_menu = self.menuBar().addMenu("API")
        self.action_regenerate_api_key = api_menu.addAction("Regenerate Key")
        self.action_regenerate_api_key.triggered.connect(self.on_regenerate_api_key_clicked)

        tools_menu = self.menuBar().addMenu("Tools")
        self.action_analysis_mode = tools_menu.addAction("Analysis Mode…")
        self.action_analysis_mode.triggered.connect(self.on_open_analysis_mode_clicked)

        self.seq_timer = QTimer(self)
        self.seq_timer.timeout.connect(self.update_seq_progress)

        self.temp_poll_timer = QTimer(self)
        self.temp_poll_timer.setInterval(5000)

        self.update_plot_labels()

        if self.seq_dir and os.path.isdir(self.seq_dir):
            display_path = self.seq_dir if len(self.seq_dir) < 25 else "..." + self.seq_dir[-22:]
            self.lbl_seq_dir.setText(f"Dir: {display_path}")
            self.btn_start_seq.setEnabled(True)
            self._set_button_style(self.btn_start_seq, self.BUTTON_STYLE_BLUE)
        else:
            self.seq_dir = ""

        self.radio_spec_mode_wl.toggled.connect(self.sync_pressure_calculator_mode)
        self.radio_spec_mode_raman.toggled.connect(self.sync_pressure_calculator_mode)
        
        self.spec_ctrl.initialize()

        if self.spec_ctrl.is_initialized and hasattr(self.spec_ctrl, "get_gratings"):
            # Warn-only: catches a spectrometerConfig.json left stale after a physical
            # grating swap. Never auto-edits the config -- see work/work_PI_grating.md Step B.
            detected_gratings = self.spec_ctrl.get_gratings()
            if detected_gratings:
                detected_by_index = {g["index"]: g["grooves"] for g in detected_gratings}
                mismatches = [
                    f"Slot {g.get('index')}: config={g.get('grooves')} g/mm, "
                    f"spectrometer={detected_by_index[g.get('index')]} g/mm"
                    for g in self.config.get("grating", [])
                    if g.get("index") in detected_by_index
                    and detected_by_index[g.get("index")] != g.get("grooves")
                ]
                if mismatches:
                    QMessageBox.warning(
                        self, "Grating configuration mismatch",
                        "spectrometerConfig.json does not match what the spectrometer "
                        "reports:\n\n" + "\n".join(mismatches) +
                        "\n\nIf a grating was physically swapped, update it via "
                        "Hardware > Hardware Configuration."
                    )

        if hasattr(self.spec_ctrl, "get_device_identity"):
            spec_identity = self.spec_ctrl.get_device_identity()
            self._spectrometer_identity = {
                "model": spec_identity.get("model") or None,
                "serial_number": spec_identity.get("serial_number") or None,
            }
            self.check_and_record_hardware_identity(
                "spectrometer", spec_identity.get("model"), spec_identity.get("serial_number")
            )
            self._update_window_title()

        current_wl = self.spec_ctrl.get_wavelength()
        print(f"[Init] Spectrometer centre wavelength readback: {current_wl} nm")
        self.physical_center_wl = current_wl
        self.spin_centre_wl.setValue(current_wl)

        current_grating_idx = self.spec_ctrl.get_grating()
        print(f"[Init] Spectrometer grating readback: index {current_grating_idx}")
        target_cb_idx = 0
        for i, g in enumerate(self.config.get("grating", [])):
            if g.get("index") == current_grating_idx:
                target_cb_idx = i
                break
                
        if 0 <= target_cb_idx < len(self.grating_list):
            self.combo_grating.setCurrentIndex(target_cb_idx)
        else:
            self.combo_grating.setCurrentIndex(0)
            
        self.physical_grating = self.combo_grating.currentText()
        self.btn_apply_spec.setEnabled(False)
        
        roi_f, roi_t = self.get_roi_for_grating(self.physical_grating)
        self.spin_vstart.blockSignals(True)
        self.spin_vend.blockSignals(True)
        self.spin_vstart.setValue(roi_f)
        self.spin_vend.setValue(roi_t)
        self.spin_vstart.blockSignals(False)
        self.spin_vend.blockSignals(False)

        self.centralWidget().setEnabled(False)
        self.init_dialog = QDialog(self)
        self.init_dialog.setWindowTitle("Please Wait")
        self.init_dialog.setModal(True)
        self.init_dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Initialising camera...\nPlease wait until the operation is completed."
        ))
        self.init_dialog.setLayout(layout)
        self.init_dialog.show()

        self.thread = CameraThread(config=self.config, debug=self.debug)
        self.thread.data_ready.connect(self.on_data_ready)
        self.thread.init_finished.connect(self.on_camera_initialized)
        self.thread.init_failed.connect(self.on_camera_init_failed)
        self.thread.hardware_error.connect(self.on_hardware_error)
        self.thread.temperature_ready.connect(self.on_temperature_read)
        self.thread.temperature_capability_ready.connect(self.on_temperature_capability_ready)
        self.thread.acquisition_failed.connect(self.on_acquisition_failed)
        self.thread.em_gain_info_ready.connect(self.on_em_gain_info_ready)
        self.thread.identity_ready.connect(self.on_camera_identity_ready)
        if hasattr(self.thread, "status_ready"):
            self.thread.status_ready.connect(self._api_on_camera_status_ready)
        if hasattr(self.thread, "exposure_applied"):
            self.thread.exposure_applied.connect(self.on_exposure_applied)
        if hasattr(self.thread, "roi_applied"):
            self.thread.roi_applied.connect(self.on_roi_applied)

        self.thread.exposure_set_finished.connect(lambda: self.spin_acq_time.setEnabled(True))
        self.thread.em_gain_set_finished.connect(self.on_em_gain_set_finished)
        self.thread.temperature_set_finished.connect(self.on_temperature_set_finished)
        self.temp_poll_timer.timeout.connect(self._poll_temperature)

        self.thread.start()

    def _update_window_title(self):
        """Show the connected instrument models once both have been identified."""
        if self.debug:
            return

        title = live_window_title(
            self.debug,
            self._spectrometer_identity.get("model"),
            self._camera_identity.get("model"),
        )
        if title:
            self.setWindowTitle(title)

    def _set_button_style(self, button, enabled_style):
        button.setStyleSheet(colored_button_style(enabled_style, BUTTON_STYLE_DISABLED))

    def closeEvent(self, event):
        confirmation = self._close_confirmation_message()
        reply = QMessageBox.question(
            self, "Confirm Exit",
            confirmation,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if getattr(self, '_api_server', None) is not None:
                self.stop_api_server()
            if (
                self.instrument_status_window is not None
                and not self.instrument_status_window.shutdown()
            ):
                QMessageBox.warning(
                    self,
                    "Instrument status is busy",
                    "The instrument status query is still using the spectrograph. "
                    "Please wait a moment and close the application again.",
                )
                event.ignore()
                return
            self.thread.stop_thread()
            self.spec_ctrl.close()
            event.accept()
        else:
            event.ignore()

    def _close_confirmation_message(self):
        if (
            getattr(self, "_temp_capability_known", False)
            and getattr(self, "_temp_control_available", False)
        ):
            return (
                "Closing this window will terminate the cooler of the camera. "
                "Are you sure you want to close FluoRaPressée?"
            )
        return "Are you sure you want to close FluoRaPressée?"
