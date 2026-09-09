"""FreeDesktop discovery and Desktop Entry parsing (IMPLEMENTATION.md §7-§9)."""

from .discovery import (
    ApplicationRoot,
    DiscoveryResult,
    desktop_id_for,
    discover_applications,
    ordered_application_roots,
)
from .exec_parser import (
    ExecParseError,
    ExecParseResult,
    FieldCodeContext,
    parse_exec,
    tokenize_exec,
    unescape_entry_value,
)
from .icons import resolve_icon
from .parser import (
    DesktopEntryError,
    DesktopEntryFile,
    ParsedEntry,
    build_application,
    detect_source,
    parse_desktop_entry,
)

__all__ = [
    "ApplicationRoot",
    "DesktopEntryError",
    "DesktopEntryFile",
    "DiscoveryResult",
    "ExecParseError",
    "ExecParseResult",
    "FieldCodeContext",
    "ParsedEntry",
    "build_application",
    "desktop_id_for",
    "detect_source",
    "discover_applications",
    "ordered_application_roots",
    "parse_desktop_entry",
    "parse_exec",
    "resolve_icon",
    "tokenize_exec",
    "unescape_entry_value",
]
