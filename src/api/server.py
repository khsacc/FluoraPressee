from concurrent.futures import TimeoutError as FutureTimeoutError

import numpy as np
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request

from src.api.schemas import (
    AcquireFitRequest,
    AcquireFitResponse,
    AcquirePressureRequest,
    AcquirePressureResponse,
    AcquireRequest,
    AcquireResponse,
    ApplyConfigurationRequest,
    ApplyConfigurationResponse,
    CalibrationRequest,
    CalibrationResponse,
    CameraInfoResponse,
    ConfigResponse,
    ConfigurationListResponse,
    ConfigurationRecordResponse,
    ResolveConfigurationsRequest,
    ResolveConfigurationsResponse,
    SpectrometerInfoResponse,
    StatusResponse,
)
from src.core.configuration_catalog import (
    AmbiguousConfigurationProfileError,
    ConfigurationCompatibilityError,
    ConfigurationError,
)
from src.core.api_clients import find_client, ip_allowed
from src.core.pressureCalc import PressureCalculator
from src.ui.ui_mixins.acquisition_mixin import GateBusyError
from src.ui.ui_mixins.api_mixin import (
    BackgroundMismatchError,
    CameraNotReadyError,
    ExposureApplyError,
    StateTokenMismatchError,
)


def _to_list(arr):
    return arr.tolist() if arr is not None else None


def _jsonify(obj):
    """Recursively convert numpy arrays/scalars to plain Python types.

    DataAnalyzer.fit_spectrum() and PressureCalculator.calculate() return
    numpy.float64 scalars (from curve_fit/formulas) and, for double-peak fits,
    numpy.ndarray curves (y_fit1/y_fit2) - none of which Pydantic/FastAPI's
    JSON encoder can serialise directly.
    """
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    return obj


def create_app(gui_window, gui_bridge, expose_docs: bool = False) -> FastAPI:
    """Build the FastAPI app exposing ApiMixin's methods over HTTP.

    All routes here run as plain (non-async) functions so Starlette executes
    them in its worker threadpool, never on the GUI thread or the asyncio
    event loop thread - this is required both because GuiBridge.call() refuses
    to be invoked from the GUI thread, and because an async handler would
    block uvicorn's single event loop for the whole duration of a blocking
    acquisition, stalling every other concurrent request.

    `expose_docs` is off by default: the key check is a router dependency, so
    FastAPI's own /docs, /redoc and /openapi.json are not behind it and would
    otherwise describe the whole instrument API to anyone who can reach the port.
    """
    docs_kwargs = (
        {} if expose_docs
        else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )
    app = FastAPI(title="FluoRaPressée API", **docs_kwargs)

    def ensure_accepting():
        """Refuse new requests the moment the operator switches the server off.

        uvicorn's should_exit only stops the accept loop; it neither interrupts a
        running handler nor rejects a request already queued. Turning the server off
        means "stop taking new work", so that rejection happens here, ahead of the key
        check (work_API_standby.md 方針6).
        """
        if not getattr(gui_window, "_api_accepting", True):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "shutting_down",
                    "message": "The API server is shutting down and is not accepting "
                               "new requests.",
                },
            )

    def verify_api_key(
        request: Request, x_api_key: str | None = Header(default=None)
    ):
        """Identify the calling client by key, then check where it called from.

        Reads gui_window._api_clients live on every request (rather than closing over
        the value captured at server-start time) so adding or revoking a client in the
        Manage Clients dialog takes effect on the very next request.

        Use Header(default=None) rather than a required Header(...) so a missing
        header and a wrong one both resolve to the same 401, instead of FastAPI's
        default 422 for a missing required header.
        """
        clients = getattr(gui_window, "_api_clients", ())
        client = find_client(clients, x_api_key)
        if client is None:
            raise HTTPException(
                status_code=401, detail="Invalid or missing X-API-Key header"
            )

        host = request.client.host if request.client is not None else None
        if not ip_allowed(client, host):
            # Deliberately says the key is valid but the address is not. That does
            # leak "this key exists" to whoever holds it, but on a lab LAN the ability
            # to tell a wrong key from a wrong address is worth far more than hiding
            # it: without the distinction a mis-set allow-list is indistinguishable
            # from a mistyped key (work_API_standby.md 方針7).
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ip_not_allowed",
                    "message": (
                        f"Client {client['name']!r} is not authorised from "
                        f"{host or 'an unknown address'}."
                    ),
                },
            )

        request.state.api_client = client["name"]
        record = getattr(gui_window, "note_api_request", None)
        if record is not None:
            record(client["name"], host)

    # Order matters: a shutting-down server answers 503 regardless of the key.
    router = APIRouter(dependencies=[Depends(ensure_accepting), Depends(verify_api_key)])

    def _run_acquire(req: AcquireRequest) -> dict:
        try:
            return gui_window.api_acquire(
                exposure_s=req.exposure_time_s,
                accumulations=req.accumulations,
                dark_mode=req.dark.mode,
                dark_data=req.dark.data,
                ignore_mismatch=req.dark.ignore_mismatch,
                configuration_id=req.configuration_id,
                axis_mode=req.axis_mode or "calibrated",
                expected_state_token=req.expected_state_token,
            )
        except ConfigurationCompatibilityError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "configuration_incompatible",
                    "message": "Configuration is incompatible with the connected hardware.",
                    "reasons": e.reasons,
                },
            )
        except ConfigurationError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except CameraNotReadyError as e:
            # 503, not 4xx: the instrument is temporarily absent, and nothing about the
            # request itself is wrong. Applies to /acquire, /acquire/fit and
            # /acquire/pressure alike, since all three come through here.
            raise HTTPException(
                status_code=503,
                detail={"code": "camera_not_ready", "message": str(e)},
            )
        except StateTokenMismatchError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "state_token_mismatch",
                    "message": str(e),
                    "expected_state_token": e.expected,
                    "instrument_state_token": e.current,
                },
            )
        except GateBusyError as e:
            raise HTTPException(status_code=409, detail=e.detail)
        except RuntimeError as e:
            if str(e) in {"acquisition busy", "instrument busy"}:
                raise HTTPException(status_code=409, detail=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        except BackgroundMismatchError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ExposureApplyError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FutureTimeoutError:
            raise HTTPException(
                status_code=504,
                detail="Configuration apply or acquisition timed out",
            )

    def _run_hardware_info(fn, device_name):
        try:
            return fn()
        except GateBusyError as e:
            raise HTTPException(status_code=409, detail=e.detail)
        except RuntimeError as e:
            if str(e) == "instrument busy":
                raise HTTPException(status_code=409, detail=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        except FutureTimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"{device_name} status refresh timed out",
            )

    def _acquire_response_payload(result: dict) -> dict:
        payload = {
            "x": _to_list(result["x"]),
            "y_raw": _to_list(result["y_raw"]),
            "y": _to_list(result["y"]),
            "mode": result["mode"],
            "exposure_time_s": result["exposure_time_s"],
            "accumulations": result["accumulations"],
            "detector_temperature_c": result["detector_temperature_c"],
            "timestamp": result["timestamp"],
            "configuration": result["configuration"],
            "hardware_state": result["hardware_state"],
            "x_axis": result["x_axis"],
            "instrument_state_token": result.get("instrument_state_token"),
        }
        if "background_mismatch_warning" in result:
            payload["background_mismatch_warning"] = result["background_mismatch_warning"]
        return payload

    def _fit_payload(req: AcquireFitRequest, result: dict) -> dict:
        if result["mode"] != "1d":
            raise HTTPException(
                status_code=400,
                detail="Fitting is not supported for 2D (image) acquisitions; "
                       "switch the GUI to a 1D mode before using /acquire/fit or /acquire/pressure.",
            )
        fit_start, fit_end = (req.fit_range.start, req.fit_range.end) if req.fit_range else (None, None)
        fit_result = gui_window.api_fit(
            result["x"], result["y"], req.fit_function, fit_start=fit_start, fit_end=fit_end,
            fit_peak_count=req.fit_peak_count, peak_sort_order=req.peak_sort_order,
            baseline_model=req.baseline_model
        )
        return {
            "success": fit_result["success"],
            "x_fit": _to_list(fit_result["x_fit"]),
            "y_fit": _to_list(fit_result["y_fit"]),
            "fit": _jsonify(fit_result["fit"]),
        }

    @router.get("/status", response_model=StatusResponse)
    def get_status():
        return gui_bridge.call(gui_window.api_get_status)

    @router.get("/hardware/camera", response_model=CameraInfoResponse)
    def get_camera_info(refresh: bool = False):
        return _run_hardware_info(
            lambda: gui_window.api_get_camera_info(refresh=refresh),
            "Camera",
        )

    @router.get("/hardware/spectrometer", response_model=SpectrometerInfoResponse)
    def get_spectrometer_info(refresh: bool = False):
        return _run_hardware_info(
            lambda: gui_window.api_get_spectrometer_info(refresh=refresh),
            "Spectrometer",
        )

    @router.get("/config", response_model=ConfigResponse)
    def get_config():
        return gui_bridge.call(gui_window.api_get_config)

    @router.get("/configurations", response_model=ConfigurationListResponse)
    def get_configurations(
        active_only: bool = True,
        include_incompatible: bool = False,
        limit: int = 100,
        offset: int = 0,
    ):
        try:
            return gui_window.api_list_configurations(
                active_only=active_only,
                include_incompatible=include_incompatible,
                limit=limit,
                offset=offset,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post(
        "/configurations/resolve", response_model=ResolveConfigurationsResponse
    )
    def resolve_configurations(req: ResolveConfigurationsRequest):
        try:
            return gui_window.api_resolve_configurations(req.slot_ids)
        except ConfigurationCompatibilityError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "configuration_incompatible",
                    "message": "A configuration is incompatible with the connected hardware.",
                    "reasons": e.reasons,
                },
            )
        except AmbiguousConfigurationProfileError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ambiguous_configuration_profile",
                    "message": str(e),
                },
            )
        except ConfigurationError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.get(
        "/configurations/{configuration_id}",
        response_model=ConfigurationRecordResponse,
    )
    def get_configuration(configuration_id: str):
        try:
            return gui_window.api_get_configuration(configuration_id)
        except ConfigurationError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post(
        "/configurations/{configuration_id}/apply",
        response_model=ApplyConfigurationResponse,
    )
    def apply_configuration(
        configuration_id: str,
        req: ApplyConfigurationRequest = ApplyConfigurationRequest(),
    ):
        try:
            return gui_window.api_apply_configuration(
                configuration_id, axis_mode=req.axis_mode
            )
        except ConfigurationCompatibilityError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "configuration_incompatible",
                    "message": "Configuration is incompatible with the connected hardware.",
                    "reasons": e.reasons,
                },
            )
        except ConfigurationError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except GateBusyError as e:
            raise HTTPException(status_code=409, detail=e.detail)
        except RuntimeError as e:
            if str(e) == "instrument busy":
                raise HTTPException(status_code=409, detail=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        except FutureTimeoutError:
            raise HTTPException(status_code=504, detail="Configuration apply timed out")

    @router.post(
        "/calibration", response_model=CalibrationResponse, deprecated=True
    )
    def post_calibration(req: CalibrationRequest):
        try:
            return gui_bridge.call(lambda: gui_window.api_apply_calibration(
                req.c0, req.c1, req.c2, req.unit,
                laser_wavelength_nm=req.laser_wavelength_nm, label=req.label,
            ))
        except GateBusyError as e:
            raise HTTPException(status_code=409, detail=e.detail)
        except ConfigurationCompatibilityError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "configuration_incompatible",
                    "message": "Calibration is incompatible with the current excitation wavelength.",
                    "reasons": e.reasons,
                },
            )

    @router.post("/acquire", response_model=AcquireResponse)
    def post_acquire(req: AcquireRequest):
        result = _run_acquire(req)
        return _acquire_response_payload(result)

    @router.post("/acquire/fit", response_model=AcquireFitResponse)
    def post_acquire_fit(req: AcquireFitRequest):
        result = _run_acquire(req)
        payload = _acquire_response_payload(result)
        payload["fit"] = _fit_payload(req, result)
        return payload

    @router.post("/acquire/pressure", response_model=AcquirePressureResponse)
    def post_acquire_pressure(req: AcquirePressureRequest):
        result = _run_acquire(req)
        payload = _acquire_response_payload(result)
        if result["configuration"]["axis_mode"] != "calibrated":
            raise HTTPException(
                status_code=400,
                detail="Pressure calculation requires a calibrated axis.",
            )
        # axis_mode=="calibrated" only means *some* calibration is active; it does not
        # guarantee its unit matches the requested sensor (e.g. a Wavelength calibration
        # active while a Raman-shift sensor is requested), which would otherwise still
        # produce a number by feeding an nm peak position into a cm-1 formula.
        sensor_unit = PressureCalculator.SENSORS.get(req.sensor, {}).get("unit")
        axis_unit = result.get("x_axis", {}).get("unit")
        if sensor_unit is not None and axis_unit is not None and sensor_unit != axis_unit:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Sensor {req.sensor!r} expects a {sensor_unit} axis, but the "
                    f"active calibration's axis is {axis_unit}."
                ),
            )
        fit_payload = _fit_payload(req, result)
        payload["fit"] = fit_payload

        fit_res = fit_payload["fit"]
        if not fit_payload["success"] or fit_res is None:
            payload["pressure_gpa"] = None
            payload["pressure_err_gpa"] = None
            payload["zero_pressure_peak_at_current_t"] = None
            payload["temperature_warning"] = None
            return payload

        peaks = fit_res.get("peaks") or []
        peak_idx = req.pressure_peak_index - 1
        if peak_idx < 0 or peak_idx >= len(peaks):
            payload["pressure_gpa"] = None
            payload["pressure_err_gpa"] = None
            payload["zero_pressure_peak_at_current_t"] = None
            payload["temperature_warning"] = None
            return payload
        peak = peaks[peak_idx]["position"]
        peak_err = peaks[peak_idx]["position_err"]

        temperature_correction = (
            req.temperature_correction.model_dump() if req.temperature_correction else None
        )
        pressure_result = gui_window.api_pressure(
            peak, peak_err, req.sensor, req.pressure_scale, req.zero_pressure_peak,
            temperature_correction=temperature_correction,
            fit_function=req.fit_function,
        )
        payload["pressure_gpa"] = _jsonify(pressure_result["pressure"])
        payload["pressure_err_gpa"] = _jsonify(pressure_result["pressure_err"])
        payload["zero_pressure_peak_at_current_t"] = _jsonify(
            pressure_result["zero_pressure_peak_at_current_t"]
        )
        payload["temperature_warning"] = pressure_result["temperature_warning"]
        return payload

    app.include_router(router)
    return app
