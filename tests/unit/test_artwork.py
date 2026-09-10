"""Phase 9 artwork naming and atomic grid placement."""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.state import StateStore
from steam_desktop_importer.steam.appid import first_import_candidate, game_id_64
from steam_desktop_importer.steam.artwork import (
    SLOT_HERO,
    SLOT_ICON,
    SLOT_LOGO,
    SLOT_PORTRAIT,
    SLOT_WIDE,
    artwork_filename,
    commit_artwork_files,
    destination_for,
    grid_dir,
    grid_id,
    place_artwork,
    slot_for_grid,
)
from steam_desktop_importer.steam.importing import apply_applications
from steam_desktop_importer.steam.shortcut_identities import shortcuts_vdf_path
from steam_desktop_importer.steam.shortcuts import ShortcutDocument, get_ci
from steam_desktop_importer.steamgriddb.images import sniff_image, steam_filename_extension

from .test_importing import CLOSED, _account, _hooks, _installation, make_app

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
WEBP_HEADER = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"\x00" * 4


def test_grid_id_is_unsigned_32_bit_not_game_id_64():
    appid = 0xF0000001
    assert grid_id(appid) == 0xF0000001
    assert grid_id(-268435455) == 0xF0000001
    name = artwork_filename(appid, SLOT_PORTRAIT, "png")
    assert name == "4026531841p.png"
    assert str(game_id_64(appid)) not in name
    assert str(game_id_64(appid)) not in artwork_filename(appid, SLOT_WIDE, "png")


def test_filename_conventions_are_decimal():
    appid = 12345
    assert artwork_filename(appid, SLOT_PORTRAIT, "png") == "12345p.png"
    assert artwork_filename(appid, SLOT_WIDE, "jpg") == "12345.jpg"
    assert artwork_filename(appid, SLOT_HERO, "png") == "12345_hero.png"
    assert artwork_filename(appid, SLOT_LOGO, "png") == "12345_logo.png"
    assert artwork_filename(appid, SLOT_ICON, "png") == "12345_icon.png"


def test_high_bit_appid_stays_decimal_unsigned():
    appid = first_import_candidate("org.example.App.desktop")
    assert appid >= 0x80000000
    assert artwork_filename(appid, SLOT_WIDE, "png") == f"{appid}.png"
    assert "-" not in artwork_filename(appid, SLOT_PORTRAIT, "png")


def test_webp_is_saved_as_png_name_with_webp_payload(tmp_path):
    assert steam_filename_extension("webp") == "png"
    account = _account(tmp_path / "Steam")
    source = tmp_path / "hero.webp"
    source.write_bytes(WEBP_HEADER)
    dest = destination_for(account, 99, SLOT_PORTRAIT, source)
    assert dest.name == "99p.png"
    placed = place_artwork(source, dest)
    assert placed.read_bytes() == WEBP_HEADER
    assert sniff_image(placed.read_bytes()) == "webp"


def test_place_artwork_does_not_delete_other_slots(tmp_path):
    account = _account(tmp_path / "Steam")
    grid = grid_dir(account)
    source = tmp_path / "wide.png"
    source.write_bytes(PNG_1X1)
    portrait = grid / "123p.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(PNG_1X1)
    place_artwork(source, grid / "123.png")
    assert portrait.is_file()
    assert (grid / "123.png").read_bytes() == PNG_1X1


def test_place_artwork_replaces_same_stem_other_extension(tmp_path):
    grid = tmp_path / "grid"
    grid.mkdir()
    stale = grid / "123.jpg"
    stale.write_bytes(b"stale")
    source = tmp_path / "wide.png"
    source.write_bytes(PNG_1X1)
    place_artwork(source, grid / "123.png")
    assert not stale.exists()
    assert (grid / "123.png").read_bytes() == PNG_1X1


def test_slot_for_grid_uses_known_sizes_then_aspect():
    assert slot_for_grid(width=600, height=900) == SLOT_PORTRAIT
    assert slot_for_grid(width=342, height=482) == SLOT_PORTRAIT
    assert slot_for_grid(width=460, height=215) == SLOT_WIDE
    assert slot_for_grid(width=920, height=430) == SLOT_WIDE
    assert slot_for_grid(width=100, height=200) == SLOT_PORTRAIT
    assert slot_for_grid(width=200, height=100) == SLOT_WIDE
    assert slot_for_grid(width=None, height=None) == SLOT_WIDE


def test_apply_applications_sets_icon_to_absolute_grid_path(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    app = make_app(tmp_path / "org.example.App.desktop")
    icon_src = tmp_path / "icon.png"
    portrait_src = tmp_path / "portrait.png"
    icon_src.write_bytes(PNG_1X1)
    portrait_src.write_bytes(PNG_1X1)
    store = StateStore(":memory:")
    result = apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
        artwork_files={
            app.desktop_id: {SLOT_ICON: icon_src, SLOT_PORTRAIT: portrait_src}
        },
    )
    assert result.artwork_errors == ()
    appid = first_import_candidate(app.desktop_id)
    expected_icon = grid_dir(account) / f"{appid}_icon.png"
    expected_portrait = grid_dir(account) / f"{appid}p.png"
    assert expected_icon.read_bytes() == PNG_1X1
    assert expected_portrait.read_bytes() == PNG_1X1
    _index, raw = ShortcutDocument.load(shortcuts_vdf_path(account))._raw_by_appid(appid)
    assert get_ci(raw, "icon") == str(expected_icon)
    assert Path(get_ci(raw, "icon")).is_absolute()


def test_artwork_failure_does_not_roll_back_vdf(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    app = make_app(tmp_path / "org.example.App.desktop")
    bad = tmp_path / "not-an-image.bin"
    bad.write_bytes(b"definitely not an image")
    store = StateStore(":memory:")
    result = apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        steam_status=CLOSED,
        hooks=_hooks(),
        artwork_files={app.desktop_id: {SLOT_PORTRAIT: bad}},
    )
    assert result.artwork_errors
    appid = first_import_candidate(app.desktop_id)
    document = ShortcutDocument.load(shortcuts_vdf_path(account))
    assert document.find_by_appid(appid) is not None
    assert store.get_mapping(installation.key, account.account_id32, app.desktop_id) is not None
    assert not (grid_dir(account) / f"{appid}p.png").exists()


def test_one_slot_failure_still_places_the_others(tmp_path):
    root = tmp_path / "Steam"
    installation = _installation(root)
    account = _account(root)
    app = make_app(tmp_path / "org.example.App.desktop")
    good = tmp_path / "hero.png"
    bad = tmp_path / "logo.bin"
    good.write_bytes(PNG_1X1)
    bad.write_bytes(b"nope")
    result = apply_applications(
        [app],
        installation=installation,
        account=account,
        store=StateStore(":memory:"),
        steam_status=CLOSED,
        hooks=_hooks(),
        artwork_files={app.desktop_id: {SLOT_HERO: good, SLOT_LOGO: bad}},
    )
    appid = first_import_candidate(app.desktop_id)
    assert (grid_dir(account) / f"{appid}_hero.png").is_file()
    assert any("logo" in error for error in result.artwork_errors)
    assert commit_artwork_files(account, {}, {}) == ()
