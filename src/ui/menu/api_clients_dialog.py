"""Manage the applications authorised to drive this rig over the API.

Replaces the single "Regenerate Key" action: with Standby listening all day, an
operator needs to revoke one client without cutting off the others, and to see which
client last called (work_API_standby.md 方針7).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.api_clients import new_client


def _masked(key: str) -> str:
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * 8}{key[-4:]}"


class ApiClientsDialog(QDialog):
    COLUMNS = ("Name", "Key", "Allowed IPs", "Created", "Last request")

    def __init__(self, clients, last_seen=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Clients")
        self.resize(760, 380)

        self.clients = [dict(client) for client in clients]
        self._last_seen = last_seen
        self._revealed: set[int] = set()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Each client has its own key, so one can be revoked without affecting the "
            "others. Leave 'Allowed IPs' empty to accept the key from any address; "
            "otherwise list addresses or CIDR ranges separated by commas "
            "(e.g. 192.168.1.42, 192.168.2.0/24).\n"
            "Traffic is not encrypted: anyone able to watch this network segment can "
            "read a key in transit. Use this only on a trusted LAN."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        buttons_row = QHBoxLayout()
        self._add_button = QPushButton("Add…")
        self._rename_button = QPushButton("Rename…")
        self._ips_button = QPushButton("Allowed IPs…")
        self._reveal_button = QPushButton("Reveal key")
        self._copy_button = QPushButton("Copy key")
        self._regenerate_button = QPushButton("Regenerate key")
        self._revoke_button = QPushButton("Revoke")
        for button, slot in (
            (self._add_button, self._on_add),
            (self._rename_button, self._on_rename),
            (self._ips_button, self._on_edit_ips),
            (self._reveal_button, self._on_reveal),
            (self._copy_button, self._on_copy),
            (self._regenerate_button, self._on_regenerate),
            (self._revoke_button, self._on_revoke),
        ):
            button.clicked.connect(slot)
            buttons_row.addWidget(button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._render()

    # ------------------------------------------------------------------

    def _selected_row(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return rows.pop()

    def _render(self):
        self._table.setRowCount(0)
        for row, client in enumerate(self.clients):
            self._table.insertRow(row)
            allowed = ", ".join(client.get("allowed_ips") or []) or "any address"
            last = self._last_seen.get(client["name"]) if self._last_seen else None
            values = (
                client["name"],
                client["key"] if row in self._revealed else _masked(client["key"]),
                allowed,
                client.get("created") or "-",
                f"{last['time']} from {last['ip']}" if last else "-",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, column, item)
        self._refresh_buttons()

    def _refresh_buttons(self):
        has_selection = self._selected_row() is not None
        for button in (
            self._rename_button, self._ips_button, self._reveal_button,
            self._copy_button, self._regenerate_button, self._revoke_button,
        ):
            button.setEnabled(has_selection)

    # ------------------------------------------------------------------

    def _name_taken(self, name, *, excluding_row=None):
        # Names double as the key into LastSeenRegistry (src/core/api_clients.py),
        # which is keyed by name alone - two clients sharing a name would show each
        # other's "last request" IP/timestamp, defeating the point of naming clients
        # individually so an operator can tell who is currently calling.
        return any(
            index != excluding_row and client["name"] == name
            for index, client in enumerate(self.clients)
        )

    def _on_add(self):
        name, ok = QInputDialog.getText(self, "Add client", "Client name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if self._name_taken(name):
            QMessageBox.warning(
                self, "Name already in use",
                f"A client named {name!r} already exists. Choose a different name."
            )
            return
        self.clients.append(new_client(name))
        self._render()
        self._table.selectRow(len(self.clients) - 1)

    def _on_rename(self):
        row = self._selected_row()
        if row is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename client", "Client name:", text=self.clients[row]["name"]
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if self._name_taken(name, excluding_row=row):
            QMessageBox.warning(
                self, "Name already in use",
                f"A client named {name!r} already exists. Choose a different name."
            )
            return
        self.clients[row]["name"] = name
        self._render()

    def _on_edit_ips(self):
        row = self._selected_row()
        if row is None:
            return
        current = ", ".join(self.clients[row].get("allowed_ips") or [])
        text, ok = QInputDialog.getText(
            self, "Allowed IPs",
            "Addresses or CIDR ranges, comma separated (empty = any address):",
            text=current,
        )
        if not ok:
            return
        self.clients[row]["allowed_ips"] = [
            entry.strip() for entry in text.split(",") if entry.strip()
        ]
        self._render()

    def _on_reveal(self):
        row = self._selected_row()
        if row is None:
            return
        self._revealed.symmetric_difference_update({row})
        self._render()
        self._table.selectRow(row)

    def _on_copy(self):
        row = self._selected_row()
        if row is None:
            return
        QApplication.clipboard().setText(self.clients[row]["key"])

    def _on_regenerate(self):
        row = self._selected_row()
        if row is None:
            return
        client = self.clients[row]
        reply = QMessageBox.question(
            self, "Regenerate key",
            f"This invalidates {client['name']}'s current key as soon as you save. "
            "That client will get 401 Unauthorized until it is reconfigured with the "
            "new key.\n\nOther clients are unaffected. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        client["key"] = new_client(client["name"])["key"]
        self._revealed.add(row)
        self._render()
        self._table.selectRow(row)

    def _on_revoke(self):
        row = self._selected_row()
        if row is None:
            return
        client = self.clients[row]
        warning = (
            f"Revoke {client['name']}? Its key stops working as soon as you save."
        )
        if len(self.clients) == 1:
            warning += (
                "\n\nThis is the last client: with none left, every API request will "
                "be rejected with 401 until you add one."
            )
        reply = QMessageBox.question(
            self, "Revoke client", warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self.clients[row]
        self._revealed.clear()
        self._render()
