"""Main-window wiring that can be checked without a display."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QListWidgetItem

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
    assert window._steam_poll.isActive() is False
    assert "Steam" in window.import_button.toolTip()
    assert window.imported_filter.isEnabled() is True
    assert window.collection_list.count() == 0
    assert window.detail_tabs.tabText(0) == "Details"
    assert window.detail_tabs.tabText(1) == "Collections"
    assert window.detail_tabs.currentIndex() == 0
    window.close()


def test_collection_list_loads_assignable_collections(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from .test_collections import seed_cloud_storage

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
    seed_cloud_storage(account)
    window._installations = [(installation, [account])]
    window._fill_installations()
    names = [
        window.collection_list.item(i).text()
        for i in range(window.collection_list.count())
        if window.collection_list.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable
    ]
    assert names == [
        "Action (tag collection)",
        "Emulation",
        "Favorites",
        "Linux Apps",
        "Tools",
    ]
    labels = [
        window.collection_list.item(i).text()
        for i in range(window.collection_list.count())
    ]
    assert "Hidden" not in labels
    assert "Verified and Playable on Deck" not in labels
    assert all(
        window.collection_list.item(i).checkState() != Qt.CheckState.Checked
        for i in range(window.collection_list.count())
        if window.collection_list.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    window.close()


def test_collection_filter_hides_non_matching_names(qapp, tmp_path):
    from .test_collections import seed_cloud_storage

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
    seed_cloud_storage(account)
    window._installations = [(installation, [account])]
    window._fill_installations()
    window.collection_filter.setText("linux")
    visible = [
        window.collection_list.item(i).text()
        for i in range(window.collection_list.count())
        if not window.collection_list.item(i).isHidden()
    ]
    assert visible == ["Linux Apps"]
    window.collection_filter.clear()
    assert all(
        not window.collection_list.item(i).isHidden()
        for i in range(window.collection_list.count())
    )
    window.close()


def test_collection_new_name_is_in_the_assignment(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from .test_collections import seed_cloud_storage

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
    seed_cloud_storage(account)
    window._installations = [(installation, [account])]
    window._fill_installations()
    linux = next(
        window.collection_list.item(i)
        for i in range(window.collection_list.count())
        if window.collection_list.item(i).text() == "Linux Apps"
    )
    linux.setCheckState(Qt.CheckState.Checked)
    window.collection_new.setText("My Shelf")
    assignment = window._collection_assignment()
    assert assignment.existing_ids == ("uc-BBBB",)
    assert assignment.create_names == ("My Shelf",)
    window.close()


def test_typed_hidden_or_dynamic_name_is_blocked_before_import(qapp, tmp_path):
    from .test_collections import seed_cloud_storage

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
    seed_cloud_storage(account)
    window._installations = [(installation, [account])]
    window._fill_installations()
    window.collection_new.setText("action")
    assert window._blocked_create_collection_reason() is None
    window.collection_new.setText("Linux Apps")
    assert window._blocked_create_collection_reason() is None
    window.collection_new.setText("Hidden")
    reason = window._blocked_create_collection_reason()
    assert reason is not None
    assert "Hidden" in reason
    window.collection_new.setText("Verified and Playable on Deck")
    reason = window._blocked_create_collection_reason()
    assert reason is not None
    assert "Verified and Playable on Deck" in reason
    window.close()


def test_collection_checks_do_not_carry_between_accounts(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from .test_collections import seed_cloud_storage

    window = _window()
    root = tmp_path / "Steam"
    first_installation = SteamInstallation(
        kind="native",
        root=root / "native",
        userdata_root=root / "native" / "userdata",
        display_name="Native Steam",
    )
    second_installation = SteamInstallation(
        kind="flatpak",
        root=root / "flatpak",
        userdata_root=root / "flatpak" / "userdata",
        display_name="Flatpak Steam",
    )
    first_account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="first",
        persona_name="First",
        userdata_dir=first_installation.userdata_root / "11111111",
        selection_hints=[],
    )
    second_account = SteamAccount(
        steam_id64="76561197982487950",
        account_id32=22222222,
        account_name="second",
        persona_name="Second",
        userdata_dir=second_installation.userdata_root / "22222222",
        selection_hints=[],
    )
    seed_cloud_storage(first_account)
    seed_cloud_storage(second_account)
    window._selected_installation = first_installation
    window._selected_account = first_account
    window._reload_collections()
    linux = next(
        window.collection_list.item(i)
        for i in range(window.collection_list.count())
        if window.collection_list.item(i).text() == "Linux Apps"
    )
    linux.setCheckState(Qt.CheckState.Checked)
    assert window._checked_collection_ids() == ["uc-BBBB"]
    window._selected_installation = second_installation
    window._selected_account = second_account
    window._reload_collections()
    assert window._checked_collection_ids() == []
    window.close()


def test_settings_dialog_has_a_steamgriddb_key_field(qapp):
    from PySide6.QtWidgets import QLabel, QLineEdit

    dialog = SettingsDialog()
    body = "\n".join(widget.text() for widget in dialog.findChildren(QLabel))
    assert "SteamGridDB" in body
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.poll_enabled.isChecked() is True
    assert dialog.host_launch.isChecked() is True
    assert dialog.artwork_filters.static.isChecked() is True
    assert dialog.artwork_filters.nsfw.isChecked() is False
    assert dialog.artwork_filters.animated.isChecked() is False
    from PySide6.QtWidgets import QGroupBox

    titles = {box.title() for box in dialog.findChildren(QGroupBox)}
    assert titles == {"SteamGridDB", "Artwork filters", "Steam", "Flatpak Steam"}
    dialog.close()


def test_settings_persists_flatpak_steam_host_launch(qapp):
    store = StateStore(":memory:")
    dialog = SettingsDialog(store=store)
    dialog.host_launch.setChecked(False)
    assert store.flatpak_steam_host_launch() is False
    dialog.close()


def test_settings_persists_steamgriddb_artwork_filters(qapp):
    store = StateStore(":memory:")
    dialog = SettingsDialog(store=store)
    dialog.artwork_filters.nsfw.setChecked(True)
    dialog.artwork_filters.animated.setChecked(True)
    dialog.artwork_filters.humor.setChecked(True)
    loaded = store.artwork_filters()
    assert loaded.allow_nsfw is True
    assert loaded.allow_humor is True
    assert loaded.include_animated is True
    dialog.close()


def test_settings_warns_when_steam_detection_is_disabled(qapp, monkeypatch):
    from steam_desktop_importer.ui.settings_dialog import DISABLE_STEAM_CHECK_WARNING

    warnings: list[str] = []

    def capture(parent, title, text, *args, **kwargs):
        warnings.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "warning", capture)
    store = StateStore(":memory:")
    dialog = SettingsDialog(store=store)
    dialog.poll_enabled.setChecked(False)
    assert store.steam_poll_enabled() is False
    assert DISABLE_STEAM_CHECK_WARNING in warnings
    dialog.poll_seconds.setValue(5)
    dialog.poll_enabled.setChecked(True)
    assert store.steam_poll_enabled() is True
    assert store.steam_poll_ms() == 5000
    dialog.close()


def test_settings_cancel_keeps_steam_detection_enabled(qapp, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Cancel
    )
    store = StateStore(":memory:")
    dialog = SettingsDialog(store=store)
    dialog.poll_enabled.setChecked(False)
    assert dialog.poll_enabled.isChecked() is True
    assert store.steam_poll_enabled() is True
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


def test_relink_confirmation_mentions_collection_assignment(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import Qt

    window = _window()
    app = _gui_app(tmp_path / "org.example.App.desktop")
    installation = SteamInstallation(
        kind="native",
        root=tmp_path / "Steam",
        userdata_root=tmp_path / "Steam" / "userdata",
        display_name="Native Steam",
    )
    account = SteamAccount(
        steam_id64="76561197971376839",
        account_id32=11111111,
        account_name="single_user",
        persona_name="Single",
        userdata_dir=installation.userdata_root / "11111111",
        selection_hints=[],
    )
    window._selected_installation = installation
    window._selected_account = account
    collection = QListWidgetItem("Linux Apps")
    collection.setData(Qt.ItemDataRole.UserRole, "uc-BBBB")
    collection.setFlags(
        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
    )
    collection.setCheckState(Qt.CheckState.Checked)
    window.collection_list.addItem(collection)
    window.collection_new.setText("My Shelf")
    monkeypatch.setattr(window, "_sync_running_status", lambda: None)
    monkeypatch.setattr(window, "_write_ready_reason", lambda: None)
    monkeypatch.setattr(window, "_selected_relink_rows", lambda: [SimpleNamespace(app=app)])
    monkeypatch.setattr(
        window,
        "_existing_shortcuts",
        lambda: [
            SimpleNamespace(
                appid_unsigned=0x8000ABCD,
                name="Example",
                exe='"/usr/bin/example"',
                launch_options="",
            )
        ],
    )
    captured: dict[str, str] = {}

    def capture(_title: str, text: str) -> bool:
        captured["text"] = text
        return False

    monkeypatch.setattr(window, "_confirm_write", capture)
    window._relink_selected()
    assert "Also add to collection(s): Linux Apps, new \"My Shelf\"" in captured["text"]
    window.close()


def test_prepare_artwork_skips_without_a_key(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("SGDB_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = _window()
    app = _gui_app(tmp_path / "org.example.App.desktop")
    assert window._prepare_import_artwork([app], tmp_path) == {}
    window.close()


def test_auto_refresh_starts_a_steam_status_poller(qapp, monkeypatch):
    from steam_desktop_importer.ui.main_window import STEAM_POLL_MS

    monkeypatch.setattr(MainWindow, "refresh", lambda self: None)
    window = MainWindow(
        auto_refresh=True,
        state_store=StateStore(":memory:"),
        detect_steam=lambda: CLOSED,
    )
    assert window._steam_poll.isActive() is True
    assert window._steam_poll.interval() == STEAM_POLL_MS
    window.close()
    assert window._steam_poll.isActive() is False


def test_stored_poll_interval_is_applied_and_can_be_disabled(qapp, monkeypatch):
    monkeypatch.setattr(MainWindow, "refresh", lambda self: None)
    store = StateStore(":memory:")
    store.set_steam_poll(enabled=True, interval_ms=5000)
    window = MainWindow(
        auto_refresh=True,
        state_store=store,
        detect_steam=lambda: CLOSED,
    )
    assert window._steam_poll.isActive() is True
    assert window._steam_poll.interval() == 5000
    store.set_steam_poll(enabled=False, interval_ms=5000)
    window._apply_steam_poll_settings()
    assert window._steam_poll.isActive() is False
    window.close()


def test_applying_running_status_disables_import_without_refresh(qapp, tmp_path):
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
    window._refresh_import_statuses()
    window._model.set_all_selected(True)
    window._running = CLOSED
    window._update_import_actions()
    assert window.import_button.isEnabled() is True
    window._apply_running_status(
        SteamRunningStatus(
            running=True, evidence=("process name 'steam'",), inspection_failures=0
        )
    )
    assert window.import_button.isEnabled() is False
    assert "Close Steam" in window.import_button.toolTip()
    assert "running" in window.steam_status.text()
    window.close()


def test_import_rechecks_steam_before_writing(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    current = {"status": CLOSED}

    window = MainWindow(
        auto_refresh=False,
        state_store=StateStore(":memory:"),
        detect_steam=lambda: current["status"],
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
    current["status"] = SteamRunningStatus(
        running=True, evidence=("process name 'steam'",), inspection_failures=0
    )
    window._import_selected()
    assert not shortcuts_vdf_path(account).exists()
    assert window.import_button.isEnabled() is False
    window.close()


def test_overriding_steam_detection_allows_import_when_probe_says_running(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    store = StateStore(":memory:")
    store.set_steam_poll(enabled=False, interval_ms=2000)
    running = SteamRunningStatus(
        running=True, evidence=("process name 'steam'",), inspection_failures=0
    )
    window = MainWindow(
        auto_refresh=False,
        state_store=store,
        detect_steam=lambda: running,
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
    window._running = running
    window._update_banner()
    window._set_steam_status(running)
    assert window.import_button.isEnabled() is True
    assert "overridden" in window.banner.text()
    assert "detection overridden" in window.steam_status.text()
    monkeypatch.setattr(window, "_confirm_write", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(window, "_prepare_import_artwork", lambda *_args, **_kwargs: {})
    window._import_selected()
    vdf = shortcuts_vdf_path(account)
    assert ShortcutDocument.load(vdf).find_by_appid(first_import_candidate(app.desktop_id))
    window.close()


def test_flatpak_install_selection_starts_async_permission_probe(qapp):
    store = StateStore(":memory:")
    window = MainWindow(auto_refresh=False, state_store=store)
    calls: list[str] = []
    installation = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam"),
        userdata_root=Path("/tmp/steam/userdata"),
        display_name="Flatpak Steam",
    )
    window._installations = [(installation, [])]
    window._probe_host_launch_permission = lambda: calls.append("probe")
    window.install_combo.addItem(installation.display_name, installation.key)
    window.install_combo.setCurrentIndex(0)
    assert calls == ["probe"]
    assert "Checking Flatpak host-launch permission" in window.banner.text()
    window.close()


def test_switching_installations_clears_cached_flatpak_permission(qapp):
    from steam_desktop_importer.launch import HostLaunchPermission

    store = StateStore(":memory:")
    window = MainWindow(auto_refresh=False, state_store=store)
    first = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-a"),
        userdata_root=Path("/tmp/steam-a/userdata"),
        display_name="Flatpak Steam A",
    )
    second = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-b"),
        userdata_root=Path("/tmp/steam-b/userdata"),
        display_name="Flatpak Steam B",
    )
    window._installations = [(second, [])]
    window._selected_installation = first
    window._host_launch_permission = HostLaunchPermission(True, "cached")
    window._probe_host_launch_permission = lambda: None
    window.install_combo.addItem(second.display_name, second.key)
    window.install_combo.setCurrentIndex(0)
    window._on_install_chosen()
    assert window._host_launch_permission is None
    window.close()


def test_outdated_flatpak_probe_result_is_ignored(qapp):
    from steam_desktop_importer.launch import HostLaunchPermission

    store = StateStore(":memory:")
    window = MainWindow(auto_refresh=False, state_store=store)
    first = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-a"),
        userdata_root=Path("/tmp/steam-a/userdata"),
        display_name="Flatpak Steam A",
    )
    second = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-b"),
        userdata_root=Path("/tmp/steam-b/userdata"),
        display_name="Flatpak Steam B",
    )
    calls: list[str] = []
    window._selected_installation = second
    window._host_launch_probe_key = first.key
    window._host_launch_probe_busy = True
    window._probe_host_launch_permission = lambda: calls.append("probe")
    window._on_host_launch_permission(HostLaunchPermission(True, "stale"))
    assert window._host_launch_permission is None
    assert calls == ["probe"]
    window.close()


def test_outdated_flatpak_probe_failure_is_ignored(qapp):
    store = StateStore(":memory:")
    window = MainWindow(auto_refresh=False, state_store=store)
    first = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-a"),
        userdata_root=Path("/tmp/steam-a/userdata"),
        display_name="Flatpak Steam A",
    )
    second = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-b"),
        userdata_root=Path("/tmp/steam-b/userdata"),
        display_name="Flatpak Steam B",
    )
    calls: list[str] = []
    window._selected_installation = second
    window._host_launch_probe_key = first.key
    window._host_launch_probe_busy = True
    window._probe_host_launch_permission = lambda: calls.append("probe")
    window._on_host_launch_permission_failed("boom")
    assert window._host_launch_permission is None
    assert calls == ["probe"]
    window.close()


def test_stale_flatpak_probe_generation_is_ignored(qapp):
    from steam_desktop_importer.launch import HostLaunchPermission

    store = StateStore(":memory:")
    window = MainWindow(auto_refresh=False, state_store=store)
    installation = SteamInstallation(
        kind="flatpak",
        root=Path("/tmp/steam-a"),
        userdata_root=Path("/tmp/steam-a/userdata"),
        display_name="Flatpak Steam A",
    )
    calls: list[str] = []
    window._selected_installation = installation
    window._host_launch_probe_key = installation.key
    window._host_launch_probe_busy = True
    window._host_launch_probe_started_generation = 0
    window._host_launch_probe_generation = 1
    window._probe_host_launch_permission = lambda: calls.append("probe")
    window._on_host_launch_permission(HostLaunchPermission(True, "stale"))
    assert window._host_launch_permission is None
    assert calls == ["probe"]
    window.close()
