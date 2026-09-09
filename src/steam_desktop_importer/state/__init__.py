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
from .store import ManagedMapping, StateStore, default_state_path, xdg_state_home

__all__ = [
    "STATUS_CHANGED",
    "STATUS_IMPORTED",
    "STATUS_NEW",
    "STATUS_POSSIBLE_MATCH",
    "STATUS_UNSCOPED",
    "ManagedMapping",
    "StateStore",
    "classify_applications",
    "current_exec",
    "current_name",
    "default_state_path",
    "import_status",
    "likely_existing_match",
    "xdg_state_home",
]
