"""Main-window wiring that can be checked without a display."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from steam_desktop_importer.models import DesktopApplication, SteamAccount, SteamInstallation
from steam_desktop_importer.steam import AccountSelection
from steam_desktop_importer.ui.account_dialog import AccountDialog
from steam_desktop_importer.ui.main_window import MainWindow
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


def test_window_constructs_without_scanning(qapp):
    window = MainWindow(auto_refresh=False)
    assert window.windowTitle() == "Steam Desktop Importer"
    assert window.import_button.isEnabled() is False
    assert "Phase 7" in window.import_button.toolTip()
    assert window.imported_filter.isEnabled() is False
    window.close()


def test_settings_dialog_does_not_offer_a_steamgriddb_key(qapp):
    from PySide6.QtWidgets import QLabel

    dialog = SettingsDialog()
    body = "\n".join(widget.text() for widget in dialog.findChildren(QLabel))
    assert "Nothing here is persisted" in body
    assert "API-key" in body
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
    window = MainWindow(auto_refresh=False)
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
    window.close()


def test_several_installations_start_on_the_placeholder(qapp, tmp_path):
    window = MainWindow(auto_refresh=False)
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
    window = MainWindow(auto_refresh=False)
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
