#!/usr/bin/env python3
"""Phase 0 fixture generator for binary ``shortcuts.vdf`` files.

The generated files are **synthetic**. They are modelled on the real capture
documented in ``docs/PHASE0_FORMAT_CHARACTERIZATION.md`` (field names, casing,
key ordering, value types, signed AppID encoding) but contain no personal
data, so they are safe to commit.

Run from the repository root:

    python scripts/build_vdf_fixtures.py

Regenerating is idempotent. The generated ``.vdf`` files are committed so that
tests do not depend on running this script.
"""

from __future__ import annotations

import struct
from pathlib import Path

import vdf

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "shortcuts_vdf"


def uint32_to_int32(value: int) -> int:
    """Convert an unsigned 32-bit AppID to the signed form stored in the VDF.

    Duplicated locally on purpose: this generator must not depend on
    ``steam_desktop_importer.steam.appid``, which is Phase 5 work.
    """
    value &= 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def steam_style_entry(
    appid_unsigned: int,
    name: str,
    exe: str,
    start_dir: str = "",
    launch_options: str = "",
) -> dict[str, object]:
    """Key set and ordering observed from entries believed to be Steam-written.

    Distinguishing feature: ``sortas`` present, ``tags`` empty.
    """
    return {
        "appid": uint32_to_int32(appid_unsigned),
        "AppName": name,
        "Exe": exe,
        "StartDir": start_dir,
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "sortas": "",
        "tags": {},
    }


def third_party_style_entry(
    appid_unsigned: int,
    name: str,
    exe: str,
    start_dir: str = "",
    launch_options: str = "",
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    """Key set and ordering observed from entries believed to be tool-written.

    Distinguishing feature: no ``sortas``, populated ``tags``.
    """
    return {
        "appid": uint32_to_int32(appid_unsigned),
        "AppName": name,
        "Exe": exe,
        "StartDir": start_dir,
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 1725000000,
        "FlatpakAppID": "",
        "tags": {str(i): tag for i, tag in enumerate(tags)},
    }


def write(name: str, data: dict[str, object]) -> None:
    path = FIXTURE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        vdf.binary_dump(data, handle)
    print(f"wrote {path.relative_to(FIXTURE_DIR.parent.parent.parent)} ({path.stat().st_size} B)")


def build() -> None:
    # 1. No shortcuts at all. Steam creates this on a fresh account.
    write("empty.vdf", {"shortcuts": {}})

    # 2. A single entry using the Steam-written key set.
    write(
        "single_steam_written.vdf",
        {
            "shortcuts": {
                "0": steam_style_entry(
                    0x87B9685F,
                    "Example Application",
                    '"/usr/bin/example-app"',
                    start_dir='"/usr/bin/"',
                )
            }
        },
    )

    # 3. Two different writers in one file, with different key sets.
    #    This is the important preservation fixture: a naive normalising
    #    rewrite would add sortas to entry 1 or drop its tags.
    write(
        "mixed_writers.vdf",
        {
            "shortcuts": {
                "0": steam_style_entry(
                    0x87B9685F,
                    "Steam Written Entry",
                    '"/usr/bin/example-app"',
                    start_dir='"/usr/bin/"',
                ),
                "1": third_party_style_entry(
                    0xD6F081EC,
                    "Tool Written Entry",
                    '"/usr/bin/retro-emulator"',
                    start_dir='"/usr/bin/"',
                    launch_options='"/home/example/roms/game.bin"',
                    tags=("Emulation", "SNES", "Favourites"),
                ),
                "2": steam_style_entry(
                    0x912643A6,
                    "Another Steam Written Entry",
                    '"/opt/other/bin/other-app"',
                    start_dir='"/opt/other/bin/"',
                ),
            }
        },
    )

    # 4. Unknown / future fields, including a nested sub-object. Section 15
    #    requires these to survive a read-modify-write cycle untouched.
    unknown = steam_style_entry(
        0xB4CF2875,
        "Entry With Unknown Fields",
        '"/usr/bin/unknown-fields"',
    )
    unknown["SomeFutureValveField"] = "keep me"
    unknown["SomeFutureIntField"] = 42
    unknown["SomeFutureNestedObject"] = {"a": "1", "b": {"c": "2"}}
    write("unknown_fields.vdf", {"shortcuts": {"0": unknown}})

    # 5. AppID boundary values, to pin signed/unsigned conversion.
    write(
        "appid_boundaries.vdf",
        {
            "shortcuts": {
                "0": steam_style_entry(0x00000000, "Zero", '"/usr/bin/zero"'),
                "1": steam_style_entry(0x7FFFFFFF, "Max Positive Signed", '"/usr/bin/maxpos"'),
                "2": steam_style_entry(0x80000000, "Min High Bit", '"/usr/bin/minhigh"'),
                "3": steam_style_entry(0xFFFFFFFF, "All Ones", '"/usr/bin/allones"'),
            }
        },
    )

    # 6. Non-contiguous indices. Section 15 says not to renumber existing
    #    entries unnecessarily, so a round trip must not compact these.
    write(
        "noncontiguous_indices.vdf",
        {
            "shortcuts": {
                "0": steam_style_entry(0xF9B4DBAB, "Index Zero", '"/usr/bin/a"'),
                "2": steam_style_entry(0xA22357A6, "Index Two", '"/usr/bin/b"'),
                "5": steam_style_entry(0xF130AA31, "Index Five", '"/usr/bin/c"'),
            }
        },
    )

    # 7. Non-ASCII content, to pin the string encoding used on the wire.
    write(
        "unicode_names.vdf",
        {
            "shortcuts": {
                "0": steam_style_entry(0xDFF14325, "Café Player — Deluxe", '"/usr/bin/cafe"'),
                "1": steam_style_entry(0xE8465453, "日本語のアプリ", '"/usr/bin/nihongo"'),
            }
        },
    )

    # 8. A deliberately corrupt file: a valid file truncated mid-entry.
    #    Section 30 requires "unreadable or unparsable VDF" to be an explicit
    #    error, so the parser needs something that genuinely fails.
    good = FIXTURE_DIR / "mixed_writers.vdf"
    payload = good.read_bytes()
    truncated = FIXTURE_DIR / "truncated.vdf"
    truncated.write_bytes(payload[: len(payload) // 2])
    print(f"wrote tests/fixtures/shortcuts_vdf/truncated.vdf ({truncated.stat().st_size} B)")

    # 9. Not a binary VDF at all.
    (FIXTURE_DIR / "not_a_vdf.vdf").write_bytes(b"this is plainly not binary keyvalues\n")
    print("wrote tests/fixtures/shortcuts_vdf/not_a_vdf.vdf")

    # Sanity check: everything we claim is loadable must load.
    for path in sorted(FIXTURE_DIR.glob("*.vdf")):
        if path.name in {"truncated.vdf", "not_a_vdf.vdf"}:
            continue
        with path.open("rb") as handle:
            vdf.binary_load(handle)

    # Sanity check: the signed encoding really is what we expect on the wire.
    with (FIXTURE_DIR / "appid_boundaries.vdf").open("rb") as handle:
        loaded = vdf.binary_load(handle)["shortcuts"]
    assert loaded["2"]["appid"] == -0x80000000, loaded["2"]["appid"]
    assert loaded["3"]["appid"] == -1, loaded["3"]["appid"]
    assert struct.pack("<i", loaded["3"]["appid"]) == b"\xff\xff\xff\xff"


if __name__ == "__main__":
    build()
