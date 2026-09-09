"""AppID allocation (IMPLEMENTATION.md §16, Phase 5)."""

from __future__ import annotations

import pytest

from steam_desktop_importer.steam.appid import (
    HIGH_BIT,
    AppIdAllocationError,
    allocate_appid,
    first_import_candidate,
    game_id_64,
    int32_to_uint32,
    uint32_to_int32,
)


def test_first_import_candidate_is_stable_and_high_bit():
    first = first_import_candidate("org.example.App.desktop")
    second = first_import_candidate("org.example.App.desktop")
    assert first == second
    assert first & HIGH_BIT
    assert first == first & 0xFFFFFFFF


def test_name_and_exec_are_not_part_of_the_seed():
    """§16: the candidate is independent of normal Name= / Exec= updates."""
    assert first_import_candidate("same.desktop") == first_import_candidate("same.desktop")


def test_different_desktop_ids_usually_differ():
    assert first_import_candidate("a.desktop") != first_import_candidate("b.desktop")


def test_signed_unsigned_round_trip():
    for unsigned in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
        signed = uint32_to_int32(unsigned)
        assert int32_to_uint32(signed) == unsigned
        if unsigned >= HIGH_BIT:
            assert signed < 0


def test_game_id_64_uses_the_specified_low_bits():
    assert game_id_64(0x80000001) == (0x80000001 << 32) | 0x02000000


def test_allocate_returns_the_first_candidate_when_free():
    desktop_id = "org.example.Free.desktop"
    assert allocate_appid(desktop_id) == first_import_candidate(desktop_id)


def test_allocate_salts_when_the_candidate_is_occupied():
    desktop_id = "org.example.Taken.desktop"
    first = first_import_candidate(desktop_id)
    allocated = allocate_appid(desktop_id, occupied={first})
    assert allocated != first
    assert allocated == first_import_candidate(desktop_id, "\0collision:1")
    assert allocated & HIGH_BIT


def test_allocate_walks_further_salts():
    desktop_id = "org.example.Busy.desktop"
    occupied = {first_import_candidate(desktop_id)}
    occupied.add(first_import_candidate(desktop_id, "\0collision:1"))
    allocated = allocate_appid(desktop_id, occupied=occupied)
    assert allocated == first_import_candidate(desktop_id, "\0collision:2")


def test_allocate_accepts_signed_occupied_values():
    desktop_id = "org.example.Signed.desktop"
    first = first_import_candidate(desktop_id)
    allocated = allocate_appid(desktop_id, occupied={uint32_to_int32(first)})
    assert allocated != first


def test_allocate_gives_up_rather_than_overwrite(monkeypatch):
    from steam_desktop_importer.steam import appid as module

    monkeypatch.setattr(module, "_COLLISION_LIMIT", 2)
    desktop_id = "org.example.Exhausted.desktop"
    occupied = {
        first_import_candidate(desktop_id),
        first_import_candidate(desktop_id, "\0collision:1"),
        first_import_candidate(desktop_id, "\0collision:2"),
    }
    with pytest.raises(AppIdAllocationError):
        allocate_appid(desktop_id, occupied=occupied)
