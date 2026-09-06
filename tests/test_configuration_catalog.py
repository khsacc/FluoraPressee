import hashlib
import threading
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.core.configuration_catalog import (
    AmbiguousConfigurationProfileError,
    ConfigurationCatalog,
    ConfigurationCompatibilityError,
    ConfigurationError,
    ConfigurationValidationError,
    excitation_wavelength_key,
    format_configuration_label,
)


def draft(
    *,
    center=690.0,
    grating_index=1,
    grooves=600,
    roi_mode="1d_roi",
    roi_start=45,
    roi_end=65,
    spectrometer_serial="SPEC-1",
    camera_serial="CAM-1",
    spectrometer_model="SP-2750",
    camera_model="DU-401",
    c0=669.4,
    unit="Wavelength",
    excitation_wavelength_nm=None,
):
    return {
        "compatibility": {
            "spectrometer_model": spectrometer_model,
            "spectrometer_serial_number": spectrometer_serial,
            "camera_model": camera_model,
            "camera_serial_number": camera_serial,
        },
        "spectrometer": {
            "grating_index": grating_index,
            "grating_grooves_per_mm": grooves,
            "target_center_wavelength_nm": center,
            "actual_center_wavelength_nm": center,
        },
        "detector": {
            "roi_mode": roi_mode,
            "roi_start": roi_start,
            "roi_end": roi_end,
            "detector_width": 1024,
            "detector_height": 127,
        },
        "calibration": {
            "source": "neon_polynomial",
            "unit": unit,
            "excitation_wavelength_nm": (
                excitation_wavelength_nm
                if excitation_wavelength_nm is not None
                else (532.0 if unit == "Raman shift" else None)
            ),
            "coefficients": {"c0": c0, "c1": 0.0208, "c2": -1.2e-7},
        },
    }


def hardware_context(
    *,
    spectrometer_serial="SPEC-1",
    camera_serial="CAM-1",
    spectrometer_model="SP-2750",
    camera_model="DU-401",
    detector_height=127,
):
    return {
        "spectrometer_model": spectrometer_model,
        "spectrometer_serial_number": spectrometer_serial,
        "camera_model": camera_model,
        "camera_serial_number": camera_serial,
        "gratings": [
            {"index": 1, "grooves": 600},
            {"index": 2, "grooves": 1200},
        ],
        "detector_width": 1024,
        "detector_height": detector_height,
    }


class ConfigurationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog = ConfigurationCatalog(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_new_calibration_replaces_active_version_in_same_slot(self):
        first = self.catalog.register_configuration(draft(c0=1.0))
        second = self.catalog.register_configuration(draft(c0=2.0))

        self.assertEqual(first["slot_id"], second["slot_id"])
        self.assertNotEqual(first["configuration_id"], second["configuration_id"])
        self.assertEqual(self.catalog.catalog_revision(), 2)

        active = self.catalog.list_selectable(hardware_context())
        self.assertEqual(active["total"], 1)
        self.assertEqual(
            active["items"][0]["configuration_id"], second["configuration_id"]
        )

        history = self.catalog.list_selectable(
            hardware_context(), active_only=False
        )
        self.assertEqual(history["total"], 2)
        self.assertTrue(history["items"][0]["active"])
        self.assertFalse(history["items"][1]["active"])
        self.assertEqual(
            self.catalog.get_configuration(first["configuration_id"])["calibration"]
            ["coefficients"]["c0"],
            1.0,
        )

    def test_slot_identity_uses_grating_center_and_roi(self):
        base = self.catalog.register_configuration(draft())
        changed_center = self.catalog.register_configuration(draft(center=691.0))
        changed_roi = self.catalog.register_configuration(draft(roi_start=40))
        changed_grating = self.catalog.register_configuration(
            draft(grating_index=2, grooves=1200)
        )

        self.assertEqual(
            len(
                {
                    base["slot_id"],
                    changed_center["slot_id"],
                    changed_roi["slot_id"],
                    changed_grating["slot_id"],
                }
            ),
            4,
        )

    def test_center_is_normalized_to_picometres_for_slot_identity(self):
        first = self.catalog.register_configuration(draft(center=690.0001))
        second = self.catalog.register_configuration(draft(center=690.0004))
        third = self.catalog.register_configuration(draft(center=690.0006))
        self.assertEqual(first["slot_id"], second["slot_id"])
        self.assertNotEqual(second["slot_id"], third["slot_id"])

    def test_list_returns_only_compatible_active_summaries(self):
        compatible = self.catalog.register_configuration(draft())
        self.catalog.register_configuration(
            draft(center=700.0, camera_serial="OTHER-CAMERA")
        )
        self.catalog.register_configuration(
            draft(center=710.0, roi_end=140)
        )

        result = self.catalog.list_selectable(hardware_context())

        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["configuration_id"], compatible["configuration_id"])
        self.assertNotIn("coefficients", item["calibration"])
        self.assertEqual(item["display_label"], "600 g/mm | 690.000 nm | ROI 45–65")

    def test_list_is_paginated_without_loading_record_payloads(self):
        for index in range(5):
            self.catalog.register_configuration(draft(center=680.0 + index))

        result = self.catalog.list_selectable(
            hardware_context(), limit=2, offset=1
        )

        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["limit"], 2)
        self.assertEqual(result["offset"], 1)

    def test_resolve_slots_freezes_exact_active_ids(self):
        first = self.catalog.register_configuration(draft(center=680.0))
        second = self.catalog.register_configuration(draft(center=690.0))

        result = self.catalog.resolve_slots(
            [first["slot_id"], second["slot_id"]], hardware_context()
        )

        self.assertEqual(
            result["resolved"],
            [
                {
                    "slot_id": first["slot_id"],
                    "configuration_id": first["configuration_id"],
                },
                {
                    "slot_id": second["slot_id"],
                    "configuration_id": second["configuration_id"],
                },
            ],
        )

    def test_assert_compatible_reports_serial_and_grating_mismatch(self):
        record = self.catalog.register_configuration(draft())
        incompatible_context = hardware_context(camera_serial="CAM-2")
        incompatible_context["gratings"] = [{"index": 1, "grooves": 1200}]

        with self.assertRaises(ConfigurationCompatibilityError) as raised:
            self.catalog.assert_compatible(record, incompatible_context)

        self.assertIn("Camera serial number does not match", raised.exception.reasons)
        self.assertIn(
            "Grating type does not match the configured slot",
            raised.exception.reasons,
        )

    def test_saved_serial_is_required_even_when_current_query_returns_none(self):
        record = self.catalog.register_configuration(draft())
        context = hardware_context(camera_serial=None)

        with self.assertRaises(ConfigurationCompatibilityError) as raised:
            self.catalog.assert_compatible(record, context)

        self.assertIn("Camera serial number does not match", raised.exception.reasons)
        self.assertEqual(self.catalog.list_selectable(context)["total"], 0)

    def test_model_is_fallback_when_backend_has_no_serial(self):
        record = self.catalog.register_configuration(
            draft(spectrometer_serial=None, camera_serial=None)
        )
        same_models = hardware_context(
            spectrometer_serial=None, camera_serial=None
        )
        wrong_camera = hardware_context(
            spectrometer_serial=None,
            camera_serial=None,
            camera_model="DIFFERENT-CAMERA",
        )

        self.catalog.assert_compatible(record, same_models)
        self.assertEqual(self.catalog.list_selectable(same_models)["total"], 1)
        self.assertEqual(self.catalog.list_selectable(wrong_camera)["total"], 0)

    def test_models_are_verified_and_part_of_slot_identity(self):
        first = self.catalog.register_configuration(draft())
        second = self.catalog.register_configuration(
            draft(spectrometer_model="DIFFERENT-SPECTROMETER")
        )

        self.assertNotEqual(first["slot_id"], second["slot_id"])
        with self.assertRaises(ConfigurationCompatibilityError) as raised:
            self.catalog.assert_compatible(
                first, hardware_context(spectrometer_model="DIFFERENT-SPECTROMETER")
            )
        self.assertIn("Spectrometer model does not match", raised.exception.reasons)

    def test_registration_rejects_identityless_device(self):
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(
                draft(camera_serial=None, camera_model=None)
            )

    def test_catalog_v1_is_migrated_with_model_identity(self):
        record = self.catalog.register_configuration(
            draft(spectrometer_serial=None, camera_serial=None)
        )
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "UPDATE catalog_meta SET value = '1' WHERE key = 'schema_version'"
            )
            conn.execute(
                "UPDATE slots SET signature = ? WHERE slot_id = ?",
                ("legacy-v1-signature", record["slot_id"]),
            )
            conn.execute("DROP INDEX idx_slots_hardware_identity")
            conn.execute("ALTER TABLE slots DROP COLUMN spectrometer_model")
            conn.execute("ALTER TABLE slots DROP COLUMN camera_model")

        migrated = ConfigurationCatalog(self.tempdir.name)
        result = migrated.list_selectable(
            hardware_context(spectrometer_serial=None, camera_serial=None)
        )
        with sqlite3.connect(database_path) as conn:
            schema_version = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(slots)").fetchall()
            }
            profile_id = conn.execute(
                "SELECT calibration_profile_id FROM configurations WHERE configuration_id = ?",
                (record["configuration_id"],),
            ).fetchone()[0]

        self.assertEqual(schema_version, "3")
        self.assertIn("spectrometer_model", columns)
        self.assertIn("camera_model", columns)
        self.assertEqual(result["total"], 1)
        self.assertIsNotNone(profile_id)

    def test_v1_catalog_migrates_straight_through_v2_and_v3_in_one_open(self):
        record = self.catalog.register_configuration(draft())
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "UPDATE catalog_meta SET value = '1' WHERE key = 'schema_version'"
            )

        migrated = ConfigurationCatalog(self.tempdir.name)
        item = migrated.list_all()["items"][0]

        with sqlite3.connect(database_path) as conn:
            schema_version = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        self.assertEqual(schema_version, "3")
        self.assertEqual(item["configuration_id"], record["configuration_id"])
        self.assertIsNotNone(item["calibration_profile_id"])

    def test_v2_catalog_migrates_to_v3_alone(self):
        record = self.catalog.register_configuration(draft())
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "UPDATE catalog_meta SET value = '2' WHERE key = 'schema_version'"
            )

        migrated = ConfigurationCatalog(self.tempdir.name)
        item = migrated.list_all()["items"][0]
        self.assertEqual(item["configuration_id"], record["configuration_id"])
        self.assertIsNotNone(item["calibration_profile_id"])

    @staticmethod
    def _write_authentic_legacy_catalog(root, *, schema_version):
        """Build a catalog.sqlite3 from the real pre-calibration_profiles DDL
        (not a fresh-schema database with only its schema_version rolled back)
        plus a matching on-disk v1 JSON record, so migration is exercised
        against what an actual old installation's files look like on disk.
        schema_version=1 additionally omits slots.spectrometer_model/camera_model,
        matching the real v1 shape _migrate_v1_to_v2 expects to ALTER in."""
        root = Path(root)
        configuration_id = "cfg_legacy0000000000000000000000"
        slot_id = "slot_legacy000000000000000000000"
        relative_path = Path("records", "2024", "01", f"{configuration_id}.json")
        record_path = root / relative_path
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "configuration_id": configuration_id,
                    "slot_id": slot_id,
                    "compatibility": {
                        "spectrometer_model": "SP-2750",
                        "spectrometer_serial_number": "SPEC-1",
                        "camera_model": "DU-401",
                        "camera_serial_number": "CAM-1",
                    },
                    "spectrometer": {
                        "grating_index": 1,
                        "grating_grooves_per_mm": 600,
                        "target_center_wavelength_nm": 690.0,
                        "actual_center_wavelength_nm": 690.0,
                    },
                    "detector": {
                        "roi_mode": "1d_roi",
                        "roi_start": 45,
                        "roi_end": 65,
                        "detector_width": 1024,
                        "detector_height": 127,
                    },
                    "calibration": {
                        "source": "neon_polynomial",
                        "unit": "Wavelength",
                        "excitation_wavelength_nm": None,
                        "coefficients": {"c0": 669.4, "c1": 0.0208, "c2": -1.2e-7},
                    },
                    "display": {"mode": "Wavelength", "excitation_wavelength_nm": None},
                },
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        digest = hashlib.sha256(record_path.read_bytes()).hexdigest()

        database_path = root / "catalog.sqlite3"
        slot_columns = """
                slot_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                spectrometer_serial TEXT,
                camera_serial TEXT,
        """
        if schema_version >= 2:
            slot_columns = """
                slot_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                spectrometer_model TEXT,
                spectrometer_serial TEXT,
                camera_model TEXT,
                camera_serial TEXT,
            """
        with sqlite3.connect(database_path) as conn:
            conn.executescript(
                f"""
                CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE slots (
                    {slot_columns}
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
                CREATE TABLE configurations (
                    configuration_id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
                    calibration_unit TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(slot_id) REFERENCES slots(slot_id)
                );
                CREATE INDEX idx_configurations_slot_created
                    ON configurations(slot_id, created_at DESC);
                CREATE INDEX idx_configurations_status_created
                    ON configurations(status, created_at DESC);
                CREATE INDEX idx_slots_active_conditions
                    ON slots(grating_index, center_position_pm, roi_mode, roi_start, roi_end);
                """
            )
            now = "2024-01-01T00:00:00+00:00"
            if schema_version >= 2:
                conn.execute(
                    "INSERT INTO slots(slot_id, signature, spectrometer_model, "
                    "spectrometer_serial, camera_model, camera_serial, grating_index, "
                    "grating_grooves_per_mm, center_position_pm, roi_mode, roi_start, "
                    "roi_end, active_configuration_id, created_at, updated_at) "
                    "VALUES (?, 'legacy-sig', 'SP-2750', 'SPEC-1', 'DU-401', 'CAM-1', "
                    "1, 600, 690000000, '1d_roi', 45, 65, NULL, ?, ?)",
                    (slot_id, now, now),
                )
                conn.execute(
                    "CREATE INDEX idx_slots_hardware_identity ON slots("
                    "spectrometer_serial, spectrometer_model, camera_serial, camera_model)"
                )
            else:
                conn.execute(
                    "INSERT INTO slots(slot_id, signature, spectrometer_serial, "
                    "camera_serial, grating_index, grating_grooves_per_mm, "
                    "center_position_pm, roi_mode, roi_start, roi_end, "
                    "active_configuration_id, created_at, updated_at) "
                    "VALUES (?, 'legacy-sig', 'SPEC-1', 'CAM-1', 1, 600, 690000000, "
                    "'1d_roi', 45, 65, NULL, ?, ?)",
                    (slot_id, now, now),
                )
            conn.execute(
                "INSERT INTO configurations(configuration_id, slot_id, created_at, "
                "status, calibration_unit, relative_path, sha256) "
                "VALUES (?, ?, ?, 'active', 'Wavelength', ?, ?)",
                (configuration_id, slot_id, now, relative_path.as_posix(), digest),
            )
            conn.execute(
                "INSERT INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                (str(schema_version),),
            )
            conn.execute("INSERT INTO catalog_meta(key, value) VALUES('revision', '0')")
            conn.commit()
        return configuration_id, slot_id

    def test_real_v2_schema_database_opens_and_migrates_without_crashing(self):
        # Regression test for a real bug: a genuinely pre-existing v2 catalog
        # (no calibration_profiles table, no configurations.calibration_profile_id
        # column at all -- not a fresh v3 database with just its schema_version
        # string rolled back) used to crash with "OperationalError: no such
        # column: calibration_profile_id" before _migrate_catalog() ever ran,
        # because idx_configurations_profile_created was created unconditionally
        # in the same executescript as the rest of the fresh-database DDL.
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            configuration_id, _ = self._write_authentic_legacy_catalog(
                legacy_dir.name, schema_version=2
            )
            migrated = ConfigurationCatalog(legacy_dir.name)
            item = migrated.get_configuration(configuration_id)
            self.assertIsNotNone(item["calibration_profile_id"])
            summary = migrated.list_all()["items"][0]
            self.assertEqual(summary["configuration_id"], configuration_id)
            self.assertIsNotNone(summary["calibration_profile_id"])
        finally:
            legacy_dir.cleanup()

    def test_real_v1_schema_database_opens_and_migrates_without_crashing(self):
        # Same as above, but starting one stage further back: slots also
        # lacks spectrometer_model/camera_model, so both migration stages
        # (v1->v2 and v2->v3) run in the same open against real old DDL.
        legacy_dir = tempfile.TemporaryDirectory()
        try:
            configuration_id, _ = self._write_authentic_legacy_catalog(
                legacy_dir.name, schema_version=1
            )
            migrated = ConfigurationCatalog(legacy_dir.name)
            item = migrated.get_configuration(configuration_id)
            self.assertIsNotNone(item["calibration_profile_id"])
            summary = migrated.list_all()["items"][0]
            self.assertEqual(summary["configuration_id"], configuration_id)
            self.assertIsNotNone(summary["calibration_profile_id"])
        finally:
            legacy_dir.cleanup()

    def test_record_is_json_and_does_not_add_acquisition_or_sample_fields(self):
        record = self.catalog.register_configuration(draft())
        loaded = self.catalog.get_configuration(record["configuration_id"])
        record_files = list(Path(self.tempdir.name).glob("records/*/*/*.json"))

        self.assertEqual(len(record_files), 1)
        with record_files[0].open(encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk, loaded)
        self.assertNotIn("exposure_time_s", loaded)
        self.assertNotIn("accumulations", loaded)
        self.assertNotIn("material", loaded)

    def test_invalid_roi_is_rejected_before_any_record_is_written(self):
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(draft(roi_start=70, roi_end=65))
        self.assertEqual(
            list(Path(self.tempdir.name).glob("records/*/*/*.json")), []
        )

    def test_record_label_uses_configuration_structure(self):
        record = self.catalog.register_configuration(
            draft(roi_mode="1d_full", roi_start=0, roi_end=127)
        )
        self.assertEqual(
            format_configuration_label(record),
            "600 g/mm | 690.000 nm | Full ROI",
        )

    def test_list_all_shows_slots_regardless_of_hardware_compatibility(self):
        self.catalog.register_configuration(draft())
        self.catalog.register_configuration(
            draft(center=700.0, camera_serial="OTHER-CAMERA")
        )

        incompatible_context = hardware_context(camera_serial="OTHER-CAMERA")
        self.assertEqual(
            self.catalog.list_selectable(incompatible_context)["total"], 1
        )
        self.assertEqual(self.catalog.list_all()["total"], 2)

    def test_list_all_active_only_default_hides_archived_versions(self):
        self.catalog.register_configuration(draft(c0=1.0))
        self.catalog.register_configuration(draft(c0=2.0))

        self.assertEqual(self.catalog.list_all()["total"], 1)
        self.assertEqual(self.catalog.list_all(active_only=False)["total"], 2)

    def test_list_all_includes_use_count_and_last_used_at(self):
        record = self.catalog.register_configuration(draft())
        self.catalog.mark_used(record["configuration_id"])
        self.catalog.mark_used(record["configuration_id"])

        item = self.catalog.list_all()["items"][0]
        self.assertEqual(item["use_count"], 2)
        self.assertIsNotNone(item["last_used_at"])
        self.assertEqual(item["status"], "active")

    def test_delete_configuration_version_removes_archived_row_and_file(self):
        first = self.catalog.register_configuration(draft(c0=1.0))
        self.catalog.register_configuration(draft(c0=2.0))

        result = self.catalog.delete_configuration_version(
            first["configuration_id"]
        )

        self.assertEqual(result["deleted_files"], [
            f"records/{first['created_at'][0:4]}/{first['created_at'][5:7]}/"
            f"{first['configuration_id']}.json"
        ])
        self.assertEqual(result["file_errors"], [])
        with self.assertRaises(ConfigurationError):
            self.catalog.get_configuration(first["configuration_id"])
        self.assertEqual(self.catalog.list_all(active_only=False)["total"], 1)
        remaining_files = list(Path(self.tempdir.name).glob("records/*/*/*.json"))
        self.assertEqual(len(remaining_files), 1)

    def test_delete_configuration_version_refuses_to_delete_the_active_version(self):
        record = self.catalog.register_configuration(draft())

        with self.assertRaises(ConfigurationError):
            self.catalog.delete_configuration_version(record["configuration_id"])

        # Nothing was mutated: the record is still readable.
        self.catalog.get_configuration(record["configuration_id"])
        remaining_files = list(Path(self.tempdir.name).glob("records/*/*/*.json"))
        self.assertEqual(len(remaining_files), 1)

    def test_delete_configuration_version_unknown_id_raises(self):
        with self.assertRaises(ConfigurationError):
            self.catalog.delete_configuration_version("cfg_does_not_exist")

    def test_delete_slot_removes_all_versions_and_files(self):
        first = self.catalog.register_configuration(draft(c0=1.0))
        self.catalog.register_configuration(draft(c0=2.0))
        other_slot = self.catalog.register_configuration(draft(center=700.0))

        result = self.catalog.delete_slot(first["slot_id"])

        self.assertEqual(len(result["deleted_configuration_ids"]), 2)
        self.assertEqual(len(result["deleted_files"]), 2)
        self.assertEqual(result["file_errors"], [])
        self.assertEqual(self.catalog.list_all(active_only=False)["total"], 1)
        self.assertEqual(
            self.catalog.list_all(active_only=False)["items"][0]["slot_id"],
            other_slot["slot_id"],
        )
        remaining_files = list(Path(self.tempdir.name).glob("records/*/*/*.json"))
        self.assertEqual(len(remaining_files), 1)

    def test_delete_slot_unknown_id_raises(self):
        with self.assertRaises(ConfigurationError):
            self.catalog.delete_slot("slot_does_not_exist")

    def test_delete_slot_treats_already_missing_file_as_deleted(self):
        record = self.catalog.register_configuration(draft())
        record_path = Path(self.tempdir.name) / (
            f"records/{record['created_at'][0:4]}/{record['created_at'][5:7]}/"
            f"{record['configuration_id']}.json"
        )
        record_path.unlink()

        result = self.catalog.delete_slot(record["slot_id"])

        self.assertEqual(result["file_errors"], [])
        self.assertEqual(len(result["deleted_files"]), 1)
        self.assertEqual(self.catalog.list_all(active_only=False)["total"], 0)

    def test_delete_slot_reports_unremovable_file_without_raising_and_still_deletes_the_row(
        self,
    ):
        record = self.catalog.register_configuration(draft())
        record_path = Path(self.tempdir.name) / (
            f"records/{record['created_at'][0:4]}/{record['created_at'][5:7]}/"
            f"{record['configuration_id']}.json"
        )
        record_path.unlink()
        record_path.mkdir()  # unlink() on a directory raises, forcing a file_error

        result = self.catalog.delete_slot(record["slot_id"])

        self.assertEqual(len(result["file_errors"]), 1)
        self.assertEqual(result["deleted_files"], [])
        # The DB row is gone even though the file removal failed.
        self.assertEqual(self.catalog.list_all(active_only=False)["total"], 0)

    # -- calibration profiles ------------------------------------------------

    def test_wavelength_and_raman_profiles_coexist_in_same_slot(self):
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )

        self.assertEqual(wavelength["slot_id"], raman["slot_id"])
        self.assertNotEqual(
            wavelength["calibration_profile_id"], raman["calibration_profile_id"]
        )

        active = self.catalog.list_selectable(hardware_context())
        self.assertEqual(active["total"], 2)
        active_ids = {item["configuration_id"] for item in active["items"]}
        self.assertEqual(
            active_ids, {wavelength["configuration_id"], raman["configuration_id"]}
        )
        for item in active["items"]:
            self.assertTrue(item["active"])

    def test_second_raman_laser_creates_a_third_independent_profile(self):
        raman_532 = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )
        raman_633 = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=633.0)
        )

        self.assertEqual(raman_532["slot_id"], raman_633["slot_id"])
        self.assertNotEqual(
            raman_532["calibration_profile_id"], raman_633["calibration_profile_id"]
        )
        active = self.catalog.list_selectable(hardware_context())
        self.assertEqual(active["total"], 2)

    def test_second_calibration_in_same_profile_still_archives_the_first(self):
        first = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0, c0=1.0)
        )
        second = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0, c0=2.0)
        )

        self.assertEqual(
            first["calibration_profile_id"], second["calibration_profile_id"]
        )
        history = self.catalog.list_selectable(hardware_context(), active_only=False)
        self.assertEqual(history["total"], 2)
        self.assertTrue(history["items"][0]["active"])
        self.assertFalse(history["items"][1]["active"])

    def test_excitation_wavelength_key_boundary(self):
        self.assertEqual(excitation_wavelength_key(532.0), excitation_wavelength_key(532.0))
        self.assertEqual(excitation_wavelength_key(532.0001), excitation_wavelength_key(532.0))
        self.assertNotEqual(excitation_wavelength_key(532.001), excitation_wavelength_key(532.0))

    # -- validation ------------------------------------------------------------

    def test_wavelength_draft_rejects_excitation_wavelength(self):
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(
                draft(unit="Wavelength", excitation_wavelength_nm=532.0)
            )

    def test_raman_draft_rejects_non_positive_excitation_wavelength(self):
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(
                draft(unit="Raman shift", excitation_wavelength_nm=0.0)
            )
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(
                draft(unit="Raman shift", excitation_wavelength_nm=-532.0)
            )

    def test_non_finite_coefficient_is_rejected(self):
        bad = draft(unit="Wavelength")
        bad["calibration"]["coefficients"]["c1"] = float("nan")
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

        bad = draft(unit="Wavelength")
        bad["calibration"]["coefficients"]["c2"] = float("inf")
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_non_finite_center_wavelength_is_rejected(self):
        bad = draft(unit="Wavelength")
        bad["spectrometer"]["target_center_wavelength_nm"] = float("inf")
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_reference_kind_and_standards_are_stored_when_supplied(self):
        raw = draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        raw["calibration"]["reference_kind"] = "raman_standard"
        raw["calibration"]["standards"] = [
            {"standard_id": "ASTM-Naphthalene", "display_name": "Naphthalene (ASTM)",
             "quantity": "raman_shift_cm1"},
        ]
        record = self.catalog.register_configuration(raw)
        loaded = self.catalog.get_configuration(record["configuration_id"])

        self.assertEqual(loaded["calibration"]["reference_kind"], "raman_standard")
        self.assertEqual(
            loaded["calibration"]["standards"][0]["standard_id"], "ASTM-Naphthalene"
        )

    def test_reference_kind_and_standards_default_when_omitted(self):
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0, center=700.0)
        )

        self.assertEqual(
            self.catalog.get_configuration(wavelength["configuration_id"])
            ["calibration"]["reference_kind"],
            "emission_lines",
        )
        self.assertEqual(
            self.catalog.get_configuration(raman["configuration_id"])
            ["calibration"]["reference_kind"],
            "emission_lines_with_excitation",
        )
        self.assertEqual(
            self.catalog.get_configuration(raman["configuration_id"])
            ["calibration"]["standards"],
            [],
        )

    def test_unsupported_reference_kind_is_rejected(self):
        bad = draft(unit="Wavelength")
        bad["calibration"]["reference_kind"] = "made_up_kind"
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_standards_must_be_a_list(self):
        bad = draft(unit="Wavelength")
        bad["calibration"]["standards"] = "Ne-I"
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_raw_spectrum_is_stored_when_supplied(self):
        raw = draft(unit="Wavelength")
        raw["calibration"]["raw_spectrum"] = [100.0, 250.5, 98.2]
        record = self.catalog.register_configuration(raw)
        loaded = self.catalog.get_configuration(record["configuration_id"])

        self.assertEqual(loaded["calibration"]["raw_spectrum"], [100.0, 250.5, 98.2])

    def test_raw_spectrum_defaults_to_none_when_omitted(self):
        record = self.catalog.register_configuration(draft(unit="Wavelength"))
        loaded = self.catalog.get_configuration(record["configuration_id"])

        self.assertIsNone(loaded["calibration"]["raw_spectrum"])

    def test_raw_spectrum_must_be_a_non_empty_list(self):
        bad = draft(unit="Wavelength")
        bad["calibration"]["raw_spectrum"] = []
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

        bad = draft(unit="Wavelength")
        bad["calibration"]["raw_spectrum"] = 123.0
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_raw_spectrum_must_contain_only_finite_numbers(self):
        bad = draft(unit="Wavelength")
        bad["calibration"]["raw_spectrum"] = [1.0, float("nan"), 2.0]
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

        bad = draft(unit="Wavelength")
        bad["calibration"]["raw_spectrum"] = [1.0, float("inf"), 2.0]
        with self.assertRaises(ConfigurationValidationError):
            self.catalog.register_configuration(bad)

    def test_fresh_v3_database_accepts_first_registration(self):
        # Regression test: the calibration_profiles table and
        # configurations.calibration_profile_id column must exist in the
        # executescript DDL itself, not only via the v2->v3 ALTER TABLE
        # migration step -- a brand-new database's schema_version is written
        # as CATALOG_SCHEMA_VERSION immediately, so _migrate_catalog() never
        # runs any migration step for it.
        fresh_dir = tempfile.TemporaryDirectory()
        try:
            fresh_catalog = ConfigurationCatalog(fresh_dir.name)
            record = fresh_catalog.register_configuration(draft())
            self.assertIsNotNone(record["calibration_profile_id"])
            self.assertEqual(fresh_catalog.count_slots(), 1)
            self.assertEqual(fresh_catalog.count_profiles(), 1)
        finally:
            fresh_dir.cleanup()

    # -- migration completeness -------------------------------------------------

    def test_full_history_migration_recovers_archived_profile_behind_active_one(self):
        # Reproduces the pre-fix bug's aftermath: an archived Wavelength record
        # sitting behind a newer active Raman record at the same slot, using
        # the OLD single-active-per-slot bookkeeping directly.
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            # Simulate the pre-migration (v2, no profiles) shape: the buggy
            # archiving already ran, and there is no calibration_profiles data.
            conn.execute("UPDATE catalog_meta SET value = '2' WHERE key = 'schema_version'")
            conn.execute("DELETE FROM calibration_profiles")
            conn.execute("UPDATE configurations SET calibration_profile_id = NULL")
            conn.execute(
                "UPDATE configurations SET status = 'archived' WHERE configuration_id = ?",
                (wavelength["configuration_id"],),
            )
            conn.execute(
                "UPDATE configurations SET status = 'active' WHERE configuration_id = ?",
                (raman["configuration_id"],),
            )

        migrated = ConfigurationCatalog(self.tempdir.name)
        result = migrated.list_selectable(hardware_context(), active_only=False)
        self.assertEqual(result["total"], 2)
        active_items = [item for item in result["items"] if item["active"]]
        self.assertEqual(len(active_items), 2)
        active_ids = {item["configuration_id"] for item in active_items}
        self.assertEqual(
            active_ids, {wavelength["configuration_id"], raman["configuration_id"]}
        )

    def test_migration_skipped_record_stays_visible_and_deletable(self):
        good = self.catalog.register_configuration(draft())
        bad_path = (
            Path(self.tempdir.name)
            / f"records/{good['created_at'][0:4]}/{good['created_at'][5:7]}"
            / "cfg_damaged.json"
        )
        bad_path.write_text("not valid json", encoding="utf-8")
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "INSERT INTO configurations("
                "configuration_id, slot_id, calibration_profile_id, created_at, "
                "status, calibration_unit, relative_path, sha256"
                ") VALUES ('cfg_damaged', ?, NULL, ?, 'active', 'Wavelength', ?, 'bogus')",
                (
                    good["slot_id"],
                    good["created_at"],
                    f"records/{good['created_at'][0:4]}/{good['created_at'][5:7]}/cfg_damaged.json",
                ),
            )
            conn.execute("UPDATE catalog_meta SET value = '2' WHERE key = 'schema_version'")

        migrated = ConfigurationCatalog(self.tempdir.name)
        all_items = migrated.list_all(active_only=False)["items"]
        damaged = next(
            item for item in all_items if item["configuration_id"] == "cfg_damaged"
        )
        self.assertTrue(damaged["migration_error"])
        self.assertEqual(damaged["status"], "unavailable")
        self.assertFalse(damaged["active"])

        # An operator can still delete it directly.
        result = migrated.delete_configuration_version("cfg_damaged")
        self.assertEqual(result["configuration_id"], "cfg_damaged")

    def _write_semantically_broken_record(self, *, unit=None, coefficients=None, excitation_wavelength_nm=None):
        """Like the "not valid json" case above, but the JSON parses fine --
        only a value inside is semantically invalid (bad unit / non-finite
        number). Returns the anchor "good" record used to derive slot_id."""
        good = self.catalog.register_configuration(draft())
        record = draft(unit=unit if unit is not None else "Wavelength")
        record["calibration"]["coefficients"] = coefficients or {"c0": 669.4, "c1": 0.0208, "c2": -1.2e-7}
        if excitation_wavelength_nm is not None:
            record["calibration"]["excitation_wavelength_nm"] = excitation_wavelength_nm
        record["schema_version"] = 1
        record["configuration_id"] = "cfg_damaged"
        record["slot_id"] = good["slot_id"]
        record["created_at"] = good["created_at"]

        bad_path = (
            Path(self.tempdir.name)
            / f"records/{good['created_at'][0:4]}/{good['created_at'][5:7]}"
            / "cfg_damaged.json"
        )
        bad_path.write_text(json.dumps(record, allow_nan=True), encoding="utf-8")
        database_path = Path(self.tempdir.name) / "catalog.sqlite3"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "INSERT INTO configurations("
                "configuration_id, slot_id, calibration_profile_id, created_at, "
                "status, calibration_unit, relative_path, sha256"
                ") VALUES ('cfg_damaged', ?, NULL, ?, 'active', 'Wavelength', ?, 'bogus')",
                (
                    good["slot_id"],
                    good["created_at"],
                    f"records/{good['created_at'][0:4]}/{good['created_at'][5:7]}/cfg_damaged.json",
                ),
            )
            conn.execute("UPDATE catalog_meta SET value = '2' WHERE key = 'schema_version'")
        return good

    def test_migration_skips_record_with_unrecognized_calibration_unit(self):
        # Regression test: _axis_kind_from_unit() treats anything other than
        # "Raman shift" as Wavelength, so a readable-but-corrupted unit used to
        # be silently resurrected as an active Wavelength profile instead of
        # being left for Configuration Manager like other damaged records.
        self._write_semantically_broken_record(unit="not-a-real-unit")

        migrated = ConfigurationCatalog(self.tempdir.name)
        damaged = next(
            item for item in migrated.list_all(active_only=False)["items"]
            if item["configuration_id"] == "cfg_damaged"
        )
        self.assertTrue(damaged["migration_error"])
        self.assertEqual(damaged["status"], "unavailable")

    def test_migration_skips_record_with_non_finite_excitation_wavelength(self):
        # Regression test: excitation_wavelength_key()'s round(nm * 1000) raises
        # OverflowError for Infinity/NaN (json.loads accepts these as an
        # extension), which used to be uncaught -- aborting the entire catalog
        # open instead of skipping just this one damaged record.
        self._write_semantically_broken_record(
            unit="Raman shift", excitation_wavelength_nm=float("inf")
        )

        migrated = ConfigurationCatalog(self.tempdir.name)
        damaged = next(
            item for item in migrated.list_all(active_only=False)["items"]
            if item["configuration_id"] == "cfg_damaged"
        )
        self.assertTrue(damaged["migration_error"])

    def test_migration_skips_record_with_non_finite_calibration_coefficient(self):
        self._write_semantically_broken_record(
            coefficients={"c0": float("nan"), "c1": 0.02, "c2": 0.0}
        )

        migrated = ConfigurationCatalog(self.tempdir.name)
        damaged = next(
            item for item in migrated.list_all(active_only=False)["items"]
            if item["configuration_id"] == "cfg_damaged"
        )
        self.assertTrue(damaged["migration_error"])

    # -- deletion tiers ----------------------------------------------------

    def test_delete_profile_removes_only_its_own_versions(self):
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )

        result = self.catalog.delete_profile(raman["calibration_profile_id"])

        self.assertEqual(result["deleted_configuration_ids"], [raman["configuration_id"]])
        remaining = self.catalog.list_selectable(hardware_context(), active_only=False)
        self.assertEqual(remaining["total"], 1)
        self.assertEqual(
            remaining["items"][0]["configuration_id"], wavelength["configuration_id"]
        )
        # The slot itself is untouched.
        self.assertEqual(self.catalog.count_slots(), 1)

    def test_delete_profile_unknown_id_raises(self):
        with self.assertRaises(ConfigurationError):
            self.catalog.delete_profile("calprof_does_not_exist")

    def test_delete_configuration_version_refuses_active_version_of_non_default_profile(self):
        self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )
        with self.assertRaises(ConfigurationError):
            self.catalog.delete_configuration_version(raman["configuration_id"])

    # -- resolve_slots disambiguation ---------------------------------------

    def test_resolve_slots_raises_when_slot_has_multiple_active_profiles(self):
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )

        with self.assertRaises(AmbiguousConfigurationProfileError):
            self.catalog.resolve_slots([wavelength["slot_id"]], hardware_context())

    def test_resolve_slots_disambiguates_with_axis_kind(self):
        wavelength = self.catalog.register_configuration(draft(unit="Wavelength"))
        raman = self.catalog.register_configuration(
            draft(unit="Raman shift", excitation_wavelength_nm=532.0)
        )

        result = self.catalog.resolve_slots(
            [
                {"slot_id": wavelength["slot_id"], "axis_kind": "wavelength"},
                {
                    "slot_id": raman["slot_id"],
                    "axis_kind": "raman_shift",
                    "excitation_wavelength_nm": 532.0,
                },
            ],
            hardware_context(),
        )

        self.assertEqual(
            result["resolved"],
            [
                {
                    "slot_id": wavelength["slot_id"],
                    "configuration_id": wavelength["configuration_id"],
                },
                {
                    "slot_id": raman["slot_id"],
                    "configuration_id": raman["configuration_id"],
                },
            ],
        )


class RecordCacheTests(unittest.TestCase):
    """get_configuration() memoises records so API responses can snapshot
    configuration state on the GUI thread without a SQLite query + file read + hash
    on every request (work_API_standby.md Step 7a)."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog = ConfigurationCatalog(self.tempdir.name)
        self.record = self.catalog.register_configuration(draft())
        self.configuration_id = self.record["configuration_id"]

    def tearDown(self):
        self.tempdir.cleanup()

    def _record_path(self):
        with sqlite3.connect(self.catalog.database_path) as conn:
            row = conn.execute(
                "SELECT relative_path FROM configurations WHERE configuration_id = ?",
                (self.configuration_id,),
            ).fetchone()
        return self.catalog.root / row[0]

    def test_second_read_is_served_from_memory(self):
        first = self.catalog.get_configuration(self.configuration_id)
        self._record_path().unlink()

        second = self.catalog.get_configuration(self.configuration_id)

        self.assertEqual(first, second)

    def test_mutating_the_returned_record_does_not_poison_the_cache(self):
        first = self.catalog.get_configuration(self.configuration_id)
        first["calibration"]["coefficients"]["c0"] = -1.0
        first["spectrometer"]["grating_index"] = 99

        second = self.catalog.get_configuration(self.configuration_id)

        self.assertEqual(second["calibration"]["coefficients"]["c0"], 669.4)
        self.assertEqual(second["spectrometer"]["grating_index"], 1)

    def test_registering_a_configuration_invalidates_the_cache(self):
        self.catalog.get_configuration(self.configuration_id)
        self._record_path().unlink()
        self.catalog.register_configuration(draft(c0=1.0))

        with self.assertRaises(ConfigurationError):
            self.catalog.get_configuration(self.configuration_id)

    def test_marking_used_keeps_the_immutable_record_cached(self):
        first = self.catalog.get_configuration(self.configuration_id)
        self._record_path().unlink()

        self.catalog.mark_used(self.configuration_id)
        second = self.catalog.get_configuration(self.configuration_id)

        self.assertEqual(second, first)

    def test_deleting_a_version_invalidates_the_cache(self):
        newer = self.catalog.register_configuration(draft(c0=1.0))
        self.catalog.get_configuration(self.configuration_id)

        self.catalog.delete_configuration_version(self.configuration_id)

        with self.assertRaises(ConfigurationError):
            self.catalog.get_configuration(self.configuration_id)
        self.assertIsNotNone(
            self.catalog.get_configuration(newer["configuration_id"])
        )

    def test_raw_spectrum_is_shared_by_reference_across_reads(self):
        """Regression: get_configuration() used to deepcopy() the whole record on
        every call, hit or miss, including calibration.raw_spectrum - a blob that no
        caller anywhere reads back or mutates after registration. Sharing it by
        reference (via deepcopy's memo) removes that cost while _copy_configuration_record
        still independently deep-copies everything else, so mutating one read's
        nested dicts must never be visible in another read.
        """
        raw_spectrum = [1.0, 2.0, 3.0]
        draft_with_spectrum = draft()
        draft_with_spectrum["calibration"]["raw_spectrum"] = raw_spectrum
        record = self.catalog.register_configuration(draft_with_spectrum)
        configuration_id = record["configuration_id"]

        first = self.catalog.get_configuration(configuration_id)
        second = self.catalog.get_configuration(configuration_id)

        self.assertIs(
            first["calibration"]["raw_spectrum"],
            second["calibration"]["raw_spectrum"],
            "raw_spectrum should be the same shared object across reads",
        )
        self.assertIsNot(
            first["calibration"]["coefficients"],
            second["calibration"]["coefficients"],
            "everything other than raw_spectrum must still be independently copied",
        )

        first["calibration"]["coefficients"]["c0"] = -999.0
        third = self.catalog.get_configuration(configuration_id)
        self.assertNotEqual(third["calibration"]["coefficients"]["c0"], -999.0)

    def test_concurrent_delete_during_a_cache_miss_does_not_resurrect_the_record(self):
        """Regression: get_configuration()'s cache-miss path reads from disk with the
        lock released (so I/O doesn't block other threads), then re-takes the lock to
        write the result back. If a delete's _invalidate_record_cache() lands in that
        window, the write-back used to run after it and silently put the just-deleted
        record right back into the cache - served forever until the process restarted.
        The generation counter must catch this: the write-back is skipped whenever the
        generation changed while the disk read was in flight.
        """
        newer = self.catalog.register_configuration(draft(c0=1.0))
        self.catalog._invalidate_record_cache()  # force a real cache miss below

        reached_normalize = threading.Event()
        deleted = threading.Event()
        original_normalize = self.catalog._normalize_record

        def delayed_normalize(*args, **kwargs):
            result = original_normalize(*args, **kwargs)
            reached_normalize.set()
            # Give the deleting thread a bounded window to invalidate the cache
            # before this thread reaches the write-back below.
            deleted.wait(timeout=5)
            return result

        self.catalog._normalize_record = delayed_normalize
        try:
            results = {}

            def reader():
                results["value"] = self.catalog.get_configuration(
                    self.configuration_id
                )

            reader_thread = threading.Thread(target=reader)
            reader_thread.start()
            self.assertTrue(reached_normalize.wait(timeout=5))

            self.catalog.delete_configuration_version(self.configuration_id)
            deleted.set()
            reader_thread.join(timeout=5)
        finally:
            self.catalog._normalize_record = original_normalize

        # The in-flight read still correctly returns what was on disk when it started.
        self.assertEqual(results["value"]["configuration_id"], self.configuration_id)
        # But it must not have been written back into the cache after the delete.
        with self.assertRaises(ConfigurationError):
            self.catalog.get_configuration(self.configuration_id)
        self.assertIsNotNone(
            self.catalog.get_configuration(newer["configuration_id"])
        )


if __name__ == "__main__":
    unittest.main()
