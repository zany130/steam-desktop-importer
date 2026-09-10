"""Settings dialog.

SteamGridDB API keys are stored in a user config file with mode ``0600``,
or taken from ``SGDB_API_KEY``. Chosen Steam installation and account stay
in the Phase 5 SQLite store.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..steamgriddb.auth import ENV_KEY, clear_stored_api_key, default_key_path, resolve_api_key, save_api_key

__all__ = ["SettingsDialog"]

_POLICY = """<p><b>Current policy</b></p>
<ul>
<li>Native Steam is the MVP target.</li>
<li>Flatpak Steam is experimental and is labelled as such.</li>
<li>Several installations or accounts are never chosen silently.</li>
<li>Writes to <code>shortcuts.vdf</code> use the Phase 7 transaction
(lock, backup, fsync, parse-back, replace) and require Steam to be
closed.</li>
<li>SteamGridDB artwork search is available when an API key is set.
Selected artwork is written into the account's <code>config/grid/</code>
directory after a successful <code>shortcuts.vdf</code> commit. Without a
key, import is shortcut-only.</li>
</ul>
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Chosen Steam installation and account are remembered in the "
            "SQLite store. The SteamGridDB key is stored separately."
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

        policy = QLabel(_POLICY)
        policy.setWordWrap(True)
        policy.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(policy)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(520, 420)

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
