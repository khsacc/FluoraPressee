from contextlib import redirect_stderr
from io import StringIO
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.ui.calibration_ui import CalibrationWindow
from src.core.calibration_reference import MatchCandidate


_APP = QApplication.instance() or QApplication([])


class CalibrationUiAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.window = CalibrationWindow()
        x = np.arange(1024.0)
        self.window.current_spectrum = (
            100.0
            + 800.0 * np.exp(-0.5 * ((x - 150.0) / 3.0) ** 2)
            + 700.0 * np.exp(-0.5 * ((x - 420.0) / 3.0) ** 2)
            + 600.0 * np.exp(-0.5 * ((x - 750.0) / 3.0) ** 2)
        )
        self.window._apply_plot_data_limits(len(self.window.current_spectrum))
        self.window.find_peaks()
        self.assertEqual(len(self.window.row_widgets), 3)

    def tearDown(self):
        self.window.close()

    def _set_standard(self, standard_id, checked):
        for index in range(self.window.list_standards.count()):
            item = self.window.list_standards.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == standard_id:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                return
        self.fail(f"Missing standard {standard_id}")

    def test_emission_standards_are_ordered_ne_ar_hg(self):
        standard_ids = [
            self.window.list_standards.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.window.list_standards.count())
        ]
        self.assertEqual(standard_ids, ["Ne-I", "Ar-I", "Hg-I"])

    def _standard_ids(self):
        return {
            self.window.list_standards.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.window.list_standards.count())
        }

    def test_raman_shift_standards_hidden_until_raman_shift_unit_selected(self):
        self.assertTrue(self.window.radio_unit_wl.isChecked())
        self.assertNotIn("ASTM-Cyclohexane", self._standard_ids())
        self.assertNotIn("Polystyrene-NMIJ", self._standard_ids())

        self.window.radio_unit_raman.setChecked(True)

        self.assertIn("ASTM-Cyclohexane", self._standard_ids())
        self.assertIn("Polystyrene-NMIJ", self._standard_ids())
        # Ne/Ar/Hg remain selectable in both units.
        self.assertIn("Ne-I", self._standard_ids())

        self.window.radio_unit_wl.setChecked(True)

        self.assertNotIn("ASTM-Cyclohexane", self._standard_ids())
        self.assertNotIn("Polystyrene-NMIJ", self._standard_ids())

    def test_raman_shift_standard_selection_survives_a_unit_round_trip(self):
        self.window.radio_unit_raman.setChecked(True)
        self._set_standard("Polystyrene-NMIJ", True)

        self.window.radio_unit_wl.setChecked(True)
        self.window.radio_unit_raman.setChecked(True)

        item = next(
            self.window.list_standards.item(index)
            for index in range(self.window.list_standards.count())
            if self.window.list_standards.item(index).data(
                Qt.ItemDataRole.UserRole
            ) == "Polystyrene-NMIJ"
        )
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)

    def test_raman_shift_line_assignment_survives_switch_back_to_wavelength_unit(self):
        self.window.radio_unit_raman.setChecked(True)
        polystyrene = self.window.reference_standards["Polystyrene-NMIJ"].lines[0]
        self.window.assign_reference_line(0, polystyrene)

        self.window.radio_unit_wl.setChecked(True)

        self.assertEqual(
            self.window.assignments[0]["line_id"], polystyrene.line_id
        )

    def test_used_reference_standards_reports_emission_lines_for_wavelength_unit(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        argon = self.window.reference_standards["Ar-I"].lines[8]
        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, argon)

        used = {s["standard_id"]: s for s in self.window._used_reference_standards()}

        self.assertEqual(set(used), {"Ne-I", "Ar-I"})
        self.assertEqual(used["Ne-I"]["quantity"], "wavelength_nm")
        self.assertEqual(
            self.window._reference_kind_for("Wavelength", list(used.values())),
            "emission_lines",
        )

    def test_used_reference_standards_reports_emission_lines_with_excitation_for_raman_unit(self):
        self.window.radio_unit_raman.setChecked(True)
        neon = self.window.reference_standards["Ne-I"].lines[17]
        argon = self.window.reference_standards["Ar-I"].lines[8]
        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, argon)

        used = self.window._used_reference_standards()

        self.assertEqual(
            self.window._reference_kind_for("Raman shift", used),
            "emission_lines_with_excitation",
        )

    def test_used_reference_standards_reports_raman_standard_when_a_raman_native_line_is_used(self):
        self.window.radio_unit_raman.setChecked(True)
        polystyrene = self.window.reference_standards["Polystyrene-NMIJ"].lines[0]
        neon = self.window.reference_standards["Ne-I"].lines[17]
        self.window.assign_reference_line(0, polystyrene)
        self.window.assign_reference_line(1, neon)

        used = {s["standard_id"]: s for s in self.window._used_reference_standards()}

        self.assertEqual(set(used), {"Polystyrene-NMIJ", "Ne-I"})
        self.assertEqual(used["Polystyrene-NMIJ"]["quantity"], "raman_shift_cm1")
        self.assertEqual(
            self.window._reference_kind_for("Raman shift", list(used.values())),
            "raman_standard",
        )

    def test_used_reference_standards_excludes_unchecked_and_manual_rows(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        argon = self.window.reference_standards["Ar-I"].lines[8]
        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, argon)
        # An unchecked row must not count even though it still has an assignment.
        self.window.row_widgets[1]["check"].setChecked(False)
        # A manually-typed value (no catalogue line_id) contributes no standard.
        self.window.assignments[2] = {
            "line_id": None, "wavelength_nm": 650.0, "value": 650.0,
            "species": "Custom", "locked": True,
        }
        self.window.row_widgets[2]["check"].setChecked(True)

        used = self.window._used_reference_standards()

        self.assertEqual([s["standard_id"] for s in used], ["Ne-I"])

    def test_automatic_matching_always_expects_increasing_wavelength(self):
        # self.current_spectrum's pixel index already has Flip X applied (see
        # on_data_ready/use_displayed_data), so increasing-pixel-index ->
        # increasing-wavelength must hold regardless of the checkbox state -
        # otherwise the matcher double-flips relative to the already-flipped data.
        for flip_state in (True, False):
            self.window._flip_x_enabled = lambda flip_state=flip_state: flip_state

            with patch(
                "src.ui.calibration_ui.find_match_candidates", return_value=[]
            ) as matcher:
                self.window.find_assignment_candidates()

            self.assertEqual(matcher.call_args.kwargs["expected_slope_sign"], 1)

    def test_raw_pixel_domain_unchanged_when_not_flipped(self):
        self.window._spectrum_is_flipped = False
        coeffs = (673.3, 2.1097e-2, -3.347e-7)

        self.assertEqual(self.window._to_raw_pixel_domain(coeffs), coeffs)

    def test_raw_pixel_domain_undoes_flip_so_each_raw_pixel_keeps_its_wavelength(self):
        # A calibration performed while Flip X was checked is fit against
        # self.current_spectrum's (already-flipped) pixel index. Once handed to
        # the main window, that index must be converted back to the raw sensor
        # pixel domain (see _to_raw_pixel_domain / save_and_apply), or the main
        # window's own flip handling double-flips it. The physical invariant
        # this must preserve: whichever raw sensor pixel a peak sits at, it
        # keeps the same wavelength whether Flip X is later toggled or not.
        self.window._spectrum_is_flipped = True
        n = len(self.window.current_spectrum)
        flipped_domain_coeffs = (673.3, 2.1097e-2, -3.347e-7)

        raw_domain_coeffs = self.window._to_raw_pixel_domain(flipped_domain_coeffs)

        c0, c1, c2 = flipped_domain_coeffs
        r0, r1, r2 = raw_domain_coeffs
        flipped_pixel = 150.0
        raw_pixel = (n - 1) - flipped_pixel
        wavelength_via_flipped_domain = c0 + c1 * flipped_pixel + c2 * flipped_pixel ** 2
        wavelength_via_raw_domain = r0 + r1 * raw_pixel + r2 * raw_pixel ** 2
        self.assertAlmostEqual(
            wavelength_via_flipped_domain, wavelength_via_raw_domain, places=6
        )
        # The conversion must be its own inverse (flipping twice is a no-op).
        roundtripped = self.window._to_raw_pixel_domain(raw_domain_coeffs)
        for actual, expected in zip(roundtripped, flipped_domain_coeffs):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_raw_domain_spectrum_unchanged_when_not_flipped(self):
        self.window._spectrum_is_flipped = False

        result = self.window._raw_domain_spectrum()

        np.testing.assert_array_equal(result, self.window.current_spectrum)

    def test_raw_domain_spectrum_undoes_flip(self):
        self.window._spectrum_is_flipped = True

        result = self.window._raw_domain_spectrum()

        np.testing.assert_array_equal(result, self.window.current_spectrum[::-1])

    def test_raw_domain_spectrum_none_when_no_spectrum_acquired(self):
        self.window.current_spectrum = None

        self.assertIsNone(self.window._raw_domain_spectrum())

    def test_selected_candidate_preview_takes_priority_over_seed_axis(self):
        self.window.initial_wavelength_axis = np.linspace(600.0, 800.0, 1024)
        self.window.match_candidates = [
            MatchCandidate(
                coefficients=(800.0, -0.2, 0.0),
                assignments=(),
                matched_count=3,
                rms_nm=0.0,
                center_error_nm=None,
                score=300.0,
            )
        ]
        self.window.combo_match_candidate.addItem("Flipped candidate")
        self.window.combo_match_candidate.setCurrentIndex(0)

        axis = self.window._projection_axis_nm()

        self.assertGreater(axis[0], axis[-1])

    def test_default_window_width_is_one_and_a_half_times_previous_width(self):
        self.assertEqual(self.window.width(), 1650)
        self.assertEqual(self.window.height(), 750)

    def test_full_screen_button_f11_and_escape(self):
        self.window.show()
        _APP.processEvents()

        self.window.btn_full_screen.click()
        _APP.processEvents()
        self.assertTrue(self.window.isFullScreen())
        self.assertEqual(self.window.btn_full_screen.text(), "Exit full screen")

        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(self.window.btn_full_screen.text(), "Full screen")

        QTest.keyClick(self.window, Qt.Key.Key_F11)
        _APP.processEvents()
        self.assertTrue(self.window.isFullScreen())
        QTest.keyClick(self.window, Qt.Key.Key_F11)
        _APP.processEvents()
        self.assertFalse(self.window.isFullScreen())

    def test_switching_standard_keeps_locked_assignment(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        self.window.assign_reference_line(0, neon)

        self._set_standard("Ar-I", True)
        self._set_standard("Ne-I", False)

        assignment = self.window.assignments[0]
        self.assertEqual(assignment["line_id"], neon.line_id)
        self.assertEqual(assignment["species"], "Ne I")
        combo = self.window.row_widgets[0]["input"]
        self.assertGreaterEqual(combo.findData(neon.line_id), 0)

    def test_disabled_neon_lines_are_kept_in_catalogue_but_hidden_from_gui(self):
        disabled = {
            line.line_id: line.wavelength_nm
            for line in self.window.reference_standards["Ne-I"].lines
            if not line.enabled_for_calibration
        }
        active_ids = {line.line_id for line in self.window.active_reference_lines()}

        self.assertEqual(
            set(disabled.values()),
            {673.80320, 675.95821, 705.12922, 706.4762},
        )
        self.assertTrue(set(disabled).isdisjoint(active_ids))
        self.assertTrue(set(disabled).issubset(self.window.reference_lines_by_id))

    def test_mixed_species_can_form_one_calibration(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        argon = self.window.reference_standards["Ar-I"].lines[8]
        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, argon)

        self.assertIsNotNone(self.window.calib_coeffs)
        self.assertEqual(
            {assignment["species"] for assignment in self.window.assignments.values()},
            {"Ne I", "Ar I"},
        )
        self.assertTrue(self.window.btn_save_apply.isEnabled())

    def test_same_literature_line_cannot_be_assigned_twice(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]

        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, neon)

        self.assertIn(0, self.window.assignments)
        self.assertNotIn(1, self.window.assignments)

    def test_only_used_peak_has_full_height_dashed_marker(self):
        self.assertFalse(hasattr(self.window, "peak_select_scatter"))
        self.assertFalse(self.window.peak_lines[0].isVisible())

        neon = self.window.reference_standards["Ne-I"].lines[17]
        self.window.assign_reference_line(0, neon)
        self.assertTrue(self.window.peak_lines[0].isVisible())
        self.assertFalse(self.window.peak_lines[1].isVisible())

        self.window.row_widgets[0]["check"].setChecked(False)
        self.assertFalse(self.window.peak_lines[0].isVisible())

    def test_detected_ticks_have_invisible_click_targets(self):
        points = self.window.detected_select_scatter.points()

        self.assertEqual(len(points), 2 * len(self.window.fitted_peaks))
        self.assertEqual(self.window.detected_select_scatter.zValue(), 1000.0)
        self.assertEqual(
            self.window.detected_select_scatter.opts["brush"].color().alpha(), 0
        )
        self.assertEqual(
            self.window.detected_select_scatter.opts["pen"].style(),
            Qt.PenStyle.NoPen,
        )
        self.assertIn("Detected peak #1", self.window._detected_peak_tooltip(
            x=0.0, y=0.0, data=0
        ))

    def test_manual_two_click_assignment_and_clear(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        point = type("Point", (), {"data": lambda _self: neon.line_id})()

        self.window.select_measured_peak(0)
        self.window.on_reference_line_clicked(None, [point])

        self.assertEqual(self.window.assignments[0]["line_id"], neon.line_id)
        self.assertTrue(self.window.row_widgets[0]["check"].isChecked())
        self.assertIsNone(self.window.selected_peak_row)

        self.window.select_measured_peak(0)
        self.assertTrue(self.window.btn_clear_assignment.isEnabled())
        self.window.btn_clear_assignment.click()
        self.assertNotIn(0, self.window.assignments)
        self.assertFalse(self.window.row_widgets[0]["check"].isChecked())

    def test_reference_combo_displays_only_the_numeric_value(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]

        self.window.assign_reference_line(0, neon)
        combo_text = self.window.row_widgets[0]["input"].currentText()

        self.assertEqual(combo_text, f"{neon.wavelength_nm:.5f}")
        self.assertNotIn(neon.species, combo_text)

    def test_overlapping_literature_ticks_offer_a_choice(self):
        lines = self.window.reference_standards["Ne-I"].lines[17:19]
        points = np.asarray([
            type("Point", (), {"data": lambda _self, line=line: line.line_id})()
            for line in lines
        ], dtype=object)

        self.window.select_measured_peak(0)
        self.window.on_reference_line_clicked(None, points)
        choices = [
            action for action in self.window.reference_candidate_menu.actions()
            if action.isEnabled() and not action.isSeparator()
        ]

        self.assertEqual(len(choices), 2)
        choices[1].trigger()
        self.assertEqual(self.window.assignments[0]["line_id"], lines[1].line_id)
        self.window.reference_candidate_menu.hide()

    def test_click_handler_errors_are_printed_to_terminal(self):
        terminal_output = StringIO()

        with redirect_stderr(terminal_output):
            self.window.on_measured_peak_clicked(None, [object()])

        output = terminal_output.getvalue()
        self.assertIn("[Calibration GUI] Detected peak selection", output)
        self.assertIn("Traceback", output)
        self.assertIn("printed to the terminal", self.window.lbl_assignment_help.text())

    def test_literature_band_uses_screen_distance_and_hover_highlight(self):
        self.window.initial_wavelength_axis = np.linspace(600.0, 800.0, 1024)
        self.window.update_reference_overlay()
        pixel, line_id = self.window.reference_tick_positions[0]
        literature_mid = sum(self.window._tick_levels()["literature"]) / 2.0
        scene_pos = self.window.plot_widget.getViewBox().mapViewToScene(
            QPointF(pixel, literature_mid)
        )

        candidates = self.window._reference_candidates_at_scene_x(scene_pos.x())
        self.window.on_main_plot_mouse_moved(scene_pos)

        self.assertTrue(candidates)
        self.assertEqual(candidates[0][1].line_id, line_id)
        self.assertEqual(self.window.hovered_reference_line_id, line_id)
        self.assertEqual(
            self.window.reference_marker_items[line_id].opts["pen"].widthF(),
            6.0,
        )

    def test_detected_band_click_remains_selectable_after_y_zoom(self):
        self.window.plot_scatter.setData(self.window.current_spectrum)
        self.window.show()
        _APP.processEvents()
        view_box = self.window.plot_widget.getViewBox()
        detected_low, detected_high = self.window._tick_levels()["detected"]
        center = self.window.row_widgets[0]["px"]
        margin = 0.02 * (detected_high - detected_low)
        view_box.setRange(
            xRange=(center - 20.0, center + 20.0),
            yRange=(detected_low - margin, detected_high + margin),
            padding=0,
        )
        _APP.processEvents()
        click_view_pos = QPointF(
            center,
            detected_low + 0.02 * (detected_high - detected_low),
        )
        click_scene_pos = view_box.mapViewToScene(click_view_pos)
        click_viewport_pos = self.window.plot_widget.mapFromScene(click_scene_pos)

        self.window.clear_peak_selection()
        QTest.mouseClick(
            self.window.plot_widget.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            click_viewport_pos,
        )
        _APP.processEvents()

        self.assertEqual(self.window.selected_peak_row, 0)

    def test_fit_plot_click_selects_peak_and_escape_cancels(self):
        second_plot = self.window.row_widgets[1]["plot"]

        second_plot.scene().sigMouseClicked.emit(None)
        self.assertEqual(self.window.selected_peak_row, 1)
        self.assertEqual(
            self.window.peak_tick_items[1].opts["pen"].widthF(), 5.0
        )
        self.assertIn("#FFB300", second_plot.styleSheet())

        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertIsNone(self.window.selected_peak_row)
        self.assertEqual(
            self.window.peak_tick_items[1].opts["pen"].widthF(), 3.0
        )

    def test_manual_line_combo_supports_contains_search(self):
        self.window.row_widgets[0]["check"].setChecked(True)
        completer = self.window.row_widgets[0]["input"].completer()

        self.assertEqual(completer.filterMode(), Qt.MatchFlag.MatchContains)
        self.assertEqual(
            completer.caseSensitivity(), Qt.CaseSensitivity.CaseInsensitive
        )

    def test_peak_legend_follows_vertical_marker_order(self):
        labels = [label.text for _sample, label in self.window.plot_legend.items]

        self.assertEqual(
            labels,
            ["Detected peak", "Literature line", "Used peak"],
        )

    def test_individual_fit_plots_have_peak_number_titles(self):
        first_plot = self.window.bottom_layout.itemAt(0).widget()
        second_plot = self.window.bottom_layout.itemAt(1).widget()

        self.assertEqual(first_plot.plotItem.titleLabel.text, "Peak #1")
        self.assertEqual(second_plot.plotItem.titleLabel.text, "Peak #2")

    def test_individual_fit_plots_mark_the_fitted_peak_center(self):
        for index, fitted_peak in enumerate(self.window.fitted_peaks):
            small_plot = self.window.bottom_layout.itemAt(index).widget()
            center_lines = [
                item for item in small_plot.plotItem.items
                if isinstance(item, pg.InfiniteLine)
            ]

            self.assertEqual(len(center_lines), 1)
            self.assertAlmostEqual(
                center_lines[0].value(), fitted_peak["center"], places=8
            )
            self.assertEqual(
                center_lines[0].pen.style(), Qt.PenStyle.DashLine
            )

    def test_detected_ticks_are_above_plain_literature_ticks(self):
        neon = self.window.reference_standards["Ne-I"].lines[17]
        argon = self.window.reference_standards["Ar-I"].lines[8]
        self.window.assign_reference_line(0, neon)
        self.window.assign_reference_line(1, argon)

        levels = self.window._tick_levels()
        self.assertGreater(levels["detected"][0], levels["literature"][1])
        self.assertTrue(self.window.reference_overlay_items)
        for marker in self.window.reference_overlay_items:
            x, y = marker.getData()
            self.assertEqual(float(x[0]), float(x[1]))
            self.assertLess(float(np.max(y)), 0.0)
        self.assertEqual(
            self.window.reference_select_scatter.opts["brush"].color().alpha(), 0
        )
        self.assertEqual(
            self.window.reference_select_scatter.opts["pen"].style(),
            Qt.PenStyle.NoPen,
        )

    def test_plot_zoom_is_limited_to_acquired_pixel_domain(self):
        view_box = self.window.plot_widget.getViewBox()

        self.assertEqual(view_box.state["limits"]["xLimits"], [0.0, 1023.0])
        view_box.setXRange(-500.0, 2000.0, padding=0)
        x_range, _y_range = view_box.viewRange()
        self.assertGreaterEqual(x_range[0], 0.0)
        self.assertLessEqual(x_range[1], 1023.0)


if __name__ == "__main__":
    unittest.main()
