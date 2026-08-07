"""Wavelength/Raman toggle and excitation-wavelength-edit invalidation.

Covers the hard boundary between calibration axis_kind and the display toggle:
neither on_spec_mode_changed() nor on_exc_wl_changed() may leave calib_coeffs
active once it disagrees with what's on screen, and get_x_axis() must no
longer perform the implicit nm<->cm-1 conversion that used to paper over such
a mismatch (src/ui/ui_mixins/acquisition_mixin.py, spectrometer_control_mixin.py).
"""
import unittest

try:
    from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
    from src.ui.ui_mixins.file_io_mixin import FileIOMixin
    from src.ui.ui_mixins.spectrometer_control_mixin import SpectrometerControlMixin
    HAS_QT = True
except ModuleNotFoundError:
    class AcquisitionMixin:
        pass

    class FileIOMixin:
        pass

    class SpectrometerControlMixin:
        pass

    HAS_QT = False


class Checked:
    """Stands in for a QRadioButton. Two radio buttons sharing the same
    parent are auto-exclusive in real Qt without an explicit QButtonGroup
    (see radio_spec_mode_wl/radio_spec_mode_raman in main_window.py), so
    `peer` lets tests exercise that same one-sets-the-other-unchecked
    behavior instead of having to flip both by hand."""

    def __init__(self, checked=False):
        self.checked = checked
        self.peer = None

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = checked
        if checked and self.peer is not None:
            self.peer.checked = False

    def blockSignals(self, _blocked):
        pass


class Value:
    def __init__(self, value=0):
        self._value = value
        self.enabled = None

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def blockSignals(self, _blocked):
        pass

    def setEnabled(self, enabled):
        self.enabled = enabled


class Combo:
    def __init__(self, text="0"):
        self._text = text
        self._index = 0

    def currentText(self):
        return self._text

    def count(self):
        return 1

    def setCurrentIndex(self, index):
        self._index = index


class Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class Enabled:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, value):
        self.enabled = value


class Thread:
    is_measuring = False
    native_wavelengths = None


class PressureWindow:
    def __init__(self):
        self.fit_peaks_calls = []

    def set_fit_peaks(self, peaks):
        self.fit_peaks_calls.append(peaks)


class Window(FileIOMixin, SpectrometerControlMixin, AcquisitionMixin):
    def __init__(self):
        self.calib_coeffs = None
        self.calib_unit = "Wavelength"
        self.calib_laser_wl = None
        self.axis_source = "pixel"
        self.configuration_label = "600 g/mm | 690.000 nm | ROI 45–65"
        self.active_configuration_id = "cfg-1"
        self.active_configuration_slot_id = "slot-1"
        self.positioned_configuration_id = "cfg-1"
        self.positioned_configuration_slot_id = "slot-1"
        self.lbl_loaded_configuration = Label()
        self.lbl_centre = Label()
        self.radio_spec_mode_wl = Checked(True)
        self.radio_spec_mode_raman = Checked(False)
        self.radio_spec_mode_wl.peer = self.radio_spec_mode_raman
        self.radio_spec_mode_raman.peer = self.radio_spec_mode_wl
        self.spin_exc_wl = Value(532.0)
        self.spin_centre_wl = Value(690.0)
        self.combo_grating = Combo("0")
        self.physical_grating = "0"
        self.physical_center_wl = 690.0
        self.btn_apply_spec = Enabled()
        self.raw_1d_data = None
        self.thread = Thread()
        self.pressure_window = None
        self.update_display_calls = []
        self.sync_fit_range_calls = 0
        self.sync_pressure_calculator_mode_calls = 0

    def update_plot_labels(self):
        pass

    def sync_fit_range_to_spectrum(self, force=False):
        self.sync_fit_range_calls += 1

    def update_display(self, is_new_data=True):
        self.update_display_calls.append(is_new_data)

    def sync_pressure_calculator_mode(self):
        self.sync_pressure_calculator_mode_calls += 1

    def on_fit_settings_changed(self):
        pass


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class AxisToggleInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.window = Window()
        self.window.calib_coeffs = (669.4, 0.0208, -1.2e-7)
        self.window.calib_unit = "Wavelength"
        self.window.calib_laser_wl = None

    def test_toggling_to_raman_invalidates_a_wavelength_calibration(self):
        self.window.radio_spec_mode_wl.setChecked(False)
        self.window.radio_spec_mode_raman.setChecked(True)

        self.window.on_spec_mode_changed()

        self.assertIsNone(self.window.calib_coeffs)
        self.assertEqual(self.window.axis_source, "pixel")
        # Physical position bookkeeping must survive -- the grating/centre/ROI
        # haven't actually moved, only the calibration became invalid.
        self.assertEqual(self.window.positioned_configuration_id, "cfg-1")
        self.assertIn("calibration invalidated", self.window.lbl_loaded_configuration.text)

    def test_toggling_to_the_same_unit_does_not_invalidate(self):
        self.window.radio_spec_mode_wl.setChecked(True)
        self.window.radio_spec_mode_raman.setChecked(False)

        self.window.on_spec_mode_changed()

        self.assertIsNotNone(self.window.calib_coeffs)

    def test_changing_excitation_wavelength_invalidates_a_raman_calibration(self):
        self.window.calib_unit = "Raman shift"
        self.window.calib_laser_wl = 532.0
        self.window.radio_spec_mode_wl.setChecked(False)
        self.window.radio_spec_mode_raman.setChecked(True)
        self.window.spin_exc_wl.setValue(633.0)

        self.window.on_exc_wl_changed()

        self.assertIsNone(self.window.calib_coeffs)
        self.assertEqual(self.window.axis_source, "pixel")

    def test_excitation_wavelength_within_tolerance_does_not_invalidate(self):
        self.window.calib_unit = "Raman shift"
        self.window.calib_laser_wl = 532.0
        self.window.radio_spec_mode_raman.setChecked(True)
        self.window.spin_exc_wl.setValue(532.0001)  # within 0.001 nm resolution

        self.window.on_exc_wl_changed()

        self.assertIsNotNone(self.window.calib_coeffs)

    def test_excitation_wavelength_change_is_a_no_op_for_a_wavelength_calibration(self):
        self.window.calib_unit = "Wavelength"
        self.window.radio_spec_mode_wl.setChecked(True)
        self.window.spin_exc_wl.setValue(999.0)

        self.window.on_exc_wl_changed()

        self.assertIsNotNone(self.window.calib_coeffs)


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class ApplyCalibrationSyncsDisplayModeTests(unittest.TestCase):
    """Regression test: apply_calibration() (used by the Calibration window's
    "Save and apply" and the deprecated inline POST /calibration) used to set
    calib_unit without touching the Wavelength/Raman display toggle at all.
    The Calibration window's own unit radio button is independent of the main
    window's toggle (only copied once, when the dialog opens), and the inline
    API's `unit` never touched the toggle either -- so calib_unit could end up
    disagreeing with what public_axis_unit()/get_x_axis() actually display,
    silently mislabeling Raman shift values as nm or vice versa."""

    def setUp(self):
        self.window = Window()

    def test_applying_a_raman_calibration_switches_the_display_toggle(self):
        self.window.radio_spec_mode_wl.setChecked(True)
        self.window.radio_spec_mode_raman.setChecked(False)

        self.window.apply_calibration(
            (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=532.0,
        )

        self.assertTrue(self.window.radio_spec_mode_raman.isChecked())
        self.assertFalse(self.window.radio_spec_mode_wl.isChecked())
        self.assertEqual(self.window.spin_exc_wl.value(), 532.0)
        self.assertEqual(self.window.calib_unit, "Raman shift")

    def test_applying_a_wavelength_calibration_switches_the_display_toggle(self):
        self.window.radio_spec_mode_wl.setChecked(False)
        self.window.radio_spec_mode_raman.setChecked(True)

        self.window.apply_calibration((669.4, 0.0208, 0.0), "label", calib_unit="Wavelength")

        self.assertTrue(self.window.radio_spec_mode_wl.isChecked())
        self.assertFalse(self.window.radio_spec_mode_raman.isChecked())

    def test_applying_a_raman_calibration_recomputes_the_displayed_centre_value(self):
        # Regression test: _sync_display_mode_to_unit() used to only flip the
        # radio buttons, leaving spin_centre_wl showing the stale Wavelength
        # centre (690 nm) instead of the Raman-shift equivalent -- reproduced
        # exactly: 690 nm physical centre / 532 nm excitation should read
        # ~4304.24 cm-1, not remain 690.
        self.window.physical_center_wl = 690.0
        self.window.radio_spec_mode_wl.setChecked(True)
        self.window.radio_spec_mode_raman.setChecked(False)
        self.window.spin_centre_wl.setValue(690.0)

        self.window.apply_calibration(
            (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=532.0,
        )

        expected = (1e7 / 532.0) - (1e7 / 690.0)
        self.assertAlmostEqual(self.window.spin_centre_wl.value(), expected, places=2)

    def test_applying_a_raman_calibration_enables_excitation_spinbox_and_syncs_pressure(self):
        self.window.radio_spec_mode_wl.setChecked(True)
        self.window.radio_spec_mode_raman.setChecked(False)
        self.window.spin_exc_wl.enabled = False

        self.window.apply_calibration(
            (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=532.0,
        )

        self.assertTrue(self.window.spin_exc_wl.enabled)
        self.assertGreaterEqual(self.window.sync_pressure_calculator_mode_calls, 1)

    def test_applying_a_wavelength_calibration_disables_excitation_spinbox(self):
        self.window.radio_spec_mode_wl.setChecked(False)
        self.window.radio_spec_mode_raman.setChecked(True)
        self.window.spin_exc_wl.enabled = True

        self.window.apply_calibration((669.4, 0.0208, 0.0), "label", calib_unit="Wavelength")

        self.assertFalse(self.window.spin_exc_wl.enabled)


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class ApplyCalibrationRejectsExcitationMismatchTests(unittest.TestCase):
    """Regression test: apply_calibration() -- the deprecated inline API's only
    path into the GUI's calibration state -- used to silently overwrite
    spin_exc_wl with whatever laser wavelength the caller specified. A loaded
    configuration already rejects this mismatch before ever mutating anything
    (_prepare_configuration_for_loading()); the inline API had no equivalent
    check, so a request for a 532 nm Raman calibration while the GUI was set
    to 633 nm would leave the GUI reporting a calibration active at a laser
    that was never actually in use."""

    def setUp(self):
        self.window = Window()
        self.window.spin_exc_wl.setValue(633.0)

    def test_mismatched_excitation_wavelength_is_rejected(self):
        from src.core.configuration_catalog import ConfigurationCompatibilityError

        with self.assertRaises(ConfigurationCompatibilityError):
            self.window.apply_calibration(
                (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=532.0,
            )
        # Rejected before any state mutation -- spin_exc_wl/calib_coeffs untouched.
        self.assertEqual(self.window.spin_exc_wl.value(), 633.0)
        self.assertIsNone(self.window.calib_coeffs)

    def test_matching_excitation_wavelength_is_accepted(self):
        self.window.apply_calibration(
            (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=633.0,
        )
        self.assertIsNotNone(self.window.calib_coeffs)

    def test_excitation_wavelength_within_tolerance_is_accepted(self):
        self.window.apply_calibration(
            (0.0, 1.0, 0.0), "label", calib_unit="Raman shift", calib_laser_wl=633.0001,
        )
        self.assertIsNotNone(self.window.calib_coeffs)

    def test_wavelength_calibration_is_unaffected_by_excitation_state(self):
        self.window.apply_calibration((669.4, 0.0208, 0.0), "label", calib_unit="Wavelength")
        self.assertIsNotNone(self.window.calib_coeffs)


class FakeFuture:
    """Minimal stand-in for the API layer's completion Future."""

    def __init__(self):
        self._done = False
        self.exception = None

    def done(self):
        return self._done

    def set_exception(self, exc):
        self.exception = exc
        self._done = True


class MoveThread:
    def __init__(self, *, cancelled=False, success=True, error_message=None):
        self.cancelled = cancelled
        self.success = success
        self.error_message = error_message


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class SpectrometerMoveFailureInvalidationTests(unittest.TestCase):
    """Regression test: a spectrometer move that fails partway through a
    configuration load used to leave the previous calibration active even
    though _prepare_configuration_for_loading() had already switched the ROI/
    display-mode/excitation widgets to the (now unconfirmed) new configuration
    -- get_x_axis() would then keep reporting the old calibration's values
    mislabeled under whatever unit the display toggle was left showing."""

    def setUp(self):
        self.window = Window()
        self.window.calib_coeffs = (669.4, 0.0208, -1.2e-7)
        self.window.calib_unit = "Wavelength"
        self.window.calib_laser_wl = None
        self.window.axis_source = "loaded_configuration"
        self.window.positioned_configuration_id = "cfg-old"
        self.window.positioned_configuration_slot_id = "slot-old"
        # Non-empty so _refresh_after_axis_change()'s redraw guard fires, and a
        # pressure window so its stale-peak-clearing call can be observed too.
        self.window.raw_1d_data = [1, 2, 3]
        self.window.pressure_window = PressureWindow()
        # Stub out the real-Qt dialog/enable-controls plumbing exercised by
        # on_spectrometer_moved() -- irrelevant to the invalidation behavior
        # under test.
        self.window._close_spectrometer_moving_dialog = lambda: None
        self.window._set_spectrometer_controls_enabled = lambda enabled: None
        self.window._loading_config = True
        self.window._pending_configuration_future = FakeFuture()
        self.window._pending_configuration_id = "cfg-new"
        self.window._pending_configuration_slot_id = "slot-new"

    def test_move_failure_during_configuration_load_invalidates_stale_calibration(self):
        future = self.window._pending_configuration_future
        self.window.spec_move_thread = MoveThread(success=False, error_message="boom")

        self.window.on_spectrometer_moved()

        self.assertIsNone(self.window.calib_coeffs)
        self.assertEqual(self.window.axis_source, "pixel")
        # Physical position is uncertain after a failed move (it may have
        # partially completed), so -- unlike a mere display/excitation
        # mismatch -- positioned_configuration_id must also be cleared rather
        # than preserved.
        self.assertIsNone(self.window.positioned_configuration_id)
        self.assertIsInstance(future.exception, RuntimeError)
        # Regression: clear_active_configuration() used to only update labels/
        # state, leaving the plot and Pressure Window showing stale calibrated
        # values/peaks until some unrelated redraw happened to occur later.
        self.assertEqual(self.window.sync_fit_range_calls, 1)
        self.assertEqual(self.window.update_display_calls, [False])
        self.assertEqual(self.window.pressure_window.fit_peaks_calls, [[]])

    def test_move_cancellation_during_configuration_load_invalidates_stale_calibration(self):
        # spec_ctrl.get_wavelength()/get_grating() are read back on cancellation;
        # stub them out minimally alongside the config-grating lookup.
        class SpecCtrl:
            def get_wavelength(self_inner):
                return 690.0

            def get_grating(self_inner):
                return 1

        self.window.spec_ctrl = SpecCtrl()
        self.window.config = {"grating": [{"index": 1}]}
        self.window.spec_move_thread = MoveThread(cancelled=True)

        self.window.on_spectrometer_moved()

        self.assertIsNone(self.window.calib_coeffs)
        self.assertEqual(self.window.axis_source, "pixel")
        self.assertIsNone(self.window.positioned_configuration_id)
        self.assertEqual(self.window.sync_fit_range_calls, 1)
        self.assertEqual(self.window.update_display_calls, [False])
        self.assertEqual(self.window.pressure_window.fit_peaks_calls, [[]])


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class GetXAxisNoImplicitConversionTests(unittest.TestCase):
    def test_calibrated_axis_is_returned_verbatim_never_converted(self):
        # Regression test: get_x_axis() used to silently convert between nm and
        # cm-1 whenever calib_unit disagreed with the display toggle. That
        # mismatch can no longer occur by construction (on_spec_mode_changed/
        # on_exc_wl_changed invalidate first) -- verify the function itself no
        # longer branches on the display toggle at all.
        window = Window()
        window.calib_coeffs = (669.4, 0.0208, -1.2e-7)
        window.calib_unit = "Wavelength"
        window.radio_spec_mode_raman.setChecked(True)  # deliberately disagrees

        x = window.get_x_axis(3)

        c0, c1, c2 = window.calib_coeffs
        expected = [c0 + c1 * i + c2 * i**2 for i in range(3)]
        self.assertEqual(list(x), expected)


if __name__ == "__main__":
    unittest.main()
