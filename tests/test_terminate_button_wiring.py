"""Regression test for a PyQt signal-forwarding trap on the Terminate button.

QPushButton.clicked emits clicked(bool checked=False). PyQt inspects a connected
Python slot's signature and forwards that bool into the slot's first parameter if it
has one - including an optional one with a default value. stop_measurement() gained an
optional `release_gate=True` parameter (work_API_standby.md Step 0), so connecting it
directly (`btn_terminate.clicked.connect(self.stop_measurement)`) silently turned every
manual Terminate click into `stop_measurement(False)`: release_gate=False skips
`_release_acquisition_gate()` and `_is_acquiring_bg = False`, leaving the acquisition
gate (and the UI lock derived from it) held forever after the very first Terminate
click, until the app is restarted.

The fix wraps the connection in a lambda so the emitted bool is discarded. This test
pins both the mechanism (via a minimal, deterministic PyQt reproduction that doesn't
need to boot the full SpectrometerGUI/camera stack) and the actual wiring line in
main_window.py, so a future edit that "simplifies" the connection back to a bare
method reference is caught immediately rather than only showing up as a GUI that
mysteriously stays locked after the first Terminate press.
"""
import inspect
import os
import textwrap
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QPushButton
    HAS_QT = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class ClickedSignalForwardingMechanismTests(unittest.TestCase):
    """Documents *why* the wrapping lambda is required at all."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_connecting_a_defaulted_slot_directly_leaks_the_checked_bool(self):
        received = []

        def slot(release_gate=True):
            received.append(release_gate)

        button = QPushButton("test")
        button.clicked.connect(slot)
        button.click()

        # This is the trap: clicked(bool) forwards straight into release_gate.
        self.assertEqual(received, [False])

    def test_wrapping_in_a_lambda_discards_the_checked_bool(self):
        received = []

        def slot(release_gate=True):
            received.append(release_gate)

        button = QPushButton("test")
        button.clicked.connect(lambda: slot())
        button.click()

        self.assertEqual(received, [True])


class TerminateButtonWiringSourceTests(unittest.TestCase):
    """Pins the actual main_window.py wiring against the same regression."""

    def test_terminate_button_does_not_connect_stop_measurement_directly(self):
        import src.ui.main_window as main_window_module

        source = inspect.getsource(main_window_module.SpectrometerGUI.__init__)
        self.assertNotIn(
            "btn_terminate.clicked.connect(self.stop_measurement)",
            textwrap.dedent(source),
            "btn_terminate must not connect directly to stop_measurement: "
            "QPushButton.clicked emits clicked(bool), which PyQt forwards into "
            "stop_measurement's release_gate parameter, turning every Terminate "
            "click into stop_measurement(release_gate=False) and leaking the "
            "acquisition gate. Wrap it: lambda: self.stop_measurement()",
        )
        self.assertIn("btn_terminate.clicked.connect(lambda", source)


if __name__ == "__main__":
    unittest.main()
