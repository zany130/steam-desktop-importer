"""Table model and filter proxy for the application list.

IMPLEMENTATION.md §25.1 and §25.3. A real ``QAbstractTableModel`` behind a
``QSortFilterProxyModel``, as §25.3 prefers, rather than a ``QTableWidget``:
the capture host resolves 804 entries, and per-cell widgets at that size make
filtering and sorting noticeably slow.

Two things are deliberately *not* faked here:

* **Import status.** §25.1's ``New``/``Imported``/``Changed``/``Possible
  Existing Match`` all require the Phase 5 state store and the Phase 6 VDF
  reader. Until those exist the column reports ``unknown``. Showing ``New``
  would actively mislead: the capture host already has 783 shortcuts, so
  "nothing is imported yet" is a false statement, not a harmless default.
* **Icons.** Resolved lazily and cached, because theme lookup for 800 entries
  costs far more than drawing them.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QIcon

from ..desktop.icons import resolve_icon
from ..launch import LaunchAdapterError, build_launch_vector
from ..models import DesktopApplication

__all__ = [
    "COLUMNS",
    "IMPORT_STATUS_UNKNOWN",
    "ApplicationFilterProxy",
    "ApplicationTableModel",
    "Column",
    "Row",
]

IMPORT_STATUS_UNKNOWN = "unknown"
"""Placeholder until Phases 5 and 6 can compute a real §25.1 import status."""


class Column:
    SELECTED = 0
    ICON = 1
    NAME = 2
    SOURCE = 3
    COMMAND = 4
    DESKTOP_ID = 5
    STATUS = 6
    IMPORT_STATUS = 7


COLUMNS = (
    (Column.SELECTED, ""),
    (Column.ICON, ""),
    (Column.NAME, "Application"),
    (Column.SOURCE, "Source"),
    (Column.COMMAND, "Command"),
    (Column.DESKTOP_ID, "Desktop ID"),
    (Column.STATUS, "Status"),
    (Column.IMPORT_STATUS, "In Steam"),
)


@dataclass
class Row:
    """One application plus the view state that belongs to the table."""

    app: DesktopApplication
    selected: bool = False

    _command: str | None = None
    _icon: QIcon | None = None
    _icon_resolved: bool = False
    _importable: bool | None = None
    _adapter_error: str | None = None

    @property
    def command(self) -> str:
        """A one-line summary of what this entry would actually run.

        Built from the Phase 2 launch vector rather than from ``exec_argv``, so
        the column shows the command that would really be used — Flatpak
        forwarding markers already stripped, ``env`` wrapper still in place.
        """
        if self._command is None:
            try:
                vector = build_launch_vector(self.app)
            except LaunchAdapterError:
                self._command = " ".join(self.app.exec_argv)
            else:
                self._command = " ".join(vector.argv)
        return self._command

    @property
    def is_importable(self) -> bool:
        """Whether this row may be queued.

        ``supported_for_import`` is necessary but not sufficient (DEV-10): the
        launch adapter can still refuse an entry Phase 1 accepted.
        """
        if not self.app.supported_for_import:
            return False
        if self._importable is None:
            try:
                build_launch_vector(self.app)
            except LaunchAdapterError as error:
                self._importable = False
                self._adapter_error = str(error)
            else:
                self._importable = True
        return self._importable

    def icon(self) -> QIcon | None:
        if not self._icon_resolved:
            self._icon_resolved = True
            path = self.app.icon_source_path or resolve_icon(self.app.icon_name, size=32)
            if path is not None:
                icon = QIcon(str(path))
                self._icon = None if icon.isNull() else icon
        return self._icon

    @property
    def status(self) -> str:
        """§25.1 visibility/support status."""
        if self.is_importable:
            return "Hidden" if self.app.no_display else "Available"
        if not self.app.is_available:
            return "Unavailable"
        return "Unsupported"


class ApplicationTableModel(QAbstractTableModel):
    """Rows of :class:`~steam_desktop_importer.models.DesktopApplication`."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[Row] = []

    # -- population -----------------------------------------------------

    def set_applications(self, applications: list[DesktopApplication]) -> None:
        """Replace every row. Selection is intentionally not carried over.

        A refresh can change which entries exist and which are importable, so
        silently preserving ticks could leave an entry selected that is no
        longer supported.
        """
        self.beginResetModel()
        self._rows = [Row(app) for app in applications]
        self.endResetModel()

    def rows(self) -> list[Row]:
        return self._rows

    def row_at(self, source_row: int) -> Row:
        return self._rows[source_row]

    def selected_applications(self) -> list[DesktopApplication]:
        return [row.app for row in self._rows if row.selected]

    def set_all_selected(self, selected: bool, indexes: list[int] | None = None) -> None:
        """Tick or untick rows, skipping anything not importable."""
        targets = range(len(self._rows)) if indexes is None else indexes
        changed = False
        for position in targets:
            row = self._rows[position]
            if not row.is_importable:
                continue
            if row.selected != selected:
                row.selected = selected
                changed = True
        if changed and self._rows:
            top = self.index(0, Column.SELECTED)
            bottom = self.index(len(self._rows) - 1, Column.SELECTED)
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.CheckStateRole])

    # -- QAbstractTableModel -------------------------------------------

    def rowCount(self, parent=None) -> int:
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=None) -> int:
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return COLUMNS[section][1]

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == Column.SELECTED:
            row = self._rows[index.row()]
            if row.is_importable:
                return base | Qt.ItemFlag.ItemIsUserCheckable
            # Unsupported entries stay visible but cannot be ticked, so the
            # table cannot be used to queue something the importer refuses.
            return Qt.ItemFlag.ItemIsSelectable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and column == Column.SELECTED:
            return Qt.CheckState.Checked if row.selected else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DecorationRole and column == Column.ICON:
            return row.icon()

        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row)

        if role == Qt.ItemDataRole.DisplayRole:
            if column == Column.NAME:
                return row.app.localized_name or row.app.name
            if column == Column.SOURCE:
                return row.app.source_kind
            if column == Column.COMMAND:
                return row.command
            if column == Column.DESKTOP_ID:
                return row.app.desktop_id
            if column == Column.STATUS:
                return row.status
            if column == Column.IMPORT_STATUS:
                return IMPORT_STATUS_UNKNOWN

        if role == Qt.ItemDataRole.UserRole:
            if column == Column.SOURCE:
                return row.app.source_kind
            if column == Column.STATUS:
                return row.status

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.CheckStateRole or index.column() != Column.SELECTED:
            return False
        row = self._rows[index.row()]
        if not row.is_importable:
            return False
        row.selected = Qt.CheckState(value) == Qt.CheckState.Checked
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    # -- helpers --------------------------------------------------------

    def _tooltip(self, row: Row) -> str:
        parts = [
            f"{row.app.desktop_id}",
            f"path: {row.app.desktop_path}",
            f"command: {row.command}",
        ]
        if row.app.working_directory:
            parts.append(f"working directory: {row.app.working_directory}")
        if row.app.unsupported_reason:
            parts.append(f"not importable: {row.app.unsupported_reason}")
        if row._adapter_error:
            parts.append(f"launch adapter refused: {row._adapter_error}")
        if row.app.has_collision:
            others = ", ".join(str(path) for path in row.app.collision_paths)
            parts.append(f"desktop ID collision between: {others}")
        if row.app.nonstandard_exec:
            parts.append("Exec needed the compatibility tokenizer")
        if row.status == "Hidden":
            parts.append("NoDisplay=true; hidden from menus, not deleted")
        return "\n".join(parts)


class ApplicationFilterProxy(QSortFilterProxyModel):
    """§25.2 filters, applied over the table model."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._search = ""
        self._source_kinds: set[str] | None = None
        self._show_no_display = False
        self._show_unsupported = True
        self._current_desktop_only = False
        self._current_desktops: set[str] = set()
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def _refresh_filter(self) -> None:
        self.invalidate()

    # -- filter inputs --------------------------------------------------

    def set_search(self, text: str) -> None:
        self._search = text.strip().lower()
        self._refresh_filter()

    def set_source_kinds(self, kinds: set[str] | None) -> None:
        self._source_kinds = kinds
        self._refresh_filter()

    def set_show_no_display(self, show: bool) -> None:
        self._show_no_display = show
        self._refresh_filter()

    def set_show_unsupported(self, show: bool) -> None:
        self._show_unsupported = show
        self._refresh_filter()

    def set_current_desktop_only(self, only: bool, desktops: set[str] | None = None) -> None:
        self._current_desktop_only = only
        if desktops is not None:
            self._current_desktops = {item.lower() for item in desktops}
        self._refresh_filter()

    # -- filtering ------------------------------------------------------

    def filterAcceptsRow(self, source_row, source_parent) -> bool:
        model = self.sourceModel()
        if not isinstance(model, ApplicationTableModel):
            return True
        row = model.rows()[source_row]
        app = row.app

        if not self._show_no_display and app.no_display:
            return False
        if not self._show_unsupported and not row.is_importable:
            return False
        if self._current_desktop_only and not self._visible_in_current_desktop(app):
            return False
        if self._source_kinds is not None and app.source_kind not in self._source_kinds:
            return False

        if self._search:
            haystack = " ".join(
                (
                    app.name or "",
                    app.localized_name or "",
                    app.desktop_id,
                    app.source_kind,
                    row.command,
                    row.status,
                )
            ).lower()
            if self._search not in haystack:
                return False

        return True

    def _visible_in_current_desktop(self, app: DesktopApplication) -> bool:
        """Apply ``OnlyShowIn``/``NotShowIn`` against ``$XDG_CURRENT_DESKTOP``.

        §8 says to preserve this metadata rather than delete entries for it,
        which is why it is a filter here and never a discovery-time drop.
        """
        if not self._current_desktops:
            return True
        if app.not_show_in and self._current_desktops & {
            item.lower() for item in app.not_show_in
        }:
            return False
        if app.only_show_in:
            return bool(self._current_desktops & {item.lower() for item in app.only_show_in})
        return True
