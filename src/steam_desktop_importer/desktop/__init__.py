"""FreeDesktop discovery and Desktop Entry parsing (IMPLEMENTATION.md §7-§9)."""

from .discovery import (
    ApplicationRoot,
    CollisionAcknowledgement,
    DesktopIdCollision,
    DiscoveryResult,
    collision_fingerprint,
    desktop_id_for,
    discover_applications,
    normalize_collision_path,
    ordered_application_roots,
)
from .exec_parser import (
    PARSE_MODE_COMPAT,
    PARSE_MODE_STRICT,
    ExecParseError,
    ExecParseResult,
    FieldCodeContext,
    looks_like_shell_single_quoting,
    parse_exec,
    tokenize_exec,
    unescape_entry_value,
    uses_shell_quote_escaping,
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
    "PARSE_MODE_COMPAT",
    "PARSE_MODE_STRICT",
    "ApplicationRoot",
    "CollisionAcknowledgement",
    "DesktopEntryError",
    "DesktopIdCollision",
    "DesktopEntryFile",
    "DiscoveryResult",
    "ExecParseError",
    "ExecParseResult",
    "FieldCodeContext",
    "ParsedEntry",
    "build_application",
    "collision_fingerprint",
    "desktop_id_for",
    "detect_source",
    "discover_applications",
    "normalize_collision_path",
    "looks_like_shell_single_quoting",
    "ordered_application_roots",
    "parse_desktop_entry",
    "parse_exec",
    "resolve_icon",
    "tokenize_exec",
    "unescape_entry_value",
    "uses_shell_quote_escaping",
]
