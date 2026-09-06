"""Manage Clients dialog: name-uniqueness guard.

src.core.api_clients.LastSeenRegistry keys the "last request" bookkeeping by client
name alone. Two clients sharing a name would silently show each other's last-seen
IP/timestamp in the dialog, undermining the whole point of naming clients
individually - letting an operator tell who is currently calling so the right one
can be revoked. The dialog is the only place a name is assigned, so it is also the
only place that can prevent the collision.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from src.ui.menu.api_clients_dialog import ApiClientsDialog
    HAS_QT = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class ClientNameUniquenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, names):
        clients = [
            {"name": name, "key": f"key-{name}", "allowed_ips": [], "created": None}
            for name in names
        ]
        return ApiClientsDialog(clients)

    def test_adding_a_duplicate_name_is_rejected(self):
        dialog = self._dialog(["press-controller"])

        with (
            patch(
                "src.ui.menu.api_clients_dialog.QInputDialog.getText",
                return_value=("press-controller", True),
            ),
            patch(
                "src.ui.menu.api_clients_dialog.QMessageBox.warning"
            ) as warning,
        ):
            dialog._on_add()

        self.assertEqual(len(dialog.clients), 1)
        warning.assert_called_once()

    def test_adding_a_new_name_succeeds(self):
        dialog = self._dialog(["press-controller"])

        with patch(
            "src.ui.menu.api_clients_dialog.QInputDialog.getText",
            return_value=("laptop", True),
        ):
            dialog._on_add()

        self.assertEqual([c["name"] for c in dialog.clients], ["press-controller", "laptop"])

    def test_renaming_to_another_clients_name_is_rejected(self):
        dialog = self._dialog(["press-controller", "laptop"])
        dialog._table.selectRow(1)

        with (
            patch(
                "src.ui.menu.api_clients_dialog.QInputDialog.getText",
                return_value=("press-controller", True),
            ),
            patch(
                "src.ui.menu.api_clients_dialog.QMessageBox.warning"
            ) as warning,
        ):
            dialog._on_rename()

        self.assertEqual(dialog.clients[1]["name"], "laptop")
        warning.assert_called_once()

    def test_renaming_to_its_own_current_name_is_allowed(self):
        dialog = self._dialog(["press-controller", "laptop"])
        dialog._table.selectRow(1)

        with patch(
            "src.ui.menu.api_clients_dialog.QInputDialog.getText",
            return_value=("laptop", True),
        ):
            dialog._on_rename()

        self.assertEqual(dialog.clients[1]["name"], "laptop")

    def test_renaming_to_a_free_name_succeeds(self):
        dialog = self._dialog(["press-controller", "laptop"])
        dialog._table.selectRow(1)

        with patch(
            "src.ui.menu.api_clients_dialog.QInputDialog.getText",
            return_value=("desktop", True),
        ):
            dialog._on_rename()

        self.assertEqual(dialog.clients[1]["name"], "desktop")


if __name__ == "__main__":
    unittest.main()
