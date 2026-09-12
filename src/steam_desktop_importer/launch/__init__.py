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
from .flatpak_steam import (
    FLATPAK_PORTAL_INTERFACE,
    FLATPAK_SPAWN,
    HostLaunchPermission,
    MANUAL_OVERRIDE_COMMAND,
    is_host_wrapped,
    probe_host_launch_permission,
    wrap_for_flatpak_steam,
)

__all__ = [
    "ADAPTERS",
    "FILE_FORWARDING_FLAG",
    "FLATPAK_PORTAL_INTERFACE",
    "FLATPAK_SPAWN",
    "HostLaunchPermission",
    "LaunchAdapterError",
    "LaunchVector",
    "MANUAL_OVERRIDE_COMMAND",
    "build_launch_vector",
    "is_host_wrapped",
    "is_transient_appimage_path",
    "probe_host_launch_permission",
    "wrap_for_flatpak_steam",
]
