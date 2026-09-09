"""Read-only helpers for Steam's *text* KeyValues files.

``loginusers.vdf`` and ``registry.vdf`` are text KeyValues, unlike the binary
``shortcuts.vdf`` handled in Phase 6. Both are read here and never written.

Lookups are case-insensitive. Observed casing already varies between files on
one machine (``users`` lowercase in ``loginusers.vdf``, ``Registry``
capitalised in ``registry.vdf``), and IMPLEMENTATION.md §13 warns that none of
this is a stable public API, so matching keys exactly would be brittle for no
benefit.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import vdf

__all__ = ["get_ci", "load_text_keyvalues", "traverse_ci"]


def load_text_keyvalues(path: Path) -> dict[str, Any] | None:
    """Parse a text KeyValues file, or return ``None`` if it cannot be read.

    Every caller treats these files as *optional hints*, so an unreadable or
    malformed file must degrade to "no hints" rather than abort discovery.
    §13 is explicit that no single field can be relied on to exist, and that
    applies to the files themselves.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            data = vdf.load(handle)
    except (OSError, SyntaxError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def get_ci(mapping: Mapping[str, Any] | None, key: str) -> Any:
    """Case-insensitive single-key lookup."""
    if not isinstance(mapping, Mapping):
        return None
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if isinstance(candidate, str) and candidate.lower() == lowered:
            return value
    return None


def traverse_ci(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    """Walk a nested KeyValues tree case-insensitively."""
    current: Any = mapping
    for key in keys:
        current = get_ci(current, key)
        if current is None:
            return None
    return current
