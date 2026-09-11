"""Safe replace of Steam cloud-storage collection JSON (Phase 12).

This is the only module allowed to replace a live
``cloud-storage-namespace-*.json`` or ``cloud-storage-namespaces.json``.
The sequence matches ``steam/commit.py``: same-directory temp, fsync,
parse-back, timestamped backup, Steam recheck, ``os.replace``, parent
directory fsync. ``localconfig.vdf`` is not written.

Callers inject :class:`~steam_desktop_importer.steam.commit.CommitHooks`
so tests can force failures without touching real userdata.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .collections import CollectionDocument
from .commit import (
    MAX_BACKUPS,
    CommitError,
    CommitHooks,
    CommitValidationError,
    ImporterLock,
    SteamIsRunningError,
    VdfChangedError,
    lock_path_for,
    temp_path_for,
)
from .running import SteamRunningStatus, steam_allows_write

__all__ = [
    "CloudStorageChangedError",
    "commit_collections",
]

_BACKUP_PREFIX = ".bak-"


class CloudStorageChangedError(VdfChangedError):
    """The live cloud-storage file changed after the in-memory document was built."""


@dataclass(frozen=True)
class CollectionCommitResult:
    """Outcome of a successful collections replace."""

    namespace_path: Path
    index_path: Path
    namespace_backup: Path | None
    index_backup: Path | None
    bytes_written: int


def _require_steam_closed(status: SteamRunningStatus) -> None:
    if steam_allows_write(status):
        return
    if status.running:
        evidence = status.evidence[0] if status.evidence else "detected"
        raise SteamIsRunningError(f"Steam is running ({evidence})")
    raise SteamIsRunningError(
        f"Steam status is uncertain ({status.inspection_failures} "
        "processes unreadable); refusing to write"
    )


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        offset += os.write(fd, view[offset:])


def _write_temp(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink_if_exists(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def _backup_destination(path: Path, when: datetime) -> Path:
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}{_BACKUP_PREFIX}{stamp}")
    if not candidate.exists():
        return candidate
    for suffix in range(2, 1000):
        collided = path.with_name(f"{path.name}{_BACKUP_PREFIX}{stamp}-{suffix}")
        if not collided.exists():
            return collided
    raise CommitError(f"could not allocate a backup name under {path.parent}")


def _existing_backups(path: Path) -> list[Path]:
    prefix = f"{path.name}{_BACKUP_PREFIX}"
    found = [
        candidate
        for candidate in path.parent.iterdir()
        if candidate.is_file() and candidate.name.startswith(prefix)
    ]
    found.sort(key=lambda item: (item.stat().st_mtime_ns, item.name))
    return found


def _prune_backups(path: Path, max_backups: int) -> None:
    extras = _existing_backups(path)[:-max_backups] if max_backups > 0 else []
    for candidate in extras:
        _unlink_if_exists(candidate)


def _live_bytes(path: Path) -> bytes:
    if not path.is_file():
        return b""
    return path.read_bytes()


def _validate_json_temp(temp_path: Path, expected: bytes, label: str) -> None:
    try:
        payload = temp_path.read_bytes()
    except OSError as error:
        raise CommitValidationError(f"could not reopen temporary {label}: {error}") from error
    if payload != expected:
        raise CommitValidationError(f"temporary {label} bytes do not match the document")
    try:
        parsed = json.loads(payload.decode("utf-8"))
        again = json.loads(expected.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CommitValidationError(f"temporary {label} failed parse-back: {error}") from error
    if parsed != again:
        raise CommitValidationError(f"parsed temporary {label} does not match the document")


def commit_collections(
    document: CollectionDocument,
    *,
    original_namespace_bytes: bytes | None = None,
    original_index_bytes: bytes | None = None,
    steam_status: SteamRunningStatus | None = None,
    hooks: CommitHooks | None = None,
    max_backups: int = MAX_BACKUPS,
    lock: ImporterLock | None = None,
) -> CollectionCommitResult:
    """Replace the collections namespace and namespaces index.

    ``original_*_bytes`` are the live files as read when the document was
    built (empty if they did not exist). After the lock is taken the live
    bytes must still match.

    The live files are never truncated in place. On any failure before
    ``os.replace``, they are left untouched.
    """
    if document.namespace_path is None or document.index_path is None:
        raise CommitError("collection document has no destination paths")

    active_hooks = hooks if hooks is not None else CommitHooks()
    initial = steam_status if steam_status is not None else active_hooks.detect_steam()
    _require_steam_closed(initial)

    namespace_path = document.namespace_path
    index_path = document.index_path
    namespace_payload = document.dumps()
    index_payload = document.index_dumps()
    owned_lock = lock is None
    held = lock if lock is not None else ImporterLock(lock_path_for(namespace_path))
    namespace_temp = temp_path_for(namespace_path)
    index_temp = temp_path_for(index_path)
    replaced = False
    try:
        if owned_lock:
            held.acquire()
        namespace_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            original_namespace_bytes is not None
            and _live_bytes(namespace_path) != original_namespace_bytes
        ):
            raise CloudStorageChangedError(
                f"{namespace_path} changed after the in-memory document was built"
            )
        if original_index_bytes is not None and _live_bytes(index_path) != original_index_bytes:
            raise CloudStorageChangedError(
                f"{index_path} changed after the in-memory document was built"
            )

        _write_temp(namespace_temp, namespace_payload)
        _write_temp(index_temp, index_payload)
        if active_hooks.after_serialize is not None:
            active_hooks.after_serialize(namespace_temp)
        _validate_json_temp(namespace_temp, namespace_payload, "namespace JSON")
        _validate_json_temp(index_temp, index_payload, "namespaces index")
        if active_hooks.after_validate is not None:
            active_hooks.after_validate(namespace_temp)

        when = datetime.now(timezone.utc)
        namespace_backup: Path | None = None
        index_backup: Path | None = None
        if namespace_path.is_file():
            namespace_backup = _backup_destination(namespace_path, when)
            active_hooks.copy_file(namespace_path, namespace_backup)
            _prune_backups(namespace_path, max_backups)
        if index_path.is_file():
            index_backup = _backup_destination(index_path, when)
            active_hooks.copy_file(index_path, index_backup)
            _prune_backups(index_path, max_backups)
        if active_hooks.after_backup is not None:
            active_hooks.after_backup(namespace_backup)

        _require_steam_closed(active_hooks.detect_steam())
        if active_hooks.before_replace is not None:
            active_hooks.before_replace(namespace_temp, namespace_path)

        active_hooks.replace(str(namespace_temp), str(namespace_path))
        active_hooks.replace(str(index_temp), str(index_path))
        replaced = True
        _fsync_directory(namespace_path.parent)
        return CollectionCommitResult(
            namespace_path=namespace_path,
            index_path=index_path,
            namespace_backup=namespace_backup,
            index_backup=index_backup,
            bytes_written=len(namespace_payload) + len(index_payload),
        )
    finally:
        if not replaced:
            _unlink_if_exists(namespace_temp)
            _unlink_if_exists(index_temp)
        if owned_lock:
            held.release()
