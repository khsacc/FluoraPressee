"""Failure handling around the GUI's named-client persistence layer."""
import unittest
from unittest.mock import Mock, patch

from src.core.api_clients import LastSeenRegistry
from src.ui.ui_mixins.api_mixin import ApiMixin, local_ip_address
from src.ui.ui_mixins.config_mixin import ConfigMixin


class MigrationHarness(ConfigMixin):
    def save_api_clients(self, _clients):
        raise OSError("disk full")


class ClientEditHarness(ApiMixin):
    def __init__(self):
        self._api_clients = ({"name": "old", "key": "old-key"},)

    def save_api_clients(self, _clients):
        raise OSError("read only")


class RecordingBridge:
    def __init__(self):
        self.posted = []

    def post(self, fn):
        self.posted.append(fn)


class StatusLabel:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text


class FrozenWidget:
    """Records setEnabled() calls so a test can assert they never happen."""

    def __init__(self, current_data=None):
        self.enable_calls = []
        self._current_data = current_data

    def setEnabled(self, enabled):
        self.enable_calls.append(enabled)

    def currentData(self):
        return self._current_data


class LastRequestHarness(ApiMixin):
    def __init__(self, running=True, stopping=False):
        self.gui_bridge = RecordingBridge()
        self._api_last_seen = LastSeenRegistry()
        self._api_server = object() if running else None
        self._api_stopping = stopping
        self._api_last_port = 8765
        self._api_mode = "standby"
        self.lbl_api_status = StatusLabel()
        # If _refresh_last_request_label() ever touches setEnabled() on any of these
        # (the widgets _on_api_state_changed() freezes while the server runs), the
        # recorded call list below will be non-empty.
        self.spin_api_port = FrozenWidget()
        self.combo_api_bind = FrozenWidget(current_data="0.0.0.0")
        self.chk_api_expose_docs = FrozenWidget()
        self.combo_api_mode = FrozenWidget()
        self.state_refreshes = 0

    def _on_api_state_changed(self):
        self.state_refreshes += 1


class ApiClientPersistenceTests(unittest.TestCase):
    def test_failed_migration_save_preserves_the_legacy_file(self):
        client = {"name": "default", "key": "legacy-key", "allowed_ips": []}
        gui = MigrationHarness()

        with (
            patch(
                "src.ui.ui_mixins.config_mixin.load_clients",
                return_value=((client,), True, True),
            ),
            patch("src.ui.ui_mixins.config_mixin.remove_legacy_key_file") as remove,
        ):
            loaded = gui.load_api_clients()

        self.assertEqual(loaded, (client,))
        remove.assert_not_called()

    def test_failed_client_edit_does_not_replace_the_live_snapshot(self):
        gui = ClientEditHarness()

        with patch("src.ui.ui_mixins.api_mixin.QMessageBox.critical") as critical:
            applied = gui.set_api_clients(({"name": "new", "key": "new-key"},))

        self.assertFalse(applied)
        self.assertEqual(gui._api_clients[0]["key"], "old-key")
        critical.assert_called_once()

    def test_last_request_queues_a_lightweight_gui_refresh(self):
        gui = LastRequestHarness()

        gui.note_api_request("press", "192.0.2.10")

        self.assertEqual(len(gui.gui_bridge.posted), 1)
        gui.gui_bridge.posted[0]()
        self.assertEqual(gui._api_last_request["client"], "press")
        self.assertIn("press", gui.lbl_api_status.text)

    def test_last_request_refresh_does_not_touch_the_frozen_widgets(self):
        """Regression: note_api_request() used to post the full
        _on_api_state_changed(), which re-touches setEnabled() on 4 widgets (plus
        rebuilding the whole status label) on every single API request even though
        none of that state can have changed just because a request came in.
        """
        gui = LastRequestHarness()

        gui.note_api_request("press", "192.0.2.10")
        gui.gui_bridge.posted[0]()

        self.assertEqual(gui.state_refreshes, 0)
        for widget in (
            gui.spin_api_port, gui.combo_api_bind,
            gui.chk_api_expose_docs, gui.combo_api_mode,
        ):
            self.assertEqual(widget.enable_calls, [])

    def test_last_request_refresh_is_skipped_while_stopping(self):
        gui = LastRequestHarness(stopping=True)

        gui.note_api_request("press", "192.0.2.10")
        gui.gui_bridge.posted[0]()

        self.assertIsNone(gui.lbl_api_status.text)

    def test_last_request_refresh_is_skipped_when_not_running(self):
        gui = LastRequestHarness(running=False)

        gui.note_api_request("press", "192.0.2.10")
        gui.gui_bridge.posted[0]()

        self.assertIsNone(gui.lbl_api_status.text)


class LocalIpAddressCachingTests(unittest.TestCase):
    def setUp(self):
        local_ip_address.cache_clear()
        self.addCleanup(local_ip_address.cache_clear)

    def test_the_resolving_syscalls_run_only_once(self):
        """Regression: this used to run socket.gethostbyname(socket.gethostname()) -
        a real, occasionally slow DNS/NSS syscall - on the GUI thread on every single
        API request. The value is documented as best-effort/display-only and a
        machine's LAN address essentially never changes mid-session, so it is safe
        (and now required) to resolve it only once per process.
        """
        with (
            patch("socket.gethostname", return_value="rig-host") as hostname,
            patch("socket.gethostbyname", return_value="192.0.2.5") as byname,
        ):
            first = local_ip_address()
            second = local_ip_address()

        self.assertEqual(first, "192.0.2.5")
        self.assertEqual(second, "192.0.2.5")
        hostname.assert_called_once()
        byname.assert_called_once()


if __name__ == "__main__":
    unittest.main()
