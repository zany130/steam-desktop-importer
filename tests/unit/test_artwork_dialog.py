"""Phase 9 artwork dialog: editable search and skip without network."""

from __future__ import annotations

from pathlib import Path

import pytest
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

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def search_games(self, query: str):
        self.searches.append(query)
        return [GameResult(id=7, name="Example Game")]

    def get_grids(self, game_id: int, dimensions=None):
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

    def get_heroes(self, game_id: int):
        return []

    def get_logos(self, game_id: int):
        return []

    def get_icons(self, game_id: int):
        return [
            GridAsset(
                id=22,
                kind="icon",
                url="https://cdn.example/icon.png",
                width=32,
                height=32,
            )
        ]

    def download_asset(self, asset, temp_path):
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
