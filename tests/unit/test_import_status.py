"""Import status classification (IMPLEMENTATION.md §25.1, Phase 5)."""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.models import DesktopApplication
from steam_desktop_importer.state import (
    STATUS_CHANGED,
    STATUS_IMPORTED,
    STATUS_NEW,
    STATUS_POSSIBLE_MATCH,
    STATUS_UNSCOPED,
    StateStore,
    classify_applications,
    import_status,
)
from steam_desktop_importer.steam.shortcut_identities import ExistingShortcut


def make_app(**overrides) -> DesktopApplication:
    values = {
        "desktop_id": "org.example.App.desktop",
        "desktop_path": Path("/usr/share/applications/org.example.App.desktop"),
        "name": "Example",
        "localized_name": None,
        "raw_exec": "/usr/bin/example %U",
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


def test_unmanaged_without_a_vdf_match_is_new():
    assert import_status(make_app(), None) == STATUS_NEW


def test_managed_and_unchanged_is_imported():
    with StateStore(":memory:") as store:
        mapping = store.save_mapping(
            "native:/a",
            1,
            "org.example.App.desktop",
            0x80000001,
            "Example",
            "/usr/bin/example %U",
            None,
        )
        assert import_status(make_app(), mapping) == STATUS_IMPORTED


def test_name_change_is_changed_and_keeps_the_appid():
    app = make_app(name="Renamed")
    with StateStore(":memory:") as store:
        mapping = store.save_mapping(
            "native:/a",
            1,
            app.desktop_id,
            0x80000001,
            "Example",
            app.raw_exec,
            None,
        )
        assert import_status(app, mapping) == STATUS_CHANGED
        updated = store.save_mapping(
            "native:/a",
            1,
            app.desktop_id,
            0,
            "Renamed",
            app.raw_exec,
            None,
        )
        assert updated.steam_appid_unsigned == 0x80000001
        assert import_status(app, updated) == STATUS_IMPORTED


def test_exec_change_is_changed():
    app = make_app(raw_exec="/usr/bin/example --new")
    with StateStore(":memory:") as store:
        mapping = store.save_mapping(
            "native:/a",
            1,
            app.desktop_id,
            0x80000001,
            "Example",
            "/usr/bin/example %U",
            None,
        )
        assert import_status(app, mapping) == STATUS_CHANGED


def test_possible_existing_match_does_not_create_a_mapping():
    """§17: name+exe is a heuristic, never automatic ownership."""
    existing = [
        ExistingShortcut(
            appid_unsigned=0x81234567,
            name="Example",
            exe='"/usr/bin/example"',
            launch_options="",
        )
    ]
    app = make_app()
    assert import_status(app, None, existing) == STATUS_POSSIBLE_MATCH
    with StateStore(":memory:") as store:
        assert store.get_mapping("native:/a", 1, app.desktop_id) is None


def test_name_alone_is_not_a_possible_match():
    existing = [
        ExistingShortcut(
            appid_unsigned=1,
            name="Example",
            exe="/usr/bin/other",
            launch_options="",
        )
    ]
    assert import_status(make_app(), None, existing) == STATUS_NEW


def test_unscoped_classification_is_not_new():
    app = make_app()
    with StateStore(":memory:") as store:
        assert classify_applications([app], store, None, None) == {
            app.desktop_id: STATUS_UNSCOPED
        }
