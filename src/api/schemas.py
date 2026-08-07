import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.core.pressureCalc import PressureCalculator


class CalibrationRequest(BaseModel):
    c0: float = Field(allow_inf_nan=False)
    c1: float = Field(allow_inf_nan=False)
    c2: float = Field(allow_inf_nan=False)
    unit: Literal["Wavelength", "Raman shift"]
    laser_wavelength_nm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    label: str = "api"

    @model_validator(mode="after")
    def _require_laser_wavelength_for_raman(self):
        if self.unit == "Raman shift" and self.laser_wavelength_nm is None:
            raise ValueError('laser_wavelength_nm is required when unit="Raman shift"')
        if self.unit == "Wavelength" and self.laser_wavelength_nm is not None:
            raise ValueError('laser_wavelength_nm must not be set when unit="Wavelength"')
        return self


class CalibrationResponse(BaseModel):
    applied: bool
    unit: str
    c0: float
    c1: float
    c2: float
    label: str


class DarkOptions(BaseModel):
    mode: Literal["none", "reuse_loaded", "provided"] = "none"
    data: list[float] | None = None
    ignore_mismatch: bool = False

    @model_validator(mode="after")
    def _require_data_for_provided(self):
        if self.mode == "provided" and self.data is None:
            raise ValueError('dark.data is required when dark.mode="provided"')
        return self


class AcquireRequest(BaseModel):
    configuration_id: str | None = None
    axis_mode: Literal["calibrated", "pixel"] | None = None
    exposure_time_s: float | None = None
    accumulations: int | None = Field(default=None, ge=1)
    dark: DarkOptions = Field(default_factory=DarkOptions)

    @model_validator(mode="after")
    def _axis_mode_belongs_to_explicit_configuration(self):
        if self.axis_mode is not None and self.configuration_id is None:
            raise ValueError("axis_mode can only be set with configuration_id")
        return self


class FitRange(BaseModel):
    start: float
    end: float


class AcquireFitRequest(AcquireRequest):
    fit_function: Literal["Pseudo Voigt", "Moffat", "Gauss", "Lorentz", "Diamond Raman Edge"]
    fit_peak_count: int = Field(default=2, ge=1, le=5)
    peak_sort_order: Literal["x_desc", "x_asc", "intensity_desc", "intensity_asc"] = "x_desc"
    baseline_model: Literal["constant", "linear", "quadratic", "auto_polynomial"] = "constant"
    fit_range: FitRange | None = None

    @model_validator(mode="after")
    def _edge_fit_uses_one_result(self):
        if self.fit_function == "Diamond Raman Edge" and self.fit_peak_count != 1:
            raise ValueError("Diamond Raman Edge fitting requires fit_peak_count=1")
        return self


class TemperatureCorrection(BaseModel):
    enabled: bool
    scale: str
    current_t: float
    t0: float
    zero_pressure_peak_at_t0: float


class AcquirePressureRequest(AcquireFitRequest):
    sensor: str
    pressure_scale: str
    zero_pressure_peak: float
    pressure_peak_index: int = Field(default=1, ge=1, le=5)
    temperature_correction: TemperatureCorrection | None = None

    @model_validator(mode="after")
    def _pressure_peak_must_exist_in_fit(self):
        if self.pressure_peak_index > self.fit_peak_count:
            raise ValueError("pressure_peak_index must be less than or equal to fit_peak_count")
        PressureCalculator.validate_fit_pressure_pair(
            fit_function=self.fit_function,
            sensor=self.sensor,
            p_scale=self.pressure_scale,
        )
        return self


# ----------------------------------------------------------------------
# Response models. x/y_raw/y are left as untyped `list` (rather than
# list[float]) because a 2D (image-mode) acquisition returns a nested
# list[list[float]] for y_raw/y and None for x.
# ----------------------------------------------------------------------

class AcquireResponse(BaseModel):
    x: list | None = None
    y_raw: list
    y: list
    mode: Literal["1d", "2d"]
    exposure_time_s: float
    accumulations: int
    detector_temperature_c: float | None = None
    timestamp: str
    configuration: dict[str, Any]
    hardware_state: dict[str, Any]
    background_mismatch_warning: bool | None = None
    # {"source": "pixel"|"native_wavelength"|"calibrated", "unit": "nm"|"cm-1"|None,
    #  "calibrated": bool} - same vocabulary and single source of truth
    # (measurement_metadata.public_axis_kind/public_axis_unit) as configuration.axis_mode.
    # "calibrated": false means no FluoRaPressée calibration is applied; it does not imply
    # the axis itself is meaningless (Ocean Optics' native_wavelength is factory-calibrated).
    x_axis: dict[str, Any] | None = None


class FitResult(BaseModel):
    success: bool
    x_fit: list | None = None
    y_fit: list | None = None
    fit: dict | None = None


class AcquireFitResponse(AcquireResponse):
    fit: FitResult


class AcquirePressureResponse(AcquireFitResponse):
    pressure_gpa: float | None = None
    pressure_err_gpa: float | None = None
    zero_pressure_peak_at_current_t: float | None = None
    temperature_warning: str | None = None


class StatusResponse(BaseModel):
    busy: bool
    camera_connected: bool
    exposure_time_s: float
    calibration: dict
    roi: dict
    background: dict
    configuration: dict[str, Any]
    hardware_state: dict[str, Any]


class ConfigurationListResponse(BaseModel):
    catalog_revision: int
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class ConfigurationRecordResponse(BaseModel):
    catalog_revision: int
    configuration: dict[str, Any]
    compatible: bool
    incompatibility_reasons: list[str]


class SlotResolutionRequest(BaseModel):
    """Names one calibration profile explicitly, for a slot_id that has more
    than one active profile (e.g. a Wavelength calibration and a Raman-shift
    calibration at the same grating/centre/ROI) -- a bare slot_id string only
    resolves when the slot currently has exactly one active profile."""
    slot_id: str
    axis_kind: Literal["wavelength", "raman_shift"] | None = None
    excitation_wavelength_nm: float | None = None

    @model_validator(mode="after")
    def _axis_kind_and_excitation_are_consistent(self):
        # Without this, an incomplete/contradictory selector wouldn't error --
        # it would silently resolve something other than what the caller meant
        # (e.g. excitation_wavelength_nm set but axis_kind omitted still
        # resolves as a bare slot_id, ignoring the excitation value entirely).
        if self.axis_kind == "raman_shift":
            if self.excitation_wavelength_nm is None:
                raise ValueError(
                    'excitation_wavelength_nm is required when axis_kind="raman_shift"'
                )
            if (
                not math.isfinite(self.excitation_wavelength_nm)
                or self.excitation_wavelength_nm <= 0
            ):
                raise ValueError("excitation_wavelength_nm must be a finite, positive number")
        elif self.excitation_wavelength_nm is not None:
            raise ValueError(
                'excitation_wavelength_nm can only be set together with axis_kind="raman_shift"'
            )
        return self


class ResolveConfigurationsRequest(BaseModel):
    slot_ids: list[str | SlotResolutionRequest] = Field(min_length=1)


class ResolveConfigurationsResponse(BaseModel):
    catalog_revision: int
    resolved: list[dict[str, str]]


class ApplyConfigurationRequest(BaseModel):
    axis_mode: Literal["calibrated", "pixel"] = "calibrated"


class ApplyConfigurationResponse(BaseModel):
    applied: bool
    configuration_id: str
    slot_id: str
    display_label: str
    configuration: dict[str, Any]
    hardware_state: dict[str, Any]


class HardwareIdentity(BaseModel):
    controller_model: str | None = None
    model: str | None = None
    serial_number: str | None = None


class DetectorSize(BaseModel):
    width: int | None = None
    height: int | None = None


class PixelPitch(BaseModel):
    width: float | None = None
    height: float | None = None


class RoiMetadata(BaseModel):
    mode: str | None = None
    horizontal_start: int | None = None
    horizontal_end: int | None = None
    vertical_start: int | None = None
    vertical_end: int | None = None


class BinningMetadata(BaseModel):
    horizontal: int | None = None
    vertical: int | None = None


class TemperatureMetadata(BaseModel):
    current_c: float | None = None
    setpoint_c: float | None = None
    status: str | None = None


class CameraMetadata(BaseModel):
    identity: HardwareIdentity
    detector_size_px: DetectorSize
    pixel_pitch_um: PixelPitch | None = None
    exposure_time_s: float
    accumulations: int
    accumulation_mode: str
    roi: RoiMetadata
    binning: BinningMetadata
    read_mode: str | None = None
    output_rows: int | None = None
    software_vertical_sum: bool | None = None
    temperature: TemperatureMetadata


class GratingMetadata(BaseModel):
    index: int | None = None
    grooves_per_mm: int | None = None
    blaze: str | None = None


class WavelengthLimits(BaseModel):
    min: float | None = None
    max: float | None = None


class SpectrometerMetadata(BaseModel):
    identity: HardwareIdentity
    center_wavelength_nm: float | None = None
    grating: GratingMetadata
    wavelength_limits_nm: WavelengthLimits | None = None


class DeviceStatusItem(BaseModel):
    key: str
    label: str
    value: Any = None
    unit: str | None = None
    state: Literal["ok", "error", "unsupported"] = "ok"
    error: str | None = None


class DeviceStatusSnapshot(BaseModel):
    backend: str
    available: bool
    error: str | None = None
    sections: dict[str, list[DeviceStatusItem]]


class CameraInfoResponse(BaseModel):
    schema_version: int
    captured_at: str
    mode: Literal["hardware", "debug"]
    operational: bool
    hardware_connected: bool
    busy: bool
    backend: str
    metadata_source: Literal["cache"]
    metadata: CameraMetadata
    status: DeviceStatusSnapshot | None = None


class SpectrometerInfoResponse(BaseModel):
    schema_version: int
    captured_at: str
    mode: Literal["hardware", "debug"]
    operational: bool
    hardware_connected: bool
    busy: bool
    backend: str
    metadata_source: Literal["cache"]
    metadata: SpectrometerMetadata
    status: DeviceStatusSnapshot | None = None


class ConfigResponse(BaseModel):
    schema_version: int
    captured_at: str
    source_file: str
    active_config: dict[str, Any]
    stored_config: dict[str, Any]
    restart_required: bool
    pending_restart_keys: list[str]
    redacted_fields: list[str]
