"""XDG application discovery.

IMPLEMENTATION.md §7. Three separate concerns, kept separate here:

1. **Ordered roots** (§7.1). ``$XDG_DATA_HOME`` first, then ``$XDG_DATA_DIRS``
   in exactly its configured order. The ``/usr/local/share:/usr/share``
   defaults are *not* merged into an explicitly configured ``$XDG_DATA_DIRS``.
2. **Supplemental provider roots** (§7.2). Flatpak and snapd export
   directories, added only when they are not already reachable through XDG.
3. **Precedence and masking** (§7.4). The first entry to claim a desktop ID
   wins; ``Hidden=true`` (and an invalid ``Hidden`` value, OPEN-4) masks the
   ID so that no lower-priority copy can resurrect it.
4. **Same-level ID collisions** (CHECKLIST OPEN-2). Precedence cannot separate
   two files in the *same* root that derive the same ID, so those are broken
   deterministically, reported, and held back from import.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..models import DesktopApplication, UnsupportedCode
from .parser import DesktopEntryError, build_application, parse_desktop_entry

__all__ = [
    "DEFAULT_XDG_DATA_DIRS",
    "SUPPLEMENTAL_APPLICATION_DIRS",
    "ApplicationRoot",
    "CollisionAcknowledgement",
    "DesktopIdCollision",
    "DiscoveryResult",
    "collision_fingerprint",
    "desktop_id_for",
    "discover_applications",
    "normalize_collision_path",
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


@dataclass(frozen=True)
class DesktopIdCollision:
    """Two or more files in one root deriving the same desktop ID.

    A diagnostic, not an error: discovery still resolves the ID to exactly one
    entry. It exists so the losing paths stay visible, since neither the
    resolved application nor ``shadowed`` alone explains *why* a file lost.
    """

    desktop_id: str
    root: Path
    paths: tuple[Path, ...]
    """Every colliding file, in the stable lexical order used to pick."""

    winner: Path | None
    """The file that claimed or masked the ID. ``None`` if all were unparsable."""


def normalize_collision_path(path: Path | str) -> str:
    """Absolute, symlink-resolved path used as the physical collision identity."""
    candidate = Path(path)
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate.absolute())


def collision_fingerprint(winner: Path | str, paths: Iterable[Path | str]) -> str:
    """Fingerprint of a same-root collision: physical winner plus colliding set.

    A later scan with a different winner or a different path set produces a
    different value, so a stored acknowledgement cannot follow a new launcher.
    """
    winner_key = normalize_collision_path(winner)
    path_keys = sorted({normalize_collision_path(path) for path in paths})
    blob = f"{winner_key}\0" + "\0".join(path_keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CollisionAcknowledgement:
    """A collision consent bound to a physical winner and colliding set.

    Global to the host filesystem, not to a Steam account. It is valid only
    while the current scan still resolves the same desktop ID to the same
    winner among the same colliding paths.
    """

    desktop_id: str
    winner_path: Path
    colliding_paths: tuple[Path, ...]

    @property
    def fingerprint(self) -> str:
        return collision_fingerprint(self.winner_path, self.colliding_paths)

    def matches(
        self,
        desktop_id: str,
        winner: Path | str | None,
        paths: Iterable[Path | str],
    ) -> bool:
        if winner is None or desktop_id != self.desktop_id:
            return False
        return collision_fingerprint(winner, paths) == self.fingerprint


@dataclass
class DiscoveryResult:
    """Outcome of a discovery pass."""

    applications: dict[str, DesktopApplication] = field(default_factory=dict)
    """Resolved entries, keyed by desktop ID, in resolution order."""

    masked_ids: dict[str, Path] = field(default_factory=dict)
    """Desktop IDs masked by ``Hidden=true``, mapped to the masking file."""

    shadowed: list[tuple[str, Path]] = field(default_factory=list)
    """Files skipped because a higher-priority entry already claimed the ID."""

    collisions: list[DesktopIdCollision] = field(default_factory=list)
    """Same-root desktop ID collisions. See CHECKLIST OPEN-2."""

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
    """All ``.desktop`` files under ``root``, in stable lexical path order.

    The sort is on the whole path string rather than on walk order, so a
    top-level file and a nested one are ordered against each other rather than
    by which directory ``os.walk`` happened to reach first. That is what makes
    the collision tie-break in :func:`discover_applications` reproducible
    across machines and filesystems.

    Directory symlinks are not followed, which avoids symlink loops in
    provider export trees. Symlinked *files* are still picked up, which is how
    Flatpak and snapd normally export entries.
    """
    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories.sort()
        for filename in filenames:
            if filename.endswith(_SUFFIX):
                found.append(Path(directory) / filename)
    return sorted(found, key=str)


def _group_by_desktop_id(root: Path) -> dict[str, list[Path]]:
    """Map each desktop ID in ``root`` to the file(s) deriving it.

    Nearly always one file per ID. More than one means a same-level collision,
    and the list is in the lexical order established by :func:`_desktop_files`.
    """
    grouped: dict[str, list[Path]] = {}
    for path in _desktop_files(root):
        try:
            desktop_id = desktop_id_for(root, path)
        except ValueError:  # pragma: no cover - os.walk always stays under root
            continue
        grouped.setdefault(desktop_id, []).append(path)
    return grouped


def _withhold_import_consent(
    app: DesktopApplication,
    paths: list[Path],
    acknowledged: tuple[CollisionAcknowledgement, ...],
) -> None:
    """Record a collision on ``app`` and, unless acknowledged, block import.

    Identity and importability are separate concerns here. ``app`` is already
    the deterministic winner, so the ID is resolved either way; what this
    withholds is *consent to import*, because a §6 state row keyed on the
    desktop ID alone could not tell the colliding files apart later.

    An acknowledgement is valid only when it names this winner and this
    colliding set. A stale row keyed only by desktop ID cannot lift the
    block, and neither can one whose winner or path set has changed.

    An entry that is already unsupported keeps its original reason: it cannot
    be imported regardless, and the parse-level problem is the more useful
    thing to show. The collision stays visible via ``collision_paths``.
    """
    app.collision_paths = list(paths)

    if not app.supported_for_import:
        return
    if any(ack.matches(app.desktop_id, app.desktop_path, paths) for ack in acknowledged):
        return

    others = ", ".join(str(path) for path in paths if path != app.desktop_path)
    app.supported_for_import = False
    app.unsupported_code = UnsupportedCode.DESKTOP_ID_COLLISION
    app.unsupported_reason = (
        f"Desktop ID {app.desktop_id!r} is also produced by {others}. "
        "Confirm which file to import; the others will be ignored."
    )


def discover_applications(
    roots: list[ApplicationRoot] | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    locale: str | None = None,
    include_supplemental: bool = True,
    acknowledged_collisions: Iterable[CollisionAcknowledgement] | None = None,
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

    Precedence handles collisions *between* roots. Files colliding within one
    root have no precedence to separate them, so the lexically first path wins
    the ID, the rest are shadowed, a :class:`DesktopIdCollision` is recorded,
    and the winner is held back from import. An acknowledgement that still
    matches the current winner and colliding set lifts that hold while
    keeping the diagnostic.
    """
    if roots is None:
        roots = ordered_application_roots(
            environ=environ, home=home, include_supplemental=include_supplemental
        )

    acknowledged = tuple(acknowledged_collisions or ())
    result = DiscoveryResult(roots=list(roots))

    for root in roots:
        for desktop_id, paths in _group_by_desktop_id(root.path).items():
            if desktop_id in result.applications or desktop_id in result.masked_ids:
                # Already settled by a higher-priority root. Any collision down
                # here is moot, so it is shadowed rather than reported.
                result.shadowed.extend((desktop_id, path) for path in paths)
                continue

            winner: Path | None = None
            for index, path in enumerate(paths):
                try:
                    parsed = parse_desktop_entry(path, locale=locale, environ=environ)
                except DesktopEntryError as error:
                    # Unparsable files do not claim the ID, so on a collision
                    # the next candidate gets its turn.
                    result.errors.append((path, str(error)))
                    continue

                winner = path
                result.shadowed.extend((desktop_id, other) for other in paths[index + 1 :])
                if parsed.hidden:
                    result.masked_ids[desktop_id] = path
                else:
                    result.applications[desktop_id] = build_application(
                        parsed, desktop_id=desktop_id, source_root=root.path
                    )
                break

            if len(paths) > 1:
                result.collisions.append(
                    DesktopIdCollision(
                        desktop_id=desktop_id,
                        root=root.path,
                        paths=tuple(paths),
                        winner=winner,
                    )
                )
                resolved = result.applications.get(desktop_id)
                if resolved is not None:
                    _withhold_import_consent(resolved, paths, acknowledged)

    return result
