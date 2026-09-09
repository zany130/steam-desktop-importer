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
from .installations import (
    FLATPAK_STEAM_APP_ID,
    NATIVE_ROOT_CANDIDATES,
    InstallationSelection,
    discover_installations,
    is_steam_root,
    select_installation,
)

__all__ = [
    "FLATPAK_STEAM_APP_ID",
    "NATIVE_ROOT_CANDIDATES",
    "STEAMID64_BASE",
    "AccountSelection",
    "InstallationSelection",
    "account_id32_to_steam_id64",
    "discover_accounts",
    "discover_installations",
    "is_steam_root",
    "select_account",
    "select_installation",
    "steam_id64_to_account_id32",
]
