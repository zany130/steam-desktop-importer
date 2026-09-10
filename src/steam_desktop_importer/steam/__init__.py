"""Steam installation and account discovery (IMPLEMENTATION.md §12-§13, Phase 4).

Read-only. Nothing in this package writes to a Steam directory, and neither
installation nor account selection is ever resolved silently when there is a
real choice to make (rules 7 and 8).
"""

from __future__ import annotations

from .accounts import (
    STEAMID64_BASE,
    AccountSelection,
    account_id32_to_steam_id64,
    discover_accounts,
    select_account,
    steam_id64_to_account_id32,
)
from .appid import (
    AppIdAllocationError,
    allocate_appid,
    first_import_candidate,
    game_id_64,
    int32_to_uint32,
    uint32_to_int32,
)
from .installations import (
    FLATPAK_STEAM_APP_ID,
    NATIVE_ROOT_CANDIDATES,
    InstallationSelection,
    discover_installations,
    is_steam_root,
    select_installation,
)
from .running import SteamRunningStatus, detect_steam_running
from .shortcut_identities import (
    ExistingShortcut,
    list_existing_shortcuts,
    normalize_exe,
    shortcuts_vdf_path,
)
from .shortcuts import (
    STEAM_NEW_ENTRY_KEYS,
    AppIdNotFoundError,
    PossibleMatch,
    ShortcutDocument,
    ShortcutEntry,
    format_exe,
    format_launch_options,
    format_start_dir,
    quote_steam_token,
)

__all__ = [
    "FLATPAK_STEAM_APP_ID",
    "NATIVE_ROOT_CANDIDATES",
    "STEAMID64_BASE",
    "AccountSelection",
    "AppIdAllocationError",
    "AppIdNotFoundError",
    "ExistingShortcut",
    "InstallationSelection",
    "PossibleMatch",
    "STEAM_NEW_ENTRY_KEYS",
    "ShortcutDocument",
    "ShortcutEntry",
    "SteamRunningStatus",
    "account_id32_to_steam_id64",
    "allocate_appid",
    "detect_steam_running",
    "discover_accounts",
    "discover_installations",
    "first_import_candidate",
    "format_exe",
    "format_launch_options",
    "format_start_dir",
    "game_id_64",
    "int32_to_uint32",
    "is_steam_root",
    "list_existing_shortcuts",
    "normalize_exe",
    "quote_steam_token",
    "select_account",
    "select_installation",
    "shortcuts_vdf_path",
    "steam_id64_to_account_id32",
    "uint32_to_int32",
]
