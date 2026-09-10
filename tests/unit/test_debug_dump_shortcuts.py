"""``debug dump-shortcuts`` (IMPLEMENTATION.md §33, Phase 6)."""

from __future__ import annotations

from steam_desktop_importer.main import build_parser, main
from steam_desktop_importer.models import SteamAccount, SteamInstallation
from steam_desktop_importer.state import default_state_path


def test_dump_shortcuts_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(["debug", "dump-shortcuts"])
    assert args.func.__name__ == "_print_dump_shortcuts"


def test_dump_shortcuts_is_read_only(monkeypatch, tmp_path, capsys, shortcuts_vdf_dir):
    root = tmp_path / "Steam"
    userdata = root / "userdata" / "11111111"
    config = userdata / "config"
    config.mkdir(parents=True)
    target = config / "shortcuts.vdf"
    payload = (shortcuts_vdf_dir / "single_steam_written.vdf").read_bytes()
    target.write_bytes(payload)

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

    assert main(["debug", "dump-shortcuts"]) == 0
    output = capsys.readouterr().out
    assert "Example Application" in output
    assert "0x87b9685f" in output.lower()
    assert target.read_bytes() == payload
    assert list(tmp_path.rglob("state.sqlite3")) == []
    assert not default_state_path().exists()
