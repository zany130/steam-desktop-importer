"""Read-only shortcut identity listing."""

from __future__ import annotations

import pytest

from steam_desktop_importer.models import SteamAccount
from steam_desktop_importer.steam.shortcut_identities import (
    list_existing_shortcuts,
    normalize_exe,
    shortcuts_vdf_path,
)


def test_shortcuts_vdf_path_is_under_userdata(tmp_path):
    account = SteamAccount(
        steam_id64="1",
        account_id32=1,
        account_name=None,
        persona_name=None,
        userdata_dir=tmp_path / "1",
        selection_hints=[],
    )
    assert shortcuts_vdf_path(account) == tmp_path / "1" / "config" / "shortcuts.vdf"


def test_normalize_exe_strips_steam_style_quotes():
    assert normalize_exe('"/usr/bin/app"') == "/usr/bin/app"
    assert normalize_exe("/usr/bin/app") == "/usr/bin/app"


def test_empty_file_is_no_shortcuts(shortcuts_vdf_dir):
    assert list_existing_shortcuts(shortcuts_vdf_dir / "empty.vdf") == []


def test_single_fixture_exposes_unsigned_appid_and_fields(shortcuts_vdf_dir):
    shortcuts = list_existing_shortcuts(shortcuts_vdf_dir / "single_steam_written.vdf")
    assert len(shortcuts) == 1
    entry = shortcuts[0]
    assert entry.appid_unsigned & 0x80000000
    assert entry.name
    assert entry.exe


def test_mixed_writers_are_all_listed(shortcuts_vdf_dir):
    shortcuts = list_existing_shortcuts(shortcuts_vdf_dir / "mixed_writers.vdf")
    assert len(shortcuts) >= 2
    assert len({item.appid_unsigned for item in shortcuts}) == len(shortcuts)


def test_missing_file_is_empty(tmp_path):
    assert list_existing_shortcuts(tmp_path / "nope.vdf") == []


def test_unparsable_file_is_an_error(shortcuts_vdf_dir):
    with pytest.raises(ValueError, match="unparsable"):
        list_existing_shortcuts(shortcuts_vdf_dir / "not_a_vdf.vdf")
