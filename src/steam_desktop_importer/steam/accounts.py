"""Steam account discovery.

IMPLEMENTATION.md §13. The controlling sentence is that Steam's
account-selection files "are not a stable public Valve API", so:

* ``userdata/<account_id32>/`` is enumerated **first** and is the source of
  truth for which accounts exist;
* ``loginusers.vdf`` and ``registry.vdf`` contribute *hints only*, and every
  field in them is optional;
* an account present on disk but absent from ``loginusers.vdf`` is still a
  real account and must still be offered.

The capture host proved the first point the hard way: ``MostRecent`` was not
present in its ``loginusers.vdf`` at all. See
docs/PHASE0_FORMAT_CHARACTERIZATION.md §3.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import SteamAccount, SteamInstallation
from .vdf_text import get_ci, load_text_keyvalues, traverse_ci

__all__ = [
    "STEAMID64_BASE",
    "AccountSelection",
    "account_id32_to_steam_id64",
    "discover_accounts",
    "select_account",
    "steam_id64_to_account_id32",
]

# The individual-account SteamID base. SteamID64 = base + account_id32.
STEAMID64_BASE = 76561197960265728

_REGISTRY_AUTOLOGIN_PATH = ("Registry", "HKCU", "Software", "Valve", "Steam", "AutoLoginUser")


def account_id32_to_steam_id64(account_id32: int) -> str:
    return str(STEAMID64_BASE + account_id32)


def steam_id64_to_account_id32(steam_id64: str) -> int | None:
    try:
        value = int(steam_id64)
    except (TypeError, ValueError):
        return None
    account_id32 = value - STEAMID64_BASE
    return account_id32 if account_id32 > 0 else None


def _login_users(installation: SteamInstallation) -> dict[int, dict[str, str]]:
    """Read ``loginusers.vdf`` into ``{account_id32: fields}``. Never raises."""
    path = installation.root / "config" / "loginusers.vdf"
    data = load_text_keyvalues(path)
    users = get_ci(data, "users")
    if not isinstance(users, dict):
        return {}

    by_account: dict[int, dict[str, str]] = {}
    for steam_id64, fields in users.items():
        account_id32 = steam_id64_to_account_id32(steam_id64)
        if account_id32 is None or not isinstance(fields, dict):
            continue
        by_account[account_id32] = {
            str(key): str(value) for key, value in fields.items() if not isinstance(value, dict)
        }
    return by_account


def _auto_login_user(installation: SteamInstallation) -> str | None:
    """``AutoLoginUser`` from ``registry.vdf``. An *account name*, not an ID."""
    if installation.registry_path is None:
        return None
    data = load_text_keyvalues(installation.registry_path)
    value = traverse_ci(data, *_REGISTRY_AUTOLOGIN_PATH)
    return str(value) if isinstance(value, str) and value else None


def _enumerate_userdata(userdata_root: Path) -> list[int]:
    """Numeric account directories under ``userdata/``, ascending.

    Non-numeric entries are ignored. ``0`` is excluded: Steam creates it as a
    placeholder rather than as a signed-in account, and §13 asks for *viable*
    accounts. Recorded as DEV-11 because §13 does not spell this out.
    """
    if not userdata_root.is_dir():
        return []

    found: list[int] = []
    for child in userdata_root.iterdir():
        if not child.is_dir():
            continue
        try:
            account_id32 = int(child.name)
        except ValueError:
            continue
        if account_id32 > 0:
            found.append(account_id32)
    return sorted(found)


def discover_accounts(installation: SteamInstallation) -> list[SteamAccount]:
    """Enumerate accounts for one installation, newest hints attached.

    ``userdata/`` drives the list. Anything read from ``loginusers.vdf`` or
    ``registry.vdf`` only decorates it, so a missing, malformed or partial
    hints file reduces the quality of the ranking and nothing else.
    """
    login_users = _login_users(installation)
    auto_login_user = _auto_login_user(installation)

    accounts: list[SteamAccount] = []
    for account_id32 in _enumerate_userdata(installation.userdata_root):
        fields = login_users.get(account_id32, {})
        account_name = fields.get("AccountName") or None
        persona_name = fields.get("PersonaName") or None

        hints: list[str] = []
        if not fields:
            # Present on disk, absent from loginusers.vdf. Still a real
            # account; §13 requires it to be offered.
            hints.append("no-loginusers-entry")
        if account_name and auto_login_user and account_name == auto_login_user:
            hints.append("registry-autologinuser")
        if fields.get("AutoLogin") == "1":
            hints.append("loginusers-autologin")
        if fields.get("MostRecent") == "1":
            hints.append("loginusers-mostrecent")
        if fields.get("Timestamp"):
            hints.append(f"timestamp:{fields['Timestamp']}")

        accounts.append(
            SteamAccount(
                steam_id64=account_id32_to_steam_id64(account_id32),
                account_id32=account_id32,
                account_name=account_name,
                persona_name=persona_name,
                userdata_dir=installation.userdata_root / str(account_id32),
                selection_hints=hints,
            )
        )

    return accounts


def _timestamp_of(account: SteamAccount) -> int:
    for hint in account.selection_hints:
        if hint.startswith("timestamp:"):
            try:
                return int(hint.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


def _rank(account: SteamAccount) -> tuple[int, int, int, int]:
    """Ranking key, highest first. Deliberately *not* a selection.

    Timestamp is the last component on purpose. §13 says never to write to an
    account solely because it has the newest timestamp, so it may break ties
    between otherwise equal candidates but can never be the reason an account
    is chosen — a multi-account install always requires confirmation anyway.
    """
    return (
        1 if "registry-autologinuser" in account.selection_hints else 0,
        1 if "loginusers-mostrecent" in account.selection_hints else 0,
        1 if "loginusers-autologin" in account.selection_hints else 0,
        _timestamp_of(account),
    )


@dataclass(frozen=True)
class AccountSelection:
    """Outcome of applying §13's selection policy."""

    accounts: tuple[SteamAccount, ...]
    """Every viable account, ranked best-hint first."""

    selected: SteamAccount | None
    """Auto-selected, or merely preselected when confirmation is required."""

    requires_confirmation: bool
    reason: str

    @property
    def is_resolved(self) -> bool:
        return self.selected is not None and not self.requires_confirmation


def select_account(
    accounts: list[SteamAccount],
    remembered_account_id32: int | None = None,
) -> AccountSelection:
    """Apply §13's policy: auto-select only when there is nothing to choose.

    Args:
        accounts: Candidates from :func:`discover_accounts`.
        remembered_account_id32: A previously confirmed account for this
            installation. §13 asks for the choice to be persisted per
            installation; the Phase 5 store writes it, this only honours it.
    """
    if not accounts:
        return AccountSelection((), None, False, "no Steam accounts found")

    ranked = tuple(sorted(accounts, key=_rank, reverse=True))

    if remembered_account_id32 is not None:
        for account in ranked:
            if account.account_id32 == remembered_account_id32:
                return AccountSelection(
                    ranked, account, False, "using the previously confirmed account"
                )

    if len(ranked) == 1:
        return AccountSelection(ranked, ranked[0], False, "exactly one account found")

    # Multiple accounts always require confirmation, however strong the hints
    # look. On the multi-account fixture the three hints disagree with each
    # other, which is exactly why this is not resolved automatically.
    top = ranked[0]
    hint_summary = ", ".join(top.selection_hints) or "no hints"
    return AccountSelection(
        ranked,
        top,
        True,
        f"{len(ranked)} accounts found; confirmation required (preselected on: {hint_summary})",
    )
