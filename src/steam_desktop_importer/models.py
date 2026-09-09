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
    TERMINAL_UNSUPPORTED = "terminal_unsupported"
    DBUS_ACTIVATABLE_NO_EXEC = "dbus_activatable_no_exec"

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

    parse_warnings: list[str] = field(default_factory=list)
    """Non-fatal problems found while parsing. Surfaced, never swallowed."""

    @property
    def is_available(self) -> bool:
        """False only when the entry itself is fine but the software is not."""
        return self.unsupported_code not in AVAILABILITY_CODES


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
