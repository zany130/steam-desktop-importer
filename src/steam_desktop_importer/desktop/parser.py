"""Desktop Entry file parsing.

IMPLEMENTATION.md §8: "Default ``ConfigParser`` semantics are not the Desktop
Entry specification." ``configparser`` is therefore not used anywhere here.
This module implements the key-file grammar directly:

* groups, comments and blank lines;
* whitespace around ``=`` ignored;
* localized keys ``Key[lang_COUNTRY@MODIFIER]`` with specification-ordered
  locale fallback;
* ``string`` value escapes;
* semicolon-separated lists with ``\\;`` escaping;
* booleans restricted to ``true``/``false``, with legacy ``1``/``0`` accepted
  leniently and anything else falling back to the default.

§8 permits "PyXDG or an equivalent Desktop Entry-aware library ... or an
equivalently tested implementation". This is the latter. See CHECKLIST.md for
why, and note that ``pyxdg`` is still used for icon theme lookup in
``icons.py``.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..models import DesktopApplication, UnsupportedCode
from .exec_parser import (
    PARSE_MODE_STRICT,
    ExecParseError,
    ExecParseResult,
    FieldCodeContext,
    parse_exec,
    unescape_entry_value,
)

__all__ = [
    "MAIN_GROUP",
    "DesktopEntryError",
    "DesktopEntryFile",
    "build_application",
    "locale_candidates",
    "parse_desktop_entry",
]

MAIN_GROUP = "Desktop Entry"

_GROUP_RE = re.compile(r"^\[(?P<name>[^\[\]]+)\]$")
_KEY_RE = re.compile(r"^(?P<base>[A-Za-z0-9-]+)(?:\[(?P<locale>[^\]]+)\])?$")

_TRUE_VALUES = {"true"}
_FALSE_VALUES = {"false"}
_LENIENT_TRUE = {"1"}
_LENIENT_FALSE = {"0"}

# Provider roots used for source-kind detection when metadata keys are absent.
_FLATPAK_PATH_MARKERS = ("flatpak/exports/share/applications",)
_SNAP_PATH_MARKERS = ("/var/lib/snapd/desktop/applications", "snapd/desktop/applications")

# ``flatpak run`` options that may take their value as a separate argument.
_FLATPAK_VALUE_OPTIONS = frozenset(
    {
        "--arch",
        "--branch",
        "--command",
        "--cwd",
        "--device",
        "--env",
        "--filesystem",
        "--nodevice",
        "--nofilesystem",
        "--nosocket",
        "--own-name",
        "--persist",
        "--runtime",
        "--runtime-version",
        "--sdk",
        "--share",
        "--socket",
        "--system-talk-name",
        "--talk-name",
        "--unshare",
    }
)


class DesktopEntryError(Exception):
    """The desktop file could not be read as a Desktop Entry file."""


def locale_candidates(locale: str | None) -> list[str]:
    """Locale keys to try, in specification order.

    For ``lang_COUNTRY.ENCODING@MODIFIER`` the order is
    ``lang_COUNTRY@MODIFIER``, ``lang_COUNTRY``, ``lang@MODIFIER``, ``lang``.
    The encoding is stripped and never matched against.
    """
    if not locale or locale in ("C", "POSIX"):
        return []

    remainder, _, modifier = locale.partition("@")
    remainder, _, _encoding = remainder.partition(".")
    lang, _, country = remainder.partition("_")
    if not lang:
        return []

    candidates: list[str] = []
    if country and modifier:
        candidates.append(f"{lang}_{country}@{modifier}")
    if country:
        candidates.append(f"{lang}_{country}")
    if modifier:
        candidates.append(f"{lang}@{modifier}")
    candidates.append(lang)

    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def current_locale(environ: dict[str, str] | None = None) -> str | None:
    """Resolve the effective ``LC_MESSAGES`` locale from the environment."""
    env = os.environ if environ is None else environ
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name)
        if value:
            return value
    return None


def _split_semicolon_list(raw: str) -> list[str]:
    """Split a Desktop Entry list value on unescaped semicolons."""
    items: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == "\\" and index + 1 < length:
            following = raw[index + 1]
            if following == ";":
                buffer.append(";")
                index += 2
                continue
            if following == "\\":
                # Preserve for the value-escape pass so it becomes one
                # backslash rather than being consumed twice.
                buffer.append("\\\\")
                index += 2
                continue
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            items.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1

    trailing = "".join(buffer)
    if trailing:
        items.append(trailing)

    return [unescape_entry_value(item) for item in items]


@dataclass
class DesktopEntryFile:
    """A parsed key file. Values are stored raw, before escape resolution."""

    path: Path
    groups: dict[str, dict[str, str]]
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> DesktopEntryFile:
        try:
            raw_bytes = path.read_bytes()
        except OSError as error:
            raise DesktopEntryError(f"cannot read {path}: {error}") from error

        warnings: list[str] = []
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="replace")
            warnings.append("file is not valid UTF-8; undecodable bytes were replaced")

        groups: dict[str, dict[str, str]] = {}
        current: dict[str, str] | None = None

        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            group_match = _GROUP_RE.match(stripped)
            if group_match:
                name = group_match.group("name")
                if name in groups:
                    warnings.append(f"line {number}: duplicate group [{name}]; merging")
                current = groups.setdefault(name, {})
                continue

            if current is None:
                warnings.append(f"line {number}: key outside any group; ignored")
                continue

            if "=" not in line:
                warnings.append(f"line {number}: not a comment, group or key/value pair; ignored")
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if not _KEY_RE.match(key):
                warnings.append(f"line {number}: malformed key {key!r}; ignored")
                continue

            if key in current:
                warnings.append(f"line {number}: duplicate key {key!r}; keeping the first value")
                continue

            current[key] = value

        if MAIN_GROUP not in groups:
            raise DesktopEntryError(f"{path}: no [{MAIN_GROUP}] group")

        return cls(path=path, groups=groups, warnings=warnings)

    # -- accessors ------------------------------------------------------

    def raw(self, key: str, group: str = MAIN_GROUP) -> str | None:
        return self.groups.get(group, {}).get(key)

    def has(self, key: str, group: str = MAIN_GROUP) -> bool:
        return key in self.groups.get(group, {})

    def string(self, key: str, group: str = MAIN_GROUP) -> str | None:
        value = self.raw(key, group)
        return None if value is None else unescape_entry_value(value)

    def localized_string(
        self, key: str, locale: str | None, group: str = MAIN_GROUP
    ) -> str | None:
        """Look up a localestring, falling back through the locale candidates.

        Returns ``None`` if no localized variant matched. The caller decides
        whether to fall back to the unlocalized key, so that "localized" and
        "unlocalized" stay distinguishable (§5.1 keeps both).
        """
        entries = self.groups.get(group, {})
        for candidate in locale_candidates(locale):
            value = entries.get(f"{key}[{candidate}]")
            if value is not None:
                return unescape_entry_value(value)
        return None

    def boolean(self, key: str, default: bool = False, group: str = MAIN_GROUP) -> bool:
        value = self.raw(key, group)
        if value is None:
            return default
        lowered = value.strip()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        if lowered in _LENIENT_TRUE:
            self.warnings.append(f"{key}={value!r} uses the legacy numeric boolean form")
            return True
        if lowered in _LENIENT_FALSE:
            self.warnings.append(f"{key}={value!r} uses the legacy numeric boolean form")
            return False
        self.warnings.append(
            f"{key}={value!r} is not a valid boolean; only 'true' and 'false' are "
            f"valid, so the default {default!r} is used"
        )
        return default

    def string_list(self, key: str, group: str = MAIN_GROUP) -> list[str]:
        value = self.raw(key, group)
        if value is None:
            return []
        return [item for item in _split_semicolon_list(value) if item]


# ----------------------------------------------------------------------
# Semantic layer
# ----------------------------------------------------------------------


def _skip_env_wrapper(argv: tuple[str, ...] | list[str]) -> int:
    """Index of the real program in ``argv``, skipping an ``env`` wrapper.

    The wrapper is only skipped for *inspection*. Nothing here rewrites argv;
    §9 and rule 5 require it to be preserved verbatim for launching.
    """
    if not argv:
        return 0
    if os.path.basename(argv[0]) != "env":
        return 0
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in ("-i", "--ignore-environment", "-0", "--null"):
            index += 1
            continue
        if token in ("-u", "--unset"):
            index += 2
            continue
        if "=" in token and not token.startswith("-"):
            index += 1
            continue
        break
    return index


def _effective_program(argv: tuple[str, ...] | list[str]) -> str | None:
    index = _skip_env_wrapper(argv)
    if index < len(argv):
        return argv[index]
    return None


def _normalize_flatpak_ref(token: str) -> str | None:
    """Reduce a Flatpak ref to a bare application ID."""
    if token.startswith(("app/", "runtime/")):
        parts = token.split("/")
        return parts[1] if len(parts) > 1 and parts[1] else None
    if "//" in token:
        return token.split("//", 1)[0] or None
    return token or None


def _flatpak_app_id_from_argv(argv: tuple[str, ...] | list[str]) -> str | None:
    """Recover the application ID from a ``flatpak run`` command vector.

    §10: "Do not assume the final token of every Flatpak-exported ``Exec=`` is
    always the app ID." Options are skipped explicitly and the *first*
    positional argument after ``run`` is taken.
    """
    index = _skip_env_wrapper(argv)
    if index >= len(argv) or os.path.basename(argv[index]) != "flatpak":
        return None

    index += 1
    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index].split("=", 1)[0]
        index += 2 if option in _FLATPAK_VALUE_OPTIONS and "=" not in argv[index] else 1

    if index >= len(argv) or argv[index] != "run":
        return None

    index += 1
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            option = token.split("=", 1)[0]
            index += 2 if option in _FLATPAK_VALUE_OPTIONS and "=" not in token else 1
            continue
        return _normalize_flatpak_ref(token)
    return None


def _snap_instance_from_argv(argv: tuple[str, ...] | list[str]) -> str | None:
    program = _effective_program(argv)
    if program and program.startswith("/snap/bin/"):
        # /snap/bin/<instance> or /snap/bin/<instance>.<app>
        return program[len("/snap/bin/") :].split(".", 1)[0] or None

    index = _skip_env_wrapper(argv)
    if index < len(argv) and os.path.basename(argv[index]) == "snap":
        cursor = index + 1
        while cursor < len(argv) and argv[cursor].startswith("-"):
            cursor += 1
        if cursor < len(argv) and argv[cursor] == "run":
            cursor += 1
            while cursor < len(argv) and argv[cursor].startswith("-"):
                cursor += 1
            if cursor < len(argv):
                return argv[cursor].split(".", 1)[0] or None
    return None


def detect_source(
    entry: DesktopEntryFile,
    argv: tuple[str, ...] | list[str],
) -> tuple[str, str | None, str | None]:
    """Classify the packaging source of an entry.

    Returns ``(source_kind, flatpak_id, snap_instance)``. Provider metadata
    keys are preferred over argv heuristics, as §10 requires.
    """
    path_text = str(entry.path)

    flatpak_id = entry.string("X-Flatpak")
    if not flatpak_id:
        flatpak_id = _flatpak_app_id_from_argv(argv)
    if not flatpak_id and any(marker in path_text for marker in _FLATPAK_PATH_MARKERS):
        flatpak_id = entry.path.stem or None
    if flatpak_id:
        return "flatpak", flatpak_id, None

    snap_instance = entry.string("X-SnapInstanceName") or _snap_instance_from_argv(argv)
    if not snap_instance and any(marker in path_text for marker in _SNAP_PATH_MARKERS):
        snap_instance = entry.path.stem.split("_", 1)[0] or None
    if snap_instance:
        return "snap", None, snap_instance

    program = _effective_program(argv)
    if entry.has("X-AppImage-Version") or entry.has("X-AppImage-Integrate"):
        return "appimage", None, None
    if program and program.lower().endswith(".appimage"):
        return "appimage", None, None

    if program:
        return "native", None, None
    return "unknown", None, None


def resolve_try_exec(try_exec: str | None, path_env: str | None = None) -> str | None:
    """Resolve ``TryExec`` to an executable path, or ``None`` if unresolvable.

    Absolute paths are checked directly; bare names are looked up in ``PATH``.
    ``shutil.which`` handles both and also checks the executable bit.
    """
    if not try_exec:
        return None
    return shutil.which(try_exec, path=path_env)


@dataclass
class ParsedEntry:
    """Everything read from one desktop file, before support is decided."""

    entry: DesktopEntryFile
    entry_type: str
    name: str | None
    localized_name: str | None
    raw_exec: str | None
    exec_result: ExecParseResult | None
    exec_error: str | None
    icon_name: str | None
    working_directory: str | None
    try_exec: str | None
    terminal: bool
    dbus_activatable: bool
    hidden: bool
    no_display: bool
    only_show_in: list[str]
    not_show_in: list[str]
    warnings: list[str]

    @property
    def path(self) -> Path:
        return self.entry.path


def parse_desktop_entry(
    path: Path,
    locale: str | None = None,
    environ: dict[str, str] | None = None,
) -> ParsedEntry:
    """Read and interpret a single desktop file.

    Raises:
        DesktopEntryError: If the file cannot be read or has no
            ``[Desktop Entry]`` group.
    """
    entry = DesktopEntryFile.load(path)
    effective_locale = locale if locale is not None else current_locale(environ)

    entry_type = entry.string("Type") or ""
    name = entry.string("Name")
    localized_name = entry.localized_string("Name", effective_locale)

    warnings: list[str] = []
    if not entry_type:
        warnings.append("required key Type is missing")
    if name is None:
        warnings.append("required key Name is missing")

    raw_exec = entry.raw("Exec")
    icon_name = entry.string("Icon")

    exec_result: ExecParseResult | None = None
    exec_error: str | None = None
    if raw_exec:
        context = FieldCodeContext(
            icon=icon_name,
            localized_name=localized_name or name,
            desktop_file_location=str(path),
        )
        try:
            exec_result = parse_exec(raw_exec, context)
        except ExecParseError as error:
            exec_error = str(error)
            warnings.append(f"Exec could not be parsed: {error}")
        else:
            warnings.extend(exec_result.warnings)

    return ParsedEntry(
        entry=entry,
        entry_type=entry_type,
        name=name,
        localized_name=localized_name,
        raw_exec=raw_exec,
        exec_result=exec_result,
        exec_error=exec_error,
        icon_name=icon_name,
        # §8: Path= maps to StartDir. Never inferred from the executable.
        working_directory=entry.string("Path") or None,
        try_exec=entry.string("TryExec"),
        terminal=entry.boolean("Terminal", default=False),
        dbus_activatable=entry.boolean("DBusActivatable", default=False),
        hidden=entry.boolean("Hidden", default=False),
        no_display=entry.boolean("NoDisplay", default=False),
        only_show_in=entry.string_list("OnlyShowIn"),
        not_show_in=entry.string_list("NotShowIn"),
        warnings=entry.warnings + warnings,
    )


def _determine_support(parsed: ParsedEntry) -> tuple[bool, str | None, str | None]:
    """Decide importability. Returns ``(supported, reason, code)``.

    Structural problems are reported before availability problems: a terminal
    application stays "unsupported" even if it is also not installed, because
    installing it would not make it importable.
    """
    if parsed.entry_type != "Application":
        return (
            False,
            f"Type={parsed.entry_type or '<missing>'} is not an application",
            UnsupportedCode.NOT_AN_APPLICATION,
        )

    if parsed.exec_error is not None:
        return False, f"Exec could not be parsed: {parsed.exec_error}", UnsupportedCode.EXEC_UNPARSABLE

    if not parsed.raw_exec:
        if parsed.dbus_activatable:
            return (
                False,
                "DBusActivatable=true with no Exec fallback; D-Bus activation is "
                "not implemented in the MVP",
                UnsupportedCode.DBUS_ACTIVATABLE_NO_EXEC,
            )
        return False, "no Exec key", UnsupportedCode.NO_EXEC

    if parsed.exec_result is None or not parsed.exec_result.argv:
        return (
            False,
            "Exec expanded to an empty command",
            UnsupportedCode.EXEC_EMPTY_AFTER_EXPANSION,
        )

    if parsed.exec_result.ambiguous_quoting:
        # Checked before Terminal= and TryExec=: this is not "we declined to
        # support it", it is "the argv we computed is wrong". Reporting a
        # missing binary here would send the user to fix the wrong thing.
        return (
            False,
            (
                "Exec escapes a single quote at shell level; the parsed command "
                "is known to be incorrect, so importing it would create a "
                "broken shortcut"
            ),
            UnsupportedCode.EXEC_AMBIGUOUS_QUOTING,
        )

    if parsed.terminal:
        return (
            False,
            "Terminal=true; the MVP targets graphical applications and no tested "
            "terminal adapter exists",
            UnsupportedCode.TERMINAL_UNSUPPORTED,
        )

    if parsed.try_exec and resolve_try_exec(parsed.try_exec) is None:
        return (
            False,
            f"TryExec={parsed.try_exec} could not be resolved to an executable",
            UnsupportedCode.TRYEXEC_UNRESOLVABLE,
        )

    return True, None, None


def build_application(
    parsed: ParsedEntry,
    desktop_id: str,
    source_root: Path | None = None,
    icon_source_path: Path | None = None,
) -> DesktopApplication:
    """Assemble the public :class:`DesktopApplication` from a parsed entry."""
    argv = list(parsed.exec_result.argv) if parsed.exec_result else []
    field_codes = list(parsed.exec_result.field_codes) if parsed.exec_result else []
    source_kind, flatpak_id, snap_instance = detect_source(parsed.entry, argv)
    supported, reason, code = _determine_support(parsed)

    return DesktopApplication(
        desktop_id=desktop_id,
        desktop_path=parsed.path,
        name=parsed.name or parsed.path.stem,
        localized_name=parsed.localized_name,
        raw_exec=parsed.raw_exec,
        exec_argv=argv,
        icon_name=parsed.icon_name,
        icon_source_path=icon_source_path,
        working_directory=parsed.working_directory,
        try_exec=parsed.try_exec,
        terminal=parsed.terminal,
        dbus_activatable=parsed.dbus_activatable,
        hidden=parsed.hidden,
        no_display=parsed.no_display,
        only_show_in=list(parsed.only_show_in),
        not_show_in=list(parsed.not_show_in),
        source_kind=source_kind,
        flatpak_id=flatpak_id,
        snap_instance=snap_instance,
        supported_for_import=supported,
        unsupported_reason=reason,
        unsupported_code=code,
        entry_type=parsed.entry_type,
        source_root=source_root,
        field_codes=field_codes,
        nonstandard_exec=bool(parsed.exec_result and parsed.exec_result.nonstandard),
        exec_parse_mode=(
            parsed.exec_result.parse_mode if parsed.exec_result else PARSE_MODE_STRICT
        ),
        parse_warnings=list(parsed.warnings),
    )
