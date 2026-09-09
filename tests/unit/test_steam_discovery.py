"""Steam installation and account discovery (IMPLEMENTATION.md §12-§13, Phase 4).

Covers the five test cases §31 lists for Phase 4: one install, native +
Flatpak, one account, several accounts, and missing historical fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steam_desktop_importer.models import SteamInstallation
from steam_desktop_importer.steam import (
    account_id32_to_steam_id64,
    discover_accounts,
    discover_installations,
    is_steam_root,
    select_account,
    select_installation,
    steam_id64_to_account_id32,
)


def native_installation(steam_config_dir, name) -> SteamInstallation:
    """Build an installation the way discovery would, for one fixture."""
    base = steam_config_dir / name
    registry = base / "registry.vdf"
    root = base / "root"
    return SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name=f"Native Steam — {root}",
        registry_path=registry if registry.is_file() else None,
    )


# ----------------------------------------------------------------------
# §12 Installation discovery
# ----------------------------------------------------------------------


def test_structure_validation_rejects_a_lookalike_directory(steam_config_dir):
    """§12: validate by expected structure rather than existence alone."""
    assert is_steam_root(steam_config_dir / "not_steam") is False
    assert is_steam_root(steam_config_dir / "native_single_account" / "root") is True
    assert is_steam_root(steam_config_dir / "flatpak_steam" / "data" / "Steam") is True


def test_a_directory_named_steam_without_userdata_is_not_a_root(tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    assert is_steam_root(root) is False

    (root / "userdata").mkdir()
    assert is_steam_root(root) is True


def test_one_native_install_is_found_and_resolves(tmp_path, steam_config_dir):
    """Phase 4 case: one install."""
    home = tmp_path / "home"
    real = steam_config_dir / "native_single_account" / "root"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "Steam").symlink_to(real)

    installations = discover_installations(home=home)
    assert len(installations) == 1
    assert installations[0].kind == "native"

    selection = select_installation(installations)
    assert selection.is_resolved is True
    assert selection.requires_confirmation is False


def test_symlink_aliases_collapse_into_one_installation(tmp_path, steam_config_dir):
    """The capture host has three paths pointing at one Steam directory.

    ~/.local/share/Steam is real; ~/.steam/steam and ~/.steam/root are
    symlinks to it. Without resolution the same install is offered three times.
    """
    home = tmp_path / "home"
    real = steam_config_dir / "native_single_account" / "root"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "Steam").symlink_to(real)
    (home / ".steam").mkdir()
    (home / ".steam" / "steam").symlink_to(real)
    (home / ".steam" / "root").symlink_to(real)

    installations = discover_installations(home=home)
    assert len(installations) == 1
    assert installations[0].root == real.resolve()


def test_native_and_flatpak_are_both_offered_and_require_confirmation(
    tmp_path, steam_config_dir
):
    """Phase 4 case: native + Flatpak. Rule 7 forbids picking silently."""
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "Steam").symlink_to(
        steam_config_dir / "native_single_account" / "root"
    )
    flatpak_app = home / ".var" / "app" / "com.valvesoftware.Steam"
    flatpak_app.mkdir(parents=True)
    (flatpak_app / "data").symlink_to(steam_config_dir / "flatpak_steam" / "data")

    installations = discover_installations(home=home)
    assert {item.kind for item in installations} == {"native", "flatpak"}

    selection = select_installation(installations)
    assert selection.requires_confirmation is True
    assert selection.is_resolved is False
    # Preselected, not chosen: native is the MVP target and Flatpak Steam is
    # experimental, but the user still has to confirm.
    assert selection.selected is not None
    assert selection.selected.kind == "native"


def test_flatpak_installation_is_marked_experimental(steam_config_dir, tmp_path):
    home = tmp_path / "home"
    flatpak_app = home / ".var" / "app" / "com.valvesoftware.Steam"
    flatpak_app.mkdir(parents=True)
    (flatpak_app / "data").symlink_to(steam_config_dir / "flatpak_steam" / "data")

    installations = discover_installations(home=home)
    assert len(installations) == 1
    assert installations[0].is_experimental is True
    assert "experimental" in installations[0].display_name


def test_no_installation_is_an_empty_result_not_an_error(tmp_path):
    selection = select_installation(discover_installations(home=tmp_path))
    assert selection.installations == ()
    assert selection.selected is None
    assert selection.is_resolved is False


def test_a_remembered_installation_resolves_without_confirmation(tmp_path, steam_config_dir):
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "Steam").symlink_to(
        steam_config_dir / "native_single_account" / "root"
    )
    flatpak_app = home / ".var" / "app" / "com.valvesoftware.Steam"
    flatpak_app.mkdir(parents=True)
    (flatpak_app / "data").symlink_to(steam_config_dir / "flatpak_steam" / "data")

    installations = discover_installations(home=home)
    flatpak = next(item for item in installations if item.kind == "flatpak")

    selection = select_installation(installations, remembered_key=flatpak.key)
    assert selection.is_resolved is True
    assert selection.selected == flatpak


def test_installation_key_is_stable_across_symlink_aliases(tmp_path, steam_config_dir):
    """§6 keys persistent state on the installation, so it must not drift."""
    real = steam_config_dir / "native_single_account" / "root"

    first = tmp_path / "a"
    (first / ".local" / "share").mkdir(parents=True)
    (first / ".local" / "share" / "Steam").symlink_to(real)

    second = tmp_path / "b"
    (second / ".steam").mkdir(parents=True)
    (second / ".steam" / "steam").symlink_to(real)

    assert discover_installations(home=first)[0].key == discover_installations(home=second)[0].key


# ----------------------------------------------------------------------
# §13 Account discovery
# ----------------------------------------------------------------------


def test_steamid64_conversion_round_trips():
    assert account_id32_to_steam_id64(11111111) == "76561197971376839"
    assert steam_id64_to_account_id32("76561197971376839") == 11111111
    # Values that cannot be an individual account are rejected rather than
    # silently producing a negative account ID.
    assert steam_id64_to_account_id32("0") is None
    assert steam_id64_to_account_id32("not-a-number") is None


def test_single_account_is_auto_selected(steam_config_dir):
    """Phase 4 case: one account. §13 auto-selects only when unambiguous."""
    installation = native_installation(steam_config_dir, "native_single_account")
    accounts = discover_accounts(installation)

    assert len(accounts) == 1
    assert accounts[0].account_id32 == 11111111
    assert accounts[0].account_name == "single_user"
    assert accounts[0].persona_name == "Single User"

    selection = select_account(accounts)
    assert selection.is_resolved is True
    assert selection.requires_confirmation is False


def test_multiple_accounts_always_require_confirmation(steam_config_dir):
    """Phase 4 case: several accounts, with hints that disagree.

    registry.vdf points at alpha, AutoLogin points at beta, and beta also has
    the newest timestamp. Rule 8 forbids guessing between them.
    """
    installation = native_installation(steam_config_dir, "native_multi_account")
    accounts = discover_accounts(installation)

    assert {account.account_id32 for account in accounts} == {11111111, 22222222, 33333333}

    selection = select_account(accounts)
    assert selection.requires_confirmation is True
    assert selection.is_resolved is False


def test_an_account_missing_from_loginusers_is_still_offered(steam_config_dir):
    """userdata/ is the source of truth; loginusers.vdf only decorates it."""
    installation = native_installation(steam_config_dir, "native_multi_account")
    accounts = {account.account_id32: account for account in discover_accounts(installation)}

    orphan = accounts[33333333]
    assert orphan.account_name is None
    assert orphan.persona_name is None
    assert "no-loginusers-entry" in orphan.selection_hints
    # It still gets a derived SteamID64 rather than being dropped.
    assert orphan.steam_id64 == account_id32_to_steam_id64(33333333)


def test_ranking_prefers_autologinuser_over_a_newer_timestamp(steam_config_dir):
    """§13: never write to an account solely because its timestamp is newest.

    beta_user has both AutoLogin=1 and the newer timestamp, but registry.vdf's
    AutoLoginUser names alpha_user, which is the stronger hint.
    """
    installation = native_installation(steam_config_dir, "native_multi_account")
    selection = select_account(discover_accounts(installation))

    assert selection.selected is not None
    assert selection.selected.account_name == "alpha_user"
    assert "registry-autologinuser" in selection.selected.selection_hints
    # Preselected only. Beta is still on the list and the user must confirm.
    assert selection.requires_confirmation is True
    assert selection.accounts[0].account_id32 == 11111111


def test_timestamp_alone_only_breaks_ties(steam_config_dir):
    """With the strongest hint removed, ordering falls back through the chain."""
    installation = native_installation(steam_config_dir, "native_multi_account")
    installation = SteamInstallation(
        kind=installation.kind,
        root=installation.root,
        userdata_root=installation.userdata_root,
        display_name=installation.display_name,
        registry_path=None,  # drop registry.vdf, so AutoLoginUser is unavailable
    )

    selection = select_account(discover_accounts(installation))
    # beta_user now wins on AutoLogin=1, which outranks the raw timestamp.
    assert selection.selected is not None
    assert selection.selected.account_name == "beta_user"
    assert selection.requires_confirmation is True


def test_missing_hint_files_still_enumerate_accounts(steam_config_dir):
    """Phase 4 case: missing historical fields.

    No loginusers.vdf and no registry.vdf. §13 says no single field can be
    required to exist, and MostRecent was already absent on the capture host.
    """
    installation = native_installation(steam_config_dir, "native_no_hints")
    accounts = discover_accounts(installation)

    assert [account.account_id32 for account in accounts] == [44444444, 55555555]
    assert all(account.account_name is None for account in accounts)
    assert all(account.persona_name is None for account in accounts)
    assert all("no-loginusers-entry" in account.selection_hints for account in accounts)

    selection = select_account(accounts)
    assert selection.requires_confirmation is True


def test_a_malformed_hints_file_degrades_to_no_hints(tmp_path):
    """An unreadable loginusers.vdf must not abort discovery."""
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "loginusers.vdf").write_text('"users" { this is not valid')
    (root / "userdata" / "12345").mkdir(parents=True)

    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="test",
    )
    accounts = discover_accounts(installation)

    assert [account.account_id32 for account in accounts] == [12345]
    assert accounts[0].account_name is None


def test_non_account_userdata_entries_are_skipped(tmp_path):
    """`0` is a Steam placeholder, not a signed-in account. See DEV-11."""
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    for name in ("0", "anonymous", "12345", "not-a-number"):
        (root / "userdata" / name).mkdir(parents=True)
    (root / "userdata" / "stray-file").write_text("")

    installation = SteamInstallation(
        kind="native",
        root=root,
        userdata_root=root / "userdata",
        display_name="test",
    )
    assert [account.account_id32 for account in discover_accounts(installation)] == [12345]


def test_a_remembered_account_resolves_without_confirmation(steam_config_dir):
    installation = native_installation(steam_config_dir, "native_multi_account")
    accounts = discover_accounts(installation)

    selection = select_account(accounts, remembered_account_id32=22222222)
    assert selection.is_resolved is True
    assert selection.selected is not None
    assert selection.selected.account_id32 == 22222222


def test_no_accounts_is_an_empty_result_not_an_error(tmp_path):
    installation = SteamInstallation(
        kind="native",
        root=tmp_path,
        userdata_root=tmp_path / "userdata",
        display_name="test",
    )
    selection = select_account(discover_accounts(installation))
    assert selection.accounts == ()
    assert selection.selected is None
    assert selection.is_resolved is False


@pytest.mark.parametrize("fixture", ["native_single_account", "native_multi_account"])
def test_userdata_dir_points_at_the_account_directory(steam_config_dir, fixture):
    """Phase 6 will look for shortcuts.vdf under this path, so pin it."""
    installation = native_installation(steam_config_dir, fixture)
    for account in discover_accounts(installation):
        assert account.userdata_dir.is_dir()
        assert account.userdata_dir.name == str(account.account_id32)
        assert account.userdata_dir.parent == installation.userdata_root


def test_discovery_reads_nothing_outside_the_installation(steam_config_dir):
    """A sanity check that fixtures are self-contained and read-only."""
    installation = native_installation(steam_config_dir, "native_single_account")
    before = sorted(Path(steam_config_dir).rglob("*"))
    discover_accounts(installation)
    assert sorted(Path(steam_config_dir).rglob("*")) == before
