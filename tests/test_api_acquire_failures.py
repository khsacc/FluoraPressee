"""Failure modes an always-listening API server meets in normal operation.

Standby keeps the server up all day, so "the camera never initialised", "the client
went away mid-acquisition" and "the operator switched the server off during a long
exposure" stop being edge cases. Each of them must leave the acquisition gate free -
the UI lock is derived from that gate, so a leaked gate means a permanently locked
GUI (work_API_standby.md Step 3).
"""
import threading
import unittest
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from unittest.mock import Mock, patch

try:
    from fastapi import HTTPException

    from src.api.schemas import AcquireRequest
    from src.api.server import create_app
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    HTTPException = None
    AcquireRequest = None
    create_app = None

try:
    from src.ui.ui_mixins import api_mixin
    from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
    from src.ui.ui_mixins.api_mixin import ApiMixin, CameraNotReadyError
    HAS_QT = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    HAS_QT = False

    class AcquisitionMixin:
        pass

    class ApiMixin:
        pass

    class CameraNotReadyError(Exception):
        pass


class DirectBridge:
    """Runs GUI-thread work inline - the tests below are single-threaded."""

    def call(self, fn, timeout=60):
        return fn()


class FakeTimer:
    def __init__(self):
        self.running = False
        self.started_with = None

    def start(self, interval_ms=None):
        self.running = True
        self.started_with = interval_ms

    def stop(self):
        self.running = False


class FakeThread:
    def __init__(self, running=True):
        self.running = running
        self.is_measuring = False
        self.current_exposure = 0.1
        self.stopped = False

    def isRunning(self):
        return self.running

    def start_measuring(self):
        self.is_measuring = True

    def stop_measuring(self):
        self.is_measuring = False
        self.stopped = True


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


class Harness(ApiMixin, AcquisitionMixin):
    """Enough of SpectrometerGUI to exercise the acquisition failure paths."""

    BUTTON_STYLE_GREEN = ""
    BUTTON_STYLE_RED = ""

    def __init__(self, camera_running=True):
        self.thread = FakeThread(running=camera_running)
        self.gui_bridge = DirectBridge()
        self._acquisition_gate = threading.Lock()
        self._gate_held_by_me = False
        self._gate_owner = None
        self._ui_lock_reasons = set()
        self._api_unlock_timer = FakeTimer()
        self._api_unlock_delay_ms = 1000
        self._api_stop_timer = FakeTimer()
        self._api_server = None
        self._api_server_thread = None
        self._api_accepting = False
        self._api_stopping = False
        self._api_pending_future = None
        self._active_target_accum = None
        self.is_single_shot = False
        self._ignore_next_frames = False
        self.spin_accumulate = FakeWidget(1)
        self.spin_acq_time = FakeWidget(0.1)
        self.take_single_calls = 0
        self.state_changes = 0

    # --- stand-ins for the pieces SequentialMixin/main_window normally provide ---
    def _lock_ui(self, reason):
        self._ui_lock_reasons.add(reason)

    def _unlock_ui(self, reason, reapply_hardware=True):
        self._ui_lock_reasons.discard(reason)

    def _set_button_style(self, *_args):
        pass

    def _on_api_state_changed(self):
        self.state_changes += 1

    def take_single_spectrum(self):
        self.take_single_calls += 1
        self.thread.start_measuring()

    def __getattr__(self, name):
        if name.startswith(("btn_", "lbl_", "spin_", "radio_", "chk_")):
            widget = FakeWidget()
            setattr(self, name, widget)
            return widget
        raise AttributeError(name)


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class CameraNotReadyTests(unittest.TestCase):
    def test_acquire_refuses_without_taking_the_gate(self):
        gui = Harness(camera_running=False)

        with self.assertRaises(CameraNotReadyError):
            gui._api_start_acquire()

        self.assertFalse(gui._acquisition_gate.locked())
        self.assertEqual(gui.take_single_calls, 0)
        self.assertEqual(gui._ui_lock_reasons, set())

    def test_running_camera_still_acquires(self):
        gui = Harness()

        future, exposure, accumulations = gui._api_start_acquire()

        self.assertEqual(gui.take_single_calls, 1)
        self.assertTrue(gui._acquisition_gate.locked())
        self.assertEqual(gui._gate_owner, "api")
        self.assertIn("api_active", gui._ui_lock_reasons)


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class AcquireTimeoutTests(unittest.TestCase):
    def setUp(self):
        # The real margin (15 s of head-room over exposure x accumulations) would make
        # every timeout test take that long.
        self._margin = api_mixin.ACQUIRE_TIMEOUT_MARGIN_S
        api_mixin.ACQUIRE_TIMEOUT_MARGIN_S = 0.0

    def tearDown(self):
        api_mixin.ACQUIRE_TIMEOUT_MARGIN_S = self._margin

    def test_timeout_releases_the_gate_and_stops_the_camera(self):
        gui = Harness()

        with self.assertRaises(FutureTimeoutError):
            gui.api_acquire(timeout=0.01)

        self.assertFalse(gui._acquisition_gate.locked())
        self.assertIsNone(gui._api_pending_future)
        self.assertIsNone(gui._active_target_accum)
        self.assertFalse(gui.is_single_shot)
        # The camera is stopped before the gate is released, so the next request
        # cannot start while the detector is still reading out.
        self.assertTrue(gui.thread.stopped)

    def test_timeout_schedules_the_debounced_unlock(self):
        gui = Harness()

        with self.assertRaises(FutureTimeoutError):
            gui.api_acquire(timeout=0.01)

        self.assertTrue(gui._api_unlock_timer.running)

    def test_abort_is_idempotent_against_a_late_completion(self):
        gui = Harness()
        future, _exposure, _accum = gui._api_start_acquire()

        gui._api_abort_acquire(future)
        # A frame that arrives after the abort resolves nothing and must not throw.
        future.set_result({"raw": [1, 2, 3], "mode": "1d"})
        gui._api_abort_acquire(future)

        self.assertFalse(gui._acquisition_gate.locked())
        self.assertIsNone(gui._api_pending_future)

    def test_stale_abort_does_not_stop_a_newer_acquisition(self):
        gui = Harness()
        old_future, _exposure, _accum = gui._api_start_acquire()

        # Simulate the old request having ended and a new request acquiring the gate
        # before the old worker's timeout cleanup reaches the GUI thread.
        gui._api_pending_future = None
        gui.thread.stop_measuring()
        gui._release_acquisition_gate()
        new_future, _exposure, _accum = gui._api_start_acquire()

        gui._api_abort_acquire(old_future)

        self.assertIs(gui._api_pending_future, new_future)
        self.assertTrue(gui.thread.is_measuring)
        self.assertTrue(gui._acquisition_gate.locked())

    def test_early_stop_completes_the_pending_future_with_an_error(self):
        gui = Harness()
        future, _exposure, _accum = gui._api_start_acquire()

        gui.stop_measurement()

        with self.assertRaisesRegex(RuntimeError, "stopped before"):
            future.result()
        self.assertIsNone(gui._api_pending_future)
        self.assertFalse(gui._acquisition_gate.locked())

    def test_configuration_completion_on_gui_thread_releases_directly(self):
        gui = Harness()
        self.assertTrue(gui._try_acquire_gate("api"))
        gui.gui_bridge.call = Mock(
            side_effect=AssertionError("must not bridge from the GUI thread")
        )
        gui_thread = object()
        app = Mock()
        app.thread.return_value = gui_thread

        with (
            patch.object(api_mixin, "QCoreApplication") as core_application,
            patch.object(api_mixin, "QThread") as qthread,
        ):
            core_application.instance.return_value = app
            qthread.currentThread.return_value = gui_thread
            gui._api_release_gate_after_future()

        self.assertFalse(gui._acquisition_gate.locked())
        gui.gui_bridge.call.assert_not_called()


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class ServerShutdownTests(unittest.TestCase):
    class _LiveThread:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    class _Server:
        def __init__(self):
            self.should_exit = False

    def test_stop_keeps_references_until_the_thread_exits(self):
        gui = Harness()
        gui._api_server = self._Server()
        gui._api_server_thread = self._LiveThread()
        gui._api_accepting = True

        gui.stop_api_server()

        # New requests are refused straight away...
        self.assertFalse(gui._api_accepting)
        self.assertTrue(gui._api_server.should_exit)
        # ...but the in-flight request still has a live server object to finish on.
        self.assertIsNotNone(gui._api_server)
        self.assertTrue(gui._api_stopping)
        self.assertTrue(gui._api_stop_timer.running)

        gui._api_server_thread.alive = False
        gui._check_api_server_stopped()

        self.assertIsNone(gui._api_server)
        self.assertIsNone(gui._api_server_thread)
        self.assertFalse(gui._api_stopping)
        self.assertFalse(gui._api_stop_timer.running)

    def test_stopping_an_idle_server_completes_immediately(self):
        gui = Harness()
        gui._api_server = self._Server()
        gui._api_server_thread = self._LiveThread()
        gui._api_server_thread.alive = False
        gui._lock_ui("api_server")

        gui.stop_api_server()

        self.assertIsNone(gui._api_server)
        self.assertNotIn("api_server", gui._ui_lock_reasons)


class _ShutdownGui:
    _api_key = "test-key"
    _api_accepting = False


@unittest.skipIf(create_app is None, "FastAPI is not installed")
class ShutdownRejectionTests(unittest.TestCase):
    """The refusal has to sit in the route's dependencies, ahead of the key check."""

    def _dependency(self, app, name):
        pending = list(app.routes)
        while pending:
            route = pending.pop(0)
            pending.extend(getattr(route, "routes", []))
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                pending.extend(original_router.routes)
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for dependency in dependant.dependencies:
                if getattr(dependency.call, "__name__", "") == name:
                    return dependency.call
        self.fail(f"Missing route dependency: {name}")

    def test_requests_are_refused_while_shutting_down(self):
        app = create_app(_ShutdownGui(), DirectBridge())
        ensure_accepting = self._dependency(app, "ensure_accepting")

        with self.assertRaises(HTTPException) as caught:
            ensure_accepting()

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["code"], "shutting_down")

    def test_requests_pass_while_the_server_is_accepting(self):
        gui = _ShutdownGui()
        gui._api_accepting = True
        app = create_app(gui, DirectBridge())

        self._dependency(app, "ensure_accepting")()


@unittest.skipIf(create_app is None, "FastAPI is not installed")
class CameraNotReadyResponseTests(unittest.TestCase):
    def _acquire_endpoint(self, app):
        pending = list(app.routes)
        while pending:
            route = pending.pop(0)
            pending.extend(getattr(route, "routes", []))
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                pending.extend(original_router.routes)
            if getattr(route, "path", None) == "/acquire" and "POST" in getattr(route, "methods", set()):
                return route.endpoint
        self.fail("Missing route: POST /acquire")

    def test_camera_not_ready_becomes_503(self):
        class _Gui:
            _api_key = "test-key"
            _api_accepting = True

            def api_acquire(self, **_kwargs):
                raise CameraNotReadyError("The camera is not initialised.")

        endpoint = self._acquire_endpoint(create_app(_Gui(), DirectBridge()))

        with self.assertRaises(HTTPException) as caught:
            endpoint(AcquireRequest())

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["code"], "camera_not_ready")


if __name__ == "__main__":
    unittest.main()
