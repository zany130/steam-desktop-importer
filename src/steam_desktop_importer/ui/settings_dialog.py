"""Settings dialog.

Phase 3 needs a settings control. Persistent settings, SteamGridDB auth, and
remembered Steam choices all belong to later phases, so this dialog is
deliberately informational. It must not pretend those features exist.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

__all__ = ["SettingsDialog"]

_BODY = """<h3>Settings</h3>
<p>Nothing here is persisted yet. The SQLite store that will remember a
chosen Steam installation and account is Phase 5 work.</p>
<p><b>Current policy</b></p>
<ul>
<li>Native Steam is the MVP target.</li>
<li>Flatpak Steam is experimental and is labelled as such.</li>
<li>Several installations or accounts are never chosen silently.</li>
<li>SteamGridDB is Phase 8; there is no API-key field here because it
would not do anything.</li>
<li>No code path writes to <code>shortcuts.vdf</code>. Importing is
Phase 7.</li>
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
