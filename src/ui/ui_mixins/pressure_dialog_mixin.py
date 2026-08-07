from PyQt6.QtWidgets import QMessageBox

from src.core.measurement_metadata import public_axis_kind
from src.ui.pressureCalc_ui import PressureCalculatorWindow


class PressureDialogMixin:
    def sync_pressure_calculator_mode(self):
        """メイン画面の nm/Raman 切り替えを圧力計算ウィンドウに即時反映させる"""
        if self.pressure_window:
            current_unit = "cm-1" if self.radio_spec_mode_raman.isChecked() else "nm"
            self.pressure_window.update_mode(current_unit)
            if public_axis_kind(self) == "pixel":
                # A toggle/excitation change may have just invalidated the axis this
                # window's peaks were computed against (deactivate_axis_calibration()) --
                # a stale pixel-based number must not keep being displayed as a pressure.
                self.pressure_window.set_fit_peaks([])

    def _warn_pressure_blocked(self, title, text):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.exec()

    def open_pressure_calculator(self):
        """圧力計算ウィンドウを開く (ボタンクリック時)"""
        current_unit = "cm-1" if self.radio_spec_mode_raman.isChecked() else "nm"

        if self.radio_fit_off.isChecked():
            self._warn_pressure_blocked(
                "Fitting required",
                "Please activate peak fitting to calculate pressure.",
            )
        elif public_axis_kind(self) == "pixel":
            # Neither a Wavelength nor a Raman-shift value exists for a bare pixel
            # axis, so a "pressure" computed from it would be meaningless.
            self._warn_pressure_blocked(
                "Calibrated axis required",
                "The x-axis is not currently calibrated (Wavelength/Raman shift). "
                "Load or apply a calibration before calculating pressure.",
            )
        else:

            if self.pressure_window is None:
                self.pressure_window = PressureCalculatorWindow(self, mode=current_unit)
            else:
                self.pressure_window.update_mode(current_unit)

            self.pressure_window.show()
            self.pressure_window.raise_()
            self.pressure_window.activateWindow()
            if getattr(self, "latest_fit_res", None) is not None and self.latest_fit_res.get("peaks"):
                self.pressure_window.set_fit_peaks(self.latest_fit_res["peaks"])
            elif hasattr(self, "combo_fit_peak_count"):
                self.pressure_window.set_fit_peak_count(self.combo_fit_peak_count.currentData())
