import tempfile
import unittest
from concurrent.futures import Future

from src.core.configuration_catalog import (
    ConfigurationCatalog,
    ConfigurationCompatibilityError,
)

try:
    from src.ui.ui_mixins.file_io_mixin import FileIOMixin
    HAS_QT = True
except ModuleNotFoundError:
    class FileIOMixin:
        pass

    HAS_QT = False


class Checked:
    def __init__(self, checked=False):
        self.checked = checked

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = checked

    def blockSignals(self, _blocked):
        pass


class Value:
    def __init__(self, value=0):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def blockSignals(self, _blocked):
        pass


class Combo:
    def __init__(self, index=0):
        self.index = index

    def currentIndex(self):
        return self.index

    def setCurrentIndex(self, index):
        self.index = index


class Camera:
    det_width = 1024
    det_height = 127


class Spectrometer:
    def get_cached_hardware_metadata(self):
        return {
            "serial_number": "SPEC-1",
            "grating": {"index": 1, "grooves_per_mm": 600},
            "center_wavelength_nm": 689.9998,
        }


class Window(FileIOMixin):
    def __init__(self, catalog, model="PrincetonInstruments"):
        self.configuration_catalog = catalog
        self.spec_ctrl = Spectrometer()
        self.thread = Camera()
        self.config = {
            "model": model,
            "hardware_identity": {
                "spectrometer": {"model": "SP-2750", "serial_number": "SPEC-1"},
                "camera": {"model": "DU-401", "serial_number": "CAM-1"},
            },
            "grating": [
                {"index": 1, "grooves": 600},
                {"index": 2, "grooves": 1200},
            ],
        }
        self._camera_identity = {"model": "DU-401", "serial_number": "CAM-1"}
        self.combo_grating = Combo(0)
        self.physical_center_wl = 690.0
        self.radio_2d = Checked(False)
        self.radio_1d_full = Checked(False)
        self.radio_1d_roi = Checked(True)
        self.spin_vstart = Value(45)
        self.spin_vend = Value(65)
        self.radio_spec_mode_raman = Checked(False)
        self.radio_spec_mode_wl = Checked(True)
        self.spin_exc_wl = Value(532.0)
        self.spin_centre_wl = Value(690.0)
        self.lbl_centre = Label()
        self.lbl_loaded_configuration = Label()
        self.roi_applied = False
        self.move_started = False
        self.raw_1d_data = None

    def update_plot_labels(self):
        pass

    def sync_fit_range_to_spectrum(self, force=False):
        pass

    def apply_roi_settings(self):
        self.roi_applied = True

    def on_apply_spectrometer(self):
        self.move_started = True

    def _sync_controls_to_display_mode(self):
        # Real SpectrometerControlMixin behavior (recomputed centre value,
        # spin_exc_wl enabled state, Apply-button state, Pressure Window sync)
        # is covered by tests/test_axis_invalidation.py; this stub Window only
        # mixes in FileIOMixin, and this file's tests care about configuration
        # load/register flow, not display-toggle side effects.
        pass


class Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


@unittest.skipUnless(HAS_QT, "PyQt6 is not importable in this environment")
class ConfigurationUiFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.window = Window(ConfigurationCatalog(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_register_uses_physical_grating_center_roi_and_not_acquisition(self):
        # A pending, unapplied combo-box selection must not be saved as the
        # physical grating on which calibration was actually performed.
        self.window.combo_grating.setCurrentIndex(1)
        record = self.window.register_current_configuration((1.0, 2.0, 3.0))

        self.assertEqual(record["spectrometer"]["grating_index"], 1)
        self.assertEqual(record["spectrometer"]["grating_grooves_per_mm"], 600)
        self.assertEqual(record["spectrometer"]["target_center_wavelength_nm"], 690.0)
        self.assertEqual(record["spectrometer"]["actual_center_wavelength_nm"], 689.9998)
        self.assertEqual(record["detector"]["roi_start"], 45)
        self.assertNotIn("exposure_time_s", record)

    def test_register_stores_raw_spectrum_used_for_calibration(self):
        import numpy as np

        record = self.window.register_current_configuration(
            (1.0, 2.0, 3.0), raw_spectrum=np.array([10.0, 20.5, 30.0])
        )

        self.assertEqual(record["calibration"]["raw_spectrum"], [10.0, 20.5, 30.0])

    def test_register_leaves_raw_spectrum_none_when_not_supplied(self):
        record = self.window.register_current_configuration((1.0, 2.0, 3.0))

        self.assertIsNone(record["calibration"]["raw_spectrum"])

    def test_prepare_loads_complete_configuration_and_defers_calibration_until_move(self):
        record = self.window.register_current_configuration((1.0, 2.0, 3.0))
        self.window.combo_grating.setCurrentIndex(1)
        self.window.spin_vstart.setValue(1)
        self.window.spin_vend.setValue(2)

        self.window._prepare_configuration_for_loading(record)

        self.assertEqual(self.window.combo_grating.currentIndex(), 0)
        self.assertEqual(self.window.spin_vstart.value(), 45)
        self.assertEqual(self.window.spin_vend.value(), 65)
        self.assertTrue(self.window.roi_applied)
        self.assertTrue(self.window.move_started)
        self.assertTrue(self.window._loading_config)
        self.assertEqual(self.window._pending_calib_coeffs, (1.0, 2.0, 3.0))
        self.assertEqual(
            self.window._pending_configuration_id, record["configuration_id"]
        )

    def test_api_noop_apply_skips_move_and_can_select_pixel_axis(self):
        record = self.window.register_current_configuration((1.0, 2.0, 3.0))
        calibrated_future = Future()

        self.assertTrue(self.window._configuration_matches_current_state(record))
        self.window._prepare_configuration_for_loading(
            record,
            completion_future=calibrated_future,
            skip_move=True,
        )

        self.assertTrue(calibrated_future.result())
        self.assertFalse(self.window.move_started)
        self.assertEqual(
            self.window.active_configuration_id, record["configuration_id"]
        )

        pixel_future = Future()
        self.window._prepare_configuration_for_loading(
            record,
            axis_mode="pixel",
            completion_future=pixel_future,
            skip_move=True,
        )

        self.assertTrue(pixel_future.result())
        self.assertIsNone(self.window.active_configuration_id)
        self.assertEqual(
            self.window.positioned_configuration_id, record["configuration_id"]
        )
        self.assertEqual(self.window.axis_source, "pixel")

    def test_oceanoptics_load_ignores_saved_center_and_skips_move(self):
        ocean = Window(self.window.configuration_catalog, model="OceanOptics")
        record = ocean.register_current_configuration((1.0, 2.0, 3.0))
        record["spectrometer"]["target_center_wavelength_nm"] = 650.0
        original_center = ocean.physical_center_wl
        original_spin_center = ocean.spin_centre_wl.value()

        ocean._prepare_configuration_for_loading(record, skip_move=True)

        self.assertFalse(ocean.move_started)
        self.assertEqual(ocean.physical_center_wl, original_center)
        self.assertEqual(ocean.spin_centre_wl.value(), original_spin_center)
        self.assertEqual(ocean.active_configuration_id, record["configuration_id"])

    def test_oceanoptics_branch_is_exact_backend_identity(self):
        ocean = Window(self.window.configuration_catalog, model="OceanOptics")
        similarly_fixed = Window(
            self.window.configuration_catalog, model="SomeOtherFixedSpectrometer"
        )

        self.assertTrue(ocean._is_oceanoptics_backend())
        self.assertFalse(similarly_fixed._is_oceanoptics_backend())
        self.assertFalse(self.window._is_oceanoptics_backend())

    def test_movable_backend_still_applies_saved_center_and_starts_move(self):
        record = self.window.register_current_configuration((1.0, 2.0, 3.0))
        record["spectrometer"]["target_center_wavelength_nm"] = 650.0

        self.window._prepare_configuration_for_loading(record)

        self.assertTrue(self.window.move_started)
        self.assertEqual(self.window.spin_centre_wl.value(), 650.0)
        self.assertTrue(self.window._loading_config)

    def test_prepare_rejects_raman_configuration_on_excitation_mismatch(self):
        record = self.window.register_current_configuration(
            (1.0, 2.0, 3.0), calibration_unit="Raman shift", excitation_wavelength_nm=532.0
        )
        self.window.spin_exc_wl.setValue(633.0)
        self.window.combo_grating.setCurrentIndex(0)
        self.window.spin_vstart.setValue(10)
        self.window.spin_vend.setValue(20)

        with self.assertRaises(ConfigurationCompatibilityError):
            self.window._prepare_configuration_for_loading(record)

        # Rejected before any widget is mutated.
        self.assertEqual(self.window.spin_exc_wl.value(), 633.0)
        self.assertEqual(self.window.spin_vstart.value(), 10)
        self.assertEqual(self.window.spin_vend.value(), 20)
        self.assertFalse(self.window.roi_applied)
        self.assertFalse(self.window.move_started)

    def test_prepare_accepts_matching_excitation_wavelength(self):
        record = self.window.register_current_configuration(
            (1.0, 2.0, 3.0), calibration_unit="Raman shift", excitation_wavelength_nm=532.0
        )
        self.window.spin_exc_wl.setValue(532.0001)  # within 0.001 nm tolerance

        self.window._prepare_configuration_for_loading(record)

        self.assertTrue(self.window.move_started)
        self.assertTrue(self.window.radio_spec_mode_raman.isChecked())

    def test_prepare_pixel_axis_mode_ignores_excitation_mismatch_and_display(self):
        record = self.window.register_current_configuration(
            (1.0, 2.0, 3.0), calibration_unit="Raman shift", excitation_wavelength_nm=532.0
        )
        self.window.spin_exc_wl.setValue(633.0)
        future = Future()

        self.window._prepare_configuration_for_loading(
            record, axis_mode="pixel", completion_future=future, skip_move=True
        )

        self.assertTrue(future.result())
        # axis_mode="pixel" must never touch the excitation spinbox or the
        # Wavelength/Raman display toggle -- positioning hardware only must have
        # no side effect on calibration/display state.
        self.assertEqual(self.window.spin_exc_wl.value(), 633.0)
        self.assertFalse(self.window.radio_spec_mode_raman.isChecked())
        self.assertIsNone(self.window.calib_coeffs)
        self.assertEqual(
            self.window.positioned_configuration_id, record["configuration_id"]
        )

    def test_deactivate_axis_calibration_keeps_position_but_clears_calibration(self):
        record = self.window.register_current_configuration(
            (1.0, 2.0, 3.0), calibration_unit="Raman shift", excitation_wavelength_nm=532.0
        )
        self.window._prepare_configuration_for_loading(record, skip_move=True)
        self.assertIsNotNone(self.window.calib_coeffs)

        self.window.deactivate_axis_calibration("test reason")

        self.assertIsNone(self.window.calib_coeffs)
        self.assertIsNone(self.window.active_configuration_id)
        self.assertEqual(self.window.axis_source, "pixel")
        self.assertEqual(
            self.window.positioned_configuration_id, record["configuration_id"]
        )
        self.assertIn("test reason", self.window.lbl_loaded_configuration.text)


if __name__ == "__main__":
    unittest.main()
