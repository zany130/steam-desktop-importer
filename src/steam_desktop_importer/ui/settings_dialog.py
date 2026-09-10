"""Settings dialog.

SteamGridDB auth and other later-phase settings do not exist yet, so this
dialog stays informational. Chosen Steam installation and account are
remembered in the Phase 5 SQLite store, not here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

__all__ = ["SettingsDialog"]

_BODY = """<h3>Settings</h3>
<p>Nothing here is persisted. Chosen Steam installation and account are
remembered in the SQLite store, not in this dialog.</p>
<p><b>Current policy</b></p>
<ul>
<li>Native Steam is the MVP target.</li>
<li>Flatpak Steam is experimental and is labelled as such.</li>
<li>Several installations or accounts are never chosen silently.</li>
<li>SteamGridDB is Phase 8; there is no API-key field here because it
would not do anything.</li>
<li>Writes to <code>shortcuts.vdf</code> use the Phase 7 transaction
(lock, backup, fsync, parse-back, replace) and require Steam to be
closed.</li>
</ul>
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        label = QLabel(_BODY)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(480, 360)
