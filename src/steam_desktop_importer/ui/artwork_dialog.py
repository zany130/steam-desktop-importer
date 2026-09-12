"""Per-application SteamGridDB artwork picker (IMPLEMENTATION.md §21, §25.4, Phase 9).

Search, preview, and download run off the GUI thread. Each worker builds its
own SteamGridDB client so ``requests.Session`` is never shared across threads.
This dialog never writes Steam ``grid/`` files; it only fills caller temps.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer, Qt, QThreadPool
from PySide6.QtGui import QImage, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..models import DesktopApplication
from ..state import StateStore
from ..steam.artwork import (
    SLOT_HERO,
    SLOT_ICON,
    SLOT_LOGO,
    SLOT_PORTRAIT,
    SLOT_WIDE,
    SLOTS,
    slot_for_grid,
)
from ..steamgriddb.apng import parse_apng
from ..steamgriddb.client import SteamGridDBClient
from ..steamgriddb.filters import ArtworkFilters
from ..steamgriddb.images import sniff_image
from ..steamgriddb.models import GameResult, GridAsset
from ..steamgriddb.queries import search_queries
from .artwork_filters import ArtworkFilterBar
from .workers import CallableWorker

__all__ = [
    "ACTION_SKIP",
    "ACTION_SKIP_REMAINING",
    "ACTION_USE",
    "ArtworkChoice",
    "ArtworkDialog",
]

ACTION_SKIP = "skip"
ACTION_SKIP_REMAINING = "skip_remaining"
ACTION_USE = "use"

_TAB_LABELS = {
    SLOT_PORTRAIT: "Portrait",
    SLOT_WIDE: "Wide",
    SLOT_HERO: "Hero",
    SLOT_LOGO: "Logo",
    SLOT_ICON: "Icon",
}


@dataclass
class ArtworkChoice:
    """What the dialog decided for one application."""

    action: str = ACTION_SKIP
    files: dict[str, Path] = field(default_factory=dict)


def _display_name(app: DesktopApplication) -> str:
    return (app.localized_name or app.name or app.desktop_id).strip()


def _asset_label(asset: GridAsset) -> str:
    size = (
        f"{asset.width}×{asset.height}"
        if asset.width and asset.height
        else "unknown size"
    )
    style = asset.style or asset.kind
    flags: list[str] = []
    if asset.animated:
        flags.append("animated")
    if asset.nsfw:
        flags.append("NSFW")
    if asset.humor:
        flags.append("joke")
    if asset.epilepsy:
        flags.append("epilepsy")
    suffix = f" · {' · '.join(flags)}" if flags else ""
    return f"{size} · {style} · #{asset.id}{suffix}"


def _compose_apng(animation, box) -> tuple[list[QPixmap], list[int]] | None:
    """Rasterise APNG frames onto a canvas and scale them for the preview label."""
    canvas = QImage(animation.width, animation.height, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    pixmaps: list[QPixmap] = []
    delays: list[int] = []
    for frame in animation.frames:
        before = canvas.copy() if frame.dispose_op == 2 else None
        image = QImage.fromData(frame.png_bytes)
        if image.isNull():
            return None
        painter = QPainter(canvas)
        if frame.blend_op == 0:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawImage(frame.x, frame.y, image)
        painter.end()
        pixmap = QPixmap.fromImage(canvas)
        if box.width() > 0 and box.height() > 0:
            pixmap = pixmap.scaled(
                box,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        pixmaps.append(pixmap)
        # Some SteamGridDB APNGs hold the first frame for several seconds.
        delays.append(min(frame.delay_ms, 1000))
        if frame.dispose_op == 1:
            painter = QPainter(canvas)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(
                frame.x,
                frame.y,
                frame.width,
                frame.height,
                Qt.GlobalColor.transparent,
            )
            painter.end()
        elif frame.dispose_op == 2 and before is not None:
            canvas = before
    if len(pixmaps) < 2:
        return None
    return pixmaps, delays


def _group_assets(
    client: SteamGridDBClient,
    game_id: int,
    filters: ArtworkFilters | None = None,
) -> dict[str, list[GridAsset]]:
    grouped: dict[str, list[GridAsset]] = {slot: [] for slot in SLOTS}
    for asset in client.get_grids(game_id, filters=filters):
        slot = slot_for_grid(width=asset.width, height=asset.height)
        grouped[slot].append(asset)
    grouped[SLOT_HERO] = list(client.get_heroes(game_id, filters=filters))
    grouped[SLOT_LOGO] = list(client.get_logos(game_id, filters=filters))
    grouped[SLOT_ICON] = list(client.get_icons(game_id, filters=filters))
    return grouped


class ArtworkDialog(QDialog):
    """Editable search plus per-slot selection for one desktop entry."""

    def __init__(
        self,
        app: DesktopApplication,
        dest_dir: Path,
        parent=None,
        *,
        client_factory: Callable[[], SteamGridDBClient] | None = None,
        inline_workers: bool = False,
        store: StateStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Artwork — {_display_name(app)}")
        self._app = app
        self._dest_dir = Path(dest_dir)
        self._client_factory = client_factory or SteamGridDBClient
        self._inline_workers = inline_workers
        self._pool = QThreadPool.globalInstance()
        self._busy = False
        self._choice = ArtworkChoice()
        self._assets: dict[str, list[GridAsset]] = {slot: [] for slot in SLOTS}
        self._preview_bytes: bytes | None = None
        self._preview_movie: QMovie | None = None
        self._preview_buffer: QBuffer | None = None
        self._preview_frames: list[QPixmap] = []
        self._preview_delays: list[int] = []
        self._preview_index = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._on_preview_tick)

        name = _display_name(app)
        queries = search_queries(name)
        initial = queries[0] if queries else name

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Search SteamGridDB, pick a game, then choose artwork for each "
                "slot. Filters match Steam ROM Manager: Static is on by default; "
                "Animated, NSFW, Joke, and Epilepsy are opt-in. If you do not "
                "care which art is used, Use first matches adds the first result "
                "in each slot. Skip or Cancel leaves the shortcut without custom art."
            )
        )

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit(initial)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText("Search SteamGridDB")
        self.search_button = QPushButton("Search")
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.filter_bar = ArtworkFilterBar(store=store)
        self.filter_bar.changed.connect(self._on_filters_changed)
        layout.addWidget(self.filter_bar)

        body = QHBoxLayout()
        self.game_list = QListWidget()
        self.game_list.setMinimumWidth(220)
        body.addWidget(self.game_list, 1)

        right = QVBoxLayout()
        self.tabs = QTabWidget()
        self._lists: dict[str, QListWidget] = {}
        for slot in SLOTS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            listing = QListWidget()
            listing.itemSelectionChanged.connect(self._on_asset_selected)
            self._lists[slot] = listing
            page_layout.addWidget(listing)
            self.tabs.addTab(page, _TAB_LABELS[slot])
        right.addWidget(self.tabs, 1)
        self.preview = QLabel("No preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 160)
        self.preview.setWordWrap(True)
        right.addWidget(self.preview)
        body.addLayout(right, 2)
        layout.addLayout(body, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Skip artwork")
        self.skip_remaining_button = QPushButton("Skip remaining apps")
        self.use_first_button = QPushButton("Use first matches")
        self.use_first_button.setToolTip(
            "Add the first SteamGridDB result in each slot. Use this when you "
            "do not care which artwork is picked."
        )
        self.use_button = QPushButton("Use selected")
        self.use_button.setToolTip(
            "Download the highlighted image in each tab. If nothing is "
            "highlighted, the first result in each slot is used."
        )
        self.cancel_button = QPushButton("Cancel")
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.skip_remaining_button)
        buttons.addStretch(1)
        buttons.addWidget(self.use_first_button)
        buttons.addWidget(self.use_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.search_button.clicked.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._on_search)
        self.game_list.itemSelectionChanged.connect(self._on_game_selected)
        self.skip_button.clicked.connect(self._skip)
        self.skip_remaining_button.clicked.connect(self._skip_remaining)
        self.use_first_button.clicked.connect(self._use_first_matches)
        self.use_button.clicked.connect(self._use_selected)
        self.cancel_button.clicked.connect(self._skip)
        self.resize(900, 640)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_opened", False):
            self._opened = True
            if self.search_edit.text().strip():
                self._on_search()

    def choice(self) -> ArtworkChoice:
        return self._choice

    def reject(self) -> None:
        if self._choice.action == ACTION_USE and self._choice.files:
            super().reject()
            return
        self._choice = ArtworkChoice(action=ACTION_SKIP)
        super().reject()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.search_button.setEnabled(not busy)
        self.search_edit.setEnabled(not busy)
        self.filter_bar.setEnabled(not busy)
        self.game_list.setEnabled(not busy)
        self.use_first_button.setEnabled(not busy)
        self.use_button.setEnabled(not busy)
        self.skip_button.setEnabled(not busy)
        self.skip_remaining_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        if message:
            self.status.setText(message)

    def _submit(self, fn: Callable[[], object], on_ok, on_err) -> None:
        if self._inline_workers:
            try:
                on_ok(fn())
            except Exception as error:  # noqa: BLE001
                on_err(str(error))
            return
        worker = CallableWorker(fn)
        worker.signals.finished.connect(on_ok)
        worker.signals.failed.connect(on_err)
        self._pool.start(worker)

    def _clear_assets(self) -> None:
        self._assets = {slot: [] for slot in SLOTS}
        for listing in self._lists.values():
            listing.blockSignals(True)
            listing.clear()
            listing.blockSignals(False)
        self._clear_preview()
        self.preview.setText("No preview")

    def _with_client(self, fn):
        client = self._client_factory()
        enter = getattr(client, "__enter__", None)
        if enter is not None:
            client = enter()
        try:
            return fn(client)
        finally:
            closer = getattr(client, "__exit__", None)
            if closer is not None:
                closer(None, None, None)
            else:
                close = getattr(client, "close", None)
                if close is not None:
                    close()

    def _on_search(self) -> None:
        if self._busy:
            return
        query = self.search_edit.text().strip()
        if not query:
            self.status.setText("Enter a search term.")
            return
        self.game_list.clear()
        self._clear_assets()
        self._set_busy(True, "Searching SteamGridDB…")

        def work() -> list[GameResult]:
            return self._with_client(lambda client: client.search_games(query))

        self._submit(work, self._on_search_done, self._on_worker_failed)

    def _on_search_done(self, games: object) -> None:
        self._set_busy(False)
        self.game_list.clear()
        assert isinstance(games, list)
        if not games:
            self.status.setText("No games found.")
            return
        for game in games:
            if not isinstance(game, GameResult):
                continue
            item = QListWidgetItem(game.name)
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.game_list.addItem(item)
        self.game_list.setCurrentRow(0)
        self.status.setText(f"{self.game_list.count()} game(s).")

    def _on_game_selected(self) -> None:
        if self._busy:
            return
        item = self.game_list.currentItem()
        if item is None:
            return
        game = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(game, GameResult):
            return
        self._clear_assets()
        self._set_busy(True, f"Loading artwork for {game.name}…")

        def work() -> dict[str, list[GridAsset]]:
            filters = self.filter_bar.filters()
            return self._with_client(
                lambda client: _group_assets(client, game.id, filters)
            )

        self._submit(work, self._on_assets_done, self._on_assets_failed)

    def _on_filters_changed(self) -> None:
        if self._busy or self.game_list.currentItem() is None:
            return
        self._on_game_selected()

    def _on_assets_done(self, grouped: object) -> None:
        self._set_busy(False)
        assert isinstance(grouped, dict)
        self._clear_assets()
        self._assets = grouped
        for slot, listing in self._lists.items():
            listing.blockSignals(True)
            listing.clear()
            for asset in grouped.get(slot, []):
                row = QListWidgetItem(_asset_label(asset))
                row.setData(Qt.ItemDataRole.UserRole, asset)
                listing.addItem(row)
            listing.blockSignals(False)
        self._clear_preview()
        self.preview.setText("Select an image to preview.")
        self.status.setText(
            "Choose artwork, or Use first matches if you do not care which "
            "result is picked."
        )

    def _on_assets_failed(self, message: str) -> None:
        self._set_busy(False)
        self._clear_assets()
        self.status.setText(message)

    def _selected_asset(self) -> GridAsset | None:
        listing = self._lists[SLOTS[self.tabs.currentIndex()]]
        item = listing.currentItem()
        if item is None:
            return None
        asset = item.data(Qt.ItemDataRole.UserRole)
        return asset if isinstance(asset, GridAsset) else None

    def _on_asset_selected(self) -> None:
        if self._busy:
            return
        asset = self._selected_asset()
        if asset is None:
            return
        self._set_busy(True, "Loading preview…")

        def work() -> tuple[bytes, GridAsset]:
            def download(client: SteamGridDBClient) -> tuple[bytes, GridAsset]:
                path = self._dest_dir / f"preview-{asset.id}"
                client.download_asset(asset, path, preview=True)
                return path.read_bytes(), asset

            return self._with_client(download)

        self._submit(work, self._on_preview_done, self._on_preview_failed)

    def _clear_preview(self) -> None:
        self._preview_timer.stop()
        self._preview_frames = []
        self._preview_delays = []
        self._preview_index = 0
        if self._preview_movie is not None:
            self._preview_movie.stop()
        self.preview.setMovie(QMovie())
        self._preview_movie = None
        self._preview_buffer = None
        self.preview.setPixmap(QPixmap())
        self._preview_bytes = None

    def _show_preview_movie(self, payload: bytes) -> bool:
        buffer = QBuffer()
        buffer.setData(QByteArray(payload))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return False
        movie = QMovie()
        movie.setDevice(buffer)
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        if not movie.isValid() or not movie.jumpToFrame(0):
            return False
        frame = movie.currentPixmap()
        if frame.isNull():
            return False
        box = self.preview.size()
        if box.width() > 0 and box.height() > 0:
            movie.setScaledSize(
                frame.size().scaled(box, Qt.AspectRatioMode.KeepAspectRatio)
            )
        self._preview_buffer = buffer
        self._preview_movie = movie
        self.preview.setMovie(movie)
        movie.start()
        return True

    def _show_preview_apng(self, payload: bytes) -> bool:
        animation = parse_apng(payload)
        if animation is None:
            return False
        frames = _compose_apng(animation, self.preview.size())
        if frames is None:
            return False
        pixmaps, delays = frames
        self._preview_frames = pixmaps
        self._preview_delays = delays
        self._preview_index = 0
        self.preview.setPixmap(pixmaps[0])
        if len(pixmaps) > 1:
            self._preview_timer.start(delays[0])
        return True

    def _on_preview_tick(self) -> None:
        if not self._preview_frames:
            return
        self._preview_index = (self._preview_index + 1) % len(self._preview_frames)
        self.preview.setPixmap(self._preview_frames[self._preview_index])
        self._preview_timer.start(self._preview_delays[self._preview_index])

    def _on_preview_done(self, result: object) -> None:
        self._set_busy(False)
        if not isinstance(result, tuple) or len(result) != 2:
            self._clear_preview()
            self.preview.setText("Preview unavailable.")
            return
        payload, asset = result
        if not isinstance(payload, (bytes, bytearray)) or not isinstance(asset, GridAsset):
            self._clear_preview()
            self.preview.setText("Preview unavailable.")
            return
        data = bytes(payload)
        self._clear_preview()
        kind = sniff_image(data)
        if kind in {"gif", "webp"} and self._show_preview_movie(data):
            self._preview_bytes = data
            self.status.setText("")
            return
        if kind == "png" and self._show_preview_apng(data):
            self._preview_bytes = data
            self.status.setText("")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.preview.setText("Preview could not be decoded.")
            return
        self._preview_bytes = data
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.status.setText("")

    def _on_preview_failed(self, message: str) -> None:
        self._set_busy(False)
        self._clear_preview()
        self.preview.setText("Preview unavailable.")
        self.status.setText(message)

    def _on_worker_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText(message)

    def _selections(self) -> dict[str, GridAsset]:
        chosen: dict[str, GridAsset] = {}
        for slot, listing in self._lists.items():
            item = listing.currentItem()
            if item is None:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(asset, GridAsset):
                chosen[slot] = asset
        return chosen

    def _first_assets(self) -> dict[str, GridAsset]:
        """First SteamGridDB result in each slot that has any artwork."""
        chosen: dict[str, GridAsset] = {}
        for slot in SLOTS:
            assets = self._assets.get(slot) or []
            if assets:
                chosen[slot] = assets[0]
                continue
            listing = self._lists[slot]
            if listing.count() == 0:
                continue
            asset = listing.item(0).data(Qt.ItemDataRole.UserRole)
            if isinstance(asset, GridAsset):
                chosen[slot] = asset
        return chosen

    def _use_selected(self) -> None:
        selected = self._selections()
        if selected:
            self._download_assets(selected, try_next=False)
            return
        self._download_assets(self._first_assets(), try_next=True)

    def _use_first_matches(self) -> None:
        self._download_assets(self._first_assets(), try_next=True)

    def _download_assets(
        self, chosen: dict[str, GridAsset], *, try_next: bool = False
    ) -> None:
        if self._busy:
            return
        if not chosen:
            self.status.setText("No artwork available to use.")
            return
        self._set_busy(True, "Downloading artwork…")
        dest_dir = self._dest_dir
        desktop_id = self._app.desktop_id
        slot_lists = {
            slot: list(self._assets.get(slot) or []) for slot in chosen
        }

        def work() -> dict[str, Path]:
            def download(client: SteamGridDBClient) -> dict[str, Path]:
                files: dict[str, Path] = {}
                errors: list[str] = []
                for slot, asset in chosen.items():
                    candidates = [asset]
                    if try_next:
                        rest = [item for item in slot_lists.get(slot, []) if item.id != asset.id]
                        candidates = [asset, *rest]
                    last_error: Exception | None = None
                    for candidate in candidates:
                        path = dest_dir / f"{desktop_id}_{slot}_{candidate.id}"
                        try:
                            client.download_asset(candidate, path)
                        except Exception as error:  # noqa: BLE001 — try next / other slots
                            last_error = error
                            continue
                        files[slot] = path
                        last_error = None
                        break
                    if last_error is not None:
                        errors.append(f"{_TAB_LABELS[slot]}: {last_error}")
                if not files:
                    raise RuntimeError(
                        errors[0] if errors else "Nothing could be downloaded."
                    )
                return files

            return self._with_client(download)

        self._submit(work, self._on_download_done, self._on_worker_failed)

    def _on_download_done(self, files: object) -> None:
        self._set_busy(False)
        if not isinstance(files, Mapping) or not files:
            self.status.setText("Nothing could be downloaded.")
            return
        self._choice = ArtworkChoice(action=ACTION_USE, files=dict(files))
        self.accept()

    def _skip(self) -> None:
        self._choice = ArtworkChoice(action=ACTION_SKIP)
        self.accept()

    def _skip_remaining(self) -> None:
        self._choice = ArtworkChoice(action=ACTION_SKIP_REMAINING)
        self.accept()
