"""Main window (IMPLEMENTATION.md §25, Phases 3–12).

Import and Relink commit ``shortcuts.vdf`` through the Phase 7 transaction,
then place selected SteamGridDB artwork under ``config/grid/``. Optional
collection membership is written afterwards through
``steam/collection_commit.py``. They stay disabled while Steam is running,
the probe is uncertain, or no installation+account is selected, unless
Steam-running detection is overridden for a false positive. Running status
is polled while the window is open, and is re-checked when Import or Relink
is clicked.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..desktop.discovery import DiscoveryResult
from ..launch import (
    HostLaunchPermission,
    MANUAL_OVERRIDE_COMMAND,
    probe_host_launch_permission,
)
from ..models import SOURCE_KINDS, DesktopApplication, SteamAccount, SteamInstallation, UnsupportedCode
from ..state import (
    DEFAULT_STEAM_POLL_MS,
    STATUS_CHANGED,
    STATUS_IMPORTED,
    STATUS_NEW,
    STATUS_POSSIBLE_MATCH,
    StateStore,
    classify_applications,
    current_exec,
    current_name,
    default_state_path,
    likely_existing_match,
)
from ..steam import (
    CollectionAssignment,
    CollectionError,
    CommitError,
    CommitHooks,
    ImportPlanningError,
    ImportResult,
    apply_applications,
    detect_steam_running,
    first_import_candidate,
    list_existing_shortcuts,
    load_collections,
    relink_application,
    select_account,
    select_installation,
    shortcuts_vdf_path,
    steam_allows_write,
)
from ..steam.collections import collection_list_label, is_tag_collection_id
from ..steam.running import SteamRunningStatus
from ..steamgriddb.auth import resolve_api_key
from .account_dialog import AccountDialog
from .artwork_dialog import ACTION_SKIP_REMAINING, ACTION_USE, ArtworkDialog
from .delegates import source_badge_delegate, status_badge_delegate
from .models import ApplicationFilterProxy, ApplicationTableModel, Column
from .settings_dialog import SettingsDialog
from .workers import (
    CallableWorker,
    ScanWorker,
    SteamProbeWorker,
    account_label,
    installation_label,
)

__all__ = ["MainWindow", "STEAM_POLL_MS", "current_desktops", "run_app"]

STEAM_POLL_MS = DEFAULT_STEAM_POLL_MS

_SOURCE_ALL = "all sources"
_PLACEHOLDER_INSTALL = "Select a Steam installation…"
_PLACEHOLDER_ACCOUNT = "Select a Steam account…"
_IMPORT_FILTER_ALL = "All import statuses"
_IMPORT_FILTER_STATUSES = (
    STATUS_NEW,
    STATUS_IMPORTED,
    STATUS_CHANGED,
    STATUS_POSSIBLE_MATCH,
)
_IMPORTABLE_STATUSES = frozenset({STATUS_NEW, STATUS_CHANGED, STATUS_IMPORTED})


def current_desktops(environ: dict[str, str] | None = None) -> set[str]:
    """``$XDG_CURRENT_DESKTOP`` split on ``:``, as the specification defines it."""
    env = os.environ if environ is None else environ
    raw = env.get("XDG_CURRENT_DESKTOP", "")
    return {part.strip().lower() for part in raw.split(":") if part.strip()}


class MainWindow(QMainWindow):
    def __init__(
        self,
        parent=None,
        *,
        auto_refresh: bool = True,
        state_store: StateStore | None = None,
        detect_steam: Callable[[], SteamRunningStatus] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Steam Desktop Importer")
        self.resize(1280, 800)

        self._pool = QThreadPool.globalInstance()
        self._store_fallback = False
        if state_store is not None:
            self._store = state_store
        else:
            try:
                self._store = StateStore(default_state_path())
            except OSError:
                self._store = StateStore(":memory:")
                self._store_fallback = True
        self._acknowledged = list(self._store.acknowledged_collisions())
        self._installations: list[tuple[SteamInstallation, list[SteamAccount]]] = []
        self._selected_installation: SteamInstallation | None = None
        self._selected_account: SteamAccount | None = None
        self._collections_identity: tuple[str, int] | None = None
        self._assignable_collection_names: set[str] = set()
        self._unassignable_collection_names: dict[str, str] = {}
        self._running: SteamRunningStatus | None = None
        self._host_launch_permission: HostLaunchPermission | None = None
        self._host_launch_probe_busy = False
        self._detect_steam = detect_steam or detect_steam_running
        self._shortcuts_error: str | None = None
        self._scan_busy = False
        self._steam_busy = False
        self._running_poll_busy = False
        self._account_prompted = False
        self._auto_refresh = auto_refresh
        self._steam_poll = QTimer(self)
        self._steam_poll.setInterval(self._store.steam_poll_ms())
        self._steam_poll.timeout.connect(self._poll_steam_running)

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
            self._apply_steam_poll_settings()

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
        self.imported_filter.addItem(_IMPORT_FILTER_ALL, None)
        for status in _IMPORT_FILTER_STATUSES:
            self.imported_filter.addItem(status, status)
        self.imported_filter.setToolTip(
            "Filter by §25.1 import status for the selected installation "
            "and account. Imported means managed in importer state, not "
            "verified in shortcuts.vdf."
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
        self.table.setItemDelegateForColumn(
            Column.IMPORT_STATUS, status_badge_delegate(self.table)
        )
        self.table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select an application to see details.")

        self.acknowledge = QPushButton("Acknowledge desktop-ID collision")
        self.acknowledge.setEnabled(False)
        self.acknowledge.setToolTip(
            "Lift the import block for this colliding desktop ID and remember "
            "that acknowledgement in the importer state store."
        )

        self.import_button = QPushButton("Import selected")
        self.import_button.setEnabled(False)
        self.relink_button = QPushButton("Relink selected")
        self.relink_button.setEnabled(False)
        self.relink_button.setToolTip(
            "Bind a Possible Existing Match to the existing shortcut instead of "
            "creating a new one."
        )

        self.collection_list = QListWidget()
        self.collection_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.collection_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.collection_list.setToolTip(
            "Optional. Checked collections receive the imported shortcuts. "
            "Steam's Hidden and tag-derived collections are omitted. Nothing "
            "is added unless you check a box or type a new name."
        )
        self.collection_filter = QLineEdit()
        self.collection_filter.setPlaceholderText("Filter collections…")
        self.collection_filter.setClearButtonEnabled(True)
        self.collection_filter.setToolTip(
            "Hide collections whose names do not match."
        )
        self.collection_new = QLineEdit()
        self.collection_new.setPlaceholderText("New collection name (optional)")
        self.collection_new.setToolTip(
            "Creates a collection if no assignable collection already has this "
            "name (including tag collections). Names used by Hidden or Dynamic "
            "Collections cannot be reused. Left blank, no collection is created."
        )

        collections_page = QWidget()
        collections_layout = QVBoxLayout(collections_page)
        collections_layout.setContentsMargins(0, 8, 0, 0)
        hint = QLabel(
            "Optional. Checked collections receive the imported shortcuts. "
            "Store-tag shelves are labelled (tag collection). Hidden and "
            "Dynamic Collections are omitted."
        )
        hint.setWordWrap(True)
        collections_layout.addWidget(hint)
        collections_layout.addWidget(self.collection_filter)
        collections_layout.addWidget(self.collection_list, stretch=1)
        collections_layout.addWidget(self.collection_new)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.detail, "Details")
        self.detail_tabs.addTab(collections_page, "Collections")

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(8, 0, 8, 8)
        bottom_layout.addWidget(self.detail_tabs, stretch=1)
        actions = QHBoxLayout()
        actions.addWidget(self.acknowledge)
        actions.addStretch()
        self.selection_label = QLabel("0 selected")
        actions.addWidget(self.selection_label)
        actions.addWidget(self.relink_button)
        actions.addWidget(self.import_button)
        bottom_layout.addLayout(actions)
        bottom.setMinimumHeight(220)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
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
        self.imported_filter.currentIndexChanged.connect(self._on_import_filter)
        self.show_no_display.toggled.connect(self._proxy.set_show_no_display)
        self.show_unsupported.toggled.connect(self._proxy.set_show_unsupported)
        self.current_desktop_only.toggled.connect(self._on_current_desktop)
        self.refresh_button.clicked.connect(self.refresh)
        self.settings_button.clicked.connect(self._open_settings)
        self.select_visible.clicked.connect(self._select_visible)
        self.clear_selection.clicked.connect(self._clear_selection)
        self.acknowledge.clicked.connect(self._acknowledge_collision)
        self.import_button.clicked.connect(self._import_selected)
        self.relink_button.clicked.connect(self._relink_selected)
        self.collection_filter.textChanged.connect(self._filter_collections)
        self.install_combo.currentIndexChanged.connect(self._on_install_chosen)
        self.account_combo.currentIndexChanged.connect(self._on_account_chosen)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._model.dataChanged.connect(self._update_selection_count)
        self._proxy.modelReset.connect(self._update_counts)
        self._proxy.layoutChanged.connect(self._update_counts)
        self._update_import_actions()

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
        self._refresh_import_statuses()
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
        if isinstance(running, SteamRunningStatus):
            self._apply_running_status(running)
        if not isinstance(pairs, list):
            return
        self._installations = pairs
        self._fill_installations()

    def _poll_steam_running(self) -> None:
        """Re-check Steam without rediscovering installations (no combo flicker)."""
        if not self._steam_checks_enabled() or self._running_poll_busy or self._steam_busy:
            return
        self._running_poll_busy = True
        detect = self._detect_steam
        worker = CallableWorker(detect)
        worker.signals.finished.connect(self._on_running_poll)
        worker.signals.failed.connect(self._on_running_poll_failed)
        self._pool.start(worker)

    def _on_running_poll(self, status: object) -> None:
        self._running_poll_busy = False
        if isinstance(status, SteamRunningStatus):
            self._apply_running_status(status)

    def _on_running_poll_failed(self, _message: str) -> None:
        self._running_poll_busy = False
        self.steam_status.setText("Steam: probe failed")
        self.steam_status.setStyleSheet("color: #c05621;")
        self._running = SteamRunningStatus(
            running=False, evidence=(), inspection_failures=1
        )
        self._update_import_actions()

    def _apply_running_status(self, status: SteamRunningStatus) -> None:
        self._running = status
        self._set_steam_status(status)

    def _sync_running_status(self) -> None:
        """Blocking probe used at click time so a just-started Steam is caught."""
        if not self._steam_checks_enabled():
            self._update_import_actions()
            return
        try:
            status = self._detect_steam()
        except Exception:  # noqa: BLE001 — treat a failed probe as not writable
            status = SteamRunningStatus(running=False, evidence=(), inspection_failures=1)
        self._apply_running_status(status)

    def _on_steam_failed(self, message: str) -> None:
        self._steam_busy = False
        self.steam_status.setText("Steam: probe failed")
        QMessageBox.warning(self, "Steam probe failed", message)

    # -- Steam selectors ------------------------------------------------

    def _fill_installations(self) -> None:
        installations = [item[0] for item in self._installations]
        selection = select_installation(
            installations,
            remembered_key=self._store.remembered_installation(),
        )
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
        if self._selected_installation is not None:
            self._store.remember_installation(self._selected_installation.key)
        self._fill_accounts(pair[1] if pair else None)
        self._update_banner()
        self._refresh_import_statuses()

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
            self._refresh_import_statuses()
            return
        self.account_combo.setEnabled(True)
        remembered = (
            self._store.remembered_account(self._selected_installation.key)
            if self._selected_installation is not None
            else None
        )
        selection = select_account(accounts, remembered_account_id32=remembered)
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
            self._refresh_import_statuses()
            return
        for installation, accounts in self._installations:
            if installation.key != self._selected_installation.key:
                continue
            for account in accounts:
                if account.account_id32 == account_id32:
                    self._selected_account = account
                    self._store.remember_account(
                        self._selected_installation.key, account.account_id32
                    )
                    self._refresh_import_statuses()
                    return
        self._refresh_import_statuses()

    def _set_steam_status(self, status: SteamRunningStatus) -> None:
        if status.running:
            reason = status.evidence[0] if status.evidence else "detected"
            self.steam_status.setText(f"Steam: running ({reason})")
            self.steam_status.setStyleSheet("color: #b3261e;")
            self.steam_status.setToolTip("\n".join(status.evidence))
        elif not status.is_certain:
            self.steam_status.setText(
                f"Steam: unclear ({status.inspection_failures} processes unreadable)"
            )
            self.steam_status.setStyleSheet("color: #c05621;")
            self.steam_status.setToolTip(
                "Some processes could not be inspected. A write is blocked until "
                "the probe is certain Steam is closed."
            )
        else:
            self.steam_status.setText("Steam: not running")
            self.steam_status.setStyleSheet("color: #2e7d32;")
            self.steam_status.setToolTip("No Steam process matched.")
        if not self._steam_checks_enabled():
            current = self.steam_status.text()
            if "overridden" not in current:
                self.steam_status.setText(f"{current} — detection overridden")
            self.steam_status.setStyleSheet("color: #c05621;")
            self.steam_status.setToolTip(
                "Steam-running detection is overridden. Writes are allowed "
                "even if the last probe thought Steam was running."
            )
        self._update_import_actions()

    def _update_banner(self) -> None:
        messages: list[str] = []
        if self._selected_installation and self._selected_installation.is_experimental:
            enabled = self._store.flatpak_steam_host_launch()
            if enabled:
                messages.append(
                    "Flatpak Steam is experimental. Shortcuts use "
                    "flatpak-spawn --host. Live launch is not validated on "
                    "this release; sandbox permissions are never changed "
                    "automatically."
                )
                permission = self._host_launch_permission
                if permission is None:
                    messages.append("Checking Flatpak host-launch permission…")
                    self._probe_host_launch_permission()
                elif not permission.granted:
                    messages.append(
                        f"{permission.evidence} If you choose to grant it "
                        f"yourself: {MANUAL_OVERRIDE_COMMAND}"
                    )
            else:
                messages.append(
                    "Flatpak Steam is experimental. Host launching is disabled "
                    "in Settings, so raw host paths will be written. Sandbox "
                    "permissions are never changed automatically."
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
        if self._store_fallback:
            messages.append(
                "Could not create the importer state directory; this session "
                "is using an in-memory store and will be forgotten on exit."
            )
        if self._shortcuts_error:
            messages.append(
                "Existing shortcuts.vdf could not be parsed, so Possible "
                f"Existing Match and AppID occupancy are incomplete. {self._shortcuts_error}"
            )
        if not self._steam_checks_enabled():
            messages.append(
                "Steam-running detection is overridden. Writes are allowed "
                "even if the probe thinks Steam is running."
            )
        self.banner.setVisible(bool(messages))
        self.banner.setText(" ".join(messages))

    def _probe_host_launch_permission(self) -> None:
        if self._host_launch_probe_busy:
            return
        self._host_launch_probe_busy = True
        worker = CallableWorker(probe_host_launch_permission)
        worker.signals.finished.connect(self._on_host_launch_permission)
        worker.signals.failed.connect(self._on_host_launch_permission_failed)
        self._pool.start(worker)

    def _on_host_launch_permission(self, result: object) -> None:
        self._host_launch_probe_busy = False
        if isinstance(result, HostLaunchPermission):
            self._host_launch_permission = result
        self._update_banner()

    def _on_host_launch_permission_failed(self, _message: str) -> None:
        self._host_launch_probe_busy = False
        if self._host_launch_permission is None:
            self._host_launch_permission = HostLaunchPermission(
                granted=False,
                evidence=(
                    "could not read Flatpak Steam permissions; host launching is "
                    "experimental and may fail until org.freedesktop.Flatpak is "
                    "granted manually"
                ),
            )
        self._update_banner()

    # -- table actions --------------------------------------------------

    def _on_source_filter(self) -> None:
        kind = self.source_filter.currentData()
        self._proxy.set_source_kinds(None if kind is None else {kind})

    def _on_import_filter(self) -> None:
        status = self.imported_filter.currentData()
        self._proxy.set_import_statuses(None if status is None else {status})

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
        lines.append(f"In Steam: {row.import_status}")
        mapping = None
        if self._selected_installation is not None and self._selected_account is not None:
            mapping = self._store.get_mapping(
                self._selected_installation.key,
                self._selected_account.account_id32,
                app.desktop_id,
            )
        if mapping is not None:
            lines.append(f"persisted AppID: {mapping.steam_appid_unsigned}")
        else:
            lines.append(
                f"first-import candidate: {first_import_candidate(app.desktop_id)} "
                "(not persisted)"
            )
        lines.append(f"current Name: {current_name(app)}")
        lines.append(f"current Exec: {current_exec(app)}")
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
        self._store.acknowledge_collision(
            app.desktop_id, app.desktop_path, app.collision_paths
        )
        self._acknowledged = list(self._store.acknowledged_collisions())
        self._start_scan()

    def _selected_import_rows(self):
        return [
            row
            for row in self._model.selected_rows()
            if row.is_importable and row.import_status in _IMPORTABLE_STATUSES
        ]

    def _selected_relink_rows(self):
        return [
            row
            for row in self._model.selected_rows()
            if row.is_importable and row.import_status == STATUS_POSSIBLE_MATCH
        ]

    def _commit_hooks(self) -> CommitHooks:
        return CommitHooks(detect_steam=self._commit_detect_steam)

    def _steam_checks_enabled(self) -> bool:
        return self._store.steam_poll_enabled()

    def _commit_detect_steam(self) -> SteamRunningStatus:
        """Live probe used at commit time, or a closed result if overridden."""
        if not self._steam_checks_enabled():
            return SteamRunningStatus(
                running=False,
                evidence=("steam-running detection overridden",),
                inspection_failures=0,
            )
        return self._detect_steam()

    def _write_ready_reason(self) -> str | None:
        if self._selected_installation is None or self._selected_account is None:
            return "Select a Steam installation and account first."
        if not self._steam_checks_enabled():
            return None
        if self._running is None:
            return "Steam status has not been checked yet."
        if self._running.running:
            return "Close Steam before writing shortcuts.vdf."
        if not steam_allows_write(self._running):
            return "Steam status is uncertain; a write is blocked."
        return None

    def _update_import_actions(self) -> None:
        blocked = self._write_ready_reason()
        to_import = self._selected_import_rows()
        to_relink = self._selected_relink_rows()
        self.import_button.setEnabled(blocked is None and bool(to_import))
        self.relink_button.setEnabled(blocked is None and len(to_relink) == 1)
        if blocked is not None:
            self.import_button.setToolTip(blocked)
            self.relink_button.setToolTip(blocked)
            return
        if not to_import:
            self.import_button.setToolTip(
                "Select New, Changed, or Imported applications. Possible "
                "Existing Match must be relinked explicitly."
            )
        else:
            self.import_button.setToolTip(
                f"Import {len(to_import)} application(s) into shortcuts.vdf."
            )
        if len(to_relink) != 1:
            self.relink_button.setToolTip(
                "Select exactly one Possible Existing Match to take ownership "
                "of the existing shortcut."
            )
        else:
            self.relink_button.setToolTip(
                "Bind this desktop entry to the matching existing shortcut."
            )

    def _confirm_write(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _import_selected(self) -> None:
        self._sync_running_status()
        blocked = self._write_ready_reason()
        if blocked is not None:
            QMessageBox.warning(self, "Cannot import", blocked)
            return
        rows = self._selected_import_rows()
        skipped = len(self._selected_relink_rows())
        if not rows:
            QMessageBox.information(
                self,
                "Nothing to import",
                "Select New, Changed, or Imported applications. Possible "
                "Existing Match must be relinked explicitly.",
            )
            return
        extra = (
            f"\n\n{skipped} Possible Existing Match row(s) will be skipped."
            if skipped
            else ""
        )
        blocked_name = self._blocked_create_collection_reason()
        if blocked_name is not None:
            QMessageBox.warning(self, "Cannot create collection", blocked_name)
            return
        account = self._selected_account
        installation = self._selected_installation
        assert account is not None and installation is not None
        if not self._confirm_write(
            "Import into Steam",
            f"Write {len(rows)} shortcut(s) to\n{shortcuts_vdf_path(account)}\n"
            f"for {account_label(account)}?\n\n"
            "Steam must stay closed. A timestamped backup of shortcuts.vdf "
            f"will be created.{self._collection_confirm_suffix()}{extra}",
        ):
            return
        apps = [row.app for row in rows]
        try:
            with tempfile.TemporaryDirectory(prefix="sdi-art-") as tmp:
                artwork_files = self._prepare_import_artwork(apps, Path(tmp))
                self._sync_running_status()
                blocked = self._write_ready_reason()
                if blocked is not None:
                    QMessageBox.warning(self, "Cannot import", blocked)
                    return
                result = apply_applications(
                    apps,
                    installation=installation,
                    account=account,
                    store=self._store,
                    steam_status=self._commit_detect_steam(),
                    hooks=self._commit_hooks(),
                    artwork_files=artwork_files,
                    collections=self._collection_assignment(),
                )
        except (CommitError, ImportPlanningError) as error:
            QMessageBox.warning(self, "Import failed", str(error))
            self._refresh_import_statuses()
            return
        self._report_write_result("Import complete", result, apps, installation, account)

    def _relink_selected(self) -> None:
        self._sync_running_status()
        blocked = self._write_ready_reason()
        if blocked is not None:
            QMessageBox.warning(self, "Cannot relink", blocked)
            return
        rows = self._selected_relink_rows()
        if len(rows) != 1:
            QMessageBox.information(
                self,
                "Select one match",
                "Relink needs exactly one Possible Existing Match.",
            )
            return
        app = rows[0].app
        matches = [
            shortcut
            for shortcut in self._existing_shortcuts()
            if likely_existing_match(app, shortcut)
        ]
        if len(matches) != 1:
            QMessageBox.warning(
                self,
                "Cannot relink",
                "Relink needs exactly one existing shortcut with this name and "
                f"executable; found {len(matches)}.",
            )
            return
        match = matches[0]
        blocked_name = self._blocked_create_collection_reason()
        if blocked_name is not None:
            QMessageBox.warning(self, "Cannot create collection", blocked_name)
            return
        account = self._selected_account
        installation = self._selected_installation
        assert account is not None and installation is not None
        if not self._confirm_write(
            "Relink existing shortcut",
            f"Take ownership of AppID {match.appid_unsigned} "
            f"({match.name}) for {app.desktop_id}?\n\n"
            f"A new shortcut will not be created.{self._collection_confirm_suffix()}",
        ):
            return
        try:
            with tempfile.TemporaryDirectory(prefix="sdi-art-") as tmp:
                artwork_files = self._prepare_import_artwork([app], Path(tmp))
                self._sync_running_status()
                blocked = self._write_ready_reason()
                if blocked is not None:
                    QMessageBox.warning(self, "Cannot relink", blocked)
                    return
                result = relink_application(
                    app,
                    match.appid_unsigned,
                    installation=installation,
                    account=account,
                    store=self._store,
                    steam_status=self._commit_detect_steam(),
                    hooks=self._commit_hooks(),
                    artwork_files=artwork_files,
                    collections=self._collection_assignment(),
                )
        except (CommitError, ImportPlanningError) as error:
            QMessageBox.warning(self, "Relink failed", str(error))
            self._refresh_import_statuses()
            return
        self._report_write_result("Relink complete", result, [app], installation, account)

    def _prepare_import_artwork(
        self,
        apps: Sequence[DesktopApplication],
        dest_dir: Path,
    ) -> dict[str, dict[str, Path]]:
        """Collect per-app temps. No key means shortcut-only import."""
        if not apps or not resolve_api_key():
            return {}
        files: dict[str, dict[str, Path]] = {}
        for app in apps:
            dialog = ArtworkDialog(app, dest_dir, parent=self, store=self._store)
            dialog.exec()
            choice = dialog.choice()
            if choice.action == ACTION_SKIP_REMAINING:
                break
            if choice.action == ACTION_USE and choice.files:
                files[app.desktop_id] = dict(choice.files)
        return files

    def _report_write_result(
        self,
        title: str,
        result: ImportResult,
        apps: Sequence[DesktopApplication],
        installation: SteamInstallation,
        account: SteamAccount,
    ) -> None:
        backup = (
            f"\nBackup: {result.commit.backup_path}" if result.commit.backup_path else ""
        )
        notes = []
        if result.state_errors:
            notes.append(
                "State store errors (VDF was written):\n" + "\n".join(result.state_errors)
            )
        if result.artwork_errors:
            notes.append(
                "Artwork errors (shortcuts remain valid):\n"
                + "\n".join(result.artwork_errors)
            )
        if result.collection_errors:
            notes.append(
                "Collection errors (shortcuts remain valid):\n"
                + "\n".join(result.collection_errors)
            )
        extra = ("\n\n" + "\n\n".join(notes)) if notes else ""
        QMessageBox.information(
            self,
            title,
            f"Wrote {len(result.imported)} shortcut(s).{backup}{extra}",
        )
        if result.artwork_errors:
            retry = QMessageBox.question(
                self,
                "Retry artwork?",
                "Shortcuts were written. Retry downloading and placing artwork?",
                QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.No,
            )
            if retry == QMessageBox.StandardButton.Yes:
                self._retry_artwork(apps, installation, account, result)
        self._refresh_import_statuses()

    def _retry_artwork(
        self,
        apps: Sequence[DesktopApplication],
        installation: SteamInstallation,
        account: SteamAccount,
        previous: ImportResult,
    ) -> None:
        failed = {
            app.desktop_id
            for app in apps
            if any(message.startswith(app.desktop_id) for message in previous.artwork_errors)
        }
        targets = [app for app in apps if app.desktop_id in failed] or list(apps)
        try:
            with tempfile.TemporaryDirectory(prefix="sdi-art-") as tmp:
                artwork_files = self._prepare_import_artwork(targets, Path(tmp))
                if not artwork_files:
                    return
                retry_apps = [app for app in targets if app.desktop_id in artwork_files]
                self._sync_running_status()
                blocked = self._write_ready_reason()
                if blocked is not None:
                    QMessageBox.warning(self, "Cannot retry artwork", blocked)
                    return
                result = apply_applications(
                    retry_apps,
                    installation=installation,
                    account=account,
                    store=self._store,
                    steam_status=self._commit_detect_steam(),
                    hooks=self._commit_hooks(),
                    artwork_files=artwork_files,
                )
        except (CommitError, ImportPlanningError) as error:
            QMessageBox.warning(self, "Artwork retry failed", str(error))
            return
        if result.artwork_errors:
            QMessageBox.warning(
                self,
                "Artwork still incomplete",
                "Shortcuts remain valid.\n" + "\n".join(result.artwork_errors),
            )
        else:
            QMessageBox.information(self, "Artwork updated", "Artwork was written.")

    def _update_selection_count(self) -> None:
        count = len(self._model.selected_applications())
        self.selection_label.setText(f"{count} selected")
        self._update_import_actions()

    def _update_counts(self) -> None:
        visible = self._proxy.rowCount()
        total = self._model.rowCount()
        importable = sum(1 for row in self._model.rows() if row.is_importable)
        self._counts.setText(
            f"{visible} shown / {total} resolved / {importable} importable"
        )
        self._update_selection_count()

    def _existing_shortcuts(self):
        self._shortcuts_error = None
        if self._selected_account is None:
            return []
        path = shortcuts_vdf_path(self._selected_account)
        try:
            return list_existing_shortcuts(path)
        except ValueError as error:
            self._shortcuts_error = str(error)
            return []

    def _filter_collections(self) -> None:
        needle = self.collection_filter.text().strip().casefold()
        for index in range(self.collection_list.count()):
            item = self.collection_list.item(index)
            if item is None:
                continue
            if not needle:
                item.setHidden(False)
                continue
            item.setHidden(needle not in item.text().casefold())

    def _checked_collection_ids(self) -> list[str]:
        checked: list[str] = []
        for index in range(self.collection_list.count()):
            item = self.collection_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                collection_id = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(collection_id, str) and collection_id:
                    checked.append(collection_id)
        return checked

    def _blocked_create_collection_reason(self) -> str | None:
        typed = self.collection_new.text().strip()
        if not typed:
            return None
        fold = typed.casefold()
        if fold in self._assignable_collection_names:
            return None
        blocked = self._unassignable_collection_names.get(fold)
        if blocked is None:
            return None
        return (
            f'Steam already has a collection named "{blocked}" that this '
            "importer cannot add shortcuts to (Hidden or a Dynamic Collection). "
            "Creating another with that name would show as a duplicate. Pick a "
            "different name."
        )

    def _collection_assignment(self) -> CollectionAssignment:
        name = self.collection_new.text().strip()
        return CollectionAssignment(
            existing_ids=tuple(self._checked_collection_ids()),
            create_names=(name,) if name else (),
        )

    def _collection_confirm_suffix(self) -> str:
        assignment = self._collection_assignment()
        if assignment.is_empty():
            return ""
        names: list[str] = []
        for index in range(self.collection_list.count()):
            item = self.collection_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                names.append(item.text())
        typed = self.collection_new.text().strip()
        if typed:
            names.append(f'new "{typed}"')
        return "\n\nAlso add to collection(s): " + ", ".join(names)

    def _reload_collections(self) -> None:
        current_identity = (
            (
                self._selected_installation.key,
                self._selected_account.account_id32,
            )
            if self._selected_installation is not None and self._selected_account is not None
            else None
        )
        previously = (
            set(self._checked_collection_ids())
            if current_identity == self._collections_identity
            else set()
        )
        self._collections_identity = current_identity
        self.collection_list.clear()
        self._assignable_collection_names = set()
        self._unassignable_collection_names = {}
        if self._selected_account is None:
            self._collections_identity = None
            self.collection_list.setEnabled(False)
            self.collection_filter.setEnabled(False)
            self.collection_new.setEnabled(False)
            return
        self.collection_list.setEnabled(True)
        self.collection_filter.setEnabled(True)
        self.collection_new.setEnabled(True)
        try:
            document = load_collections(self._selected_account)
        except CollectionError as error:
            placeholder = QListWidgetItem(f"Could not read collections ({error})")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.collection_list.addItem(placeholder)
            self._filter_collections()
            return
        for live in document.live_collections():
            fold = live.name.casefold()
            if live.assignable:
                self._assignable_collection_names.add(fold)
            else:
                self._unassignable_collection_names[fold] = live.name
        collections = sorted(
            document.assignable_collections(),
            key=lambda item: item.name.casefold(),
        )
        if not collections:
            placeholder = QListWidgetItem("No collections yet — type a name to create one")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.collection_list.addItem(placeholder)
            self._filter_collections()
            return
        for collection in collections:
            item = QListWidgetItem(collection_list_label(collection))
            item.setData(Qt.ItemDataRole.UserRole, collection.collection_id)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            checked = (
                Qt.CheckState.Checked
                if collection.collection_id in previously
                else Qt.CheckState.Unchecked
            )
            item.setCheckState(checked)
            if is_tag_collection_id(collection.collection_id):
                item.setToolTip(
                    f"{collection.collection_id} — Steam store-tag collection, "
                    f"{len(collection.added)} games"
                )
            else:
                item.setToolTip(
                    f"{collection.collection_id} — {len(collection.added)} games"
                )
            self.collection_list.addItem(item)
        self._filter_collections()

    def _refresh_import_statuses(self) -> None:
        existing = self._existing_shortcuts()
        statuses = classify_applications(
            [row.app for row in self._model.rows()],
            self._store,
            self._selected_installation.key if self._selected_installation else None,
            self._selected_account.account_id32 if self._selected_account else None,
            existing,
        )
        self._model.set_import_statuses(statuses)
        self._reload_collections()
        self._update_banner()
        self._update_import_actions()
        current = self.table.currentIndex()
        if current.isValid():
            self._on_row_changed(current, current)

    def _apply_steam_poll_settings(self, *, probe_now: bool = False) -> None:
        """Start, stop, or retune the live Steam poll from stored preferences.

        Tests construct the window with ``auto_refresh=False`` and never poll.
        When detection is overridden, Import/Relink and the VDF commit skip
        the Steam-running probe as well.
        """
        if not self._auto_refresh or not self._steam_checks_enabled():
            self._steam_poll.stop()
            self._update_banner()
            if self._running is not None:
                self._set_steam_status(self._running)
            else:
                self._update_import_actions()
            return
        interval = self._store.steam_poll_ms()
        self._steam_poll.setInterval(interval)
        if not self._steam_poll.isActive():
            self._steam_poll.start()
        if probe_now:
            self._poll_steam_running()
        self._update_banner()
        if self._running is not None:
            self._set_steam_status(self._running)
        else:
            self._update_import_actions()

    def _open_settings(self) -> None:
        SettingsDialog(
            self,
            store=self._store,
            on_poll_changed=lambda: self._apply_steam_poll_settings(probe_now=True),
        ).exec()
        self._apply_steam_poll_settings()
        self._update_banner()

    def closeEvent(self, event) -> None:
        self._steam_poll.stop()
        self._store.close()
        super().closeEvent(event)


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
