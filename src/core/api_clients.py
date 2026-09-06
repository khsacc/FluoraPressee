"""Named API clients: storage, key matching and IP authorisation.

The single pre-shared key this replaces could only prove "the caller knows the
secret" - never "the caller is one of the N applications I authorised". With the
server listening all day (Standby mode) that gap stops being theoretical: the usual
lab accident is the same key pasted into several machines' configuration, so rig A's
script ends up driving rig B, or a client someone forgot to stop keeps firing. Named
clients make each one revocable on its own and let an operator see who called last.

Qt-independent on purpose, so the authorisation rules are testable without a GUI and
reusable from the API worker threads.

Threading: the client list is handed out as an immutable tuple and replaced wholesale
on edit, so a worker thread reading it never sees a half-written list and needs no
lock (rebinding a name is atomic). Only the last-seen bookkeeping is mutable, and it
carries its own lock.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

CLIENTS_FILE_VERSION = 2

# Where the single-key file used to live: the repository working directory.
LEGACY_KEY_FILENAME = "fluora_pressee_api_key.json"


def default_api_clients_path() -> Path:
    """Per-user application-data location, matching default_configuration_root()."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "FluoraPressee" / "api_clients.json"


def new_client(name: str, allowed_ips: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "key": secrets.token_urlsafe(24),
        "allowed_ips": list(allowed_ips or []),
        "created": datetime.now().isoformat(timespec="seconds"),
    }


def _normalize_client(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    if not key:
        return None
    allowed = entry.get("allowed_ips") or []
    if not isinstance(allowed, list):
        allowed = []
    return {
        "name": str(entry.get("name") or "unnamed"),
        "key": str(key),
        "allowed_ips": [str(value) for value in allowed],
        "created": entry.get("created"),
    }


def load_clients(
    path: Path | None = None, legacy_path: Path | None = None
) -> tuple[tuple[dict[str, Any], ...], bool, bool]:
    """Read the client list, migrating older shapes and locations on the way.

    Returns (clients, needs_save, migrated_legacy_file).

    `needs_save` is True when the caller should persist the result, either because it
    was migrated or because a first client was generated. Distinguishing "no file at
    all" from "a file holding an empty list" matters: the first is a fresh install and
    gets a key generated, the second is an operator who deliberately revoked
    everything and must stay revoked.

    `migrated_legacy_file` is True only when the old single-key file was read
    successfully and carried over, so a corrupt one is never deleted - it may still be
    the only copy of a key the operator can repair by hand.
    """
    path = Path(path) if path is not None else default_api_clients_path()
    legacy_path = (
        Path(legacy_path) if legacy_path is not None else Path(LEGACY_KEY_FILENAME)
    )

    payload = None
    migrated = False
    migrated_legacy = False
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            # Refusing every request is worse than carrying on with what is
            # effectively a fresh install, but never silently: say so loudly.
            print(f"Failed to read API client file {path}: {exc}")
            payload = None
    elif legacy_path.exists():
        try:
            with open(legacy_path, "r", encoding="utf-8") as handle:
                legacy = json.load(handle)
            legacy_key = legacy.get("api_key")
            if legacy_key:
                # An existing paired client must keep working untouched, so the old
                # key is carried over verbatim with no IP restriction.
                payload = {
                    "version": CLIENTS_FILE_VERSION,
                    "clients": [
                        {
                            "name": "default",
                            "key": legacy_key,
                            "allowed_ips": [],
                            "created": datetime.now().isoformat(timespec="seconds"),
                        }
                    ],
                }
                migrated = True
                migrated_legacy = True
        except Exception as exc:
            print(f"Failed to migrate the legacy API key file {legacy_path}: {exc}")

    if payload is None:
        return (new_client("default"),), True, False

    raw_clients = payload.get("clients")
    if raw_clients is None and payload.get("api_key"):
        # A v1 payload that somehow ended up at the new path.
        raw_clients = [{"name": "default", "key": payload["api_key"], "allowed_ips": []}]
        migrated = True
    if raw_clients is None:
        return (new_client("default"),), True, False

    clients = tuple(
        client for client in (_normalize_client(entry) for entry in raw_clients)
        if client is not None
    )
    return clients, migrated, migrated_legacy


def save_clients(clients, path: Path | None = None) -> None:
    """Write the client list atomically, owner-readable only where that means anything.

    Written to a temporary file and renamed, so an interrupted write can never leave a
    half-written file where the keys used to be. chmod is close to meaningless on
    Windows; there the protection is the location itself, under the user's profile.
    """
    path = Path(path) if path is not None else default_api_clients_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CLIENTS_FILE_VERSION,
        "clients": [dict(client) for client in clients],
    }
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    os.replace(temp_path, path)


def remove_legacy_key_file(legacy_path: Path | None = None) -> None:
    legacy_path = (
        Path(legacy_path) if legacy_path is not None else Path(LEGACY_KEY_FILENAME)
    )
    try:
        legacy_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"Could not remove the legacy API key file {legacy_path}: {exc}")


def find_client(clients, presented_key: str | None):
    """Return the client whose key matches, comparing in constant time."""
    if not presented_key:
        return None
    for client in clients:
        if secrets.compare_digest(client["key"], presented_key):
            return client
    return None


def _normalized_host(host: str | None):
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    # A dual-stack listener reports IPv4 peers as ::ffff:192.168.1.42, which would
    # never match a plain "192.168.1.42" entry. (ipv4_mapped exists on IPv6Address
    # only, hence the getattr.)
    return getattr(address, "ipv4_mapped", None) or address


def ip_allowed(client, host: str | None) -> bool:
    """Whether `host` is inside the client's allow-list.

    An empty list means "no restriction" - both the v1 migration path and the default
    for a newly added client, so enabling allow-listing stays an opt-in.
    """
    allowed = client.get("allowed_ips") or []
    if not allowed:
        return True
    address = _normalized_host(host)
    if address is None:
        return False
    for entry in allowed:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


class LastSeenRegistry:
    """Per-client "last request" bookkeeping, written from API worker threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, str]] = {}

    def record(self, client_name: str, host: str | None) -> None:
        with self._lock:
            self._entries[client_name] = {
                "ip": host or "unknown",
                "time": datetime.now().isoformat(timespec="seconds"),
            }

    def get(self, client_name: str):
        with self._lock:
            entry = self._entries.get(client_name)
            return dict(entry) if entry else None
