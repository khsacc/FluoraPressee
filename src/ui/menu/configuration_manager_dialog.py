"""Hardware > Manage Configuration Files -- inspect and delete the versioned
configuration registry (src/configuration_catalog.py).

Before this dialog, there was no place in the GUI to see the full stored
contents of a configuration (ROI, calibration coefficients, actual centre
wavelength, ...) -- only the compact summaries `ConfigurationBrowserDialog`
shows while loading one. Selecting a row here fetches and displays the
complete record. Deletion is layered on top of that: checking a row and
clicking Delete permanently removes it (and, for an active row, its whole
slot/measurement condition) from both the SQLite index and disk.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.configuration_catalog import ConfigurationError, format_configuration_label


_REFERENCE_KIND_LABELS = {
    "emission_lines": "Emission-line standard",
    "emission_lines_with_excitation": "Emission-line standard (converted via excitation)",
    "raman_standard": "Raman-shift standard",
}


def _reference_kind_text(calibration: dict[str, Any]) -> str:
    label = _REFERENCE_KIND_LABELS.get(
        calibration.get("reference_kind"), calibration.get("reference_kind") or "—"
    )
    standard_names = [
        standard.get("display_name") or standard.get("standard_id")
        for standard in calibration.get("standards") or []
    ]
    if standard_names:
        label += f" ({', '.join(standard_names)})"
    return label


def _format_record_details(record: dict[str, Any]) -> str:
    """Render every field of a stored configuration record for human inspection."""
    compatibility = record["compatibility"]
    spectrometer = record["spectrometer"]
    detector = record["detector"]
    calibration = record["calibration"]
    coefficients = calibration["coefficients"]

    lines = [
        f"Configuration ID: {record['configuration_id']}",
        f"Slot ID: {record['slot_id']}",
        f"Calibration profile ID: {record.get('calibration_profile_id') or '—'}",
        f"Created: {record['created_at']}",
        "",
        "Compatibility",
        f"  Spectrometer: {compatibility.get('spectrometer_model') or '—'} "
        f"(serial {compatibility.get('spectrometer_serial_number') or '—'})",
        f"  Camera: {compatibility.get('camera_model') or '—'} "
        f"(serial {compatibility.get('camera_serial_number') or '—'})",
        "",
        "Grating",
        f"  Index {spectrometer['grating_index']}, "
        f"{spectrometer['grating_grooves_per_mm']} g/mm",
        f"  Target centre: {spectrometer['target_center_wavelength_nm']:.3f} nm   "
        f"Actual centre: {spectrometer['actual_center_wavelength_nm']:.3f} nm",
        "",
        "Detector / ROI",
        f"  Mode: {detector['roi_mode']}   "
        f"Rows {detector['roi_start']}–{detector['roi_end']}",
        f"  Detector: {detector['detector_width']} x {detector['detector_height']}",
        "",
        "Calibration",
        f"  Source: {calibration.get('source', '—')}   Unit: {calibration['unit']}"
        + (
            f"   Excitation: {calibration['excitation_wavelength_nm']} nm"
            if calibration.get("excitation_wavelength_nm") is not None else ""
        ),
        f"  Reference: {_reference_kind_text(calibration)}",
        f"  Coefficients: c0={coefficients['c0']!r}  c1={coefficients['c1']!r}  "
        f"c2={coefficients['c2']!r}",
    ]
    return "\n".join(lines)


class ConfigurationManagerDialog(QDialog):
    """List every stored configuration (regardless of connected hardware),
    show its full contents on selection, and let the operator delete
    slots/versions that are no longer needed.
    """

    APPLIED_ROW_COLOR = QColor("#FCE8E8")
    _COLUMNS = (
        "", "Hardware", "Grating", "Centre", "ROI",
        "Axis", "Status", "Created", "Last used",
    )

    def __init__(
        self,
        catalog,
        *,
        active_configuration_id=None,
        positioned_configuration_id=None,
        ui_lock_check=None,
        parent=None,
    ):
        super().__init__(parent)
        self.catalog = catalog
        self.active_configuration_id = active_configuration_id
        self.positioned_configuration_id = positioned_configuration_id
        self._ui_lock_check = ui_lock_check or (lambda: False)
        self.active_configuration_was_deleted = False

        self._row_summaries: list[dict[str, Any]] = []
        self._row_checkboxes: list[QCheckBox] = []
        self._current_configuration_id: str | None = None

        self.setWindowTitle("Manage Configuration Files")
        self.resize(960, 640)

        layout = QVBoxLayout(self)
        description = QLabel(
            "Every stored measurement condition, regardless of which spectrometer/"
            "camera is currently connected. Select a row to view its full stored "
            "condition (ROI, calibration coefficients, etc.). Deleting a slot "
            "permanently removes all of its versions and JSON files."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self.chk_history = QCheckBox("Show version history")
        self.chk_history.toggled.connect(self.refresh)
        controls.addWidget(self.chk_history)
        controls.addStretch()
        self.btn_open_folder = QPushButton("Open records folder")
        self.btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        controls.addWidget(self.btn_open_folder)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        controls.addWidget(self.btn_refresh)
        layout.addLayout(controls)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self._COLUMNS))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, len(self._COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(self._on_current_row_changed)
        layout.addWidget(self.table)

        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        self.details_text = QPlainTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setFont(QFont("Courier New"))
        self.details_text.setPlainText("Select a row to view its stored condition.")
        details_layout.addWidget(self.details_text)
        layout.addWidget(details_group)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

        self.footer_label = QLabel("")
        self.footer_label.setStyleSheet("color: #666;")
        layout.addWidget(self.footer_label)

        buttons_row = QHBoxLayout()
        self.btn_export = QPushButton("Export Selected JSON…")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export_clicked)
        buttons_row.addWidget(self.btn_export)
        self.btn_delete = QPushButton("Delete Selected…")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        buttons_row.addWidget(self.btn_delete)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(
            self.accept
        )
        layout.addWidget(self.buttons)

        self.refresh()

    # -- formatting helpers --------------------------------------------------

    @staticmethod
    def _roi_text(roi):
        if roi["mode"] == "1d_roi":
            return f"Custom {roi['start']}–{roi['end']}"
        if roi["mode"] == "1d_full":
            return "Full range"
        return "2D image"

    @staticmethod
    def _created_text(value):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _hardware_text(compatibility):
        spectrometer = compatibility.get("spectrometer_model") or "?"
        if compatibility.get("spectrometer_serial_number"):
            spectrometer += f" / {compatibility['spectrometer_serial_number']}"
        camera = compatibility.get("camera_model") or "?"
        if compatibility.get("camera_serial_number"):
            camera += f" / {compatibility['camera_serial_number']}"
        return f"{spectrometer}  +  {camera}"

    @staticmethod
    def _center_text(summary):
        center_nm = summary["center_wavelength_nm"]
        excitation = summary.get("excitation_wavelength_nm")
        if summary.get("axis_kind") == "raman_shift" and excitation:
            shift_cm1 = (1e7 / excitation) - (1e7 / center_nm)
            return f"{shift_cm1:.2f} cm⁻¹"
        return f"{center_nm:.3f} nm"

    @staticmethod
    def _axis_text(summary):
        if summary.get("migration_error"):
            return "Unavailable"
        axis_kind = summary.get("axis_kind")
        if axis_kind == "raman_shift":
            excitation = summary.get("excitation_wavelength_nm")
            return f"Raman shift @ {excitation:.3f} nm" if excitation else "Raman shift"
        if axis_kind == "wavelength":
            return "Wavelength"
        return summary.get("calibration", {}).get("unit") or "—"

    def _last_used_text(self, summary):
        last_used_at = summary.get("last_used_at")
        if not last_used_at:
            return "Never used"
        return f"{summary.get('use_count', 0)}× · {self._created_text(last_used_at)}"

    def _is_currently_loaded(self, summary):
        configuration_id = summary["configuration_id"]
        return configuration_id in {
            self.active_configuration_id, self.positioned_configuration_id
        }

    # -- population -----------------------------------------------------------

    def refresh(self):
        result = self.catalog.list_all(
            active_only=not self.chk_history.isChecked(), limit=500
        )
        self.table.setRowCount(0)
        self._row_summaries = []
        self._row_checkboxes = []

        for summary in result["items"]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_summaries.append(summary)

            applied = self._is_currently_loaded(summary)
            checkbox = QCheckBox()
            checkbox.stateChanged.connect(self._update_delete_button)
            checkbox_container = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_container)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox_layout.addWidget(checkbox)
            if applied:
                checkbox_container.setStyleSheet(
                    f"background-color: {self.APPLIED_ROW_COLOR.name()};"
                )
                checkbox.setToolTip("Currently loaded in this session")
            self.table.setCellWidget(row, 0, checkbox_container)
            self._row_checkboxes.append(checkbox)

            grating = summary["grating"]
            values = [
                self._hardware_text(summary["compatibility"]),
                f"{grating['grooves_per_mm']} g/mm (slot {grating['index']})",
                self._center_text(summary),
                self._roi_text(summary["roi"]),
                self._axis_text(summary),
                summary.get("status", "active" if summary.get("active") else "archived").capitalize(),
                self._created_text(summary["created_at"]),
                self._last_used_text(summary),
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if applied:
                    item.setBackground(self.APPLIED_ROW_COLOR)
                    item.setToolTip("Currently loaded in this session")
                self.table.setItem(row, column, item)

        if result["items"]:
            self.status_label.setText(
                f"{result['total']} shown (catalog revision {result['catalog_revision']})"
            )
        else:
            self.status_label.setText("No configurations are stored yet.")

        self._update_footer()
        self._update_delete_button()
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self._current_configuration_id = None
            self.details_text.setPlainText("Select a row to view its stored condition.")
            self.btn_export.setEnabled(False)

    def _update_footer(self):
        slot_total = self.catalog.count_slots()
        profile_total = self.catalog.count_profiles()
        version_total = self.catalog.list_all(active_only=False, limit=1)["total"]
        files = list(self.catalog.records_root.rglob("*.json"))
        size_mb = sum(path.stat().st_size for path in files) / (1024 * 1024)
        self.footer_label.setText(
            f"{slot_total} slot(s) · {profile_total} calibration profile(s) · "
            f"{version_total} version(s) · {len(files)} file(s) · ~{size_mb:.2f} MB on disk"
        )

    # -- selection / details ---------------------------------------------------

    def _on_current_row_changed(self, current_row, *_args):
        if current_row < 0 or current_row >= len(self._row_summaries):
            self._current_configuration_id = None
            self.details_text.setPlainText("Select a row to view its stored condition.")
            self.btn_export.setEnabled(False)
            return
        summary = self._row_summaries[current_row]
        configuration_id = summary["configuration_id"]
        self._current_configuration_id = configuration_id
        try:
            record = self.catalog.get_configuration(configuration_id)
            self.details_text.setPlainText(_format_record_details(record))
        except ConfigurationError as exc:
            self.details_text.setPlainText(f"Failed to load this configuration:\n{exc}")
        self.btn_export.setEnabled(True)

    # -- export ------------------------------------------------------------

    def _on_open_folder_clicked(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.catalog.root)))

    def _on_export_clicked(self):
        if not self._current_configuration_id:
            return
        try:
            record = self.catalog.get_configuration(self._current_configuration_id)
        except ConfigurationError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Configuration",
            f"{self._current_configuration_id}.json", "JSON files (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    # -- delete ------------------------------------------------------------

    def _update_delete_button(self):
        any_checked = any(checkbox.isChecked() for checkbox in self._row_checkboxes)
        locked = self._ui_lock_check()
        self.btn_delete.setEnabled(any_checked and not locked)
        if locked:
            self.btn_delete.setToolTip(
                "Delete is disabled while a sequential run or the API server is active."
            )
        elif not any_checked:
            self.btn_delete.setToolTip("Check one or more rows to delete.")
        else:
            self.btn_delete.setToolTip("")

    def _confirm(self, message: str) -> bool:
        reply = QMessageBox.question(
            self, "Confirm deletion", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _build_delete_plan(self):
        plan = []
        for row_index, checkbox in enumerate(self._row_checkboxes):
            if not checkbox.isChecked():
                continue
            summary = self._row_summaries[row_index]
            if not summary.get("active"):
                # Not any profile's active row (or an orphaned/migration-error row,
                # which nothing protects) -- always a plain single-version delete.
                kind = "version"
            elif summary.get("profile_count_for_slot", 1) > 1:
                # This slot also has at least one other calibration profile (e.g. a
                # Wavelength calibration alongside this Raman one) -- deleting this
                # profile must not remove the whole physical slot.
                kind = "profile"
            else:
                kind = "slot"
            plan.append((kind, summary))
        return plan

    def _describe_plan_item(self, kind, summary) -> str:
        label = summary.get("display_label") or format_configuration_label(summary)
        warning = (
            "WARNING: this is the configuration currently loaded in this session.\n  "
            if self._is_currently_loaded(summary) else ""
        )
        if kind == "slot":
            return (
                f"{warning}• Delete slot {label} — removes ALL saved versions "
                "(including the active calibration) and their files. "
                "This cannot be undone."
            )
        if kind == "profile":
            return (
                f"{warning}• Delete calibration profile {label} "
                f"({self._axis_text(summary)}) — removes this profile's versions "
                "only; other calibrations at the same physical condition "
                "(grating/centre/ROI) are kept. This cannot be undone."
            )
        created = self._created_text(summary["created_at"])
        return (
            f"{warning}• Delete archived version of {label} (created {created}) — "
            "removes 1 file. The active calibration is kept."
        )

    def _on_delete_clicked(self):
        plan = self._build_delete_plan()
        if not plan:
            return
        message = "\n".join(
            self._describe_plan_item(kind, summary) for kind, summary in plan
        )
        if not self._confirm(message):
            return

        deleted_slots = 0
        deleted_profiles = 0
        deleted_versions = 0
        file_errors: list[dict[str, str]] = []
        item_errors: list[str] = []
        deleted_active = False

        for kind, summary in plan:
            try:
                if kind == "slot":
                    result = self.catalog.delete_slot(summary["slot_id"])
                    deleted_slots += 1
                    removed_ids = set(result["deleted_configuration_ids"])
                elif kind == "profile":
                    result = self.catalog.delete_profile(
                        summary["calibration_profile_id"]
                    )
                    deleted_profiles += 1
                    removed_ids = set(result["deleted_configuration_ids"])
                else:
                    result = self.catalog.delete_configuration_version(
                        summary["configuration_id"]
                    )
                    deleted_versions += 1
                    removed_ids = {result["configuration_id"]}
                file_errors.extend(result["file_errors"])
                # Checking only the selected row's own id misses this: deleting a
                # whole profile/slot also removes every other version under it,
                # which can include a configuration the GUI has loaded or is
                # positioned at (e.g. an archived version applied remotely via the
                # API) even though that version isn't the row the operator checked.
                if {self.active_configuration_id, self.positioned_configuration_id} & removed_ids:
                    deleted_active = True
            except ConfigurationError as exc:
                item_errors.append(str(exc))

        if deleted_active:
            self.active_configuration_was_deleted = True

        summary_lines = [
            f"Deleted {deleted_slots} slot(s), {deleted_profiles} calibration "
            f"profile(s), and {deleted_versions} archived version(s) from the catalog."
        ]
        if file_errors:
            summary_lines.append(
                "The catalog was updated, but these files could not be removed "
                "from disk and should be deleted manually:"
            )
            summary_lines.extend(
                f"  {error['relative_path']}: {error['error']}"
                for error in file_errors
            )
        if item_errors:
            summary_lines.append("Some items could not be deleted:")
            summary_lines.extend(f"  {message}" for message in item_errors)
        QMessageBox.information(self, "Delete Configuration Files", "\n".join(summary_lines))

        self.refresh()
