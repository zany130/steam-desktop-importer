"""Data model.

Mirrors IMPLEMENTATION.md §5. The three specified dataclasses keep the exact
field names and types given there.

A small number of **additive** fields are present, each defaulted so that the
specified constructor shape still works. They are called out individually
below and recorded in CHECKLIST.md, because §5 does not mention them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "AVAILABILITY_CODES",
    "SOURCE_KINDS",
    "DesktopApplication",
    "SteamAccount",
    "SteamInstallation",
    "UnsupportedCode",
]


# IMPLEMENTATION.md §5.1.
SOURCE_KINDS = ("native", "flatpak", "snap", "appimage", "unknown")


class UnsupportedCode:
    """Machine-readable reasons an entry cannot be imported.

    §5.1 only specifies a human-readable ``unsupported_reason``. §25.1 asks the
    UI for two *distinct* statuses, "Unavailable" and "Unsupported", which a
    free-text string cannot reliably distinguish. These codes exist so the UI
    can tell them apart without string matching.
    """

    NOT_AN_APPLICATION = "not_an_application"
    ENTRY_UNPARSABLE = "entry_unparsable"
    NO_EXEC = "no_exec"
    EXEC_UNPARSABLE = "exec_unparsable"
    EXEC_EMPTY_AFTER_EXPANSION = "exec_empty_after_expansion"
    EXEC_AMBIGUOUS_QUOTING = "exec_ambiguous_quoting"
    """``Exec`` uses shell-level quote escaping that no tokenizer here handles.

    The entry parses, but its argv is *known* to be wrong, so it is refused
    rather than launched incorrectly. See CHECKLIST DEV-9.
    """
    TERMINAL_UNSUPPORTED = "terminal_unsupported"
    DBUS_ACTIVATABLE_NO_EXEC = "dbus_activatable_no_exec"
    DESKTOP_ID_COLLISION = "desktop_id_collision"
    """Several files in one root derive this desktop ID. See CHECKLIST OPEN-2.

    Unlike the other codes this one is *liftable*: the entry is perfectly
    valid, but importing it would make the §6 state row ambiguous, so import
    consent is withheld until the user acknowledges the collision.
    """

    # Availability rather than support: the entry is fine, the software is not
    # currently installed or reachable.
    TRYEXEC_UNRESOLVABLE = "tryexec_unresolvable"
    DESKTOP_FILE_MISSING = "desktop_file_missing"


AVAILABILITY_CODES = frozenset(
    {
        UnsupportedCode.TRYEXEC_UNRESOLVABLE,
        UnsupportedCode.DESKTOP_FILE_MISSING,
    }
)
"""Codes the UI should render as "Unavailable" rather than "Unsupported"."""


@dataclass
class DesktopApplication:
    """A resolved desktop entry, as specified in IMPLEMENTATION.md §5.1."""

    desktop_id: str
    desktop_path: Path
    name: str
    localized_name: str | None

    raw_exec: str | None
    exec_argv: list[str]

    icon_name: str | None
    icon_source_path: Path | None

    working_directory: str | None
    try_exec: str | None

    terminal: bool
    dbus_activatable: bool

    hidden: bool
    no_display: bool

    only_show_in: list[str]
    not_show_in: list[str]

    source_kind: str
    flatpak_id: str | None
    snap_instance: str | None

    supported_for_import: bool
    unsupported_reason: str | None

    # ------------------------------------------------------------------
    # Additive fields (not in §5.1). All defaulted.
    # ------------------------------------------------------------------

    unsupported_code: str | None = None
    """Machine-readable counterpart to ``unsupported_reason``. See §25.1."""

    entry_type: str = "Application"
    """The ``Type=`` value.

    Needed because §7.4's resolution step admits non-Application entries into
    the resolved set (they still occupy their desktop ID), while §8 says only
    ``Type=Application`` is an import candidate.
    """

    source_root: Path | None = None
    """The ``applications/`` root this entry was resolved from.

    Used by the §33 debug commands and by the UI to explain precedence.
    """

    field_codes: list[str] = field(default_factory=list)
    """Field codes seen in ``Exec=``. Required by the §33 debug output."""

    nonstandard_exec: bool = False
    """``Exec=`` did not tokenize correctly under the strict grammar.

    Currently this means POSIX shell single-quote quoting, which the Desktop
    Entry specification reserves. See ``exec_parse_mode``.
    """

    exec_parse_mode: str = "strict"
    """Which tokenizer produced ``exec_argv``: ``strict`` or ``compat``.

    Compatibility parsing is applied per entry, only after the strict parse is
    shown to be wrong. It is never enabled globally.
    """

    parse_warnings: list[str] = field(default_factory=list)
    """Non-fatal problems found while parsing. Surfaced, never swallowed."""

    collision_paths: list[Path] = field(default_factory=list)
    """Every file in this entry's root that derives the same desktop ID.

    Empty in the normal case. When populated it includes ``desktop_path``
    itself and is in the same stable lexical order discovery used to pick the
    winner, so the losing files stay visible for debugging. See CHECKLIST
    OPEN-2.
    """

    @property
    def is_available(self) -> bool:
        """False only when the entry itself is fine but the software is not."""
        return self.unsupported_code not in AVAILABILITY_CODES

    @property
    def has_collision(self) -> bool:
        """Whether this entry's desktop ID was claimed by more than one file."""
        return len(self.collision_paths) > 1


@dataclass
class SteamInstallation:
    """IMPLEMENTATION.md §5.2. Unused until Phase 4."""

    kind: str  # native | flatpak
    root: Path
    userdata_root: Path
    display_name: str


@dataclass
class SteamAccount:
    """IMPLEMENTATION.md §5.3. Unused until Phase 4.

    Note that the ``userdata/<number>/`` directory name is the 32-bit account
    ID component, not a complete textual SteamID3.
    """

    steam_id64: str
    account_id32: int
    account_name: str | None
    persona_name: str | None
    userdata_dir: Path
    selection_hints: list[str]
