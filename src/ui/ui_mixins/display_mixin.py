import os
from datetime import datetime
import numpy as np
import pyqtgraph as pg

from src.core.measurement_metadata import public_axis_kind


class DisplayMixin:
    def _configure_spectrum_plot_range(self, min_x, max_x):
        view_box = self.plot_widget.getViewBox()
        view_box.setLimits(xMin=min_x, xMax=max_x)
        view_box.setDefaultPadding(0)
        # Keep complete curve bounds available to pyqtgraph's standard
        # "View All", as the calibration plot does. With clip-to-view enabled,
        # autoRange can only see the currently zoomed x-range.
        self.plot_widget.setClipToView(False)

    def toggle_fitting_panel(self):
        is_on = self.radio_fit_on.isChecked()
        self.fitting_panel.setVisible(is_on)
        self.chk_save_fitting.setEnabled(is_on)

        # Auto-switch default plot style when fitting mode changes
        self.radio_plot_scatter.blockSignals(True)
        self.radio_plot_line.blockSignals(True)
        if is_on:
            self.radio_plot_scatter.setChecked(True)
        else:
            self.radio_plot_line.setChecked(True)
        self.radio_plot_scatter.blockSignals(False)
        self.radio_plot_line.blockSignals(False)

        if not is_on:
            self.chk_save_fitting.setChecked(False)
            self.fit_curve.clear()
            self.fit_baseline_curve.clear()
            self.fit_curve_sub1.clear()
            self.fit_curve_sub2.clear()
            self.edge_marker.hide()
            self.current_w_peak1 = None
            self.latest_fit_res = None
            self.latest_fit_func = None
            if self.pressure_window is not None:
                self.pressure_window.set_fit_peaks([])
        else:
            self.sync_fit_range_to_spectrum()

            if getattr(self, 'raw_1d_data', None) is not None and hasattr(self.thread, 'is_measuring') and not self.thread.is_measuring:
                self.update_display(is_new_data=False)

    def sync_fit_range_to_spectrum(self, force=False):
        """Recompute Range Start/Range End from the currently displayed spectrum's x-axis.

        With force=False, only clamps when the current range falls outside the
        new bounds (used when fitting is switched on). With force=True, always
        resets to the full new bounds — used when the pixel-to-x-axis calibration
        changes, since the previous range values are meaningless under the new mapping.
        """
        if getattr(self, 'raw_1d_data', None) is None:
            return

        x_data = self.get_x_axis(len(self.raw_1d_data))
        min_x = np.min(x_data)
        max_x = np.max(x_data)

        curr_start = self.spin_fit_start.value()
        curr_end = self.spin_fit_end.value()

        if force or curr_start < min_x or curr_end > max_x:
            self.spin_fit_start.blockSignals(True)
            self.spin_fit_end.blockSignals(True)
            self.spin_fit_start.setValue(float(min_x))
            self.spin_fit_end.setValue(float(max_x))
            self.spin_fit_start.blockSignals(False)
            self.spin_fit_end.blockSignals(False)

    def on_fit_settings_changed(self):
        if getattr(self, 'raw_1d_data', None) is not None and hasattr(self.thread, 'is_measuring') and not self.thread.is_measuring:
            self.update_display(is_new_data=False)

    def on_fit_peak_count_changed(self):
        if self.pressure_window is not None:
            self.pressure_window.set_fit_peak_count(self.combo_fit_peak_count.currentData(), reset_selection=True)
        self.on_fit_settings_changed()

    def update_display(self, is_new_data=False, mode="1d"):
        if mode == "1d":
            if getattr(self, 'raw_1d_data', None) is None: return

            self.update_plot_labels()

            disp_data = self.raw_1d_data.astype(np.float64).copy()

            if self.radio_bg_on.isChecked() and self.loaded_bg_data is not None:
                if len(disp_data) == len(self.loaded_bg_data):
                    disp_data = disp_data - self.loaded_bg_data

            if self.chk_flip_x.isChecked():
                disp_data = disp_data[::-1]

            self.latest_1d_data = disp_data
            self.stacked_widget.setCurrentIndex(0)

            x_data = self.get_x_axis(len(disp_data))
            if self.chk_flip_x.isChecked() and public_axis_kind(self) != "pixel":
                # A meaningful physical x-axis (native wavelength or FluoRaPressée-calibrated)
                # must flip together with y, unlike a bare pixel index (see public_axis_kind()
                # docstring) - otherwise flip_x desyncs wavelength/Raman-shift from intensity.
                x_data = x_data[::-1]

            min_x = np.min(x_data)
            max_x = np.max(x_data)

            # Plot design
            self._configure_spectrum_plot_range(min_x, max_x)

            if self.radio_plot_scatter.isChecked():
                self.plot_scatter.setData(x_data, disp_data)
                self.plot_line.setData([], [])
            else:
                self.plot_line.setData(x_data, disp_data)
                self.plot_scatter.setData([], [])

            if self.chk_rescale_x.isChecked():
                self.plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.XAxis)
            else:
                self.plot_widget.getViewBox().disableAutoRange(axis=pg.ViewBox.XAxis)

            if self.chk_rescale_y.isChecked():
                self.plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)
            else:
                self.plot_widget.getViewBox().disableAutoRange(axis=pg.ViewBox.YAxis)

            do_fit = self.radio_fit_on.isChecked()
            is_save_frame = False
            if getattr(self, 'is_sequential_running', False) and is_new_data:
                is_save_frame = (self.current_skip_count >= self.spin_skip_frames.value())
                if getattr(self, '_seq_fit_failed', False) and not is_save_frame:
                    do_fit = False

            if do_fit:
                func = self.combo_fit_func.currentText()
                peak_count = self.combo_fit_peak_count.currentData()
                peak_sort_order = self.combo_peak_sort.currentData()
                baseline_model = self.combo_baseline_model.currentData()
                fit_start = self.spin_fit_start.value()
                fit_end = self.spin_fit_end.value()

                x_fit, y_fit, res = self.analyzer.fit_spectrum(
                    x_data, disp_data, func, fit_start, fit_end,
                    peak_count=peak_count, peak_sort_order=peak_sort_order,
                    baseline_model=baseline_model
                )

                if x_fit is not None:
                    self._seq_fit_failed = False
                    self.fit_curve.setData(x_fit, y_fit)
                    is_edge = res.get("analysis_type") == "diamond_raman_edge"
                    if is_edge:
                        self.fit_baseline_curve.clear()
                        self.edge_marker.setValue(res["edge_position"])
                        self.edge_marker.show()
                    else:
                        self.fit_baseline_curve.setData(x_fit, res["y_baseline"])
                        self.edge_marker.hide()

                    first_peak = res["peaks"][0]
                    w_peak1 = first_peak["position"]
                    w_err1 = first_peak["position_err"]

                    self.current_w_peak1 = w_peak1

                    if res.get("peak_count", 1) > 1 and "y_fit1" in res:
                        self.fit_curve_sub1.setData(x_fit, res["y_fit1"])
                        if "y_fit2" in res:
                            self.fit_curve_sub2.setData(x_fit, res["y_fit2"])
                        else:
                            self.fit_curve_sub2.clear()
                    else:
                        self.fit_curve_sub1.clear()
                        self.fit_curve_sub2.clear()

                    text = (
                        f"<span><b>Function:</b> {func}<br>"
                    )
                    if is_edge:
                        text += "<b>Method:</b> -dI/dν, pseudo-Voigt + linear baseline<br><br>"
                    else:
                        text += (
                            f"<b>Fit Peaks:</b> {peak_count}<br>"
                            f"<b>Sort peaks:</b> {self.combo_peak_sort.currentText()}<br>"
                        )
                        baseline = res["baseline"]
                        baseline_text = baseline["requested"]
                        if baseline["requested"] != baseline["selected"]:
                            baseline_text += f" &rarr; {baseline['selected']}"
                        text += f"<b>Baseline:</b> {baseline_text}<br><br>"

                    self.latest_fit_res = res.copy()
                    self.latest_fit_func = func

                    for peak in res["peaks"]:
                        i = peak["index"]
                        text += f"<u>{'Diamond edge' if is_edge else f'Peak {i}'}</u><br>"
                        text += f" Pos: {peak['position']:.3f} ± {peak['position_err']:.3f}<br>"
                        text += f" Width: {peak['width']:.3f} ± {peak['width_err']:.3f}<br><br>"

                    text += f"<b>R-value:</b><br> {res['R2']:.4f}</span>"

                    if self.pressure_window is not None and self.pressure_window.isVisible():
                        # A pixel axis has no Wavelength/Raman-shift value at all, so a
                        # peak position fit against one must never reach the pressure
                        # calculator as though it were a real physical unit.
                        if public_axis_kind(self) == "pixel":
                            self.pressure_window.set_fit_peaks([])
                            text += "<br><br><span>Calculated Pressure:<br>No calibrated axis</span>"
                        else:
                            self.pressure_window.set_fit_peaks(res["peaks"])
                            p = self.pressure_window.current_pressure
                            p_err = self.pressure_window.current_pressure_err
                            if p is not None and p_err is not None:
                                text += f"<br><br><span>Calculated Pressure:<br>{p:.3f} ± {p_err:.3f} GPa</span>"
                            else:
                                text += "<br><br><span>Calculated Pressure:<br>Calc Error</span>"






                    self.fitting_text.setHtml(text)
                else:
                    self.fit_curve.clear()
                    self.fit_baseline_curve.clear()
                    self.fit_curve_sub1.clear()
                    self.fit_curve_sub2.clear()
                    self.edge_marker.hide()
                    self.current_w_peak1 = None
                    self.latest_fit_res = None
                    self.latest_fit_func = None
                    if self.pressure_window is not None:
                        self.pressure_window.set_fit_peaks([])
                    self.fitting_text.setHtml("<span>Fitting failed or out of range.</span>")

                    if getattr(self, 'is_sequential_running', False):
                        self._seq_fit_failed = True
                    else:
                        self.radio_fit_off.setChecked(True)
            else:
                self.fit_curve.clear()
                self.fit_baseline_curve.clear()
                self.fit_curve_sub1.clear()
                self.fit_curve_sub2.clear()
                self.edge_marker.hide()
                self.current_w_peak1 = None
                self.latest_fit_res = None
                self.latest_fit_func = None
                if self.pressure_window is not None:
                    self.pressure_window.set_fit_peaks([])
                if self.radio_fit_on.isChecked():
                     self.fitting_text.setHtml("<span>Fitting failed. Paused for skipped frames.</span>")
                else:
                     self.fitting_text.setHtml("")

        elif mode == "2d":
            if getattr(self, 'raw_2d_data', None) is None: return
            disp_data = self.raw_2d_data.copy()
            if self.chk_flip_x.isChecked():
                disp_data = disp_data[:, ::-1]

            self.latest_2d_data = disp_data
            self.stacked_widget.setCurrentIndex(1)
            self.image_view.setImage(disp_data.T)

            h, w = disp_data.shape
            view = self.image_view.getView()
            vb = view.vb if hasattr(view, 'vb') else view
            vb.setLimits(xMin=0, xMax=w, yMin=0, yMax=h)

        if getattr(self, 'is_sequential_running', False) and is_new_data:
            if self.current_skip_count >= self.spin_skip_frames.value():
                now_dt = datetime.now()
                date_str = now_dt.strftime("%Y%m%d_%H%M%S_%f")[:-3]
                timestamp_str = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                filename = f"seq_{self.seq_count:05d}_{date_str}.txt"
                file_path = os.path.join(self.seq_dir, filename)

                success = self._save_data_to_path(file_path, show_msg=False)
                if success:
                    self.seq_log_data.append([filename, now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]])

                    if self.radio_fit_on.isChecked() and getattr(self, 'seq_fitting_summary_path', None):
                        peak_count = self.combo_fit_peak_count.currentData()
                        pw = self.pressure_window
                        pressure_info = None
                        if pw is not None and pw.isVisible():
                            pressure_info = {
                                "pressure":     pw.current_pressure,
                                "pressure_err": pw.current_pressure_err,
                            }
                        try:
                            self.file_io.append_fitting_seq_row(
                                self.seq_fitting_summary_path, filename, timestamp_str,
                                self.latest_fit_res, peak_count, pressure_info
                            )
                        except Exception as e:
                            print(f"Failed to write summary: {e}")

                    self.current_skip_count = 0
                    self.seq_count += 1
                    self.update_seq_progress()
                    if self.seq_count >= self.spin_max_num.value():
                        self.stop_sequential()
                else:
                    self.current_skip_count = 0
            else:
                self.current_skip_count += 1

    def on_mouse_moved(self, pos):
        try:
            if self.stacked_widget.currentIndex() == 0:
                vb = self.plot_widget.plotItem.vb
                if vb.sceneBoundingRect().contains(pos):
                    mouse_point = vb.mapSceneToView(pos)
                    x_val = mouse_point.x()

                    x_pixel = int(np.round(x_val))
                    axis_kind = public_axis_kind(self)
                    if axis_kind != "pixel" and self.latest_1d_data is not None:
                        x_arr = self.get_x_axis(len(self.latest_1d_data))
                        if self.chk_flip_x.isChecked():
                            x_arr = x_arr[::-1]
                        disp_idx = np.argmin(np.abs(x_arr - x_val))
                    else:
                        disp_idx = x_pixel

                    data_val_str = ""
                    if self.latest_1d_data is not None and 0 <= disp_idx < len(self.latest_1d_data):
                        counts = self.latest_1d_data[disp_idx]
                        data_val_str = f", Counts: {counts:.1f}"

                    unit = "Wavelength" if not self.radio_spec_mode_raman.isChecked() else "Raman shift"
                    unit_sym = "nm" if not self.radio_spec_mode_raman.isChecked() else "cm⁻¹"

                    if axis_kind != "pixel":
                        self.coord_label.setText(f"1D Spectrum - {unit}: {x_val:.3f} {unit_sym} (Pixel: {disp_idx}){data_val_str}")
                    else:
                        self.coord_label.setText(f"1D Spectrum - Pixel: {x_val:.1f}{data_val_str}")

            elif self.stacked_widget.currentIndex() == 1:
                view = self.image_view.getView()
                vb = view.vb if hasattr(view, 'vb') else view
                if vb.sceneBoundingRect().contains(pos):
                    mouse_point = vb.mapSceneToView(pos)
                    x_pixel = int(np.round(mouse_point.x()))
                    y_pixel = int(np.round(mouse_point.y()))
                    data_val_str = ""
                    if self.latest_2d_data is not None:
                        h, w = self.latest_2d_data.shape
                        if 0 <= x_pixel < w and 0 <= y_pixel < h:
                            intensity = self.latest_2d_data[y_pixel, x_pixel]
                            data_val_str = f", Intensity: {intensity:.1f}"
                    self.coord_label.setText(f"2D Image Cursor - X: {x_pixel}, Y: {y_pixel}{data_val_str}")
        except: pass
