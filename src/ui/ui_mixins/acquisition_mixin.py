import math
from contextlib import contextmanager

import numpy as np
from PyQt6.QtWidgets import QMessageBox

from src.hardware.accumulation import AccumulationCombiner
from src.core.measurement_metadata import capture_hardware_state

# Cap on how many raw frames we buffer in memory for spike rejection (list of
# individual frames instead of an O(1) running sum). ~16MB worst case for a
# 1024-pixel 1D spectrum. Accumulation counts above this fall back to the
# legacy plain-sum path (no rejection) rather than risking unbounded memory,
# since spin_accumulate allows values up to 99999.
MAX_BUFFERED_FRAMES = 2000

# Detector temperature polling/stabilisation (used only for the "unsupported" status -
# i.e. hardware with no Locked/Unlocked/Faulted enum, see on_temperature_read()).
_TEMP_STABLE_WINDOW = 6          # number of recent readings considered
_TEMP_STABLE_SPREAD_C = 0.3      # max peak-to-peak spread over that window, in °C
_TEMP_TARGET_TOLERANCE_C = 1.0   # max distance from the accepted set point, in °C
_TEMP_POLL_INTERVAL_MS = 5000


class GateBusyError(RuntimeError):
    """排他ゲートを取得できなかったときに送出される。

    src/api/server.py はこれを 409 に変換し、`detail` をそのまま応答に載せる。
    Standbyでは409の頻度が上がるため、リモート側が「ローカル操作中なのか、別のAPI
    リクエストと衝突したのか」を判別できる必要がある（方針5、_gate_busy_reason()）。
    """

    def __init__(self, detail):
        super().__init__(detail.get("message", "The instrument is busy."))
        self.detail = detail


class AcquisitionMixin:
    def _try_acquire_gate(self, owner="gui") -> bool:
        """測定権の排他ゲートを非ブロッキングで取得する。既に誰かが握っていれば False を返す。

        owner は "gui"（操作者の手動操作）か "api"（APIリクエスト由来）。owner=="api" の間だけ
        UIロック理由 "api_active" が立つ。**必ずGUIスレッドから呼ぶこと**（方針4の不変条件） —
        `_gate_owner` の読み書きにロックを追加していないのはこの前提に依る。新たにワーカー
        スレッドから直接ゲートを取る経路を追加してはならない。
        """
        if self._acquisition_gate.acquire(blocking=False):
            self._gate_held_by_me = True
            self._gate_owner = owner
            if owner == "api":
                # Cancels a pending release from the previous request, so a burst of
                # requests never flickers the UI between them (方針5).
                self._api_unlock_timer.stop()
                self._lock_ui("api_active")
            return True
        return False

    def _release_acquisition_gate(self) -> None:
        """自分が取得したゲートを解放する（保持していなければ何もしない、二重解放を防止）。

        ゲートの解放をUIロック解除より先に行う: 逆順にすると、UI再有効化のシグナル処理中に
        操作者がボタンを押せてしまい、まだ解放されていないゲートに対して "Busy" が出る。
        """
        if getattr(self, '_gate_held_by_me', False):
            self._gate_held_by_me = False
            owner = getattr(self, '_gate_owner', None)
            self._gate_owner = None
            self._acquisition_gate.release()
            if owner == "api":
                self._end_api_gate_ownership()

    def _end_api_gate_ownership(self):
        """API所有のゲートが外れたときのUIロック解除（1秒デバウンス、方針5）。

        Timed from the gate release, never from the request's arrival: an accumulating
        exposure can run far longer than the delay, and a timer started on arrival
        would expire in the middle of it.

        A closed-loop client's inter-request gap is just its own response handling plus
        the network round trip - it does not grow with exposure time - so the timer
        simply never fires during a burst, and the UI stops flickering at ~4 Hz.
        """
        self._api_unlock_timer.start(self._api_unlock_delay_ms)

    def _on_api_unlock_timeout(self):
        # The timer firing and a new request taking the gate can race, so re-check
        # ownership at fire time rather than trusting the schedule.
        if self._gate_owner == "api":
            return
        self._unlock_ui("api_active", reapply_hardware=False)
        self._restore_api_lock_focus()

    @property
    def instrument_state_token(self):
        """Opaque token identifying the current instrument/axis state.

        Clients must treat it as an opaque string and only ever compare it for
        equality: the format is not part of the API. Passing the token you last saw
        back as `expected_state_token` turns "acquire with the setup I checked" into
        something the server can verify, without forcing every caller to send a
        configuration_id (work_API_standby.md 方針8).
        """
        return f"{self._instrument_state_epoch}:{self._instrument_state_counter}"

    def _bump_instrument_state(self):
        """Mark the instrument state as changed.

        Only monotonicity matters, so bumping more than once for a single logical
        change is harmless. API requests compare their expected token immediately
        after taking the gate, then bump after any physical change; consequently the
        response carries the post-operation token without rejecting the request's own
        pre-operation token.
        """
        # getattr so the mixins stay usable from the lightweight test stubs that do
        # not build the full SpectrometerGUI state.
        self._instrument_state_counter = getattr(self, "_instrument_state_counter", 0) + 1

    def _ui_is_locked(self):
        """True while any UI lock reason stands (sequential run, API server, in-flight
        API request). Tolerates mixin stubs that never set up _ui_lock_reasons."""
        return bool(getattr(self, "_ui_lock_reasons", None))

    def _gate_busy_reason(self):
        """ゲートを取得できなかった理由を機械可読な形で返す（work_API_standby.md 方針2/方針5）。"""
        if getattr(self, "_gate_owner", None) == "api":
            reason = "another_api_request"
            message = "Another API request is currently using the instrument."
        elif getattr(self, "is_sequential_running", False):
            reason = "local_sequential_run"
            message = "A sequential measurement is running on the instrument."
        else:
            reason = "local_operator_action"
            message = "The instrument is busy with a local operation."
        return {"code": "busy", "reason": reason, "message": message}

    @contextmanager
    def acquisition_gate(self, owner="gui"):
        """同期スコープの排他ゲート。例外時も finally で必ず解放する。

        取得や分光器移動のように「開始と完了が別のイベントループターンに分かれる」操作には
        使えない（work_API_standby.md 方針3）。それらは従来通り明示的な取得/解放を使うこと。
        """
        if not self._try_acquire_gate(owner):
            raise GateBusyError(self._gate_busy_reason())
        try:
            yield
        finally:
            self._release_acquisition_gate()

    def take_single_spectrum(self):
        self.is_single_shot = True
        self._ignore_next_frames = False
        self.current_accum_count = 0
        self.btn_single.setEnabled(False)
        self.btn_commence.setEnabled(False)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_terminate.setEnabled(True)
        self._set_button_style(self.btn_terminate, self.BUTTON_STYLE_RED)
        self.thread.start_measuring()

    def get_x_axis(self, num_pixels):
        x = np.arange(num_pixels)
        if self.calib_coeffs is not None:
            # calib_unit always matches the current Wavelength/Raman display by
            # construction: the display toggle and excitation-wavelength edits
            # invalidate the calibration the moment either would disagree with it
            # (deactivate_axis_calibration(), spectrometer_control_mixin.py), and a
            # configuration load rejects a laser mismatch before ever reaching this
            # state (_prepare_configuration_for_loading(), file_io_mixin.py). So there
            # is never a unit to convert here -- doing so implicitly used to paper
            # over exactly the mismatches those two now prevent.
            c0, c1, c2 = self.calib_coeffs
            return c0 + c1 * x + c2 * x**2

        native_wavelengths = getattr(self.thread, "native_wavelengths", None)
        if native_wavelengths is not None and len(native_wavelengths) == num_pixels:
            # Ocean Optics: no FluoRaPressée calibration loaded, but the device reports its
            # own factory-calibrated wavelength axis (nm) - use it instead of a bare pixel
            # index. See public_axis_kind() in measurement_metadata.py for the flip_x/display
            # implications of this being a real physical axis rather than an arbitrary index.
            if not self.radio_spec_mode_raman.isChecked():
                return native_wavelengths
            laser_wl = self.spin_exc_wl.value()
            if laser_wl > 0:
                with np.errstate(divide='ignore', invalid='ignore'):
                    rs = 1e7 / laser_wl - 1e7 / native_wavelengths
                return np.where(np.isfinite(rs), rs, np.nan)
            return native_wavelengths

        return x

    def on_exposure_changed(self):
        val = self.spin_acq_time.value()
        self.spin_acq_time.setEnabled(False)
        self._bump_instrument_state()
        self.thread.update_exposure(val)

    def on_accumulations_changed(self):
        self._bump_instrument_state()

    def on_spike_threshold_changed(self):
        self._bump_instrument_state()

    def on_exposure_set_finished(self):
        """Re-enable the exposure box once the camera thread has applied the value.

        An API request sets the exposure on nearly every acquisition, so in Standby
        mode this fires several times a second while the UI lock is held - hence the
        lock check instead of the unconditional setEnabled(True) this replaced
        (work_API_standby.md 方針4 経路2).
        """
        self.spin_acq_time.setEnabled(not self._ui_is_locked())

    def on_exposure_applied(self, actual_exposure, success, error):
        """Reflect the exposure accepted by Andor, including SDK rounding."""
        self.spin_acq_time.blockSignals(True)
        self.spin_acq_time.setValue(actual_exposure)
        self.spin_acq_time.blockSignals(False)
        if success:
            self.status_label.setText(f"Exposure set to {actual_exposure:g} s")

    def on_roi_applied(self, applied):
        """Cache and display the ROI/read mode actually accepted by Andor."""
        self._last_applied_camera_roi = dict(applied)
        if applied.get("mode") == "1d_roi":
            self.spin_vstart.blockSignals(True)
            self.spin_vend.blockSignals(True)
            self.spin_vstart.setValue(applied["vertical_start"])
            self.spin_vend.setValue(applied["vertical_end"])
            self.spin_vstart.blockSignals(False)
            self.spin_vend.blockSignals(False)

    def on_em_gain_info_ready(self, exists, available, minimum, maximum, increment, current):
        """Render the EM Gain row only when PICam says the parameter exists."""
        self.label_em_gain.setVisible(exists)
        self.spin_em_gain.setVisible(exists)
        self._em_gain_available = bool(exists and available)
        if not exists:
            self.spin_em_gain.setEnabled(False)
            return

        self.spin_em_gain.blockSignals(True)
        if available:
            self.spin_em_gain.setRange(minimum, maximum)
            self.spin_em_gain.setSingleStep(max(1, increment))
            self.spin_em_gain.setValue(current)
            self.spin_em_gain.setToolTip(
                "Electron-multiplication gain reported by the connected camera.\n"
                "Changing this value selects the Electron Multiplied ADC quality."
            )
        else:
            self.spin_em_gain.setToolTip(
                "The camera has an EM Gain parameter, but it is not available "
                "with the current camera/readout settings."
            )
        self.spin_em_gain.blockSignals(False)
        self.spin_em_gain.setEnabled(
            self._em_gain_available and not self._ui_is_locked()
        )

    def on_em_gain_changed(self):
        if not self._em_gain_available:
            return
        self.spin_em_gain.setEnabled(False)
        self._bump_instrument_state()
        self.thread.update_em_gain(self.spin_em_gain.value())

    def on_em_gain_set_finished(self, actual_gain):
        self.spin_em_gain.blockSignals(True)
        self.spin_em_gain.setValue(actual_gain)
        self.spin_em_gain.blockSignals(False)
        self.spin_em_gain.setEnabled(
            self._em_gain_available and not self._ui_is_locked()
        )

    def on_temperature_changed(self):
        val = self.spin_cooler_temp.value()
        self.spin_cooler_temp.setEnabled(False)
        self._bump_instrument_state()
        self.thread.update_temperature(val)

    def on_temperature_set_finished(self, actual_temperature):
        """Reflect the set point that the camera accepted, not only the requested value."""
        self.spin_cooler_temp.blockSignals(True)
        self.spin_cooler_temp.setValue(round(actual_temperature))
        self.spin_cooler_temp.blockSignals(False)
        self.spin_cooler_temp.setEnabled(not self._ui_is_locked())

        # A new set point invalidates any previous "Stabilised" verdict: drop the
        # spread history and re-check immediately instead of leaving a stale
        # "Stabilised" label on screen for up to one more poll interval. This is
        # also the only thing (besides on_temperature_capability_ready) allowed to
        # re-enable auto polling once it has stopped - see on_temperature_read().
        #
        # Gated on _temp_control_available (a plain flag set by
        # on_temperature_capability_ready), not on label_cooler_target.isVisible():
        # QWidget.isVisible() reflects actual on-screen visibility, which requires the
        # whole window to be shown - it can read False for a minimized/not-yet-shown
        # window even though the widget's own setVisible(True) was already applied.
        self._temp_accepted_setpoint = actual_temperature
        self._temp_history = []
        if self._temp_control_available:
            self._temp_auto_poll_enabled = True
            self.label_current_temp.setText("Reading...")
            self._poll_temperature()

    def on_temperature_capability_ready(self, has_control, has_status, temp_min, temp_max):
        """Show/hide the temperature GUI based on whether this camera actually has
        temperature control (mirrors on_em_gain_info_ready's exists-driven show/hide)."""
        for widget in (self.label_cooler_target, self.spin_cooler_temp,
                       self.btn_read_temp, self.label_current_temp):
            widget.setVisible(has_control)

        self._temp_capability_known = True
        self._temp_control_available = has_control
        if not has_control:
            self._temp_auto_poll_enabled = False
            self.temp_poll_timer.stop()
            return

        # ceil()/floor() (not round()) so the spinbox's integer bounds never fall outside
        # the camera-reported float range - rounding outward could let the user pick a
        # value the hardware would then reject or silently clamp further.
        self.spin_cooler_temp.blockSignals(True)
        self.spin_cooler_temp.setRange(math.ceil(temp_min), math.floor(temp_max))
        self.spin_cooler_temp.blockSignals(False)
        self.spin_cooler_temp.setToolTip(
            f"Settable range for this camera: {temp_min:.1f} to {temp_max:.1f} °C"
        )

        self._temp_status_supported = has_status
        self._temp_history = []
        self._temp_auto_poll_enabled = True
        self.temp_poll_timer.start(_TEMP_POLL_INTERVAL_MS)

    def _poll_temperature(self):
        """Timer-driven read: no "Reading..." flash, to avoid flicker every 5s."""
        self.thread.read_temperature()

    def request_temperature_read(self):
        self.label_current_temp.setText("Reading...")
        self.thread.read_temperature()

    def _check_temp_stabilised_by_spread(self, temp):
        """Fallback stabilisation check for hardware with no Locked/Unlocked/Faulted
        enum (status == "unsupported"): recent readings must both be tightly clustered
        AND close to the accepted set point, so a cooler that plateaus far from target
        is not mistaken for "stabilised" just because it stopped drifting."""
        self._temp_history.append(temp)
        if len(self._temp_history) > _TEMP_STABLE_WINDOW:
            self._temp_history.pop(0)
        if len(self._temp_history) < _TEMP_STABLE_WINDOW:
            return False
        spread = max(self._temp_history) - min(self._temp_history)
        if spread > _TEMP_STABLE_SPREAD_C:
            return False
        if self._temp_accepted_setpoint is None:
            return False
        return abs(temp - self._temp_accepted_setpoint) <= _TEMP_TARGET_TOLERANCE_C

    def _restart_temp_poll_timer_if_enabled(self):
        """Resume 5s polling, but only while auto polling is actually enabled.

        Once a Locked/Stabilised verdict disables auto polling, a manual
        "Read current temperature" click must never silently re-enable it, no
        matter what status comes back - only a fresh set point (see
        on_temperature_set_finished) or a reconnect is allowed to do that.
        """
        if self._temp_auto_poll_enabled:
            self.temp_poll_timer.start()

    def on_temperature_read(self, temp, status):
        """Render the latest reading and decide whether polling continues.

        Timer start/stop for the "still settling" cases funnels through
        _restart_temp_poll_timer_if_enabled() regardless of whether this read was
        triggered by the timer or by the manual button, so the two trigger paths
        can never leave the timer running when auto polling was already disabled.
        """
        self._last_temperature_status = status
        if temp == -999.0:
            self.label_current_temp.setText("Error")
            self._restart_temp_poll_timer_if_enabled()
            return
        self._last_temperature_c = float(temp)

        if status == "unsupported":
            # No status enum on this hardware: infer stabilisation from spread +
            # proximity to the set point instead of trusting a reported status.
            stabilised = self._check_temp_stabilised_by_spread(temp)
            suffix = " Stabilised" if stabilised else ""
            self.label_current_temp.setText(f"{temp:.1f} °C{suffix}")
            if stabilised:
                self._temp_auto_poll_enabled = False
                self.temp_poll_timer.stop()
            else:
                self._restart_temp_poll_timer_if_enabled()
            return

        if status == "locked":
            self.label_current_temp.setText(f"{temp:.1f} °C Stabilised")
            self._temp_auto_poll_enabled = False
            self.temp_poll_timer.stop()
        elif status == "faulted":
            self.label_current_temp.setText(f"{temp:.1f} °C (Cooling Fault)")
            self._restart_temp_poll_timer_if_enabled()
        elif status == "drifted":
            # Was previously locked/stabilised and has since moved off the set point
            # (Andor SDK2 DRV_TEMP_DRIFT) - worth calling out distinctly rather than
            # showing a plain reading that looks identical to "still converging".
            self.label_current_temp.setText(f"{temp:.1f} °C (Drifted)")
            self._restart_temp_poll_timer_if_enabled()
        elif status == "off":
            self.label_current_temp.setText(f"{temp:.1f} °C (Cooler Off)")
            self._restart_temp_poll_timer_if_enabled()
        else:
            # "unlocked", or "unknown" (status-capable hardware whose status read
            # failed just this once) - neither is a settled state, so keep polling.
            # "unknown" deliberately does NOT fall through to the spread-based
            # fallback above: a transient status read failure must never be
            # mistaken for "Stabilised" when the camera could actually be Faulted.
            self.label_current_temp.setText(f"{temp:.1f} °C")
            self._restart_temp_poll_timer_if_enabled()

    def on_camera_initialized(self):
        # The unconditional centralWidget().setEnabled(True) below is deliberately NOT
        # made lock-aware: it fires only once, when initialisation completes, and
        # Standby's automatic listen-start is sequenced *after* it (方針2/方針4 の除外
        # 判断). Do not start the API server before this point, or the lock is broken.
        self.init_dialog.accept()
        self.centralWidget().setEnabled(True)

        self.btn_commence.setEnabled(True)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_single.setEnabled(True)

        self.status_label.setText("Camera Ready")

        self.spin_vstart.setMaximum(self.thread.det_height - 1)
        self.spin_vend.setMaximum(self.thread.det_height)

        self.radio_2d.setText(f"2D Image View ({self.thread.det_width}x{self.thread.det_height})")

        self._sync_fixed_spectrometer_center()
        self._apply_hardware_capability_ui()
        # update_plot_labels() (SpectrometerControlMixin) is otherwise only wired to
        # calibration-state changes (apply/clear) and the Raman/Wavelength toggle - none of
        # which fire when a camera with its own native wavelength axis (Ocean Optics) simply
        # finishes connecting. Without this call the axis label/warning banner stay at their
        # pre-connection "Pixel" state until the operator happens to touch calibration or
        # spec mode (confirmed by manually driving SpectrometerGUI end-to-end in --debug mode;
        # see work/work_OceanOptics.md review round 5).
        self.update_plot_labels()

        self.apply_roi_settings()

        # Last: both this handler and on_camera_init_failed() re-enable the central
        # widget unconditionally above, so Standby must not begin listening before
        # this point (work_API_standby.md Step 5 手順3).
        self.maybe_autostart_api_server()

    def _sync_fixed_spectrometer_center(self):
        """For hardware with no movable centre wavelength (Ocean Optics), seed spec_ctrl's
        fixed value from the camera's native wavelength calibration - see
        work/work_OceanOptics.md Step 4. spec_ctrl.get_wavelength() only had a placeholder
        (0.0) at GUI startup (ui.py's init sequence calls it before CameraThread exists), so
        this must run once the camera is actually connected. Native wavelength array validity
        (finite/non-empty/monotonic) is already guaranteed by CameraThreadOceanOptics before
        init_finished is ever emitted (Step 1), so only the median needs computing here."""
        native_wavelengths = getattr(self.thread, "native_wavelengths", None)
        if native_wavelengths is None or len(native_wavelengths) == 0:
            return
        fixed_center_nm = float(np.median(native_wavelengths))
        set_reference_center = getattr(self.spec_ctrl, "set_reference_center", None)
        if set_reference_center is not None:
            set_reference_center(fixed_center_nm)
        self.physical_center_wl = fixed_center_nm
        # Also mirror it onto the (hidden, for Ocean Optics) spinbox itself, matching the
        # blockSignals/setValue pattern SpectrometerControlMixin uses whenever the physical
        # centre changes - otherwise it stays at its initial placeholder (0.0) forever, and
        # FileIOMixin._save_data_to_path()'s legacy "center_wl" header field (which reads
        # spin_centre_wl.value() directly) would contradict the correct value recorded in
        # hardware_metadata (see work/work_OceanOptics.md review round 5).
        self.spin_centre_wl.blockSignals(True)
        self.spin_centre_wl.setValue(fixed_center_nm)
        self.spin_centre_wl.blockSignals(False)

    def _apply_hardware_capability_ui(self):
        """Hide controls the connected hardware cannot actually perform, rather than leaving
        them enabled-but-nonfunctional. Two independent sources (see
        work/work_OceanOptics.md Step 5, 方針4): grating/centre movability is reported by
        spec_ctrl (device-reported capability, matching the existing temperature/EM-gain
        capability-signal pattern) - not a static config flag - while 2D/vertical-ROI support
        is derived directly from the already-known detector height, since a 1-row detector
        cannot physically support either regardless of vendor."""
        capabilities = getattr(
            self.spec_ctrl, "get_capabilities",
            lambda: {"supports_grating": True, "supports_movable_center": True},
        )()
        supports_grating = capabilities.get("supports_grating", True)
        supports_movable_center = capabilities.get("supports_movable_center", True)

        self.combo_grating.setVisible(supports_grating)
        self.lbl_grating.setVisible(supports_grating)
        self.spin_centre_wl.setVisible(supports_movable_center)
        self.lbl_centre.setVisible(supports_movable_center)
        self.btn_apply_spec.setVisible(supports_movable_center)

        is_fixed_1d_detector = self.thread.det_height <= 1
        for widget in (
            self.radio_2d, self.radio_1d_roi,
            self.spin_vstart, self.spin_vend,
            self.lbl_roi_start, self.lbl_roi_end,
        ):
            widget.setVisible(not is_fixed_1d_detector)
        if is_fixed_1d_detector:
            self.radio_1d_full.setChecked(True)

    def on_camera_identity_ready(self, model, serial_number):
        """CameraThread reports the connected camera's model/serial once after connecting;
        cross-check it against spectrometerConfig.json's recorded identity (see ConfigMixin)."""
        self._camera_identity = {"model": model or None, "serial_number": serial_number or None}
        self.check_and_record_hardware_identity("camera", model, serial_number)

        if self.config.get("model") == "OceanOptics":
            # Ocean Optics is a single physical device serving both roles (see
            # work/work_OceanOptics.md 方針2): spec_ctrl never queries hardware for its own
            # identity, so record the camera-reported identity under "spectrometer" too,
            # keeping ConfigurationCatalog's independent camera/spectrometer serial checks
            # meaningful instead of always-empty.
            self.check_and_record_hardware_identity("spectrometer", model, serial_number)
            self._spectrometer_identity = {
                "model": model or None,
                "serial_number": serial_number or None,
            }

        self._update_window_title()

    def on_hardware_error(self, message):
        self.status_label.setText(f"Camera error: {message}")
        QMessageBox.warning(self, "Camera Error", message)

    def on_camera_init_failed(self, reason):
        # Same exclusion as on_camera_initialized(): fires once, before any Standby
        # listen-start, so it can re-enable the central widget unconditionally.
        self.init_dialog.reject()
        self.centralWidget().setEnabled(True)

        self.btn_commence.setEnabled(False)
        self.btn_single.setEnabled(False)

        # temperature_capability_ready can fire (and start the poll timer) before a
        # later init step fails, since capability reporting happens partway through
        # the same try block as the rest of connect (see camera_princeton.py's
        # run()). Stop it defensively so it doesn't keep sending read requests to a
        # thread whose run() has already returned.
        self._temp_auto_poll_enabled = False
        self.temp_poll_timer.stop()

        self.status_label.setText("Camera initialization failed")
        QMessageBox.critical(
            self, "Camera Initialization Failed",
            f"Failed to initialize the camera:\n\n{reason}"
        )

        # Standby still resumes: the operator may well fix the connection and want the
        # rig reachable. Requests that arrive meanwhile get the 503 from Step 3(A).
        self.maybe_autostart_api_server()

    def _sync_roi_widget_states(self):
        """Widget-only half of apply_roi_settings(): the ROI spin boxes' enabled state
        and the background radio's consistency with the current read mode.

        Split out from the hardware push below so re-enabling the UI after an API
        request (which happens several times a second in Standby mode) does not
        re-send ROI settings to the camera thread every time. Returns the read mode
        the widgets currently describe.

        Every setEnabled(True) here is conditioned on the UI lock, since this runs from
        signal handlers that can fire while a remote request owns the instrument.
        """
        unlocked = not self._ui_is_locked()
        is_custom_roi = self.radio_1d_roi.isChecked()
        self.spin_vstart.setEnabled(is_custom_roi and unlocked)
        self.spin_vend.setEnabled(is_custom_roi and unlocked)

        if self.radio_2d.isChecked():
            mode = "2d"
            self.radio_bg_off.setChecked(True)
            self.radio_bg_on.setEnabled(False)
        elif self.radio_1d_full.isChecked():
            mode = "1d_full"
            self.radio_bg_on.setEnabled(unlocked)
        else:
            mode = "1d_roi"
            self.radio_bg_on.setEnabled(unlocked)
        return mode

    def apply_roi_settings(self):
        mode = self._sync_roi_widget_states()
        self._bump_instrument_state()
        self.thread.update_roi_settings(mode, self.spin_vstart.value(), self.spin_vend.value())

    def on_roi_spin_changed(self):
        self.apply_roi_settings()

        for g in self.config.get("grating", []):
            if str(g.get("grooves")) == self.physical_grating:
                g.setdefault("defaultROI", {})["from"] = self.spin_vstart.value()
                g.setdefault("defaultROI", {})["to"] = self.spin_vend.value()
                break
        self.save_config_to_file()

    def start_measurement(self):
        self.is_single_shot = False
        self._ignore_next_frames = False
        self.current_accum_count = 0
        self.btn_single.setEnabled(False)
        self.btn_commence.setEnabled(False)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_terminate.setEnabled(True)
        self._set_button_style(self.btn_terminate, self.BUTTON_STYLE_RED)
        self.thread.start_measuring()

    def stop_measurement(self, release_gate=True):
        """Stop the running measurement.

        release_gate=False is only for the case where "the shot finished, but this
        acquisition's gate ownership must continue" - the path that extends exclusion
        through the background-acquisition save dialog (work_API_standby.md 表#6). The
        default True releases it here as before.
        """
        pending_to_fail = None
        if release_gate:
            pending = getattr(self, "_api_pending_future", None)
            if pending is not None and not pending.done():
                # This is an early stop (operator Terminate or acquisition_failed), not
                # the normal API completion path, which calls us with release_gate=False.
                # Complete the correct Future now so it cannot time out later and run
                # stale cleanup against a subsequent request.
                pending_to_fail = pending
                self._api_pending_future = None
                self._api_pending_context = None

        self.btn_single.setEnabled(True)
        self.btn_commence.setEnabled(True)
        self._set_button_style(self.btn_commence, self.BUTTON_STYLE_GREEN)
        self.btn_terminate.setEnabled(False)
        self._set_button_style(self.btn_terminate, self.BUTTON_STYLE_RED)
        self.lbl_accum_status.setVisible(False)
        self.thread.stop_measuring()
        if self._ui_is_locked():
            # An API-triggered single shot ends here too. Re-enabling Single/Commence
            # while a lock is held would let the operator start a local measurement in
            # the middle of a remote session (work_API_standby.md 方針4).
            self.btn_single.setEnabled(False)
            self.btn_commence.setEnabled(False)
        if release_gate:
            # A background acquisition stopped here never reaches
            # _process_acquired_bg(), so also drop the flag that would otherwise
            # extend the gate through its save dialog (avoids a leaked gate).
            self._is_acquiring_bg = False
            self._release_acquisition_gate()
        if pending_to_fail is not None:
            pending_to_fail.set_exception(
                RuntimeError("The acquisition stopped before producing a complete frame.")
            )

    def on_acquisition_failed(self, error_msg):
        """Camera thread auto-stopped acquisition after repeated hardware errors.

        Without this handler the GUI would silently stay in a "measuring" state
        forever (buttons still showing Terminate as active) with no indication
        that data collection actually halted - dangerous for unattended
        Sequential runs that may go for hours.
        """
        was_sequential = getattr(self, 'is_sequential_running', False)

        self.current_accum_count = 0
        self.accumulated_data = None
        self.accum_frames = None
        self.is_single_shot = False

        if was_sequential:
            self.stop_sequential()
        else:
            self.stop_measurement()

        # stop_sequential() only relays to stop_measurement() (which releases the gate) when
        # thread.is_measuring is still True; but the camera thread already sets is_measuring to
        # False internally before emitting this signal, so that relay is skipped here. Release
        # explicitly as a backstop (no-op if already released by the stop_measurement() call above).
        self._release_acquisition_gate()

        self.status_label.setText("Camera Error: acquisition stopped")
        QMessageBox.critical(
            self, "Acquisition Stopped",
            "Data acquisition failed repeatedly and was stopped automatically.\n\n"
            f"Last error: {error_msg}\n\n"
            "Check the camera connection/hardware before starting a new measurement."
        )

    def on_cosmic_ray_removal_toggled(self, checked):
        self.spin_spike_threshold.setEnabled(checked)
        self._bump_instrument_state()

    def on_data_ready(self, mode, data):
        if not getattr(self.thread, 'is_measuring', False) and not self.is_single_shot:
            # Stray frame that arrived after the thread was told to stop measuring
            # (the camera thread may already be mid-acquisition when stop_measuring()
            # is called) - ignore it rather than accumulating/displaying/saving it.
            return

        if self._ignore_next_frames:
            return

        target_accum = self._active_target_accum if self._active_target_accum is not None else self.spin_accumulate.value()

        if self.current_accum_count == 0:
            # Decide the combine strategy once per cycle, at the first frame, so a
            # mid-cycle checkbox toggle can't desync accum_frames vs accumulated_data.
            self._accum_use_rejection = (
                mode == "1d"
                and self.chk_cosmic_ray_removal.isChecked()
                and AccumulationCombiner.MIN_FRAMES_FOR_REJECTION <= target_accum <= MAX_BUFFERED_FRAMES
            )
            if self._accum_use_rejection:
                self.accum_frames = [data.astype(np.float64).copy()]
                self.accumulated_data = None
            else:
                if (self.chk_cosmic_ray_removal.isChecked() and mode == "1d"
                        and not (AccumulationCombiner.MIN_FRAMES_FOR_REJECTION <= target_accum <= MAX_BUFFERED_FRAMES)):
                    print("[Cosmic ray removal] skipped: accumulation count out of range "
                          f"({AccumulationCombiner.MIN_FRAMES_FOR_REJECTION}-{MAX_BUFFERED_FRAMES}).")
                self.accum_frames = None
                self.accumulated_data = data.astype(np.float64).copy()
        else:
            if self._accum_use_rejection:
                self.accum_frames.append(data.astype(np.float64).copy())
            else:
                self.accumulated_data += data.astype(np.float64)

        self.current_accum_count += 1
        self.lbl_accum_status.setText(f"Acquired: {self.current_accum_count} / {target_accum}")
        self.lbl_accum_status.setVisible(target_accum > 1)

        if self.current_accum_count >= target_accum:
            if self.is_single_shot:
                self._ignore_next_frames = True
                # A background acquisition holds the gate through its save dialog, and
                # an API acquisition holds it until the response-state snapshot has
                # been taken (表#6, 方針9).
                defer_release = (
                    getattr(self, '_is_acquiring_bg', False)
                    or getattr(self, '_api_pending_future', None) is not None
                )
                self.stop_measurement(release_gate=not defer_release)
            self.current_accum_count = 0

            if self._accum_use_rejection:
                threshold_k = self.spin_spike_threshold.value()
                final_data, n_spikes = AccumulationCombiner.combine(
                    self.accum_frames, reject_spikes=True, threshold_k=threshold_k
                )
                self.accum_frames = None
                if n_spikes > 0:
                    print(f"[Cosmic ray removal] {n_spikes} spike value(s) rejected "
                          f"over {target_accum} accumulated frames (threshold={threshold_k}σ).")
                    self.lbl_accum_status.setText(
                        f"Acquired: {target_accum} / {target_accum} ({n_spikes} spikes rejected)"
                    )
                    self.lbl_accum_status.setVisible(True)
            else:
                final_data = self.accumulated_data.copy()
                self.accumulated_data = None

            self._latest_hardware_capture = capture_hardware_state(self, target_accum)
            self._hardware_capture_by_mode[mode] = self._latest_hardware_capture
            self._process_completed_data(mode, final_data)

    def _process_completed_data(self, mode, data):
        if self.is_single_shot:
            self.is_single_shot = False
            acquiring_bg = getattr(self, '_is_acquiring_bg', False)
            pending = getattr(self, '_api_pending_future', None)
            if not acquiring_bg and pending is None:
                # stop_measurement() (called from on_data_ready() just before this method, for the
                # single-shot completion path) already releases the gate; call again defensively in
                # case that call path ever changes so a single-shot completion never leaves it stuck.
                # Only a background acquisition is deliberately kept held until its
                # save dialog closes (表#6).
                self._release_acquisition_gate()
            # Reset any API-supplied accumulation override so the next GUI-triggered
            # acquisition goes back to reading spin_accumulate.value().
            self._active_target_accum = None

            if mode == "1d":
                self.raw_1d_data = data
            elif mode == "2d":
                self.raw_2d_data = data

            # `pending` is None for an operator-triggered single shot, and also for a
            # frame that arrives after an API request already timed out
            # (_api_abort_acquire() clears the future and _active_target_accum). Such a
            # late frame deliberately falls through to the ordinary display path: the
            # plot updates, nothing else happens. See work_API_standby.md Step 3(C).
            if pending is not None:
                self._api_pending_future = None
                try:
                    if not pending.done():
                        raw = self.raw_1d_data if mode == "1d" else self.raw_2d_data
                        payload = {"raw": raw, "mode": mode}
                        # Captured before the gate is released, so the state reported
                        # really is the state this frame was taken under (方針9).
                        payload.update(self._api_acquire_snapshot(
                            len(raw) if mode == "1d" else None, mode
                        ))
                        pending.set_result(payload)
                finally:
                    self._api_pending_context = None
                    self._release_acquisition_gate()

            self.update_display(is_new_data=True, mode=mode)

            if acquiring_bg:
                self._is_acquiring_bg = False
                try:
                    self._process_acquired_bg()
                finally:
                    # Always released here, whether the save succeeded, was
                    # cancelled, or raised (表#6).
                    self._release_acquisition_gate()
            return

        if mode == "1d":
            self.raw_1d_data = data
        elif mode == "2d":
            self.raw_2d_data = data
        self.update_display(is_new_data=True, mode=mode)
