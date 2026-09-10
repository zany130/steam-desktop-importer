"""Optional search-query cleanup (IMPLEMENTATION.md §21.1).

The original display name is always first. Extra queries only strip obvious
packaging suffixes; they are a heuristic, not a rewrite of the title.
"""

from __future__ import annotations

import re

__all__ = ["search_queries"]

_PACKAGING_SUFFIX = re.compile(
    r"""
    \s*
    [\(\[]
    (?:
        Flatpak | Flathub | Snap | AppImage |
        Nightly | Beta | Alpha | Dev | Git | Stable | Preview
    )
    [\)\]]
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def search_queries(name: str) -> tuple[str, ...]:
    """Return the original name, then a cleaned copy if that actually differs."""
    original = name.strip()
    if not original:
        return ()
    cleaned = original
    while True:
        updated = _PACKAGING_SUFFIX.sub("", cleaned).strip()
        if updated == cleaned:
            break
        cleaned = updated
    if cleaned and cleaned != original:
        return (original, cleaned)
    return (original,)
