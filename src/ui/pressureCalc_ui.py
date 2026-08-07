from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
                             QLabel, QDoubleSpinBox, QAbstractSpinBox, QWidget,
                             QRadioButton, QHBoxLayout, QGroupBox, QPushButton)
from PyQt6.QtCore import Qt
from src.core.pressureCalc import PressureCalculator

class CustomDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

class PressureCalculatorWindow(QDialog):
    def __init__(self, parent=None, mode="nm", embedded=False, fit_controls_owner=None):
        super().__init__(parent)
        self.embedded = embedded
        self._fit_controls_owner = fit_controls_owner if fit_controls_owner is not None else parent
        if self.embedded:
            # QDialog is normally always a top-level window. Qt.WindowType.Widget keeps this
            # exact same calculator UI usable as an in-layout child widget.
            self.setWindowFlags(Qt.WindowType.Widget)
        self.unit = mode
        self.setWindowTitle("Pressure Calculator")
        if not self.embedded:
            self.resize(450, 700)
        self.current_peak_val = 0.0
        self.current_peak_err = 0.0
        self.current_pressure = None       
        self.current_pressure_err = None
        self.current_zero_peak_at_current_t = None
        self.current_fit_peaks = []
        self.fit_peak_count = 1
        self.peak_selection_for_pressure_calc = 1
        self.init_ui()
        self.unconstrained_width = self.minimumSizeHint().width()
        if self.embedded:
            # Keep the shared calculator readable in the narrower embedded column.
            # Newlines preserve the full labels without letting these two buttons
            # dictate the width of the whole Analysis Mode window.
            self.btn_apply_current.setText(
                "Use the current value as\nzero-pressure peak position"
            )
            self.btn_set_lam0_t0.setText(
                "Use the Current Value as the\nzero-pressure peak position at T0"
            )
        self.setup_connections()
        self.update_mode(self.unit)

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 1. Base settings (Sensor / Pressure Scale)
        top_group = QGroupBox("Base Settings")
        form = QFormLayout()

        self.combo_sensor = QComboBox()
        self.combo_p_scale = QComboBox()
        form.addRow("Sensor:", self.combo_sensor)
        form.addRow("Pressure Scale:", self.combo_p_scale)

        self.lbl_cur_peak = QLabel(f"0.000 {self.unit}")
        form.addRow(f"Current peak ({self.unit}):", self.lbl_cur_peak)

        self.combo_pressure_peak = QComboBox()
        form.addRow("Calculate pressure by:", self.combo_pressure_peak)

        self.lbl_t_mandatory = QLabel("")
        self.lbl_t_mandatory.setStyleSheet("height: 0;")
        form.addRow(self.lbl_t_mandatory)

        # Button to apply the current value (hidden/disabled when temperature correction is ON)
        self.btn_apply_current = QPushButton("Use the current value as zero-pressure peak position")
        self.btn_apply_current.setAutoDefault(False)  
        self.btn_apply_current.setDefault(False)
        form.addRow(self.btn_apply_current)

        self.spin_lam0 = CustomDoubleSpinBox()
        self.spin_lam0.setRange(-99999, 99999); self.spin_lam0.setDecimals(3)
        self.lbl_lam0_tag = QLabel(f"Zero-pressure peak ({self.unit}):")
        form.addRow(self.lbl_lam0_tag, self.spin_lam0)

        self.lbl_zero_peak_current_t_tag = QLabel(f"Zero-pressure peak at current T ({self.unit}):")
        self.lbl_zero_peak_current_t = QLabel("")
        form.addRow(self.lbl_zero_peak_current_t_tag, self.lbl_zero_peak_current_t)
        self.lbl_zero_peak_current_t_tag.hide()
        self.lbl_zero_peak_current_t.hide()

        top_group.setLayout(form)
        layout.addWidget(top_group)

        # 2. Temperature correction group
        self.temp_group = QGroupBox("Temperature Correction")
        temp_v_layout = QVBoxLayout()

        # On/Off radio buttons
        self.radio_widget = QWidget()
        radio_h = QHBoxLayout(self.radio_widget)
        self.radio_off = QRadioButton("Off"); self.radio_on = QRadioButton("On")
        self.radio_off.setChecked(True)
        radio_h.addWidget(self.radio_off); radio_h.addWidget(self.radio_on)
        temp_v_layout.addWidget(self.radio_widget)

        # Detailed correction settings form
        self.t_form_widget = QWidget()
        self.t_form = QFormLayout(self.t_form_widget)

        self.combo_t_scale = QComboBox()
        self.lbl_t_scale_tag = QLabel("Temperature Scale:")
        self.t_form.addRow(self.lbl_t_scale_tag, self.combo_t_scale)

        

        

        self.spin_t = CustomDoubleSpinBox(); self.spin_t.setRange(0, 5000); self.spin_t.setValue(300)
        self.spin_t0 = CustomDoubleSpinBox(); self.spin_t0.setRange(0, 5000); self.spin_t0.setValue(300)

        self.lbl_t_warning = QLabel("")
        self.lbl_t_warning.setStyleSheet("color: red; font-weight: bold;")

        self.t_form.addRow("Current T (K):", self.spin_t)
        self.t_form.addRow("", self.lbl_t_warning)
        self.t_form.addRow("Reference T0 (K):", self.spin_t0)

        self.lbl_t0_fixed_note = QLabel("")
        self.lbl_t0_fixed_note.setStyleSheet("color: #666; font-style: italic;")
        self.t_form.addRow("", self.lbl_t0_fixed_note)
        self.lbl_t0_fixed_note.hide()

        self.spin_lam0_t0 = CustomDoubleSpinBox()
        self.spin_lam0_t0.setRange(-99999, 99999); self.spin_lam0_t0.setDecimals(3)
        self.lbl_lam0_t0_tag = QLabel(f"Zero-pressure peak at T0 ({self.unit}):")
        self.t_form.addRow(self.lbl_lam0_t0_tag, self.spin_lam0_t0)

        self.btn_set_lam0_t0 = QPushButton("Use the Current Value as the zero-pressure peak position at T0")
        self.btn_set_lam0_t0.setAutoDefault(False)  # Prevent the button from being triggered by pressing Enter
        self.btn_set_lam0_t0.setDefault(False)
        self.t_form.addRow(self.btn_set_lam0_t0)

        temp_v_layout.addWidget(self.t_form_widget)
        self.temp_group.setLayout(temp_v_layout)
        layout.addWidget(self.temp_group)

        # 3. Result display
        self.lbl_result = QLabel()
        self.lbl_result.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_result.setStyleSheet("background: #333; color: white; padding: 15px; border-radius: 5px;")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setText(
            "<div style='text-align:center;'>"
            "<span style='font-size:24px; font-weight:bold;'>P = --- GPa</span>"
            "</div>"
        )
        layout.addWidget(self.lbl_result)

    def setup_connections(self):
        self.combo_sensor.currentTextChanged.connect(self.on_sensor_changed)
        self.combo_p_scale.currentTextChanged.connect(self.on_p_scale_changed)
        self.combo_t_scale.currentTextChanged.connect(self.calculate)
        self.combo_pressure_peak.currentIndexChanged.connect(self.on_pressure_peak_selection_changed)
        self.spin_lam0.valueChanged.connect(self.calculate)
        self.spin_lam0_t0.valueChanged.connect(self.calculate)
        self.spin_t.valueChanged.connect(self.calculate)
        self.spin_t0.valueChanged.connect(self.calculate)
        self.radio_on.toggled.connect(self.toggle_temp_ui)

        # Wire up button signals
        self.btn_apply_current.clicked.connect(self.apply_current_to_lam0)
        self.btn_set_lam0_t0.clicked.connect(self.apply_current_to_lam0_t0)

    def toggle_temp_ui(self):
        is_on = self.radio_on.isChecked()
        self.t_form_widget.setEnabled(is_on)
        self.spin_lam0.setEnabled(not is_on)
        self.spin_lam0.setVisible(not is_on)
        self.lbl_lam0_tag.setVisible(not is_on)
        self.btn_apply_current.setEnabled(not is_on)
        self.btn_apply_current.setVisible(not is_on)
        if not is_on:
            self._set_zero_peak_at_current_t(None)
        self._apply_t0_constraint()
        self.calculate()

    def apply_current_to_lam0(self):
        """現在のピーク値を λ0 に適用 (温度補正OFF用)"""
        if self.current_peak_val != 0:
            self.spin_lam0.setValue(self.current_peak_val)

    def apply_current_to_lam0_t0(self):
        """現在のピーク値を λ0 at T0 に適用 (温度補正ON用)"""
        if self.current_peak_val != 0:
            self.spin_lam0_t0.setValue(self.current_peak_val)

    def calculate(self):
        sensor = self.combo_sensor.currentData()
        p_scale = self.combo_p_scale.currentData()
        t_scale = self.combo_t_scale.currentData()
        curr_t = self.spin_t.value()
        if sensor is None or p_scale is None:
            return

        try:
            PressureCalculator.validate_fit_pressure_pair(
                fit_function=self._current_fit_function(), sensor=sensor, p_scale=p_scale
            )
        except ValueError as exc:
            self.current_pressure = None
            self.current_pressure_err = None
            self._set_zero_peak_at_current_t(None)
            self.lbl_result.setText(
                "<div style='text-align:center; color:#ff8a80;'>"
                "<span style='font-size:20px; font-weight:bold;'>Configuration Error</span><br>"
                f"<span style='font-size:16px;'>{exc}</span></div>"
            )
            return

        is_valid, rng = PressureCalculator.is_temp_in_range(
            sensor=sensor, p_scale=p_scale, t_scale=t_scale, temp=curr_t
        )
        if self.radio_on.isChecked() and not is_valid and rng[0] is not None:
            self.lbl_t_warning.setText(f"Warning: T out of range ({rng[0]} - {rng[1]} K)")
            self.spin_t.setStyleSheet("background-color: #FFCCCC; color: red;")
        else:
            self.lbl_t_warning.setText("")
            self.spin_t.setStyleSheet("")

        if self.current_peak_val == 0:
            self.current_pressure = None
            self.current_pressure_err = None
            self.lbl_result.setText(self._build_result_html(None, None))
            return

        result = PressureCalculator.calculate(
            sensor=sensor, p_scale=p_scale, peak=self.current_peak_val,
            zero_peak=self.spin_lam0.value(),
            zero_peak_at_t0=self.spin_lam0_t0.value(),
            peak_err=self.current_peak_err,
            temperature_correction_enabled=self.radio_on.isChecked(),
            t_scale=t_scale,
            current_t=curr_t, t0=self._current_t0()
        )
        self._set_zero_peak_at_current_t(result.zero_peak_at_current_t)
        if result.pressure is not None:
            self.current_pressure = result.pressure
            self.current_pressure_err = result.pressure_err
            self.lbl_result.setText(self._build_result_html(result.pressure, result.pressure_err))
        else:
            self.current_pressure = None
            self.current_pressure_err = None
            self.lbl_result.setText("<span style='font-size:20px;'>Calc Error</span>")

    def update_mode(self, mode):
        self.unit = mode
        self.lbl_lam0_tag.setText(f"Zero-pressure peak ({mode}):")
        self.lbl_zero_peak_current_t_tag.setText(f"Zero-pressure peak at current T ({mode}):")
        self.lbl_lam0_t0_tag.setText(f"Zero-pressure peak at T0 ({mode}):")
        self.combo_sensor.blockSignals(True); self.combo_sensor.clear()
        for sensor_key in PressureCalculator.get_sensors_for_unit(mode):
            self.combo_sensor.addItem(PressureCalculator.SENSORS[sensor_key]["label"], sensor_key)
        self.combo_sensor.blockSignals(False)
        self.on_sensor_changed()

    def on_sensor_changed(self):
        sensor = self.combo_sensor.currentData()
        if sensor is None:
            return

        default_val = PressureCalculator.SENSORS[sensor]["initial_value"]
        self.spin_lam0.setValue(default_val)
        self.spin_lam0_t0.setValue(default_val)
        self._set_zero_peak_at_current_t(None)
        self.reset_peak_selection_for_pressure_calc()

        self.combo_p_scale.blockSignals(True); self.combo_p_scale.clear()
        self.combo_t_scale.blockSignals(True); self.combo_t_scale.clear()

        for scale_key, scale_meta in PressureCalculator.PRESSURE_SCALES.get(sensor, {}).items():
            self.combo_p_scale.addItem(scale_meta["label"], scale_key)

        for scale_key, scale_meta in PressureCalculator.TEMPERATURE_SCALES.get(sensor, {}).items():
            self.combo_t_scale.addItem(scale_meta["label"], scale_key)

        self.combo_p_scale.blockSignals(False); self.combo_t_scale.blockSignals(False)
        self.on_p_scale_changed()
        # Re-run validation after all sensor/scale widgets have settled.  In
        # particular, do not overwrite a fit/scale configuration error with an
        # empty pressure result while switching sensors.
        self.calculate()

    def on_p_scale_changed(self):
        sensor = self.combo_sensor.currentData()
        scale = self.combo_p_scale.currentData()
        is_pt_scale = PressureCalculator.pressure_scale_requires_temperature(sensor=sensor, p_scale=scale)

        self.radio_widget.setVisible(not is_pt_scale)
        self.lbl_t_scale_tag.setVisible(not is_pt_scale)
        self.combo_t_scale.setVisible(not is_pt_scale)
        if is_pt_scale:
            self.radio_on.setChecked(True)
            self.lbl_t_mandatory.setText("Temperature input is mandatory for this scale!")
            self.lbl_t_mandatory.setStyleSheet("color: red; font-weight: bold; height: 1em;")
        else:
            self.lbl_t_mandatory.setText("")
            self.lbl_t_mandatory.setStyleSheet("height: 0em;")
        self._apply_t0_constraint()
        self.toggle_temp_ui()
        self._apply_edge_scale_ui()
        self._apply_recommended_fit_peak_count()

    def _current_fit_function(self):
        owner = self._fit_controls_owner
        if owner is not None and hasattr(owner, "combo_fit_func"):
            return owner.combo_fit_func.currentText()
        return ""

    def _apply_edge_scale_ui(self):
        sensor = self.combo_sensor.currentData()
        scale = self.combo_p_scale.currentData()
        is_edge = PressureCalculator.is_diamond_edge_scale(sensor=sensor, p_scale=scale)
        scale_zero = PressureCalculator.get_scale_zero_peak(sensor=sensor, p_scale=scale)
        if is_edge and scale_zero is not None:
            for spin in (self.spin_lam0, self.spin_lam0_t0):
                spin.blockSignals(True)
                spin.setValue(scale_zero)
                spin.blockSignals(False)
                spin.setEnabled(False)
            self.lbl_lam0_tag.setText(f"Scale reference ν0 ({self.unit}):")
            self.btn_apply_current.setVisible(False)
            self.temp_group.setVisible(False)
        else:
            self.lbl_lam0_tag.setText(f"Zero-pressure peak ({self.unit}):")
            self.spin_lam0.setEnabled(not self.radio_on.isChecked())
            self.spin_lam0_t0.setEnabled(True)
            self.btn_apply_current.setVisible(not self.radio_on.isChecked())
            self.temp_group.setVisible(True)
        self.calculate()

    def _apply_recommended_fit_peak_count(self):
        sensor = self.combo_sensor.currentData()
        recommended = None
        if sensor == "ruby":
            recommended = 2
        elif sensor == "sm_srb4o7":
            recommended = 1
        elif sensor == "sm_yag_y1":
            recommended = 1
        elif sensor == "diamond_raman_edge":
            recommended = 1
        elif sensor == "quartz_464":
            recommended = 1
        elif sensor == "quartz_128":
            recommended = 1

        if recommended is None:
            self.reset_peak_selection_for_pressure_calc()
            return

        owner = self._fit_controls_owner
        if owner is not None and hasattr(owner, "combo_fit_peak_count"):
            if owner.combo_fit_peak_count.currentData() != recommended:
                owner.combo_fit_peak_count.setCurrentIndex(owner.combo_fit_peak_count.findData(recommended))
            else:
                self.set_fit_peak_count(recommended, reset_selection=True)
        else:
            self.set_fit_peak_count(recommended, reset_selection=True)

    def _current_t0(self):
        sensor = self.combo_sensor.currentData()
        p_scale = self.combo_p_scale.currentData()
        if sensor is None or p_scale is None:
            return self.spin_t0.value()
        return PressureCalculator.resolve_t0(sensor=sensor, p_scale=p_scale, t0=self.spin_t0.value())

    def _apply_t0_constraint(self):
        sensor = self.combo_sensor.currentData()
        p_scale = self.combo_p_scale.currentData()
        if sensor is None or p_scale is None:
            self.spin_t0.setEnabled(True)
            self.lbl_t0_fixed_note.setText("")
            self.lbl_t0_fixed_note.hide()
            return

        fixed_t0 = PressureCalculator.get_fixed_t0(sensor=sensor, p_scale=p_scale)
        if fixed_t0 is None:
            self.spin_t0.setEnabled(True)
            self.lbl_t0_fixed_note.setText("")
            self.lbl_t0_fixed_note.hide()
            return

        self.spin_t0.blockSignals(True)
        self.spin_t0.setValue(fixed_t0)
        self.spin_t0.blockSignals(False)
        self.spin_t0.setEnabled(False)
        self.lbl_t0_fixed_note.setText(PressureCalculator.get_fixed_t0_note(sensor=sensor, p_scale=p_scale))
        self.lbl_t0_fixed_note.show()

    def _set_zero_peak_at_current_t(self, value):
        self.current_zero_peak_at_current_t = value
        should_show = self.radio_on.isChecked() and value is not None
        self.lbl_zero_peak_current_t_tag.setVisible(should_show)
        self.lbl_zero_peak_current_t.setVisible(should_show)
        if value is not None:
            self.lbl_zero_peak_current_t.setText(f"{value:.3f}")
        else:
            self.lbl_zero_peak_current_t.setText("")

    def _refresh_pressure_peak_combo(self):
        count = max(1, int(self.fit_peak_count))
        current = min(max(1, int(self.peak_selection_for_pressure_calc)), count)
        self.combo_pressure_peak.blockSignals(True)
        self.combo_pressure_peak.clear()
        for i in range(1, count + 1):
            self.combo_pressure_peak.addItem(f"Peak {i}", i)
        self.combo_pressure_peak.setCurrentIndex(current - 1)
        self.combo_pressure_peak.blockSignals(False)
        self.peak_selection_for_pressure_calc = current

    def reset_peak_selection_for_pressure_calc(self):
        self.peak_selection_for_pressure_calc = 1
        self._refresh_pressure_peak_combo()
        self._update_current_peak_from_selection()

    def on_pressure_peak_selection_changed(self):
        selected = self.combo_pressure_peak.currentData()
        self.peak_selection_for_pressure_calc = selected if selected is not None else 1
        self._update_current_peak_from_selection()
        self.calculate()

    def set_fit_peak_count(self, peak_count, reset_selection=False):
        self.fit_peak_count = max(1, min(5, int(peak_count)))
        if self.current_fit_peaks and len(self.current_fit_peaks) != self.fit_peak_count:
            self.current_fit_peaks = []
        if reset_selection:
            self.peak_selection_for_pressure_calc = 1
        self._refresh_pressure_peak_combo()
        self._update_current_peak_from_selection()
        self.calculate()

    def set_fit_peaks(self, peaks):
        self.current_fit_peaks = list(peaks or [])
        if self.current_fit_peaks:
            self.fit_peak_count = len(self.current_fit_peaks)
        self._refresh_pressure_peak_combo()
        self._update_current_peak_from_selection()
        self.calculate()

    def _update_current_peak_from_selection(self):
        idx = self.peak_selection_for_pressure_calc - 1
        if 0 <= idx < len(self.current_fit_peaks):
            peak = self.current_fit_peaks[idx]
            self.current_peak_val = peak.get("position", 0.0)
            self.current_peak_err = peak.get("position_err", 0.0)
            self.lbl_cur_peak.setText(f"{self.current_peak_val:.3f} {self.unit}")
        elif self.current_fit_peaks:
            peak = self.current_fit_peaks[0]
            self.current_peak_val = peak.get("position", 0.0)
            self.current_peak_err = peak.get("position_err", 0.0)
            self.lbl_cur_peak.setText(f"{self.current_peak_val:.3f} {self.unit}")
        else:
            self.current_peak_val = 0.0
            self.current_peak_err = 0.0
            self.lbl_cur_peak.setText(f"0.000 {self.unit}")

    def _build_result_html(self, p, dp):
        """結果表示ラベル用のHTMLを生成する"""
        sensor = self.combo_sensor.currentText()
        p_scale = self.combo_p_scale.currentText()

        if p is not None:
            pressure_line = f"P = {p:.3f} &plusmn; {dp:.3f} GPa"
        else:
            pressure_line = "P = --- GPa"

        if self.unit == "nm":
            peak_line = f"&lambda; = {self.current_peak_val:.3f} nm"
        else:
            peak_line = f"&nu; = {self.current_peak_val:.3f} cm<sup>-1</sup>"

        info_line = f"{sensor}, {p_scale}"

        return (
            f"<div style='text-align:center;'>"
            f"<span style='font-size:24px; font-weight:bold;'>{pressure_line}</span><br>"
            f"<span style='font-size:20px; margin-top: 20px'>{peak_line}</span><br>"
            f"<span style='font-size:18px; margin-top: 12px'>{info_line}</span>"
            f"</div>"
        )

    def set_current_peak(self, val, err=0.0):
        self.set_fit_peaks([{
            "index": 1,
            "position": val,
            "position_err": err,
            "width": 0.0,
            "width_err": 0.0,
        }])
