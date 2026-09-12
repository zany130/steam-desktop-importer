"""Flatpak Steam host-launch wrapping (IMPLEMENTATION.md §11, Phase 11).

Flatpak Steam runs inside a sandbox. A host ``/usr/bin/foo`` shortcut is not
assumed to work. The experimental adapter prefixes the host command with
``flatpak-spawn --host``.

This module never grants sandbox permissions. The manual override command is
shown to the user; it is never executed here. Writes still require Steam
closed.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from .adapters import LaunchVector

__all__ = [
    "FLATPAK_PORTAL_INTERFACE",
    "FLATPAK_SPAWN",
    "FLATPAK_STEAM_APP_ID",
    "HostLaunchPermission",
    "MANUAL_OVERRIDE_COMMAND",
    "is_host_wrapped",
    "probe_host_launch_permission",
    "wrap_for_flatpak_steam",
]

FLATPAK_STEAM_APP_ID = "com.valvesoftware.Steam"
FLATPAK_PORTAL_INTERFACE = "org.freedesktop.Flatpak"
FLATPAK_SPAWN = "flatpak-spawn"
MANUAL_OVERRIDE_COMMAND = (
    "flatpak override --user "
    f"--talk-name={FLATPAK_PORTAL_INTERFACE} {FLATPAK_STEAM_APP_ID}"
)


@dataclass(frozen=True)
class HostLaunchPermission:
    """Whether Flatpak Steam appears able to talk to ``org.freedesktop.Flatpak``."""

    granted: bool
    evidence: str
    override_command: str = MANUAL_OVERRIDE_COMMAND


_Run = Callable[..., CompletedProcess]


def wrap_for_flatpak_steam(
    vector: LaunchVector,
    *,
    spawn_path: str | None = None,
) -> LaunchVector:
    """Prefix a host launch vector with ``flatpak-spawn --host``.

    Already-wrapped vectors are returned unchanged. ``StartDir`` is kept as
    the desktop ``Path=`` value (rule 6); whether Flatpak Steam honours a host
    working directory is experimental.
    """
    if is_host_wrapped(vector):
        return vector
    spawn = spawn_path or shutil.which(FLATPAK_SPAWN) or f"/usr/bin/{FLATPAK_SPAWN}"
    warning = (
        "experimental Flatpak Steam host launch via "
        f"{FLATPAK_SPAWN} --host; not a public Steam contract"
    )
    return replace(
        vector,
        exe=spawn,
        arguments=("--host", vector.exe, *vector.arguments),
        adapter=f"{vector.adapter}+flatpak-steam-host",
        warnings=(*vector.warnings, warning),
    )


def is_host_wrapped(vector: LaunchVector) -> bool:
    name = Path(vector.exe).name
    return name == FLATPAK_SPAWN and bool(vector.arguments) and vector.arguments[0] == "--host"


def probe_host_launch_permission(*, run: _Run | None = None) -> HostLaunchPermission:
    """Read-only probe. Never runs ``flatpak override`` except ``--show``."""
    runner = run or _default_run
    chunks: list[str] = []
    for args in (
        ("override", "--show", "--user", FLATPAK_STEAM_APP_ID),
        ("info", "--show-permissions", FLATPAK_STEAM_APP_ID),
    ):
        text = _flatpak_output(runner, args)
        if text is None:
            continue
        chunks.append(text)
        if _session_bus_allows_portal(text):
            return HostLaunchPermission(
                granted=True,
                evidence=f"flatpak {' '.join(args)} lists {FLATPAK_PORTAL_INTERFACE}=talk",
            )
    if not chunks:
        return HostLaunchPermission(
            granted=False,
            evidence=(
                "could not read Flatpak Steam permissions; host launching is "
                "experimental and may fail until "
                f"{FLATPAK_PORTAL_INTERFACE} is granted manually"
            ),
        )
    return HostLaunchPermission(
        granted=False,
        evidence=(
            f"stock Flatpak Steam does not grant {FLATPAK_PORTAL_INTERFACE}; "
            "host launching needs a manual override and will not be granted "
            "by this importer"
        ),
    )


def _default_run(*args: object, **kwargs: object) -> CompletedProcess:
    import subprocess

    return subprocess.run(*args, **kwargs)


def _flatpak_output(run: _Run, args: Sequence[str]) -> str | None:
    try:
        completed = run(
            ["flatpak", *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, OSError, TimeoutExpired):
        return None
    stdout = getattr(completed, "stdout", "") or ""
    if getattr(completed, "returncode", 1) != 0 and not stdout.strip():
        return None
    return stdout


def _session_bus_allows_portal(text: str) -> bool:
    in_session = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_session = line.lower() == "[session bus policy]"
            continue
        if not in_session or "=" not in line:
            continue
        name, _, value = line.partition("=")
        policy = value.strip().split(";")[0].strip().lower()
        key = name.strip()
        if key in {FLATPAK_PORTAL_INTERFACE, "*"} and policy == "talk":
            return True
    return False
