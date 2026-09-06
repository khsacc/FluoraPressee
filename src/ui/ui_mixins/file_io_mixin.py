import os
from datetime import datetime
import numpy as np
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from src.ui.configuration_browser import ConfigurationBrowserDialog
from src.core.configuration_catalog import (
    ConfigurationCompatibilityError,
    ConfigurationError,
    excitation_wavelength_key,
    format_configuration_label,
)
from src.core.measurement_metadata import background_mismatch_fields, build_hardware_metadata, public_axis_kind
from src.ui.ui_mixins.acquisition_mixin import GateBusyError


def _background_default_filename(
    acquisition_time, accumulations, roi_start, roi_end, custom_roi, timestamp
):
    date_str = timestamp.strftime("%Y%m%d_%H%M%S")
    roi_str = (
        f"ROI_from_{int(roi_start)}_to_{int(roi_end)}"
        if custom_roi
        else "ROI_full"
    )
    return (
        f"background_{date_str}"
        f"_acq_{float(acquisition_time):.3f}s"
        f"_accum_{int(accumulations)}"
        f"_{roi_str}.txt"
    )


class FileIOMixin:
    def check_bg_mismatch(self):
        if not self.radio_bg_on.isChecked() or getattr(self, 'loaded_bg_metadata', None) is None:
            return False

        return bool(background_mismatch_fields(self))

    def handle_bg_mismatch_and_run(self, callback) -> bool:
        """Returns True if callback was actually invoked, False if the user declined (Quit)."""
        if not self.check_bg_mismatch():
            callback()
            return True

        msgBox = QMessageBox(self)
        msgBox.setIcon(QMessageBox.Icon.Warning)
        msgBox.setWindowTitle("Background Mismatch")
        msgBox.setText("Current measurement settings do not match the loaded background.\nPlease close the shutter and acquire a new background, or ignore to continue.")

        btn_ignore = msgBox.addButton("Ignore and continue", QMessageBox.ButtonRole.ActionRole)
        msgBox.addButton("Quit", QMessageBox.ButtonRole.RejectRole)

        msgBox.exec()

        if msgBox.clickedButton() == btn_ignore:
            callback()
            return True
        return False

    def check_bg_and_take_single(self):
        if not self._try_acquire_gate():
            QMessageBox.warning(self, "Busy", "Another acquisition is already in progress.")
            return
        if not self.handle_bg_mismatch_and_run(self.take_single_spectrum):
            self._release_acquisition_gate()

    def check_bg_and_start_meas(self):
        if not self._try_acquire_gate():
            QMessageBox.warning(self, "Busy", "Another acquisition is already in progress.")
            return
        if not self.handle_bg_mismatch_and_run(self.start_measurement):
            self._release_acquisition_gate()

    def check_bg_and_start_seq(self):
        if not self._try_acquire_gate():
            QMessageBox.warning(self, "Busy", "Another acquisition is already in progress.")
            return
        if not self.handle_bg_mismatch_and_run(self.start_sequential):
            self._release_acquisition_gate()

    def _sync_display_mode_to_unit(self, unit, excitation_wavelength_nm):
        """Force the Wavelength/Raman display toggle (and excitation wavelength,
        for Raman shift) to agree with a calibration about to become active --
        the one choke point that guarantees calib_unit can never disagree with
        what on_spec_mode_changed()/on_exc_wl_changed()/public_axis_unit() read
        off the display toggle, regardless of which caller is applying the
        calibration (a loaded configuration, the Calibration window's own
        independent unit radio button, or the deprecated inline API -- none of
        which otherwise touch this toggle themselves). Signals are blocked so
        this never re-triggers those handlers' own invalidation logic against
        the calibration being applied right now; _sync_controls_to_display_mode()
        (SpectrometerControlMixin) then applies the same displayed-centre-value/
        spin_exc_wl-enabled/Pressure-Window-sensor side effects on_spec_mode_changed()
        itself would, so the two paths can never drift apart.
        """
        self.radio_spec_mode_raman.blockSignals(True)
        self.radio_spec_mode_wl.blockSignals(True)
        if unit == "Raman shift":
            if excitation_wavelength_nm is not None:
                self.spin_exc_wl.setValue(float(excitation_wavelength_nm))
            self.radio_spec_mode_raman.setChecked(True)
        else:
            self.radio_spec_mode_wl.setChecked(True)
        self.radio_spec_mode_raman.blockSignals(False)
        self.radio_spec_mode_wl.blockSignals(False)
        self._sync_controls_to_display_mode()

    def apply_calibration(
        self, coeffs, label, calib_unit='Wavelength', calib_laser_wl=None,
        axis_source="loaded_configuration", configuration_id=None, slot_id=None,
    ):
        # A loaded configuration already rejected a laser mismatch before ever
        # reaching this point (_prepare_configuration_for_loading()), and the
        # Calibration window's "Save and apply" reads calib_laser_wl directly
        # from this same spinbox -- so both agree trivially. Only the
        # deprecated inline API can reach apply_calibration() with a
        # calib_laser_wl that disagrees with the operator's current excitation
        # wavelength; without this check it would silently overwrite
        # spin_exc_wl instead of erroring, exactly the "unit mismatch" this
        # feature otherwise always rejects.
        if calib_unit == "Raman shift" and calib_laser_wl is not None:
            if excitation_wavelength_key(self.spin_exc_wl.value()) != excitation_wavelength_key(
                calib_laser_wl
            ):
                raise ConfigurationCompatibilityError([
                    "Excitation wavelength does not match: this calibration was "
                    f"taken at {calib_laser_wl:.3f} nm, but the excitation "
                    f"wavelength is currently set to {self.spin_exc_wl.value():.3f} nm. "
                    "Set the excitation wavelength to the calibrated value first."
                ])
        self._sync_display_mode_to_unit(calib_unit, calib_laser_wl)
        self.calib_coeffs = coeffs
        self.calib_unit = calib_unit
        self.calib_laser_wl = calib_laser_wl
        self.configuration_label = label
        self.active_configuration_id = configuration_id
        self.active_configuration_slot_id = slot_id
        self.positioned_configuration_id = configuration_id
        self.positioned_configuration_slot_id = slot_id
        self.axis_source = axis_source
        self._bump_instrument_state()
        self.lbl_loaded_configuration.setText(f"Loaded: {label}")
        self.update_plot_labels()
        self.sync_fit_range_to_spectrum(force=True)

        if getattr(self, 'raw_1d_data', None) is not None and hasattr(self.thread, 'is_measuring') and not self.thread.is_measuring:
            self.update_display(is_new_data=False)

    def _refresh_after_axis_change(self):
        """Shared tail for every axis-invalidating state change: resync the fit
        range, repaint the plot under the (now pixel) axis if there's live
        data to repaint, and drop any peak/pressure the Pressure Window is
        still showing under the calibration that just stopped being active.
        Without this, a caller that only updates calib_coeffs/labels can leave
        stale calibrated x-values and a stale pressure figure on screen even
        though the underlying state has already fallen back to pixel.
        """
        self.sync_fit_range_to_spectrum(force=True)
        if (
            getattr(self, 'raw_1d_data', None) is not None
            and hasattr(self.thread, 'is_measuring')
            and not self.thread.is_measuring
        ):
            self.update_display(is_new_data=False)
        if getattr(self, 'pressure_window', None) is not None:
            self.pressure_window.set_fit_peaks([])

    def clear_active_configuration(self):
        self.calib_coeffs = None
        self.axis_source = "pixel"
        self.calib_unit = "Wavelength"
        self.calib_laser_wl = None
        self.configuration_label = "None"
        self.active_configuration_id = None
        self.active_configuration_slot_id = None
        self.positioned_configuration_id = None
        self.positioned_configuration_slot_id = None
        self._bump_instrument_state()
        self.lbl_loaded_configuration.setText("Loaded: None")
        self.update_plot_labels()
        self._refresh_after_axis_change()

    def deactivate_axis_calibration(self, reason):
        """Invalidate the active calibration without disturbing physical-position
        bookkeeping -- unlike clear_active_configuration(), positioned_configuration_id/
        positioned_configuration_slot_id are left alone, since the grating/centre/ROI
        haven't actually moved. Used when the Wavelength/Raman display toggle or the
        excitation wavelength changes out from under an active calibration, so the
        calibration can never keep reporting shifts/wavelengths computed for a unit or
        laser that no longer matches what's on screen.
        """
        self.calib_coeffs = None
        self.axis_source = "pixel"
        self.calib_unit = "Wavelength"
        self.calib_laser_wl = None
        self.active_configuration_id = None
        self.active_configuration_slot_id = None
        self._bump_instrument_state()
        self.configuration_label = f"{self.configuration_label} (calibration invalidated: {reason})"
        self.lbl_loaded_configuration.setText(f"Loaded: {self.configuration_label}")
        self.update_plot_labels()
        self._refresh_after_axis_change()

    def configuration_hardware_context(self):
        """Return cached hardware facts used by both GUI and future API queries."""
        spec_metadata = {}
        getter = getattr(self.spec_ctrl, "get_cached_hardware_metadata", None)
        if getter is not None:
            spec_metadata = getter() or {}
        configured = self.config.get("hardware_identity", {})
        camera_identity = getattr(self, "_camera_identity", {})
        configured_camera = configured.get("camera", {})
        configured_spectrometer = configured.get("spectrometer", {})
        return {
            "spectrometer_model": (
                spec_metadata.get("model")
                or configured_spectrometer.get("model")
                or self.config.get("model")
            ),
            "spectrometer_serial_number": (
                spec_metadata.get("serial_number")
                or configured_spectrometer.get("serial_number")
            ),
            "camera_model": (
                camera_identity.get("model") or configured_camera.get("model")
            ),
            "camera_serial_number": (
                camera_identity.get("serial_number")
                or configured_camera.get("serial_number")
            ),
            "gratings": [
                {
                    "index": int(item.get("index", index + 1)),
                    "grooves": int(item.get("grooves", 0)),
                }
                for index, item in enumerate(self.config.get("grating", []))
            ],
            "detector_width": getattr(self.thread, "det_width", None),
            "detector_height": getattr(self.thread, "det_height", None),
            "current_grating": spec_metadata.get("grating"),
            "actual_center_wavelength_nm": spec_metadata.get("center_wavelength_nm"),
        }

    def _is_oceanoptics_backend(self):
        """Return True only for the integrated fixed Ocean Optics backend.

        This deliberately uses the configured backend identity instead of inferring from
        generic capabilities.  Other fixed or partially movable instruments must continue
        through their existing grating/centre movement and validation paths unchanged.
        """
        return self.config.get("model") == "OceanOptics"

    def _current_grating_definition(self, hardware_context=None):
        cached_grating = (hardware_context or {}).get("current_grating") or {}
        if (
            cached_grating.get("index") is not None
            and cached_grating.get("grooves_per_mm") is not None
        ):
            return {
                "index": int(cached_grating["index"]),
                "grooves_per_mm": int(cached_grating["grooves_per_mm"]),
            }
        combo_index = self.combo_grating.currentIndex()
        gratings = self.config.get("grating", [])
        if not 0 <= combo_index < len(gratings):
            raise ConfigurationError("The selected grating is not defined in the hardware configuration.")
        item = gratings[combo_index]
        return {
            "index": int(item.get("index", combo_index + 1)),
            "grooves_per_mm": int(item.get("grooves", 0)),
        }

    def _current_roi_definition(self):
        if self.radio_2d.isChecked():
            mode = "2d"
            start, end = 0, int(self.thread.det_height)
        elif self.radio_1d_full.isChecked():
            mode = "1d_full"
            start, end = 0, int(self.thread.det_height)
        else:
            mode = "1d_roi"
            start, end = self.spin_vstart.value(), self.spin_vend.value()
        return {"roi_mode": mode, "roi_start": int(start), "roi_end": int(end)}

    def register_current_configuration(
        self, coeffs, calibration_unit="Wavelength", excitation_wavelength_nm=None,
        reference_kind=None, standards=None, raw_spectrum=None,
    ):
        """Create a new active record for the current grating/centre/ROI slot."""
        hardware = self.configuration_hardware_context()
        grating = self._current_grating_definition(hardware)
        roi = self._current_roi_definition()
        c0, c1, c2 = coeffs
        draft = {
            "compatibility": {
                "spectrometer_model": hardware["spectrometer_model"],
                "spectrometer_serial_number": hardware["spectrometer_serial_number"],
                "camera_model": hardware["camera_model"],
                "camera_serial_number": hardware["camera_serial_number"],
            },
            "spectrometer": {
                "grating_index": grating["index"],
                "grating_grooves_per_mm": grating["grooves_per_mm"],
                # Slot identity follows the commanded/nominal position. Manual
                # calibration occurs after movement, so physical_center_wl is the
                # stable target rather than a fresh noisy readback.
                "target_center_wavelength_nm": float(self.physical_center_wl),
                "actual_center_wavelength_nm": float(
                    hardware["actual_center_wavelength_nm"]
                    if hardware["actual_center_wavelength_nm"] is not None
                    else self.physical_center_wl
                ),
            },
            "detector": {
                **roi,
                "detector_width": hardware["detector_width"],
                "detector_height": hardware["detector_height"],
            },
            "calibration": {
                "source": "emission_standard_polynomial",
                "unit": calibration_unit,
                "excitation_wavelength_nm": (
                    float(excitation_wavelength_nm)
                    if calibration_unit == "Raman shift"
                    else None
                ),
                # Which standard(s) the operator actually assigned peaks
                # against, and whether a Raman-shift calibration came from
                # emission-lamp lines (converted via the excitation
                # wavelength) or a Raman-shift-native standard measured
                # directly -- see CalibrationWindow._reference_kind_for.
                "reference_kind": reference_kind or (
                    "emission_lines_with_excitation"
                    if calibration_unit == "Raman shift" else "emission_lines"
                ),
                "standards": standards or [],
                "coefficients": {
                    "c0": float(c0), "c1": float(c1), "c2": float(c2),
                },
                # The measured spectrum the operator fit peaks against, in the
                # same raw (unflipped) sensor pixel domain as the coefficients
                # above -- kept as evidence of how this calibration was derived.
                "raw_spectrum": (
                    [float(v) for v in raw_spectrum] if raw_spectrum is not None else None
                ),
            },
        }
        return self.configuration_catalog.register_configuration(draft)

    def _release_gui_config_gate(self):
        """Release only the gate ownership taken by an operator-started configuration
        load.

        An API-driven configuration apply runs through the same GUI callbacks
        (_finalize_pending_configuration / _fail_pending_configuration), but its gate
        must be released only after the API worker thread has assembled the response,
        so it must not be released here. A flag distinguishes the two owners so this
        mix-up can never happen structurally (work_API_standby.md 表#8).
        """
        if getattr(self, "_gui_config_gate_held", False):
            self._gui_config_gate_held = False
            self._release_acquisition_gate()

    def on_load_configuration(self):
        # Keep the dialog itself outside the gate: otherwise every API request would
        # get 409 for as long as the operator is still deciding
        # (work_API_standby.md Step 0 手順3).
        dialog = ConfigurationBrowserDialog(
            self.configuration_catalog,
            self.configuration_hardware_context(),
            active_configuration_id=self.active_configuration_id,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        # This is an async scope involving a physical move, so take the gate after
        # confirmation and before the move starts.
        if not self._try_acquire_gate():
            QMessageBox.warning(
                self, "Busy",
                "Another operation is in progress. Please try again in a moment."
            )
            return
        self._gui_config_gate_held = True
        try:
            record = self.configuration_catalog.get_configuration(
                dialog.selected_configuration_id
            )
            self.configuration_catalog.assert_compatible(
                record, self.configuration_hardware_context()
            )
            self._prepare_configuration_for_loading(
                record,
                # Ocean Optics' model+serial compatibility was already checked above.
                # It has no movable centre, so a saved display/slot centre must not be
                # sent through SpectrometerMoveThread.  Every other backend retains the
                # previous unconditional GUI move/apply behaviour.
                skip_move=self._is_oceanoptics_backend(),
            )
        except Exception as e:
            self._loading_config = False
            self._pending_calib_coeffs = None
            self._pending_axis_source = None
            QMessageBox.warning(self, "Error", f"Failed to load configuration:\n{e}")
            self._clear_pending_configuration()
            self._release_gui_config_gate()

    def _apply_pixel_configuration(self, label, configuration_id, slot_id):
        """Keep the configuration's physical state but expose an uncalibrated axis."""
        self.calib_coeffs = None
        self.calib_unit = "Wavelength"
        self.calib_laser_wl = None
        self.configuration_label = label
        self.active_configuration_id = None
        self.active_configuration_slot_id = None
        self.positioned_configuration_id = configuration_id
        self.positioned_configuration_slot_id = slot_id
        self.axis_source = "pixel"
        self._bump_instrument_state()
        self.lbl_loaded_configuration.setText(f"Loaded: {label} (pixel axis)")
        self.update_plot_labels()
        self.sync_fit_range_to_spectrum(force=True)
        if (
            getattr(self, "raw_1d_data", None) is not None
            and hasattr(self.thread, "is_measuring")
            and not self.thread.is_measuring
        ):
            self.update_display(is_new_data=False)

    def _configuration_matches_current_state(self, record):
        """Return whether a configuration move would be physically redundant."""
        spectrometer = record["spectrometer"]
        detector = record["detector"]
        try:
            current_grating = self._current_grating_definition(
                self.configuration_hardware_context()
            )
            current_roi = self._current_roi_definition()
            return (
                int(current_grating["index"])
                == int(spectrometer["grating_index"])
                and int(current_grating["grooves_per_mm"])
                == int(spectrometer["grating_grooves_per_mm"])
                and abs(
                    float(self.physical_center_wl)
                    - float(spectrometer["target_center_wavelength_nm"])
                )
                < 5e-4
                and current_roi["roi_mode"] == detector["roi_mode"]
                and int(current_roi["roi_start"]) == int(detector["roi_start"])
                and int(current_roi["roi_end"]) == int(detector["roi_end"])
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _prepare_configuration_for_loading(
        self,
        record,
        *,
        axis_mode="calibrated",
        completion_future=None,
        skip_move=False,
    ):
        detector = record["detector"]
        spectrometer = record["spectrometer"]
        calibration = record["calibration"]

        # Checked before any widget is mutated, so a rejected load leaves the GUI
        # untouched. axis_mode="pixel" (API-only; positions hardware without
        # applying calibration) has no laser-mismatch concern at all, since it
        # never applies or reads the calibration's excitation wavelength below.
        if axis_mode == "calibrated" and calibration["unit"] == "Raman shift":
            saved_excitation_nm = calibration["excitation_wavelength_nm"]
            if excitation_wavelength_key(self.spin_exc_wl.value()) != excitation_wavelength_key(
                saved_excitation_nm
            ):
                raise ConfigurationCompatibilityError([
                    "Excitation wavelength does not match: this configuration was "
                    f"calibrated at {saved_excitation_nm:.3f} nm, but the excitation "
                    f"wavelength is currently set to {self.spin_exc_wl.value():.3f} nm. "
                    "Set the excitation wavelength to the calibrated value first, or "
                    "load a configuration matching the current laser."
                ])

        roi_mode = detector["roi_mode"]
        if roi_mode == "2d":
            self.radio_2d.setChecked(True)
        elif roi_mode == "1d_full":
            self.radio_1d_full.setChecked(True)
        else:
            self.radio_1d_roi.setChecked(True)

        self.spin_vstart.blockSignals(True)
        self.spin_vend.blockSignals(True)
        self.spin_vstart.setValue(detector["roi_start"])
        self.spin_vend.setValue(detector["roi_end"])
        self.spin_vstart.blockSignals(False)
        self.spin_vend.blockSignals(False)
        self.apply_roi_settings()

        # axis_mode="pixel" deliberately leaves the Wavelength/Raman display toggle
        # and the excitation wavelength exactly as the operator currently has them --
        # positioning hardware only must not have side effects on calibration/display
        # state (matches _apply_pixel_configuration()'s "never touch calibration"
        # behavior, which finalizes this same load a few lines below).
        if axis_mode == "calibrated":
            self._sync_display_mode_to_unit(
                calibration["unit"], calibration.get("excitation_wavelength_nm")
            )

        cb_idx = next(
            (
                index for index, item in enumerate(self.config.get("grating", []))
                if int(item.get("index", index + 1)) == int(spectrometer["grating_index"])
            ),
            -1,
        )
        if cb_idx < 0:
            raise ConfigurationError("The configuration's grating slot is not available.")
        self.combo_grating.setCurrentIndex(cb_idx)

        # Ocean Optics has no movable centre.  Use its connected native centre for the
        # hidden display widget; the saved centre is metadata, not a position to apply.
        # Movable backends retain the original saved-target path.
        if self._is_oceanoptics_backend():
            center_nm = float(self.physical_center_wl)
        else:
            center_nm = float(spectrometer["target_center_wavelength_nm"])
        # axis_mode="pixel" never changed the display toggle/spin_exc_wl above, so this
        # must follow whatever is currently displayed, not the loaded record's own unit.
        if self.radio_spec_mode_raman.isChecked():
            ex_wl = self.spin_exc_wl.value()
            if ex_wl > 0 and center_nm > 0:
                center_for_spinbox = 1e7 / ex_wl - 1e7 / center_nm
            else:
                center_for_spinbox = 0.0
        else:
            center_for_spinbox = center_nm
        self.spin_centre_wl.setValue(center_for_spinbox)

        self._loading_config = True
        coefficients = calibration["coefficients"]
        self._pending_calib_coeffs = (
            coefficients["c0"], coefficients["c1"], coefficients["c2"]
        )
        self._pending_configuration_label = format_configuration_label(record)
        self._pending_calib_unit = calibration["unit"]
        self._pending_calib_laser_wl = calibration.get("excitation_wavelength_nm")
        self._pending_axis_source = "loaded_configuration"
        self._pending_configuration_id = record["configuration_id"]
        self._pending_configuration_slot_id = record["slot_id"]
        self._pending_configuration_axis_mode = axis_mode
        self._pending_configuration_future = completion_future

        if skip_move:
            self._finalize_pending_configuration()
        else:
            self.on_apply_spectrometer()

    def _finalize_pending_configuration(self):
        """Apply the staged axis state after a move, or immediately for a no-op move."""
        configuration_id = self._pending_configuration_id
        slot_id = self._pending_configuration_slot_id
        completion_future = self._pending_configuration_future
        try:
            try:
                if self._pending_configuration_axis_mode == "pixel":
                    self._apply_pixel_configuration(
                        self._pending_configuration_label, configuration_id, slot_id
                    )
                else:
                    self.apply_calibration(
                        self._pending_calib_coeffs,
                        self._pending_configuration_label,
                        calib_unit=self._pending_calib_unit,
                        calib_laser_wl=self._pending_calib_laser_wl,
                        axis_source=self._pending_axis_source,
                        configuration_id=configuration_id,
                        slot_id=slot_id,
                    )
                try:
                    self.configuration_catalog.mark_used(configuration_id)
                except Exception as exc:
                    print(f"Failed to update configuration usage metadata: {exc}")
            except Exception as exc:
                self._clear_pending_configuration()
                self._loading_config = False
                if completion_future is not None and not completion_future.done():
                    completion_future.set_exception(exc)
                    return
                raise

            self._clear_pending_configuration()
            self._loading_config = False
            if completion_future is not None and not completion_future.done():
                completion_future.set_result(True)
        finally:
            # The GUI-load gate ownership ends here regardless of success, failure, or
            # exception.
            self._release_gui_config_gate()

    def _fail_pending_configuration(self, message):
        completion_future = getattr(self, "_pending_configuration_future", None)
        self._clear_pending_configuration()
        self._loading_config = False
        self._release_gui_config_gate()
        if completion_future is not None and not completion_future.done():
            completion_future.set_exception(RuntimeError(message))
            return True
        return False

    def _clear_pending_configuration(self):
        self._pending_calib_coeffs = None
        self._pending_configuration_label = None
        self._pending_calib_unit = None
        self._pending_calib_laser_wl = None
        self._pending_axis_source = None
        self._pending_configuration_id = None
        self._pending_configuration_slot_id = None
        self._pending_configuration_axis_mode = "calibrated"
        self._pending_configuration_future = None

    def on_acq_bg_clicked(self):
        if not self._try_acquire_gate():
            QMessageBox.warning(self, "Busy", "Another acquisition is already in progress.")
            return
        self._is_acquiring_bg = True
        self.is_single_shot = True
        self._ignore_next_frames = False
        self.current_accum_count = 0
        self.btn_single.setEnabled(False)
        self.btn_commence.setEnabled(False)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_terminate.setEnabled(True)
        self._set_button_style(self.btn_terminate, self.BUTTON_STYLE_RED)
        self.thread.start_measuring()

    def _process_acquired_bg(self):
        if getattr(self, 'raw_1d_data', None) is None:
            QMessageBox.warning(self, "Error", "No 1D data available for background.")
            return

        acq_time = self.spin_acq_time.value()
        accum = self.spin_accumulate.value()
        mode_str = "1D Spectrum (Custom ROI)" if self.radio_1d_roi.isChecked() else "1D Spectrum (Full Range Binning)"
        default_filename = _background_default_filename(
            acq_time,
            accum,
            self.spin_vstart.value(),
            self.spin_vend.value(),
            self.radio_1d_roi.isChecked(),
            datetime.now(),
        )
        initial_path = os.path.join(self._last_save_dir, default_filename) if self._last_save_dir else default_filename

        file_path, _ = QFileDialog.getSaveFileName(self, "Save Background Data", initial_path, "Text/JSON Files (*.txt *.json)")
        if not file_path:
            return
        self._last_save_dir = os.path.dirname(file_path)
        self._save_local_cache("last_save_dir", self._last_save_dir)

        try:
            bg_arr, bg_meta = self.file_io.save_background(
                file_path, self.raw_1d_data,
                acq_time, accum, mode_str,
                self.spin_vstart.value(), self.spin_vend.value(),
                self.label_current_temp.text(),
                hardware_metadata=build_hardware_metadata(
                    self,
                    self._hardware_capture_by_mode.get("1d", self._latest_hardware_capture),
                    include_background=False,
                ),
            )
            QMessageBox.information(self, "Success", "Background saved successfully.")
            self.loaded_bg_data = bg_arr
            self.loaded_bg_metadata = bg_meta
            self._bump_instrument_state()
            self.lbl_loaded_bg.setText(f"Loaded: {os.path.basename(file_path)}")
            self.radio_bg_on.setChecked(True)
            self.on_fit_settings_changed()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save background:\n{e}")

    def on_load_bg_clicked(self):
        # Keep the file dialog outside the gate, so every API request does not get
        # 409 for as long as the dialog is left open (work_API_standby.md Step 0 手順2).
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Background Data", self._last_save_dir, "Text/JSON Files (*.txt *.json)")
        if file_path:
            self._last_save_dir = os.path.dirname(file_path)
            self._save_local_cache("last_save_dir", self._last_save_dir)
            try:
                with self.acquisition_gate():
                    bg_arr, bg_meta = self.file_io.load_background(file_path)
                    self.loaded_bg_data = bg_arr
                    self.loaded_bg_metadata = bg_meta
                    self._bump_instrument_state()
                    self.lbl_loaded_bg.setText(f"Loaded: {os.path.basename(file_path)}")
                    self.radio_bg_on.setChecked(True)
                    self.on_fit_settings_changed()
            except GateBusyError:
                QMessageBox.warning(
                    self, "Busy",
                    "Another operation is in progress. Please try again in a moment."
                )
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load background:\n{e}")

    def on_save_data_clicked(self):
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        default_filename = f"data_{date_str}.txt"
        initial_path = os.path.join(self._last_save_dir, default_filename) if self._last_save_dir else default_filename
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Data", initial_path, "Text Files (*.txt);;All Files (*)")
        if file_path:
            self._last_save_dir = os.path.dirname(file_path)
            self._save_local_cache("last_save_dir", self._last_save_dir)
            self._save_data_to_path(file_path, show_msg=True)

    def _save_data_to_path(self, file_path, show_msg=True):
        is_1d = self.stacked_widget.currentIndex() == 0
        if is_1d and self.latest_1d_data is None:
            if show_msg:
                QMessageBox.warning(self, "Error", "No 1D data available to save.")
            return False
        if not is_1d and self.latest_2d_data is None:
            if show_msg:
                QMessageBox.warning(self, "Error", "No 2D data available to save.")
            return False

        try:
            capture_mode = "1d" if is_1d else "2d"
            hardware_capture = self._hardware_capture_by_mode.get(
                capture_mode, self._latest_hardware_capture
            )
            metadata = {
                "grating":      self.combo_grating.currentText(),
                "center_wl":    self.spin_centre_wl.value(),
                "acq_time":     self.spin_acq_time.value(),
                "accum":        self.spin_accumulate.value(),
                "calib_coeffs": self.calib_coeffs,
                "roi_start":    self.spin_vstart.value(),
                "roi_end":      self.spin_vend.value(),
                "mode":         "2D" if self.radio_2d.isChecked() else "1D (Full)" if self.radio_1d_full.isChecked() else "1D (ROI)",
                "spec_mode":    "Raman shift" if self.radio_spec_mode_raman.isChecked() else "Wavelength",
                "exc_wl":       self.spin_exc_wl.value(),
                "hardware_metadata": build_hardware_metadata(
                    self, hardware_capture
                ),
            }

            if is_1d:
                x_data = self.get_x_axis(len(self.latest_1d_data))
                if self.chk_flip_x.isChecked() and public_axis_kind(self) != "pixel":
                    # Must match DisplayMixin.update_display()'s flip condition, or the
                    # saved x column would desync from native wavelength/calibrated y data.
                    x_data = x_data[::-1]
                raw_data = None
                bg_data = None
                if self.radio_bg_on.isChecked() and self.loaded_bg_data is not None:
                    if len(self.raw_1d_data) == len(self.loaded_bg_data):
                        raw_data = self.raw_1d_data.astype(np.float64)
                        bg_data = self.loaded_bg_data.astype(np.float64)
                        if self.chk_flip_x.isChecked():
                            raw_data = raw_data[::-1]
                            bg_data = bg_data[::-1]
                self.file_io.save_spectrum_1d(file_path, x_data, self.latest_1d_data, metadata,
                                              raw_data=raw_data, bg_data=bg_data)

                if self.chk_save_fitting.isChecked() and self.radio_fit_on.isChecked() and self.latest_fit_res is not None:
                    fit_file_path = file_path.rsplit('.', 1)[0] + "_fitting_results.txt"
                    pressure_info = None
                    pw = self.pressure_window
                    if pw is not None and pw.isVisible() and pw.current_pressure is not None:
                        lam0_value = pw.current_zero_peak_at_current_t
                        if lam0_value is None:
                            lam0_value = pw.spin_lam0_t0.value() if pw.radio_on.isChecked() else pw.spin_lam0.value()
                        pressure_info = {
                            "pressure":     pw.current_pressure,
                            "pressure_err": pw.current_pressure_err,
                            "scale":        pw.combo_p_scale.currentText(),
                            "sensor":       pw.combo_sensor.currentText(),
                            "lam0":         lam0_value,
                            "lam0_unit":    pw.unit,
                        }
                    self.file_io.save_fitting_results(fit_file_path, self.latest_fit_res,
                                                      getattr(self, 'latest_fit_func', 'Unknown'),
                                                      pressure_info=pressure_info)
            else:
                self.file_io.save_spectrum_2d(file_path, self.latest_2d_data, metadata)

            if show_msg:
                QMessageBox.information(self, "Success", f"Data saved successfully to:\n{file_path}")
            return True
        except Exception as e:
            if show_msg:
                QMessageBox.critical(self, "Error", f"Failed to save data:\n{e}")
            else:
                print(f"Sequential save error: {e}")
            return False
