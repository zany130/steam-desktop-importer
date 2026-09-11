"""Phase 0 fixture integrity.

These tests do not exercise importer code. They assert that the committed
fixtures really have the properties that docs/PHASE0_FORMAT_CHARACTERIZATION.md
claims, so that later phases are built against an accurate model.

They also enforce that Steam filesystem writes live only in
``steam/commit.py`` (VDF) and ``steam/artwork.py`` (``grid/`` placement),
plus ``Path.mkdir`` for the importer state directory, a 0600 SteamGridDB
key file, and temp artwork downloads. Tests must not write this host's
live Steam ``grid/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import vdf

SRC = Path(__file__).resolve().parents[2] / "src" / "steam_desktop_importer"

# Observed on-disk key casing, from the real capture. Section 15 of the
# specification lists different casing for four of these, which is exactly why
# the on-disk form is pinned here.
OBSERVED_KEYS = [
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
]


def load(path: Path) -> dict:
    with path.open("rb") as handle:
        return vdf.binary_load(handle)


# ----------------------------------------------------------------------
# shortcuts.vdf fixtures
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "empty.vdf",
        "single_steam_written.vdf",
        "mixed_writers.vdf",
        "unknown_fields.vdf",
        "appid_boundaries.vdf",
        "noncontiguous_indices.vdf",
        "unicode_names.vdf",
    ],
)
def test_fixture_loads_and_has_a_shortcuts_root(shortcuts_vdf_dir, name):
    data = load(shortcuts_vdf_dir / name)
    assert list(data) == ["shortcuts"]


def test_empty_fixture_has_no_entries(shortcuts_vdf_dir):
    assert load(shortcuts_vdf_dir / "empty.vdf")["shortcuts"] == {}


def test_steam_written_entry_uses_the_observed_key_casing(shortcuts_vdf_dir):
    entry = load(shortcuts_vdf_dir / "single_steam_written.vdf")["shortcuts"]["0"]
    assert list(entry) == OBSERVED_KEYS


def test_mixed_writers_fixture_really_has_two_different_key_sets(shortcuts_vdf_dir):
    """The preservation fixture for section 15 is only useful if it differs."""
    shortcuts = load(shortcuts_vdf_dir / "mixed_writers.vdf")["shortcuts"]
    steam_written = shortcuts["0"]
    tool_written = shortcuts["1"]

    assert "sortas" in steam_written
    assert steam_written["tags"] == {}

    assert "sortas" not in tool_written
    assert len(tool_written["tags"]) == 3

    assert list(steam_written) != list(tool_written)


def test_unknown_fields_survive_a_load(shortcuts_vdf_dir):
    entry = load(shortcuts_vdf_dir / "unknown_fields.vdf")["shortcuts"]["0"]
    assert entry["SomeFutureValveField"] == "keep me"
    assert entry["SomeFutureIntField"] == 42
    assert entry["SomeFutureNestedObject"] == {"a": "1", "b": {"c": "2"}}


@pytest.mark.parametrize(
    ("index", "unsigned", "signed"),
    [
        ("0", 0x00000000, 0),
        ("1", 0x7FFFFFFF, 2147483647),
        ("2", 0x80000000, -2147483648),
        ("3", 0xFFFFFFFF, -1),
    ],
)
def test_appids_are_stored_signed(shortcuts_vdf_dir, index, unsigned, signed):
    entry = load(shortcuts_vdf_dir / "appid_boundaries.vdf")["shortcuts"][index]
    assert entry["appid"] == signed
    assert entry["appid"] & 0xFFFFFFFF == unsigned


def test_noncontiguous_indices_are_preserved_on_load(shortcuts_vdf_dir):
    shortcuts = load(shortcuts_vdf_dir / "noncontiguous_indices.vdf")["shortcuts"]
    assert list(shortcuts) == ["0", "2", "5"]


def test_unicode_round_trips(shortcuts_vdf_dir):
    shortcuts = load(shortcuts_vdf_dir / "unicode_names.vdf")["shortcuts"]
    assert shortcuts["0"]["AppName"] == "Café Player — Deluxe"
    assert shortcuts["1"]["AppName"] == "日本語のアプリ"


@pytest.mark.parametrize("name", ["truncated.vdf", "not_a_vdf.vdf"])
def test_corrupt_fixtures_really_fail_to_parse(shortcuts_vdf_dir, name):
    """Section 30 needs "unreadable or unparsable VDF" to be a real error."""
    with pytest.raises(Exception):
        load(shortcuts_vdf_dir / name)


# ----------------------------------------------------------------------
# Steam config fixtures
# ----------------------------------------------------------------------


def test_loginusers_fixture_matches_the_observed_schema(steam_config_dir):
    path = steam_config_dir / "native_single_account" / "root" / "config" / "loginusers.vdf"
    users = vdf.load(path.open(encoding="utf-8"))["users"]
    assert list(users) == ["76561197971376839"]
    entry = users["76561197971376839"]
    assert set(entry) == {
        "AccountName",
        "PersonaName",
        "RememberPassword",
        "WantsOfflineMode",
        "SkipOfflineModeWarning",
        "AutoLogin",
        "Timestamp",
    }
    # Section 13 warns against depending on MostRecent; the real capture did
    # not contain it, so neither does the fixture.
    assert "MostRecent" not in entry


def test_registry_fixture_exposes_autologinuser(steam_config_dir):
    path = steam_config_dir / "native_single_account" / "registry.vdf"
    data = vdf.load(path.open(encoding="utf-8"))
    steam = data["Registry"]["HKCU"]["Software"]["Valve"]["Steam"]
    assert steam["AutoLoginUser"] == "single_user"


def test_multi_account_fixture_has_disagreeing_hints(steam_config_dir):
    base = steam_config_dir / "native_multi_account"
    users = vdf.load((base / "root" / "config" / "loginusers.vdf").open(encoding="utf-8"))["users"]
    registry = vdf.load((base / "registry.vdf").open(encoding="utf-8"))
    auto_login_user = registry["Registry"]["HKCU"]["Software"]["Valve"]["Steam"]["AutoLoginUser"]

    by_name = {entry["AccountName"]: entry for entry in users.values()}
    newest = max(users.values(), key=lambda entry: int(entry["Timestamp"]))
    auto_login_flagged = [e["AccountName"] for e in users.values() if e["AutoLogin"] == "1"]

    # Three hints, three different answers is the point of this fixture.
    assert auto_login_user == "alpha_user"
    assert newest["AccountName"] == "beta_user"
    assert auto_login_flagged == ["beta_user"]
    assert by_name["alpha_user"]["AutoLogin"] == "0"

    # And a userdata directory exists that loginusers.vdf knows nothing about.
    userdata = sorted(p.name for p in (base / "root" / "userdata").iterdir())
    assert userdata == ["11111111", "22222222", "33333333"]


def test_no_hints_fixture_still_has_enumerable_accounts(steam_config_dir):
    base = steam_config_dir / "native_no_hints" / "root"
    assert not (base / "config" / "loginusers.vdf").exists()
    assert not (base.parent / "registry.vdf").exists()
    assert sorted(p.name for p in (base / "userdata").iterdir()) == ["44444444", "55555555"]


def test_decoy_directory_has_no_steam_structure(steam_config_dir):
    base = steam_config_dir / "not_steam"
    assert base.is_dir()
    for marker in ("steamapps", "userdata", "config"):
        assert not (base / marker).exists()


# ----------------------------------------------------------------------
# Phase 7 gate: no live writes anywhere in the package
# ----------------------------------------------------------------------

# Bare method names specific enough that no non-filesystem object uses them.
# Ambiguous names such as `replace` and `move` are deliberately excluded here
# and matched only in their qualified `os.` / `shutil.` form below, so that
# ordinary `str.replace` calls do not trip the guard.
_WRITE_METHODS = {
    "write_text",
    "write_bytes",
    "touch",
    "mkdir",
    "symlink_to",
    "hardlink_to",
}

_WRITE_FUNCTIONS = {
    "os": {
        "replace",
        "rename",
        "renames",
        "remove",
        "unlink",
        "rmdir",
        "removedirs",
        "mkdir",
        "makedirs",
        "fsync",
        "truncate",
        "write",
        "symlink",
        "link",
        "chmod",
    },
    "shutil": {"copy", "copy2", "copyfile", "copytree", "move", "rmtree"},
}


def _write_calls(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _WRITE_METHODS:
                found.append(func.attr)
            if isinstance(func.value, ast.Name):
                module_writes = _WRITE_FUNCTIONS.get(func.value.id, set())
                if func.attr in module_writes:
                    found.append(f"{func.value.id}.{func.attr}")
        if isinstance(func, ast.Name) and func.id == "open":
            mode = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if isinstance(mode, str) and any(flag in mode for flag in "wxa+"):
                found.append(f"open(mode={mode!r})")
        if isinstance(func, ast.Attribute) and func.attr == "open":
            mode = None
            if node.args and isinstance(node.args[0], ast.Constant):
                mode = node.args[0].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if isinstance(mode, str) and any(flag in mode for flag in "wxa+"):
                found.append(f".open(mode={mode!r})")
    return found


# Phase 5 may create the importer state directory. Phase 7 may replace a
# shortcuts.vdf through steam/commit.py only. Phase 8 may write a 0600 API-key
# file under XDG_CONFIG_HOME and download artwork into caller-supplied temp
# paths. Phase 9 may place validated images under a userdata config/grid.
# Phase 12 may replace cloud-storage collection JSON through collection_commit.
_WRITE_ALLOWED = {
    "state/store.py": {"mkdir"},
    "steam/commit.py": {
        "mkdir",
        "os.replace",
        "os.fsync",
        "os.write",
        "os.unlink",
        "shutil.copy2",
    },
    "steam/artwork.py": {
        "mkdir",
        "os.write",
        "os.fsync",
        "os.replace",
        "os.unlink",
    },
    "steam/collection_commit.py": {
        "mkdir",
        "os.replace",
        "os.fsync",
        "os.write",
        "os.unlink",
    },
    "steamgriddb/auth.py": {"mkdir", "write_text", "os.chmod", "os.unlink"},
    "steamgriddb/download.py": {
        "mkdir",
        "os.write",
        "os.replace",
        "os.unlink",
    },
}


@pytest.mark.parametrize("module", sorted(SRC.rglob("*.py")), ids=lambda p: p.name)
def test_package_performs_no_filesystem_writes(module):
    """Steam writes exist only in ``steam/commit.py``, ``steam/artwork.py``,
    and ``steam/collection_commit.py``.

    The state store may create its own directory. SteamGridDB may write a
    config-file API key and temp artwork downloads. Grid placement is
    allowlisted in ``steam/artwork.py`` only. Collection JSON replacement is
    allowlisted in ``steam/collection_commit.py`` only.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    found = _write_calls(tree)
    allowed = _WRITE_ALLOWED.get(str(module.relative_to(SRC)), set())
    unexpected = [name for name in found if name not in allowed]
    assert unexpected == [], f"{module.name} performs filesystem writes: {unexpected}"
