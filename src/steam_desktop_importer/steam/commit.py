"""Safe ``shortcuts.vdf`` commit (IMPLEMENTATION.md §18, Phase 7).

This is the only module allowed to replace a live Steam ``shortcuts.vdf``.
The sequence follows §18.3: same-directory temp, flush, fsync, parse-back
validation, timestamped backup, Steam recheck, ``os.replace``, parent-directory
fsync. An advisory per-file lock serialises importer instances; it does not
lock Steam itself.

Callers inject ``CommitHooks`` in tests so failure points can be forced
without touching a real Steam userdata directory.
"""

from __future__ import annotations

import fcntl
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .running import SteamRunningStatus, detect_steam_running, steam_allows_write
from .shortcuts import ShortcutDocument

__all__ = [
    "MAX_BACKUPS",
    "CommitError",
    "CommitHooks",
    "CommitResult",
    "CommitValidationError",
    "ImporterLock",
    "ImporterLockedError",
    "SteamIsRunningError",
    "VdfChangedError",
    "commit_shortcuts",
    "lock_path_for",
    "steam_allows_write",
    "temp_path_for",
]

MAX_BACKUPS = 10
_BACKUP_PREFIX = ".bak-"


class CommitError(Exception):
    """A ``shortcuts.vdf`` commit did not complete."""


class SteamIsRunningError(CommitError):
    """Steam appears to be running, or the probe was not certain enough."""


class CommitValidationError(CommitError):
    """The temporary VDF could not be parsed back as the expected document."""


class ImporterLockedError(CommitError):
    """Another importer instance already holds the per-account lock."""


class VdfChangedError(CommitError):
    """The live file changed after the in-memory document was built."""


def _replace(source: str, destination: str) -> None:
    os.replace(source, destination)


def _copy_file(source: Path, destination: Path) -> object:
    return shutil.copy2(source, destination)


@dataclass
class CommitHooks:
    """Injectable side effects for the §18.3 sequence.

    Production uses the defaults. Tests replace individual callables to
    simulate crashes, replace failures, and Steam starting mid-transaction.
    """

    detect_steam: Callable[[], SteamRunningStatus] = detect_steam_running
    replace: Callable[[str, str], None] = _replace
    copy_file: Callable[[Path, Path], object] = _copy_file
    after_serialize: Callable[[Path], None] | None = None
    after_validate: Callable[[Path], None] | None = None
    after_backup: Callable[[Path | None], None] | None = None
    before_replace: Callable[[Path, Path], None] | None = None


@dataclass(frozen=True)
class CommitResult:
    """Outcome of a successful replace."""

    vdf_path: Path
    backup_path: Path | None
    bytes_written: int


def lock_path_for(vdf_path: Path) -> Path:
    """Advisory lock file kept next to the VDF, per installation+account."""
    return vdf_path.with_name(vdf_path.name + ".lock")


def temp_path_for(vdf_path: Path) -> Path:
    """Same-directory temp used for the durable write."""
    return vdf_path.with_name(vdf_path.name + ".tmp")


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


def _backup_destination(vdf_path: Path, when: datetime) -> Path:
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = vdf_path.with_name(f"{vdf_path.name}{_BACKUP_PREFIX}{stamp}")
    if not candidate.exists():
        return candidate
    for suffix in range(2, 1000):
        collided = vdf_path.with_name(
            f"{vdf_path.name}{_BACKUP_PREFIX}{stamp}-{suffix}"
        )
        if not collided.exists():
            return collided
    raise CommitError(f"could not allocate a backup name under {vdf_path.parent}")


def _existing_backups(vdf_path: Path) -> list[Path]:
    prefix = f"{vdf_path.name}{_BACKUP_PREFIX}"
    found = [
        path
        for path in vdf_path.parent.iterdir()
        if path.is_file() and path.name.startswith(prefix)
    ]
    found.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    return found


def _prune_backups(vdf_path: Path, max_backups: int) -> None:
    extras = _existing_backups(vdf_path)[:-max_backups] if max_backups > 0 else []
    for path in extras:
        _unlink_if_exists(path)


def _live_bytes(path: Path) -> bytes:
    if not path.is_file():
        return b""
    return path.read_bytes()


def _validate_temp(temp_path: Path, expected: bytes, document: ShortcutDocument) -> None:
    try:
        payload = temp_path.read_bytes()
    except OSError as error:
        raise CommitValidationError(f"could not reopen temporary VDF: {error}") from error
    try:
        parsed = ShortcutDocument.loads(payload)
    except ValueError as error:
        raise CommitValidationError(f"temporary VDF failed parse-back: {error}") from error
    if parsed.dumps() != expected:
        raise CommitValidationError("parsed temporary VDF does not re-serialize identically")
    if parsed.occupied_appids() != document.occupied_appids():
        raise CommitValidationError("parsed temporary VDF AppIDs do not match the document")
    if len(parsed.entries()) != len(document.entries()):
        raise CommitValidationError("parsed temporary VDF entry count does not match the document")


class ImporterLock:
    """Exclusive advisory lock for one ``shortcuts.vdf``.

    Held through the whole read-modify-write. Non-blocking: a second importer
    fails immediately rather than waiting, so the UI can say the file is busy.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise ImporterLockedError(
                f"another importer already holds {self.path}"
            ) from error
        self._fd = fd

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> ImporterLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def commit_shortcuts(
    vdf_path: Path,
    document: ShortcutDocument,
    *,
    original_bytes: bytes | None = None,
    steam_status: SteamRunningStatus | None = None,
    hooks: CommitHooks | None = None,
    max_backups: int = MAX_BACKUPS,
    lock: ImporterLock | None = None,
) -> CommitResult:
    """Replace ``vdf_path`` with ``document`` using the §18.3 sequence.

    ``original_bytes`` is the live file as read when the document was built
    (empty if it did not exist). After the lock is taken the live bytes must
    still match, otherwise another writer won the race.

    The live file is never truncated in place. On any failure before
    ``os.replace``, it is left untouched.
    """
    active_hooks = hooks if hooks is not None else CommitHooks()
    initial = steam_status if steam_status is not None else active_hooks.detect_steam()
    _require_steam_closed(initial)

    payload = document.dumps()
    owned_lock = lock is None
    held = lock if lock is not None else ImporterLock(lock_path_for(vdf_path))
    temp_path = temp_path_for(vdf_path)
    replaced = False
    try:
        if owned_lock:
            held.acquire()
        vdf_path.parent.mkdir(parents=True, exist_ok=True)
        if original_bytes is not None and _live_bytes(vdf_path) != original_bytes:
            raise VdfChangedError(
                f"{vdf_path} changed after the in-memory document was built"
            )

        _write_temp(temp_path, payload)
        if active_hooks.after_serialize is not None:
            active_hooks.after_serialize(temp_path)
        _validate_temp(temp_path, payload, document)
        if active_hooks.after_validate is not None:
            active_hooks.after_validate(temp_path)

        backup_path: Path | None = None
        if vdf_path.is_file():
            backup_path = _backup_destination(vdf_path, datetime.now(timezone.utc))
            active_hooks.copy_file(vdf_path, backup_path)
            _prune_backups(vdf_path, max_backups)
        if active_hooks.after_backup is not None:
            active_hooks.after_backup(backup_path)

        _require_steam_closed(active_hooks.detect_steam())
        if active_hooks.before_replace is not None:
            active_hooks.before_replace(temp_path, vdf_path)

        active_hooks.replace(str(temp_path), str(vdf_path))
        replaced = True
        _fsync_directory(vdf_path.parent)
        return CommitResult(
            vdf_path=vdf_path,
            backup_path=backup_path,
            bytes_written=len(payload),
        )
    finally:
        if not replaced:
            _unlink_if_exists(temp_path)
        if owned_lock:
            held.release()
