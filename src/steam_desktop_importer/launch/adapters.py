"""Launch adapters: DesktopApplication -> launch vector.

IMPLEMENTATION.md §10. Parsing and Steam serialization are separate stages,
and this module is neither. It turns a parsed application into the command
Steam should run, and stops there. Nothing here writes to Steam; producing the
actual ``shortcuts.vdf`` fields is Phase 6.

Every adapter is *preserving* by default. The Desktop Entry already contains a
working command, so the job is to carry it across faithfully rather than to
reconstruct it from provider metadata. Concretely:

* the ``env VAR=value`` wrapper stays in the command (§9, rule 5);
* the Flatpak application ID is **not** assumed to be the last token (§10);
* no universal Snap executable path is invented (§10);
* ``StartDir`` comes from ``Path=`` or stays empty (§8, rule 6).

The one place an adapter removes anything is Flatpak's ``--file-forwarding``
scaffolding, which is meaningless once ``%u``/``%f`` have expanded to nothing.
See CHECKLIST OPEN-3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..models import DesktopApplication

__all__ = [
    "ADAPTERS",
    "FILE_FORWARDING_FLAG",
    "LaunchAdapterError",
    "LaunchVector",
    "build_launch_vector",
    "is_transient_appimage_path",
]

FILE_FORWARDING_FLAG = "--file-forwarding"
_MARKER_END = "@@"
_MARKER_URI = "@@u"
_MARKERS = frozenset({_MARKER_END, _MARKER_URI})

# The AppImage runtime mounts its payload at "$TMPDIR/.mount_<random>" for the
# lifetime of the process. A launcher pointing inside one describes a squashfs
# that will not exist next boot.
_TRANSIENT_MOUNT_PREFIX = ".mount_"


class LaunchAdapterError(ValueError):
    """No launch vector could be produced for an application."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code
        """Machine-readable reason, mirroring ``UnsupportedCode`` in style."""


@dataclass(frozen=True)
class LaunchVector:
    """A command Steam can run, before any Steam-specific serialization.

    ``arguments`` is deliberately a list of separate tokens. Steam stores
    arguments as a single ``LaunchOptions`` string, but quoting that correctly
    is a serialization concern for Phase 6; flattening here would lose the
    token boundaries that make it possible.
    """

    exe: str
    """The program to run. Steam's ``exe`` field."""

    arguments: tuple[str, ...]
    """Arguments after ``exe``, still tokenized."""

    start_dir: str
    """Steam's ``StartDir``. Empty when the entry has no ``Path=``."""

    adapter: str
    """Which adapter produced this: ``native``/``flatpak``/``snap``/``appimage``."""

    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def argv(self) -> tuple[str, ...]:
        """The full command, ``exe`` included."""
        return (self.exe, *self.arguments)


def is_transient_appimage_path(path: str) -> bool:
    """Whether ``path`` lives inside an AppImage's temporary runtime mount.

    Detected by a ``.mount_`` path component rather than by a ``/tmp`` prefix,
    because the AppImage runtime honours ``$TMPDIR`` and the mount can appear
    under other directories.
    """
    return any(part.startswith(_TRANSIENT_MOUNT_PREFIX) for part in PurePosixPath(path).parts)


def _marker_regions(arguments: tuple[str, ...]) -> list[tuple[int, int]] | None:
    """Locate ``@@``/``@@u`` ... ``@@`` regions. ``None`` if one is unterminated."""
    regions: list[tuple[int, int]] = []
    index = 0
    length = len(arguments)
    while index < length:
        if arguments[index] in _MARKERS:
            closing = None
            for candidate in range(index + 1, length):
                if arguments[candidate] == _MARKER_END:
                    closing = candidate
                    break
            if closing is None:
                return None
            regions.append((index, closing))
            index = closing + 1
            continue
        index += 1
    return regions


def _strip_file_forwarding(
    arguments: tuple[str, ...], warnings: list[str]
) -> tuple[str, ...]:
    r"""Remove ``--file-forwarding`` scaffolding left behind by field codes.

    ``flatpak run --file-forwarding app @@u %U @@`` becomes ``... app @@u @@``
    once ``%U`` expands to nothing, and those markers would otherwise reach the
    application as literal arguments.

    Stripping is all-or-nothing. If any marker region still holds arguments,
    or a region is unterminated, the vector is left exactly as parsed and a
    warning is recorded: a half-removed forwarding block would be worse than
    an untouched one.
    """
    if FILE_FORWARDING_FLAG not in arguments:
        # Without the flag, flatpak does not interpret the markers, so they are
        # ordinary arguments and must not be touched.
        return arguments

    regions = _marker_regions(arguments)
    if regions is None:
        warnings.append(
            "unterminated file-forwarding marker in Exec; the launch vector was "
            "left exactly as parsed"
        )
        return arguments

    occupied = [region for region in regions if arguments[region[0] + 1 : region[1]]]
    if occupied:
        warnings.append(
            "a file-forwarding region still contains arguments, which is "
            "unexpected when no document is passed; the launch vector was left "
            "exactly as parsed"
        )
        return arguments

    drop = {index for region in regions for index in region}
    drop.update(index for index, token in enumerate(arguments) if token == FILE_FORWARDING_FLAG)
    return tuple(token for index, token in enumerate(arguments) if index not in drop)


def _split(app: DesktopApplication) -> tuple[str, tuple[str, ...]]:
    """Split argv into ``(exe, arguments)``, refusing an empty command."""
    if not app.exec_argv:
        raise LaunchAdapterError(
            f"{app.desktop_id} has no command to launch after Exec expansion",
            "exec_empty_after_expansion",
        )
    return app.exec_argv[0], tuple(app.exec_argv[1:])


def _native_adapter(app: DesktopApplication) -> LaunchVector:
    """Host application: use the resolved executable and parsed arguments.

    An ``env VAR=value`` wrapper is part of the command, not decoration, so it
    stays at the front. Rewriting it into Steam's ``LaunchOptions`` would drop
    the variables entirely.
    """
    exe, arguments = _split(app)
    return LaunchVector(
        exe=exe,
        arguments=arguments,
        start_dir=app.working_directory or "",
        adapter="native",
    )


def _flatpak_adapter(app: DesktopApplication) -> LaunchVector:
    """Host Flatpak: preserve the exported command, minus forwarding markers.

    The exported ``Exec=`` already carries the branch, architecture and
    ``--command=`` flags the Flatpak was published with. Rebuilding the command
    from the application ID alone would silently discard them, so the argv is
    passed through and only the file-forwarding scaffolding is removed.

    §10 warns against assuming the final token is the application ID. This
    adapter never needs to locate it, but it does cross-check the ID that
    ``X-Flatpak`` declared against the argv, and warns on a mismatch rather
    than "fixing" either one.
    """
    warnings: list[str] = []
    exe, arguments = _split(app)
    arguments = _strip_file_forwarding(arguments, warnings)

    if app.flatpak_id and app.flatpak_id not in arguments:
        warnings.append(
            f"the declared Flatpak application ID {app.flatpak_id!r} does not "
            "appear in the launch arguments; the exported command was preserved "
            "as-is rather than rewritten around the ID"
        )

    return LaunchVector(
        exe=exe,
        arguments=arguments,
        start_dir=app.working_directory or "",
        adapter="flatpak",
        warnings=tuple(warnings),
    )


def _snap_adapter(app: DesktopApplication) -> LaunchVector:
    """Snap: use the exported desktop-launch semantics as published.

    Snap desktop files are generated by snapd and already point at whatever
    launcher that snap uses, which is not uniformly ``/snap/bin/<name>``.
    §10 explicitly forbids inventing a universal path, so this is a
    pass-through that exists to make the decision visible and testable.
    """
    exe, arguments = _split(app)
    return LaunchVector(
        exe=exe,
        arguments=arguments,
        start_dir=app.working_directory or "",
        adapter="snap",
    )


def _appimage_adapter(app: DesktopApplication) -> LaunchVector:
    """Integrated AppImage: a normal executable at a persistent path.

    An entry whose command points inside the AppImage's temporary runtime
    mount describes a path that disappears when the process exits, so a Steam
    shortcut built from it would break. §10 puts unintegrated AppImages
    outside automatic MVP discovery, and this is where that is enforced.
    """
    exe, arguments = _split(app)

    if is_transient_appimage_path(exe):
        raise LaunchAdapterError(
            f"{app.desktop_id} launches from the AppImage's temporary mount "
            f"({exe}), which will not exist after the application exits; only "
            "integrated AppImages at a persistent path can be imported",
            "appimage_not_integrated",
        )

    return LaunchVector(
        exe=exe,
        arguments=arguments,
        start_dir=app.working_directory or "",
        adapter="appimage",
    )


ADAPTERS = {
    "native": _native_adapter,
    "flatpak": _flatpak_adapter,
    "snap": _snap_adapter,
    "appimage": _appimage_adapter,
}


def build_launch_vector(app: DesktopApplication) -> LaunchVector:
    """Produce the command Steam should run for ``app``.

    Raises:
        LaunchAdapterError: If the application was already refused at parse
            time, if its packaging source has no adapter, or if the adapter
            itself rejects it (see :func:`is_transient_appimage_path`).
    """
    if not app.supported_for_import:
        # A refused entry has, by definition, something wrong with the command
        # or the identity behind it. Building a vector anyway would route
        # around every check Phase 1 performed.
        raise LaunchAdapterError(
            f"{app.desktop_id} is not supported for import: "
            f"{app.unsupported_reason or 'no reason recorded'}",
            app.unsupported_code or "unsupported",
        )

    adapter = ADAPTERS.get(app.source_kind)
    if adapter is None:
        raise LaunchAdapterError(
            f"{app.desktop_id} has packaging source {app.source_kind!r}, which "
            "has no launch adapter",
            "no_adapter_for_source",
        )

    return adapter(app)
