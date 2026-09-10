"""``debug snapshot-shortcuts`` and ``debug compare-snapshots`` (Phase 10)."""

from __future__ import annotations

import json

from steam_desktop_importer.main import build_parser, main
from steam_desktop_importer.models import SteamAccount, SteamInstallation
from steam_desktop_importer.state import default_state_path
from steam_desktop_importer.steam.shortcut_identities import shortcuts_vdf_path
from steam_desktop_importer.steam.snapshot import snapshot_library
from steam_desktop_importer.steam.shortcuts import ShortcutDocument


def test_snapshot_commands_are_registered():
    parser = build_parser()
    snap = parser.parse_args(["debug", "snapshot-shortcuts"])
    assert snap.func.__name__ == "_print_snapshot_shortcuts"
    compare = parser.parse_args(
        ["debug", "compare-snapshots", "before.json", "after.json", "--ignore-appid", "1"]
    )
    assert compare.func.__name__ == "_print_compare_snapshots"
    assert compare.ignore_appid == [1]


def test_snapshot_shortcuts_is_read_only(monkeypatch, tmp_path, capsys, shortcuts_vdf_dir):
    root = tmp_path / "Steam"
    userdata = root / "userdata" / "11111111"
    config = userdata / "config"
    grid = config / "grid"
    grid.mkdir(parents=True)
    target = config / "shortcuts.vdf"
    payload = (shortcuts_vdf_dir / "mixed_writers.vdf").read_bytes()
    target.write_bytes(payload)
    (grid / "1.png").write_bytes(b"wide")

    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="native",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="user",
        persona_name="User",
        userdata_dir=userdata,
        selection_hints=[],
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_installations",
        lambda: [installation],
    )
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_accounts",
        lambda _installation: [account],
    )

    assert main(["debug", "snapshot-shortcuts"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["shortcut_count"] == 3
    assert data["grid"][0]["name"] == "1.png"
    assert target.read_bytes() == payload
    document = ShortcutDocument.load(target)
    for entry in document.entries():
        assert entry.name not in captured.out
    assert list(tmp_path.rglob("state.sqlite3")) == []
    assert not default_state_path().exists()
    assert shortcuts_vdf_path(account) == target


def test_compare_snapshots_command_detects_unrelated_edits(
    monkeypatch, tmp_path, capsys, shortcuts_vdf_dir
):
    source = shortcuts_vdf_dir / "mixed_writers.vdf"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before = snapshot_library(source)
    before_path.write_text(json.dumps(before.to_jsonable()), encoding="utf-8")
    document = ShortcutDocument.load(source)
    victim = before.shortcuts[0].appid_unsigned
    document.update_by_appid(victim, name="mutated")
    mutated = tmp_path / "mutated.vdf"
    mutated.write_bytes(document.dumps())
    after_path.write_text(
        json.dumps(snapshot_library(mutated).to_jsonable()), encoding="utf-8"
    )
    assert main(["debug", "compare-snapshots", str(before_path), str(after_path)]) == 1
    assert "CHANGED" in capsys.readouterr().out
