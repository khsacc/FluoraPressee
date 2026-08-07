from PyQt6.QtWidgets import QVBoxLayout, QLabel, QDialog, QMessageBox, QPushButton
from PyQt6.QtCore import Qt

from src.hardware.spectrometer import SpectrometerMoveThread
from src.ui.calibration_ui import CalibrationWindow
from src.core.configuration_catalog import excitation_wavelength_key
from src.core.measurement_metadata import public_axis_kind


class SpectrometerControlMixin:
    def _sync_controls_to_display_mode(self):
        """Every side effect of the Wavelength/Raman display toggle besides the
        toggle itself and calibration invalidation (each caller decides that on
        its own terms): spin_exc_wl's enabled state, the displayed centre value
        (recomputed from physical_center_wl, since spin_centre_wl's own nm<->cm-1
        meaning flips with the toggle), the Apply button's state, the plot's
        axis label, fit-range-dependent UI, and the Pressure Window's sensor
        list. Shared by on_spec_mode_changed() (a user-driven toggle) and
        apply_calibration()'s _sync_display_mode_to_unit() (a programmatic
        toggle to match a calibration being applied) so neither can drift from
        what the other keeps in sync.
        """
        is_raman = self.radio_spec_mode_raman.isChecked()
        self.spin_exc_wl.setEnabled(is_raman)

        if is_raman:
            self.lbl_centre.setText("Centre (cm⁻¹):")
            ex_wl = self.spin_exc_wl.value()
            if ex_wl > 0 and self.physical_center_wl > 0:
                new_center = (1e7 / ex_wl) - (1e7 / self.physical_center_wl)
            else:
                new_center = 0.0
        else:
            self.lbl_centre.setText("Centre (nm):")
            new_center = self.physical_center_wl

        self.spin_centre_wl.blockSignals(True)
        self.spin_centre_wl.setValue(new_center)
        self.spin_centre_wl.blockSignals(False)

        self.check_spectrometer_changes()
        self.update_plot_labels()
        self.on_fit_settings_changed()
        self.sync_pressure_calculator_mode()

    def on_spec_mode_changed(self):
        # The centralWidget is fully disabled while the spectrometer moves
        # (_show_spectrometer_moving_dialog calls centralWidget().setEnabled(False)),
        # so this handler should not fire during movement. Return as a safety guard.
        if hasattr(self, 'spec_move_thread') and self.spec_move_thread.isRunning():
            return

        is_raman = self.radio_spec_mode_raman.isChecked()
        if self.calib_coeffs is not None:
            new_unit = "Raman shift" if is_raman else "Wavelength"
            if new_unit != self.calib_unit:
                self.deactivate_axis_calibration("display mode changed")

        self._sync_controls_to_display_mode()

    def on_exc_wl_changed(self):
        if self.calib_coeffs is not None and self.calib_unit == "Raman shift":
            if (
                self.calib_laser_wl is None
                or excitation_wavelength_key(self.spin_exc_wl.value())
                != excitation_wavelength_key(self.calib_laser_wl)
            ):
                self.deactivate_axis_calibration("excitation wavelength changed")

        if self.radio_spec_mode_raman.isChecked():
            ex_wl = self.spin_exc_wl.value()
            if ex_wl > 0 and self.physical_center_wl > 0:
                new_center = (1e7 / ex_wl) - (1e7 / self.physical_center_wl)
            else:
                new_center = 0.0
            self.spin_centre_wl.blockSignals(True)
            self.spin_centre_wl.setValue(new_center)
            self.spin_centre_wl.blockSignals(False)
            self.check_spectrometer_changes()

    def update_plot_labels(self):
        axis_kind = public_axis_kind(self)
        if axis_kind == "calibrated":
            if self.radio_spec_mode_raman.isChecked():
                x_label = 'Raman shift (cm⁻¹)'
            else:
                x_label = 'Wavelength (nm)'
        elif axis_kind == "native_wavelength":
            if self.radio_spec_mode_raman.isChecked():
                x_label = 'Raman shift (cm⁻¹) [Ocean Optics factory calibration]'
            else:
                x_label = 'Wavelength (nm) [Ocean Optics factory calibration]'
        else:
            x_label = 'Pixel'

        self.plot_widget.setLabel('bottom', x_label)
        self.image_view.getView().setLabel('bottom', x_label)
        self.lbl_axis_warning.setVisible(axis_kind == "native_wavelength")

    def check_spectrometer_changes(self, *args):
        curr_g = self.combo_grating.currentText()
        val = self.spin_centre_wl.value()

        if self.radio_spec_mode_raman.isChecked():
            ex_wl = self.spin_exc_wl.value()
            if ex_wl > 0:
                try:
                    # Check for invalid Raman shift value (when Raman shift >= excitation wavenumber)
                    denom = 1e7 / ex_wl - val
                    if denom <= 0:
                        # Invalid state: Raman shift is too large
                        self.btn_apply_spec.setEnabled(False)
                        return
                    target_wl = 1e7 / denom
                except (ZeroDivisionError, ValueError):
                    # If Raman shift calculation fails, disable apply button
                    self.btn_apply_spec.setEnabled(False)
                    return
            else:
                # Invalid state: ex_wl should never be <= 0 in Raman mode
                # Cannot calculate target wavelength from Raman shift
                self.btn_apply_spec.setEnabled(False)
                return
        else:
            target_wl = val

        if curr_g == self.physical_grating and abs(target_wl - self.physical_center_wl) < 1e-4:
            self.btn_apply_spec.setEnabled(False)
        else:
            self.btn_apply_spec.setEnabled(True)

    def on_flip_x_changed(self):
        self.config["flip_x"] = self.chk_flip_x.isChecked()
        self.save_config_to_file()
        if getattr(self, 'raw_1d_data', None) is not None and hasattr(self.thread, 'is_measuring') and not self.thread.is_measuring:
            self.update_display(is_new_data=False)

    def on_calibrate_neon(self):
        was_measuring = self.thread.is_measuring
        if was_measuring:
            self.stop_measurement()  # also releases the acquisition gate

        if not self._try_acquire_gate():
            QMessageBox.warning(self, "Busy", "Another acquisition is already in progress.")
            return

        calib_win = CalibrationWindow(camera_thread=self.thread, parent=self)
        calib_win.exec()

        self._release_acquisition_gate()

        if was_measuring:
            # Re-acquire before resuming; if something else grabbed the gate in the brief
            # window since release (e.g. a concurrent API request), skip the resume rather
            # than starting an unprotected measurement.
            if self._try_acquire_gate():
                self.start_measurement()
            else:
                QMessageBox.warning(self, "Busy", "Could not resume measurement: acquisition is busy.")

    def _set_spectrometer_controls_enabled(self, enabled):
        # A completed API-triggered move must not partially undo the broader
        # API-server/sequential UI lock. _unlock_ui() will restore controls
        # after every lock reason has been removed.
        if enabled and getattr(self, "_ui_lock_reasons", set()):
            enabled = False
        self.combo_grating.setEnabled(enabled)
        self.radio_spec_mode_wl.setEnabled(enabled)
        self.radio_spec_mode_raman.setEnabled(enabled)
        self.spin_centre_wl.setEnabled(enabled)
        self.spin_exc_wl.setEnabled(enabled and self.radio_spec_mode_raman.isChecked())
        self.btn_calib_neon.setEnabled(enabled)
        self.btn_load_configuration.setEnabled(enabled)
        if enabled:
            self.check_spectrometer_changes()
        else:
            self.btn_apply_spec.setEnabled(False)

    def _show_spectrometer_moving_dialog(self):
        if getattr(self, 'spec_move_dialog', None) is not None:
            return
        self.centralWidget().setEnabled(False)
        self.spec_move_dialog = QDialog(self)
        self.spec_move_dialog.setWindowTitle("Spectrometer is moving")
        self.spec_move_dialog.setModal(True)
        self.spec_move_dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        layout = QVBoxLayout(self.spec_move_dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel("Spectrometer is moving. Please wait..."))

        # Cancelling (MONO-STOP) is only meaningful for the Princeton serial
        # backend, and only during the wavelength-move phase (grating turret
        # changes have no documented abort). See on_spectrometer_move_phase.
        self.spec_move_cancel_btn = None
        if hasattr(self.spec_ctrl, "request_cancel_move"):
            self.spec_move_cancel_btn = QPushButton("Cancel")
            self.spec_move_cancel_btn.setEnabled(False)
            self.spec_move_cancel_btn.clicked.connect(self.on_cancel_spectrometer_move)
            layout.addWidget(self.spec_move_cancel_btn)
            self.spec_move_dialog.setFixedSize(320, 130)
        else:
            self.spec_move_dialog.setFixedSize(320, 100)

        self.spec_move_dialog.setLayout(layout)
        self.spec_move_dialog.show()
        self.spec_move_dialog.raise_()
        self.spec_move_dialog.activateWindow()

    def _close_spectrometer_moving_dialog(self):
        dialog = getattr(self, 'spec_move_dialog', None)
        if dialog is not None:
            dialog.accept()
            self.spec_move_dialog = None
        self.spec_move_cancel_btn = None
        self.centralWidget().setEnabled(True)

    def on_spectrometer_move_phase(self, phase):
        """Enable Cancel only once the wavelength-move phase begins (see
        SpectrometerMoveThread.phase_signal)."""
        if self.spec_move_cancel_btn is not None:
            self.spec_move_cancel_btn.setEnabled(phase == "wavelength")

    def on_cancel_spectrometer_move(self):
        if self.spec_move_cancel_btn is not None:
            self.spec_move_cancel_btn.setEnabled(False)
            self.spec_move_cancel_btn.setText("Cancelling...")
        if hasattr(self, 'spec_move_thread'):
            self.spec_move_thread.request_cancel()

    def on_apply_spectrometer(self):
        combo_index = self.combo_grating.currentIndex()
        gratings = self.config.get("grating", [])
        if 0 <= combo_index < len(gratings):
            grating_index = int(gratings[combo_index].get("index", combo_index + 1))
        else:
            grating_index = combo_index + 1
        val = self.spin_centre_wl.value()

        if self.radio_spec_mode_raman.isChecked():
            ex_wl = self.spin_exc_wl.value()
            if ex_wl > 0:
                try:
                    target_wl = 1e7 / (1e7 / ex_wl - val)
                except ZeroDivisionError:
                    target_wl = val
            else:
                target_wl = val
        else:
            target_wl = val

        self._set_spectrometer_controls_enabled(False)
        self._show_spectrometer_moving_dialog()

        self.spec_move_thread = SpectrometerMoveThread(self.spec_ctrl, grating_index, target_wl)
        self.spec_move_thread.finished_signal.connect(self.on_spectrometer_moved)
        if hasattr(self.spec_move_thread, "phase_signal"):
            self.spec_move_thread.phase_signal.connect(self.on_spectrometer_move_phase)
        self.spec_move_thread.start()

    def on_spectrometer_moved(self):
        if getattr(self.spec_move_thread, "cancelled", False):
            # The actual position is now wherever MONO-STOP caught it, not the old
            # value nor the requested target -- read it back from hardware instead
            # of guessing.
            self._loading_config = False
            self._close_spectrometer_moving_dialog()
            actual_wl = self.spec_ctrl.get_wavelength()
            actual_grating_idx = self.spec_ctrl.get_grating()
            for i, g in enumerate(self.config.get("grating", [])):
                if g.get("index") == actual_grating_idx and i < self.combo_grating.count():
                    self.combo_grating.setCurrentIndex(i)
                    break
            self.physical_grating = self.combo_grating.currentText()
            self.physical_center_wl = actual_wl
            if self.radio_spec_mode_raman.isChecked():
                ex_wl = self.spin_exc_wl.value()
                shown_value = (1e7 / ex_wl) - (1e7 / actual_wl) if ex_wl > 0 and actual_wl > 0 else 0.0
            else:
                shown_value = actual_wl
            self.spin_centre_wl.blockSignals(True)
            self.spin_centre_wl.setValue(shown_value)
            self.spin_centre_wl.blockSignals(False)

            # Position no longer matches whatever the pixel calibration was taken at.
            self.clear_active_configuration()
            handled_by_api = self._fail_pending_configuration(
                "The spectrometer move was cancelled."
            )

            self._set_spectrometer_controls_enabled(True)
            if not handled_by_api:
                QMessageBox.information(
                    self, "Move cancelled",
                    f"The spectrometer move was cancelled. Actual position read back: "
                    f"grating index {actual_grating_idx}, {actual_wl:.3f} nm."
                )
            return

        if getattr(self.spec_move_thread, "success", True) is False:
            message = getattr(
                self.spec_move_thread,
                "error_message",
                "The spectrometer setting change failed."
            )
            # The move may have partially completed (e.g. Princeton moves grating
            # then wavelength in sequence), and _prepare_configuration_for_loading()
            # already switched the ROI/display-mode/excitation widgets to the
            # rejected configuration's values before this move was attempted -- so
            # the previous calibration can no longer be trusted to match either the
            # physical position or what is now on screen. Fall back to pixel rather
            # than silently leaving it active, mirroring the cancelled-move branch
            # above and the plain successful-Apply path below.
            self.clear_active_configuration()
            handled_by_api = self._fail_pending_configuration(message)
            self._close_spectrometer_moving_dialog()
            self._set_spectrometer_controls_enabled(True)
            if not handled_by_api:
                QMessageBox.warning(self, "Spectrometer error", message)
            return

        self.physical_grating = self.combo_grating.currentText()
        val = self.spin_centre_wl.value()
        if self.radio_spec_mode_raman.isChecked():
            ex_wl = self.spin_exc_wl.value()
            self.physical_center_wl = 1e7 / (1e7 / ex_wl - val) if ex_wl > 0 else val
        else:
            self.physical_center_wl = val

        self.btn_apply_spec.setEnabled(False)

        if getattr(self, '_loading_config', False):
            self._finalize_pending_configuration()
        else:
            # Grating/centre-wavelength changes invalidate the pixel calibration (it was only
            # valid at the previous physical position), but must NOT touch the ROI: ROI is set
            # independently via configuration load or direct user edits only.
            self.clear_active_configuration()

        if getattr(self, 'raw_1d_data', None) is not None and hasattr(self.thread, 'is_measuring') and not self.thread.is_measuring:
            self.update_display(is_new_data=False)

        self._close_spectrometer_moving_dialog()
        self._set_spectrometer_controls_enabled(True)
