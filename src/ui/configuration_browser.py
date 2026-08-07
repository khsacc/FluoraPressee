"""Qt selector for active, hardware-compatible configuration records."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QVBoxLayout,
)


class ConfigurationBrowserDialog(QDialog):
    """Display the same selectable summaries intended for the future API."""

    APPLIED_ROW_COLOR = QColor("#FCE8E8")

    def __init__(
        self, catalog, hardware_context, active_configuration_id=None, parent=None
    ):
        super().__init__(parent)
        self.catalog = catalog
        self.hardware_context = hardware_context
        self.active_configuration_id = active_configuration_id
        self.selected_configuration_id = None
        self.selected_slot_id = None
        self._radio_records = {}

        self.setWindowTitle("Load Configuration")
        self.resize(780, 430)

        layout = QVBoxLayout(self)
        description = QLabel(
            "Only configurations compatible with the connected spectrometer, "
            "camera, grating, and detector ROI are shown."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self.chk_history = QCheckBox("Show version history")
        self.chk_history.toggled.connect(self.refresh)
        controls.addWidget(self.chk_history)
        controls.addStretch()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        controls.addWidget(self.btn_refresh)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Select", "Grating", "Centre (nm)", "ROI", "Axis", "Created"]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_load = self.buttons.addButton(
            "Load and Apply", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.btn_load.setEnabled(False)
        self.btn_load.clicked.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.refresh()

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
    def _axis_text(summary):
        """Distinguish Wavelength from Raman-shift-at-a-given-laser (and
        different lasers from each other) so an operator can tell two
        calibration profiles at the same physical slot apart before loading."""
        axis_kind = summary.get("axis_kind")
        if axis_kind == "raman_shift":
            excitation = summary.get("excitation_wavelength_nm")
            return f"Raman shift @ {excitation:.3f} nm" if excitation else "Raman shift"
        if axis_kind == "wavelength":
            return "Wavelength"
        return summary.get("calibration", {}).get("unit") or "—"

    def refresh(self):
        result = self.catalog.list_selectable(
            self.hardware_context,
            active_only=not self.chk_history.isChecked(),
            limit=200,
        )
        self.table.setRowCount(0)
        self._radio_records.clear()
        self._radio_group = QButtonGroup(self.table)
        self._radio_group.setExclusive(True)
        self._radio_group.buttonToggled.connect(self._update_load_button)
        for summary in result["items"]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            applied = (
                summary["configuration_id"] == self.active_configuration_id
            )
            radio = QRadioButton()
            radio.setToolTip("Select this configuration")
            radio_container = QWidget()
            radio_layout = QHBoxLayout(radio_container)
            radio_layout.setContentsMargins(0, 0, 0, 0)
            radio_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            radio_layout.addWidget(radio)
            if applied:
                radio_container.setStyleSheet(
                    f"background-color: {self.APPLIED_ROW_COLOR.name()};"
                )
                radio.setToolTip(
                    "Select this configuration (currently applied)"
                )
            self._radio_group.addButton(radio)
            self._radio_records[radio] = (
                summary["configuration_id"], summary["slot_id"]
            )
            select_item = QTableWidgetItem()
            if applied:
                select_item.setBackground(self.APPLIED_ROW_COLOR)
            self.table.setItem(row, 0, select_item)
            self.table.setCellWidget(row, 0, radio_container)
            grating = summary["grating"]
            values = [
                f"{grating['grooves_per_mm']} g/mm (slot {grating['index']})",
                f"{summary['center_wavelength_nm']:.3f}",
                self._roi_text(summary["roi"]),
                self._axis_text(summary),
                self._created_text(summary["created_at"]),
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, summary["configuration_id"])
                    item.setData(Qt.ItemDataRole.UserRole + 1, summary["slot_id"])
                    if not summary["active"]:
                        item.setToolTip("Archived version")
                if applied:
                    item.setBackground(self.APPLIED_ROW_COLOR)
                self.table.setItem(row, column, item)

        if result["items"]:
            kind = "versions" if self.chk_history.isChecked() else "active configurations"
            self.status_label.setText(
                f"{result['total']} compatible {kind} "
                f"(catalog revision {result['catalog_revision']})"
            )
        else:
            self.status_label.setText(
                "No compatible configuration is available. Calibrate this grating, "
                "centre position, and ROI first."
            )
        self._update_load_button()

    def _update_load_button(self):
        self.btn_load.setEnabled(self._radio_group.checkedButton() is not None)

    def _accept_selection(self):
        radio = self._radio_group.checkedButton()
        if radio is None:
            return
        self.selected_configuration_id, self.selected_slot_id = (
            self._radio_records[radio]
        )
        self.accept()
