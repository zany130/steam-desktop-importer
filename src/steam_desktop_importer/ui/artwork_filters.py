"""SteamGridDB artwork filters, matching Steam ROM Manager's allow-checkboxes."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..state import StateStore
from ..steamgriddb.filters import GRID_STYLES, ArtworkFilters

__all__ = ["ArtworkFilterBar"]

_STYLE_LABELS = {
    "alternate": "Alternate",
    "blurred": "Blurred",
    "white_logo": "White logo",
    "material": "Material",
    "no_logo": "No logo",
}


class ArtworkFilterBar(QWidget):
    """Static/animated, NSFW, joke, epilepsy, and optional grid style."""

    changed = Signal()

    def __init__(
        self,
        parent=None,
        *,
        store: StateStore | None = None,
        persist: bool = True,
        compact: bool = True,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._persist = persist
        self._loading = True

        self.static = QCheckBox("Static")
        self.animated = QCheckBox("Animated")
        self.nsfw = QCheckBox("NSFW")
        self.humor = QCheckBox("Joke")
        self.epilepsy = QCheckBox("Epilepsy")
        self.static.setChecked(True)
        self.static.setToolTip("Include still images (SteamGridDB default).")
        self.animated.setToolTip("Include animated WebP artwork.")
        self.nsfw.setToolTip("Include NSFW artwork. Off matches SteamGridDB's default.")
        self.humor.setToolTip("Include joke / humor artwork.")
        self.epilepsy.setToolTip("Include artwork tagged as potentially seizure-inducing.")
        self.style = QComboBox()
        self.style.addItem("Any", "")
        for value in GRID_STYLES:
            self.style.addItem(_STYLE_LABELS[value], value)
        self.style.setToolTip(
            "Limit portrait/wide grids to one SteamGridDB style. Heroes, logos, "
            "and icons are not filtered by this."
        )
        self.style.setMinimumWidth(160)
        self.style.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        if compact:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            for box in (self.static, self.animated, self.nsfw, self.humor, self.epilepsy):
                layout.addWidget(box)
            layout.addWidget(QLabel("Grid style"))
            layout.addWidget(self.style)
            layout.addStretch(1)
        else:
            stack = QVBoxLayout(self)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(8)

            def labeled_row(title: str, *widgets: QWidget) -> None:
                stack.addWidget(QLabel(title))
                holder = QWidget()
                inner = QHBoxLayout(holder)
                inner.setContentsMargins(12, 0, 0, 0)
                inner.setSpacing(16)
                for widget in widgets:
                    inner.addWidget(widget)
                inner.addStretch(1)
                stack.addWidget(holder)

            labeled_row("Animation", self.static, self.animated)
            labeled_row("Include", self.nsfw, self.humor, self.epilepsy)
            style_row = QWidget()
            style_layout = QHBoxLayout(style_row)
            style_layout.setContentsMargins(0, 4, 0, 0)
            style_layout.addWidget(QLabel("Grid style"))
            style_layout.addWidget(self.style, 1)
            stack.addWidget(style_row)

        for box in (self.static, self.animated, self.nsfw, self.humor, self.epilepsy):
            box.toggled.connect(self._on_toggled)
        self.style.currentIndexChanged.connect(self._on_toggled)

        if store is not None:
            self.set_filters(store.artwork_filters())
        self._loading = False

    def filters(self) -> ArtworkFilters:
        style = self.style.currentData()
        return ArtworkFilters.from_allows(
            nsfw=self.nsfw.isChecked(),
            humor=self.humor.isChecked(),
            epilepsy=self.epilepsy.isChecked(),
            static=self.static.isChecked(),
            animated=self.animated.isChecked(),
            styles=(style,) if style else (),
        )

    def set_filters(self, filters: ArtworkFilters) -> None:
        self._loading = True
        self.static.setChecked(filters.include_static)
        self.animated.setChecked(filters.include_animated)
        self.nsfw.setChecked(filters.allow_nsfw)
        self.humor.setChecked(filters.allow_humor)
        self.epilepsy.setChecked(filters.allow_epilepsy)
        wanted = filters.grid_style
        index = self.style.findData(wanted)
        self.style.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

    def _on_toggled(self, *_args: object) -> None:
        if self._loading:
            return
        if not self.static.isChecked() and not self.animated.isChecked():
            self.static.blockSignals(True)
            self.static.setChecked(True)
            self.static.blockSignals(False)
        filters = self.filters()
        if self._persist and self._store is not None:
            self._store.set_artwork_filters(filters)
        self.changed.emit()
