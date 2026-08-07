import threading
import unittest

import numpy as np

from src.core.configuration_catalog import (
    AmbiguousConfigurationProfileError,
    ConfigurationCompatibilityError,
)

try:
    from pydantic import ValidationError

    from src.api.schemas import (
        AcquirePressureRequest,
        AcquireRequest,
        CalibrationRequest,
        SlotResolutionRequest,
    )
except (ImportError, ModuleNotFoundError):
    ValidationError = None
    AcquirePressureRequest = None
    AcquireRequest = None
    CalibrationRequest = None
    SlotResolutionRequest = None

try:
    from fastapi import HTTPException

    from src.api.schemas import (
        ApplyConfigurationRequest,
        ResolveConfigurationsRequest,
    )
    from src.api.server import create_app
except (ImportError, ModuleNotFoundError):
    HTTPException = None
    ApplyConfigurationRequest = None
    ResolveConfigurationsRequest = None
    create_app = None

try:
    from src.ui.ui_mixins.api_mixin import ApiMixin
except (ImportError, ModuleNotFoundError):
    ApiMixin = None


class DirectBridge:
    def call(self, fn):
        return fn()


class FakeGui:
    _api_key = "test-key"

    def __init__(self):
        self.last_acquire = None

    def api_get_status(self):
        return {
            "busy": False,
            "camera_connected": True,
            "exposure_time_s": 0.1,
            "calibration": {"applied": True, "unit": "Wavelength", "label": "test"},
            "roi": {"mode": "1d_roi", "start": 45, "end": 65},
            "background": {"loaded": False, "metadata": None},
            "configuration": {
                "configuration_id": "cfg-1",
                "slot_id": "slot-1",
                "axis_mode": "calibrated",
                "calibration_applied": True,
                "unit": "Wavelength",
            },
            "hardware_state": {
                "grating_index": 1,
                "grooves_per_mm": 600,
                "actual_center_wavelength_nm": 690.0,
                "roi_mode": "1d_roi",
                "roi_start": 45,
                "roi_end": 65,
            },
        }

    def api_list_configurations(self, **kwargs):
        return {
            "catalog_revision": 7,
            "items": [{"configuration_id": "cfg-1", "slot_id": "slot-1"}],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }

    def api_get_configuration(self, configuration_id):
        return {
            "catalog_revision": 7,
            "configuration": {"configuration_id": configuration_id},
            "compatible": True,
            "incompatibility_reasons": [],
        }

    def api_resolve_configurations(self, slot_ids):
        self.last_resolve_slot_ids = slot_ids
        resolved = []
        for index, entry in enumerate(slot_ids, start=1):
            slot_id = entry if isinstance(entry, str) else entry.slot_id
            if slot_id == "ambiguous-slot":
                raise AmbiguousConfigurationProfileError(
                    f"Slot {slot_id} has multiple active calibration profiles"
                )
            resolved.append({"slot_id": slot_id, "configuration_id": f"cfg-{index}"})
        return {"catalog_revision": 7, "resolved": resolved}

    def api_apply_configuration(self, configuration_id, axis_mode="calibrated"):
        return {
            "applied": True,
            "configuration_id": configuration_id,
            "slot_id": "slot-1",
            "display_label": "600 g/mm | 690.000 nm | ROI 45–65",
            "configuration": {
                "configuration_id": configuration_id,
                "slot_id": "slot-1",
                "axis_mode": axis_mode,
                "calibration_applied": axis_mode == "calibrated",
                "unit": "Wavelength" if axis_mode == "calibrated" else "pixel",
            },
            "hardware_state": {
                "grating_index": 1,
                "grooves_per_mm": 600,
                "actual_center_wavelength_nm": 690.0,
                "roi_mode": "1d_roi",
                "roi_start": 45,
                "roi_end": 65,
            },
        }

    def api_apply_calibration(self, c0, c1, c2, unit, laser_wavelength_nm=None, label="api"):
        if unit == "Raman shift" and laser_wavelength_nm == 999.0:
            raise ConfigurationCompatibilityError([
                "Excitation wavelength does not match: this calibration was "
                "taken at 999.000 nm, but the excitation wavelength is "
                "currently set to 633.000 nm. Set the excitation wavelength "
                "to the calibrated value first."
            ])
        return {"applied": True, "unit": unit, "c0": c0, "c1": c1, "c2": c2, "label": label}

    def api_acquire(self, **kwargs):
        self.last_acquire = kwargs
        configuration_id = kwargs["configuration_id"]
        axis_mode = kwargs["axis_mode"] if configuration_id else "calibrated"
        return {
            "x": np.asarray([690.0, 690.1]),
            "y_raw": np.asarray([10.0, 20.0]),
            "y": np.asarray([10.0, 20.0]),
            "mode": "1d",
            "exposure_time_s": 0.1,
            "accumulations": 1,
            "detector_temperature_c": -65.0,
            "timestamp": "2026-07-22T00:00:00+09:00",
            "configuration": {
                "configuration_id": configuration_id,
                "slot_id": "slot-1" if configuration_id else None,
                "axis_mode": axis_mode,
                "calibration_applied": axis_mode == "calibrated",
                "unit": "Wavelength" if axis_mode == "calibrated" else "pixel",
            },
            "hardware_state": {
                "grating_index": 1,
                "grooves_per_mm": 600,
                "actual_center_wavelength_nm": 690.0,
                "roi_mode": "1d_roi",
                "roi_start": 45,
                "roi_end": 65,
            },
            "x_axis": {
                "source": axis_mode,
                "unit": "nm" if axis_mode == "calibrated" else None,
                "calibrated": axis_mode == "calibrated",
            },
        }


class BusyConfigurationHarness(ApiMixin if ApiMixin is not None else object):
    """Model another operation holding the process-wide acquisition gate."""

    def __init__(self):
        self.gui_bridge = DirectBridge()
        self._acquisition_gate = threading.Lock()
        self._acquisition_gate.acquire()
        # This deliberately reproduces the current unscoped ownership flag:
        # the busy operation set it before this API call began.
        self._gate_held_by_me = True

    def _api_validate_configuration(self, configuration_id):
        return {
            "configuration_id": configuration_id,
            "slot_id": "slot-1",
        }

    def _instrument_status_busy(self):
        return True

    def _release_acquisition_gate(self):
        if self._gate_held_by_me:
            self._gate_held_by_me = False
            self._acquisition_gate.release()


@unittest.skipIf(create_app is None, "FastAPI test dependencies are unavailable")
class ConfigurationApiTests(unittest.TestCase):
    def setUp(self):
        self.gui = FakeGui()
        self.app = create_app(self.gui, DirectBridge())

    def endpoint(self, path, method):
        pending = list(self.app.routes)
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

    def test_acquire_configuration_is_optional(self):
        response = self.endpoint("/acquire", "POST")(AcquireRequest())

        self.assertIsNone(self.gui.last_acquire["configuration_id"])
        self.assertEqual(self.gui.last_acquire["axis_mode"], "calibrated")
        self.assertIsNone(response["configuration"]["configuration_id"])

    def test_explicit_configuration_and_pixel_mode_are_forwarded(self):
        response = self.endpoint("/acquire", "POST")(
            AcquireRequest(configuration_id="cfg-1", axis_mode="pixel")
        )

        self.assertEqual(self.gui.last_acquire["configuration_id"], "cfg-1")
        self.assertEqual(self.gui.last_acquire["axis_mode"], "pixel")
        self.assertEqual(response["configuration"]["unit"], "pixel")

    def test_acquire_response_includes_x_axis(self):
        # Regression test: _acquire_response_payload() in src/api/server.py used to omit the
        # "x_axis" key entirely (only "configuration"/"hardware_state" were copied from
        # api_acquire()'s return value), so AcquireResponse.x_axis was always null over HTTP
        # even though ApiMixin._api_configuration_state() always computes it -
        # see work/work_OceanOptics.md review round 5.
        response = self.endpoint("/acquire", "POST")(AcquireRequest())

        self.assertEqual(
            response["x_axis"],
            {"source": "calibrated", "unit": "nm", "calibrated": True},
        )

    def test_catalog_discovery_resolve_get_and_apply(self):
        listing = self.endpoint("/configurations", "GET")()
        resolved = self.endpoint("/configurations/resolve", "POST")(
            ResolveConfigurationsRequest(slot_ids=["slot-1", "slot-2"])
        )
        record = self.endpoint("/configurations/{configuration_id}", "GET")(
            "cfg-1"
        )
        applied = self.endpoint(
            "/configurations/{configuration_id}/apply", "POST"
        )("cfg-1")

        self.assertEqual(listing["catalog_revision"], 7)
        self.assertEqual(len(resolved["resolved"]), 2)
        self.assertEqual(record["configuration"]["configuration_id"], "cfg-1")
        self.assertTrue(applied["configuration"]["calibration_applied"])

    def test_resolve_accepts_an_explicit_axis_kind_entry(self):
        resolved = self.endpoint("/configurations/resolve", "POST")(
            ResolveConfigurationsRequest(
                slot_ids=[
                    "slot-1",
                    {
                        "slot_id": "slot-2",
                        "axis_kind": "raman_shift",
                        "excitation_wavelength_nm": 532.0,
                    },
                ]
            )
        )
        self.assertEqual(len(resolved["resolved"]), 2)
        self.assertEqual(self.gui.last_resolve_slot_ids[1].slot_id, "slot-2")

    def test_resolve_ambiguous_slot_returns_409(self):
        with self.assertRaises(HTTPException) as raised:
            self.endpoint("/configurations/resolve", "POST")(
                ResolveConfigurationsRequest(slot_ids=["ambiguous-slot"])
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["code"], "ambiguous_configuration_profile"
        )

    def test_acquire_pressure_rejects_sensor_axis_unit_mismatch(self):
        # FakeGui.api_acquire() reports x_axis.unit="nm" for a calibrated axis, but
        # this sensor expects cm-1 -- axis_mode=="calibrated" alone must not be
        # enough to let this through (see server.py's post_acquire_pressure()).
        with self.assertRaises(HTTPException) as raised:
            self.endpoint("/acquire/pressure", "POST")(
                AcquirePressureRequest(
                    configuration_id="cfg-1",
                    axis_mode="calibrated",
                    fit_function="Pseudo Voigt",
                    sensor="diamond_13c_1st_order",
                    pressure_scale="diamond_13c_schiferl_1997",
                    zero_pressure_peak=1287.79,
                )
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_post_calibration_rejects_excitation_mismatch(self):
        # Regression test: the deprecated inline POST /calibration route had no
        # exception handling at all, so apply_calibration()'s new excitation-
        # mismatch check would have surfaced as an unhandled 500 rather than a
        # clean 409 like every other ConfigurationCompatibilityError.
        with self.assertRaises(HTTPException) as raised:
            self.endpoint("/calibration", "POST")(
                CalibrationRequest(
                    c0=0.0, c1=1.0, c2=0.0,
                    unit="Raman shift", laser_wavelength_nm=999.0,
                )
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["code"], "configuration_incompatible"
        )

    def test_post_calibration_accepts_a_matching_request(self):
        response = self.endpoint("/calibration", "POST")(
            CalibrationRequest(c0=694.2, c1=0.0153, c2=0.0, unit="Wavelength")
        )
        self.assertTrue(response["applied"])

    def test_openapi_contains_configuration_routes(self):
        paths = self.app.openapi()["paths"]
        self.assertIn("/configurations", paths)
        self.assertIn("/configurations/resolve", paths)
        self.assertIn("/configurations/{configuration_id}/apply", paths)
        acquire_schema = paths["/acquire"]["post"]["requestBody"]["content"]
        acquire_schema = acquire_schema["application/json"]["schema"]
        if "$ref" in acquire_schema:
            schema_name = acquire_schema["$ref"].rsplit("/", 1)[-1]
            acquire_schema = self.app.openapi()["components"]["schemas"][schema_name]
        self.assertIn("configuration_id", acquire_schema["properties"])
        apply_operation = paths["/configurations/{configuration_id}/apply"]["post"]
        self.assertFalse(apply_operation.get("requestBody", {}).get("required", False))


@unittest.skipIf(ApiMixin is None, "GUI API dependencies are unavailable")
class ConfigurationGateOwnershipTests(unittest.TestCase):
    def test_apply_does_not_release_another_operations_gate_when_busy(self):
        gui = BusyConfigurationHarness()

        with self.assertRaisesRegex(RuntimeError, "instrument busy"):
            gui.api_apply_configuration("cfg-1")

        self.assertTrue(gui._acquisition_gate.locked())
        self.assertTrue(gui._gate_held_by_me)

    def test_acquire_with_configuration_does_not_release_busy_gate(self):
        gui = BusyConfigurationHarness()

        with self.assertRaisesRegex(RuntimeError, "instrument busy"):
            gui.api_acquire(configuration_id="cfg-1")

        self.assertTrue(gui._acquisition_gate.locked())
        self.assertTrue(gui._gate_held_by_me)


class ConfigurationApiSchemaTests(unittest.TestCase):
    @unittest.skipIf(AcquireRequest is None, "Pydantic is unavailable")
    def test_axis_mode_is_only_valid_with_configuration(self):
        self.assertIsNone(AcquireRequest().configuration_id)
        self.assertIsNone(AcquireRequest().axis_mode)
        self.assertIsNone(AcquireRequest().accumulations)
        with self.assertRaises(ValueError):
            AcquireRequest(axis_mode="pixel")

    @unittest.skipIf(SlotResolutionRequest is None, "Pydantic is unavailable")
    def test_raman_shift_requires_excitation_wavelength(self):
        with self.assertRaises(ValidationError):
            SlotResolutionRequest(slot_id="slot-2", axis_kind="raman_shift")

    @unittest.skipIf(SlotResolutionRequest is None, "Pydantic is unavailable")
    def test_raman_shift_rejects_non_positive_excitation_wavelength(self):
        with self.assertRaises(ValidationError):
            SlotResolutionRequest(
                slot_id="slot-2", axis_kind="raman_shift", excitation_wavelength_nm=0.0
            )

    @unittest.skipIf(SlotResolutionRequest is None, "Pydantic is unavailable")
    def test_wavelength_forbids_excitation_wavelength(self):
        # Regression test: previously excitation_wavelength_nm was silently
        # ignored here instead of rejected, which could mask a caller bug.
        with self.assertRaises(ValidationError):
            SlotResolutionRequest(
                slot_id="slot-2", axis_kind="wavelength", excitation_wavelength_nm=532.0
            )

    @unittest.skipIf(SlotResolutionRequest is None, "Pydantic is unavailable")
    def test_bare_axis_kind_forbids_excitation_wavelength(self):
        # Regression test: an excitation wavelength with no axis_kind used to be
        # silently dropped, resolving as a bare slot_id -- which only works when
        # the slot happens to have exactly one active profile, and ignores which
        # profile the caller actually meant to select.
        with self.assertRaises(ValidationError):
            SlotResolutionRequest(slot_id="slot-2", excitation_wavelength_nm=532.0)

    @unittest.skipIf(SlotResolutionRequest is None, "Pydantic is unavailable")
    def test_raman_shift_with_valid_excitation_wavelength_is_accepted(self):
        request = SlotResolutionRequest(
            slot_id="slot-2", axis_kind="raman_shift", excitation_wavelength_nm=532.0
        )
        self.assertEqual(request.excitation_wavelength_nm, 532.0)

    @unittest.skipIf(CalibrationRequest is None, "Pydantic is unavailable")
    def test_calibration_request_rejects_non_finite_coefficients(self):
        # Regression test: the deprecated inline POST /calibration route does not
        # go through configuration_catalog's finite-value validation at all, so
        # nan/inf coefficients used to be accepted and applied directly.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValidationError):
                CalibrationRequest(c0=bad, c1=0.02, c2=0.0, unit="Wavelength")

    @unittest.skipIf(CalibrationRequest is None, "Pydantic is unavailable")
    def test_calibration_request_rejects_non_positive_laser_wavelength(self):
        with self.assertRaises(ValidationError):
            CalibrationRequest(
                c0=1.0, c1=0.02, c2=0.0, unit="Raman shift", laser_wavelength_nm=-532.0
            )

    @unittest.skipIf(CalibrationRequest is None, "Pydantic is unavailable")
    def test_calibration_request_rejects_non_finite_laser_wavelength(self):
        with self.assertRaises(ValidationError):
            CalibrationRequest(
                c0=1.0, c1=0.02, c2=0.0, unit="Raman shift",
                laser_wavelength_nm=float("inf"),
            )

    @unittest.skipIf(CalibrationRequest is None, "Pydantic is unavailable")
    def test_calibration_request_accepts_valid_values(self):
        request = CalibrationRequest(
            c0=694.2, c1=0.0153, c2=0.0, unit="Raman shift", laser_wavelength_nm=532.0
        )
        self.assertEqual(request.laser_wavelength_nm, 532.0)


if __name__ == "__main__":
    unittest.main()
