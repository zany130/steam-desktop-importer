"""Steam library collections in userdata cloud storage (Phase 12).

Modern Steam does **not** treat ``shortcuts.vdf`` ``tags`` as collections.
On current clients the live source is

``<userdata>/config/cloudstorage/cloud-storage-namespace-<N>.json``

with an index in ``cloud-storage-namespaces.json``. Each collection is a
``user-collections.<id>`` record whose ``value`` is a JSON string
``{id, name, added, removed}``. ``added`` holds unsigned 32-bit AppIDs
(high-bit non-Steam IDs included), never the derived 64-bit game ID.

This module parses and mutates that document in memory. The only writer is
:mod:`steam_desktop_importer.steam.collection_commit`. ``localconfig.vdf``'s
``user-collections`` string is treated as a Steam cache and is not written.

Observed, not a public Valve contract. See ``docs/PHASE12_COLLECTIONS.md``.
"""

from __future__ import annotations

import json
import os
import re
import time
from base64 import urlsafe_b64encode
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import SteamAccount

__all__ = [
    "COLLECTION_KEY_PREFIX",
    "DEFAULT_NAMESPACE_ID",
    "IMPORTER_COLLECTION_PREFIX",
    "TIMESTAMP_VERSION_FLOOR",
    "CollectionAssignment",
    "CollectionDocument",
    "CollectionError",
    "SteamCollection",
    "cloud_storage_dir",
    "collection_list_label",
    "is_assignable_collection_id",
    "is_hidden_collection_id",
    "is_tag_collection_id",
    "load_collections",
    "namespace_index_path",
    "namespace_path",
    "new_collection_id",
]

COLLECTION_KEY_PREFIX = "user-collections."
IMPORTER_COLLECTION_PREFIX = "sdi-"
DEFAULT_NAMESPACE_ID = 1
TIMESTAMP_VERSION_FLOOR = 1_000_000_000
_NAMESPACE_FILE = re.compile(r"^cloud-storage-namespace-(\d+)\.json$")
_TAG_ID_PREFIX = "from-tag-"
_SKIP_IDS = frozenset({"hidden"})


class CollectionError(ValueError):
    """The cloud-storage document could not be parsed or updated."""


@dataclass(frozen=True)
class SteamCollection:
    """One live ``user-collections.*`` record, decoded for the UI."""

    collection_id: str
    name: str
    added: tuple[int, ...]
    removed: tuple[int, ...]
    assignable: bool
    dynamic: bool = False


@dataclass(frozen=True)
class CollectionAssignment:
    """What to do to collections after a successful shortcut import.

    Empty means "do not open cloud storage". Existing ids are the suffix after
    ``user-collections.``. Create names become new ``sdi-`` collections unless
    a live assignable collection already has that name (case-insensitive),
    including ``from-tag-*`` store-tag shelves. A name that only exists on
    ``hidden`` or a Dynamic Collection (``filterSpec``) is an error, not a
    second collection with the same label.
    """

    existing_ids: tuple[str, ...] = ()
    create_names: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not self.existing_ids and not any(
            name.strip() for name in self.create_names
        )


def cloud_storage_dir(account: SteamAccount) -> Path:
    return account.userdata_dir / "config" / "cloudstorage"


def namespace_index_path(account: SteamAccount) -> Path:
    return cloud_storage_dir(account) / "cloud-storage-namespaces.json"


def namespace_path(account: SteamAccount, namespace_id: int) -> Path:
    return cloud_storage_dir(account) / f"cloud-storage-namespace-{namespace_id}.json"


def is_tag_collection_id(collection_id: str) -> bool:
    return collection_id.startswith(_TAG_ID_PREFIX)


def is_hidden_collection_id(collection_id: str) -> bool:
    return collection_id in _SKIP_IDS


def is_assignable_collection_id(collection_id: str) -> bool:
    """Id-only skip list. ``hidden`` hides games; Dynamic Collections are
    ``uc-*`` ids plus a ``filterSpec`` and are skipped at parse time.
    Store-tag ``from-tag-*`` shelves are assignable.
    """
    return collection_id not in _SKIP_IDS


def _payload_is_dynamic(payload: dict[str, Any]) -> bool:
    return payload.get("filterSpec") is not None


def collection_list_label(collection: SteamCollection) -> str:
    """Label for the Collections tab. Tag shelves are marked in the list."""
    if is_tag_collection_id(collection.collection_id):
        return f"{collection.name} (tag collection)"
    return collection.name


def _blocked_create_name_error(name: str, blocked: SteamCollection) -> str:
    if blocked.dynamic:
        kind = "dynamic collection"
    elif is_hidden_collection_id(blocked.collection_id):
        kind = "hidden-games collection"
    else:
        kind = "collection"
    return (
        f'{name}: Steam already has a {kind} named "{blocked.name}" '
        f"({blocked.collection_id}); pick a different name"
    )


def new_collection_id(existing: Iterable[str] = ()) -> str:
    """Random ``sdi-`` id that does not collide with ``existing`` ids."""
    taken = set(existing)
    for _ in range(64):
        raw = urlsafe_b64encode(os.urandom(9)).decode("ascii").rstrip("=")
        candidate = f"{IMPORTER_COLLECTION_PREFIX}{raw}"
        if candidate not in taken:
            return candidate
    raise CollectionError("could not allocate a unique collection id")


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.isdigit():
            return int(value) & 0xFFFFFFFF
        return None
    return int(value) & 0xFFFFFFFF


def _decode_value(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_int_list(raw: object) -> tuple[int, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    return tuple(number for number in (_as_int(value) for value in raw) if number is not None)


def _encode_value(payload: dict[str, Any]) -> str:
    ordered: dict[str, Any] = {}
    for key in ("id", "name", "added", "removed"):
        if key in payload:
            ordered[key] = payload[key]
    for key, value in payload.items():
        if key not in ordered:
            ordered[key] = value
    return json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)


def _collection_id_from_key(key: object) -> str | None:
    if not isinstance(key, str) or not key.startswith(COLLECTION_KEY_PREFIX):
        return None
    return key[len(COLLECTION_KEY_PREFIX) :]


def _dumps(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_json(path: Path) -> tuple[bytes, object]:
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise CollectionError(f"{path}: {error}") from error
    try:
        return payload, json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectionError(f"{path}: {error}") from error


def _index_version_map(index: list[Any]) -> dict[int, int]:
    versions: dict[int, int] = {}
    for item in index:
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            namespace_id = int(item[0])
            version = int(item[1])
        except (TypeError, ValueError):
            continue
        versions[namespace_id] = version
    return versions


def _steam_counter(values: Iterable[object]) -> int:
    """Max version that looks like Steam's namespace counter, not a unix timestamp.

    Third-party tools (SRM, some BoilR-era writes) store ``version == timestamp``.
    Steam's own writes on the capture host used the namespaces.json counter
    (thousands), not ``max(all versions)+1``.
    """
    highest = 0
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number < TIMESTAMP_VERSION_FLOOR:
            highest = max(highest, number)
    return highest


def discover_collections_namespace(cloud_dir: Path) -> int:
    """Namespace whose file actually holds ``user-collections.*`` records.

    Prefers namespace 1 when it has collections. Falls back to any other
    namespace file that does, then to namespace 1 so a first write has a home.
    """
    if not cloud_dir.is_dir():
        return DEFAULT_NAMESPACE_ID
    found: list[int] = []
    for path in sorted(cloud_dir.iterdir()):
        match = _NAMESPACE_FILE.match(path.name)
        if match is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        if any(
            isinstance(item, list)
            and item
            and isinstance(item[0], str)
            and item[0].startswith(COLLECTION_KEY_PREFIX)
            for item in data
        ):
            found.append(int(match.group(1)))
    if DEFAULT_NAMESPACE_ID in found:
        return DEFAULT_NAMESPACE_ID
    if found:
        return found[0]
    return DEFAULT_NAMESPACE_ID


@dataclass
class CollectionDocument:
    """In-memory cloud-storage namespace plus the namespaces index."""

    namespace_id: int
    entries: list[Any]
    index: list[Any]
    namespace_path: Path | None = None
    index_path: Path | None = None
    original_namespace_bytes: bytes = b""
    original_index_bytes: bytes = b""

    @classmethod
    def empty(cls, namespace_id: int = DEFAULT_NAMESPACE_ID) -> CollectionDocument:
        return cls(
            namespace_id=namespace_id,
            entries=[],
            index=[[namespace_id, "0"]],
        )

    @classmethod
    def load(cls, account: SteamAccount) -> CollectionDocument:
        cloud_dir = cloud_storage_dir(account)
        namespace_id = discover_collections_namespace(cloud_dir)
        ns_path = namespace_path(account, namespace_id)
        idx_path = namespace_index_path(account)
        if not ns_path.is_file():
            document = cls.empty(namespace_id)
            document.namespace_path = ns_path
            document.index_path = idx_path
            if idx_path.is_file():
                document.original_index_bytes, loaded = _load_json(idx_path)
                if not isinstance(loaded, list):
                    raise CollectionError(f"{idx_path}: expected a JSON array")
                document.index = loaded
            return document
        namespace_bytes, entries = _load_json(ns_path)
        if not isinstance(entries, list):
            raise CollectionError(f"{ns_path}: expected a JSON array")
        if idx_path.is_file():
            index_bytes, index = _load_json(idx_path)
            if not isinstance(index, list):
                raise CollectionError(f"{idx_path}: expected a JSON array")
        else:
            index_bytes = b""
            index = [[namespace_id, "0"]]
        return cls(
            namespace_id=namespace_id,
            entries=entries,
            index=index,
            namespace_path=ns_path,
            index_path=idx_path,
            original_namespace_bytes=namespace_bytes,
            original_index_bytes=index_bytes,
        )

    def dumps(self) -> bytes:
        return _dumps(self.entries)

    def index_dumps(self) -> bytes:
        return _dumps(self.index)

    def live_collections(self) -> list[SteamCollection]:
        found: list[SteamCollection] = []
        for item in self.entries:
            collection = self._collection_from_item(item)
            if collection is not None:
                found.append(collection)
        return found

    def assignable_collections(self) -> list[SteamCollection]:
        return [item for item in self.live_collections() if item.assignable]

    def collection_ids(self) -> set[str]:
        ids: set[str] = set()
        for item in self.entries:
            if not isinstance(item, list) or not item:
                continue
            collection_id = _collection_id_from_key(item[0])
            if collection_id:
                ids.add(collection_id)
        return ids

    def next_version(self) -> int:
        versions: list[object] = [_index_version_map(self.index).get(self.namespace_id, 0)]
        for item in self.entries:
            if not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], dict):
                continue
            if _collection_id_from_key(item[0]) is None:
                continue
            versions.append(item[1].get("version"))
        return _steam_counter(versions) + 1

    def bump_namespace_version(self, version: int) -> None:
        updated = False
        for item in self.index:
            if not isinstance(item, list) or len(item) < 2:
                continue
            try:
                if int(item[0]) == self.namespace_id:
                    item[1] = str(version)
                    updated = True
                    break
            except (TypeError, ValueError):
                continue
        if not updated:
            self.index.append([self.namespace_id, str(version)])

    def apply_assignment(
        self,
        assignment: CollectionAssignment,
        appids: Sequence[int],
        *,
        now: int | None = None,
    ) -> list[str]:
        """Add ``appids`` to existing and newly created collections.

        Returns human-readable errors for ids that could not be updated.
        Steam-closed atomic replace is the caller's job.
        """
        unsigned = [int(appid) & 0xFFFFFFFF for appid in appids]
        if not unsigned or assignment.is_empty():
            return []
        timestamp = int(time.time() if now is None else now)
        version = self.next_version()
        errors: list[str] = []
        existing_ids = set(self.collection_ids())
        changed = False
        live_by_id = {item.collection_id: item for item in self.live_collections()}

        for collection_id in assignment.existing_ids:
            target = live_by_id.get(collection_id)
            if target is None:
                errors.append(f"{collection_id}: collection not found")
                continue
            if not target.assignable:
                errors.append(f"{collection_id}: not an assignable collection")
                continue
            if not self._add_to_existing(collection_id, unsigned, version, timestamp):
                errors.append(f"{collection_id}: collection not found")
                continue
            changed = True

        for raw_name in assignment.create_names:
            name = raw_name.strip()
            if not name:
                continue
            match = self._assignable_by_name(name)
            if match is not None:
                self._add_to_existing(match, unsigned, version, timestamp)
                changed = True
                continue
            blocked = self._unassignable_by_name(name)
            if blocked is not None:
                errors.append(_blocked_create_name_error(name, blocked))
                continue
            new_id = new_collection_id(existing_ids)
            existing_ids.add(new_id)
            self._create(new_id, name, unsigned, version, timestamp)
            changed = True

        if changed:
            self.bump_namespace_version(version)
        return errors

    def _assignable_by_name(self, name: str) -> str | None:
        needle = name.casefold()
        matches = [
            collection
            for collection in self.assignable_collections()
            if collection.name.casefold() == needle
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda item: (is_tag_collection_id(item.collection_id), item.collection_id)
        )
        return matches[0].collection_id

    def _unassignable_by_name(self, name: str) -> SteamCollection | None:
        needle = name.casefold()
        for collection in self.live_collections():
            if not collection.assignable and collection.name.casefold() == needle:
                return collection
        return None

    def _collection_from_item(self, item: object) -> SteamCollection | None:
        if not isinstance(item, list) or len(item) < 2:
            return None
        collection_id = _collection_id_from_key(item[0])
        record = item[1]
        if collection_id is None or not isinstance(record, dict):
            return None
        if record.get("is_deleted"):
            return None
        payload = _decode_value(record.get("value"))
        if payload is None:
            return None
        added = _as_int_list(payload.get("added"))
        removed = _as_int_list(payload.get("removed"))
        if added is None or removed is None:
            return None
        name = payload.get("name")
        dynamic = _payload_is_dynamic(payload)
        return SteamCollection(
            collection_id=collection_id,
            name=name if isinstance(name, str) else collection_id,
            added=added,
            removed=removed,
            assignable=is_assignable_collection_id(collection_id) and not dynamic,
            dynamic=dynamic,
        )

    def _find_item(self, collection_id: str) -> list[Any] | None:
        key = f"{COLLECTION_KEY_PREFIX}{collection_id}"
        for item in self.entries:
            if isinstance(item, list) and item and item[0] == key:
                return item
        return None

    def _add_to_existing(
        self,
        collection_id: str,
        appids: Sequence[int],
        version: int,
        timestamp: int,
    ) -> bool:
        item = self._find_item(collection_id)
        if item is None or len(item) < 2 or not isinstance(item[1], dict):
            return False
        record = item[1]
        if record.get("is_deleted"):
            return False
        payload = _decode_value(record.get("value"))
        if payload is None:
            return False
        added_values = _as_int_list(payload.get("added"))
        removed_values = _as_int_list(payload.get("removed"))
        if added_values is None or removed_values is None:
            return False
        added = list(added_values)
        removed = list(removed_values)
        for appid in appids:
            if appid not in added:
                added.append(appid)
            if appid in removed:
                removed = [value for value in removed if value != appid]
        payload["id"] = payload.get("id") or collection_id
        payload["added"] = added
        payload["removed"] = removed
        record["timestamp"] = timestamp
        record["value"] = _encode_value(payload)
        record["version"] = str(version)
        record.setdefault("conflictResolutionMethod", "custom")
        record.setdefault("strMethodId", "union-collections")
        return True

    def _create(
        self,
        collection_id: str,
        name: str,
        appids: Sequence[int],
        version: int,
        timestamp: int,
    ) -> None:
        key = f"{COLLECTION_KEY_PREFIX}{collection_id}"
        payload = {
            "id": collection_id,
            "name": name,
            "added": list(appids),
            "removed": [],
        }
        record = {
            "key": key,
            "timestamp": timestamp,
            "value": _encode_value(payload),
            "version": str(version),
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
        }
        self.entries.append([key, record])


def load_collections(account: SteamAccount) -> CollectionDocument:
    """Load the collections namespace for ``account``. Missing files are empty."""
    return CollectionDocument.load(account)
