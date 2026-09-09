"""Importer-owned AppID allocation (IMPLEMENTATION.md §16, Phase 5).

CRC32 is used here as **this importer's** first-import generator. It is not
Steam's algorithm (rule 9). The persisted AppID, once stored, is authoritative
and is not recomputed when ``Name=`` or ``Exec=`` changes.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable

__all__ = [
    "APPID_MASK",
    "HIGH_BIT",
    "IMPORTER_NAMESPACE",
    "AppIdAllocationError",
    "allocate_appid",
    "first_import_candidate",
    "game_id_64",
    "int32_to_uint32",
    "uint32_to_int32",
]

APPID_MASK = 0xFFFFFFFF
HIGH_BIT = 0x80000000
IMPORTER_NAMESPACE = "steam-desktop-importer"
_COLLISION_LIMIT = 10_000


class AppIdAllocationError(RuntimeError):
    """No unused high-bit AppID could be derived for this desktop ID."""


def uint32_to_int32(value: int) -> int:
    """Convert an unsigned 32-bit AppID to the signed form stored in a VDF."""
    value &= APPID_MASK
    return value if value < HIGH_BIT else value - 0x100000000


def int32_to_uint32(value: int) -> int:
    """Convert a signed VDF AppID to the unsigned 32-bit form used internally."""
    return value & APPID_MASK


def game_id_64(appid_unsigned: int) -> int:
    """Derived 64-bit game ID for ``rungameid``-type contexts, not artwork."""
    return ((appid_unsigned & APPID_MASK) << 32) | 0x02000000


def first_import_candidate(desktop_id: str, salt: str = "") -> int:
    """Deterministic high-bit candidate for ``desktop_id``.

    Seed is ``steam-desktop-importer\\0`` + desktop ID + optional salt, as
    specified in §16. The high bit is forced so the result sits in the
    non-Steam range observed on the capture host.
    """
    seed = f"{IMPORTER_NAMESPACE}\0{desktop_id}{salt}".encode("utf-8")
    return (zlib.crc32(seed) | HIGH_BIT) & APPID_MASK


def allocate_appid(desktop_id: str, occupied: Iterable[int] = ()) -> int:
    """Return an unused high-bit AppID for ``desktop_id``.

    ``occupied`` is the set of unsigned AppIDs already taken in this
    installation+account, typically from ``shortcuts.vdf`` plus this
    importer's own mappings. Collision salts are ``\\0collision:N`` as §16
    recommends. This function does not persist anything.
    """
    taken = {int32_to_uint32(value) for value in occupied}
    candidate = first_import_candidate(desktop_id)
    if candidate not in taken:
        return candidate

    for attempt in range(1, _COLLISION_LIMIT + 1):
        candidate = first_import_candidate(desktop_id, f"\0collision:{attempt}")
        if candidate not in taken:
            return candidate

    raise AppIdAllocationError(
        f"could not allocate an unused AppID for {desktop_id!r} after "
        f"{_COLLISION_LIMIT} collision salts"
    )
