"""Named API clients: storage, migration and authorisation.

Deliberately Qt-free - these rules decide who may drive the instrument, so they
should be verifiable without a GUI (work_API_standby.md Step 6).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from src.core.api_clients import (
    CLIENTS_FILE_VERSION,
    LastSeenRegistry,
    find_client,
    ip_allowed,
    load_clients,
    new_client,
    save_clients,
)


class LoadAndMigrateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "api_clients.json"
        self.legacy = Path(self.tempdir.name) / "fluora_pressee_api_key.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_first_run_generates_one_client(self):
        clients, needs_save, _migrated = load_clients(self.path, self.legacy)

        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["name"], "default")
        self.assertTrue(clients[0]["key"])
        self.assertEqual(clients[0]["allowed_ips"], [])
        self.assertTrue(needs_save)

    def test_an_explicitly_emptied_list_is_respected(self):
        # "The operator revoked everything" must not be undone by regenerating a key.
        self.path.write_text(
            json.dumps({"version": 2, "clients": []}), encoding="utf-8"
        )

        clients, needs_save, _migrated = load_clients(self.path, self.legacy)

        self.assertEqual(clients, ())
        self.assertFalse(needs_save)

    def test_legacy_single_key_file_is_migrated_verbatim(self):
        self.legacy.write_text(
            json.dumps({"api_key": "legacy-secret"}), encoding="utf-8"
        )

        clients, needs_save, migrated_legacy = load_clients(self.path, self.legacy)

        # An already-paired client must keep working with no reconfiguration at all.
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["key"], "legacy-secret")
        self.assertEqual(clients[0]["name"], "default")
        self.assertEqual(clients[0]["allowed_ips"], [])
        self.assertTrue(needs_save)
        self.assertTrue(migrated_legacy)

    def test_an_unreadable_legacy_file_is_not_reported_as_migrated(self):
        # It may be the only copy of a key the operator can still repair by hand, so
        # the caller must not delete it.
        self.legacy.write_text("{ not json", encoding="utf-8")

        clients, needs_save, migrated_legacy = load_clients(self.path, self.legacy)

        self.assertEqual(len(clients), 1)
        self.assertTrue(needs_save)
        self.assertFalse(migrated_legacy)

    def test_v1_payload_at_the_new_path_is_migrated(self):
        self.path.write_text(json.dumps({"api_key": "older"}), encoding="utf-8")

        clients, needs_save, _migrated = load_clients(self.path, self.legacy)

        self.assertEqual(clients[0]["key"], "older")
        self.assertTrue(needs_save)

    def test_existing_v2_file_is_read_as_is(self):
        payload = {
            "version": 2,
            "clients": [
                {"name": "press", "key": "k1", "allowed_ips": ["10.0.0.1"]},
                {"name": "laptop", "key": "k2", "allowed_ips": []},
            ],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        clients, needs_save, _migrated = load_clients(self.path, self.legacy)

        self.assertEqual([c["name"] for c in clients], ["press", "laptop"])
        self.assertEqual(clients[0]["allowed_ips"], ["10.0.0.1"])
        self.assertFalse(needs_save)

    def test_entries_without_a_key_are_dropped(self):
        payload = {"version": 2, "clients": [{"name": "broken"}, {"key": "ok"}]}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        clients, _needs_save, _migrated = load_clients(self.path, self.legacy)

        self.assertEqual([c["key"] for c in clients], ["ok"])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "nested" / "api_clients.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_save_is_atomic_and_leaves_no_temporary_file(self):
        save_clients([new_client("one")], self.path)

        self.assertTrue(self.path.exists())
        self.assertEqual(
            list(self.path.parent.glob("*.tmp")), [],
            "a leftover temp file means the rename did not happen",
        )
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], CLIENTS_FILE_VERSION)

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        save_clients([new_client("one")], self.path)
        original = self.path.read_text(encoding="utf-8")

        class Unserialisable:
            pass

        with self.assertRaises(TypeError):
            save_clients([{"name": "x", "key": Unserialisable()}], self.path)

        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    @unittest.skipIf(os.name == "nt", "POSIX file modes only")
    def test_saved_file_is_owner_only(self):
        save_clients([new_client("one")], self.path)

        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_round_trip_through_save_and_load(self):
        clients = [new_client("press", ["192.168.1.0/24"])]
        save_clients(clients, self.path)

        loaded, needs_save, _migrated = load_clients(self.path, self.path.with_name("legacy.json"))

        self.assertFalse(needs_save)
        self.assertEqual(loaded[0]["key"], clients[0]["key"])
        self.assertEqual(loaded[0]["allowed_ips"], ["192.168.1.0/24"])


class KeyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.clients = (
            {"name": "press", "key": "key-one", "allowed_ips": []},
            {"name": "laptop", "key": "key-two", "allowed_ips": []},
        )

    def test_the_matching_client_is_returned(self):
        self.assertEqual(find_client(self.clients, "key-two")["name"], "laptop")

    def test_unknown_and_missing_keys_match_nothing(self):
        self.assertIsNone(find_client(self.clients, "nope"))
        self.assertIsNone(find_client(self.clients, None))
        self.assertIsNone(find_client(self.clients, ""))
        self.assertIsNone(find_client((), "key-one"))


class IpAuthorisationTests(unittest.TestCase):
    def test_an_empty_list_allows_any_address(self):
        client = {"name": "c", "key": "k", "allowed_ips": []}

        self.assertTrue(ip_allowed(client, "192.168.1.42"))
        self.assertTrue(ip_allowed(client, None))

    def test_a_single_address_matches_only_itself(self):
        client = {"name": "c", "key": "k", "allowed_ips": ["192.168.1.42"]}

        self.assertTrue(ip_allowed(client, "192.168.1.42"))
        self.assertFalse(ip_allowed(client, "192.168.1.43"))

    def test_cidr_ranges_are_supported(self):
        client = {"name": "c", "key": "k", "allowed_ips": ["192.168.2.0/24"]}

        self.assertTrue(ip_allowed(client, "192.168.2.7"))
        self.assertFalse(ip_allowed(client, "192.168.3.7"))

    def test_ipv4_mapped_addresses_are_normalised(self):
        # A dual-stack listener reports IPv4 peers in this form.
        client = {"name": "c", "key": "k", "allowed_ips": ["192.168.1.42"]}

        self.assertTrue(ip_allowed(client, "::ffff:192.168.1.42"))

    def test_an_unknown_address_is_rejected_when_restricted(self):
        client = {"name": "c", "key": "k", "allowed_ips": ["192.168.1.42"]}

        self.assertFalse(ip_allowed(client, None))
        self.assertFalse(ip_allowed(client, "not-an-address"))

    def test_malformed_entries_are_skipped_not_fatal(self):
        client = {"name": "c", "key": "k", "allowed_ips": ["nonsense", "10.0.0.5"]}

        self.assertTrue(ip_allowed(client, "10.0.0.5"))
        self.assertFalse(ip_allowed(client, "10.0.0.6"))


class LastSeenTests(unittest.TestCase):
    def test_records_and_returns_a_copy(self):
        registry = LastSeenRegistry()
        registry.record("press", "192.168.1.42")

        entry = registry.get("press")
        self.assertEqual(entry["ip"], "192.168.1.42")
        entry["ip"] = "changed"

        self.assertEqual(registry.get("press")["ip"], "192.168.1.42")
        self.assertIsNone(registry.get("unknown"))


if __name__ == "__main__":
    unittest.main()
