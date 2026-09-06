"""Read-only camera and spectrograph status window."""
from __future__ import annotations

import json
from datetime import datetime

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from src.hardware.status.instrument_status import (
    display_value,
    legacy_camera_snapshot,
    make_report,
    unavailable_device,
)


_POLL_INTERVAL_MS = 500
_REQUEST_TIMEOUT_MS = 10000
_OVERVIEW_KEYS = {
    "controller_model", "head_model", "serial_number", "camera_status",
    "temperature", "temperature_status", "exposure", "centre_wavelength",
    "grating", "turret",
}


class SpectrographStatusWorker(QThread):
    result_ready = pyqtSignal(dict)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller

    def run(self):
        try:
            if not hasattr(self._controller, "get_status_snapshot"):
                snapshot = unavailable_device(
                    type(self._controller).__name__,
                    "Detailed status is not available for this spectrograph backend.",
                )
            else:
                snapshot = self._controller.get_status_snapshot()
        except Exception as exc:
            snapshot = unavailable_device(type(self._controller).__name__, exc)
        self.result_ready.emit(snapshot)


class InstrumentStatusDialog(QDialog):
    def __init__(
        self,
        camera_thread,
        spectrograph_controller,
        busy_check=None,
        gate_acquire=None,
        gate_release=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Instrument Status")
        self.resize(760, 620)

        self._camera_thread = camera_thread
        self._spectrograph_controller = spectrograph_controller
        self._busy_check = busy_check or (lambda: False)
        # A refresh really does query the hardware (camera request_status() and the
        # spectrograph worker below), so it has to hold the same exclusion gate as a
        # measurement. This window is modeless, so the gate is held for one refresh
        # cycle only - never for as long as the window is open, which would block every
        # API request while an operator leaves the status window up.
        self._gate_acquire = gate_acquire
        self._gate_release = gate_release
        self._gate_held = False
        self._camera_supported = hasattr(camera_thread, "request_status") and hasattr(camera_thread, "status_ready")
        self._spectrograph_worker = None
        self._pending = set()
        self._parts = {}
        self._report = None

        if self._camera_supported:
            camera_thread.status_ready.connect(self._on_camera_ready)

        layout = QVBoxLayout(self)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._tabs = QTabWidget()
        self._tables = {}
        for key, title in (
            ("overview", "Overview"),
            ("detector", "Detector"),
            ("spectrograph", "Spectrograph"),
            ("accessories", "Accessories"),
        ):
            table = self._make_table()
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(table)
            self._tabs.addTab(page, title)
            self._tables[key] = table
        layout.addWidget(self._tabs)

        command_layout = QHBoxLayout()
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self.refresh)
        self._copy_button = QPushButton("Copy Summary")
        self._copy_button.clicked.connect(self._copy_summary)
        self._save_button = QPushButton("Save Report...")
        self._save_button.clicked.connect(self._save_report)
        command_layout.addWidget(self._refresh_button)
        command_layout.addWidget(self._copy_button)
        command_layout.addWidget(self._save_button)
        command_layout.addStretch()
        layout.addLayout(command_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)
        layout.addWidget(buttons)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._refresh_controls)
        self._request_timeout = QTimer(self)
        self._request_timeout.setSingleShot(True)
        self._request_timeout.setInterval(_REQUEST_TIMEOUT_MS)
        self._request_timeout.timeout.connect(self._on_request_timeout)
        self._refresh_controls()

    @staticmethod
    def _make_table():
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Parameter", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def showEvent(self, event):
        super().showEvent(event)
        self._poll_timer.start()
        self._refresh_controls()
        if self._report is None and not self._is_busy():
            QTimer.singleShot(0, self.refresh)

    def hideEvent(self, event):
        self._poll_timer.stop()
        super().hideEvent(event)

    def _acquire_gate(self):
        if self._gate_acquire is None:
            return True
        if self._gate_held:
            return True
        if not self._gate_acquire():
            return False
        self._gate_held = True
        return True

    def _maybe_release_gate(self):
        """Release only once nothing is still touching the hardware.

        The spectrograph worker may outlive a timed-out request (it can still hold the
        controller lock), so a timeout deliberately keeps the gate until
        _on_worker_finished() confirms the thread really exited - the same rule
        ApiMixin uses for GET /hardware/spectrometer?refresh=true.
        """
        if not self._gate_held:
            return
        worker = self._spectrograph_worker
        if self._pending or (worker is not None and worker.isRunning()):
            return
        self._gate_held = False
        if self._gate_release is not None:
            self._gate_release()

    def refresh(self):
        if self._pending or self._is_busy():
            self._refresh_controls()
            return

        if not self._acquire_gate():
            self._status_label.setText(
                "The instrument is busy with another operation (a remote request or a "
                "local measurement). Try refreshing again in a moment."
            )
            self._refresh_controls()
            return

        self._parts = {}
        self._pending = {"camera", "spectrograph"}
        self._request_timeout.start()
        self._status_label.setText("Reading instrument status...")
        self._refresh_controls()

        if self._camera_supported:
            try:
                self._camera_thread.request_status()
            except Exception as exc:
                self._parts["camera"] = unavailable_device(
                    type(self._camera_thread).__name__, exc
                )
                self._pending.discard("camera")
        else:
            self._on_camera_ready(
                unavailable_device(type(self._camera_thread).__name__, "Camera status reporting is unavailable.")
            )

        try:
            worker = SpectrographStatusWorker(self._spectrograph_controller, self)
            self._spectrograph_worker = worker
            worker.result_ready.connect(self._on_spectrograph_ready)
            worker.finished.connect(lambda worker=worker: self._on_worker_finished(worker))
            worker.start()
        except Exception as exc:
            self._spectrograph_worker = None
            self._parts["spectrograph"] = unavailable_device(
                type(self._spectrograph_controller).__name__, exc
            )
            self._pending.discard("spectrograph")

        # Either startup call may fail synchronously. Finish immediately if both did;
        # otherwise the surviving request (or the timeout) retains and releases the gate.
        self._finish_if_ready()

    def _on_camera_ready(self, snapshot):
        if "camera" not in self._pending:
            return
        self._parts["camera"] = legacy_camera_snapshot(snapshot, type(self._camera_thread).__name__)
        self._pending.discard("camera")
        self._finish_if_ready()

    def _on_spectrograph_ready(self, snapshot):
        if "spectrograph" not in self._pending:
            return
        self._parts["spectrograph"] = snapshot
        self._pending.discard("spectrograph")
        self._finish_if_ready()

    def _on_worker_finished(self, worker):
        if self._spectrograph_worker is worker:
            self._spectrograph_worker = None
        worker.deleteLater()
        self._maybe_release_gate()
        self._refresh_controls()

    def _finish_if_ready(self):
        if self._pending:
            return
        self._request_timeout.stop()
        try:
            self._report = make_report(
                self._parts["camera"], self._parts["spectrograph"]
            )
            self._render_report()
            self._status_label.setText(
                f"Last updated: {self._report['captured_at']}"
            )
        finally:
            # Formatting/rendering is downstream of the hardware reads and must not
            # turn a display error into a permanently held instrument gate.
            self._maybe_release_gate()
            self._refresh_controls()

    def _on_request_timeout(self):
        if "camera" in self._pending:
            self._parts["camera"] = unavailable_device(
                type(self._camera_thread).__name__, "Timed out while reading camera status."
            )
        if "spectrograph" in self._pending:
            self._parts["spectrograph"] = unavailable_device(
                type(self._spectrograph_controller).__name__,
                "Timed out while reading spectrograph status.",
            )
        self._pending.clear()
        self._finish_if_ready()

    def _render_report(self):
        for table in self._tables.values():
            table.setRowCount(0)

        camera = self._report["camera"]
        spectrograph = self._report["spectrograph"]
        self._render_overview(camera, spectrograph)
        self._render_device(self._tables["detector"], camera)
        self._render_device(self._tables["spectrograph"], spectrograph, excluded={"Accessories"})
        self._render_device(self._tables["accessories"], spectrograph, included={"Accessories"})

        for table in self._tables.values():
            table.resizeRowsToContents()

    def _render_overview(self, camera, spectrograph):
        table = self._tables["overview"]
        self._add_availability(table, "Detector", camera)
        self._add_selected_items(table, camera, _OVERVIEW_KEYS)
        self._add_availability(table, "Spectrograph", spectrograph)
        self._add_selected_items(table, spectrograph, _OVERVIEW_KEYS)

    def _render_device(self, table, device, included=None, excluded=None):
        if not device.get("available"):
            self._add_row(table, "Status", device.get("error") or "Unavailable", "error")
            return
        for section, entries in device.get("sections", {}).items():
            if included is not None and section not in included:
                continue
            if excluded is not None and section in excluded:
                continue
            self._add_section(table, section)
            for entry in entries:
                self._add_entry(table, entry)

    def _add_selected_items(self, table, device, keys):
        for entries in device.get("sections", {}).values():
            for entry in entries:
                if entry.get("key") in keys:
                    self._add_entry(table, entry)

    def _add_availability(self, table, title, device):
        value = "Connected" if device.get("available") else device.get("error", "Unavailable")
        self._add_section(table, title)
        self._add_row(table, "Status", value, "ok" if device.get("available") else "error")

    @staticmethod
    def _add_section(table, title):
        row = table.rowCount()
        table.insertRow(row)
        cell = QTableWidgetItem(title)
        font = QFont()
        font.setBold(True)
        cell.setFont(font)
        cell.setBackground(QBrush(QColor("#e5e7eb")))
        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, 0, cell)
        table.setSpan(row, 0, 1, 2)

    def _add_entry(self, table, entry):
        self._add_row(
            table,
            entry.get("label", entry.get("key", "")),
            display_value(entry),
            entry.get("state", "ok"),
            entry.get("error"),
        )

    @staticmethod
    def _add_row(table, label, value, state="ok", error=None):
        row = table.rowCount()
        table.insertRow(row)
        label_item = QTableWidgetItem(str(label))
        value_item = QTableWidgetItem(str(value))
        if state in ("error", "unsupported"):
            color = QColor("#b91c1c" if state == "error" else "#6b7280")
            value_item.setForeground(QBrush(color))
        if error:
            value_item.setToolTip(str(error))
        table.setItem(row, 0, label_item)
        table.setItem(row, 1, value_item)

    def _copy_summary(self):
        if self._report is None:
            return
        QApplication.clipboard().setText(self._summary_text())
        self._status_label.setText("Instrument summary copied to the clipboard.")

    def _summary_text(self):
        lines = [f"Instrument status captured: {self._report['captured_at']}"]
        for title, device in (("Detector", self._report["camera"]), ("Spectrograph", self._report["spectrograph"])):
            lines.append(f"\n{title}: {'connected' if device.get('available') else 'unavailable'}")
            for entries in device.get("sections", {}).values():
                for entry in entries:
                    if entry.get("key") in _OVERVIEW_KEYS:
                        lines.append(f"{entry['label']}: {display_value(entry)}")
        return "\n".join(lines)

    def _save_report(self):
        if self._report is None:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Instrument Status", f"instrument-status-{stamp}.json", "JSON files (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self._report, handle, indent=2, ensure_ascii=True)
            self._status_label.setText(f"Report saved: {path}")
        except OSError as exc:
            self._status_label.setText(f"Could not save report: {exc}")

    def _is_busy(self):
        try:
            return bool(self._busy_check())
        except Exception:
            return True

    def _refresh_controls(self):
        busy = self._is_busy()
        worker_running = self._spectrograph_worker is not None and self._spectrograph_worker.isRunning()
        fetching = bool(self._pending) or worker_running
        self._refresh_button.setEnabled(not busy and not fetching)
        self._copy_button.setEnabled(self._report is not None and not fetching)
        self._save_button.setEnabled(self._report is not None and not fetching)
        if busy and not fetching:
            self._status_label.setText(
                "Stop the measurement and wait for spectrograph movement to finish before refreshing."
            )

    def shutdown(self):
        self._poll_timer.stop()
        self._request_timeout.stop()
        worker = self._spectrograph_worker
        if worker is not None and worker.isRunning():
            if not worker.wait(3000):
                return False
            self._spectrograph_worker = None
        self._pending.clear()
        self._maybe_release_gate()
        return True
