"""Main-window wiring that can be checked without a display."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from steam_desktop_importer.models import (
    DesktopApplication,
    SteamAccount,
    SteamInstallation,
    UnsupportedCode,
)
from steam_desktop_importer.state import STATUS_IMPORTED, STATUS_NEW, StateStore
from steam_desktop_importer.steam import AccountSelection
from steam_desktop_importer.steam.appid import first_import_candidate
from steam_desktop_importer.steam.running import SteamRunningStatus
from steam_desktop_importer.steam.shortcut_identities import shortcuts_vdf_path
from steam_desktop_importer.steam.shortcuts import ShortcutDocument
from steam_desktop_importer.ui.account_dialog import AccountDialog
from steam_desktop_importer.ui.main_window import MainWindow
from steam_desktop_importer.ui.models import Column
from steam_desktop_importer.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def qapp():
    existing = QApplication.instance()
    if existing is not None:
        yield existing
        return
    app = QApplication([])
    yield app
    app.quit()


def _window(store: StateStore | None = None) -> MainWindow:
    return MainWindow(auto_refresh=False, state_store=store or StateStore(":memory:"))


def test_window_constructs_without_scanning(qapp):
    window = _window()
    assert window.windowTitle() == "Steam Desktop Importer"
    assert window.import_button.isEnabled() is False
    assert window.relink_button.isEnabled() is False
    assert "Steam" in window.import_button.toolTip()
    assert window.imported_filter.isEnabled() is True
    window.close()


def test_settings_dialog_has_a_steamgriddb_key_field(qapp):
    from PySide6.QtWidgets import QLabel, QLineEdit

    dialog = SettingsDialog()
    body = "\n".join(widget.text() for widget in dialog.findChildren(QLabel))
    assert "SteamGridDB" in body
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
    dialog.close()


def test_account_dialog_preselects_the_ranked_account(qapp, tmp_path):
    accounts = [
        SteamAccount(
            steam_id64="76561197971376839",
            account_id32=11111111,
            account_name="alpha_user",
            persona_name="Alpha",
            userdata_dir=tmp_path / "11111111",
            selection_hints=["registry-autologinuser"],
        ),
        SteamAccount(
            steam_id64="76561197982487950",
            account_id32=22222222,
            account_name="beta_user",
            persona_name="Beta",
            userdata_dir=tmp_path / "22222222",
            selection_hints=["loginusers-autologin", "timestamp:9"],
        ),
    ]
    selection = AccountSelection(
        accounts=tuple(accounts),
        selected=accounts[0],
        requires_confirmation=True,
        reason="2 accounts found; confirmation required",
    )
    dialog = AccountDialog(selection)
    assert dialog._list.currentRow() == 0
    dialog.accept()
    assert dialog.chosen_account() is accounts[0]
    dialog.close()


def test_filling_a_single_installation_resolves_without_a_placeholder(qapp, tmp_path):
    window = _window()
    root = tmp_path / "Steam"
    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="Native Steam",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="single_user",
        persona_name="Single",
        userdata_dir=root / "userdata" / "11111111",
        selection_hints=[],
    )
    window._installations = [(installation, [account])]
    window._fill_installations()
    assert window.install_combo.currentData() == installation.key
    assert window.account_combo.currentData() == 11111111
    assert window._selected_account is account
    assert window._store.remembered_installation() == installation.key
    assert window._store.remembered_account(installation.key) == 11111111
    window.close()


def test_several_installations_start_on_the_placeholder(qapp, tmp_path):
    window = _window()
    native = SteamInstallation(
        kind="native",
        root=tmp_path / "native",
        userdata_root=tmp_path / "native" / "userdata",
        display_name="native",
    )
    flatpak = SteamInstallation(
        kind="flatpak",
        root=tmp_path / "flatpak",
        userdata_root=tmp_path / "flatpak" / "userdata",
        display_name="flatpak",
    )
    window._installations = [(native, []), (flatpak, [])]
    window._fill_installations()
    assert window.install_combo.currentData() is None
    assert "Select a Steam installation" in window.install_combo.currentText()
    window.close()


def test_populated_table_updates_the_status_line(qapp):
    window = _window()
    app = DesktopApplication(
        desktop_id="org.example.App.desktop",
        desktop_path=Path("/usr/share/applications/org.example.App.desktop"),
        name="Example",
        localized_name=None,
        raw_exec="/usr/bin/example",
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
    window._model.set_applications([app])
    window._update_counts()
    assert "1 shown" in window._counts.text()
    assert "1 importable" in window._counts.text()
    window.close()


def test_remembered_installation_skips_the_placeholder(qapp, tmp_path):
    store = StateStore(":memory:")
    native = SteamInstallation(
        kind="native",
        root=tmp_path / "native",
        userdata_root=tmp_path / "native" / "userdata",
        display_name="native",
    )
    flatpak = SteamInstallation(
        kind="flatpak",
        root=tmp_path / "flatpak",
        userdata_root=tmp_path / "flatpak" / "userdata",
        display_name="flatpak",
    )
    store.remember_installation(flatpak.key)
    window = _window(store)
    window._installations = [(native, []), (flatpak, [])]
    window._fill_installations()
    assert window.install_combo.currentData() == flatpak.key
    window.close()


def test_acknowledge_persists_then_rescans(qapp, monkeypatch):
    store = StateStore(":memory:")
    window = _window(store)
    scanned: list[bool] = []
    monkeypatch.setattr(window, "_start_scan", lambda: scanned.append(True))
    app = DesktopApplication(
        desktop_id="vendor-app.desktop",
        desktop_path=Path("/tmp/vendor-app.desktop"),
        name="Vendor",
        localized_name=None,
        raw_exec="/usr/bin/vendor",
        exec_argv=["/usr/bin/vendor"],
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
        supported_for_import=False,
        unsupported_reason="desktop ID collision",
        unsupported_code=UnsupportedCode.DESKTOP_ID_COLLISION,
        collision_paths=[Path("/tmp/a.desktop"), Path("/tmp/b.desktop")],
    )
    window._model.set_applications([app])
    window.show()
    index = window._proxy.index(0, Column.NAME)
    assert index.isValid()
    window.table.setCurrentIndex(index)
    assert window.table.currentIndex().isValid()
    window._acknowledge_collision()
    acks = store.acknowledged_collisions()
    assert len(acks) == 1
    assert acks[0].desktop_id == "vendor-app.desktop"
    assert acks[0].matches(
        "vendor-app.desktop",
        Path("/tmp/vendor-app.desktop"),
        [Path("/tmp/a.desktop"), Path("/tmp/b.desktop")],
    )
    assert scanned == [True]
    window.close()


def test_scoped_rows_are_new_on_a_fresh_store(qapp, tmp_path):
    window = _window()
    root = tmp_path / "Steam"
    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="Native Steam",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="single_user",
        persona_name="Single",
        userdata_dir=root / "userdata" / "11111111",
        selection_hints=[],
    )
    app = DesktopApplication(
        desktop_id="org.example.App.desktop",
        desktop_path=Path("/usr/share/applications/org.example.App.desktop"),
        name="Example",
        localized_name=None,
        raw_exec="/usr/bin/example",
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
    window._installations = [(installation, [account])]
    window._model.set_applications([app])
    window._fill_installations()
    assert window._model.rows()[0].import_status == STATUS_NEW
    window.close()


CLOSED = SteamRunningStatus(running=False, evidence=(), inspection_failures=0)


def _gui_app(path: Path) -> DesktopApplication:
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Example\nExec=/usr/bin/example\n",
        encoding="utf-8",
    )
    return DesktopApplication(
        desktop_id="org.example.App.desktop",
        desktop_path=path,
        name="Example",
        localized_name=None,
        raw_exec="/usr/bin/example",
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


def test_import_stays_disabled_while_steam_is_running(qapp, tmp_path):
    window = _window()
    root = tmp_path / "Steam"
    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="Native Steam",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="single_user",
        persona_name="Single",
        userdata_dir=root / "userdata" / "11111111",
        selection_hints=[],
    )
    window._installations = [(installation, [account])]
    window._fill_installations()
    window._model.set_applications([_gui_app(tmp_path / "org.example.App.desktop")])
    window._model.set_all_selected(True)
    window._running = SteamRunningStatus(
        running=True, evidence=("process name 'steam'",), inspection_failures=0
    )
    window._update_import_actions()
    assert window.import_button.isEnabled() is False
    assert "Close Steam" in window.import_button.toolTip()
    window.close()


def test_import_selected_commits_vdf_and_state(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    store = StateStore(":memory:")
    window = MainWindow(
        auto_refresh=False,
        state_store=store,
        detect_steam=lambda: CLOSED,
    )
    root = tmp_path / "Steam"
    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="Native Steam",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="single_user",
        persona_name="Single",
        userdata_dir=root / "userdata" / "11111111",
        selection_hints=[],
    )
    window._installations = [(installation, [account])]
    window._fill_installations()
    app = _gui_app(tmp_path / "org.example.App.desktop")
    window._model.set_applications([app])
    window._refresh_import_statuses()
    window._model.set_all_selected(True)
    window._running = CLOSED
    window._update_import_actions()
    assert window.import_button.isEnabled() is True
    monkeypatch.setattr(window, "_confirm_write", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(window, "_prepare_import_artwork", lambda *_args, **_kwargs: {})
    window._import_selected()
    vdf = shortcuts_vdf_path(account)
    entry = ShortcutDocument.load(vdf).find_by_appid(first_import_candidate(app.desktop_id))
    assert entry is not None
    assert entry.name == "Example"
    mapping = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
    assert mapping is not None
    assert window._model.rows()[0].import_status == STATUS_IMPORTED
    window.close()


def test_prepare_artwork_skips_without_a_key(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("SGDB_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = _window()
    app = _gui_app(tmp_path / "org.example.App.desktop")
    assert window._prepare_import_artwork([app], tmp_path) == {}
    window.close()
