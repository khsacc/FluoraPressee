"""Pure helpers for the read-only hardware/config API responses."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime


SCHEMA_VERSION = 1
HARDWARE_RESTART_KEYS = (
    "model",
    "com_port",
    "dll_path",
    "PIcam_dll_path",
    "camera_serial_number",
)
_SENSITIVE_KEY_PARTS = ("api_key", "password", "token", "secret")


def captured_at_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_camera_metadata(state):
    """Normalise the measurement-metadata camera shape for the public API."""
    metadata = deepcopy(state or {})
    if "exposure_s" in metadata:
        metadata["exposure_time_s"] = metadata.pop("exposure_s")
    return metadata


def normalize_spectrometer_metadata(state, configured_identity=None):
    """Normalise both spectrometer backends to one public identity shape."""
    metadata = deepcopy(state or {})
    configured_identity = configured_identity or {}
    serial_number = metadata.pop("serial_number", None)
    metadata["identity"] = {
        "model": configured_identity.get("model"),
        "serial_number": serial_number or configured_identity.get("serial_number"),
    }
    return metadata


def build_device_response(
    *, backend, debug, operational, hardware_connected, busy, metadata, status=None,
    captured_at=None,
):
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at or captured_at_now(),
        "mode": "debug" if debug else "hardware",
        "operational": bool(operational),
        "hardware_connected": bool(hardware_connected),
        "busy": bool(busy),
        "backend": backend,
        "metadata_source": "cache",
        "metadata": deepcopy(metadata),
        "status": deepcopy(status),
    }


def build_config_response(current_config, startup_config, stored_config=None, captured_at=None):
    """Return active/stored config snapshots and restart-required differences.

    ``current_config`` is the GUI's current configuration. Hardware connection
    keys are replaced with their startup values in ``active_config`` because
    changing those keys in the dialog only takes effect after a restart.
    """
    current = deepcopy(current_config or {})
    startup = deepcopy(startup_config or {})
    stored = deepcopy(stored_config if stored_config is not None else current)

    active = deepcopy(current)
    pending = []
    for key in HARDWARE_RESTART_KEYS:
        if current.get(key) != startup.get(key):
            pending.append(key)
        if key in startup:
            active[key] = deepcopy(startup[key])
        else:
            active.pop(key, None)

    redacted_fields = set()
    active = _redact_mapping(active, (), redacted_fields)
    stored = _redact_mapping(stored, (), redacted_fields)
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at or captured_at_now(),
        "source_file": "spectrometerConfig.json",
        "active_config": active,
        "stored_config": stored,
        "restart_required": bool(pending),
        "pending_restart_keys": pending,
        "redacted_fields": sorted(redacted_fields),
    }


def _redact_mapping(value, path, redacted_fields):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            key_text = str(key)
            child_path = path + (key_text,)
            lowered = key_text.lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                redacted_fields.add(".".join(child_path))
                continue
            result[key] = _redact_mapping(child, child_path, redacted_fields)
        return result
    if isinstance(value, list):
        return [
            _redact_mapping(child, path + (str(index),), redacted_fields)
            for index, child in enumerate(value)
        ]
    return deepcopy(value)
