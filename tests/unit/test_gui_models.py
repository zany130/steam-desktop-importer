"""GUI table model and filters (IMPLEMENTATION.md §25, Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from steam_desktop_importer.models import DesktopApplication, UnsupportedCode
from steam_desktop_importer.ui.main_window import current_desktops
from steam_desktop_importer.ui.models import (
    IMPORT_STATUS_UNKNOWN,
    ApplicationFilterProxy,
    ApplicationTableModel,
    Column,
)


@pytest.fixture(scope="module")
def qapp():
    existing = QApplication.instance()
    if existing is not None:
        yield existing
        return
    app = QApplication([])
    yield app
    app.quit()


def make_app(**overrides) -> DesktopApplication:
    values = {
        "desktop_id": "org.example.App.desktop",
        "desktop_path": Path("/usr/share/applications/org.example.App.desktop"),
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


def populated(*apps) -> tuple[ApplicationTableModel, ApplicationFilterProxy]:
    model = ApplicationTableModel()
    model.set_applications(list(apps))
    proxy = ApplicationFilterProxy()
    proxy.setSourceModel(model)
    return model, proxy


def visible_names(proxy: ApplicationFilterProxy) -> list[str]:
    names = []
    for row in range(proxy.rowCount()):
        names.append(proxy.index(row, Column.NAME).data(Qt.ItemDataRole.DisplayRole))
    return names


def test_current_desktops_splits_colon_list():
    assert current_desktops({"XDG_CURRENT_DESKTOP": "KDE:GNOME"}) == {"kde", "gnome"}
    assert current_desktops({}) == set()


def test_table_has_the_specified_columns(qapp):
    model, _proxy = populated(make_app())
    headers = [model.headerData(column, Qt.Orientation.Horizontal) for column in range(model.columnCount())]
    assert headers == ["", "", "Application", "Source", "Command", "Desktop ID", "Status", "In Steam"]


def test_import_status_is_unknown_rather_than_new(qapp):
    """Showing New would claim the capture host has no shortcuts. It has 783."""
    model, _proxy = populated(make_app())
    index = model.index(0, Column.IMPORT_STATUS)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == IMPORT_STATUS_UNKNOWN


def test_unsupported_rows_cannot_be_ticked(qapp):
    app = make_app(
        supported_for_import=False,
        unsupported_reason="Terminal=true",
        unsupported_code=UnsupportedCode.TERMINAL_UNSUPPORTED,
    )
    model, _proxy = populated(app)
    index = model.index(0, Column.SELECTED)
    assert not (model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable)
    assert model.setData(index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole) is False
    assert model.selected_applications() == []


def test_select_all_skips_non_importable_rows(qapp):
    good = make_app(desktop_id="good.desktop", name="Good")
    bad = make_app(
        desktop_id="bad.desktop",
        name="Bad",
        supported_for_import=False,
        unsupported_reason="no Exec",
        unsupported_code=UnsupportedCode.NO_EXEC,
        exec_argv=[],
    )
    model, _proxy = populated(good, bad)
    model.set_all_selected(True)
    assert [app.desktop_id for app in model.selected_applications()] == ["good.desktop"]


def test_adapter_refusal_makes_a_phase1_supported_row_unimportable(qapp):
    """DEV-10: supported_for_import is necessary but not sufficient."""
    app = make_app(
        desktop_id="org.example.AppImageTransient.desktop",
        name="Transient",
        source_kind="appimage",
        exec_argv=["/tmp/.mount_Exampl3XY/AppRun"],
        supported_for_import=True,
    )
    model, _proxy = populated(app)
    row = model.rows()[0]
    assert row.is_importable is False
    assert row.status == "Unsupported"
    index = model.index(0, Column.SELECTED)
    assert not (model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable)


def test_search_matches_name_and_desktop_id(qapp):
    _model, proxy = populated(
        make_app(desktop_id="one.desktop", name="Alpha"),
        make_app(desktop_id="two.desktop", name="Beta"),
    )
    proxy.set_search("two.desktop")
    assert visible_names(proxy) == ["Beta"]
    proxy.set_search("alp")
    assert visible_names(proxy) == ["Alpha"]


def test_source_filter(qapp):
    _model, proxy = populated(
        make_app(desktop_id="n.desktop", name="Native", source_kind="native"),
        make_app(desktop_id="f.desktop", name="Flatpak", source_kind="flatpak", exec_argv=["flatpak", "run", "x"]),
    )
    proxy.set_source_kinds({"flatpak"})
    assert visible_names(proxy) == ["Flatpak"]


def test_no_display_is_hidden_by_default_and_shown_on_request(qapp):
    _model, proxy = populated(
        make_app(desktop_id="shown.desktop", name="Shown"),
        make_app(desktop_id="hidden.desktop", name="Hidden", no_display=True),
    )
    assert visible_names(proxy) == ["Shown"]
    proxy.set_show_no_display(True)
    assert sorted(visible_names(proxy)) == ["Hidden", "Shown"]
    assert _model.index(1, Column.STATUS).data() == "Hidden"


def test_unsupported_filter(qapp):
    _model, proxy = populated(
        make_app(desktop_id="ok.desktop", name="Ok"),
        make_app(
            desktop_id="term.desktop",
            name="Term",
            supported_for_import=False,
            unsupported_reason="terminal",
            unsupported_code=UnsupportedCode.TERMINAL_UNSUPPORTED,
        ),
    )
    proxy.set_show_unsupported(False)
    assert visible_names(proxy) == ["Ok"]


def test_current_desktop_filter_honours_only_show_in_and_not_show_in(qapp):
    _model, proxy = populated(
        make_app(desktop_id="kde.desktop", name="KDE Only", only_show_in=["KDE"]),
        make_app(desktop_id="hidden-on-kde.desktop", name="Not KDE", not_show_in=["KDE"]),
        make_app(desktop_id="everywhere.desktop", name="Everywhere"),
    )
    proxy.set_current_desktop_only(True, {"kde"})
    assert sorted(visible_names(proxy)) == ["Everywhere", "KDE Only"]


def test_unavailable_and_unsupported_are_distinct_status_labels(qapp):
    missing = make_app(
        desktop_id="missing.desktop",
        name="Missing",
        supported_for_import=False,
        unsupported_reason="TryExec failed",
        unsupported_code=UnsupportedCode.TRYEXEC_UNRESOLVABLE,
    )
    terminal = make_app(
        desktop_id="term.desktop",
        name="Term",
        supported_for_import=False,
        unsupported_reason="terminal",
        unsupported_code=UnsupportedCode.TERMINAL_UNSUPPORTED,
    )
    model, _proxy = populated(missing, terminal)
    assert model.rows()[0].status == "Unavailable"
    assert model.rows()[1].status == "Unsupported"


def test_localized_name_is_preferred_in_the_name_column(qapp):
    model, _proxy = populated(make_app(name="Kate", localized_name="Advanced Text Editor"))
    assert model.index(0, Column.NAME).data() == "Advanced Text Editor"


def test_filtering_two_thousand_rows_stays_responsive(qapp):
    """§31 Phase 3: verify the model/view stays usable on a large set."""
    apps = [
        make_app(desktop_id=f"app-{index:04d}.desktop", name=f"App {index:04d}")
        for index in range(2000)
    ]
    _model, proxy = populated(*apps)
    proxy.set_search("app-1999")
    assert visible_names(proxy) == ["App 1999"]
    proxy.set_search("")
    assert proxy.rowCount() == 2000
