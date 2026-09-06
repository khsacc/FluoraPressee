"""Instrument Status must release its operation gate on startup failures."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.ui.menu.instrument_status_dialog import InstrumentStatusDialog


class FakeSignal:
    def connect(self, _slot):
        pass


class FailingCamera:
    status_ready = FakeSignal()

    def request_status(self):
        raise RuntimeError("camera query could not start")


class Controller:
    pass


class InstrumentStatusGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_both_startup_failures_release_the_gate(self):
        state = {"held": False, "releases": 0}

        def acquire():
            state["held"] = True
            return True

        def release():
            state["held"] = False
            state["releases"] += 1

        dialog = InstrumentStatusDialog(
            FailingCamera(),
            Controller(),
            gate_acquire=acquire,
            gate_release=release,
        )
        with patch(
            "src.ui.menu.instrument_status_dialog.SpectrographStatusWorker",
            side_effect=RuntimeError("worker could not start"),
        ):
            dialog.refresh()

        self.assertFalse(state["held"])
        self.assertEqual(state["releases"], 1)
        self.assertIsNotNone(dialog._report)


if __name__ == "__main__":
    unittest.main()
