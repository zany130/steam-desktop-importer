"""Read-only fingerprints of ``shortcuts.vdf`` and ``config/grid/``.

Phase 10 needs to prove that unrelated existing shortcuts (and their artwork)
survive an import. These fingerprints hash entry payloads and grid files
without recording names, executables, or other user content.

This module never opens a Steam path for writing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vdf

from .appid import int32_to_uint32
from .artwork import grid_dir
from .shortcut_identities import shortcuts_vdf_path
from .shortcuts import ShortcutDocument, get_ci

__all__ = [
    "GRID_FILENAME",
    "GridFileFingerprint",
    "LibrarySnapshot",
    "ShortcutFingerprint",
    "SurvivalReport",
    "compare_snapshots",
    "fingerprint_bytes",
    "fingerprint_mapping",
    "grid_appid_from_name",
    "snapshot_from_json",
    "snapshot_grid",
    "snapshot_library",
    "snapshot_vdf",
    "unrelated_survival",
]

GRID_FILENAME = re.compile(
    r"^(?P<appid>\d+)(?P<slot>p|_hero|_logo|_icon)?\.(?P<ext>png|jpe?g|webp|gif)$",
    re.IGNORECASE,
)


def fingerprint_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fingerprint_mapping(value: Mapping[str, Any]) -> str:
    """Stable hash of one VDF map. Does not include the shortcut index."""
    return fingerprint_bytes(vdf.binary_dumps({"fingerprint": dict(value)}))


def grid_appid_from_name(name: str) -> int | None:
    """Unsigned AppID encoded in a Steam grid filename, if the name matches."""
    matched = GRID_FILENAME.match(name)
    if matched is None:
        return None
    return int(matched.group("appid")) & 0xFFFFFFFF


@dataclass(frozen=True)
class ShortcutFingerprint:
    appid_unsigned: int
    index: str
    digest: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class GridFileFingerprint:
    name: str
    digest: str
    size: int
    appid_unsigned: int | None


@dataclass(frozen=True)
class LibrarySnapshot:
    """Structural snapshot of one account's shortcuts and grid folder."""

    vdf_digest: str
    vdf_size: int
    shortcut_count: int
    shortcuts: tuple[ShortcutFingerprint, ...]
    duplicate_appids: tuple[int, ...]
    grid: tuple[GridFileFingerprint, ...]

    def shortcut_by_appid(self) -> dict[int, ShortcutFingerprint]:
        return {item.appid_unsigned: item for item in self.shortcuts}

    def grid_by_name(self) -> dict[str, GridFileFingerprint]:
        return {item.name: item for item in self.grid}

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "vdf_digest": self.vdf_digest,
            "vdf_size": self.vdf_size,
            "shortcut_count": self.shortcut_count,
            "duplicate_appids": list(self.duplicate_appids),
            "shortcuts": [
                {
                    "appid_unsigned": item.appid_unsigned,
                    "index": item.index,
                    "digest": item.digest,
                    "keys": list(item.keys),
                }
                for item in self.shortcuts
            ],
            "grid": [
                {
                    "name": item.name,
                    "digest": item.digest,
                    "size": item.size,
                    "appid_unsigned": item.appid_unsigned,
                }
                for item in self.grid
            ],
        }


def snapshot_from_json(payload: Mapping[str, Any] | str | bytes) -> LibrarySnapshot:
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
    shortcuts = tuple(
        ShortcutFingerprint(
            appid_unsigned=int(item["appid_unsigned"]),
            index=str(item["index"]),
            digest=str(item["digest"]),
            keys=tuple(item.get("keys") or ()),
        )
        for item in data.get("shortcuts") or ()
    )
    grid = tuple(
        GridFileFingerprint(
            name=str(item["name"]),
            digest=str(item["digest"]),
            size=int(item["size"]),
            appid_unsigned=(
                None if item.get("appid_unsigned") is None else int(item["appid_unsigned"])
            ),
        )
        for item in data.get("grid") or ()
    )
    return LibrarySnapshot(
        vdf_digest=str(data["vdf_digest"]),
        vdf_size=int(data["vdf_size"]),
        shortcut_count=int(data["shortcut_count"]),
        shortcuts=shortcuts,
        duplicate_appids=tuple(int(value) for value in data.get("duplicate_appids") or ()),
        grid=grid,
    )


def snapshot_vdf(path: Path, payload: bytes | None = None) -> tuple[
    str,
    int,
    tuple[ShortcutFingerprint, ...],
    tuple[int, ...],
]:
    """Fingerprint a shortcuts.vdf. ``payload`` avoids a second read when known."""
    raw = path.read_bytes() if payload is None else payload
    document = ShortcutDocument.loads(raw) if raw else ShortcutDocument.empty()
    seen: dict[int, ShortcutFingerprint] = {}
    duplicates: list[int] = []
    ordered: list[ShortcutFingerprint] = []
    shortcuts = document.raw().get("shortcuts") or {}
    if not isinstance(shortcuts, dict):
        shortcuts = {}
    for index, entry in shortcuts.items():
        if not isinstance(entry, dict):
            continue
        raw_appid = get_ci(entry, "appid")
        if not isinstance(raw_appid, int):
            continue
        appid = int32_to_uint32(raw_appid)
        item = ShortcutFingerprint(
            appid_unsigned=appid,
            index=str(index),
            digest=fingerprint_mapping(entry),
            keys=tuple(str(key) for key in entry),
        )
        ordered.append(item)
        if appid in seen:
            duplicates.append(appid)
        else:
            seen[appid] = item
    return fingerprint_bytes(raw), len(raw), tuple(ordered), tuple(duplicates)


def snapshot_grid(directory: Path) -> tuple[GridFileFingerprint, ...]:
    if not directory.is_dir():
        return ()
    items: list[GridFileFingerprint] = []
    for child in sorted(directory.iterdir(), key=lambda path: path.name):
        if not child.is_file():
            continue
        payload = child.read_bytes()
        items.append(
            GridFileFingerprint(
                name=child.name,
                digest=fingerprint_bytes(payload),
                size=len(payload),
                appid_unsigned=grid_appid_from_name(child.name),
            )
        )
    return tuple(items)


def snapshot_library(vdf_path: Path, grid_path: Path | None = None) -> LibrarySnapshot:
    """Read ``shortcuts.vdf`` and the account ``grid/`` directory."""
    payload = vdf_path.read_bytes() if vdf_path.is_file() else b""
    digest, size, shortcuts, duplicates = snapshot_vdf(vdf_path, payload)
    grid = snapshot_grid(grid_path) if grid_path is not None else ()
    return LibrarySnapshot(
        vdf_digest=digest,
        vdf_size=size,
        shortcut_count=len(shortcuts),
        shortcuts=shortcuts,
        duplicate_appids=duplicates,
        grid=grid,
    )


def snapshot_account(account) -> LibrarySnapshot:
    return snapshot_library(shortcuts_vdf_path(account), grid_dir(account))


@dataclass(frozen=True)
class SurvivalReport:
    """Whether unmanaged shortcuts and grid files survived a write."""

    added_appids: tuple[int, ...]
    removed_appids: tuple[int, ...]
    changed_appids: tuple[int, ...]
    index_moved_appids: tuple[int, ...]
    unchanged_appids: tuple[int, ...]
    unmanaged_added_appids: tuple[int, ...]
    unmanaged_removed_appids: tuple[int, ...]
    unmanaged_changed_appids: tuple[int, ...]
    grid_added: tuple[str, ...]
    grid_removed: tuple[str, ...]
    grid_changed: tuple[str, ...]
    unmanaged_grid_added: tuple[str, ...]
    unmanaged_grid_removed: tuple[str, ...]
    unmanaged_grid_changed: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.unmanaged_added_appids
            or self.unmanaged_removed_appids
            or self.unmanaged_changed_appids
            or self.unmanaged_grid_added
            or self.unmanaged_grid_removed
            or self.unmanaged_grid_changed
        )

    def summary(self) -> str:
        if self.ok:
            return (
                f"ok: {len(self.unchanged_appids)} shortcuts byte-identical; "
                f"{len(self.added_appids)} added; "
                f"{len(self.grid_added)} grid files added"
            )
        return (
            "CHANGED: "
            f"unmanaged shortcuts added={len(self.unmanaged_added_appids)} "
            f"removed={len(self.unmanaged_removed_appids)} "
            f"changed={len(self.unmanaged_changed_appids)}; "
            f"unrelated grid added={len(self.unmanaged_grid_added)} "
            f"removed={len(self.unmanaged_grid_removed)} "
            f"changed={len(self.unmanaged_grid_changed)}"
        )


def _is_managed_grid(item: GridFileFingerprint, managed_appids: set[int]) -> bool:
    return item.appid_unsigned is not None and item.appid_unsigned in managed_appids


def compare_snapshots(
    before: LibrarySnapshot,
    after: LibrarySnapshot,
    *,
    managed_appids: Iterable[int] = (),
) -> SurvivalReport:
    managed = {int(appid) & 0xFFFFFFFF for appid in managed_appids}
    before_map = before.shortcut_by_appid()
    after_map = after.shortcut_by_appid()
    before_ids = set(before_map)
    after_ids = set(after_map)
    added = tuple(sorted(after_ids - before_ids))
    removed = tuple(sorted(before_ids - after_ids))
    common = before_ids & after_ids
    changed: list[int] = []
    moved: list[int] = []
    unchanged: list[int] = []
    for appid in sorted(common):
        previous = before_map[appid]
        current = after_map[appid]
        digest_changed = previous.digest != current.digest
        index_changed = previous.index != current.index
        if digest_changed:
            changed.append(appid)
        if index_changed:
            moved.append(appid)
        if not digest_changed and not index_changed:
            unchanged.append(appid)
        elif not digest_changed and index_changed:
            changed.append(appid)
    # Index-only moves are still a change to the unrelated document.
    changed_set = set(changed)
    changed = tuple(sorted(changed_set))
    moved = tuple(moved)

    before_grid = before.grid_by_name()
    after_grid = after.grid_by_name()
    grid_added = tuple(sorted(set(after_grid) - set(before_grid)))
    grid_removed = tuple(sorted(set(before_grid) - set(after_grid)))
    grid_changed = tuple(
        sorted(
            name
            for name in set(before_grid) & set(after_grid)
            if before_grid[name].digest != after_grid[name].digest
        )
    )

    unmanaged_added = tuple(appid for appid in added if appid not in managed)
    unmanaged_removed = tuple(appid for appid in removed if appid not in managed)
    unmanaged_changed = tuple(appid for appid in changed if appid not in managed)

    def unmanaged_name(name: str, source: dict[str, GridFileFingerprint]) -> bool:
        return not _is_managed_grid(source[name], managed)

    unmanaged_grid_added = tuple(
        name for name in grid_added if unmanaged_name(name, after_grid)
    )
    unmanaged_grid_removed = tuple(
        name for name in grid_removed if unmanaged_name(name, before_grid)
    )
    unmanaged_grid_changed = tuple(
        name for name in grid_changed if unmanaged_name(name, before_grid)
    )

    return SurvivalReport(
        added_appids=added,
        removed_appids=removed,
        changed_appids=changed,
        index_moved_appids=moved,
        unchanged_appids=tuple(unchanged),
        unmanaged_added_appids=unmanaged_added,
        unmanaged_removed_appids=unmanaged_removed,
        unmanaged_changed_appids=unmanaged_changed,
        grid_added=grid_added,
        grid_removed=grid_removed,
        grid_changed=grid_changed,
        unmanaged_grid_added=unmanaged_grid_added,
        unmanaged_grid_removed=unmanaged_grid_removed,
        unmanaged_grid_changed=unmanaged_grid_changed,
    )


def unrelated_survival(
    before: LibrarySnapshot,
    after: LibrarySnapshot,
    managed_appids: Iterable[int] = (),
) -> SurvivalReport:
    return compare_snapshots(before, after, managed_appids=managed_appids)
