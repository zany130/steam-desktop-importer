"""Steam installation discovery.

IMPLEMENTATION.md §12. Three rules drive the whole module:

1. **Validate by structure, not existence.** A directory named ``Steam`` that
   contains nothing Steam-shaped is not an installation.
2. **Resolve symlinks and de-duplicate.** On the capture host
   ``~/.local/share/Steam``, ``~/.steam/steam`` and ``~/.steam/root`` all
   resolve to one directory, so a naive probe offers the same install three
   times.
3. **Never silently use the first path that exists** (rule 7). Discovery
   returns every candidate; choosing is a separate, explicit step.

Nothing here writes. Selection is *computed*, and persisting the user's
choice is Phase 5 work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import SteamInstallation

__all__ = [
    "FLATPAK_STEAM_APP_ID",
    "NATIVE_ROOT_CANDIDATES",
    "InstallationSelection",
    "discover_installations",
    "is_steam_root",
    "select_installation",
]

FLATPAK_STEAM_APP_ID = "com.valvesoftware.Steam"

# §12 says "known native locations around" these. ~/.steam/steam and
# ~/.steam/root are normally symlinks into the first entry; they are probed
# anyway because a machine can have one without the other.
NATIVE_ROOT_CANDIDATES = (
    ".local/share/Steam",
    ".steam/steam",
    ".steam/root",
    ".steam/Steam",
)

# Canonical and compatibility locations below ~/.var/app/<app-id>/.
_FLATPAK_ROOT_CANDIDATES = (
    "data/Steam",
    ".local/share/Steam",
    "data/steam",
)

# A Steam root always has userdata/. Requiring a second marker as well keeps a
# bare directory that merely happens to contain "userdata" from qualifying.
_REQUIRED_DIRECTORY = "userdata"
_SUPPORTING_DIRECTORIES = ("steamapps", "config")


def is_steam_root(path: Path) -> bool:
    """Whether ``path`` looks structurally like a Steam root (§12)."""
    if not path.is_dir():
        return False
    if not (path / _REQUIRED_DIRECTORY).is_dir():
        return False
    return any((path / name).is_dir() for name in _SUPPORTING_DIRECTORIES)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - resolve rarely raises on a real tree
        return path


def _native_registry_path(home: Path) -> Path | None:
    """``registry.vdf`` lives at ``~/.steam/registry.vdf``, outside the root.

    Returned separately from the installation root for exactly that reason;
    see docs/PHASE0_FORMAT_CHARACTERIZATION.md §3.2.
    """
    candidate = home / ".steam" / "registry.vdf"
    return candidate if candidate.is_file() else None


def _flatpak_registry_path(app_root: Path) -> Path | None:
    for relative in (".steam/registry.vdf", "data/Steam/registry.vdf"):
        candidate = app_root / relative
        if candidate.is_file():
            return candidate
    return None


def _make(kind: str, root: Path, registry_path: Path | None) -> SteamInstallation:
    display = "Native Steam" if kind == "native" else "Flatpak Steam (experimental)"
    return SteamInstallation(
        kind=kind,
        root=root,
        userdata_root=root / _REQUIRED_DIRECTORY,
        display_name=f"{display} — {root}",
        registry_path=registry_path,
    )


def discover_installations(
    home: Path | None = None,
    extra_roots: list[tuple[str, Path]] | None = None,
) -> list[SteamInstallation]:
    """Find every valid Steam installation, native first.

    Args:
        home: Home directory to probe under. Defaults to the real one.
        extra_roots: Additional ``(kind, path)`` pairs to consider, used by
            tests to point at fixtures. They are validated and de-duplicated
            exactly like the probed candidates.

    Returns:
        Every distinct installation found. Possibly empty, possibly several.
        The caller must not assume the first entry is the right one; see
        :func:`select_installation`.
    """
    base = home if home is not None else Path.home()

    candidates: list[tuple[str, Path, Path | None]] = []
    for relative in NATIVE_ROOT_CANDIDATES:
        candidates.append(("native", base / relative, _native_registry_path(base)))

    flatpak_app_root = base / ".var" / "app" / FLATPAK_STEAM_APP_ID
    for relative in _FLATPAK_ROOT_CANDIDATES:
        candidates.append(
            ("flatpak", flatpak_app_root / relative, _flatpak_registry_path(flatpak_app_root))
        )

    for kind, path in extra_roots or []:
        registry = _native_registry_path(path.parent) or (
            path.parent / "registry.vdf" if (path.parent / "registry.vdf").is_file() else None
        )
        candidates.append((kind, path, registry))

    installations: list[SteamInstallation] = []
    seen: set[Path] = set()
    for kind, path, registry in candidates:
        if not is_steam_root(path):
            continue
        # De-duplicate on the resolved path so the ~/.steam/* symlinks collapse
        # into the one real installation they point at.
        key = _resolved(path)
        if key in seen:
            continue
        seen.add(key)
        installations.append(_make(kind, key, registry))

    return installations


@dataclass(frozen=True)
class InstallationSelection:
    """Outcome of applying §12's selection policy."""

    installations: tuple[SteamInstallation, ...]
    selected: SteamInstallation | None
    requires_confirmation: bool
    reason: str

    @property
    def is_resolved(self) -> bool:
        """Whether an installation can be used without asking the user."""
        return self.selected is not None and not self.requires_confirmation


def select_installation(
    installations: list[SteamInstallation],
    remembered_key: str | None = None,
) -> InstallationSelection:
    """Apply §12's policy without ever silently picking a path (rule 7).

    A single installation resolves on its own. A remembered choice resolves,
    because the user already made it explicitly. Anything else is preselected
    at most, and the caller has to confirm.

    Args:
        installations: Candidates from :func:`discover_installations`.
        remembered_key: A previously confirmed
            :attr:`~steam_desktop_importer.models.SteamInstallation.key`.
            Persisting it is Phase 5; this only honours it.
    """
    if not installations:
        return InstallationSelection((), None, False, "no Steam installation found")

    if remembered_key:
        for installation in installations:
            if installation.key == remembered_key:
                return InstallationSelection(
                    tuple(installations),
                    installation,
                    False,
                    "using the previously confirmed installation",
                )

    if len(installations) == 1:
        return InstallationSelection(
            tuple(installations),
            installations[0],
            False,
            "exactly one Steam installation found",
        )

    # Native is preselected because §31 makes it the MVP target and §11 keeps
    # Flatpak Steam experimental. Preselected is not chosen: confirmation is
    # still required, which is what rule 7 actually demands.
    native = [item for item in installations if item.kind == "native"]
    preselected = native[0] if native else installations[0]
    return InstallationSelection(
        tuple(installations),
        preselected,
        True,
        f"{len(installations)} installations found; confirmation required",
    )
