"""The opaque instrument_state_token: generation, comparison point and responses.

The token lets a client say "acquire, but only if nothing changed since the state I
inspected" without having to carry a configuration_id (work_API_standby.md 方針8).
Two properties matter most and are pinned here: the comparison happens once, right
after the gate is taken and before anything is changed - so combining it with a
configuration_id means "apply this, unless someone else got in first" rather than
always mismatching - and a rejected token never leaks the gate.
"""
import threading
import unittest

import numpy as np

try:
    from fastapi import HTTPException

    from src.api.schemas import AcquireRequest
    from src.api.server import create_app
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    HTTPException = None
    AcquireRequest = None
    create_app = None

try:
    from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
    from src.ui.ui_mixins.api_mixin import ApiMixin, StateTokenMismatchError
    HAS_QT = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    HAS_QT = False

    class AcquisitionMixin:
        pass

    class ApiMixin:
        pass

    class StateTokenMismatchError(Exception):
        pass


class DirectBridge:
    def call(self, fn, timeout=60):
        return fn()


class FakeTimer:
    def __init__(self):
        self.running = False

    def start(self, interval_ms=None):
        self.running = True

    def stop(self):
        self.running = False


class FakeWidget:
    def __init__(self, value=0):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def setEnabled(self, _enabled):
        pass

    def setVisible(self, _visible):
        pass

    def setText(self, _text):
        pass

    def isChecked(self):
        return False


class FakeThread:
    is_measuring = False
    current_exposure = 0.1

    def isRunning(self):
        return True

    def start_measuring(self):
        pass

    def stop_measuring(self):
        pass

    def update_exposure(self, exposure):
        self.current_exposure = float(exposure)
        return 1

    def wait_for_exposure_applied(self, _seq, timeout=None):
        return True


class Harness(ApiMixin, AcquisitionMixin):
    def __init__(self):
        self.thread = FakeThread()
        self.gui_bridge = DirectBridge()
        self._acquisition_gate = threading.Lock()
        self._gate_held_by_me = False
        self._gate_owner = None
        self._ui_lock_reasons = set()
        self._api_unlock_timer = FakeTimer()
        self._api_unlock_delay_ms = 1000
        self._api_pending_future = None
        self._api_pending_context = None
        self._active_target_accum = None
        self.is_single_shot = False
        self._instrument_state_epoch = "epoch123"
        self._instrument_state_counter = 0
        self.spin_accumulate = FakeWidget(1)
        self.spin_acq_time = FakeWidget(0.1)
        self.applied_configurations = []

    def _lock_ui(self, reason):
        self._ui_lock_reasons.add(reason)

    def _unlock_ui(self, reason, reapply_hardware=True):
        self._ui_lock_reasons.discard(reason)

    def take_single_spectrum(self):
        pass

    def _instrument_status_busy(self):
        return False

    def _prepare_configuration_for_loading(self, record, **kwargs):
        self.applied_configurations.append(record["configuration_id"])
        # A real apply changes the instrument, and so the token.
        self._bump_instrument_state()
        future = kwargs.get("completion_future")
        if future is not None:
            future.set_result(True)

    def _is_oceanoptics_backend(self):
        return False

    def _configuration_matches_current_state(self, _record):
        return True

    def _clear_pending_configuration(self):
        pass


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class TokenGenerationTests(unittest.TestCase):
    def test_token_changes_when_the_state_changes(self):
        gui = Harness()
        before = gui.instrument_state_token

        gui._bump_instrument_state()

        self.assertNotEqual(gui.instrument_state_token, before)

    def test_token_is_stable_without_a_change(self):
        gui = Harness()

        self.assertEqual(gui.instrument_state_token, gui.instrument_state_token)

    def test_the_epoch_separates_two_runs_with_the_same_counter(self):
        first, second = Harness(), Harness()
        second._instrument_state_epoch = "epoch456"

        # Same counter, different launch: the tokens must not collide (the ABA
        # problem a bare integer would have).
        self.assertEqual(first._instrument_state_counter, second._instrument_state_counter)
        self.assertNotEqual(first.instrument_state_token, second.instrument_state_token)


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class TokenComparisonTests(unittest.TestCase):
    def test_a_matching_token_is_accepted(self):
        gui = Harness()

        gui._api_start_acquire(expected_state_token=gui.instrument_state_token)

        self.assertTrue(gui._acquisition_gate.locked())

    def test_omitting_the_token_still_works(self):
        gui = Harness()

        gui._api_start_acquire()

        self.assertTrue(gui._acquisition_gate.locked())

    def test_a_stale_token_is_rejected_without_leaking_the_gate(self):
        gui = Harness()

        with self.assertRaises(StateTokenMismatchError) as caught:
            gui._api_start_acquire(expected_state_token="epoch123:99")

        self.assertEqual(caught.exception.expected, "epoch123:99")
        self.assertEqual(caught.exception.current, gui.instrument_state_token)
        self.assertFalse(gui._acquisition_gate.locked())
        # The lock itself is dropped by the debounce timer, not synchronously, so what
        # matters here is that the release path ran at all (see 方針5).
        self.assertTrue(gui._api_unlock_timer.running)

    def test_a_configuration_id_and_token_can_be_combined(self):
        gui = Harness()
        token = gui.instrument_state_token

        # The apply itself bumps the token, so comparing anywhere later than "gate
        # taken, nothing changed yet" would always fail.
        gui._api_wait_for_configuration(
            {"configuration_id": "cfg-1"}, "calibrated", expected_state_token=token
        )

        self.assertEqual(gui.applied_configurations, ["cfg-1"])

    def test_combining_them_still_rejects_a_stale_token(self):
        gui = Harness()

        with self.assertRaises(StateTokenMismatchError):
            gui._api_wait_for_configuration(
                {"configuration_id": "cfg-1"}, "calibrated",
                expected_state_token="epoch123:99",
            )

        self.assertEqual(gui.applied_configurations, [])
        self.assertFalse(gui._acquisition_gate.locked())

    def test_the_acquire_after_an_apply_does_not_recheck_the_stale_token(self):
        gui = Harness()
        token = gui.instrument_state_token
        gui._api_wait_for_configuration(
            {"configuration_id": "cfg-1"}, "calibrated", expected_state_token=token
        )

        # gate_already_held: the apply already compared, and its own change must not
        # now be read as "someone else interfered".
        gui._api_start_acquire(
            gate_already_held=True, expected_state_token=token
        )

        self.assertTrue(gui._acquisition_gate.locked())

    def test_api_exposure_change_advances_the_response_state(self):
        gui = Harness()
        token = gui.instrument_state_token

        gui._api_start_acquire(
            exposure_s=0.2, expected_state_token=token
        )

        self.assertNotEqual(gui.instrument_state_token, token)


@unittest.skipIf(create_app is None, "FastAPI is not installed")
class TokenResponseTests(unittest.TestCase):
    def _endpoint(self, app, path, method):
        pending = list(app.routes)
        while pending:
            route = pending.pop(0)
            pending.extend(getattr(route, "routes", []))
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                pending.extend(original_router.routes)
            if (
                getattr(route, "path", None) == path
                and method in getattr(route, "methods", set())
            ):
                return route.endpoint
        self.fail(f"Missing route: {method} {path}")

    class _Gui:
        _api_key = "test-key"
        _api_accepting = True

        def __init__(self, raiser=None):
            self.raiser = raiser
            self.received = None

        def api_acquire(self, **kwargs):
            self.received = kwargs
            if self.raiser is not None:
                raise self.raiser
            return {
                "x": None,
                "y_raw": np.asarray([1.0]),
                "y": np.asarray([1.0]),
                "mode": "1d",
                "exposure_time_s": 0.1,
                "accumulations": 1,
                "detector_temperature_c": None,
                "timestamp": "2026-09-05T00:00:00",
                "configuration": {},
                "hardware_state": {},
                "x_axis": {"source": "pixel", "unit": None, "calibrated": False},
                "instrument_state_token": "epoch123:4",
            }

    def test_acquire_response_carries_the_token(self):
        gui = self._Gui()
        endpoint = self._endpoint(create_app(gui, DirectBridge()), "/acquire", "POST")

        payload = endpoint(AcquireRequest())

        # _acquire_response_payload() lists its fields by hand, so this would silently
        # go missing if only the schema were updated.
        self.assertEqual(payload["instrument_state_token"], "epoch123:4")

    def test_expected_token_is_forwarded_to_the_gui(self):
        gui = self._Gui()
        endpoint = self._endpoint(create_app(gui, DirectBridge()), "/acquire", "POST")

        endpoint(AcquireRequest(expected_state_token="epoch123:1"))

        self.assertEqual(gui.received["expected_state_token"], "epoch123:1")

    def test_a_mismatch_becomes_409_with_the_current_token(self):
        gui = self._Gui(raiser=StateTokenMismatchError("epoch123:1", "epoch123:5"))
        endpoint = self._endpoint(create_app(gui, DirectBridge()), "/acquire", "POST")

        with self.assertRaises(HTTPException) as caught:
            endpoint(AcquireRequest(expected_state_token="epoch123:1"))

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "state_token_mismatch")
        self.assertEqual(
            caught.exception.detail["instrument_state_token"], "epoch123:5"
        )


if __name__ == "__main__":
    unittest.main()
