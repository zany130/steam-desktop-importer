"""``debug collections`` (Phase 12). Read-only."""

from __future__ import annotations

from steam_desktop_importer.main import build_parser, main
from steam_desktop_importer.models import SteamAccount, SteamInstallation
from steam_desktop_importer.state import default_state_path

from .test_collections import seed_cloud_storage


def test_collections_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(["debug", "collections"])
    assert args.func.__name__ == "_print_collections"


def test_debug_collections_is_read_only(monkeypatch, tmp_path, capsys):
    root = tmp_path / "Steam"
    userdata = root / "userdata" / "11111111"
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="user",
        persona_name="User",
        userdata_dir=userdata,
        selection_hints=[],
    )
    cloud = seed_cloud_storage(account)
    original = (cloud / "cloud-storage-namespace-1.json").read_bytes()
    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="native",
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

    assert main(["debug", "collections"]) == 0
    output = capsys.readouterr().out
    assert "Linux Apps" in output
    assert "uc-BBBB" in output
    assert "skipped" in output
    assert (cloud / "cloud-storage-namespace-1.json").read_bytes() == original
    assert list(tmp_path.rglob("state.sqlite3")) == []
    assert not default_state_path().exists()
