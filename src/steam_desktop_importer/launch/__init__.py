"""Launch adapters (IMPLEMENTATION.md §10, Phase 2).

Turns a parsed :class:`~steam_desktop_importer.models.DesktopApplication` into
the command Steam should run. Nothing in this package writes to Steam.
"""

from __future__ import annotations

from .adapters import (
    ADAPTERS,
    FILE_FORWARDING_FLAG,
    LaunchAdapterError,
    LaunchVector,
    build_launch_vector,
    is_transient_appimage_path,
)

__all__ = [
    "ADAPTERS",
    "FILE_FORWARDING_FLAG",
    "LaunchAdapterError",
    "LaunchVector",
    "build_launch_vector",
    "is_transient_appimage_path",
]
