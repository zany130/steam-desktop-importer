"""XDG application discovery.

IMPLEMENTATION.md §7. Three separate concerns, kept separate here:

1. **Ordered roots** (§7.1). ``$XDG_DATA_HOME`` first, then ``$XDG_DATA_DIRS``
   in exactly its configured order. The ``/usr/local/share:/usr/share``
   defaults are *not* merged into an explicitly configured ``$XDG_DATA_DIRS``.
2. **Supplemental provider roots** (§7.2). Flatpak and snapd export
   directories, added only when they are not already reachable through XDG.
3. **Precedence and masking** (§7.4). The first entry to claim a desktop ID
   wins; ``Hidden=true`` masks the ID so that no lower-priority copy can
   resurrect it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..models import DesktopApplication
from .parser import DesktopEntryError, build_application, parse_desktop_entry

__all__ = [
    "DEFAULT_XDG_DATA_DIRS",
    "SUPPLEMENTAL_APPLICATION_DIRS",
    "ApplicationRoot",
    "DiscoveryResult",
    "desktop_id_for",
    "discover_applications",
    "ordered_application_roots",
    "xdg_data_dirs",
    "xdg_data_home",
]

DEFAULT_XDG_DATA_DIRS = ("/usr/local/share", "/usr/share")

# §7.2. These are the applications directories themselves, not data roots.
SUPPLEMENTAL_APPLICATION_DIRS = (
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

_APPLICATIONS = "applications"
_SUFFIX = ".desktop"


@dataclass(frozen=True)
class ApplicationRoot:
    """One ``applications/`` directory, with where it came from."""

    path: Path
    origin: str
    """``XDG_DATA_HOME``, ``XDG_DATA_DIRS`` or ``supplemental``."""

    supplemental: bool = False


@dataclass
class DiscoveryResult:
    """Outcome of a discovery pass."""

    applications: dict[str, DesktopApplication] = field(default_factory=dict)
    """Resolved entries, keyed by desktop ID, in resolution order."""

    masked_ids: dict[str, Path] = field(default_factory=dict)
    """Desktop IDs masked by ``Hidden=true``, mapped to the masking file."""

    shadowed: list[tuple[str, Path]] = field(default_factory=list)
    """Files skipped because a higher-priority entry already claimed the ID."""

    roots: list[ApplicationRoot] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def importable(self) -> list[DesktopApplication]:
        return [app for app in self.applications.values() if app.supported_for_import]


def xdg_data_home(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """``$XDG_DATA_HOME``, defaulting to ``~/.local/share``.

    A relative value is invalid per the XDG base directory specification and
    falls back to the default.
    """
    env = os.environ if environ is None else environ
    base = home if home is not None else Path.home()
    value = env.get("XDG_DATA_HOME")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return base / ".local" / "share"


def xdg_data_dirs(environ: dict[str, str] | None = None) -> list[Path]:
    """``$XDG_DATA_DIRS`` in configured order.

    If the variable is unset or empty the specification's defaults apply. If it
    is set and non-empty it is used *exactly*; the defaults are deliberately
    not appended (§7.1). Relative entries are invalid and dropped.
    """
    env = os.environ if environ is None else environ
    value = env.get("XDG_DATA_DIRS")
    raw = value.split(":") if value else list(DEFAULT_XDG_DATA_DIRS)
    return [Path(item) for item in raw if item and Path(item).is_absolute()]


def _dedupe_roots(roots: list[ApplicationRoot]) -> list[ApplicationRoot]:
    """Drop repeated directories, keeping the first (highest-priority) one.

    Real ``$XDG_DATA_DIRS`` values do contain duplicates; see
    docs/PHASE0_FORMAT_CHARACTERIZATION.md §5.
    """
    seen: set[Path] = set()
    unique: list[ApplicationRoot] = []
    for root in roots:
        try:
            key = root.path.resolve()
        except OSError:
            key = root.path
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def ordered_application_roots(
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    include_supplemental: bool = True,
    supplemental_dirs: tuple[str, ...] | None = None,
    require_existing: bool = True,
) -> list[ApplicationRoot]:
    """Build the ordered list of ``applications/`` directories to scan.

    Supplemental provider roots are appended *after* the XDG roots. §7.2 says
    to treat them as "provider-specific discovery sources, not replacements for
    XDG semantics" but does not state where they sit in precedence; placing
    them last means a real XDG root can always override them. This is an
    interpretation and is recorded in CHECKLIST.md.
    """
    base = home if home is not None else Path.home()

    roots = [ApplicationRoot(xdg_data_home(environ, base) / _APPLICATIONS, "XDG_DATA_HOME")]
    roots += [
        ApplicationRoot(directory / _APPLICATIONS, "XDG_DATA_DIRS")
        for directory in xdg_data_dirs(environ)
    ]

    if include_supplemental:
        candidates = (
            SUPPLEMENTAL_APPLICATION_DIRS if supplemental_dirs is None else supplemental_dirs
        )
        for raw in candidates:
            if raw.startswith("~/"):
                expanded = base / raw[2:]
            elif raw == "~":
                expanded = base
            else:
                expanded = Path(raw)
            roots.append(ApplicationRoot(expanded, "supplemental", supplemental=True))

    roots = _dedupe_roots(roots)
    if require_existing:
        roots = [root for root in roots if root.path.is_dir()]
    return roots


def desktop_id_for(root: Path, path: Path) -> str:
    """Compute the FreeDesktop desktop-file ID.

    The ID is the path relative to the ``applications/`` root with ``/``
    replaced by ``-``, so ``applications/vendor/app.desktop`` becomes
    ``vendor-app.desktop``. §7.3 and rule 2 forbid keying by basename alone.
    """
    relative = path.relative_to(root)
    return "-".join(relative.parts)


def _desktop_files(root: Path) -> list[Path]:
    """All ``.desktop`` files under ``root``, in a deterministic order.

    Directory symlinks are not followed, which avoids symlink loops in
    provider export trees. Symlinked *files* are still picked up, which is how
    Flatpak and snapd normally export entries.
    """
    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories.sort()
        for filename in sorted(filenames):
            if filename.endswith(_SUFFIX):
                found.append(Path(directory) / filename)
    return found


def discover_applications(
    roots: list[ApplicationRoot] | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    locale: str | None = None,
    include_supplemental: bool = True,
) -> DiscoveryResult:
    """Scan the ordered roots and resolve desktop IDs.

    Implements the §7.4 algorithm: the first entry to claim an ID wins, and a
    ``Hidden=true`` entry masks the ID instead of claiming it. Entries are
    never resolved from a high-priority root and then overwritten by a
    lower-priority one.

    Non-``Application`` entries still *claim* their ID, matching the §7.4
    pseudo-code; they are then marked unsupported by the parser. A lower
    priority ``Type=Application`` copy must not be resurrected behind them.

    A file that fails to parse is recorded in ``errors`` and does **not** claim
    its ID, so a lower-priority readable copy can still be used. §7.4 does not
    define this case; see CHECKLIST.md.
    """
    if roots is None:
        roots = ordered_application_roots(
            environ=environ, home=home, include_supplemental=include_supplemental
        )

    result = DiscoveryResult(roots=list(roots))

    for root in roots:
        for path in _desktop_files(root.path):
            try:
                desktop_id = desktop_id_for(root.path, path)
            except ValueError:  # pragma: no cover - os.walk always stays under root
                continue

            if desktop_id in result.applications or desktop_id in result.masked_ids:
                result.shadowed.append((desktop_id, path))
                continue

            try:
                parsed = parse_desktop_entry(path, locale=locale, environ=environ)
            except DesktopEntryError as error:
                result.errors.append((path, str(error)))
                continue

            if parsed.hidden:
                result.masked_ids[desktop_id] = path
                continue

            result.applications[desktop_id] = build_application(
                parsed, desktop_id=desktop_id, source_root=root.path
            )

    return result
