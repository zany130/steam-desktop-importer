"""Persistent importer state (IMPLEMENTATION.md §6, Phase 5)."""

from __future__ import annotations

from .status import (
    STATUS_CHANGED,
    STATUS_IMPORTED,
    STATUS_NEW,
    STATUS_POSSIBLE_MATCH,
    STATUS_UNSCOPED,
    classify_applications,
    current_exec,
    current_name,
    import_status,
    likely_existing_match,
)
from .store import (
    DEFAULT_STEAM_POLL_MS,
    MAX_STEAM_POLL_MS,
    MIN_STEAM_POLL_MS,
    ManagedMapping,
    StateStore,
    clamp_steam_poll_ms,
    default_state_path,
    xdg_state_home,
)

__all__ = [
    "DEFAULT_STEAM_POLL_MS",
    "MAX_STEAM_POLL_MS",
    "MIN_STEAM_POLL_MS",
    "STATUS_CHANGED",
    "STATUS_IMPORTED",
    "STATUS_NEW",
    "STATUS_POSSIBLE_MATCH",
    "STATUS_UNSCOPED",
    "ManagedMapping",
    "StateStore",
    "clamp_steam_poll_ms",
    "classify_applications",
    "current_exec",
    "current_name",
    "default_state_path",
    "import_status",
    "likely_existing_match",
    "xdg_state_home",
]
