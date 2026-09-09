"""Main window (IMPLEMENTATION.md §25, Phase 3).

Read-only. The Import button is visible and disabled on purpose: selection
and status have to be exercisable, but nothing here writes to Steam.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QThreadPool
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..desktop.discovery import DiscoveryResult
from ..models import SOURCE_KINDS, SteamAccount, SteamInstallation, UnsupportedCode
from ..steam import select_account, select_installation
from ..steam.running import SteamRunningStatus
from .account_dialog import AccountDialog
from .delegates import source_badge_delegate, status_badge_delegate
from .models import ApplicationFilterProxy, ApplicationTableModel, Column
from .settings_dialog import SettingsDialog
from .workers import (
    ScanWorker,
    SteamProbeWorker,
    account_label,
    installation_label,
)

__all__ = ["MainWindow", "current_desktops", "run_app"]

_SOURCE_ALL = "all sources"
_PLACEHOLDER_INSTALL = "Select a Steam installation…"
_PLACEHOLDER_ACCOUNT = "Select a Steam account…"
_IMPORT_DISABLED = (
    "Importing is not implemented yet. Safe writes to shortcuts.vdf are Phase 7."
)


def current_desktops(environ: dict[str, str] | None = None) -> set[str]:
    """``$XDG_CURRENT_DESKTOP`` split on ``:``, as the specification defines it."""
    env = os.environ if environ is None else environ
    raw = env.get("XDG_CURRENT_DESKTOP", "")
    return {part.strip().lower() for part in raw.split(":") if part.strip()}


class MainWindow(QMainWindow):
    def __init__(self, parent=None, *, auto_refresh: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Steam Desktop Importer")
        self.resize(1280, 800)

        self._pool = QThreadPool.globalInstance()
        self._acknowledged: set[str] = set()
        self._installations: list[tuple[SteamInstallation, list[SteamAccount]]] = []
        self._selected_installation: SteamInstallation | None = None
        self._selected_account: SteamAccount | None = None
        self._running: SteamRunningStatus | None = None
        self._scan_busy = False
        self._steam_busy = False
        self._account_prompted = False

        self._model = ApplicationTableModel(self)
        self._proxy = ApplicationFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(Qt.ItemDataRole.DisplayRole)

        self._build_toolbar()
        self._build_body()
        self._build_status_bar()
        self._wire()
        self._apply_default_filters()

        scan = QAction("Refresh", self)
        scan.setShortcut(QKeySequence.StandardKey.Refresh)
        scan.triggered.connect(self.refresh)
        self.addAction(scan)

        if auto_refresh:
            self.refresh()

    # -- construction ---------------------------------------------------

    def _build_toolbar(self) -> None:
        top = QWidget()
        layout = QVBoxLayout(top)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(4)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, desktop ID, command…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        search_row.addWidget(self.search, stretch=1)

        self.source_filter = QComboBox()
        self.source_filter.addItem(_SOURCE_ALL, None)
        for kind in SOURCE_KINDS:
            if kind != "unknown":
                self.source_filter.addItem(kind, kind)
        search_row.addWidget(self.source_filter)

        self.refresh_button = QPushButton("Refresh")
        search_row.addWidget(self.refresh_button)
        self.settings_button = QPushButton("Settings")
        search_row.addWidget(self.settings_button)
        layout.addLayout(search_row)

        steam_row = QHBoxLayout()
        steam_row.addWidget(QLabel("Steam:"))
        self.install_combo = QComboBox()
        self.install_combo.setMinimumContentsLength(28)
        self.install_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.install_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.install_combo.setToolTip("Steam installation. Several are never chosen silently.")
        steam_row.addWidget(self.install_combo, stretch=3)

        self.account_combo = QComboBox()
        self.account_combo.setMinimumContentsLength(16)
        self.account_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.account_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.account_combo.setToolTip("Steam account. Several are never chosen silently.")
        steam_row.addWidget(self.account_combo, stretch=2)

        self.steam_status = QLabel("Steam: checking…")
        steam_row.addWidget(self.steam_status)
        layout.addLayout(steam_row)

        self._toolbar = top

    def _build_body(self) -> None:
        filters = QWidget()
        filter_row = QHBoxLayout(filters)
        filter_row.setContentsMargins(8, 0, 8, 0)

        self.show_no_display = QCheckBox("Show NoDisplay")
        self.show_unsupported = QCheckBox("Show unsupported")
        self.current_desktop_only = QCheckBox("Current desktop only")
        self.imported_filter = QComboBox()
        self.imported_filter.addItems(["Imported status: unavailable until Phase 5"])
        self.imported_filter.setEnabled(False)
        self.imported_filter.setToolTip(
            "New / Imported / Changed / Possible Existing Match need the "
            "Phase 5 state store and the Phase 6 VDF reader. The column shows "
            "'unknown' until then, so this filter would be a lie."
        )
        filter_row.addWidget(self.show_no_display)
        filter_row.addWidget(self.show_unsupported)
        filter_row.addWidget(self.current_desktop_only)
        filter_row.addWidget(self.imported_filter)
        filter_row.addStretch()

        self.select_visible = QPushButton("Select visible")
        self.clear_selection = QPushButton("Clear selection")
        filter_row.addWidget(self.select_visible)
        filter_row.addWidget(self.clear_selection)

        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(24, 24))
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(Column.SELECTED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(Column.ICON, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(Column.COMMAND, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.DESKTOP_ID, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(Column.STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(Column.IMPORT_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(Column.NAME, 260)
        self.table.setColumnWidth(Column.COMMAND, 280)
        self.table.setColumnWidth(Column.DESKTOP_ID, 220)
        self.table.setItemDelegateForColumn(Column.SOURCE, source_badge_delegate(self.table))
        self.table.setItemDelegateForColumn(Column.STATUS, status_badge_delegate(self.table))
        self.table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select an application to see details.")

        self.acknowledge = QPushButton("Acknowledge desktop-ID collision")
        self.acknowledge.setEnabled(False)
        self.acknowledge.setToolTip(
            "Lift the import block for this colliding desktop ID for the rest "
            "of the session. Persistent acknowledgement is Phase 5."
        )

        self.import_button = QPushButton("Import selected")
        self.import_button.setEnabled(False)
        self.import_button.setToolTip(_IMPORT_DISABLED)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(8, 0, 8, 8)
        bottom_layout.addWidget(self.detail)
        actions = QHBoxLayout()
        actions.addWidget(self.acknowledge)
        actions.addStretch()
        self.selection_label = QLabel("0 selected")
        actions.addWidget(self.selection_label)
        actions.addWidget(self.import_button)
        bottom_layout.addLayout(actions)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toolbar)
        layout.addWidget(filters)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        self.banner.setStyleSheet(
            "QLabel { background: #fff3cd; color: #664d03; padding: 8px; border: 1px solid #ffecb5; }"
        )
        layout.addWidget(self.banner)
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(central)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self._counts = QLabel("Scanning…")
        bar.addWidget(self._counts, stretch=1)
        self.setStatusBar(bar)

    def _wire(self) -> None:
        self.search.textChanged.connect(self._proxy.set_search)
        self.source_filter.currentIndexChanged.connect(self._on_source_filter)
        self.show_no_display.toggled.connect(self._proxy.set_show_no_display)
        self.show_unsupported.toggled.connect(self._proxy.set_show_unsupported)
        self.current_desktop_only.toggled.connect(self._on_current_desktop)
        self.refresh_button.clicked.connect(self.refresh)
        self.settings_button.clicked.connect(self._open_settings)
        self.select_visible.clicked.connect(self._select_visible)
        self.clear_selection.clicked.connect(self._clear_selection)
        self.acknowledge.clicked.connect(self._acknowledge_collision)
        self.install_combo.currentIndexChanged.connect(self._on_install_chosen)
        self.account_combo.currentIndexChanged.connect(self._on_account_chosen)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._model.dataChanged.connect(self._update_selection_count)
        self._proxy.modelReset.connect(self._update_counts)
        self._proxy.layoutChanged.connect(self._update_counts)

    def _apply_default_filters(self) -> None:
        self.show_unsupported.setChecked(True)
        self.show_no_display.setChecked(False)
        self._proxy.set_show_unsupported(True)
        self._proxy.set_show_no_display(False)
        self._proxy.set_current_desktop_only(False, current_desktops())

    # -- refresh --------------------------------------------------------

    def refresh(self) -> None:
        self._start_scan()
        self._start_steam_probe()

    def _start_scan(self) -> None:
        if self._scan_busy:
            return
        self._scan_busy = True
        self.refresh_button.setEnabled(False)
        self._counts.setText("Scanning desktop entries…")
        worker = ScanWorker(set(self._acknowledged))
        worker.signals.finished.connect(self._on_scan_finished)
        worker.signals.failed.connect(self._on_scan_failed)
        self._pool.start(worker)

    def _start_steam_probe(self) -> None:
        if self._steam_busy:
            return
        self._steam_busy = True
        self.steam_status.setText("Steam: checking…")
        worker = SteamProbeWorker()
        worker.signals.finished.connect(self._on_steam_finished)
        worker.signals.failed.connect(self._on_steam_failed)
        self._pool.start(worker)

    def _on_scan_finished(self, result: object) -> None:
        self._scan_busy = False
        self.refresh_button.setEnabled(True)
        if not isinstance(result, DiscoveryResult):
            return
        apps = list(result.applications.values())
        self._model.set_applications(apps)
        self._proxy.invalidate()
        self._update_counts()
        collisions = len(result.collisions)
        extra = f", {collisions} desktop-ID collision(s)" if collisions else ""
        self.statusBar().showMessage(
            f"Resolved {len(apps)} entries from {len(result.roots)} roots{extra}.",
            8000,
        )

    def _on_scan_failed(self, message: str) -> None:
        self._scan_busy = False
        self.refresh_button.setEnabled(True)
        QMessageBox.warning(self, "Scan failed", message)

    def _on_steam_finished(self, pairs: object, running: object) -> None:
        self._steam_busy = False
        if not isinstance(running, SteamRunningStatus):
            return
        self._running = running
        self._set_steam_status(running)
        if not isinstance(pairs, list):
            return
        self._installations = pairs
        self._fill_installations()

    def _on_steam_failed(self, message: str) -> None:
        self._steam_busy = False
        self.steam_status.setText("Steam: probe failed")
        QMessageBox.warning(self, "Steam probe failed", message)

    # -- Steam selectors ------------------------------------------------

    def _fill_installations(self) -> None:
        installations = [item[0] for item in self._installations]
        selection = select_installation(installations)
        previous = self.install_combo.blockSignals(True)
        self.install_combo.clear()
        if not installations:
            self.install_combo.addItem("No Steam installation found", None)
            self.install_combo.setEnabled(False)
            self._selected_installation = None
            self.install_combo.blockSignals(previous)
            self._fill_accounts(None)
            self._update_banner()
            return
        self.install_combo.setEnabled(True)
        if selection.requires_confirmation:
            self.install_combo.addItem(_PLACEHOLDER_INSTALL, None)
        for installation in installations:
            self.install_combo.addItem(installation_label(installation), installation.key)
        if selection.is_resolved and selection.selected is not None:
            self._select_install_key(selection.selected.key)
        self.install_combo.blockSignals(previous)
        self._on_install_chosen()

    def _select_install_key(self, key: str) -> None:
        for index in range(self.install_combo.count()):
            if self.install_combo.itemData(index) == key:
                self.install_combo.setCurrentIndex(index)
                return

    def _on_install_chosen(self) -> None:
        key = self.install_combo.currentData()
        pair = next((item for item in self._installations if item[0].key == key), None)
        self._selected_installation = pair[0] if pair else None
        self._fill_accounts(pair[1] if pair else None)
        self._update_banner()

    def _fill_accounts(self, accounts: list[SteamAccount] | None) -> None:
        previous = self.account_combo.blockSignals(True)
        self.account_combo.clear()
        if not accounts:
            label = (
                "No accounts in this installation"
                if self._selected_installation
                else "No installation selected"
            )
            self.account_combo.addItem(label, None)
            self.account_combo.setEnabled(False)
            self._selected_account = None
            self.account_combo.blockSignals(previous)
            return
        self.account_combo.setEnabled(True)
        selection = select_account(accounts)
        if selection.requires_confirmation:
            self.account_combo.addItem(_PLACEHOLDER_ACCOUNT, None)
        for account in selection.accounts:
            self.account_combo.addItem(account_label(account), account.account_id32)
        if selection.is_resolved and selection.selected is not None:
            self._select_account_id(selection.selected.account_id32)
        elif selection.requires_confirmation and not self._account_prompted:
            self._account_prompted = True
            self.account_combo.blockSignals(previous)
            self._prompt_for_account(selection)
            return
        self.account_combo.blockSignals(previous)
        self._on_account_chosen()

    def _select_account_id(self, account_id32: int) -> None:
        for index in range(self.account_combo.count()):
            if self.account_combo.itemData(index) == account_id32:
                self.account_combo.setCurrentIndex(index)
                return

    def _prompt_for_account(self, selection) -> None:
        dialog = AccountDialog(selection, self)
        if dialog.exec() and dialog.chosen_account() is not None:
            chosen = dialog.chosen_account()
            blocked = self.account_combo.blockSignals(True)
            self._select_account_id(chosen.account_id32)
            self.account_combo.blockSignals(blocked)
        self._on_account_chosen()

    def _on_account_chosen(self) -> None:
        account_id32 = self.account_combo.currentData()
        self._selected_account = None
        if self._selected_installation is None or account_id32 is None:
            return
        for installation, accounts in self._installations:
            if installation.key != self._selected_installation.key:
                continue
            for account in accounts:
                if account.account_id32 == account_id32:
                    self._selected_account = account
                    return

    def _set_steam_status(self, status: SteamRunningStatus) -> None:
        if status.running:
            reason = status.evidence[0] if status.evidence else "detected"
            self.steam_status.setText(f"Steam: running ({reason})")
            self.steam_status.setStyleSheet("color: #b3261e;")
            self.steam_status.setToolTip("\n".join(status.evidence))
            return
        if not status.is_certain:
            self.steam_status.setText(
                f"Steam: unclear ({status.inspection_failures} processes unreadable)"
            )
            self.steam_status.setStyleSheet("color: #c05621;")
            self.steam_status.setToolTip(
                "Some processes could not be inspected. A conservative false "
                "positive is required before any future write."
            )
            return
        self.steam_status.setText("Steam: not running")
        self.steam_status.setStyleSheet("color: #2e7d32;")
        self.steam_status.setToolTip("No Steam process matched.")

    def _update_banner(self) -> None:
        messages: list[str] = []
        if self._selected_installation and self._selected_installation.is_experimental:
            messages.append(
                "Flatpak Steam is experimental. Host launching is not implemented "
                "in the MVP, and sandbox permissions will never be changed automatically."
            )
        if self.install_combo.currentData() is None and self.install_combo.isEnabled():
            messages.append(
                "Several Steam installations were found. Choose one; the first "
                "path is not used automatically."
            )
        if self.account_combo.currentData() is None and self.account_combo.isEnabled():
            messages.append(
                "Several Steam accounts were found. Choose one; timestamps are "
                "hints, not a decision."
            )
        self.banner.setVisible(bool(messages))
        self.banner.setText(" ".join(messages))

    # -- table actions --------------------------------------------------

    def _on_source_filter(self) -> None:
        kind = self.source_filter.currentData()
        self._proxy.set_source_kinds(None if kind is None else {kind})

    def _on_current_desktop(self, checked: bool) -> None:
        self._proxy.set_current_desktop_only(checked, current_desktops())

    def _visible_source_rows(self) -> list[int]:
        rows: list[int] = []
        for proxy_row in range(self._proxy.rowCount()):
            index = self._proxy.index(proxy_row, 0)
            source = self._proxy.mapToSource(index)
            rows.append(source.row())
        return rows

    def _select_visible(self) -> None:
        self._model.set_all_selected(True, self._visible_source_rows())
        self._update_selection_count()

    def _clear_selection(self) -> None:
        self._model.set_all_selected(False)
        self._update_selection_count()

    def _on_row_changed(self, current, _previous) -> None:
        if not current.isValid():
            self.detail.clear()
            self.acknowledge.setEnabled(False)
            return
        source = self._proxy.mapToSource(current)
        row = self._model.row_at(source.row())
        app = row.app
        lines = [
            f"{app.localized_name or app.name}",
            f"desktop ID: {app.desktop_id}",
            f"path: {app.desktop_path}",
            f"source: {app.source_kind}",
            f"status: {row.status}",
            f"command: {row.command}",
        ]
        if app.working_directory:
            lines.append(f"working directory: {app.working_directory}")
        if app.unsupported_reason:
            lines.append(f"not importable: {app.unsupported_reason}")
        if row._adapter_error:
            lines.append(f"launch adapter refused: {row._adapter_error}")
        if app.has_collision:
            lines.append("colliding files:")
            lines.extend(f"  {path}" for path in app.collision_paths)
        if app.parse_warnings:
            lines.append("warnings:")
            lines.extend(f"  {warning}" for warning in app.parse_warnings)
        if self._selected_installation:
            lines.append(f"Steam install: {self._selected_installation.root}")
        if self._selected_account:
            lines.append(f"Steam account: {account_label(self._selected_account)}")
        lines.append("In Steam: unknown (needs Phase 5 state and Phase 6 VDF).")
        self.detail.setPlainText("\n".join(lines))
        self.acknowledge.setEnabled(
            app.unsupported_code == UnsupportedCode.DESKTOP_ID_COLLISION
        )

    def _acknowledge_collision(self) -> None:
        current = self.table.currentIndex()
        if not current.isValid():
            return
        source = self._proxy.mapToSource(current)
        app = self._model.row_at(source.row()).app
        if app.unsupported_code != UnsupportedCode.DESKTOP_ID_COLLISION:
            return
        self._acknowledged.add(app.desktop_id)
        self._start_scan()

    def _update_selection_count(self) -> None:
        count = len(self._model.selected_applications())
        self.selection_label.setText(f"{count} selected")

    def _update_counts(self) -> None:
        visible = self._proxy.rowCount()
        total = self._model.rowCount()
        importable = sum(1 for row in self._model.rows() if row.is_importable)
        self._counts.setText(
            f"{visible} shown / {total} resolved / {importable} importable"
        )
        self._update_selection_count()

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()


def run_app() -> int:
    """Create a QApplication and show the main window."""
    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    app = existing if existing is not None else QApplication([])
    app.setApplicationName("Steam Desktop Importer")
    window = MainWindow()
    window.show()
    if existing is not None:
        return 0
    return app.exec()
