"""Phase 10 fingerprinting: unrelated shortcuts must survive a write."""

from __future__ import annotations

import json

from steam_desktop_importer.steam.snapshot import (
    compare_snapshots,
    grid_appid_from_name,
    snapshot_from_json,
    snapshot_grid,
    snapshot_library,
)
from steam_desktop_importer.steam.shortcuts import ShortcutDocument


def test_grid_appid_from_steam_style_names():
    assert grid_appid_from_name("3681933876p.png") == 3681933876
    assert grid_appid_from_name("3681933876.jpg") == 3681933876
    assert grid_appid_from_name("3681933876_hero.webp") == 3681933876
    assert grid_appid_from_name("3681933876_logo.png") == 3681933876
    assert grid_appid_from_name("3681933876_icon.jpg") == 3681933876
    assert grid_appid_from_name("notes.txt") is None
    assert grid_appid_from_name("123p-wide.png") is None


def test_snapshot_round_trips_json_without_names(shortcuts_vdf_dir):
    source = shortcuts_vdf_dir / "mixed_writers.vdf"
    snap = snapshot_library(source, grid_path=None)
    assert snap.shortcut_count == 3
    assert snap.vdf_size == source.stat().st_size
    payload = json.dumps(snap.to_jsonable())
    restored = snapshot_from_json(payload)
    document = ShortcutDocument.load(source)
    for entry in document.entries():
        assert entry.name not in payload
        assert entry.exe not in payload
    assert restored.vdf_digest == snap.vdf_digest
    assert [item.appid_unsigned for item in restored.shortcuts] == [
        item.appid_unsigned for item in snap.shortcuts
    ]


def test_adding_a_shortcut_leaves_unrelated_fingerprints(shortcuts_vdf_dir, tmp_path):
    source = shortcuts_vdf_dir / "mixed_writers.vdf"
    before = snapshot_library(source)
    document = ShortcutDocument.load(source)
    existing = {item.appid_unsigned for item in before.shortcuts}
    new_appid = 0xF00DF00D
    assert new_appid not in existing
    document.add_new(
        appid_unsigned=new_appid,
        name="Phase 10 Probe",
        exe="/usr/bin/true",
    )
    written = tmp_path / "shortcuts.vdf"
    written.write_bytes(document.dumps())
    after = snapshot_library(written)
    report = compare_snapshots(before, after, managed_appids={new_appid})
    assert report.ok
    assert report.added_appids == (new_appid,)
    assert report.unmanaged_removed_appids == ()
    assert report.unmanaged_changed_appids == ()
    assert len(report.unchanged_appids) == before.shortcut_count


def test_mutating_an_unrelated_shortcut_is_detected(shortcuts_vdf_dir, tmp_path):
    source = shortcuts_vdf_dir / "mixed_writers.vdf"
    before = snapshot_library(source)
    victim = before.shortcuts[0]
    document = ShortcutDocument.load(source)
    document.update_by_appid(victim.appid_unsigned, name="mutated")
    written = tmp_path / "shortcuts.vdf"
    written.write_bytes(document.dumps())
    after = snapshot_library(written)
    report = compare_snapshots(before, after, managed_appids=())
    assert report.ok is False
    assert victim.appid_unsigned in report.unmanaged_changed_appids


def test_grid_survival_ignores_managed_appid_files(tmp_path):
    grid = tmp_path / "grid"
    grid.mkdir()
    (grid / "111.png").write_bytes(b"wide-a")
    (grid / "111p.png").write_bytes(b"portrait-a")
    (grid / "222_hero.png").write_bytes(b"hero-b")
    before = snapshot_grid(grid)
    assert {item.name for item in before} == {"111.png", "111p.png", "222_hero.png"}
    (grid / "111.png").write_bytes(b"wide-a")
    (grid / "333p.png").write_bytes(b"new-portrait")
    (grid / "222_hero.png").write_bytes(b"hero-b")
    from steam_desktop_importer.steam.snapshot import LibrarySnapshot

    def wrap(files):
        return LibrarySnapshot(
            vdf_digest="x",
            vdf_size=0,
            shortcut_count=0,
            shortcuts=(),
            duplicate_appids=(),
            grid=files,
        )

    report = compare_snapshots(wrap(before), wrap(snapshot_grid(grid)), managed_appids={333})
    assert report.ok
    assert report.grid_added == ("333p.png",)
    assert report.unmanaged_grid_added == ()
    assert report.unmanaged_grid_changed == ()
