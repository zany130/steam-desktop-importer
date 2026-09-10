"""Phase 7 safe VDF commit: lock, backup, fsync, parse-back, replace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from steam_desktop_importer.steam.commit import (
    CommitHooks,
    CommitValidationError,
    ImporterLock,
    ImporterLockedError,
    SteamIsRunningError,
    VdfChangedError,
    commit_shortcuts,
    temp_path_for,
)
from steam_desktop_importer.steam.running import SteamRunningStatus, steam_allows_write
from steam_desktop_importer.steam.shortcuts import ShortcutDocument

CLOSED = SteamRunningStatus(running=False, evidence=(), inspection_failures=0)
RUNNING = SteamRunningStatus(
    running=True, evidence=("process name 'steam'",), inspection_failures=0
)
UNCERTAIN = SteamRunningStatus(running=False, evidence=(), inspection_failures=4)


def _closed_hooks(**overrides) -> CommitHooks:
    return CommitHooks(detect_steam=lambda: CLOSED, **overrides)


def _copy_fixture(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def test_steam_allows_write_requires_a_certain_negative():
    assert steam_allows_write(CLOSED) is True
    assert steam_allows_write(RUNNING) is False
    assert steam_allows_write(UNCERTAIN) is False


def test_commit_replaces_live_file_and_keeps_a_backup(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="After Commit")

    result = commit_shortcuts(
        live, document, original_bytes=original, hooks=_closed_hooks()
    )

    assert live.read_bytes() == document.dumps()
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == original
    assert result.backup_path.name.startswith("shortcuts.vdf.bak-")
    assert not temp_path_for(live).exists()
    reloaded = ShortcutDocument.load(live)
    assert reloaded.find_by_appid(0x87B9685F).name == "After Commit"


def test_commit_creates_a_missing_file(tmp_path):
    live = tmp_path / "config" / "shortcuts.vdf"
    document = ShortcutDocument.empty()
    document.add_new(
        appid_unsigned=0x80000001,
        name="First",
        exe="/usr/bin/first",
    )
    result = commit_shortcuts(live, document, original_bytes=b"", hooks=_closed_hooks())
    assert live.is_file()
    assert result.backup_path is None
    assert ShortcutDocument.load(live).find_by_appid(0x80000001).name == "First"


def test_steam_running_at_start_does_not_touch_the_live_file(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Should Not Land")
    with pytest.raises(SteamIsRunningError, match="running"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            steam_status=RUNNING,
            hooks=_closed_hooks(),
        )
    assert live.read_bytes() == original
    assert not temp_path_for(live).exists()
    assert list(tmp_path.glob("*.bak-*")) == []


def test_uncertain_steam_status_blocks_the_write(tmp_path):
    live = tmp_path / "shortcuts.vdf"
    document = ShortcutDocument.empty()
    document.add_new(appid_unsigned=0x80000001, name="X", exe="/usr/bin/x")
    with pytest.raises(SteamIsRunningError, match="uncertain"):
        commit_shortcuts(
            live,
            document,
            original_bytes=b"",
            steam_status=UNCERTAIN,
            hooks=_closed_hooks(),
        )
    assert not live.exists()


def test_crash_after_temp_serialization_leaves_live_file(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Crashed")

    def boom(path: Path) -> None:
        assert path.exists()
        raise RuntimeError("crash after temp serialization")

    with pytest.raises(RuntimeError, match="crash after temp"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            hooks=_closed_hooks(after_serialize=boom),
        )
    assert live.read_bytes() == original
    assert not temp_path_for(live).exists()
    assert list(tmp_path.glob("*.bak-*")) == []


def test_validation_failure_does_not_replace(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Invalidated")

    def corrupt(path: Path) -> None:
        path.write_bytes(b"not a vdf")

    with pytest.raises(CommitValidationError, match="parse-back"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            hooks=_closed_hooks(after_serialize=corrupt),
        )
    assert live.read_bytes() == original
    assert not temp_path_for(live).exists()


def test_crash_before_replace_keeps_live_file_and_leaves_a_backup(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Not Replaced")

    def boom(_temp: Path, _live: Path) -> None:
        raise RuntimeError("crash before replace")

    with pytest.raises(RuntimeError, match="crash before replace"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            hooks=_closed_hooks(before_replace=boom),
        )
    assert live.read_bytes() == original
    backups = list(tmp_path.glob("shortcuts.vdf.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert not temp_path_for(live).exists()


def test_replace_failure_leaves_live_file(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Replace Failed")

    def fail(source: str, destination: str) -> None:
        raise OSError("replace failed")

    with pytest.raises(OSError, match="replace failed"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            hooks=_closed_hooks(replace=fail),
        )
    assert live.read_bytes() == original
    assert not temp_path_for(live).exists()


def test_steam_starting_during_transaction_aborts_before_replace(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    document.update_by_appid(0x87B9685F, name="Steam Opened")
    probes = [RUNNING]

    def detect() -> SteamRunningStatus:
        return probes.pop(0)

    with pytest.raises(SteamIsRunningError, match="running"):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            steam_status=CLOSED,
            hooks=CommitHooks(detect_steam=detect),
        )
    assert live.read_bytes() == original
    assert probes == []


def test_concurrent_importer_cannot_take_the_lock(tmp_path):
    lock_path = tmp_path / "shortcuts.vdf.lock"
    holder = ImporterLock(lock_path)
    holder.acquire()
    try:
        script = (
            "from pathlib import Path\n"
            "from steam_desktop_importer.steam.commit import ImporterLock, ImporterLockedError\n"
            f"try:\n"
            f"    ImporterLock(Path({str(lock_path)!r})).acquire()\n"
            "except ImporterLockedError:\n"
            "    raise SystemExit(2)\n"
            "raise SystemExit(0)\n"
        )
        env = os.environ.copy()
        src = str(Path(__file__).resolve().parents[2] / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2, result.stderr
    finally:
        holder.release()


def test_same_process_second_lock_is_non_blocking(tmp_path):
    path = tmp_path / "shortcuts.vdf.lock"
    with ImporterLock(path):
        with pytest.raises(ImporterLockedError):
            ImporterLock(path).acquire()


def test_stale_document_is_refused(shortcuts_vdf_dir, tmp_path):
    live = _copy_fixture(shortcuts_vdf_dir / "single_steam_written.vdf", tmp_path / "shortcuts.vdf")
    original = live.read_bytes()
    document = ShortcutDocument.load(live)
    live.write_bytes(original + b"\x00")
    with pytest.raises(VdfChangedError):
        commit_shortcuts(
            live,
            document,
            original_bytes=original,
            hooks=_closed_hooks(),
        )


def test_backup_names_do_not_collide(shortcuts_vdf_dir, tmp_path, monkeypatch):
    live = _copy_fixture(shortcuts_vdf_dir / "empty.vdf", tmp_path / "shortcuts.vdf")
    from datetime import datetime, timezone

    from steam_desktop_importer.steam import commit as commit_mod

    frozen = datetime(2026, 9, 10, 14, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(commit_mod, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: frozen)}))

    first = ShortcutDocument.load(live)
    first.add_new(appid_unsigned=0x80000001, name="A", exe="/usr/bin/a")
    commit_shortcuts(live, first, original_bytes=live.read_bytes(), hooks=_closed_hooks())
    second = ShortcutDocument.load(live)
    second.add_new(appid_unsigned=0x80000002, name="B", exe="/usr/bin/b")
    commit_shortcuts(live, second, original_bytes=live.read_bytes(), hooks=_closed_hooks())
    names = sorted(path.name for path in tmp_path.glob("shortcuts.vdf.bak-*"))
    assert names == [
        "shortcuts.vdf.bak-20260910T143000Z",
        "shortcuts.vdf.bak-20260910T143000Z-2",
    ]


def test_backup_history_is_bounded(tmp_path):
    live = tmp_path / "shortcuts.vdf"
    document = ShortcutDocument.empty()
    document.add_new(appid_unsigned=0x80000001, name="A", exe="/usr/bin/a")
    commit_shortcuts(live, document, original_bytes=b"", hooks=_closed_hooks(), max_backups=3)
    for index in range(5):
        current = ShortcutDocument.load(live)
        current.update_by_appid(0x80000001, name=f"A{index}")
        commit_shortcuts(
            live,
            current,
            original_bytes=live.read_bytes(),
            hooks=_closed_hooks(),
            max_backups=3,
        )
    backups = list(tmp_path.glob("shortcuts.vdf.bak-*"))
    assert len(backups) == 3


def test_lock_is_released_after_a_failed_commit(tmp_path):
    live = tmp_path / "shortcuts.vdf"
    document = ShortcutDocument.empty()
    document.add_new(appid_unsigned=0x80000001, name="A", exe="/usr/bin/a")

    def boom(_path: Path) -> None:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        commit_shortcuts(
            live, document, original_bytes=b"", hooks=_closed_hooks(after_serialize=boom)
        )
    # A later commit in this process must be able to take the lock again.
    commit_shortcuts(live, document, original_bytes=b"", hooks=_closed_hooks())
    assert live.is_file()
