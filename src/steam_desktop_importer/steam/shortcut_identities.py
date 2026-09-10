"""Read-only listing of existing shortcut identities.

Phase 5 collision checks and the Possible Existing Match heuristic need the
AppIDs, names, executables and launch options already in ``shortcuts.vdf``.
The Phase 6 document in :mod:`.shortcuts` is the loader; this module keeps
the small identity view those callers already use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import SteamAccount
from .shortcuts import ShortcutDocument, normalize_exe

__all__ = [
    "ExistingShortcut",
    "list_existing_shortcuts",
    "normalize_exe",
    "shortcuts_vdf_path",
]


def shortcuts_vdf_path(account: SteamAccount) -> Path:
    """``<userdata>/<account_id32>/config/shortcuts.vdf``."""
    return account.userdata_dir / "config" / "shortcuts.vdf"


@dataclass(frozen=True)
class ExistingShortcut:
    """One shortcut as far as Phase 5 identity needs it."""

    appid_unsigned: int
    name: str
    exe: str
    launch_options: str


def list_existing_shortcuts(path: Path) -> list[ExistingShortcut]:
    """Load shortcut identities from a binary ``shortcuts.vdf``.

    Returns an empty list if the file is missing. Raises ``ValueError`` if
    the file exists but cannot be parsed — the caller must not treat that
    as "no shortcuts", which would hide collisions.
    """
    document = ShortcutDocument.load(path)
    return [
        ExistingShortcut(
            appid_unsigned=entry.appid_unsigned,
            name=entry.name,
            exe=entry.exe,
            launch_options=entry.launch_options,
        )
        for entry in document.entries()
    ]
