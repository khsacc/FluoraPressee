"""Regression tests for ApiMixin behaviour specific to Ocean Optics, covering the issues
raised in work/work_OceanOptics.md review round 5:
  - a failed exposure write must block acquisition instead of silently proceeding with the
    stale exposure while reporting the requested (never-applied) value,
  - GET /hardware/camera and /hardware/spectrometer must identify an Ocean Optics backend
    correctly and read its connection state from the right attribute,
  - configuration.unit must not collapse a native-wavelength axis down to "pixel".
"""
import threading
import unittest
from unittest import mock

import numpy as np

from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
from src.ui.ui_mixins.api_mixin import ApiMixin, ExposureApplyError


class _Value:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _Checked:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


def _fake_type(name, module, namespace=None):
    """Build a throwaway class whose __module__ matches a real backend module, so
    ApiMixin._api_camera_backend()/_api_spectrometer_backend() (which dispatch on
    type(...).__module__) see it exactly as they would the real
    CameraThreadOceanOptics/SpectrometerControllerOceanOptics classes, without needing to
    construct the real QThread-based classes."""
    return type(name, (), {"__module__": module, **(namespace or {})})


class _FakeTimer:
    def __init__(self):
        self.started_with = None
        self.stop_count = 0

    def start(self, interval_ms):
        self.started_with = interval_ms

    def stop(self):
        self.stop_count += 1


class _Harness(ApiMixin, AcquisitionMixin):
    def __init__(self, thread=None, spec_ctrl=None):
        self.thread = thread
        self.spec_ctrl = spec_ctrl
        self.config = {}
        self._acquisition_gate = threading.Lock()
        self._gate_held_by_me = False
        self._gate_owner = None
        # SequentialMixin is not mixed in here; stand in for the lock bookkeeping
        # _try_acquire_gate("api")/_release_acquisition_gate() drive (方針4), and for
        # the QTimer that debounces the release (方針5).
        self._ui_lock_reasons = set()
        self._api_unlock_timer = _FakeTimer()
        self._api_unlock_delay_ms = 1000
        self._instrument_state_epoch = "testepoch"
        self._instrument_state_counter = 0
        self.debug = False
        self.spin_accumulate = _Value(1)

    def _lock_ui(self, reason):
        self._ui_lock_reasons.add(reason)

    def _unlock_ui(self, reason, reapply_hardware=True):
        self._ui_lock_reasons.discard(reason)

    def _instrument_status_busy(self):
        return False


class BackendDetectionTests(unittest.TestCase):
    """Regression test for review round 5, point 3: backend detection lumped Ocean Optics
    in with the "everything that isn't Princeton" Andor fallback."""

    def test_camera_backend_is_oceanoptics(self):
        thread_type = _fake_type("FakeCameraThread", "src.hardware.camera_oceanoptics")
        gui = _Harness(thread=thread_type())
        self.assertEqual(gui._api_camera_backend(), "oceanoptics_seabreeze")

    def test_spectrometer_backend_is_oceanoptics(self):
        ctrl_type = _fake_type("FakeSpecCtrl", "src.hardware.spectrometer_oceanoptics")
        gui = _Harness(spec_ctrl=ctrl_type())
        self.assertEqual(gui._api_spectrometer_backend(), "oceanoptics_seabreeze")

    def test_camera_backend_still_defaults_to_andor(self):
        thread_type = _fake_type("FakeCameraThread", "src.hardware.camera_andor")
        gui = _Harness(thread=thread_type())
        self.assertEqual(gui._api_camera_backend(), "andor_sdk2")


class HardwareConnectedDetectionTests(unittest.TestCase):
    """Regression test for review round 5, point 3: hardware_connected read
    thread.cam (Andor/Princeton-only attribute) and spec_ctrl.is_initialized (always True
    for the Ocean Optics no-op controller), so a disconnected Ocean Optics device was
    reported as camera_connected=False / spectrometer hardware_connected=True."""

    def _thread(self, module, *, running, spec):
        thread_type = _fake_type(
            "FakeCameraThread", module,
            {"isRunning": lambda self: running, "debug": False, "spec": spec},
        )
        return thread_type()

    @mock.patch("src.ui.ui_mixins.api_mixin.capture_hardware_state")
    def test_camera_reports_connected_via_spec_attribute(self, mock_capture):
        mock_capture.return_value = {"camera": {}, "spectrometer": {}}
        connected_thread = self._thread("src.hardware.camera_oceanoptics", running=True, spec=object())
        gui = _Harness(thread=connected_thread)

        info = gui._api_build_camera_info()

        self.assertTrue(info["hardware_connected"])

    @mock.patch("src.ui.ui_mixins.api_mixin.capture_hardware_state")
    def test_camera_not_running_is_not_connected(self, mock_capture):
        mock_capture.return_value = {"camera": {}, "spectrometer": {}}
        disconnected_thread = self._thread("src.hardware.camera_oceanoptics", running=False, spec=None)
        gui = _Harness(thread=disconnected_thread)

        info = gui._api_build_camera_info()

        self.assertFalse(info["hardware_connected"])

    @mock.patch("src.ui.ui_mixins.api_mixin.capture_hardware_state")
    def test_spectrometer_ignores_always_true_is_initialized_and_uses_camera_thread(
        self, mock_capture
    ):
        mock_capture.return_value = {"camera": {}, "spectrometer": {}}
        # is_initialized=True (SpectrometerControllerOceanOptics is always "initialized"),
        # but the shared physical device (owned by the camera thread) is not connected.
        ctrl_type = _fake_type(
            "FakeSpecCtrl", "src.hardware.spectrometer_oceanoptics",
            {"debug": False, "is_initialized": True},
        )
        disconnected_thread = self._thread("src.hardware.camera_oceanoptics", running=True, spec=None)
        gui = _Harness(thread=disconnected_thread, spec_ctrl=ctrl_type())

        info = gui._api_build_spectrometer_info()

        self.assertFalse(info["hardware_connected"])

    @mock.patch("src.ui.ui_mixins.api_mixin.capture_hardware_state")
    def test_spectrometer_reports_connected_when_camera_thread_holds_a_device(
        self, mock_capture
    ):
        mock_capture.return_value = {"camera": {}, "spectrometer": {}}
        ctrl_type = _fake_type(
            "FakeSpecCtrl", "src.hardware.spectrometer_oceanoptics",
            {"debug": False, "is_initialized": True},
        )
        connected_thread = self._thread("src.hardware.camera_oceanoptics", running=True, spec=object())
        gui = _Harness(thread=connected_thread, spec_ctrl=ctrl_type())

        info = gui._api_build_spectrometer_info()

        self.assertTrue(info["hardware_connected"])


class _ExposureFailingThread:
    """Mimics CameraThreadOceanOptics after a rejected exposure write: current_exposure
    stays at the old value, and get_exposure_error(seq) reports the failure for that seq."""

    def __init__(self):
        self.current_exposure = 0.1
        self._seq = 0
        self.error_message = (
            "Requested exposure 12.0 s is outside this Ocean Optics device's supported "
            "range (0.000001-10 s)."
        )

    def update_exposure(self, exp_time):
        self._seq += 1
        return self._seq

    def wait_for_exposure_applied(self, seq, timeout=None):
        return True  # a failed request still resolves the wait, per its contract

    def get_exposure_error(self, seq):
        return self.error_message if seq == self._seq else None

    def isRunning(self):
        return True


class _ExposureSucceedingThread(_ExposureFailingThread):
    def get_exposure_error(self, seq):
        return None


class _AcquireHarness(_Harness):
    def __init__(self, thread):
        super().__init__(thread=thread)
        self.take_single_spectrum_called = False

    def take_single_spectrum(self):
        self.take_single_spectrum_called = True


class ExposureFailureBlocksAcquisitionTests(unittest.TestCase):
    """Regression test for review round 5, point 1 (P0): a rejected exposure write must
    raise instead of silently proceeding to acquire with the stale exposure while telling
    the caller its requested value was applied."""

    def test_exposure_error_prevents_acquisition_and_releases_the_gate(self):
        thread = _ExposureFailingThread()
        gui = _AcquireHarness(thread)

        with self.assertRaises(ExposureApplyError):
            gui._api_start_acquire(exposure_s=12.0)

        self.assertFalse(gui.take_single_spectrum_called)
        self.assertFalse(gui._acquisition_gate.locked())

    def test_successful_exposure_reports_the_actually_applied_value(self):
        thread = _ExposureSucceedingThread()
        thread.current_exposure = 0.1
        # Simulate run() having applied the new exposure by the time wait_for_exposure_applied
        # returns (SDK rounding may mean this differs slightly from the request - the point is
        # that the response must reflect this, not the raw requested value).
        original_update = thread.update_exposure

        def update_and_apply(exp_time):
            seq = original_update(exp_time)
            thread.current_exposure = exp_time
            return seq

        thread.update_exposure = update_and_apply
        gui = _AcquireHarness(thread)

        future, actual_exposure, actual_accum = gui._api_start_acquire(exposure_s=0.5)

        self.assertTrue(gui.take_single_spectrum_called)
        self.assertEqual(actual_exposure, 0.5)

    def test_backends_without_get_exposure_error_are_unaffected(self):
        class _PlainThread:
            current_exposure = 0.1

            def isRunning(self):
                return True

            def update_exposure(self, exp_time):
                return 1

            def wait_for_exposure_applied(self, seq, timeout=None):
                return True

        gui = _AcquireHarness(_PlainThread())

        future, actual_exposure, actual_accum = gui._api_start_acquire(exposure_s=0.3)

        self.assertTrue(gui.take_single_spectrum_called)


class ConfigurationUnitLabelTests(unittest.TestCase):
    """Regression test for review round 5, point 2: configuration.unit hardcoded "pixel"
    whenever no FluoRaPressée calibration was loaded, even when axis_mode was already
    "native_wavelength" (a real Wavelength/Raman-shift axis, just not FluoRaPressée-
    calibrated) - the two fields contradicted each other in the very same response."""

    def _gui(self, *, native_wavelengths, calib_coeffs, raman):
        gui = _Harness()
        gui.thread = type("Thread", (), {"native_wavelengths": native_wavelengths})()
        gui.calib_coeffs = calib_coeffs
        gui.calib_unit = "Wavelength"
        gui.radio_spec_mode_raman = _Checked(raman)
        gui.physical_center_wl = 700.0
        gui.configuration_hardware_context = lambda: {"actual_center_wavelength_nm": 700.0}
        gui._current_grating_definition = lambda hardware: {"index": 1, "grooves_per_mm": 0}
        gui._current_roi_definition = lambda: {
            "roi_mode": "1d_full", "roi_start": 0, "roi_end": 1,
        }
        return gui

    def test_native_wavelength_unit_is_wavelength_not_pixel(self):
        gui = self._gui(
            native_wavelengths=np.linspace(350.0, 1050.0, 128), calib_coeffs=None, raman=False
        )
        state = gui._api_configuration_state()
        self.assertEqual(state["configuration"]["axis_mode"], "native_wavelength")
        self.assertEqual(state["configuration"]["unit"], "Wavelength")

    def test_native_wavelength_unit_is_raman_shift_in_raman_mode(self):
        gui = self._gui(
            native_wavelengths=np.linspace(350.0, 1050.0, 128), calib_coeffs=None, raman=True
        )
        state = gui._api_configuration_state()
        self.assertEqual(state["configuration"]["unit"], "Raman shift")

    def test_pixel_axis_still_reports_pixel(self):
        gui = self._gui(native_wavelengths=None, calib_coeffs=None, raman=False)
        state = gui._api_configuration_state()
        self.assertEqual(state["configuration"]["axis_mode"], "pixel")
        self.assertEqual(state["configuration"]["unit"], "pixel")

    def test_calibrated_axis_still_uses_calib_unit(self):
        gui = self._gui(native_wavelengths=None, calib_coeffs=(690.0, 0.1, 0.0), raman=False)
        state = gui._api_configuration_state()
        self.assertEqual(state["configuration"]["axis_mode"], "calibrated")
        self.assertEqual(state["configuration"]["unit"], "Wavelength")


if __name__ == "__main__":
    unittest.main()
