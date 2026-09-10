"""Phase 7 import workflow: VDF commit then state mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from steam_desktop_importer.models import DesktopApplication, SteamAccount, SteamInstallation
from steam_desktop_importer.state import STATUS_IMPORTED, StateStore, import_status
from steam_desktop_importer.steam.appid import first_import_candidate
from steam_desktop_importer.steam.commit import CommitHooks, SteamIsRunningError
from steam_desktop_importer.steam.importing import (
    ImportPlanningError,
    apply_applications,
    relink_application,
)
from steam_desktop_importer.steam.running import SteamRunningStatus
from steam_desktop_importer.steam.shortcut_identities import shortcuts_vdf_path
from steam_desktop_importer.steam.shortcuts import ShortcutDocument

CLOSED = SteamRunningStatus(running=False, evidence=(), inspection_failures=0)


def _hooks() -> CommitHooks:
    return CommitHooks(detect_steam=lambda: CLOSED)


def _installation(root: Path) -> SteamInstallation:
    return SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="native",
    )


def _account(root: Path, account_id32: int = 11111111) -> SteamAccount:
    return SteamAccount(
        steam_id64="76561197971376839",
        account_id32=account_id32,
        account_name="tester",
        persona_name="Tester",
        userdata_dir=root / "userdata" / str(account_id32),
        selection_hints=[],
    )


def make_app(desktop_path: Path, **overrides) -> DesktopApplication:
    desktop_path.write_text(
        "[Desktop Entry]\nType=Application\nName=Example\nExec=/usr/bin/example\n",
        encoding="utf-8",
    )
    values = {
        "desktop_id": "org.example.App.desktop",
        "desktop_path": desktop_path,
        "name": "Example",
        "localized_name": None,
        "raw_exec": "/usr/bin/example",
        "exec_argv": ["/usr/bin/example"],
        "icon_name": None,
        "icon_source_path": None,
        "working_directory": None,
        "try_exec": None,
        "terminal": False,
        "dbus_activatable": False,
        "hidden": False,
        "no_display": False,
        "only_show_in": [],
        "not_show_in": [],
        "source_kind": "native",
        "flatpak_id": None,
        "snap_instance": None,
        "supported_for_import": True,
        "unsupported_reason": None,
    }
    values.update(overrides)
    return DesktopApplication(**values)


def test_first_import_writes_vdf_then_state(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    app = make_app(tmp_path / "org.example.App.desktop")
    store = StateStore(":memory:")

    result = apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )

    expected = first_import_candidate(app.desktop_id)
    assert result.imported[0].action == "created"
    assert result.imported[0].appid_unsigned == expected
    assert result.state_errors == ()
    document = ShortcutDocument.load(shortcuts_vdf_path(account))
    entry = document.find_by_appid(expected)
    assert entry is not None
    assert entry.name == "Example"
    assert entry.exe == '"/usr/bin/example"'
    mapping = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
    assert mapping is not None
    assert mapping.steam_appid_unsigned == expected
    assert import_status(app, mapping) == STATUS_IMPORTED


def test_changed_entry_updates_in_place_and_keeps_appid(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    path = tmp_path / "org.example.App.desktop"
    app = make_app(path)
    store = StateStore(":memory:")
    apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Renamed\nExec=/usr/bin/renamed\n",
        encoding="utf-8",
    )
    changed = make_app(
        path,
        name="Renamed",
        raw_exec="/usr/bin/renamed",
        exec_argv=["/usr/bin/renamed"],
    )
    result = apply_applications(
        [changed],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )
    assert result.imported[0].action == "updated"
    appid = first_import_candidate(app.desktop_id)
    assert result.imported[0].appid_unsigned == appid
    entry = ShortcutDocument.load(shortcuts_vdf_path(account)).find_by_appid(appid)
    assert entry is not None
    assert entry.name == "Renamed"
    assert entry.exe == '"/usr/bin/renamed"'
    mapping = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
    assert mapping is not None
    assert mapping.steam_appid_unsigned == appid
    assert mapping.last_known_name == "Renamed"


def test_possible_match_is_not_auto_imported(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    vdf_path = shortcuts_vdf_path(account)
    vdf_path.parent.mkdir(parents=True)
    document = ShortcutDocument.empty()
    document.add_new(
        appid_unsigned=0x8000ABCD,
        name="Example",
        exe="/usr/bin/example",
    )
    vdf_path.write_bytes(document.dumps())
    original = vdf_path.read_bytes()
    app = make_app(tmp_path / "org.example.App.desktop")
    store = StateStore(":memory:")
    with pytest.raises(ImportPlanningError, match="relinked explicitly"):
        apply_applications(
            [app],
            installation=installation,
            account=account,
            store=store,
            steam_status=CLOSED,
            hooks=_hooks(),
        )
    assert vdf_path.read_bytes() == original
    assert store.get_mapping(installation.key, account.account_id32, app.desktop_id) is None


def test_relink_takes_ownership_of_the_existing_appid(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    vdf_path = shortcuts_vdf_path(account)
    vdf_path.parent.mkdir(parents=True)
    document = ShortcutDocument.empty()
    document.add_new(
        appid_unsigned=0x8000ABCD,
        name="Example",
        exe="/usr/bin/example",
    )
    vdf_path.write_bytes(document.dumps())
    app = make_app(tmp_path / "org.example.App.desktop")
    store = StateStore(":memory:")
    result = relink_application(
        app,
        0x8000ABCD,
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )
    assert result.imported[0].action == "relinked"
    assert result.imported[0].appid_unsigned == 0x8000ABCD
    mapping = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
    assert mapping is not None
    assert mapping.steam_appid_unsigned == 0x8000ABCD
    assert ShortcutDocument.load(vdf_path).find_by_appid(0x8000ABCD).name == "Example"


def test_relink_refuses_an_appid_owned_by_another_desktop_id(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    owner = make_app(tmp_path / "owner.desktop", desktop_id="owner.desktop")
    store = StateStore(":memory:")
    apply_applications(
        [owner],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )
    owned = store.get_mapping(installation.key, account.account_id32, owner.desktop_id)
    assert owned is not None
    other = make_app(tmp_path / "other.desktop", desktop_id="other.desktop", name="Other")
    with pytest.raises(ImportPlanningError, match="already owned"):
        relink_application(
            other,
            owned.steam_appid_unsigned,
            installation=installation,
            account=account,
            store=store,
            steam_status=CLOSED,
            hooks=_hooks(),
        )


def test_missing_desktop_file_does_not_write(tmp_path):
    root = tmp_path / "Steam"
    app = make_app(tmp_path / "org.example.App.desktop")
    app.desktop_path.unlink()
    store = StateStore(":memory:")
    with pytest.raises(ImportPlanningError, match="missing"):
        apply_applications(
            [app],
            installation=_installation(root),
            account=_account(root),
            store=store,
            steam_status=CLOSED,
            hooks=_hooks(),
        )
    assert not shortcuts_vdf_path(_account(root)).exists()


def test_steam_running_blocks_import_before_any_write(tmp_path):
    root = tmp_path / "Steam"
    app = make_app(tmp_path / "org.example.App.desktop")
    store = StateStore(":memory:")
    running = SteamRunningStatus(running=True, evidence=("process name 'steam'",))
    with pytest.raises(SteamIsRunningError):
        apply_applications(
            [app],
            installation=_installation(root),
            account=_account(root),
            store=store,
            steam_status=running,
            hooks=CommitHooks(detect_steam=lambda: running),
        )
    assert not shortcuts_vdf_path(_account(root)).exists()
    assert store.get_mapping(_installation(root).key, 11111111, app.desktop_id) is None


def test_vdf_commit_precedes_state_so_a_store_failure_does_not_roll_back(tmp_path, monkeypatch):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    app = make_app(tmp_path / "org.example.App.desktop")
    store = StateStore(":memory:")

    def boom(*_args, **_kwargs):
        raise RuntimeError("state commit failed")

    monkeypatch.setattr(store, "save_mapping", boom)
    result = apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
    )
    assert result.state_errors
    assert "state commit failed" in result.state_errors[0]
    document = ShortcutDocument.load(shortcuts_vdf_path(account))
    assert document.find_by_appid(first_import_candidate(app.desktop_id)) is not None
    assert store.get_mapping(installation.key, account.account_id32, app.desktop_id) is None


def test_planning_failure_in_a_batch_writes_nothing(tmp_path):
    root = tmp_path / "Steam"
    good = make_app(tmp_path / "good.desktop", desktop_id="good.desktop")
    bad = make_app(
        tmp_path / "bad.desktop",
        desktop_id="bad.desktop",
        supported_for_import=False,
        unsupported_reason="nope",
    )
    store = StateStore(":memory:")
    with pytest.raises(ImportPlanningError, match="nope"):
        apply_applications(
            [good, bad],
            installation=_installation(root),
            account=_account(root),
            store=store,
            steam_status=CLOSED,
            hooks=_hooks(),
        )
    assert not shortcuts_vdf_path(_account(root)).exists()
    assert store.list_mappings(_installation(root).key, 11111111) == []
