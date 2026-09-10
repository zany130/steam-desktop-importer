"""Steam running detection.

IMPLEMENTATION.md §14. Detection itself is read-only. Phase 7 uses
:func:`steam_allows_write` to gate ``shortcuts.vdf`` commits: a conservative
false positive is preferable to writing while Steam is running.

§14 asks for detection "based on more than only ``name == 'steam'`` where
practical". Both shape the implementation:

* several independent signals are checked, not just the process name;
* anything ambiguous resolves to "running";
* a process that cannot be inspected is treated as a possible Steam rather
  than ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psutil

__all__ = ["SteamRunningStatus", "detect_steam_running", "steam_allows_write"]

# Matched against the process name, case-insensitively and exactly. "steam" on
# its own is deliberately included, but it is never the only signal consulted.
_PROCESS_NAMES = frozenset(
    {
        "steam",
        "steamwebhelper",
        "steamerrorreporter",
        "steam.sh",
    }
)

# Substrings that identify Steam by where it was launched from. These catch
# wrapper scripts and the Flatpak sandbox, where the process name alone is
# uninformative.
_PATH_MARKERS = (
    "/steam/steam.sh",
    "/steamapps/",
    "/.steam/",
    "/steam/ubuntu12_32/",
    "/steam/ubuntu12_64/",
)

_FLATPAK_MARKERS = ("com.valvesoftware.steam",)


@dataclass(frozen=True)
class SteamRunningStatus:
    """Whether Steam appears to be running, and what said so."""

    running: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable reasons, for the UI and the logs. Never process dumps."""

    inspection_failures: int = 0
    """Processes that could not be inspected. See :attr:`is_certain`."""

    @property
    def is_certain(self) -> bool:
        """False when something could not be inspected and nothing matched.

        §14 prefers a conservative false positive, so an uncertain *negative*
        is the one result callers must not act on blindly.
        """
        return self.running or self.inspection_failures == 0


def steam_allows_write(status: SteamRunningStatus) -> bool:
    """Whether a VDF write may proceed given this probe result.

    Running Steam is an absolute block (rule 15). An uncertain *negative*
    (unreadable processes, nothing matched) is also a block: §14 prefers a
    conservative false positive over writing while Steam might be running.
    """
    return (not status.running) and status.is_certain


def _matches(process: psutil.Process) -> str | None:
    """Return the reason this process looks like Steam, or ``None``."""
    info = process.info
    name = (info.get("name") or "").lower()
    executable = (info.get("exe") or "").lower()
    cmdline = " ".join(info.get("cmdline") or []).lower()

    if name in _PROCESS_NAMES:
        return f"process name {name!r}"

    for marker in _FLATPAK_MARKERS:
        if marker in executable or marker in cmdline:
            return f"Flatpak Steam marker {marker!r}"

    for marker in _PATH_MARKERS:
        if marker in executable:
            return f"executable path contains {marker!r}"
        if marker in cmdline:
            return f"command line contains {marker!r}"

    return None


def detect_steam_running() -> SteamRunningStatus:
    """Check whether Steam appears to be running anywhere on the system.

    Returns as soon as the picture is complete; there is no early exit on the
    first match, because the evidence list is more useful to a user deciding
    whether to close Steam than a bare boolean.
    """
    evidence: list[str] = []
    failures = 0

    for process in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            reason = _matches(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Could not inspect it. Counted rather than ignored so that a
            # negative result can report that it is not certain.
            failures += 1
            continue
        if reason is not None:
            evidence.append(reason)

    # De-duplicate while keeping first-seen order; dozens of steamwebhelper
    # processes are normal and would otherwise flood the indicator.
    unique: list[str] = []
    for reason in evidence:
        if reason not in unique:
            unique.append(reason)

    return SteamRunningStatus(
        running=bool(unique),
        evidence=tuple(unique),
        inspection_failures=failures,
    )
