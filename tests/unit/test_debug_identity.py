"""``debug identity`` (IMPLEMENTATION.md §33, Phase 5)."""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.desktop.discovery import DiscoveryResult
from steam_desktop_importer.main import build_parser, main
from steam_desktop_importer.models import DesktopApplication, SteamAccount, SteamInstallation
from steam_desktop_importer.state import default_state_path
from steam_desktop_importer.steam.appid import first_import_candidate, uint32_to_int32


def make_app() -> DesktopApplication:
    return DesktopApplication(
        desktop_id="org.example.App.desktop",
        desktop_path=Path("/usr/share/applications/org.example.App.desktop"),
        name="Example",
        localized_name=None,
        raw_exec="/usr/bin/example %U",
        exec_argv=["/usr/bin/example"],
        icon_name=None,
        icon_source_path=None,
        working_directory=None,
        try_exec=None,
        terminal=False,
        dbus_activatable=False,
        hidden=False,
        no_display=False,
        only_show_in=[],
        not_show_in=[],
        source_kind="native",
        flatpak_id=None,
        snap_instance=None,
        supported_for_import=True,
        unsupported_reason=None,
    )


def test_identity_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(["debug", "identity", "org.example.App.desktop"])
    assert args.func.__name__ == "_print_identity"


def test_unknown_desktop_id_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_applications",
        lambda: DiscoveryResult(),
    )
    assert main(["debug", "identity", "missing.desktop"]) == 1
    assert "no resolved entry" in capsys.readouterr().err


def test_identity_does_not_create_state_and_shows_the_candidate(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    app = make_app()
    installation = SteamInstallation(
        kind="native",
        root=tmp_path / "Steam",
        userdata_root=tmp_path / "Steam" / "userdata",
        display_name="native",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="user",
        persona_name="User",
        userdata_dir=tmp_path / "Steam" / "userdata" / "11111111",
        selection_hints=[],
    )
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_applications",
        lambda: DiscoveryResult(applications={app.desktop_id: app}),
    )
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_installations",
        lambda: [installation],
    )
    monkeypatch.setattr(
        "steam_desktop_importer.main.discover_accounts",
        lambda _installation: [account],
    )

    assert main(["debug", "identity", app.desktop_id]) == 0
    output = capsys.readouterr().out
    candidate = first_import_candidate(app.desktop_id)
    assert f"desktop ID          {app.desktop_id}" in output
    assert "persisted AppID     (none" in output
    assert f"first-import cand.  {candidate}" in output
    assert f"signed VDF          {uint32_to_int32(candidate)}" in output
    assert "import status       New" in output
    assert not default_state_path().exists()
    assert list(tmp_path.rglob("state.sqlite3")) == []
