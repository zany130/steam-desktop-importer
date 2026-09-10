"""Steam installation, account discovery, VDF documents, and safe commits.

Phases 4–6 are read-only against Steam paths. Phase 7 may replace
``shortcuts.vdf`` only through :func:`commit_shortcuts` / :func:`apply_applications`.
Phase 9 may place artwork under ``userdata/.../config/grid/`` only through
:mod:`steam_desktop_importer.steam.artwork`. Installation and account
selection is never resolved silently when there is a real choice to make
(rules 7 and 8).
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
from .artwork import (
    SLOT_HERO,
    SLOT_ICON,
    SLOT_LOGO,
    SLOT_PORTRAIT,
    SLOT_WIDE,
    artwork_filename,
    commit_artwork_files,
    grid_dir,
    grid_id,
    place_artwork,
)
from .commit import (
    MAX_BACKUPS,
    CommitError,
    CommitHooks,
    CommitResult,
    CommitValidationError,
    ImporterLock,
    ImporterLockedError,
    SteamIsRunningError,
    VdfChangedError,
    commit_shortcuts,
    lock_path_for,
    steam_allows_write,
    temp_path_for,
)
from .importing import (
    ImportPlanningError,
    ImportResult,
    ImportedShortcut,
    apply_applications,
    relink_application,
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
    "MAX_BACKUPS",
    "NATIVE_ROOT_CANDIDATES",
    "SLOT_HERO",
    "SLOT_ICON",
    "SLOT_LOGO",
    "SLOT_PORTRAIT",
    "SLOT_WIDE",
    "STEAMID64_BASE",
    "AccountSelection",
    "AppIdAllocationError",
    "AppIdNotFoundError",
    "CommitError",
    "CommitHooks",
    "CommitResult",
    "CommitValidationError",
    "ExistingShortcut",
    "ImportPlanningError",
    "ImportResult",
    "ImportedShortcut",
    "ImporterLock",
    "ImporterLockedError",
    "InstallationSelection",
    "PossibleMatch",
    "STEAM_NEW_ENTRY_KEYS",
    "ShortcutDocument",
    "ShortcutEntry",
    "SteamIsRunningError",
    "SteamRunningStatus",
    "VdfChangedError",
    "account_id32_to_steam_id64",
    "allocate_appid",
    "apply_applications",
    "artwork_filename",
    "commit_artwork_files",
    "commit_shortcuts",
    "detect_steam_running",
    "discover_accounts",
    "discover_installations",
    "first_import_candidate",
    "format_exe",
    "format_launch_options",
    "format_start_dir",
    "game_id_64",
    "grid_dir",
    "grid_id",
    "int32_to_uint32",
    "is_steam_root",
    "list_existing_shortcuts",
    "lock_path_for",
    "normalize_exe",
    "place_artwork",
    "quote_steam_token",
    "relink_application",
    "select_account",
    "select_installation",
    "shortcuts_vdf_path",
    "steam_allows_write",
    "steam_id64_to_account_id32",
    "temp_path_for",
    "uint32_to_int32",
]
