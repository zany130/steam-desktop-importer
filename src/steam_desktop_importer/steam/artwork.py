"""Steam grid artwork naming and atomic placement (IMPLEMENTATION.md §19–§20, §22).

Filenames use the shortcut's unsigned 32-bit AppID, not the derived 64-bit
game ID. Files are replaced in ``<userdata>/config/grid/`` via a same-directory
temp + ``os.replace``. WebP payloads are stored under a ``.png`` name.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..models import SteamAccount
from ..steamgriddb.images import COMPAT_EXTENSIONS, sniff_image, steam_filename_extension

__all__ = [
    "MAX_ASSETS_PER_SLOT",
    "PORTRAIT_DIMENSIONS",
    "SLOT_HERO",
    "SLOT_ICON",
    "SLOT_LOGO",
    "SLOT_PORTRAIT",
    "SLOT_WIDE",
    "SLOTS",
    "WIDE_DIMENSIONS",
    "artwork_filename",
    "commit_artwork_files",
    "destination_for",
    "grid_dir",
    "grid_id",
    "place_artwork",
    "slot_for_grid",
]

SLOT_PORTRAIT = "portrait"
SLOT_WIDE = "wide"
SLOT_HERO = "hero"
SLOT_LOGO = "logo"
SLOT_ICON = "icon"
SLOTS = (SLOT_PORTRAIT, SLOT_WIDE, SLOT_HERO, SLOT_LOGO, SLOT_ICON)
MAX_ASSETS_PER_SLOT = 24
# Common SteamGridDB sizes (IMPLEMENTATION.md §19.3). Not Steam-mandated.
PORTRAIT_DIMENSIONS = frozenset({"600x900", "342x482"})
WIDE_DIMENSIONS = frozenset({"460x215", "920x430"})

_STEM = {
    SLOT_PORTRAIT: "{grid}p",
    SLOT_WIDE: "{grid}",
    SLOT_HERO: "{grid}_hero",
    SLOT_LOGO: "{grid}_logo",
    SLOT_ICON: "{grid}_icon",
}


def grid_id(appid_unsigned: int) -> int:
    """Canonical artwork ID: unsigned 32-bit AppID (rule 12)."""
    return int(appid_unsigned) & 0xFFFFFFFF


def slot_for_grid(*, width: int | None, height: int | None) -> str:
    """Classify a SteamGridDB grid as portrait or wide.

    Known portrait/wide dimensions win. Leftovers with ``height > width`` are
    portrait; everything else, including missing size, is wide.
    """
    if width and height:
        dim = f"{width}x{height}"
        if dim in PORTRAIT_DIMENSIONS:
            return SLOT_PORTRAIT
        if dim in WIDE_DIMENSIONS:
            return SLOT_WIDE
        return SLOT_PORTRAIT if height > width else SLOT_WIDE
    return SLOT_WIDE


def grid_dir(account: SteamAccount) -> Path:
    return account.userdata_dir / "config" / "grid"


def artwork_filename(appid_unsigned: int, slot: str, extension: str) -> str:
    """Steam-style grid filename for ``slot``.

    ``extension`` is the on-disk suffix without a leading dot, already passed
    through :func:`steam_filename_extension` when the payload is WebP.
    """
    if slot not in _STEM:
        raise ValueError(f"unknown artwork slot {slot!r}")
    ext = extension.lstrip(".")
    return f"{_STEM[slot].format(grid=grid_id(appid_unsigned))}.{ext}"


def destination_for(
    account: SteamAccount,
    appid_unsigned: int,
    slot: str,
    source: Path,
) -> Path:
    payload = source.read_bytes()
    kind = sniff_image(payload)
    if kind is None:
        raise ValueError(f"{source} is not a recognised image")
    name = artwork_filename(appid_unsigned, slot, steam_filename_extension(kind))
    return grid_dir(account) / name


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        offset += os.write(fd, view[offset:])


def _fsync_directory(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink_if_exists(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def _clear_other_extensions(destination: Path) -> None:
    """Remove same-stem image files with a different suffix.

    ``123.png`` must not delete ``123p.png``: stems differ. Only recognised
    image suffixes are removed.
    """
    parent = destination.parent
    if not parent.is_dir():
        return
    stem = destination.stem
    for candidate in parent.iterdir():
        if (
            candidate != destination
            and candidate.stem == stem
            and candidate.suffix.lower() in COMPAT_EXTENSIONS
        ):
            _unlink_if_exists(candidate)


def place_artwork(source: Path, destination: Path) -> Path:
    """Copy validated ``source`` onto ``destination`` with a same-dir replace.

    Never streams a download onto the live grid name. Parent ``grid/`` is
    created if needed.
    """
    payload = source.read_bytes()
    if sniff_image(payload) is None:
        raise ValueError(f"{source} is not a recognised image")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    fd = os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        _write_all(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(str(part), str(destination))
        _clear_other_extensions(destination)
        _fsync_directory(destination.parent)
    except Exception:
        _unlink_if_exists(part)
        raise
    return destination


def commit_artwork_files(
    account: SteamAccount,
    appids: Mapping[str, int],
    files: Mapping[str, Mapping[str, Path]],
) -> tuple[str, ...]:
    """Place prepared temps into ``grid/``. Failures do not roll back the VDF.

    ``files`` maps desktop ID → slot → source path. Missing AppIDs or slots
    are reported rather than raised.
    """
    errors: list[str] = []
    for desktop_id, slots in files.items():
        appid = appids.get(desktop_id)
        if appid is None:
            errors.append(f"{desktop_id}: no AppID available for artwork")
            continue
        for slot, source in slots.items():
            try:
                dest = destination_for(account, appid, slot, source)
                place_artwork(source, dest)
            except Exception as error:  # noqa: BLE001 — VDF already committed
                errors.append(f"{desktop_id} {slot}: {error}")
    return tuple(errors)
