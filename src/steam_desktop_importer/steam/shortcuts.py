"""In-memory ``shortcuts.vdf`` document (IMPLEMENTATION.md §15–§17, Phase 6).

Load, update, create, and serialize binary Valve KeyValues. This module never
opens a Steam path for writing: ``dumps()`` returns bytes. Phase 7 is the
only phase allowed to replace a live file.

Existing entries are mutated in place so unknown fields, key casing, key
order, and non-contiguous indices survive. New entries use the Steam-written
schema locked to the Phase 0 fixtures (DEV-1 / §15).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vdf

from .appid import int32_to_uint32, uint32_to_int32

__all__ = [
    "STEAM_NEW_ENTRY_KEYS",
    "AppIdNotFoundError",
    "PossibleMatch",
    "ShortcutDocument",
    "ShortcutEntry",
    "format_exe",
    "format_launch_options",
    "format_start_dir",
    "get_ci",
    "normalize_exe",
    "quote_steam_token",
    "set_ci",
]

# Observed Steam-written key order from Phase 0 / single_steam_written.vdf.
STEAM_NEW_ENTRY_KEYS = (
    "appid",
    "AppName",
    "Exe",
    "StartDir",
    "icon",
    "ShortcutPath",
    "LaunchOptions",
    "IsHidden",
    "AllowDesktopConfig",
    "AllowOverlay",
    "OpenVR",
    "Devkit",
    "DevkitGameID",
    "DevkitOverrideAppID",
    "LastPlayTime",
    "FlatpakAppID",
    "sortas",
    "tags",
)


class AppIdNotFoundError(KeyError):
    """No shortcut in the document has this unsigned AppID."""


def normalize_exe(exe: str) -> str:
    """Strip the quotes Steam often wraps around ``Exe``."""
    value = exe.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def quote_steam_token(value: str) -> str:
    """Quote one LaunchOptions token the way observed Steam values do.

    Tokens without whitespace or quotes are left bare. Tokens that need
    quoting are wrapped in double quotes; an embedded ``"`` is escaped as
    ``\\"``. This is a serializer choice, not a documented Steam grammar.
    """
    if value == "":
        return '""'
    if any(ch.isspace() or ch in {'"', "\\"} for ch in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def format_exe(exe: str) -> str:
    """Steam-style ``Exe`` value: quoted path, or empty."""
    path = normalize_exe(exe)
    return f'"{path}"' if path else ""


def format_start_dir(start_dir: str) -> str:
    """Steam-style ``StartDir``: quoted when present, empty when absent.

    ``Path=`` is used as given. A missing working directory stays empty
    rather than being inferred from the executable parent (rule 6).
    """
    value = normalize_exe(start_dir)
    return f'"{value}"' if value else ""


def format_launch_options(tokens: Iterable[str]) -> str:
    """Join launch-vector argument tokens into one ``LaunchOptions`` string."""
    return " ".join(quote_steam_token(token) for token in tokens)


def get_ci(mapping: dict[str, Any], key: str) -> Any:
    """Case-insensitive lookup. Writes still go through :func:`set_ci`."""
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if isinstance(candidate, str) and candidate.lower() == lowered:
            return value
    return None


def set_ci(mapping: dict[str, Any], key: str, value: Any, *, create: bool = True) -> None:
    """Assign ``value`` to the existing casing of ``key``, if any.

    When the key is absent, ``create=True`` inserts ``key`` as given (the
    observed Steam casing for new managed fields). ``create=False`` leaves
    optional keys such as ``sortas`` off entries that never had them.
    """
    if key in mapping:
        mapping[key] = value
        return
    lowered = key.lower()
    for candidate in mapping:
        if isinstance(candidate, str) and candidate.lower() == lowered:
            mapping[candidate] = value
            return
    if create:
        mapping[key] = value


@dataclass(frozen=True)
class ShortcutEntry:
    """One shortcut as currently stored in the document."""

    index: str
    appid_unsigned: int
    name: str
    exe: str
    start_dir: str
    launch_options: str
    extra_keys: tuple[str, ...]


@dataclass(frozen=True)
class PossibleMatch:
    """A §17 heuristic hit. Never implies ownership."""

    entry: ShortcutEntry
    launch_options_match: bool


def _entry_from_raw(index: str, raw: dict[str, Any]) -> ShortcutEntry | None:
    raw_appid = get_ci(raw, "appid")
    if not isinstance(raw_appid, int):
        return None
    name = get_ci(raw, "AppName")
    exe = get_ci(raw, "Exe")
    start_dir = get_ci(raw, "StartDir")
    options = get_ci(raw, "LaunchOptions")
    known = {
        "appid",
        "appname",
        "exe",
        "startdir",
        "icon",
        "shortcutpath",
        "launchoptions",
        "ishidden",
        "allowdesktopconfig",
        "allowoverlay",
        "openvr",
        "devkit",
        "devkitgameid",
        "devkitoverrideappid",
        "lastplaytime",
        "flatpakappid",
        "sortas",
        "tags",
    }
    extra = tuple(
        key
        for key in raw
        if isinstance(key, str) and key.lower() not in known
    )
    return ShortcutEntry(
        index=str(index),
        appid_unsigned=int32_to_uint32(raw_appid),
        name=name if isinstance(name, str) else "",
        exe=exe if isinstance(exe, str) else "",
        start_dir=start_dir if isinstance(start_dir, str) else "",
        launch_options=options if isinstance(options, str) else "",
        extra_keys=extra,
    )


def _new_steam_entry(
    appid_unsigned: int,
    name: str,
    exe: str,
    start_dir: str,
    launch_options: str,
    *,
    last_play_time: int = 0,
) -> dict[str, Any]:
    """Build a new entry using the fixture-locked Steam-written schema."""
    values: dict[str, Any] = {
        "appid": uint32_to_int32(appid_unsigned),
        "AppName": name,
        "Exe": format_exe(exe),
        "StartDir": format_start_dir(start_dir),
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": last_play_time,
        "FlatpakAppID": "",
        "sortas": "",
        "tags": {},
    }
    return {key: values[key] for key in STEAM_NEW_ENTRY_KEYS}


class ShortcutDocument:
    """Parsed ``shortcuts.vdf`` that can be updated and serialized."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        payload = {"shortcuts": {}} if data is None else data
        shortcuts = payload.get("shortcuts")
        if not isinstance(shortcuts, dict):
            shortcuts = {}
            payload = {"shortcuts": shortcuts}
        self._data = payload
        self._shortcuts: dict[str, Any] = shortcuts

    @classmethod
    def empty(cls) -> ShortcutDocument:
        return cls({"shortcuts": {}})

    @classmethod
    def loads(cls, payload: bytes) -> ShortcutDocument:
        try:
            data = vdf.binary_loads(payload)
        except (SyntaxError, ValueError, TypeError) as error:
            raise ValueError("unreadable or unparsable VDF") from error
        if not isinstance(data, dict):
            raise ValueError("unreadable or unparsable VDF")
        return cls(data)

    @classmethod
    def load(cls, path: Path) -> ShortcutDocument:
        """Load a file, or an empty document if it is missing.

        An existing unparsable file is an error, not an empty document.
        """
        if not path.is_file():
            return cls.empty()
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ValueError(f"unreadable or unparsable VDF: {path}") from error
        try:
            return cls.loads(payload)
        except ValueError as error:
            raise ValueError(f"unreadable or unparsable VDF: {path}") from error

    def dumps(self) -> bytes:
        """Serialize to binary KeyValues. Does not touch the filesystem."""
        return vdf.binary_dumps(self._data)

    def raw(self) -> dict[str, Any]:
        return self._data

    def indices(self) -> list[str]:
        return [str(index) for index in self._shortcuts]

    def entries(self) -> list[ShortcutEntry]:
        found: list[ShortcutEntry] = []
        for index, raw in self._shortcuts.items():
            if not isinstance(raw, dict):
                continue
            entry = _entry_from_raw(str(index), raw)
            if entry is not None:
                found.append(entry)
        return found

    def occupied_appids(self) -> set[int]:
        return {entry.appid_unsigned for entry in self.entries()}

    def find_by_appid(self, appid_unsigned: int) -> ShortcutEntry | None:
        target = int32_to_uint32(appid_unsigned)
        for entry in self.entries():
            if entry.appid_unsigned == target:
                return entry
        return None

    def _raw_by_appid(self, appid_unsigned: int) -> tuple[str, dict[str, Any]]:
        target = int32_to_uint32(appid_unsigned)
        for index, raw in self._shortcuts.items():
            if not isinstance(raw, dict):
                continue
            raw_appid = get_ci(raw, "appid")
            if isinstance(raw_appid, int) and int32_to_uint32(raw_appid) == target:
                return str(index), raw
        raise AppIdNotFoundError(f"no shortcut with AppID {target}")

    def update_by_appid(
        self,
        appid_unsigned: int,
        *,
        name: str | None = None,
        exe: str | None = None,
        start_dir: str | None = None,
        launch_options: str | None = None,
        icon: str | None = None,
    ) -> ShortcutEntry:
        """Update managed fields of the shortcut with this AppID.

        The AppID is retained. Unknown keys, tags, ``sortas``, and other
        existing fields are left alone. Missing optional keys are not added.
        """
        _index, raw = self._raw_by_appid(appid_unsigned)
        if name is not None:
            set_ci(raw, "AppName", name)
        if exe is not None:
            set_ci(raw, "Exe", format_exe(exe))
        if start_dir is not None:
            set_ci(raw, "StartDir", format_start_dir(start_dir))
        if launch_options is not None:
            set_ci(raw, "LaunchOptions", launch_options)
        if icon is not None:
            set_ci(raw, "icon", icon)
        entry = _entry_from_raw(_index, raw)
        assert entry is not None
        return entry

    def next_index(self) -> str:
        """Next index for a new shortcut. Existing holes are not filled."""
        numeric = []
        for key in self._shortcuts:
            try:
                numeric.append(int(str(key)))
            except ValueError:
                continue
        return str(max(numeric) + 1) if numeric else "0"

    def add_new(
        self,
        *,
        appid_unsigned: int,
        name: str,
        exe: str,
        start_dir: str = "",
        launch_options: str = "",
        last_play_time: int = 0,
    ) -> ShortcutEntry:
        """Append a Steam-schema entry. Does not persist importer state."""
        if appid_unsigned in self.occupied_appids():
            raise ValueError(f"AppID {appid_unsigned} is already used in this document")
        index = self.next_index()
        raw = _new_steam_entry(
            appid_unsigned,
            name,
            exe,
            start_dir,
            launch_options,
            last_play_time=last_play_time,
        )
        self._shortcuts[index] = raw
        entry = _entry_from_raw(index, raw)
        assert entry is not None
        return entry

    def possible_matches(
        self,
        name: str,
        exe: str,
        launch_options: str | None = None,
    ) -> list[PossibleMatch]:
        """§17 heuristic: name+exe, with optional launch-options confirmation.

        Never writes a mapping and never updates an entry. A name+exe hit is
        a possible existing match; matching launch options only strengthens
        the hint.
        """
        want_exe = normalize_exe(exe)
        if not name or not want_exe:
            return []
        found: list[PossibleMatch] = []
        for entry in self.entries():
            if entry.name != name:
                continue
            if normalize_exe(entry.exe) != want_exe:
                continue
            options_match = (
                launch_options is not None and entry.launch_options == launch_options
            )
            found.append(PossibleMatch(entry=entry, launch_options_match=options_match))
        return found
