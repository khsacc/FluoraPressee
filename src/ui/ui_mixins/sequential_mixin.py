import os
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QLabel, QPushButton,
                             QFileDialog, QMessageBox)


class SequentialMixin:
    def _lock_ui(self, reason):
        """Add `reason` to the set of active UI locks and disable the
        measurement/config controls (see set_ui_enabled_during_seq). Multiple
        independent lockers (sequential run, API server, an in-flight API
        request) can be active at once; the UI only re-enables once all of them
        have released.

        Idempotent: with Standby mode this is called once per API request (several
        times a second during a burst), and re-walking ~35 widgets each time is both
        wasteful and a source of focus stealing. Idempotency is only safe because
        every path that re-enables widgets while a lock is held has been closed
        (work_API_standby.md 方針4) - a widget wrongly re-enabled would otherwise
        never be repaired, since later locks would skip the walk.
        """
        was_locked = self._ui_is_locked()
        self._ui_lock_reasons.add(reason)
        if not was_locked:
            self._capture_lock_focus()
            self.set_ui_enabled_during_seq(False)
        self._update_remote_active_indicator()

    def _unlock_ui(self, reason, reapply_hardware=True):
        """Drop one lock reason; re-enable the controls once none are left.

        Returns without touching anything when `reason` was not actually held, so a
        stray release can never be mistaken for "the last lock was just removed".
        `reapply_hardware=False` skips pushing the ROI back to the camera thread on
        re-enable (see set_ui_enabled_during_seq) - used by the per-request API lock,
        which must not re-send hardware settings several times a second.
        """
        if reason not in self._ui_lock_reasons:
            return
        self._ui_lock_reasons.discard(reason)
        if len(self._ui_lock_reasons) == 0:
            self.set_ui_enabled_during_seq(True, reapply_hardware=reapply_hardware)
        self._update_remote_active_indicator()

    def _reassert_ui_lock(self):
        """Re-apply the disabled state if any lock reason is still standing.

        Call this right after any unavoidable bulk re-enable (a modal dialog closing
        and restoring centralWidget, for instance) so that re-enable cannot silently
        outlive the lock.
        """
        if self._ui_is_locked():
            self.set_ui_enabled_during_seq(False)

    def _capture_lock_focus(self):
        """Remember the focused widget so unlocking can hand focus back.

        Disabling a focused widget makes Qt move focus elsewhere, so without this a
        remote request would silently steal the caret out of whatever spin box the
        operator was typing in. Debouncing lowers how often that happens; only
        restoring focus actually undoes it (方針5).
        """
        self._api_lock_focus_widget = QApplication.focusWidget()

    def _restore_api_lock_focus(self):
        widget = getattr(self, "_api_lock_focus_widget", None)
        self._api_lock_focus_widget = None
        if widget is None:
            return
        try:
            if widget.isVisible() and widget.isEnabled():
                widget.setFocus()
        except RuntimeError:
            # PyQt6 raises this when the underlying C++ widget has been destroyed
            # since the lock was applied.
            pass

    def show_skip_frames_info(self, link):
        dialog = QDialog(self)
        dialog.setWindowTitle("How Skip frames works")
        dialog.setModal(True)
        layout = QVBoxLayout()
        info_text = (
            "If you set 'Skip frames' to N, the system will save 1 frame and then ignore the next N frames.<br><br>"
            "For example, if you set it to 9 with an exposure time of 0.1 s, the system will save 1 frame every 1 second<br>"
            "(1 saved + 9 skipped = 10 frames = 1.0 s)."
        )
        lbl = QLabel(info_text)
        layout.addWidget(lbl)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)

        dialog.setLayout(layout)
        dialog.exec()

    def update_seq_progress(self):
        if not self.is_sequential_running:
            return
        self.lbl_seq_progress.setText(f"Progress: Acquired {self.seq_count} / {self.spin_max_num.value()}")

    def set_ui_enabled_during_seq(self, enabled, reapply_hardware=True):
        self.action_hardware_config.setEnabled(enabled)

        self.btn_single.setEnabled(enabled)
        self.btn_commence.setEnabled(enabled)
        self.btn_terminate.setEnabled(enabled)
        self.btn_save_data.setEnabled(enabled)

        self.spin_acq_time.setEnabled(enabled)
        self.spin_em_gain.setEnabled(
            enabled and self.spin_em_gain.isVisible() and self._em_gain_available
        )
        self.spin_accumulate.setEnabled(enabled)
        self.chk_cosmic_ray_removal.setEnabled(enabled)
        self.spin_spike_threshold.setEnabled(enabled and self.chk_cosmic_ray_removal.isChecked())
        self.spin_cooler_temp.setEnabled(enabled)
        self.btn_read_temp.setEnabled(enabled)

        self.btn_choose_dir.setEnabled(enabled)
        self.spin_skip_frames.setEnabled(enabled)
        self.spin_max_num.setEnabled(enabled)
        # btn_stop_seq is deliberately NOT part of this set: stopping a running
        # sequential measurement has to stay possible. btn_start_seq is, though -
        # without it an operator could kick off a sequential run while the API server
        # holds the UI lock (it is only ever disabled by start_sequential() itself).
        self.btn_start_seq.setEnabled(enabled and bool(self.seq_dir))

        self.radio_bg_on.setEnabled(enabled)
        self.radio_bg_off.setEnabled(enabled)
        self.btn_acq_bg.setEnabled(enabled)
        self.btn_load_bg.setEnabled(enabled)

        self.radio_2d.setEnabled(enabled)
        self.radio_1d_full.setEnabled(enabled)
        self.radio_1d_roi.setEnabled(enabled)
        self.spin_vstart.setEnabled(enabled)
        self.spin_vend.setEnabled(enabled)
        self.chk_flip_x.setEnabled(enabled)

        self.combo_grating.setEnabled(enabled)
        self.radio_spec_mode_wl.setEnabled(enabled)
        self.radio_spec_mode_raman.setEnabled(enabled)
        self.spin_centre_wl.setEnabled(enabled)
        if self.radio_spec_mode_raman.isChecked():
            self.spin_exc_wl.setEnabled(enabled)
        if enabled:
            # Recomputed rather than blanket-enabled: Apply is only meaningful when the
            # widgets actually differ from the physical position, and with Standby this
            # runs after every remote request rather than once per sequential run.
            self.check_spectrometer_changes()
        else:
            self.btn_apply_spec.setEnabled(False)
        self.btn_calib_neon.setEnabled(enabled)
        self.btn_load_configuration.setEnabled(enabled)

        self.radio_fit_on.setEnabled(enabled)
        self.radio_fit_off.setEnabled(enabled)
        self.combo_fit_func.setEnabled(enabled)
        self.combo_fit_peak_count.setEnabled(enabled)
        self.combo_peak_sort.setEnabled(enabled)
        self.combo_baseline_model.setEnabled(enabled)
        self.spin_fit_start.setEnabled(enabled)
        self.spin_fit_end.setEnabled(enabled)



        if enabled:
            self.toggle_fitting_panel()
            if reapply_hardware:
                self.apply_roi_settings()
            else:
                self._sync_roi_widget_states()

    def on_choose_seq_dir(self):
        start_dir = self.seq_dir if self.seq_dir and os.path.isdir(self.seq_dir) else ""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Directory for Sequential Data", start_dir)
        if dir_path:
            self.seq_dir = dir_path
            self._save_local_cache("last_seq_dir", dir_path)
            display_path = dir_path if len(dir_path) < 25 else "..." + dir_path[-22:]
            self.lbl_seq_dir.setText(f"Dir: {display_path}")
            if not self.is_sequential_running:
                self.btn_start_seq.setEnabled(True)
                self._set_button_style(self.btn_start_seq, self.BUTTON_STYLE_BLUE)

    def start_sequential(self):
        if not self.seq_dir:
            QMessageBox.warning(self, "Error", "Please select a directory first.")
            return

        self.is_sequential_running = True
        self.seq_count = 0
        self.current_skip_count = self.spin_skip_frames.value()
        self._seq_fit_failed = False

        self.btn_start_seq.setEnabled(False)
        self._set_button_style(self.btn_start_seq, self.BUTTON_STYLE_BLUE)
        self.btn_stop_seq.setEnabled(True)
        self._set_button_style(self.btn_stop_seq, self.BUTTON_STYLE_RED)

        self.seq_start_time_dt = datetime.now()
        self.seq_log_data = []

        self.lbl_seq_progress.setVisible(True)
        self.lbl_seq_progress.setText(f"Progress: Acquired 0 / {self.spin_max_num.value()}")

        self._lock_ui("sequential")

        if self.radio_fit_on.isChecked():
            start_date_str = self.seq_start_time_dt.strftime("%Y%m%d_%H%M%S")
            self.seq_fitting_summary_path = os.path.join(self.seq_dir, f"fitting_seq_summary_{start_date_str}.txt")

            func = self.combo_fit_func.currentText()
            fit_start = self.spin_fit_start.value()
            fit_end = self.spin_fit_end.value()
            peak_count = self.combo_fit_peak_count.currentData()
            peak_sort = self.combo_peak_sort.currentText()
            baseline_model = self.combo_baseline_model.currentText()


            try:
                unit = "cm-1" if self.radio_spec_mode_raman.isChecked() else "nm"
                has_pressure = (self.pressure_window is not None and self.pressure_window.isVisible())
                self.file_io.create_fitting_seq_summary(
                    self.seq_fitting_summary_path, func, fit_start, fit_end,
                    peak_count, unit, has_pressure, peak_sort=peak_sort,
                    baseline_model=baseline_model
                )
            except Exception as e:
                print(f"Failed to create summary file: {e}")
                self.seq_fitting_summary_path = None
        else:
            self.seq_fitting_summary_path = None

        self._ignore_next_frames = False
        if not hasattr(self.thread, 'is_measuring') or not self.thread.is_measuring:
            self.start_measurement()

    def stop_sequential(self):
        if getattr(self, 'is_sequential_running', False):
            if hasattr(self, 'seq_start_time_dt') and self.seq_dir:
                seq_end_time_dt = datetime.now()
                summary_path = os.path.join(self.seq_dir, f"seq_summary_{self.seq_start_time_dt.strftime('%Y%m%d_%H%M%S')}.txt")
                try:
                    self.file_io.save_sequential_summary(
                        summary_path,
                        self.seq_start_time_dt, seq_end_time_dt,
                        self.spin_acq_time.value(), self.spin_accumulate.value(),
                        self.spin_skip_frames.value(), self.seq_log_data
                    )
                except Exception as e:
                    print(f"Failed to write sequential summary: {e}")

        self.is_sequential_running = False
        self.lbl_seq_progress.setVisible(False)
        self.seq_fitting_summary_path = None

        self.btn_start_seq.setEnabled(True)
        self._set_button_style(self.btn_start_seq, self.BUTTON_STYLE_BLUE)
        self.btn_stop_seq.setEnabled(False)
        self._set_button_style(self.btn_stop_seq, self.BUTTON_STYLE_RED)

        self._unlock_ui("sequential")

        if hasattr(self.thread, 'is_measuring') and self.thread.is_measuring:
            self.stop_measurement()

    def toggle_sequential(self, checked):
        self.seq_content.setVisible(checked)
        self.seq_toggle_btn.setText("▼ Sequential measurements" if checked else "▶ Sequential measurements")
