"""Settings dialog.

SteamGridDB API keys are stored in a user config file with mode ``0600``,
or taken from ``SGDB_API_KEY``. Chosen Steam installation, account, and
Steam-poll preferences stay in the Phase 5 SQLite store.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..state import (
    DEFAULT_STEAM_POLL_MS,
    MAX_STEAM_POLL_MS,
    MIN_STEAM_POLL_MS,
    StateStore,
)
from ..steamgriddb.auth import ENV_KEY, clear_stored_api_key, default_key_path, resolve_api_key, save_api_key

__all__ = ["DISABLE_STEAM_CHECK_WARNING", "SettingsDialog"]

_POLICY = """<p><b>Current policy</b></p>
<ul>
<li>Native Steam is the MVP target.</li>
<li>Flatpak Steam host launching is experimental. Shortcuts are wrapped
with <code>flatpak-spawn --host</code>. This importer never grants
<code>org.freedesktop.Flatpak</code>.</li>
<li>Several installations or accounts are never chosen silently.</li>
<li>Writes to <code>shortcuts.vdf</code> use the Phase 7 transaction
(lock, backup, fsync, parse-back, replace) and require Steam to be
closed unless Steam-running detection is overridden for a false
positive.</li>
<li>SteamGridDB artwork search is available when an API key is set.
Selected artwork is written into the account's <code>config/grid/</code>
directory after a successful <code>shortcuts.vdf</code> commit. Without a
key, import is shortcut-only.</li>
</ul>
"""

DISABLE_STEAM_CHECK_WARNING = (
    "This turns off every Steam-running check: the live indicator, "
    "Import and Relink gating, and the probe immediately before writing "
    "shortcuts.vdf.\n\n"
    "Use this only if the detector is wrong and you have fully exited Steam. "
    "Writing that file while Steam is actually running can overwrite or "
    "corrupt your non-Steam shortcuts."
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        store: StateStore | None = None,
        on_poll_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._store = store
        self._on_poll_changed = on_poll_changed
        self._loading = True
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Chosen Steam installation, account, and Steam-detection "
            "preferences are remembered in the SQLite store. The SteamGridDB "
            "key is stored separately."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("SteamGridDB API key")
        env_key = (os.environ.get(ENV_KEY) or "").strip()
        self._env_overrides = bool(env_key)
        if self._env_overrides:
            self.key_edit.setText(env_key)
            self.key_edit.setEnabled(False)
            hint = QLabel(
                f"{ENV_KEY} is set in the environment and takes precedence. "
                "Unset it to use a saved key."
            )
        else:
            stored = resolve_api_key()
            if stored:
                self.key_edit.setText(stored)
            hint = QLabel(f"Saved at {default_key_path()} with mode 0600.")
        hint.setWordWrap(True)
        form.addRow("SteamGridDB API key", self.key_edit)
        layout.addLayout(form)
        layout.addWidget(hint)

        key_row = QHBoxLayout()
        self.save_key = QPushButton("Save key")
        self.clear_key = QPushButton("Clear saved key")
        self.save_key.setEnabled(not self._env_overrides)
        self.clear_key.setEnabled(not self._env_overrides)
        self.save_key.clicked.connect(self._save_key)
        self.clear_key.clicked.connect(self._clear_key)
        key_row.addWidget(self.save_key)
        key_row.addWidget(self.clear_key)
        key_row.addStretch()
        layout.addLayout(key_row)

        self.poll_enabled = QCheckBox("Detect whether Steam is running (recommended)")
        self.poll_enabled.setChecked(True)
        self.poll_seconds = QSpinBox()
        self.poll_seconds.setRange(MIN_STEAM_POLL_MS // 1000, MAX_STEAM_POLL_MS // 1000)
        self.poll_seconds.setValue(DEFAULT_STEAM_POLL_MS // 1000)
        self.poll_seconds.setSuffix(" seconds")
        if store is not None:
            self.poll_enabled.setChecked(store.steam_poll_enabled())
            self.poll_seconds.setValue(max(1, store.steam_poll_ms() // 1000))
        self.poll_seconds.setEnabled(self.poll_enabled.isChecked())
        poll_form = QFormLayout()
        poll_form.addRow(self.poll_enabled)
        poll_form.addRow("Live check every", self.poll_seconds)
        layout.addLayout(poll_form)
        poll_note = QLabel(
            "Uncheck only if the detector is wrong and you have confirmed Steam "
            "is fully exited. That override also skips the write-time probe."
        )
        poll_note.setWordWrap(True)
        layout.addWidget(poll_note)
        self.poll_enabled.toggled.connect(self._on_poll_toggled)
        self.poll_seconds.valueChanged.connect(self._on_poll_interval_changed)

        self.host_launch = QCheckBox(
            "Experimental Flatpak Steam host launching (flatpak-spawn --host)"
        )
        self.host_launch.setChecked(True)
        self.host_launch.setToolTip(
            "When Flatpak Steam is the import target, wrap host commands with "
            "flatpak-spawn --host. This importer never runs flatpak override."
        )
        if store is not None:
            self.host_launch.setChecked(store.flatpak_steam_host_launch())
        layout.addWidget(self.host_launch)
        host_note = QLabel(
            "Stock Flathub Steam does not grant org.freedesktop.Flatpak. "
            "If launching from Flatpak Steam fails, you may run the override "
            "yourself; the importer will not do it."
        )
        host_note.setWordWrap(True)
        layout.addWidget(host_note)
        self.host_launch.toggled.connect(self._on_host_launch_toggled)

        policy = QLabel(_POLICY)
        policy.setWordWrap(True)
        policy.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(policy)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(540, 560)
        self._loading = False

    def _on_poll_toggled(self, checked: bool) -> None:
        if self._loading:
            self.poll_seconds.setEnabled(checked)
            return
        if not checked:
            answer = QMessageBox.warning(
                self,
                "Disable Steam-running detection?",
                DISABLE_STEAM_CHECK_WARNING,
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.poll_enabled.blockSignals(True)
                self.poll_enabled.setChecked(True)
                self.poll_enabled.blockSignals(False)
                return
        self.poll_seconds.setEnabled(checked)
        self._save_poll()

    def _on_poll_interval_changed(self, _value: int) -> None:
        if not self._loading:
            self._save_poll()

    def _save_poll(self) -> None:
        if self._store is None:
            return
        self._store.set_steam_poll(
            enabled=self.poll_enabled.isChecked(),
            interval_ms=self.poll_seconds.value() * 1000,
        )
        if self._on_poll_changed is not None:
            self._on_poll_changed()

    def _on_host_launch_toggled(self, checked: bool) -> None:
        if self._loading or self._store is None:
            return
        self._store.set_flatpak_steam_host_launch(checked)

    def _save_key(self) -> None:
        try:
            save_api_key(self.key_edit.text())
        except Exception as error:
            QMessageBox.warning(self, "Could not save API key", str(error))
            return
        QMessageBox.information(self, "API key saved", f"Stored at {default_key_path()}.")

    def _clear_key(self) -> None:
        clear_stored_api_key()
        self.key_edit.clear()
