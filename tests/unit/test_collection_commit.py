"""Phase 12 safe collection cloud-storage commit."""

from __future__ import annotations

from pathlib import Path

import pytest

from steam_desktop_importer.steam.collection_commit import (
    CloudStorageChangedError,
    commit_collections,
    temp_path_for,
)
from steam_desktop_importer.steam.collections import CollectionAssignment, load_collections
from steam_desktop_importer.steam.commit import CommitHooks, SteamIsRunningError
from steam_desktop_importer.steam.running import SteamRunningStatus

from .test_collections import _account, seed_cloud_storage

CLOSED = SteamRunningStatus(running=False, evidence=(), inspection_failures=0)
RUNNING = SteamRunningStatus(
    running=True, evidence=("process name 'steam'",), inspection_failures=0
)


def _closed_hooks(**overrides) -> CommitHooks:
    return CommitHooks(detect_steam=lambda: CLOSED, **overrides)


def test_commit_replaces_namespace_and_keeps_a_backup(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    live = cloud / "cloud-storage-namespace-1.json"
    original = live.read_bytes()
    index = cloud / "cloud-storage-namespaces.json"
    original_index = index.read_bytes()
    document = load_collections(account)
    document.apply_assignment(
        CollectionAssignment(existing_ids=("uc-BBBB",)),
        [99],
        now=1_800_000_000,
    )

    result = commit_collections(
        document,
        original_namespace_bytes=original,
        original_index_bytes=original_index,
        hooks=_closed_hooks(),
    )

    reloaded = load_collections(account)
    linux = next(item for item in reloaded.live_collections() if item.collection_id == "uc-BBBB")
    assert 99 in linux.added
    assert result.namespace_backup is not None
    assert result.namespace_backup.read_bytes() == original
    assert result.index_backup is not None
    assert result.index_backup.read_bytes() == original_index
    assert not temp_path_for(live).exists()
    showcase = next(item for item in reloaded.entries if item[0] == "showcases.example")
    assert showcase[1]["value"] == '{"keep":true}'
    assert showcase[1]["version"] == "1973"


def test_steam_running_does_not_touch_cloud_storage(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    live = cloud / "cloud-storage-namespace-1.json"
    original = live.read_bytes()
    document = load_collections(account)
    document.apply_assignment(CollectionAssignment(create_names=("Nope",)), [1], now=1)
    with pytest.raises(SteamIsRunningError, match="running"):
        commit_collections(
            document,
            original_namespace_bytes=original,
            original_index_bytes=(cloud / "cloud-storage-namespaces.json").read_bytes(),
            steam_status=RUNNING,
            hooks=_closed_hooks(),
        )
    assert live.read_bytes() == original


def test_stale_namespace_bytes_refuse_the_write(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    live = cloud / "cloud-storage-namespace-1.json"
    original = live.read_bytes()
    document = load_collections(account)
    document.apply_assignment(CollectionAssignment(create_names=("X",)), [1], now=1)
    live.write_bytes(original + b" ")
    with pytest.raises(CloudStorageChangedError):
        commit_collections(
            document,
            original_namespace_bytes=original,
            original_index_bytes=(cloud / "cloud-storage-namespaces.json").read_bytes(),
            hooks=_closed_hooks(),
        )


def test_crash_after_serialize_leaves_live_files(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    live = cloud / "cloud-storage-namespace-1.json"
    original = live.read_bytes()
    document = load_collections(account)
    document.apply_assignment(CollectionAssignment(create_names=("Crash",)), [1], now=1)

    def boom(_path: Path) -> None:
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        commit_collections(
            document,
            original_namespace_bytes=original,
            original_index_bytes=(cloud / "cloud-storage-namespaces.json").read_bytes(),
            hooks=_closed_hooks(after_serialize=boom),
        )
    assert live.read_bytes() == original
    assert not temp_path_for(live).exists()


def test_second_replace_failure_rolls_back_namespace(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    live = cloud / "cloud-storage-namespace-1.json"
    index = cloud / "cloud-storage-namespaces.json"
    original = live.read_bytes()
    original_index = index.read_bytes()
    document = load_collections(account)
    document.apply_assignment(CollectionAssignment(create_names=("Crash",)), [1], now=1)
    calls = 0

    def fail_second_replace(source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replace failure")
        Path(source).replace(destination)

    with pytest.raises(OSError, match="injected replace failure"):
        commit_collections(
            document,
            original_namespace_bytes=original,
            original_index_bytes=original_index,
            hooks=_closed_hooks(replace=fail_second_replace),
        )
    assert live.read_bytes() == original
    assert index.read_bytes() == original_index
    assert not temp_path_for(live).exists()
    assert not temp_path_for(index).exists()
