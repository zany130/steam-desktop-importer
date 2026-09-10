"""Phase 6 VDF document: load, update, create, serialize (IMPLEMENTATION.md §15–§17)."""

from __future__ import annotations

from pathlib import Path

import pytest
import vdf

from steam_desktop_importer.steam.appid import uint32_to_int32
from steam_desktop_importer.steam.shortcuts import (
    STEAM_NEW_ENTRY_KEYS,
    AppIdNotFoundError,
    ShortcutDocument,
    format_exe,
    format_launch_options,
    format_start_dir,
    quote_steam_token,
)

LOADABLE = (
    "empty.vdf",
    "single_steam_written.vdf",
    "mixed_writers.vdf",
    "unknown_fields.vdf",
    "appid_boundaries.vdf",
    "noncontiguous_indices.vdf",
    "unicode_names.vdf",
)


@pytest.mark.parametrize("name", LOADABLE)
def test_fixture_load_dumps_is_byte_identical(shortcuts_vdf_dir, name):
    path = shortcuts_vdf_dir / name
    raw = path.read_bytes()
    document = ShortcutDocument.load(path)
    assert document.dumps() == raw


def test_missing_file_is_an_empty_document(tmp_path):
    document = ShortcutDocument.load(tmp_path / "nope.vdf")
    assert document.entries() == []
    assert document.indices() == []


def test_unparsable_file_is_an_error(shortcuts_vdf_dir):
    with pytest.raises(ValueError, match="unparsable"):
        ShortcutDocument.load(shortcuts_vdf_dir / "not_a_vdf.vdf")
    with pytest.raises(ValueError, match="unparsable"):
        ShortcutDocument.load(shortcuts_vdf_dir / "truncated.vdf")


def test_update_by_appid_preserves_unrelated_entries_and_unknown_fields(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "mixed_writers.vdf")
    before = vdf.binary_loads(document.dumps())
    updated = document.update_by_appid(
        0x87B9685F,
        name="Renamed Steam Entry",
        exe="/usr/bin/example-app",
        start_dir="/usr/bin/",
        launch_options="--updated",
    )
    assert updated.name == "Renamed Steam Entry"
    assert updated.exe == '"/usr/bin/example-app"'
    assert updated.launch_options == "--updated"

    after = vdf.binary_loads(document.dumps())
    assert list(after["shortcuts"]) == ["0", "1", "2"]
    assert after["shortcuts"]["1"] == before["shortcuts"]["1"]
    assert after["shortcuts"]["2"] == before["shortcuts"]["2"]
    assert list(after["shortcuts"]["0"]) == list(before["shortcuts"]["0"])
    assert after["shortcuts"]["0"]["sortas"] == ""
    assert after["shortcuts"]["1"]["tags"] == before["shortcuts"]["1"]["tags"]
    assert "sortas" not in after["shortcuts"]["1"]


def test_update_does_not_add_sortas_to_a_tool_written_entry(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "mixed_writers.vdf")
    document.update_by_appid(0xD6F081EC, name="Still Tool Written")
    raw = vdf.binary_loads(document.dumps())["shortcuts"]["1"]
    assert "sortas" not in raw
    assert raw["AppName"] == "Still Tool Written"


def test_unknown_fields_survive_an_update(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "unknown_fields.vdf")
    document.update_by_appid(0xB4CF2875, name="Still Unknown")
    raw = vdf.binary_loads(document.dumps())["shortcuts"]["0"]
    assert raw["SomeFutureValveField"] == "keep me"
    assert raw["SomeFutureIntField"] == 42
    assert raw["SomeFutureNestedObject"] == {"a": "1", "b": {"c": "2"}}
    assert raw["AppName"] == "Still Unknown"


def test_update_missing_appid_is_an_error(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "single_steam_written.vdf")
    with pytest.raises(AppIdNotFoundError):
        document.update_by_appid(0x11111111, name="Nope")


def test_new_entry_uses_the_steam_written_fixture_schema(shortcuts_vdf_dir):
    document = ShortcutDocument.empty()
    entry = document.add_new(
        appid_unsigned=0x80000001,
        name="Imported App",
        exe="/usr/bin/imported",
        start_dir="/opt/app",
        launch_options=format_launch_options(["--flag", "two words"]),
    )
    assert entry.index == "0"
    assert entry.appid_unsigned == 0x80000001
    assert list(document.raw()["shortcuts"]["0"]) == list(STEAM_NEW_ENTRY_KEYS)
    steam_written = ShortcutDocument.load(shortcuts_vdf_dir / "single_steam_written.vdf")
    assert list(document.raw()["shortcuts"]["0"]) == list(
        steam_written.raw()["shortcuts"]["0"]
    )
    raw = document.raw()["shortcuts"]["0"]
    assert raw["appid"] == uint32_to_int32(0x80000001)
    assert raw["LastPlayTime"] == 0
    assert raw["ShortcutPath"] == ""
    assert raw["FlatpakAppID"] == ""
    assert raw["tags"] == {}
    assert raw["sortas"] == ""
    assert raw["Exe"] == '"/usr/bin/imported"'
    assert raw["StartDir"] == '"/opt/app"'
    assert raw["LaunchOptions"] == '--flag "two words"'

    reloaded = ShortcutDocument.loads(document.dumps())
    assert reloaded.entries()[0].name == "Imported App"


def test_new_entry_appends_without_filling_index_holes(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "noncontiguous_indices.vdf")
    assert document.indices() == ["0", "2", "5"]
    entry = document.add_new(
        appid_unsigned=0x80000002,
        name="Appended",
        exe="/usr/bin/new",
    )
    assert entry.index == "6"
    assert document.indices() == ["0", "2", "5", "6"]
    reloaded = ShortcutDocument.loads(document.dumps())
    assert reloaded.indices() == ["0", "2", "5", "6"]
    assert [item.name for item in reloaded.entries()] == [
        "Index Zero",
        "Index Two",
        "Index Five",
        "Appended",
    ]


def test_add_new_refuses_a_duplicate_appid(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "single_steam_written.vdf")
    with pytest.raises(ValueError, match="already used"):
        document.add_new(appid_unsigned=0x87B9685F, name="Dup", exe="/bin/dup")


def test_unicode_survives_update_then_reload(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "unicode_names.vdf")
    document.update_by_appid(0xDFF14325, launch_options="--café")
    reloaded = ShortcutDocument.loads(document.dumps())
    names = [entry.name for entry in reloaded.entries()]
    assert names == ["Café Player — Deluxe", "日本語のアプリ"]
    assert reloaded.entries()[0].launch_options == "--café"


def test_possible_matches_are_heuristic_only(shortcuts_vdf_dir):
    document = ShortcutDocument.load(shortcuts_vdf_dir / "single_steam_written.vdf")
    matches = document.possible_matches("Example Application", "/usr/bin/example-app")
    assert len(matches) == 1
    assert matches[0].entry.appid_unsigned == 0x87B9685F
    assert matches[0].launch_options_match is False

    strong = document.possible_matches(
        "Example Application",
        "/usr/bin/example-app",
        launch_options="",
    )
    assert strong[0].launch_options_match is True

    assert document.possible_matches("Example Application", "/usr/bin/other") == []
    # A match never inserts a mapping and never mutates the document.
    assert document.dumps() == (shortcuts_vdf_dir / "single_steam_written.vdf").read_bytes()


def test_update_preserves_a_short_lowercase_writer_schema():
    """Steam ROM Manager-style entries use ``appname``/``exe`` and fewer keys."""
    document = ShortcutDocument(
        {
            "shortcuts": {
                "0": {
                    "LaunchOptions": "",
                    "StartDir": "/roms",
                    "appid": uint32_to_int32(0x80000010),
                    "appname": "Rom",
                    "exe": '"/roms/game.desktop"',
                    "icon": "/roms/game.png",
                    "tags": {},
                }
            }
        }
    )
    document.update_by_appid(0x80000010, name="Rom Renamed", exe="/roms/game.desktop")
    raw = document.raw()["shortcuts"]["0"]
    assert list(raw) == [
        "LaunchOptions",
        "StartDir",
        "appid",
        "appname",
        "exe",
        "icon",
        "tags",
    ]
    assert "AppName" not in raw
    assert "Exe" not in raw
    assert raw["appname"] == "Rom Renamed"
    assert raw["exe"] == '"/roms/game.desktop"'


def test_quoting_helpers():
    assert format_exe("/usr/bin/app") == '"/usr/bin/app"'
    assert format_exe('"/usr/bin/app"') == '"/usr/bin/app"'
    assert format_start_dir("") == ""
    assert format_start_dir("/home/user/Game") == '"/home/user/Game"'
    assert quote_steam_token("plain") == "plain"
    assert quote_steam_token("two words") == '"two words"'
    assert format_launch_options(["--flag", "two words", ""]) == '--flag "two words" ""'
