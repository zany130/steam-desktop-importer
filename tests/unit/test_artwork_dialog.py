"""Phase 9 artwork dialog: editable search and skip without network."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLineEdit

from steam_desktop_importer.steamgriddb.models import GameResult, GridAsset
from steam_desktop_importer.ui.artwork_dialog import (
    ACTION_SKIP,
    ACTION_SKIP_REMAINING,
    ACTION_USE,
    ArtworkDialog,
)

from .test_gui_window import _gui_app


@pytest.fixture(scope="module")
def qapp():
    existing = QApplication.instance()
    if existing is not None:
        yield existing
        return
    app = QApplication([])
    yield app
    app.quit()


class FakeClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.searches: list[str] = []
        self.downloads: list[int] = []
        self.grid_filters: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def search_games(self, query: str):
        self.searches.append(query)
        return [GameResult(id=7, name="Example Game")]

    def get_grids(self, game_id: int, dimensions=None, **kwargs):
        self.grid_filters.append(kwargs.get("filters"))
        return [
            GridAsset(
                id=11,
                kind="grid",
                url="https://cdn.example/p.png",
                thumb="https://cdn.example/p.thumb.png",
                width=600,
                height=900,
            ),
            GridAsset(
                id=12,
                kind="grid",
                url="https://cdn.example/p2.png",
                thumb="https://cdn.example/p2.thumb.png",
                width=600,
                height=900,
            ),
        ]

    def get_heroes(self, game_id: int, **kwargs):
        return []

    def get_logos(self, game_id: int, **kwargs):
        return []

    def get_icons(self, game_id: int, **kwargs):
        return [
            GridAsset(
                id=22,
                kind="icon",
                url="https://cdn.example/icon.png",
                width=32,
                height=32,
            )
        ]

    def download_asset(self, asset, temp_path, **kwargs):
        path = Path(temp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._payload)
        self.downloads.append(asset.id)
        return path


def test_artwork_dialog_search_is_editable_and_skip_skips(qapp, tmp_path):
    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FakeClient(b""),
    )
    assert dialog.search_edit.isReadOnly() is False
    assert dialog.search_edit.echoMode() == QLineEdit.EchoMode.Normal
    assert dialog.search_edit.text() == "Example"
    dialog.search_edit.setText("Kate (Flatpak)")
    assert dialog.search_edit.text() == "Kate (Flatpak)"
    dialog.skip_button.click()
    assert dialog.choice().action == ACTION_SKIP
    assert dialog.choice().files == {}
    dialog.close()


def test_artwork_dialog_skip_remaining(qapp, tmp_path):
    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FakeClient(b""),
    )
    dialog.skip_remaining_button.click()
    assert dialog.choice().action == ACTION_SKIP_REMAINING
    dialog.close()


def test_artwork_dialog_use_selected_downloads_temps(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = FakeClient(png)
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    dialog = ArtworkDialog(
        app,
        art_dir,
        inline_workers=True,
        client_factory=lambda: client,
    )
    dialog._on_search()
    assert dialog.game_list.count() == 1
    listing = dialog._lists["portrait"]
    assert listing.count() == 2
    listing.setCurrentRow(0)
    dialog._lists["icon"].setCurrentRow(0)
    dialog.use_button.click()
    choice = dialog.choice()
    assert choice.action == ACTION_USE
    assert "portrait" in choice.files
    assert "icon" in choice.files
    assert choice.files["portrait"].read_bytes() == png
    dialog.close()


def test_use_selected_with_no_picks_takes_the_first_in_each_slot(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = FakeClient(png)
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    dialog = ArtworkDialog(
        app,
        art_dir,
        inline_workers=True,
        client_factory=lambda: client,
    )
    dialog._on_search()
    assert dialog._lists["portrait"].currentItem() is None
    assert dialog._lists["icon"].currentItem() is None
    dialog.use_button.click()
    choice = dialog.choice()
    assert choice.action == ACTION_USE
    assert set(choice.files) == {"portrait", "icon"}
    assert client.downloads == [11, 22]
    dialog.close()


def test_use_first_matches_ignores_later_picks(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = FakeClient(png)
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    dialog = ArtworkDialog(
        app,
        art_dir,
        inline_workers=True,
        client_factory=lambda: client,
    )
    dialog._on_search()
    dialog._lists["portrait"].setCurrentRow(1)
    client.downloads.clear()
    dialog.use_first_button.click()
    choice = dialog.choice()
    assert choice.action == ACTION_USE
    assert set(choice.files) == {"portrait", "icon"}
    assert client.downloads == [11, 22]
    dialog.close()


def test_artwork_dialog_cancel_is_skip(qapp, tmp_path):
    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FakeClient(b""),
    )
    dialog.cancel_button.click()
    assert dialog.choice().action == ACTION_SKIP
    dialog.close()


def test_cancel_after_results_still_skips(qapp, tmp_path):
    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FakeClient(b""),
    )
    dialog._on_search()
    dialog.cancel_button.click()
    assert dialog.choice().action == ACTION_SKIP
    assert dialog.choice().files == {}
    dialog.close()


def test_asset_load_failure_clears_previous_assets(qapp, tmp_path):
    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FakeClient(b""),
    )
    dialog._on_search()
    assert dialog._first_assets()
    dialog._on_assets_failed("boom")
    assert dialog._first_assets() == {}
    assert dialog.status.text() == "boom"
    dialog.close()


def test_partial_download_failure_keeps_successful_slots(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )

    class PartialFailClient(FakeClient):
        def download_asset(self, asset, temp_path, **kwargs):
            if asset.id == 22:
                raise RuntimeError("icon failed")
            return super().download_asset(asset, temp_path, **kwargs)

    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: PartialFailClient(png),
    )
    dialog._on_search()
    dialog.use_first_button.click()
    choice = dialog.choice()
    assert choice.action == ACTION_USE
    assert "portrait" in choice.files
    assert "icon" not in choice.files
    dialog.close()


def test_all_download_failures_keep_dialog_open(qapp, tmp_path):
    class FailClient(FakeClient):
        def download_asset(self, asset, temp_path, **kwargs):
            raise RuntimeError("nope")

    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: FailClient(b""),
    )
    dialog._on_search()
    dialog.use_first_button.click()
    assert dialog.choice().action == ACTION_SKIP
    assert dialog.choice().files == {}
    assert "nope" in dialog.status.text()
    dialog.close()


def test_first_matches_skips_oversized_and_uses_next(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )

    class OversizedHeroClient(FakeClient):
        def get_heroes(self, game_id: int, **kwargs):
            return [
                GridAsset(
                    id=31,
                    kind="hero",
                    url="https://cdn.example/h1.png",
                    width=1920,
                    height=620,
                ),
                GridAsset(
                    id=32,
                    kind="hero",
                    url="https://cdn.example/h2.png",
                    width=1920,
                    height=620,
                ),
            ]

        def get_icons(self, game_id: int, **kwargs):
            return []

        def download_asset(self, asset, temp_path, **kwargs):
            if asset.id == 31:
                raise RuntimeError("artwork is 47.3 MB (limit 20 MB)")
            return super().download_asset(asset, temp_path, **kwargs)

    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: OversizedHeroClient(png),
    )
    dialog._on_search()
    dialog.use_first_button.click()
    choice = dialog.choice()
    assert choice.action == ACTION_USE
    assert "hero" in choice.files
    assert choice.files["hero"].name.endswith("_32")
    dialog.close()


def test_artwork_dialog_defaults_exclude_nsfw_and_reloads_when_enabled(qapp, tmp_path):
    from steam_desktop_importer.state import StateStore
    from steam_desktop_importer.steamgriddb.filters import ArtworkFilters

    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = FakeClient(b"")
    store = StateStore(":memory:")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: client,
        store=store,
    )
    assert dialog.filter_bar.static.isChecked() is True
    assert dialog.filter_bar.nsfw.isChecked() is False
    dialog._on_search()
    assert client.grid_filters
    first = client.grid_filters[-1]
    assert isinstance(first, ArtworkFilters)
    assert first.allow_nsfw is False
    dialog.filter_bar.nsfw.setChecked(True)
    assert store.artwork_filters().allow_nsfw is True
    assert client.grid_filters[-1].allow_nsfw is True
    dialog.close()


def test_preview_skips_webm_thumbs(qapp, tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )

    class WebmThumbClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(png)
            self.preview_flags: list[bool] = []

        def get_grids(self, game_id: int, dimensions=None, **kwargs):
            return [
                GridAsset(
                    id=116506,
                    kind="grid",
                    url="https://cdn.example/grid.png",
                    thumb="https://cdn.example/thumb.webm",
                    width=600,
                    height=900,
                    mime="image/png",
                    animated=True,
                )
            ]

        def get_icons(self, game_id: int, **kwargs):
            return []

        def download_asset(self, asset, temp_path, **kwargs):
            self.preview_flags.append(bool(kwargs.get("preview")))
            from steam_desktop_importer.steamgriddb.client import (
                asset_download_url,
                preview_download_url,
            )

            url = (
                preview_download_url(asset)
                if kwargs.get("preview")
                else asset_download_url(asset)
            )
            assert not url.endswith(".webm")
            return super().download_asset(asset, temp_path, **kwargs)

    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = WebmThumbClient()
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: client,
    )
    dialog._on_search()
    dialog._lists["portrait"].setCurrentRow(0)
    assert client.preview_flags == [True]
    assert dialog.preview.pixmap() is not None
    assert not dialog.preview.pixmap().isNull()
    dialog.close()


def test_apng_preview_plays_frames(qapp, tmp_path):
    from .test_apng import two_frame_apng

    class ApngClient(FakeClient):
        def get_icons(self, game_id: int, **kwargs):
            return []

    app = _gui_app(tmp_path / "org.example.App.desktop")
    client = ApngClient(two_frame_apng())
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: client,
    )
    dialog._on_search()
    dialog._lists["portrait"].setCurrentRow(0)
    assert "first frame" not in dialog.status.text()
    assert len(dialog._preview_frames) == 2
    assert dialog._preview_timer.isActive()
    first = QPixmap(dialog.preview.pixmap())
    dialog._on_preview_tick()
    assert dialog._preview_index == 1
    second = dialog.preview.pixmap()
    assert first.toImage() != second.toImage()
    dialog.close()


def test_artwork_dialog_keeps_all_grid_results(qapp, tmp_path):
    class ManyGrids(FakeClient):
        def get_grids(self, game_id: int, dimensions=None, **kwargs):
            self.grid_filters.append(kwargs.get("filters"))
            return [
                GridAsset(
                    id=index,
                    kind="grid",
                    url=f"https://cdn.example/{index}.png",
                    width=600,
                    height=900,
                )
                for index in range(30)
            ]

        def get_icons(self, game_id: int, **kwargs):
            return []

    app = _gui_app(tmp_path / "org.example.App.desktop")
    dialog = ArtworkDialog(
        app,
        tmp_path,
        inline_workers=True,
        client_factory=lambda: ManyGrids(b""),
    )
    dialog._on_search()
    assert dialog._lists["portrait"].count() == 30
    dialog.close()
