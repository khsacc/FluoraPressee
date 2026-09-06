import functools
import json
import socket
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from datetime import datetime

import numpy as np
import uvicorn
from PyQt6.QtCore import QCoreApplication, QThread
from PyQt6.QtWidgets import QMessageBox

from src.api.info_helpers import (
    build_config_response,
    build_device_response,
    normalize_camera_metadata,
    normalize_spectrometer_metadata,
)
from src.core.configuration_catalog import format_configuration_label
from src.hardware.status.instrument_status import legacy_camera_snapshot, unavailable_device
from src.core.measurement_metadata import capture_hardware_state, public_axis_kind, public_axis_unit
from src.core.pressureCalc import PressureCalculator
from src.ui.ui_mixins.acquisition_mixin import GateBusyError


class BackgroundMismatchError(Exception):
    """Raised by api_acquire() when dark_mode="reuse_loaded" doesn't match the
    loaded background's acquisition settings and the caller didn't opt in via
    ignore_mismatch=True.
    """
    pass


class CameraNotReadyError(Exception):
    """Raised by _api_start_acquire() when the camera thread is not running.

    In Standby mode the app listens all day, so a request can perfectly well arrive
    while the camera failed to initialise or was never connected. src/api/server.py
    turns this into a 503: it is a temporary absence of the instrument, not a defect
    in the caller's request (work_API_standby.md Step 3(A)).
    """
    pass


class StateTokenMismatchError(Exception):
    """Raised when a request's expected_state_token no longer matches the instrument.

    Carries the current token so the client can resynchronise without a second call.
    """

    def __init__(self, expected, current):
        super().__init__(
            "The instrument state has changed since the token you supplied was issued."
        )
        self.expected = expected
        self.current = current


class ExposureApplyError(Exception):
    """Raised by _api_start_acquire() when the camera thread reports (via its optional
    get_exposure_error(seq)) that a requested exposure_time_s failed to reach hardware -
    e.g. outside an Ocean Optics device's supported integration time range. Without this
    check, acquisition would silently proceed with the previous exposure time while the
    response reported the requested (never-applied) one - see work/work_OceanOptics.md.
    """
    pass


# The three API server modes (work_API_standby.md 方針1). "locked" reproduces the
# original "Start API Server" behaviour exactly: listening plus a UI lock held for the
# whole run. "standby" listens just the same but locks only while a request is
# actually operating the instrument.
API_MODES = ("off", "standby", "locked")

# How often the shutdown watcher checks whether the uvicorn thread has exited.
_API_STOP_POLL_MS = 200

# Start verification: how often to look, and how many looks before giving up waiting.
_API_START_POLL_MS = 200
_API_START_MAX_TICKS = 15

# Head-room added to exposure x accumulations when deriving an acquisition's timeout,
# covering readout, the frame's trip through the Qt event loop, and camera start-up.
ACQUIRE_TIMEOUT_MARGIN_S = 15.0


@functools.lru_cache(maxsize=1)
def local_ip_address():
    """Best-effort LAN address of this machine, for display only.

    Cached for the life of the process: resolving it is a real (occasionally slow)
    DNS/NSS syscall, and this used to run on the GUI thread on every single API
    request (via note_api_request -> _build_api_status_text) even though the
    documented contract here is already "best-effort, for display only" - a machine's
    LAN address essentially never changes mid-session, and this function was never a
    promise of a live-verified value.
    """
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _parse_temp_c(text):
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


class ApiMixin:
    # ------------------------------------------------------------------
    # GUI-thread helpers. Must be invoked via self.gui_bridge.call(...) from
    # a non-GUI thread (GuiBridge itself refuses to be called from the GUI
    # thread, see src/api/gui_bridge.py).
    # ------------------------------------------------------------------

    def _api_camera_backend(self):
        module = type(self.thread).__module__
        if module.endswith("camera_princeton"):
            return "princeton_picam"
        if module.endswith("camera_oceanoptics"):
            return "oceanoptics_seabreeze"
        return "andor_sdk2"

    def _api_spectrometer_backend(self):
        module = type(self.spec_ctrl).__module__
        if module.endswith("spectrometer_princeton"):
            return "princeton_acton"
        if module.endswith("spectrometer_oceanoptics"):
            return "oceanoptics_seabreeze"
        return "andor_shamrock"

    def _api_build_camera_info(self, status=None):
        """Build a camera response on the GUI thread from cached state."""
        capture = capture_hardware_state(self, self.spin_accumulate.value())
        metadata = normalize_camera_metadata(capture["camera"])
        running = bool(self.thread.isRunning())
        debug = bool(getattr(self.thread, "debug", self.debug))
        # CameraThreadOceanOptics holds its connection as `self.spec`, not `self.cam`
        # (Andor/Princeton) - checking both covers every backend without needing to
        # branch on _api_camera_backend() here too.
        hardware_connected = bool(
            not debug
            and running
            and (
                getattr(self.thread, "cam", None) is not None
                or getattr(self.thread, "spec", None) is not None
            )
        )
        return build_device_response(
            backend=self._api_camera_backend(),
            debug=debug,
            operational=running and (debug or hardware_connected),
            hardware_connected=hardware_connected,
            busy=self._instrument_status_busy(),
            metadata=metadata,
            status=status,
        )

    def _api_build_spectrometer_info(self, status=None):
        """Build a spectrometer response on the GUI thread from cached state."""
        capture = capture_hardware_state(self, self.spin_accumulate.value())
        configured_identity = self.config.get("hardware_identity", {}).get("spectrometer", {})
        metadata = normalize_spectrometer_metadata(
            capture["spectrometer"], configured_identity
        )
        debug = bool(getattr(self.spec_ctrl, "debug", self.debug))
        if self._api_spectrometer_backend() == "oceanoptics_seabreeze":
            # Ocean Optics has no separate spectrometer hardware to query -
            # SpectrometerControllerOceanOptics is a no-op that always reports
            # is_initialized=True regardless of whether the single physical device (owned
            # by the camera thread) is actually connected. Report the real connection
            # state from there instead, so a disconnected/failed camera is never shown as
            # a connected spectrometer.
            hardware_connected = bool(
                not debug
                and self.thread.isRunning()
                and getattr(self.thread, "spec", None) is not None
            )
        else:
            hardware_connected = bool(getattr(self.spec_ctrl, "is_initialized", False))
        return build_device_response(
            backend=self._api_spectrometer_backend(),
            debug=debug,
            operational=debug or hardware_connected,
            hardware_connected=hardware_connected,
            busy=self._instrument_status_busy(),
            metadata=metadata,
            status=status,
        )

    def _api_begin_hardware_refresh(self):
        """Acquire the same exclusion gate used by measurement/calibration."""
        if self._instrument_status_busy() or not self._try_acquire_gate("api"):
            raise GateBusyError(self._gate_busy_reason())

    def _api_end_hardware_refresh(self):
        self._release_acquisition_gate()

    def _api_start_camera_status_refresh(self):
        self._api_begin_hardware_refresh()
        future = Future()
        self._api_camera_status_future = future
        try:
            if not self.thread.isRunning():
                future.set_result(unavailable_device(
                    self._api_camera_backend(), "Camera is not connected."
                ))
            elif not hasattr(self.thread, "request_status"):
                future.set_result(unavailable_device(
                    self._api_camera_backend(), "Camera status reporting is unavailable."
                ))
            else:
                self.thread.request_status()
        except Exception:
            self._api_camera_status_future = None
            self._api_end_hardware_refresh()
            raise
        return future

    def _api_on_camera_status_ready(self, snapshot):
        """Resolve an API live-status request from the camera thread signal."""
        future = getattr(self, "_api_camera_status_future", None)
        if future is None or future.done():
            return
        if isinstance(snapshot, dict) and "Error" in snapshot and "sections" not in snapshot:
            rows = snapshot.get("Error") or []
            message = rows[0][1] if rows and len(rows[0]) > 1 else "Camera status query failed."
            normalized = unavailable_device(self._api_camera_backend(), message)
        else:
            normalized = legacy_camera_snapshot(snapshot, self._api_camera_backend())
        future.set_result(normalized)

    def api_get_camera_info(self, refresh=False, timeout=10.0):
        """Worker-thread entry point for GET /hardware/camera."""
        if not refresh:
            return self.gui_bridge.call(self._api_build_camera_info)

        future = self.gui_bridge.call(self._api_start_camera_status_refresh)
        try:
            snapshot = future.result(timeout=timeout)
            return self.gui_bridge.call(
                lambda: self._api_build_camera_info(status=snapshot)
            )
        finally:
            self.gui_bridge.call(lambda: self._api_finish_camera_status_refresh(future))

    def _api_finish_camera_status_refresh(self, future):
        if getattr(self, "_api_camera_status_future", None) is future:
            self._api_camera_status_future = None
        self._api_end_hardware_refresh()

    def api_get_spectrometer_info(self, refresh=False, timeout=30.0):
        """Worker-thread entry point for GET /hardware/spectrometer."""
        if not refresh:
            return self.gui_bridge.call(self._api_build_spectrometer_info)

        self.gui_bridge.call(self._api_begin_hardware_refresh)
        result_future = Future()

        def collect_status():
            try:
                result_future.set_result(self.spec_ctrl.get_status_snapshot())
            except Exception as exc:
                result_future.set_exception(exc)

        worker = threading.Thread(
            target=collect_status,
            name="FluoraPressee-SpectrometerStatus",
            daemon=True,
        )
        worker.start()

        release_in_finally = True
        try:
            snapshot = result_future.result(timeout=timeout)
            return self.gui_bridge.call(
                lambda: self._api_build_spectrometer_info(status=snapshot)
            )
        except FutureTimeoutError:
            # The hardware worker may still hold its controller lock. Keep the
            # acquisition gate until it really exits, even though HTTP returns 504.
            release_in_finally = False
            result_future.add_done_callback(
                lambda _future: self._api_release_refresh_after_timeout()
            )
            raise
        finally:
            if release_in_finally:
                self.gui_bridge.call(self._api_end_hardware_refresh)

    def _api_release_refresh_after_timeout(self):
        try:
            self.gui_bridge.call(self._api_end_hardware_refresh)
        except Exception as exc:
            print(f"Failed to release API hardware-status gate after timeout: {exc}")

    def api_get_config(self):
        """GUI-thread helper for GET /config."""
        try:
            with open("spectrometerConfig.json", "r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except Exception:
            stored = self.config
        return build_config_response(
            self.config,
            getattr(self, "_startup_config", self.config),
            stored_config=stored,
        )

    def _assert_instrument_state_token(self, expected):
        """Compare a caller-supplied token against the live one.

        Deliberately called at exactly one moment - immediately after the gate is
        acquired and before anything is changed - so that supplying both a
        configuration_id and an expected_state_token means "apply this configuration,
        but abort if anyone changed something first" rather than always mismatching on
        the configuration apply's own state change (work_API_standby.md 方針8).
        """
        if expected is None:
            return
        current = self.instrument_state_token
        if expected != current:
            raise StateTokenMismatchError(expected, current)

    def _api_start_acquire(
        self, exposure_s=None, accumulations=None, *, gate_already_held=False,
        dark_mode="none", expected_state_token=None,
    ):
        """Kick off a single-shot acquisition and return immediately.

        Does not wait for the frame to arrive - that happens asynchronously via
        the existing data_ready signal path; the returned Future is resolved
        later by _process_completed_data().
        """
        # Checked before the gate is taken, so a not-ready camera cannot add another
        # path that has to remember to release it.
        if not self.thread.isRunning():
            raise CameraNotReadyError(
                "The camera is not initialised, so no acquisition can be started."
            )
        if not gate_already_held and not self._try_acquire_gate("api"):
            raise GateBusyError(self._gate_busy_reason())
        if gate_already_held and not self._acquisition_gate.locked():
            raise RuntimeError("configuration operation lost the acquisition gate")
        try:
            # gate_already_held means a configuration apply already compared the token
            # before it moved anything; comparing again here would fail against the
            # state that apply itself just created.
            if not gate_already_held:
                self._assert_instrument_state_token(expected_state_token)
        except Exception:
            if not gate_already_held:
                self._release_acquisition_gate()
            raise

        actual_accum = accumulations if accumulations is not None else self.spin_accumulate.value()

        if exposure_s is not None:
            # Block until the camera thread has actually pushed the new exposure to
            # hardware rather than hoping a fixed sleep was long enough - if the thread
            # is mid-snap() on a previous long exposure, a flat 0.1s sleep can elapse
            # before it's picked up, and take_single_spectrum() below would then measure
            # with the stale exposure still on the hardware.
            previous_exposure = float(self.thread.current_exposure)
            wait_timeout = previous_exposure + 15
            seq = self.thread.update_exposure(exposure_s)
            if not self.thread.wait_for_exposure_applied(seq, timeout=wait_timeout):
                print("Warning: timed out waiting for the new exposure to reach hardware before API acquisition")
            # Not every camera thread implements this (only CameraThreadOceanOptics does,
            # since its integration_time_micros() can reject an out-of-range value outright
            # rather than clamping it) - absence means "assume applied", matching the
            # pre-existing behaviour for Andor/Princeton.
            get_exposure_error = getattr(self.thread, "get_exposure_error", None)
            exposure_error = get_exposure_error(seq) if get_exposure_error is not None else None
            if exposure_error is not None:
                self._release_acquisition_gate()
                raise ExposureApplyError(f"Failed to set exposure: {exposure_error}")
            # Reflects what the hardware actually accepted (or, on failure, was left
            # unchanged at) rather than blindly trusting the requested value - see
            # work/work_OceanOptics.md review round 5.
            actual_exposure = self.thread.current_exposure
            if not np.isclose(
                float(actual_exposure), previous_exposure, rtol=0.0, atol=1e-12
            ):
                # The expected token was already checked immediately after taking the
                # gate.  Bump now so this request's response describes the new physical
                # exposure and later callers cannot reuse a token for the old state.
                self._bump_instrument_state()
        else:
            actual_exposure = self.spin_acq_time.value()

        if accumulations is not None:
            self._active_target_accum = accumulations

        future = Future()
        self._api_pending_future = future
        # Everything _process_completed_data() needs to build the response snapshot
        # while it still holds the gate (方針9).
        self._api_pending_context = {
            "dark_mode": dark_mode,
            "exposure": actual_exposure,
            "accumulations": actual_accum,
        }
        try:
            self.take_single_spectrum()
        except Exception:
            self._active_target_accum = None
            self._api_pending_future = None
            self._api_pending_context = None
            self._release_acquisition_gate()
            raise

        return future, actual_exposure, actual_accum

    def _api_acquire_snapshot(self, raw_len, mode):
        """Response state captured on the GUI thread while the gate is still held.

        Reading it afterwards would be a lie under multi-client use: another request
        could change the instrument between the frame arriving and the state being
        read, so the hardware_state and token returned would not describe the data
        actually returned. Capturing it inside the gate also cuts the round trips per
        /acquire from three to one (方針9).
        """
        context = getattr(self, "_api_pending_context", None) or {}
        x, temp_text, bg_data, bg_mismatch = self._api_finalize_acquire(
            raw_len,
            mode,
            context.get("dark_mode", "none"),
            context.get("exposure", self.spin_acq_time.value()),
            context.get("accumulations", self.spin_accumulate.value()),
        )
        return {
            "x": x,
            "temp_text": temp_text,
            "bg_data": bg_data,
            "bg_mismatch": bg_mismatch,
            "configuration_state": self._api_configuration_state(),
        }

    def _api_abort_acquire(self, future):
        """Runs on the GUI thread. Cleans up after a timed-out API acquisition.

        Without this the acquisition gate (and with it the UI lock derived from it)
        would stay held forever after a 504, leaving the GUI permanently locked.
        Stopping the camera before releasing the gate matters: releasing while the
        detector is still acquiring would let the next request start and collide with
        it on hardware.
        """
        if getattr(self, "_api_pending_future", None) is not future:
            # The frame may have completed at the timeout boundary, or a failed/stopped
            # request may already have released the gate and allowed a newer request to
            # start.  Cleanup for an old generation must never stop the new acquisition.
            return
        self._api_pending_future = None
        self._api_pending_context = None
        self._active_target_accum = None
        self.is_single_shot = False
        if getattr(self.thread, "is_measuring", False):
            # stop_measurement() calls _release_acquisition_gate() internally; the
            # _gate_held_by_me guard means this can never double-release.
            self.stop_measurement()
        else:
            self._release_acquisition_gate()

    def _api_check_bg_mismatch(self, actual_exposure, actual_accum):
        """Like FileIOMixin.check_bg_mismatch(), but independent of the
        radio_bg_on toggle - the API's dark handling must not depend on that
        GUI setting (see work/work_API.md, "Darkデータの扱い"). Compares
        against the *actual* exposure/accumulations used for this request
        rather than the current widget values, since an API caller may have
        overridden either without touching the widgets.
        """
        bg_meta = self.loaded_bg_metadata
        if bg_meta is None:
            return False

        curr_mode = "1D Spectrum (Custom ROI)" if self.radio_1d_roi.isChecked() else "1D Spectrum (Full Range Binning)"

        mismatch = False
        if abs(actual_exposure - bg_meta.get("acquisition_time", 0)) > 1e-4:
            mismatch = True
        if actual_accum != bg_meta.get("accumulations", 1):
            mismatch = True
        if curr_mode != bg_meta.get("mode"):
            mismatch = True
        if curr_mode == "1D Spectrum (Custom ROI)":
            if self.spin_vstart.value() != bg_meta.get("roi_start") or self.spin_vend.value() != bg_meta.get("roi_end"):
                mismatch = True
        return mismatch

    def _api_finalize_acquire(self, raw_len, mode, dark_mode, actual_exposure, actual_accum):
        """Gather everything the response needs that requires reading GUI state:
        the calibrated x-axis (1D only - a 2D image has no single x-axis), the
        detector temperature label, and - for dark_mode="reuse_loaded" - the
        loaded background array and whether it mismatches this request.
        """
        x = self.get_x_axis(raw_len) if mode == "1d" else None
        temp_text = self.label_current_temp.text()

        bg_data = None
        bg_mismatch = False
        if mode == "1d" and dark_mode == "reuse_loaded":
            bg_data = self.loaded_bg_data
            if bg_data is not None:
                bg_mismatch = self._api_check_bg_mismatch(actual_exposure, actual_accum)

        return x, temp_text, bg_data, bg_mismatch

    # ------------------------------------------------------------------
    # API worker-thread entry point.
    # ------------------------------------------------------------------

    def _api_start_configuration_apply(
        self, record, axis_mode, expected_state_token=None
    ):
        """Acquire the operation gate and stage/move a configuration on the GUI thread."""
        if self._instrument_status_busy() or not self._try_acquire_gate("api"):
            raise GateBusyError(self._gate_busy_reason())
        completion_future = Future()
        try:
            # Compared while the gate is held but before anything moves, so this is
            # the one place the check has to happen for a configuration request.
            self._assert_instrument_state_token(expected_state_token)
            self._prepare_configuration_for_loading(
                record,
                axis_mode=axis_mode,
                completion_future=completion_future,
                # Only Ocean Optics bypasses centre comparison/movement by device
                # identity.  For every other backend preserve the existing exact
                # grating+centre+ROI no-op test.
                skip_move=(
                    self._is_oceanoptics_backend()
                    or self._configuration_matches_current_state(record)
                ),
            )
        except Exception:
            self._clear_pending_configuration()
            self._loading_config = False
            self._release_acquisition_gate()
            raise
        return completion_future

    def _api_release_gate_after_future(self):
        try:
            app = QCoreApplication.instance()
            if app is not None and QThread.currentThread() is app.thread():
                # concurrent.futures callbacks run synchronously in the thread that
                # completes the Future. Configuration completion normally happens on
                # the GUI thread, where GuiBridge.call() would reject/deadlock.
                self._release_acquisition_gate()
            else:
                # If the Future completed in the narrow gap before add_done_callback(),
                # the callback runs immediately on the API worker instead.
                self.gui_bridge.call(self._release_acquisition_gate)
        except Exception as exc:
            print(f"Failed to release configuration operation gate: {exc}")

    def _api_validate_configuration(self, configuration_id):
        record = self.configuration_catalog.get_configuration(configuration_id)
        hardware_context = self.gui_bridge.call(self.configuration_hardware_context)
        self.configuration_catalog.assert_compatible(record, hardware_context)
        return record

    def _api_wait_for_configuration(
        self, record, axis_mode, timeout=120.0, expected_state_token=None
    ):
        # If this call raises, _api_start_configuration_apply either never
        # acquired the gate (busy), or acquired and released it while rolling
        # back a staging failure. Do not release an unknown owner's gate here.
        future = self.gui_bridge.call(
            lambda: self._api_start_configuration_apply(
                record, axis_mode, expected_state_token=expected_state_token
            )
        )
        try:
            future.result(timeout=timeout)
        except FutureTimeoutError:
            # Movement may still hold a controller lock. Release the operation
            # gate only after the move callback has actually completed.
            future.add_done_callback(
                lambda _future: self._api_release_gate_after_future()
            )
            raise
        except Exception:
            # Receiving a Future proves that this configuration operation
            # acquired the gate. A completed move failure will not release it
            # in the GUI callback, so release this operation's ownership here.
            self.gui_bridge.call(self._release_acquisition_gate)
            raise
        return future

    def _api_configuration_state(self):
        hardware = self.configuration_hardware_context()
        try:
            grating = self._current_grating_definition(hardware)
        except Exception:
            grating = {"index": None, "grooves_per_mm": None}
        roi = self._current_roi_definition()
        calibrated = self.calib_coeffs is not None
        # public_axis_kind()/public_axis_unit() are the single source of truth for both
        # "configuration.axis_mode" here and AcquireResponse.x_axis (added below) - see
        # work/work_OceanOptics.md Step 8. Without this, Ocean Optics' native-wavelength
        # acquisitions would report x_axis.source="native_wavelength" while axis_mode stayed
        # "pixel" in the very same response.
        axis_kind = public_axis_kind(self)
        axis_unit = public_axis_unit(self, axis_kind)
        # "configuration.unit" intentionally keeps the "Wavelength"/"Raman shift"/"pixel"
        # vocabulary (matching self.calib_unit and POST /calibration's request/response,
        # see docs-site/docs/api/calibration.md) rather than axis_unit's "nm"/"cm-1"/None - the two fields serve
        # different callers. It must still track axis_kind rather than only
        # `calibrated`, though: a native-wavelength axis (Ocean Optics, no FluoRaPressée
        # calibration loaded) is a real Wavelength/Raman-shift axis, not a pixel index.
        if calibrated:
            display_unit = self.calib_unit
        elif axis_kind == "native_wavelength":
            display_unit = "Raman shift" if self.radio_spec_mode_raman.isChecked() else "Wavelength"
        else:
            display_unit = "pixel"
        positioned_id = getattr(self, "positioned_configuration_id", None)
        positioned_slot_id = getattr(
            self, "positioned_configuration_slot_id", None
        )
        if positioned_id is not None:
            try:
                positioned_record = self.configuration_catalog.get_configuration(
                    positioned_id
                )
                if not self._configuration_matches_current_state(positioned_record):
                    positioned_id = None
                    positioned_slot_id = None
            except Exception:
                positioned_id = None
                positioned_slot_id = None
        return {
            # Declared on StatusResponse, AcquireResponse and
            # ApplyConfigurationResponse (src/api/schemas.py) - pydantic's default
            # extra="ignore" would otherwise drop it silently - and copied explicitly
            # in _acquire_response_payload(), which lists its fields by hand.
            "instrument_state_token": self.instrument_state_token,
            "configuration": {
                "configuration_id": positioned_id,
                "slot_id": positioned_slot_id,
                "axis_mode": axis_kind,
                "calibration_applied": calibrated,
                "unit": display_unit,
            },
            "hardware_state": {
                "grating_index": grating["index"],
                "grooves_per_mm": grating["grooves_per_mm"],
                "actual_center_wavelength_nm": float(
                    hardware["actual_center_wavelength_nm"]
                    if hardware["actual_center_wavelength_nm"] is not None
                    else self.physical_center_wl
                ),
                "roi_mode": roi["roi_mode"],
                "roi_start": roi["roi_start"],
                "roi_end": roi["roi_end"],
            },
            # Only declared on AcquireResponse (src/api/schemas.py); pydantic's default
            # extra="ignore" silently drops this key for StatusResponse/
            # ApplyConfigurationResponse, which also consume this same dict via **state.
            "x_axis": {
                "source": axis_kind,
                "unit": axis_unit,
                "calibrated": axis_kind == "calibrated",
            },
        }

    def api_list_configurations(
        self, *, active_only=True, include_incompatible=False, limit=100, offset=0
    ):
        hardware_context = self.gui_bridge.call(self.configuration_hardware_context)
        return self.configuration_catalog.list_selectable(
            hardware_context,
            active_only=active_only,
            include_incompatible=include_incompatible,
            limit=limit,
            offset=offset,
        )

    def api_get_configuration(self, configuration_id):
        record = self.configuration_catalog.get_configuration(configuration_id)
        hardware_context = self.gui_bridge.call(self.configuration_hardware_context)
        reasons = self.configuration_catalog.compatibility_reasons(
            record, hardware_context
        )
        return {
            "catalog_revision": self.configuration_catalog.catalog_revision(),
            "configuration": record,
            "compatible": not reasons,
            "incompatibility_reasons": reasons,
        }

    def api_resolve_configurations(self, slot_ids):
        hardware_context = self.gui_bridge.call(self.configuration_hardware_context)
        # Each entry is a bare slot_id string or a SlotResolutionRequest pydantic
        # model (src/api/schemas.py) naming axis_kind/excitation explicitly;
        # ConfigurationCatalog.resolve_slots() only knows str | dict.
        normalized = [
            entry if isinstance(entry, str) else entry.model_dump(exclude_none=True)
            for entry in slot_ids
        ]
        return self.configuration_catalog.resolve_slots(normalized, hardware_context)

    def api_apply_configuration(self, configuration_id, axis_mode="calibrated"):
        record = self._api_validate_configuration(configuration_id)
        self._api_wait_for_configuration(record, axis_mode)
        try:
            state = self.gui_bridge.call(self._api_configuration_state)
        finally:
            # _api_wait_for_configuration returned successfully, so this call
            # owns the gate and is responsible for releasing it.
            self.gui_bridge.call(self._release_acquisition_gate)
        return {
            "applied": True,
            "configuration_id": record["configuration_id"],
            "slot_id": record["slot_id"],
            "display_label": format_configuration_label(record),
            **state,
        }

    def api_acquire(
        self,
        exposure_s=None,
        accumulations=None,
        dark_mode="none",
        dark_data=None,
        ignore_mismatch=False,
        configuration_id=None,
        axis_mode="calibrated",
        expected_state_token=None,
        timeout=30.0,
    ):
        """Synchronous single-shot acquisition for API callers.

        Must be called from a non-GUI thread. Background subtraction is
        computed here from the raw acquired data and this call's own
        parameters only - it never depends on the GUI's "Subtract background"
        toggle or "Flip X-axis" checkbox (see work/work_API.md).
        """
        if dark_mode == "provided" and dark_data is None:
            raise ValueError('dark_mode="provided" requires dark_data')

        configuration_applied = False
        if configuration_id is not None:
            record = self._api_validate_configuration(configuration_id)
            self._api_wait_for_configuration(
                record, axis_mode, expected_state_token=expected_state_token
            )
            configuration_applied = True

        try:
            future, actual_exposure, actual_accum = self.gui_bridge.call(
                lambda: self._api_start_acquire(
                    exposure_s,
                    accumulations,
                    gate_already_held=configuration_applied,
                    dark_mode=dark_mode,
                    expected_state_token=expected_state_token,
                )
            )
        except Exception:
            if configuration_applied:
                self.gui_bridge.call(self._release_acquisition_gate)
            raise
        acquisition_timeout = max(
            float(timeout),
            float(actual_exposure) * int(actual_accum) + ACQUIRE_TIMEOUT_MARGIN_S,
        )
        try:
            result = future.result(timeout=acquisition_timeout)
        except FutureTimeoutError:
            # Only on timeout: a normal completion is already cleaned up by the
            # existing data_ready path, so an unconditional finally would double up.
            self.gui_bridge.call(lambda: self._api_abort_acquire(future))
            raise
        raw = result["raw"]
        mode = result["mode"]

        # Everything below was captured on the GUI thread while the gate was still
        # held (方針9), so no further gui_bridge round trip is needed here.
        x = result["x"]
        temp_text = result["temp_text"]
        bg_data = result["bg_data"]
        bg_mismatch = result["bg_mismatch"]
        configuration_state = result["configuration_state"]

        background_mismatch_warning = False

        if mode == "1d":
            if dark_mode == "none":
                y = raw.copy()
            elif dark_mode == "provided":
                dark_arr = np.asarray(dark_data)
                if len(dark_arr) != len(raw):
                    raise ValueError(
                        f"dark_data length ({len(dark_arr)}) does not match acquired data length ({len(raw)})"
                    )
                y = raw - dark_arr
            elif dark_mode == "reuse_loaded":
                if bg_data is None:
                    raise ValueError('dark_mode="reuse_loaded" but no background is currently loaded')
                if bg_mismatch and not ignore_mismatch:
                    raise BackgroundMismatchError(
                        "Loaded background does not match this request's exposure/accumulations/ROI settings. "
                        "Pass ignore_mismatch=True to subtract anyway, or use dark_mode='provided'/'none'."
                    )
                y = raw - bg_data
                if bg_mismatch:
                    background_mismatch_warning = True
            else:
                raise ValueError(f"Unknown dark_mode: {dark_mode!r}")
        else:
            y = raw

        response = {
            "x": x,
            "y_raw": raw,
            "y": y,
            "mode": mode,
            "exposure_time_s": actual_exposure,
            "accumulations": actual_accum,
            "detector_temperature_c": _parse_temp_c(temp_text),
            "timestamp": datetime.now().isoformat(),
            **configuration_state,
        }
        if background_mismatch_warning:
            response["background_mismatch_warning"] = True
        return response

    # ------------------------------------------------------------------
    # Stateless computation (no GUI thread / widget dependency at all).
    # ------------------------------------------------------------------

    def api_fit(self, x, y, fit_function, fit_start=None, fit_end=None,
                fit_peak_count=2, peak_sort_order="x_desc", baseline_model="constant"):
        """Fit a spectrum using DataAnalyzer directly - no GuiBridge needed,
        since DataAnalyzer.fit_spectrum() is Qt-independent. Deliberately does
        not touch combo_fit_func/spin_fit_start/spin_fit_end so a concurrent
        API request never disturbs the operator's own fit display settings.
        """
        x_fit, y_fit_curve, res = self.analyzer.fit_spectrum(
            np.asarray(x), np.asarray(y), fit_function, fit_start, fit_end,
            peak_count=fit_peak_count, peak_sort_order=peak_sort_order,
            baseline_model=baseline_model
        )
        if res is None:
            return {"success": False, "x_fit": None, "y_fit": None, "fit": None}
        return {"success": True, "x_fit": x_fit, "y_fit": y_fit_curve, "fit": res}

    def api_pressure(self, peak, peak_err, sensor, pressure_scale, zero_pressure_peak,
                      temperature_correction=None, fit_function=""):
        """Calculate pressure using PressureCalculator's internal keys.

        `sensor`, `pressure_scale`, and `temperature_correction["scale"]` are
        backend keys such as "ruby", "ruby_shen_2020", and
        "ruby_kobayashi_unpublished", not GUI labels.

        This mirrors PressureCalculatorWindow.calculate() without any widget
        dependency. Temperature correction is performed inside
        PressureCalculator.calculate(); this method only gathers request values
        and formats the API response.
        """
        PressureCalculator.validate_fit_pressure_pair(
            fit_function=fit_function, sensor=sensor, p_scale=pressure_scale
        )

        zero_peak_at_t0 = None
        current_t = 298.15
        t0 = 298.15
        t_scale = None
        temperature_enabled = False
        temperature_warning = None

        if temperature_correction is not None:
            current_t = temperature_correction.get("current_t", current_t)
            t0 = temperature_correction.get("t0", t0)
            zero_peak_at_t0 = temperature_correction.get("zero_pressure_peak_at_t0", zero_pressure_peak)
            temperature_enabled = temperature_correction.get("enabled", False)

            if temperature_enabled:
                t_scale = temperature_correction.get("scale")
                is_valid, rng = PressureCalculator.is_temp_in_range(
                    sensor=sensor, p_scale=pressure_scale, t_scale=t_scale, temp=current_t
                )
                if not is_valid and rng[0] is not None:
                    warning_scale = (
                        pressure_scale
                        if PressureCalculator.pressure_scale_requires_temperature(
                            sensor=sensor, p_scale=pressure_scale
                        )
                        else t_scale
                    )
                    temperature_warning = (
                        f"Temperature {current_t} K is outside the valid range "
                        f"({rng[0]}-{rng[1]} K) for {sensor} / {warning_scale}."
                    )

        t0 = PressureCalculator.resolve_t0(sensor=sensor, p_scale=pressure_scale, t0=t0)
        result = PressureCalculator.calculate(
            sensor=sensor, p_scale=pressure_scale,
            peak=peak, zero_peak=zero_pressure_peak,
            zero_peak_at_t0=zero_peak_at_t0,
            peak_err=peak_err,
            temperature_correction_enabled=temperature_enabled,
            t_scale=t_scale,
            current_t=current_t, t0=t0,
        )

        return {
            "pressure": result.pressure,
            "pressure_err": result.pressure_err,
            "zero_pressure_peak_at_current_t": result.zero_peak_at_current_t,
            "temperature_warning": temperature_warning,
        }

    # ------------------------------------------------------------------
    # More GUI-thread helpers (state mutation / widget reads).
    # ------------------------------------------------------------------

    def api_apply_calibration(self, c0, c1, c2, unit, laser_wavelength_nm=None, label="api"):
        """Must run on the GUI thread (updates the loaded-configuration label via
        FileIOMixin.apply_calibration()).

        Deprecated, but still reachable, so it takes the exclusion gate like every other
        state-changing API route (work_API_standby.md 表#14). Applying a calibration is a
        synchronous scope, so the context manager releases it even on failure.
        """
        with self.acquisition_gate("api"):
            self.apply_calibration(
                (c0, c1, c2), label, calib_unit=unit,
                calib_laser_wl=laser_wavelength_nm,
                axis_source="api_inline_calibration",
            )
            return {
                "applied": True,
                "unit": self.calib_unit,
                "c0": c0, "c1": c1, "c2": c2,
                "label": self.configuration_label,
            }

    def api_get_status(self):
        """Must run on the GUI thread (reads several widgets)."""
        if self.radio_2d.isChecked():
            roi_mode = "2d"
        elif self.radio_1d_full.isChecked():
            roi_mode = "1d_full"
        else:
            roi_mode = "1d_roi"

        configuration_state = self._api_configuration_state()
        return {
            "busy": self._acquisition_gate.locked(),
            "camera_connected": hasattr(self, 'thread') and self.thread.isRunning(),
            "exposure_time_s": self.spin_acq_time.value(),
            "calibration": {
                "applied": self.calib_coeffs is not None,
                "unit": self.calib_unit,
                "label": self.configuration_label,
            },
            "roi": {
                "mode": roi_mode,
                "start": self.spin_vstart.value(),
                "end": self.spin_vend.value(),
            },
            "background": {
                "loaded": self.loaded_bg_data is not None,
                "metadata": self.loaded_bg_metadata,
            },
            **configuration_state,
        }

    # ------------------------------------------------------------------
    # API server lifecycle (GUI thread only - touches widgets/threads).
    # ------------------------------------------------------------------

    def start_api_server(self, host, port):
        """Start the FastAPI server in a background thread.

        Only the server lifecycle lives here; whether the measurement/config UI is
        locked for the whole run is the caller's decision, because that is exactly what
        distinguishes "locked" mode from "standby" (work_API_standby.md 方針1/Step 5).
        """
        # Deferred import: src.api.server imports BackgroundMismatchError from
        # this module, so importing it at module load time here would be a
        # circular import. Importing lazily, inside the method, avoids it.
        from src.api.server import create_app

        self.load_api_client_list()
        self._api_last_port = port
        self._api_accepting = True
        api_app = create_app(
            self, self.gui_bridge,
            expose_docs=bool(self.chk_api_expose_docs.isChecked()),
        )
        config = uvicorn.Config(api_app, host=host, port=port, log_level="info")
        self._api_server = uvicorn.Server(config)
        self._api_server_thread = threading.Thread(target=self._api_server.run, daemon=True)
        self._api_server_thread.start()

    def stop_api_server(self):
        """Stop accepting new requests and let in-flight operations finish (方針6).

        uvicorn's should_exit does not interrupt a running handler, and a hardware
        operation can easily outlive any join() worth blocking the GUI thread for. So
        the switch-off is: refuse new requests immediately (503 via _api_accepting),
        ask uvicorn to exit, then watch the thread from a timer. The server references
        are deliberately kept until the thread has really exited - dropping them while
        the thread lives was the main defect of the previous 5-second join().
        """
        if getattr(self, '_api_server', None) is None:
            self._unlock_ui("api_server")
            self._api_stopping = False
            self._on_api_state_changed()
            return
        self._api_accepting = False
        self._api_stopping = True
        self._api_server.should_exit = True
        self._on_api_state_changed()
        self._api_stop_timer.start(_API_STOP_POLL_MS)
        # Handles the common case (idle server) without waiting for the first tick.
        self._check_api_server_stopped()

    def _check_api_server_stopped(self):
        thread = getattr(self, '_api_server_thread', None)
        if thread is not None and thread.is_alive():
            return
        self._api_stop_timer.stop()
        self._api_server = None
        self._api_server_thread = None
        self._api_stopping = False
        self._unlock_ui("api_server")
        self._on_api_state_changed()

    # ------------------------------------------------------------------
    # Mode management (off / standby / locked).
    # ------------------------------------------------------------------

    def apply_api_mode(self, mode):
        """Bring the server in line with `mode`, without restarting it needlessly.

        standby <-> locked differ only in whether the "api_server" UI lock is held, so
        switching between them leaves the running server (and any in-flight request)
        completely untouched.
        """
        previous = self._api_mode
        self._api_mode = mode
        self._save_local_cache("api_mode", mode)

        running = getattr(self, "_api_server", None) is not None
        if mode == "off":
            if running:
                self.stop_api_server()
            else:
                self._unlock_ui("api_server")
                self._on_api_state_changed()
            return

        if not running:
            self._start_api_server_for_mode(mode)
            return

        if mode == "locked":
            self._lock_ui("api_server")
        elif previous == "locked":
            self._unlock_ui("api_server")
        self._on_api_state_changed()

    def _start_api_server_for_mode(self, mode):
        host = self.combo_api_bind.currentData() or "0.0.0.0"
        port = self.spin_api_port.value()
        try:
            self.start_api_server(host=host, port=port)
        except Exception as exc:
            self._api_failed_to_start(str(exc))
            return
        if mode == "locked":
            self._lock_ui("api_server")
        self._api_start_attempts = 0
        self._api_start_timer.start(_API_START_POLL_MS)
        self._on_api_state_changed()

    def _check_api_server_started(self):
        server = getattr(self, "_api_server", None)
        thread = getattr(self, "_api_server_thread", None)
        if server is None:
            self._api_start_timer.stop()
            return
        if getattr(server, "started", False):
            self._api_start_timer.stop()
            self._on_api_state_changed()
            return
        if thread is not None and not thread.is_alive():
            # uvicorn failed inside its own thread - almost always a port clash.
            self._api_start_timer.stop()
            self._api_failed_to_start(
                f"Could not listen on port {self._api_last_port}. "
                "The port may already be in use by another program."
            )
            return
        self._api_start_attempts += 1
        if self._api_start_attempts >= _API_START_MAX_TICKS:
            # Thread alive but not serving yet. Reverting the mode here would be worse
            # than waiting: a slow start is far more likely than a silent failure that
            # keeps the thread running. Stop watching and say so.
            self._api_start_timer.stop()
            print("Warning: the API server has not reported readiness yet; "
                  "it may still be starting.")
            self._on_api_state_changed()

    def _api_failed_to_start(self, message):
        self._api_start_timer.stop()
        self._api_server = None
        self._api_server_thread = None
        self._api_accepting = False
        self._api_stopping = False
        self._unlock_ui("api_server")
        self._set_api_mode_widget("off")
        self._api_mode = "off"
        self._save_local_cache("api_mode", "off")
        self._on_api_state_changed()
        QMessageBox.critical(
            self, "API server could not start",
            f"The API server did not start, so this machine is NOT listening for "
            f"remote requests.\n\n{message}\n\nThe mode has been set back to Off."
        )

    def _set_api_mode_widget(self, mode):
        index = self.combo_api_mode.findData(mode)
        if index < 0:
            return
        self.combo_api_mode.blockSignals(True)
        self.combo_api_mode.setCurrentIndex(index)
        self.combo_api_mode.blockSignals(False)

    def on_api_mode_changed(self):
        mode = self.combo_api_mode.currentData()
        if mode is None or mode == self._api_mode:
            return
        if "api_active" in self._ui_lock_reasons:
            reply = QMessageBox.question(
                self, "A remote operation is running",
                "An API request is currently operating the instrument.\n\n"
                "Switching the mode stops new requests being accepted, but the "
                "operation already in progress will run to completion - it is not "
                "interrupted.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._set_api_mode_widget(self._api_mode)
                return
        self.apply_api_mode(mode)

    def on_api_bind_changed(self):
        self._save_local_cache(
            "api_bind_host", self.combo_api_bind.currentData() or "0.0.0.0"
        )

    def on_api_unlock_delay_changed(self, seconds):
        self._api_unlock_delay_ms = int(round(float(seconds) * 1000))
        self._save_local_cache("api_unlock_delay_ms", self._api_unlock_delay_ms)

    def maybe_autostart_api_server(self):
        """Resume a persisted standby/locked mode once the hardware has settled.

        Called from both the camera-initialised and camera-failed handlers. The order
        matters and must not be changed: both of those re-enable the central widget
        unconditionally, so starting to listen any earlier would break the UI lock
        (work_API_standby.md 方針2 の除外理由). A request that arrives after a failed
        initialisation is answered with the 503 from Step 3(A).
        """
        if getattr(self, "_api_server", None) is not None:
            return
        if self._api_mode == "off":
            return
        self._start_api_server_for_mode(self._api_mode)

    def _on_api_state_changed(self):
        """Refresh the API panel from the server's actual state."""
        running = getattr(self, '_api_server', None) is not None
        stopping = getattr(self, '_api_stopping', False)
        if running and stopping:
            self.lbl_api_status.setText(
                "Stopping… (the request in progress is allowed to finish)"
            )
        elif running:
            self.lbl_api_status.setText(
                self._build_api_status_text(self._api_last_port)
            )
        else:
            self.lbl_api_status.setText("Not running")
        # Port/bind/docs only take effect at start-up, so they are frozen while the
        # server runs. The mode selector itself always stays live.
        for widget in (self.spin_api_port, self.combo_api_bind, self.chk_api_expose_docs):
            widget.setEnabled(not running)
        self.combo_api_mode.setEnabled(not stopping)
        self._update_remote_active_indicator()

    def _update_remote_active_indicator(self):
        """Light the "Remote control active" marker while a request owns the gate.

        In Standby the controls come and go on their own, so the operator needs to be
        able to tell at a glance that a remote client - not a local glitch - is
        driving the instrument (work_API_standby.md Step 5 手順5).
        """
        label = getattr(self, "lbl_api_remote_active", None)
        if label is None:
            return
        label.setVisible("api_active" in getattr(self, "_ui_lock_reasons", set()))

    # ------------------------------------------------------------------
    # Named API clients.
    # ------------------------------------------------------------------

    def load_api_client_list(self):
        """Load (and cache) the authorised clients as an immutable snapshot.

        src/api/server.py re-reads self._api_clients on every request rather than
        closing over the value captured at server-start time, so an edit takes effect
        on the very next request without restarting the server.
        """
        self._api_clients = tuple(self.load_api_clients())
        return self._api_clients

    def set_api_clients(self, clients):
        """Replace the client list wholesale.

        Rebinding the attribute is atomic, so a worker thread mid-request either sees
        the old tuple or the new one, never a partially edited list - which is why the
        reader needs no lock (see src/core/api_clients.py).
        """
        snapshot = tuple(clients)
        try:
            self.save_api_clients(snapshot)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Could not save API clients",
                "The client changes were not applied because they could not be "
                f"saved safely.\n\n{exc}",
            )
            return False
        self._api_clients = snapshot
        return True

    def on_manage_api_clients_clicked(self):
        from src.ui.menu.api_clients_dialog import ApiClientsDialog

        dialog = ApiClientsDialog(
            self.load_api_client_list(),
            last_seen=self._api_last_seen,
            parent=self,
        )
        if dialog.exec() == dialog.DialogCode.Accepted:
            if self.set_api_clients(dialog.clients):
                self._on_api_state_changed()

    def _build_api_status_text(self, port):
        host = self.combo_api_bind.currentData() or "0.0.0.0"
        shown_host = local_ip_address() if host == "0.0.0.0" else host
        mode_text = {"standby": "Standby", "locked": "Locked"}.get(self._api_mode, "")
        lines = [f"{mode_text}: listening at http://{shown_host}:{port}"]
        last = getattr(self, "_api_last_request", None)
        if last is not None:
            lines.append(
                f"Last request: {last['client']} ({last['ip']}) {last['time']}"
            )
        lines.append("Keys are managed under API → Manage Clients.")
        return "\n".join(lines)

    def _refresh_last_request_label(self):
        """Lightweight refresh used by note_api_request(): only the "Last request"
        text can have changed just because a request came in - running/stopping and
        the widget-enabled state _on_api_state_changed() also touches cannot - so this
        skips that work instead of redoing it several times a second in Standby mode.
        It also skips _update_remote_active_indicator(): that indicator only changes
        when the lock itself changes, which _lock_ui()/_unlock_ui() already handle.
        """
        if getattr(self, '_api_server', None) is not None and not getattr(
            self, '_api_stopping', False
        ):
            self.lbl_api_status.setText(
                self._build_api_status_text(self._api_last_port)
            )

    def note_api_request(self, client_name, client_ip):
        """Record who last called, for the panel's "Last request" line.

        Called from an API worker thread, so it updates plain data and queues the
        widget refresh on the GUI thread.
        """
        self._api_last_request = {
            "client": client_name or "unknown",
            "ip": client_ip or "unknown",
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        self._api_last_seen.record(client_name or "unknown", client_ip)
        post = getattr(self.gui_bridge, "post", None)
        if post is not None:
            # Authentication runs in a FastAPI worker. Queue the label refresh instead
            # of touching Qt widgets here or blocking every request on GuiBridge.call().
            post(self._refresh_last_request_label)
