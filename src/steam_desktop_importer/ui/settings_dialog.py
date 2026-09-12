"""Settings dialog.

SteamGridDB API keys are stored in a user config file with mode ``0600``,
or taken from ``SGDB_API_KEY``. Chosen Steam installation, account, and
Steam-poll preferences stay in the Phase 5 SQLite store.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
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
from .artwork_filters import ArtworkFilterBar

__all__ = ["DISABLE_STEAM_CHECK_WARNING", "SettingsDialog"]

DISABLE_STEAM_CHECK_WARNING = (
    "This turns off every Steam-running check: the live indicator, "
    "Import and Relink gating, and the probe immediately before writing "
    "shortcuts.vdf.\n\n"
    "Use this only if the detector is wrong and you have fully exited Steam. "
    "Writing that file while Steam is actually running can overwrite or "
    "corrupt your non-Steam shortcuts."
)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    palette = label.palette()
    muted = palette.color(QPalette.ColorRole.PlaceholderText)
    if muted.alpha() == 0:
        muted = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
    palette.setColor(QPalette.ColorRole.WindowText, muted)
    label.setPalette(palette)
    return label


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
        layout.setSpacing(12)

        layout.addWidget(self._steamgriddb_group())
        layout.addWidget(self._artwork_group())
        layout.addWidget(self._steam_group())
        layout.addWidget(self._flatpak_group())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.setMinimumWidth(580)
        self.resize(580, max(self.sizeHint().height(), 640))
        self._loading = False

    def _steamgriddb_group(self) -> QGroupBox:
        box = QGroupBox("SteamGridDB")
        layout = QVBoxLayout(box)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("API key")
        env_key = (os.environ.get(ENV_KEY) or "").strip()
        self._env_overrides = bool(env_key)
        if self._env_overrides:
            self.key_edit.setText(env_key)
            self.key_edit.setEnabled(False)
            hint = (
                f"{ENV_KEY} is set in the environment and takes precedence. "
                "Unset it to use a saved key."
            )
        else:
            stored = resolve_api_key()
            if stored:
                self.key_edit.setText(stored)
            hint = f"Saved at {default_key_path()} with mode 0600."

        self.save_key = QPushButton("Save")
        self.clear_key = QPushButton("Clear")
        self.save_key.setEnabled(not self._env_overrides)
        self.clear_key.setEnabled(not self._env_overrides)
        self.save_key.clicked.connect(self._save_key)
        self.clear_key.clicked.connect(self._clear_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.save_key)
        key_row.addWidget(self.clear_key)
        form = QFormLayout()
        form.addRow("API key", key_row)
        layout.addLayout(form)
        layout.addWidget(_hint(hint))
        return box

    def _artwork_group(self) -> QGroupBox:
        box = QGroupBox("Artwork filters")
        layout = QVBoxLayout(box)
        self.artwork_filters = ArtworkFilterBar(
            store=self._store, compact=False
        )
        layout.addWidget(self.artwork_filters)
        layout.addWidget(
            _hint(
                "Static only by default, matching SteamGridDB and Steam ROM "
                "Manager. Turn on Animated, NSFW, Joke, or Epilepsy to include "
                "those categories. Grid style applies to portrait and wide covers."
            )
        )
        return box

    def _steam_group(self) -> QGroupBox:
        box = QGroupBox("Steam")
        layout = QVBoxLayout(box)
        self.poll_enabled = QCheckBox("Detect whether Steam is running (recommended)")
        self.poll_enabled.setChecked(True)
        self.poll_seconds = QSpinBox()
        self.poll_seconds.setRange(MIN_STEAM_POLL_MS // 1000, MAX_STEAM_POLL_MS // 1000)
        self.poll_seconds.setValue(DEFAULT_STEAM_POLL_MS // 1000)
        self.poll_seconds.setSuffix(" seconds")
        if self._store is not None:
            self.poll_enabled.setChecked(self._store.steam_poll_enabled())
            self.poll_seconds.setValue(max(1, self._store.steam_poll_ms() // 1000))
        self.poll_seconds.setEnabled(self.poll_enabled.isChecked())
        interval = QHBoxLayout()
        interval.addWidget(QLabel("Check every"))
        interval.addWidget(self.poll_seconds)
        interval.addStretch(1)
        layout.addWidget(self.poll_enabled)
        layout.addLayout(interval)
        layout.addWidget(
            _hint(
                "Uncheck only if the detector is wrong and Steam is fully "
                "exited. That override also skips the write-time probe."
            )
        )
        self.poll_enabled.toggled.connect(self._on_poll_toggled)
        self.poll_seconds.valueChanged.connect(self._on_poll_interval_changed)
        return box

    def _flatpak_group(self) -> QGroupBox:
        box = QGroupBox("Flatpak Steam")
        layout = QVBoxLayout(box)
        self.host_launch = QCheckBox("Wrap host commands with flatpak-spawn --host")
        self.host_launch.setChecked(True)
        self.host_launch.setToolTip(
            "When Flatpak Steam is the import target, wrap host commands with "
            "flatpak-spawn --host. This importer never runs flatpak override."
        )
        if self._store is not None:
            self.host_launch.setChecked(self._store.flatpak_steam_host_launch())
        layout.addWidget(self.host_launch)
        layout.addWidget(
            _hint(
                "Experimental. This importer never grants org.freedesktop.Flatpak. "
                "If a launch from Flatpak Steam fails, you may run the override "
                "yourself."
            )
        )
        self.host_launch.toggled.connect(self._on_host_launch_toggled)
        return box

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
