"""Versioned spectrometer-configuration storage and indexed discovery.

The JSON records are the canonical, portable configuration files.  SQLite is
only a compact catalog used to find the active record for each measurement
condition without scanning every historical JSON file.  This module has no Qt
dependency so the GUI and the future HTTP API can share exactly the same
selection and compatibility rules.

A physical measurement condition (hardware + grating + centre + ROI) is a
*slot*.  A slot can carry more than one independently-active calibration at
once -- a Wavelength calibration and one or more Raman-shift calibrations (one
per excitation laser) -- so calibrations are grouped into *calibration
profiles* underneath their slot, keyed by (slot_id, axis_kind,
excitation_wavelength_key).  Saving a new calibration only ever replaces the
active version of its own profile; it never touches sibling profiles at the
same slot.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


CONFIGURATION_SCHEMA_VERSION = 2
CATALOG_SCHEMA_VERSION = 3


class ConfigurationError(Exception):
    """Base class for configuration-catalog failures."""


class ConfigurationValidationError(ConfigurationError):
    """A configuration record is incomplete or malformed."""


class ConfigurationCompatibilityError(ConfigurationError):
    """A configuration cannot be applied to the current hardware."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class AmbiguousConfigurationProfileError(ConfigurationError):
    """A bare slot_id has more than one active calibration profile and needs
    (axis_kind, excitation_wavelength_nm) to disambiguate which one is meant."""


def default_configuration_root() -> Path:
    """Return a per-user, writable application-data directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "FluoraPressee" / "configurations"


def excitation_wavelength_key(value_nm: Any) -> int:
    """Integer key at 0.001 nm resolution, used both for calibration-profile
    identity in the catalog and for the GUI's load-time excitation-mismatch
    check (file_io_mixin.py) -- kept as one function so the two never drift."""
    return round(float(value_nm) * 1000.0)


_WAVELENGTH_EXCITATION_KEY = -1


def _axis_kind_from_unit(unit: str) -> str:
    return "raman_shift" if unit == "Raman shift" else "wavelength"


def format_configuration_label(summary: dict[str, Any]) -> str:
    grating = summary.get("grating", summary.get("spectrometer", {}))
    roi = summary.get("roi", summary.get("detector", {}))
    roi_mode = roi.get("mode", roi.get("roi_mode"))
    roi_start = roi.get("start", roi.get("roi_start"))
    roi_end = roi.get("end", roi.get("roi_end"))
    center_nm = summary.get(
        "center_wavelength_nm", grating.get("target_center_wavelength_nm")
    )
    grooves = grating.get(
        "grooves_per_mm", grating.get("grating_grooves_per_mm")
    )
    if roi_mode == "1d_roi":
        roi_text = f"ROI {roi_start}–{roi_end}"
    elif roi_mode == "1d_full":
        roi_text = "Full ROI"
    else:
        roi_text = "2D"
    return (
        f"{grooves} g/mm | "
        f"{center_nm:.3f} nm | {roi_text}"
    )


class ConfigurationCatalog:
    """Persist immutable records and maintain one active version per
    calibration profile (not per slot -- see module docstring).

    get_configuration() memoises records in process memory. Records are immutable
    once written, so the content can never go stale; every structural write method
    clears the cache so index-derived fields cannot either. Usage-counter updates are
    intentionally excluded because they do not affect record payloads. Two
    consequences are deliberate:

    * The SHA-256 integrity check runs on the *first* read of each record only.
      That matches this module's existing assumption that this process is the sole
      writer of its catalog.
    * If another process writes to the same catalog directory, this cache will not
      notice. Restart the application to pick such changes up.

    The cache exists because API responses snapshot configuration state while holding
    the acquisition gate on the GUI thread, where a SQLite query plus a file read plus
    a hash on every request would show up as UI latency (work_API_standby.md 方針10).
    It is read from API worker threads as well as the GUI thread, hence the lock.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root) if root is not None else default_configuration_root()
        self.records_root = self.root / "records"
        self.database_path = self.root / "catalog.sqlite3"
        self.records_root.mkdir(parents=True, exist_ok=True)
        self._record_cache: dict[str, dict[str, Any]] = {}
        self._record_cache_lock = threading.Lock()
        # Bumped by every invalidation. get_configuration() captures this value before
        # it starts the (lock-released) disk read below, and only writes its result
        # back if the generation is still the same one it started with - otherwise a
        # concurrent delete/register that invalidated the cache mid-read would have its
        # clear() overwritten by a write racing in right behind it, permanently
        # resurrecting a deleted/superseded record until the process restarts.
        self._record_cache_generation = 0
        self._initialize_database()

    def _invalidate_record_cache(self) -> None:
        with self._record_cache_lock:
            self._record_cache.clear()
            self._record_cache_generation += 1

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_database(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS slots (
                    slot_id TEXT PRIMARY KEY,
                    signature TEXT NOT NULL UNIQUE,
                    spectrometer_model TEXT,
                    spectrometer_serial TEXT,
                    camera_model TEXT,
                    camera_serial TEXT,
                    grating_index INTEGER NOT NULL,
                    grating_grooves_per_mm INTEGER NOT NULL,
                    center_position_pm INTEGER NOT NULL,
                    roi_mode TEXT NOT NULL,
                    roi_start INTEGER NOT NULL,
                    roi_end INTEGER NOT NULL,
                    active_configuration_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS calibration_profiles (
                    calibration_profile_id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL,
                    axis_kind TEXT NOT NULL CHECK(axis_kind IN ('wavelength', 'raman_shift')),
                    excitation_wavelength_key INTEGER NOT NULL,
                    active_configuration_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(slot_id) REFERENCES slots(slot_id),
                    CHECK(
                        (axis_kind = 'wavelength' AND excitation_wavelength_key = -1)
                        OR (axis_kind = 'raman_shift' AND excitation_wavelength_key > 0)
                    )
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_calibration_profiles_identity
                    ON calibration_profiles(slot_id, axis_kind, excitation_wavelength_key);

                CREATE TABLE IF NOT EXISTS configurations (
                    configuration_id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL,
                    calibration_profile_id TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
                    calibration_unit TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(slot_id) REFERENCES slots(slot_id),
                    FOREIGN KEY(calibration_profile_id) REFERENCES calibration_profiles(calibration_profile_id)
                );

                CREATE INDEX IF NOT EXISTS idx_configurations_slot_created
                    ON configurations(slot_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_configurations_status_created
                    ON configurations(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_slots_active_conditions
                    ON slots(grating_index, center_position_pm, roi_mode, roi_start, roi_end);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                (str(CATALOG_SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO catalog_meta(key, value) VALUES('revision', '0')"
            )
            # idx_slots_hardware_identity/idx_configurations_profile_created reference
            # columns (spectrometer_model/camera_model, calibration_profile_id) that a
            # pre-existing database only gains via _migrate_catalog()'s ALTER TABLE
            # steps -- CREATE TABLE IF NOT EXISTS above is a no-op against an existing
            # table, so creating these indexes any earlier would fail against a real
            # old database with "no such column" before migration ever runs.
            self._migrate_catalog(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_slots_hardware_identity
                ON slots(
                    spectrometer_serial, spectrometer_model,
                    camera_serial, camera_model
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_configurations_profile_created
                ON configurations(calibration_profile_id, created_at DESC)
                """
            )

    def _migrate_catalog(self, conn: sqlite3.Connection) -> None:
        """Upgrade catalog metadata while keeping immutable JSON records canonical.

        Staged so a catalog already at v2 (from a previous release of this app)
        goes straight to the v2->v3 step, while a still-v1 catalog runs both
        steps in one open.
        """
        version_row = conn.execute(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
        ).fetchone()
        version = int(version_row["value"])
        if version >= CATALOG_SCHEMA_VERSION:
            return

        if version < 2:
            self._migrate_v1_to_v2(conn)
            version = 2
        if version < 3:
            self._migrate_v2_to_v3(conn)
            version = 3

        conn.execute(
            "UPDATE catalog_meta SET value = ? WHERE key = 'schema_version'",
            (str(version),),
        )
        self._increment_revision(conn)

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Index hardware identity by model as well as serial number, and
        separate serial-less hardware into its own namespace instead of
        collapsing it into one."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(slots)").fetchall()
        }
        for column in ("spectrometer_model", "camera_model"):
            if column not in columns:
                conn.execute(f"ALTER TABLE slots ADD COLUMN {column} TEXT")

        # Any one configuration under a slot carries the same
        # compatibility/spectrometer/detector fields as every other (that's
        # the definition of slot identity), so picking one representative row
        # per slot is enough -- this must not join through
        # slots.active_configuration_id, which is deprecated going forward
        # and is never written by the current register_configuration().
        rows = conn.execute(
            """
            SELECT s.slot_id, MIN(c.relative_path) AS relative_path
            FROM slots s
            JOIN configurations c ON c.slot_id = s.slot_id
            GROUP BY s.slot_id
            """
        ).fetchall()
        for row in rows:
            try:
                record = json.loads(
                    (self.root / row["relative_path"]).read_text(encoding="utf-8")
                )
                compatibility = record.get("compatibility", {})
                signature = self._signature(record)
            except (
                OSError,
                json.JSONDecodeError,
                ConfigurationError,
                KeyError,
                TypeError,
                ValueError,
            ):
                # A damaged record must not prevent the rest of the catalog opening.
                continue
            conn.execute(
                """
                UPDATE slots
                SET signature = ?, spectrometer_model = ?, camera_model = ?
                WHERE slot_id = ?
                """,
                (
                    signature,
                    compatibility.get("spectrometer_model"),
                    compatibility.get("camera_model"),
                    row["slot_id"],
                ),
            )

    def _migrate_v2_to_v3(self, conn: sqlite3.Connection) -> None:
        """Bootstrap calibration_profiles from every configuration's full
        history (not just each slot's current active record -- the bug this
        introduces calibration_profiles to fix means an older, still-valid
        calibration of a different axis_kind/laser can already be sitting
        archived behind a newer one)."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(configurations)").fetchall()
        }
        if "calibration_profile_id" not in columns:
            conn.execute(
                "ALTER TABLE configurations ADD COLUMN calibration_profile_id TEXT "
                "REFERENCES calibration_profiles(calibration_profile_id)"
            )

        rows = conn.execute(
            "SELECT configuration_id, slot_id, relative_path, created_at "
            "FROM configurations ORDER BY created_at ASC"
        ).fetchall()

        groups: dict[tuple[str, str, int], list[tuple[str, str]]] = {}
        skipped: list[str] = []
        for row in rows:
            try:
                record = json.loads(
                    (self.root / row["relative_path"]).read_text(encoding="utf-8")
                )
                calibration = record["calibration"]
                unit = calibration["unit"]
                # A readable-but-corrupted unit must not silently fall back to
                # "wavelength" via _axis_kind_from_unit()'s permissive default --
                # that would resurrect a broken record as an active Wavelength
                # profile instead of leaving it for Configuration Manager.
                if unit not in ("Wavelength", "Raman shift"):
                    raise ValueError(f"unrecognized calibration unit: {unit!r}")
                axis_kind = _axis_kind_from_unit(unit)
                coefficients = calibration["coefficients"]
                if not all(
                    math.isfinite(float(coefficients[name]))
                    for name in ("c0", "c1", "c2")
                ):
                    raise ValueError("non-finite calibration coefficient")
                if axis_kind == "raman_shift":
                    excitation_nm = calibration["excitation_wavelength_nm"]
                    # Checked before excitation_wavelength_key() so an Infinity/NaN
                    # value (json.loads accepts these as an extension) is rejected
                    # as a normal skip instead of an uncaught OverflowError that
                    # would otherwise abort catalog opening entirely.
                    if not math.isfinite(float(excitation_nm)):
                        raise ValueError("non-finite excitation_wavelength_nm")
                    key = excitation_wavelength_key(excitation_nm)
                    if key <= 0:
                        raise ValueError("non-positive excitation_wavelength_nm")
                else:
                    key = _WAVELENGTH_EXCITATION_KEY
            except (
                OSError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
            ):
                skipped.append(row["configuration_id"])
                continue
            groups.setdefault((row["slot_id"], axis_kind, key), []).append(
                (row["configuration_id"], row["created_at"])
            )

        now = self._now()
        for (slot_id, axis_kind, key), entries in groups.items():
            entries.sort(key=lambda entry: entry[1])
            active_configuration_id = entries[-1][0]
            # Find-or-create: a profile for this group may already exist (a
            # retried/partial migration, or manual catalog_meta surgery in
            # tests) -- INSERTing unconditionally would violate the identity
            # UNIQUE index instead of reconciling.
            existing_profile = conn.execute(
                "SELECT calibration_profile_id FROM calibration_profiles "
                "WHERE slot_id = ? AND axis_kind = ? AND excitation_wavelength_key = ?",
                (slot_id, axis_kind, key),
            ).fetchone()
            if existing_profile is None:
                profile_id = self._new_id("calprof")
                conn.execute(
                    """
                    INSERT INTO calibration_profiles(
                        calibration_profile_id, slot_id, axis_kind, excitation_wavelength_key,
                        active_configuration_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (profile_id, slot_id, axis_kind, key, active_configuration_id, now, now),
                )
            else:
                profile_id = existing_profile["calibration_profile_id"]
                conn.execute(
                    "UPDATE calibration_profiles SET active_configuration_id = ?, "
                    "updated_at = ? WHERE calibration_profile_id = ?",
                    (active_configuration_id, now, profile_id),
                )
            for configuration_id, _ in entries:
                status = "active" if configuration_id == active_configuration_id else "archived"
                conn.execute(
                    "UPDATE configurations SET calibration_profile_id = ?, status = ? "
                    "WHERE configuration_id = ?",
                    (profile_id, status, configuration_id),
                )

        if skipped:
            print(
                f"Configuration catalog v2->v3 migration: {len(skipped)} record(s) "
                f"could not be read and were left without a calibration profile "
                f"(visible/deletable via Configuration Manager): {skipped}"
            )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @staticmethod
    def _center_pm(value_nm: Any) -> int:
        try:
            return int(round(float(value_nm) * 1000.0))
        except (TypeError, ValueError) as exc:
            raise ConfigurationValidationError(
                "spectrometer.target_center_wavelength_nm must be numeric"
            ) from exc

    @classmethod
    def _validate_draft(cls, draft: dict[str, Any]) -> None:
        try:
            compatibility = draft["compatibility"]
            spectrometer = draft["spectrometer"]
            detector = draft["detector"]
            calibration = draft["calibration"]
            int(spectrometer["grating_index"])
            int(spectrometer["grating_grooves_per_mm"])
            center_wl = float(spectrometer["target_center_wavelength_nm"])
            mode = detector["roi_mode"]
            roi_start = int(detector["roi_start"])
            roi_end = int(detector["roi_end"])
            coefficients = calibration["coefficients"]
            c0 = float(coefficients["c0"])
            c1 = float(coefficients["c1"])
            c2 = float(coefficients["c2"])
            unit = calibration["unit"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationValidationError(
                f"Incomplete configuration record: {exc}"
            ) from exc

        for name, value in (
            ("spectrometer.target_center_wavelength_nm", center_wl),
            ("calibration.coefficients.c0", c0),
            ("calibration.coefficients.c1", c1),
            ("calibration.coefficients.c2", c2),
        ):
            if not math.isfinite(value):
                raise ConfigurationValidationError(f"{name} must be a finite number")

        if mode not in {"1d_roi", "1d_full", "2d"}:
            raise ConfigurationValidationError(f"Unsupported ROI mode: {mode!r}")
        if roi_start < 0 or roi_end <= roi_start:
            raise ConfigurationValidationError("ROI must satisfy 0 <= start < end")
        if unit not in {"Wavelength", "Raman shift"}:
            raise ConfigurationValidationError(f"Unsupported calibration unit: {unit!r}")

        excitation = calibration.get("excitation_wavelength_nm")
        if unit == "Raman shift":
            if excitation is None:
                raise ConfigurationValidationError(
                    "Raman shift calibration requires excitation_wavelength_nm"
                )
            excitation = float(excitation)
            if not math.isfinite(excitation) or excitation <= 0:
                raise ConfigurationValidationError(
                    "calibration.excitation_wavelength_nm must be a finite positive number"
                )
        elif excitation is not None:
            raise ConfigurationValidationError(
                "Wavelength calibration must not include excitation_wavelength_nm"
            )

        reference_kind = calibration.get("reference_kind")
        if reference_kind is not None and reference_kind not in (
            "emission_lines", "emission_lines_with_excitation", "raman_standard"
        ):
            raise ConfigurationValidationError(
                f"Unsupported calibration reference_kind: {reference_kind!r}"
            )
        standards = calibration.get("standards")
        if standards is not None and not isinstance(standards, list):
            raise ConfigurationValidationError("calibration.standards must be a list")

        raw_spectrum = calibration.get("raw_spectrum")
        if raw_spectrum is not None:
            if not isinstance(raw_spectrum, list) or not raw_spectrum:
                raise ConfigurationValidationError(
                    "calibration.raw_spectrum must be a non-empty list"
                )
            try:
                if not all(math.isfinite(float(value)) for value in raw_spectrum):
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ConfigurationValidationError(
                    "calibration.raw_spectrum must contain only finite numbers"
                ) from exc

        if not isinstance(compatibility, dict):
            raise ConfigurationValidationError("compatibility must be an object")
        for device in ("spectrometer", "camera"):
            if not (
                compatibility.get(f"{device}_serial_number")
                or compatibility.get(f"{device}_model")
            ):
                raise ConfigurationValidationError(
                    f"{device} compatibility requires a serial number or model"
                )

    @classmethod
    def _signature(cls, draft: dict[str, Any]) -> str:
        compatibility = draft["compatibility"]
        spectrometer = draft["spectrometer"]
        detector = draft["detector"]
        identity = {
            "spectrometer_model": compatibility.get("spectrometer_model"),
            "spectrometer_serial_number": compatibility.get("spectrometer_serial_number"),
            "camera_model": compatibility.get("camera_model"),
            "camera_serial_number": compatibility.get("camera_serial_number"),
            "grating_index": int(spectrometer["grating_index"]),
            "grating_grooves_per_mm": int(spectrometer["grating_grooves_per_mm"]),
            "center_position_pm": cls._center_pm(
                spectrometer["target_center_wavelength_nm"]
            ),
            "roi_mode": detector["roi_mode"],
            "roi_start": int(detector["roi_start"]),
            "roi_end": int(detector["roi_end"]),
        }
        return json.dumps(identity, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _record_bytes(record: dict[str, Any]) -> bytes:
        try:
            payload = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
        except ValueError as exc:
            raise ConfigurationValidationError(
                f"Configuration record contains a non-finite value: {exc}"
            ) from exc
        return (payload + "\n").encode("utf-8")

    def register_configuration(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Register a new immutable version and make it active for its
        calibration profile (physical slot + axis_kind + excitation laser).
        Sibling profiles at the same slot (a different axis_kind, or the same
        axis_kind at a different excitation wavelength) are left untouched."""
        self._validate_draft(draft)
        signature = self._signature(draft)
        calibration = draft["calibration"]
        axis_kind = _axis_kind_from_unit(calibration["unit"])
        excitation_key = (
            excitation_wavelength_key(calibration["excitation_wavelength_nm"])
            if axis_kind == "raman_shift"
            else _WAVELENGTH_EXCITATION_KEY
        )
        created_at = self._now()
        configuration_id = self._new_id("cfg")
        final_path: Path | None = None

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            slot_row = conn.execute(
                "SELECT * FROM slots WHERE signature = ?", (signature,)
            ).fetchone()
            slot_id = slot_row["slot_id"] if slot_row else self._new_id("slot")

            profile_row = conn.execute(
                "SELECT * FROM calibration_profiles WHERE slot_id = ? "
                "AND axis_kind = ? AND excitation_wavelength_key = ?",
                (slot_id, axis_kind, excitation_key),
            ).fetchone()
            calibration_profile_id = (
                profile_row["calibration_profile_id"]
                if profile_row else self._new_id("calprof")
            )

            record = deepcopy(draft)
            record["schema_version"] = CONFIGURATION_SCHEMA_VERSION
            record["configuration_id"] = configuration_id
            record["slot_id"] = slot_id
            record["calibration_profile_id"] = calibration_profile_id
            record["created_at"] = created_at
            # Written to disk (not just defaulted on read by _normalize_record)
            # so a caller that omits them -- direct register_configuration()
            # use, not just the calibration dialog -- still produces a
            # self-describing record on disk.
            record["calibration"].setdefault(
                "reference_kind",
                "emission_lines_with_excitation"
                if record["calibration"].get("unit") == "Raman shift"
                else "emission_lines",
            )
            record["calibration"].setdefault("standards", [])
            record["calibration"].setdefault("raw_spectrum", None)

            relative_path = Path(
                "records",
                created_at[0:4],
                created_at[5:7],
                f"{configuration_id}.json",
            )
            final_path = self.root / relative_path
            final_path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._record_bytes(record)
            digest = hashlib.sha256(payload).hexdigest()
            temporary_path = final_path.with_suffix(".json.tmp")

            try:
                temporary_path.write_bytes(payload)
                os.replace(temporary_path, final_path)

                spectrometer = record["spectrometer"]
                detector = record["detector"]
                compatibility = record["compatibility"]
                center_pm = self._center_pm(
                    spectrometer["target_center_wavelength_nm"]
                )

                if slot_row is None:
                    conn.execute(
                        """
                        INSERT INTO slots(
                            slot_id, signature,
                            spectrometer_model, spectrometer_serial,
                            camera_model, camera_serial,
                            grating_index, grating_grooves_per_mm, center_position_pm,
                            roi_mode, roi_start, roi_end, active_configuration_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            slot_id,
                            signature,
                            compatibility.get("spectrometer_model"),
                            compatibility.get("spectrometer_serial_number"),
                            compatibility.get("camera_model"),
                            compatibility.get("camera_serial_number"),
                            int(spectrometer["grating_index"]),
                            int(spectrometer["grating_grooves_per_mm"]),
                            center_pm,
                            detector["roi_mode"],
                            int(detector["roi_start"]),
                            int(detector["roi_end"]),
                            created_at,
                            created_at,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE slots SET updated_at = ? WHERE slot_id = ?",
                        (created_at, slot_id),
                    )

                if profile_row is None:
                    conn.execute(
                        """
                        INSERT INTO calibration_profiles(
                            calibration_profile_id, slot_id, axis_kind,
                            excitation_wavelength_key, active_configuration_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            calibration_profile_id, slot_id, axis_kind, excitation_key,
                            configuration_id, created_at, created_at,
                        ),
                    )
                else:
                    if profile_row["active_configuration_id"] is not None:
                        conn.execute(
                            "UPDATE configurations SET status = 'archived' "
                            "WHERE configuration_id = ?",
                            (profile_row["active_configuration_id"],),
                        )
                    conn.execute(
                        "UPDATE calibration_profiles SET active_configuration_id = ?, "
                        "updated_at = ? WHERE calibration_profile_id = ?",
                        (configuration_id, created_at, calibration_profile_id),
                    )

                conn.execute(
                    """
                    INSERT INTO configurations(
                        configuration_id, slot_id, calibration_profile_id, created_at,
                        status, calibration_unit, relative_path, sha256
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        configuration_id,
                        slot_id,
                        calibration_profile_id,
                        created_at,
                        record["calibration"]["unit"],
                        relative_path.as_posix(),
                        digest,
                    ),
                )
                self._increment_revision(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                if final_path.exists():
                    final_path.unlink()
                raise

        self._invalidate_record_cache()
        return record

    @staticmethod
    def _increment_revision(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM catalog_meta WHERE key = 'revision'"
        ).fetchone()
        revision = int(row["value"]) + 1
        conn.execute(
            "UPDATE catalog_meta SET value = ? WHERE key = 'revision'",
            (str(revision),),
        )
        return revision

    def catalog_revision(self) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = 'revision'"
            ).fetchone()
        return int(row["value"])

    def count_slots(self) -> int:
        with self._connection() as conn:
            return int(
                conn.execute("SELECT COUNT(*) AS count FROM slots").fetchone()["count"]
            )

    def count_profiles(self) -> int:
        with self._connection() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM calibration_profiles"
                ).fetchone()["count"]
            )

    @staticmethod
    def _normalize_record(
        record: dict[str, Any], db_calibration_profile_id: str | None
    ) -> dict[str, Any]:
        version = record.get("schema_version", 1)
        if version > CONFIGURATION_SCHEMA_VERSION:
            raise ConfigurationError(
                f"Configuration record schema_version {version} is newer than this "
                f"application supports (max {CONFIGURATION_SCHEMA_VERSION})"
            )
        record = deepcopy(record)
        record.pop("display", None)
        if version < 2:
            record["schema_version"] = CONFIGURATION_SCHEMA_VERSION
            record["calibration_profile_id"] = db_calibration_profile_id
        elif (
            db_calibration_profile_id is not None
            and record.get("calibration_profile_id") != db_calibration_profile_id
        ):
            raise ConfigurationError(
                f"Configuration {record.get('configuration_id')} calibration_profile_id "
                "does not match the catalog index; the record or database may be corrupt."
            )
        # Applies regardless of schema_version: any record written before
        # reference_kind/standards/raw_spectrum existed (v1, or v2 saved
        # before a given field was ever produced) is missing them just the
        # same, and all are informational -- there is no version's shape
        # they'd conflict with.
        calibration = record.get("calibration", {})
        calibration.setdefault(
            "reference_kind",
            "emission_lines_with_excitation"
            if calibration.get("unit") == "Raman shift"
            else "emission_lines",
        )
        calibration.setdefault("standards", [])
        calibration.setdefault("raw_spectrum", None)
        return record

    @staticmethod
    def _copy_configuration_record(record: dict[str, Any]) -> dict[str, Any]:
        """Deep-copy a record for return, except calibration.raw_spectrum.

        raw_spectrum is written once at registration and, per an audit of every
        get_configuration() caller (api_mixin.py, file_io_mixin.py,
        configuration_manager_dialog.py), never read back or mutated afterwards - it
        is kept purely as evidence of how the calibration was derived. For a large
        stored spectrum, deep-copying it on every cache hit can dominate the cost the
        cache exists to avoid. Seeding deepcopy's memo with the array mapped to
        itself makes deepcopy reuse the same object instead of recursing into it,
        without touching (and thus never racing on) the cached record itself.
        """
        calibration = record.get("calibration")
        raw_spectrum = calibration.get("raw_spectrum") if calibration else None
        memo = {id(raw_spectrum): raw_spectrum} if raw_spectrum is not None else {}
        return deepcopy(record, memo)

    def get_configuration(self, configuration_id: str) -> dict[str, Any]:
        with self._record_cache_lock:
            cached = self._record_cache.get(configuration_id)
            generation = self._record_cache_generation
        if cached is not None:
            # Copied so a caller mutating the returned dict cannot poison the cache.
            return self._copy_configuration_record(cached)

        with self._connection() as conn:
            row = conn.execute(
                "SELECT relative_path, sha256, calibration_profile_id FROM configurations "
                "WHERE configuration_id = ?",
                (configuration_id,),
            ).fetchone()
        if row is None:
            raise ConfigurationError(f"Unknown configuration: {configuration_id}")
        path = self.root / row["relative_path"]
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(
                f"Configuration file is unavailable: {configuration_id}"
            ) from exc
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ConfigurationError(
                f"Configuration file failed its integrity check: {configuration_id}"
            )
        try:
            record = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Configuration file is invalid JSON: {configuration_id}"
            ) from exc
        normalized = self._normalize_record(record, row["calibration_profile_id"])
        with self._record_cache_lock:
            if self._record_cache_generation == generation:
                self._record_cache[configuration_id] = normalized
            # else: a delete/register invalidated the cache while this read was in
            # flight (I/O above releases the GIL) - do not resurrect a possibly
            # deleted/superseded record; the next call will simply re-read from disk.
        return self._copy_configuration_record(normalized)

    def _summary_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        is_orphaned = row["calibration_profile_id"] is None
        active = (
            not is_orphaned
            and row["configuration_id"] == row["profile_active_configuration_id"]
        )
        excitation_key = row["excitation_wavelength_key"] if not is_orphaned else None
        summary = {
            "slot_id": row["slot_id"],
            "configuration_id": row["configuration_id"],
            "calibration_profile_id": row["calibration_profile_id"],
            "active_configuration_id": (
                row["profile_active_configuration_id"] if not is_orphaned else None
            ),
            "active": active,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "grating": {
                "index": row["grating_index"],
                "grooves_per_mm": row["grating_grooves_per_mm"],
            },
            "center_wavelength_nm": row["center_position_pm"] / 1000.0,
            "roi": {
                "mode": row["roi_mode"],
                "start": row["roi_start"],
                "end": row["roi_end"],
            },
            "calibration": {
                "available": not is_orphaned,
                "unit": row["calibration_unit"],
            },
            "axis_kind": row["axis_kind"] if not is_orphaned else None,
            "excitation_wavelength_nm": (
                excitation_key / 1000.0
                if excitation_key is not None and excitation_key > 0
                else None
            ),
            "profile_count_for_slot": row["profile_count_for_slot"],
            "compatibility": {
                "spectrometer_model": row["spectrometer_model"],
                "spectrometer_serial_number": row["spectrometer_serial"],
                "camera_model": row["camera_model"],
                "camera_serial_number": row["camera_serial"],
            },
        }
        available_columns = row.keys()
        if "last_used_at" in available_columns:
            summary["last_used_at"] = row["last_used_at"]
            summary["use_count"] = row["use_count"]
        if "status" in available_columns:
            summary["status"] = "unavailable" if is_orphaned else row["status"]
            summary["migration_error"] = is_orphaned
        summary["display_label"] = format_configuration_label(summary)
        return summary

    @staticmethod
    def compatibility_reasons(
        configuration: dict[str, Any], hardware_context: dict[str, Any]
    ) -> list[str]:
        compatibility = configuration.get("compatibility", {})
        reasons: list[str] = []
        for device, label in (("spectrometer", "Spectrometer"), ("camera", "Camera")):
            expected_serial = compatibility.get(f"{device}_serial_number")
            current_serial = hardware_context.get(f"{device}_serial_number")
            expected_model = compatibility.get(f"{device}_model")
            current_model = hardware_context.get(f"{device}_model")

            if expected_serial:
                if current_serial != expected_serial:
                    reasons.append(f"{label} serial number does not match")
                    continue
                if expected_model and current_model and current_model != expected_model:
                    reasons.append(f"{label} model does not match")
            elif expected_model:
                if not current_model:
                    reasons.append(f"{label} model is unavailable")
                elif current_model != expected_model:
                    reasons.append(f"{label} model does not match")
            else:
                reasons.append(f"{label} identity is unavailable in the configuration")

        spectrometer = configuration.get("spectrometer", {})
        grating = configuration.get("grating") or spectrometer.get("grating")
        if grating is None:
            grating = {
                "index": spectrometer.get(
                    "grating_index", configuration.get("grating_index")
                ),
                "grooves_per_mm": spectrometer.get(
                    "grating_grooves_per_mm",
                    configuration.get("grating_grooves_per_mm"),
                ),
            }
        available_gratings = hardware_context.get("gratings", [])
        if available_gratings:
            match = next(
                (
                    item for item in available_gratings
                    if int(item.get("index", -1)) == int(grating.get("index", -2))
                ),
                None,
            )
            if match is None:
                reasons.append("Grating slot is not available")
            elif int(match.get("grooves", -1)) != int(grating.get("grooves_per_mm", -2)):
                reasons.append("Grating type does not match the configured slot")

        roi = configuration.get("roi") or configuration.get("detector", {})
        detector_height = hardware_context.get("detector_height")
        if detector_height is not None:
            if int(roi.get("roi_start", roi.get("start", 0))) < 0:
                reasons.append("ROI start is outside the detector")
            if int(roi.get("roi_end", roi.get("end", 0))) > int(detector_height):
                reasons.append("ROI end is outside the detector")
        return reasons

    def assert_compatible(
        self, configuration: dict[str, Any], hardware_context: dict[str, Any]
    ) -> None:
        reasons = self.compatibility_reasons(configuration, hardware_context)
        if reasons:
            raise ConfigurationCompatibilityError(reasons)

    @staticmethod
    def _base_select(*, include_management_columns: bool) -> str:
        management_columns = (
            ", c.status, c.last_used_at, c.use_count" if include_management_columns else ""
        )
        join = "LEFT JOIN" if include_management_columns else "JOIN"
        return f"""
            SELECT
                c.configuration_id, c.slot_id, c.calibration_profile_id, c.created_at,
                c.calibration_unit{management_columns},
                p.axis_kind, p.excitation_wavelength_key,
                p.active_configuration_id AS profile_active_configuration_id,
                (SELECT COUNT(*) FROM calibration_profiles p2 WHERE p2.slot_id = s.slot_id)
                    AS profile_count_for_slot,
                s.updated_at,
                s.spectrometer_model, s.spectrometer_serial,
                s.camera_model, s.camera_serial,
                s.grating_index, s.grating_grooves_per_mm,
                s.center_position_pm, s.roi_mode, s.roi_start, s.roi_end
            FROM configurations c
            JOIN slots s ON s.slot_id = c.slot_id
            {join} calibration_profiles p ON p.calibration_profile_id = c.calibration_profile_id
        """

    def list_all(
        self,
        *,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return every slot/version in the catalog, unfiltered by hardware
        compatibility. For a management/inspection UI only -- callers that
        need to know whether a record can actually be applied to the
        connected hardware must use list_selectable() instead.

        Uses a LEFT join to calibration_profiles (unlike list_selectable's
        INNER join) so a configuration a migration couldn't assign to a
        profile (a damaged/unreadable JSON file at upgrade time) still shows
        up here -- tagged via summary["migration_error"] -- instead of
        silently disappearing from the one screen an operator could use to
        find and delete it.
        """
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        where = (
            "WHERE (c.configuration_id = p.active_configuration_id "
            "OR c.calibration_profile_id IS NULL)"
            if active_only else ""
        )
        select_from = self._base_select(include_management_columns=True) + where
        with self._connection() as conn:
            conn.execute("BEGIN")
            revision = int(
                conn.execute(
                    "SELECT value FROM catalog_meta WHERE key = 'revision'"
                ).fetchone()["value"]
            )
            total = conn.execute(
                f"SELECT COUNT(*) AS count FROM ({select_from})"
            ).fetchone()["count"]
            rows = conn.execute(
                select_from + " ORDER BY s.updated_at DESC, c.created_at DESC, "
                "s.slot_id LIMIT ? OFFSET ?",
                [limit, offset],
            ).fetchall()

        return {
            "catalog_revision": revision,
            "items": [self._summary_from_row(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def list_selectable(
        self,
        hardware_context: dict[str, Any],
        *,
        active_only: bool = True,
        include_incompatible: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return lightweight structured rows suitable for GUI/API selection."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        conditions = []
        parameters: list[Any] = []
        if active_only:
            conditions.append("c.configuration_id = p.active_configuration_id")
        if not include_incompatible:
            for serial_column, model_column, device in (
                ("s.spectrometer_serial", "s.spectrometer_model", "spectrometer"),
                ("s.camera_serial", "s.camera_model", "camera"),
            ):
                current_serial = hardware_context.get(f"{device}_serial_number")
                current_model = hardware_context.get(f"{device}_model")
                if current_serial and current_model:
                    conditions.append(
                        f"(({serial_column} = ? AND "
                        f"({model_column} IS NULL OR {model_column} = ?)) OR "
                        f"({serial_column} IS NULL AND {model_column} = ?))"
                    )
                    parameters.extend([current_serial, current_model, current_model])
                elif current_serial:
                    conditions.append(f"{serial_column} = ?")
                    parameters.append(current_serial)
                elif current_model:
                    conditions.append(
                        f"{serial_column} IS NULL AND {model_column} = ?"
                    )
                    parameters.append(current_model)
                else:
                    conditions.append("0")

            gratings = hardware_context.get("gratings", [])
            if gratings:
                grating_clauses = []
                for grating in gratings:
                    grating_clauses.append(
                        "(s.grating_index = ? AND s.grating_grooves_per_mm = ?)"
                    )
                    parameters.extend(
                        [int(grating["index"]), int(grating["grooves"])]
                    )
                conditions.append("(" + " OR ".join(grating_clauses) + ")")

            detector_height = hardware_context.get("detector_height")
            if detector_height is not None:
                conditions.append("s.roi_start >= 0 AND s.roi_end <= ?")
                parameters.append(int(detector_height))

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        select_from = self._base_select(include_management_columns=False) + where
        with self._connection() as conn:
            conn.execute("BEGIN")
            revision = int(
                conn.execute(
                    "SELECT value FROM catalog_meta WHERE key = 'revision'"
                ).fetchone()["value"]
            )
            total = conn.execute(
                f"SELECT COUNT(*) AS count FROM ({select_from})",
                parameters,
            ).fetchone()["count"]
            rows = conn.execute(
                select_from + " ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()

        compatible_items: list[dict[str, Any]] = []
        for row in rows:
            summary = self._summary_from_row(row)
            reasons = self.compatibility_reasons(summary, hardware_context)
            summary["compatible"] = not reasons
            summary["incompatibility_reasons"] = reasons
            if include_incompatible or not reasons:
                compatible_items.append(summary)

        return {
            "catalog_revision": revision,
            "items": compatible_items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def resolve_slots(
        self, slot_ids: list[str | dict[str, Any]], hardware_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve stable slot/profile references to exact active record IDs.

        Each entry is either a bare slot_id string -- which resolves only if
        that slot currently has exactly one active calibration profile, the
        common case -- or a dict {slot_id, axis_kind, excitation_wavelength_nm}
        that names the profile explicitly. A bare slot_id with more than one
        active profile raises AmbiguousConfigurationProfileError rather than
        guessing which calibration was meant.
        """
        resolved_ids = []
        with self._connection() as conn:
            conn.execute("BEGIN")
            revision = int(
                conn.execute(
                    "SELECT value FROM catalog_meta WHERE key = 'revision'"
                ).fetchone()["value"]
            )
            for entry in slot_ids:
                if isinstance(entry, dict):
                    slot_id = entry["slot_id"]
                    axis_kind = entry.get("axis_kind")
                    excitation_nm = entry.get("excitation_wavelength_nm")
                else:
                    slot_id = entry
                    axis_kind = None
                    excitation_nm = None

                if axis_kind is not None:
                    key = (
                        excitation_wavelength_key(excitation_nm)
                        if axis_kind == "raman_shift" and excitation_nm is not None
                        else _WAVELENGTH_EXCITATION_KEY
                    )
                    profile_row = conn.execute(
                        "SELECT active_configuration_id FROM calibration_profiles "
                        "WHERE slot_id = ? AND axis_kind = ? AND excitation_wavelength_key = ?",
                        (slot_id, axis_kind, key),
                    ).fetchone()
                    if profile_row is None or profile_row["active_configuration_id"] is None:
                        raise ConfigurationError(
                            f"No active configuration for slot {slot_id} "
                            f"(axis_kind={axis_kind!r})"
                        )
                    resolved_ids.append((slot_id, profile_row["active_configuration_id"]))
                else:
                    profile_rows = conn.execute(
                        "SELECT active_configuration_id FROM calibration_profiles "
                        "WHERE slot_id = ? AND active_configuration_id IS NOT NULL",
                        (slot_id,),
                    ).fetchall()
                    if not profile_rows:
                        raise ConfigurationError(f"No active configuration for slot: {slot_id}")
                    if len(profile_rows) > 1:
                        raise AmbiguousConfigurationProfileError(
                            f"Slot {slot_id} has {len(profile_rows)} active calibration "
                            "profiles; specify axis_kind (and excitation_wavelength_nm "
                            "for Raman shift) to disambiguate."
                        )
                    resolved_ids.append((slot_id, profile_rows[0]["active_configuration_id"]))

        resolved = []
        for slot_id, configuration_id in resolved_ids:
            record = self.get_configuration(configuration_id)
            self.assert_compatible(record, hardware_context)
            resolved.append(
                {"slot_id": slot_id, "configuration_id": configuration_id}
            )
        return {"catalog_revision": revision, "resolved": resolved}

    def mark_used(self, configuration_id: str) -> None:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE configurations SET last_used_at = ?, use_count = use_count + 1 "
                "WHERE configuration_id = ?",
                (self._now(), configuration_id),
            )
            if cursor.rowcount == 0:
                raise ConfigurationError(f"Unknown configuration: {configuration_id}")
        # last_used_at/use_count live only in the management index; they do not alter
        # the immutable record returned by get_configuration(). Invalidating here made
        # every configuration-backed acquisition re-read and re-hash the JSON during
        # its response snapshot, defeating the cache's purpose.

    def _unlink_best_effort(
        self, relative_paths: list[str]
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Delete JSON record files best-effort. Returns (deleted, errors).

        A missing file counts as already deleted -- restores the pre-catalog
        workflow of removing a record by deleting its file directly. Any other
        OSError (permissions, file in use) is recorded in errors rather than
        raised: by the time this runs the DB rows are already committed, so
        the catalog's own state is correct either way.
        """
        deleted: list[str] = []
        errors: list[dict[str, str]] = []
        for relative_path in relative_paths:
            try:
                (self.root / relative_path).unlink()
                deleted.append(relative_path)
            except FileNotFoundError:
                deleted.append(relative_path)
            except OSError as exc:
                errors.append({"relative_path": relative_path, "error": str(exc)})
        return deleted, errors

    def delete_configuration_version(self, configuration_id: str) -> dict[str, Any]:
        """Delete one archived (non-active) version: its catalog row and its
        immutable JSON file.

        Refuses to delete a configuration that is its own calibration
        profile's current active version -- use delete_profile() or
        delete_slot() for that -- so a profile can never be left with
        active_configuration_id pointing at a row that no longer exists. A
        configuration with no calibration_profile_id at all (a
        migration-skipped/damaged record) is not protected by anything and can
        always be deleted directly.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT c.slot_id, c.relative_path, c.calibration_profile_id, "
                "p.active_configuration_id AS profile_active_configuration_id "
                "FROM configurations c "
                "LEFT JOIN calibration_profiles p "
                "  ON p.calibration_profile_id = c.calibration_profile_id "
                "WHERE c.configuration_id = ?",
                (configuration_id,),
            ).fetchone()
            if row is None:
                raise ConfigurationError(f"Unknown configuration: {configuration_id}")
            if (
                row["calibration_profile_id"] is not None
                and configuration_id == row["profile_active_configuration_id"]
            ):
                raise ConfigurationError(
                    "Cannot delete a calibration profile's active configuration "
                    "version; use delete_profile() to remove the whole profile or "
                    "delete_slot() to remove the whole measurement condition."
                )
            slot_id, relative_path = row["slot_id"], row["relative_path"]
            conn.execute(
                "DELETE FROM configurations WHERE configuration_id = ?",
                (configuration_id,),
            )
            revision = self._increment_revision(conn)

        deleted, errors = self._unlink_best_effort([relative_path])
        self._invalidate_record_cache()
        return {
            "configuration_id": configuration_id,
            "slot_id": slot_id,
            "deleted_files": deleted,
            "file_errors": errors,
            "catalog_revision": revision,
        }

    def delete_profile(self, calibration_profile_id: str) -> dict[str, Any]:
        """Delete one calibration profile -- every configuration version
        under it, plus the profile row itself -- leaving the physical slot
        and any sibling profiles (a different axis_kind, or the same
        axis_kind at a different excitation wavelength) untouched.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM calibration_profiles WHERE calibration_profile_id = ?",
                (calibration_profile_id,),
            ).fetchone() is None:
                raise ConfigurationError(
                    f"Unknown calibration profile: {calibration_profile_id}"
                )
            config_rows = conn.execute(
                "SELECT configuration_id, relative_path FROM configurations "
                "WHERE calibration_profile_id = ?",
                (calibration_profile_id,),
            ).fetchall()
            conn.execute(
                "DELETE FROM configurations WHERE calibration_profile_id = ?",
                (calibration_profile_id,),
            )
            conn.execute(
                "DELETE FROM calibration_profiles WHERE calibration_profile_id = ?",
                (calibration_profile_id,),
            )
            revision = self._increment_revision(conn)

        deleted, errors = self._unlink_best_effort(
            [row["relative_path"] for row in config_rows]
        )
        self._invalidate_record_cache()
        return {
            "calibration_profile_id": calibration_profile_id,
            "deleted_configuration_ids": [
                row["configuration_id"] for row in config_rows
            ],
            "deleted_files": deleted,
            "file_errors": errors,
            "catalog_revision": revision,
        }

    def delete_slot(self, slot_id: str) -> dict[str, Any]:
        """Delete an entire measurement-condition slot: every calibration
        profile at it, every version (active and archived) of each, and every
        one of their JSON files. This is the "I don't need this condition any
        more" operation.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM slots WHERE slot_id = ?", (slot_id,)
            ).fetchone() is None:
                raise ConfigurationError(f"Unknown slot: {slot_id}")
            config_rows = conn.execute(
                "SELECT configuration_id, relative_path FROM configurations "
                "WHERE slot_id = ?",
                (slot_id,),
            ).fetchall()
            # FK order: configurations -> calibration_profiles -> slots.
            conn.execute("DELETE FROM configurations WHERE slot_id = ?", (slot_id,))
            conn.execute("DELETE FROM calibration_profiles WHERE slot_id = ?", (slot_id,))
            conn.execute("DELETE FROM slots WHERE slot_id = ?", (slot_id,))
            revision = self._increment_revision(conn)

        deleted, errors = self._unlink_best_effort(
            [row["relative_path"] for row in config_rows]
        )
        self._invalidate_record_cache()
        return {
            "slot_id": slot_id,
            "deleted_configuration_ids": [
                row["configuration_id"] for row in config_rows
            ],
            "deleted_files": deleted,
            "file_errors": errors,
            "catalog_revision": revision,
        }
