"""Read-only listing of existing shortcut identities.

Phase 5 collision checks and the Possible Existing Match heuristic need the
AppIDs, names, executables and launch options already in ``shortcuts.vdf``.
Updating or serialising that file is Phase 6; this module only opens it
``rb`` and never writes.

Observed on-disk keys from Phase 0: ``appid``, ``AppName``, ``Exe``,
``LaunchOptions``. Lookups are case-insensitive because Phase 0 found two
writers with different casing in one file, and §15 forbids treating casing
as a Valve API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vdf

from ..models import SteamAccount

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


def normalize_exe(exe: str) -> str:
    """Strip the quotes Steam often wraps around ``Exe``."""
    value = exe.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _get_ci(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if isinstance(candidate, str) and candidate.lower() == lowered:
            return value
    return None


def list_existing_shortcuts(path: Path) -> list[ExistingShortcut]:
    """Load shortcut identities from a binary ``shortcuts.vdf``.

    Returns an empty list if the file is missing. Raises ``ValueError`` if
    the file exists but cannot be parsed — the caller must not treat that
    as "no shortcuts", which would hide collisions.
    """
    if not path.is_file():
        return []

    try:
        with path.open("rb") as handle:
            data = vdf.binary_load(handle)
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"unreadable or unparsable VDF: {path}") from error

    shortcuts = data.get("shortcuts")
    if not isinstance(shortcuts, dict):
        return []

    found: list[ExistingShortcut] = []
    for entry in shortcuts.values():
        if not isinstance(entry, dict):
            continue
        raw_appid = _get_ci(entry, "appid")
        if not isinstance(raw_appid, int):
            continue
        name = _get_ci(entry, "AppName")
        exe = _get_ci(entry, "Exe")
        options = _get_ci(entry, "LaunchOptions")
        found.append(
            ExistingShortcut(
                appid_unsigned=raw_appid & 0xFFFFFFFF,
                name=name if isinstance(name, str) else "",
                exe=exe if isinstance(exe, str) else "",
                launch_options=options if isinstance(options, str) else "",
            )
        )
    return found
