import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox

from src.core.configuration_catalog import ConfigurationError
from src.ui.menu.configuration_manager_dialog import ConfigurationManagerDialog


class _Catalog:
    """Stub with just enough surface for ConfigurationManagerDialog."""

    def __init__(self):
        self.deleted_slots = []
        self.deleted_versions = []
        self.records_root = Path(tempfile.gettempdir())
        self.root = self.records_root

    @staticmethod
    def _compatibility():
        return {
            "spectrometer_model": "SP-2750",
            "spectrometer_serial_number": "SPEC-1",
            "camera_model": "DU-401",
            "camera_serial_number": "CAM-1",
        }

    def _summary(self, configuration_id, slot_id, status, created_at, last_used_at=None, use_count=0):
        return {
            "slot_id": slot_id,
            "configuration_id": configuration_id,
            "active_configuration_id": "cfg-active",
            "active": status == "active",
            "status": status,
            "created_at": created_at,
            "updated_at": created_at,
            "grating": {"index": 1, "grooves_per_mm": 600},
            "center_wavelength_nm": 690.0,
            "roi": {"mode": "1d_roi", "start": 45, "end": 65},
            "calibration": {"available": True, "unit": "Wavelength"},
            "compatibility": self._compatibility(),
            "display_label": "600 g/mm | 690.000 nm | ROI 45–65",
            "last_used_at": last_used_at,
            "use_count": use_count,
        }

    def _all_summaries(self):
        return [
            self._summary("cfg-active", "slot-1", "active", "2026-07-23T10:00:00+09:00"),
            self._summary("cfg-broken", "slot-2", "active", "2026-07-22T10:00:00+09:00"),
            self._summary(
                "cfg-archived", "slot-1", "archived", "2026-07-20T09:00:00+09:00",
                last_used_at="2026-07-21T09:00:00+09:00", use_count=3,
            ),
        ]

    def list_all(self, *, active_only, limit=500, offset=0):
        items = [
            item for item in self._all_summaries()
            if not active_only or item["status"] == "active"
        ]
        return {
            "items": items, "total": len(items),
            "catalog_revision": 1, "limit": limit, "offset": offset,
        }

    def count_slots(self):
        return len({item["slot_id"] for item in self._all_summaries()})

    def count_profiles(self):
        return len([item for item in self._all_summaries() if item["status"] == "active"])

    def get_configuration(self, configuration_id):
        if configuration_id == "cfg-broken":
            raise ConfigurationError("broken record")
        return {
            "configuration_id": configuration_id,
            "slot_id": "slot-1",
            "created_at": "2026-07-23T10:00:00+09:00",
            "compatibility": self._compatibility(),
            "spectrometer": {
                "grating_index": 1, "grating_grooves_per_mm": 600,
                "target_center_wavelength_nm": 690.0,
                "actual_center_wavelength_nm": 690.012,
            },
            "detector": {
                "roi_mode": "1d_roi", "roi_start": 45, "roi_end": 65,
                "detector_width": 1024, "detector_height": 127,
            },
            "display": {"mode": "Wavelength", "excitation_wavelength_nm": 532.0},
            "calibration": {
                "source": "neon_polynomial", "unit": "Wavelength",
                "excitation_wavelength_nm": None,
                "coefficients": {"c0": 669.4, "c1": 0.0208, "c2": -1.2e-7},
            },
        }

    def delete_slot(self, slot_id):
        self.deleted_slots.append(slot_id)
        return {
            "slot_id": slot_id,
            "deleted_configuration_ids": ["cfg-active", "cfg-archived"],
            "deleted_files": [], "file_errors": [], "catalog_revision": 2,
        }

    def delete_configuration_version(self, configuration_id):
        self.deleted_versions.append(configuration_id)
        return {
            "configuration_id": configuration_id, "slot_id": "slot-1",
            "deleted_files": [], "file_errors": [], "catalog_revision": 2,
        }


class ConfigurationManagerDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.catalog = _Catalog()
        self.dialog = ConfigurationManagerDialog(
            self.catalog, active_configuration_id="cfg-active", parent=None
        )

    def _row_of(self, configuration_id):
        for row, summary in enumerate(self.dialog._row_summaries):
            if summary["configuration_id"] == configuration_id:
                return row
        raise AssertionError(f"{configuration_id} not shown")

    def _checkbox(self, row):
        return self.dialog.table.cellWidget(row, 0).findChild(QCheckBox)

    def test_table_has_checkbox_column_and_expected_columns(self):
        self.assertEqual(self.dialog.table.columnCount(), 9)
        self.assertEqual(
            [
                self.dialog.table.horizontalHeaderItem(i).text()
                for i in range(1, 9)
            ],
            ["Hardware", "Grating", "Centre (nm)", "ROI", "Axis",
             "Status", "Created", "Last used"],
        )

    def test_selecting_a_row_shows_full_record_details(self):
        self.dialog.table.selectRow(self._row_of("cfg-active"))

        text = self.dialog.details_text.toPlainText()
        self.assertIn("Configuration ID: cfg-active", text)
        self.assertIn("c0=669.4", text)

    def test_selecting_a_broken_record_shows_error_without_raising(self):
        self.dialog.table.selectRow(self._row_of("cfg-broken"))

        text = self.dialog.details_text.toPlainText()
        self.assertIn("Failed to load this configuration", text)
        self.assertIn("broken record", text)

    def test_ui_lock_disables_delete_button_regardless_of_checked_rows(self):
        locked_dialog = ConfigurationManagerDialog(
            self.catalog, ui_lock_check=lambda: True, parent=None
        )
        row = 0
        checkbox = locked_dialog.table.cellWidget(row, 0).findChild(QCheckBox)
        checkbox.setChecked(True)

        self.assertFalse(locked_dialog.btn_delete.isEnabled())

    def test_checking_an_archived_row_calls_delete_configuration_version(self):
        self.dialog.chk_history.setChecked(True)
        row = self._row_of("cfg-archived")
        self._checkbox(row).setChecked(True)
        self.assertTrue(self.dialog.btn_delete.isEnabled())

        with patch(
            "src.ui.menu.configuration_manager_dialog.QMessageBox.information",
            return_value=None,
        ):
            self.dialog._confirm = lambda message: True
            self.dialog._on_delete_clicked()

        self.assertEqual(self.catalog.deleted_versions, ["cfg-archived"])
        self.assertEqual(self.catalog.deleted_slots, [])
        self.assertFalse(self.dialog.active_configuration_was_deleted)

    def test_checking_an_active_row_calls_delete_slot_and_flags_currently_loaded(self):
        row = self._row_of("cfg-active")
        self._checkbox(row).setChecked(True)

        with patch(
            "src.ui.menu.configuration_manager_dialog.QMessageBox.information",
            return_value=None,
        ):
            self.dialog._confirm = lambda message: True
            self.dialog._on_delete_clicked()

        self.assertEqual(self.catalog.deleted_slots, ["slot-1"])
        self.assertEqual(self.catalog.deleted_versions, [])
        self.assertTrue(self.dialog.active_configuration_was_deleted)


class _MultiProfileCatalog(_Catalog):
    """A slot with two simultaneously-active calibration profiles: Wavelength
    and Raman shift @ 532 nm -- exercises the "delete profile" tier, which
    must not touch the sibling profile or the physical slot."""

    def __init__(self):
        super().__init__()
        self.deleted_profiles = []

    def _all_summaries(self):
        wavelength = self._summary("cfg-wl", "slot-1", "active", "2026-07-23T10:00:00+09:00")
        wavelength["calibration_profile_id"] = "calprof-wl"
        wavelength["axis_kind"] = "wavelength"
        wavelength["excitation_wavelength_nm"] = None
        wavelength["profile_count_for_slot"] = 2

        raman = self._summary("cfg-raman", "slot-1", "active", "2026-07-23T11:00:00+09:00")
        raman["calibration_profile_id"] = "calprof-raman-532"
        raman["axis_kind"] = "raman_shift"
        raman["excitation_wavelength_nm"] = 532.0
        raman["profile_count_for_slot"] = 2
        raman["calibration"] = {"available": True, "unit": "Raman shift"}

        return [wavelength, raman]

    def delete_profile(self, calibration_profile_id):
        self.deleted_profiles.append(calibration_profile_id)
        return {
            "calibration_profile_id": calibration_profile_id,
            "deleted_configuration_ids": ["cfg-raman"],
            "deleted_files": [], "file_errors": [], "catalog_revision": 2,
        }


class ConfigurationManagerProfileDeletionTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.catalog = _MultiProfileCatalog()
        self.dialog = ConfigurationManagerDialog(self.catalog, parent=None)

    def _checkbox_for(self, configuration_id):
        for row, summary in enumerate(self.dialog._row_summaries):
            if summary["configuration_id"] == configuration_id:
                return self.dialog.table.cellWidget(row, 0).findChild(QCheckBox)
        raise AssertionError(f"{configuration_id} not shown")

    def test_deleting_one_raman_profile_leaves_the_wavelength_profile_and_slot_intact(self):
        self._checkbox_for("cfg-raman").setChecked(True)

        with patch(
            "src.ui.menu.configuration_manager_dialog.QMessageBox.information",
            return_value=None,
        ):
            self.dialog._confirm = lambda message: True
            self.dialog._on_delete_clicked()

        self.assertEqual(self.catalog.deleted_profiles, ["calprof-raman-532"])
        self.assertEqual(self.catalog.deleted_slots, [])
        self.assertEqual(self.catalog.deleted_versions, [])

    def test_axis_column_distinguishes_wavelength_and_raman_profiles(self):
        axis_column = self.dialog._COLUMNS.index("Axis")
        texts = {
            self.dialog.table.item(row, axis_column).text()
            for row in range(self.dialog.table.rowCount())
        }
        self.assertEqual(texts, {"Wavelength", "Raman shift @ 532.000 nm"})


class _ProfileWithArchivedLoadedVersionCatalog(_MultiProfileCatalog):
    """Like _MultiProfileCatalog, but the Raman profile has a second
    (archived) version too -- e.g. one applied remotely via the API while a
    newer calibration became active locally. delete_profile() removes both."""

    def delete_profile(self, calibration_profile_id):
        self.deleted_profiles.append(calibration_profile_id)
        return {
            "calibration_profile_id": calibration_profile_id,
            "deleted_configuration_ids": ["cfg-raman", "cfg-raman-old"],
            "deleted_files": [], "file_errors": [], "catalog_revision": 2,
        }


class ConfigurationManagerDeletionDetectsNonSelectedLoadedVersionTests(unittest.TestCase):
    """Regression test: checking a profile's active row and deleting the whole
    profile also removes every archived version under it. If one of those
    archived versions is what the GUI session actually has loaded/positioned
    (e.g. applied via the API), active_configuration_was_deleted must still be
    set -- even though that version's id isn't the row the operator checked."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.catalog = _ProfileWithArchivedLoadedVersionCatalog()
        self.dialog = ConfigurationManagerDialog(
            self.catalog, positioned_configuration_id="cfg-raman-old", parent=None
        )

    def _checkbox_for(self, configuration_id):
        for row, summary in enumerate(self.dialog._row_summaries):
            if summary["configuration_id"] == configuration_id:
                return self.dialog.table.cellWidget(row, 0).findChild(QCheckBox)
        raise AssertionError(f"{configuration_id} not shown")

    def test_deleting_the_active_row_flags_a_positioned_archived_sibling_as_deleted(self):
        self._checkbox_for("cfg-raman").setChecked(True)

        with patch(
            "src.ui.menu.configuration_manager_dialog.QMessageBox.information",
            return_value=None,
        ):
            self.dialog._confirm = lambda message: True
            self.dialog._on_delete_clicked()

        self.assertEqual(self.catalog.deleted_profiles, ["calprof-raman-532"])
        self.assertTrue(self.dialog.active_configuration_was_deleted)


if __name__ == "__main__":
    unittest.main()
