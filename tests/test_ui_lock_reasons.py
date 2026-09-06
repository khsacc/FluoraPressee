"""UI lock semantics: idempotency, and the paths that used to break a standing lock.

Standby mode derives "a remote request is running" from the acquisition gate and
applies the same UI lock the API server has always used (work_API_standby.md 方針4).
That derivation is only sound while no code re-enables a locked widget behind the
lock's back, so the regressions below pin the three paths that used to do exactly
that, plus the idempotency the per-request locking depends on.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from src.ui.ui_mixins.acquisition_mixin import AcquisitionMixin
    from src.ui.ui_mixins.sequential_mixin import SequentialMixin
    from src.ui.ui_mixins.spectrometer_control_mixin import SpectrometerControlMixin
    HAS_QT = True
except ModuleNotFoundError:  # pragma: no cover - environments without PyQt6
    HAS_QT = False


class FakeWidget:
    """Minimal stand-in recording enabled/checked state for one control."""

    def __init__(self, name, checked=False, value=0.0, text=""):
        self.name = name
        self.enabled = True
        self.visible = True
        self.checked = checked
        self._value = value
        self._text = text
        self.enable_calls = 0

    def setEnabled(self, enabled):
        self.enable_calls += 1
        self.enabled = bool(enabled)

    def isEnabled(self):
        return self.enabled

    def setVisible(self, visible):
        self.visible = bool(visible)

    def isVisible(self):
        return self.visible

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = bool(checked)

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def currentText(self):
        return self._text

    def blockSignals(self, _blocked):
        pass

    def accept(self):
        pass


class FakeCamera:
    def __init__(self):
        self.roi_pushes = []

    def update_roi_settings(self, mode, start, end):
        self.roi_pushes.append((mode, start, end))


class LockWindow(SequentialMixin, AcquisitionMixin, SpectrometerControlMixin):
    """Just enough of SpectrometerGUI for the lock helpers to run."""

    BUTTON_STYLE_GREEN = ""
    BUTTON_STYLE_RED = ""

    def __init__(self):
        self._ui_lock_reasons = set()
        self._widgets = {}
        self._em_gain_available = True
        self.seq_dir = "/tmp/seq"
        self.physical_grating = "600"
        self.physical_center_wl = 694.0
        self.thread = FakeCamera()
        self._central = FakeWidget("centralWidget")
        self.spec_move_dialog = None
        self.spec_move_cancel_btn = None
        self.fitting_panel_toggles = 0
        # Read modes: plain 1D full-range binning, wavelength display.
        self._widgets["radio_1d_full"] = FakeWidget("radio_1d_full", checked=True)
        self._widgets["combo_grating"] = FakeWidget("combo_grating", text="600")
        self._widgets["spin_centre_wl"] = FakeWidget("spin_centre_wl", value=694.0)
        self._widgets["spin_exc_wl"] = FakeWidget("spin_exc_wl", value=532.0)

    def __getattr__(self, name):
        # Auto-create the widgets set_ui_enabled_during_seq walks, so this stub does
        # not have to list all ~35 of them.
        if name.startswith(("btn_", "spin_", "radio_", "chk_", "combo_", "action_", "lbl_")):
            widgets = self.__dict__.setdefault("_widgets", {})
            return widgets.setdefault(name, FakeWidget(name))
        raise AttributeError(name)

    def widget(self, name):
        return getattr(self, name)

    def centralWidget(self):
        return self._central

    def _update_remote_active_indicator(self):
        # ApiMixin owns the real one; this stub has no API panel.
        pass

    def toggle_fitting_panel(self):
        self.fitting_panel_toggles += 1

    def update_plot_labels(self):
        pass

    def on_fit_settings_changed(self):
        pass

    def sync_pressure_calculator_mode(self):
        pass


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class UiLockIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.window = LockWindow()

    def test_lock_walks_the_widgets_once_for_repeated_locks(self):
        self.window._lock_ui("api_active")
        calls_after_first = self.window.widget("btn_single").enable_calls

        self.window._lock_ui("api_active")
        self.window._lock_ui("api_server")

        self.assertEqual(self.window.widget("btn_single").enable_calls, calls_after_first)
        self.assertFalse(self.window.widget("btn_single").isEnabled())

    def test_unlocking_a_reason_that_was_never_held_does_nothing(self):
        self.window._lock_ui("sequential")
        calls_after_lock = self.window.widget("btn_single").enable_calls

        self.window._unlock_ui("api_active")

        self.assertEqual(self.window._ui_lock_reasons, {"sequential"})
        self.assertEqual(self.window.widget("btn_single").enable_calls, calls_after_lock)
        self.assertFalse(self.window.widget("btn_single").isEnabled())

    def test_controls_return_only_after_the_last_reason_is_released(self):
        self.window._lock_ui("api_server")
        self.window._lock_ui("sequential")

        self.window._unlock_ui("sequential")
        self.assertFalse(self.window.widget("btn_single").isEnabled())

        self.window._unlock_ui("api_server")
        self.assertTrue(self.window.widget("btn_single").isEnabled())

    def test_unlock_can_skip_re_pushing_roi_to_the_camera(self):
        self.window._lock_ui("api_active")
        self.window.thread.roi_pushes.clear()

        self.window._unlock_ui("api_active", reapply_hardware=False)

        self.assertEqual(self.window.thread.roi_pushes, [])
        self.assertTrue(self.window.widget("btn_single").isEnabled())

    def test_unlock_re_pushes_roi_by_default(self):
        self.window._lock_ui("sequential")
        self.window.thread.roi_pushes.clear()

        self.window._unlock_ui("sequential")

        self.assertEqual(len(self.window.thread.roi_pushes), 1)


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class LockBreakingPathTests(unittest.TestCase):
    """Regressions for the three paths listed in work_API_standby.md 方針4."""

    def setUp(self):
        self.window = LockWindow()
        self.window._lock_ui("api_active")

    def test_closing_the_spectrometer_move_dialog_keeps_the_lock(self):
        # 経路1: this runs at the end of every API-triggered configuration apply.
        self.window._close_spectrometer_moving_dialog()

        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertFalse(self.window.widget("btn_single").isEnabled())
        self.assertFalse(self.window.widget("spin_acq_time").isEnabled())

    def test_exposure_set_finished_keeps_the_exposure_box_disabled(self):
        # 経路2: an API request sets the exposure on nearly every acquisition.
        self.window.on_exposure_set_finished()

        self.assertFalse(self.window.widget("spin_acq_time").isEnabled())

    def test_exposure_set_finished_restores_the_box_once_unlocked(self):
        self.window._unlock_ui("api_active")
        self.window.widget("spin_acq_time").setEnabled(False)

        self.window.on_exposure_set_finished()

        self.assertTrue(self.window.widget("spin_acq_time").isEnabled())

    def test_roi_sync_does_not_re_enable_controls_while_locked(self):
        # 経路3: apply_roi_settings() runs from several signal handlers.
        self.window.widget("radio_1d_full").setChecked(False)
        self.window.widget("radio_1d_roi").setChecked(True)

        self.window.apply_roi_settings()

        self.assertFalse(self.window.widget("spin_vstart").isEnabled())
        self.assertFalse(self.window.widget("spin_vend").isEnabled())
        self.assertFalse(self.window.widget("radio_bg_on").isEnabled())

    def test_roi_sync_restores_custom_roi_controls_once_unlocked(self):
        self.window.widget("radio_1d_full").setChecked(False)
        self.window.widget("radio_1d_roi").setChecked(True)
        self.window._unlock_ui("api_active")

        self.window.apply_roi_settings()

        self.assertTrue(self.window.widget("spin_vstart").isEnabled())
        self.assertTrue(self.window.widget("radio_bg_on").isEnabled())

    def test_stopping_a_measurement_does_not_re_enable_start_buttons(self):
        self.window.thread.stop_measuring = lambda: None
        self.window._set_button_style = lambda *_args: None

        self.window.stop_measurement()

        self.assertFalse(self.window.widget("btn_single").isEnabled())
        self.assertFalse(self.window.widget("btn_commence").isEnabled())

    def test_spectrometer_change_check_does_not_re_enable_apply(self):
        self.window.widget("spin_centre_wl").setValue(700.0)

        self.window.check_spectrometer_changes()

        self.assertFalse(self.window.widget("btn_apply_spec").isEnabled())


if __name__ == "__main__":
    unittest.main()
