"""XDG discovery tests (IMPLEMENTATION.md §7, Phase 1, §34)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steam_desktop_importer.desktop.discovery import (
    ApplicationRoot,
    desktop_id_for,
    discover_applications,
    ordered_application_roots,
    xdg_data_dirs,
    xdg_data_home,
)
from steam_desktop_importer.models import UnsupportedCode


def roots_from(paths: list[Path]) -> list[ApplicationRoot]:
    return [ApplicationRoot(path, "test") for path in paths]


# ----------------------------------------------------------------------
# §7.1 ordered roots
# ----------------------------------------------------------------------


def test_xdg_data_home_default():
    assert xdg_data_home({}, home=Path("/home/u")) == Path("/home/u/.local/share")


def test_xdg_data_home_explicit():
    env = {"XDG_DATA_HOME": "/custom/data"}
    assert xdg_data_home(env, home=Path("/home/u")) == Path("/custom/data")


def test_relative_xdg_data_home_is_invalid_and_falls_back():
    env = {"XDG_DATA_HOME": "relative/data"}
    assert xdg_data_home(env, home=Path("/home/u")) == Path("/home/u/.local/share")


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_xdg_data_dirs_uses_the_defaults(value):
    env = {} if value is None else {"XDG_DATA_DIRS": value}
    assert xdg_data_dirs(env) == [Path("/usr/local/share"), Path("/usr/share")]


def test_explicit_xdg_data_dirs_is_used_exactly_and_defaults_are_not_merged():
    """§7.1: do not merge /usr/local/share:/usr/share into a configured value."""
    env = {"XDG_DATA_DIRS": "/opt/a/share:/opt/b/share"}
    assert xdg_data_dirs(env) == [Path("/opt/a/share"), Path("/opt/b/share")]


def test_xdg_data_dirs_preserves_configured_order():
    env = {"XDG_DATA_DIRS": "/usr/share:/usr/local/share:/opt/z/share"}
    assert xdg_data_dirs(env) == [
        Path("/usr/share"),
        Path("/usr/local/share"),
        Path("/opt/z/share"),
    ]


def test_relative_entries_in_xdg_data_dirs_are_dropped():
    env = {"XDG_DATA_DIRS": "/opt/a/share:relative/share::/opt/b/share"}
    assert xdg_data_dirs(env) == [Path("/opt/a/share"), Path("/opt/b/share")]


def test_data_home_comes_before_data_dirs():
    env = {"XDG_DATA_HOME": "/home/u/.local/share", "XDG_DATA_DIRS": "/usr/share"}
    roots = ordered_application_roots(
        env, home=Path("/home/u"), include_supplemental=False, require_existing=False
    )
    assert [root.path for root in roots] == [
        Path("/home/u/.local/share/applications"),
        Path("/usr/share/applications"),
    ]
    assert [root.origin for root in roots] == ["XDG_DATA_HOME", "XDG_DATA_DIRS"]


def test_duplicate_roots_are_deduplicated_keeping_the_first():
    """Real XDG_DATA_DIRS values contain duplicates; see the Phase 0 capture."""
    env = {
        "XDG_DATA_HOME": "/home/u/.local/share",
        "XDG_DATA_DIRS": "/opt/p/share:/usr/local/share:/usr/share:/opt/p/share:/usr/share",
    }
    roots = ordered_application_roots(
        env, home=Path("/home/u"), include_supplemental=False, require_existing=False
    )
    assert [root.path for root in roots] == [
        Path("/home/u/.local/share/applications"),
        Path("/opt/p/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]


def test_supplemental_roots_are_appended(tmp_path):
    supplemental = tmp_path / "flatpak" / "exports" / "share" / "applications"
    supplemental.mkdir(parents=True)
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_DATA_DIRS": str(tmp_path / "sys")}
    (tmp_path / "data" / "applications").mkdir(parents=True)
    (tmp_path / "sys" / "applications").mkdir(parents=True)

    roots = ordered_application_roots(
        env, home=tmp_path, supplemental_dirs=(str(supplemental),)
    )
    assert roots[-1].path == supplemental
    assert roots[-1].supplemental is True
    assert [root.supplemental for root in roots[:-1]] == [False, False]


def test_supplemental_root_already_reachable_through_xdg_is_not_duplicated(tmp_path):
    """§7.2: inspect these only when not already reachable through XDG."""
    shared = tmp_path / "flatpak" / "exports" / "share"
    (shared / "applications").mkdir(parents=True)
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_DATA_DIRS": str(shared)}
    (tmp_path / "data" / "applications").mkdir(parents=True)

    roots = ordered_application_roots(
        env, home=tmp_path, supplemental_dirs=(str(shared / "applications"),)
    )
    assert [root.path for root in roots] == [
        tmp_path / "data" / "applications",
        shared / "applications",
    ]
    # It kept the XDG origin, not the supplemental one.
    assert roots[1].origin == "XDG_DATA_DIRS"
    assert roots[1].supplemental is False


def test_nonexistent_roots_are_skipped(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "nope"), "XDG_DATA_DIRS": str(tmp_path / "also-nope")}
    assert ordered_application_roots(env, home=tmp_path, include_supplemental=False) == []


# ----------------------------------------------------------------------
# §7.3 desktop IDs
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("app.desktop", "app.desktop"),
        ("vendor/app.desktop", "vendor-app.desktop"),
        ("a/b/c/app.desktop", "a-b-c-app.desktop"),
    ],
)
def test_desktop_id_for(relative, expected):
    root = Path("/data/applications")
    assert desktop_id_for(root, root / relative) == expected


def test_desktop_id_is_not_the_basename(precedence_roots):
    result = discover_applications(roots=roots_from(precedence_roots))
    assert "nested-vendor-Deep.desktop" in result.applications
    assert "Deep.desktop" not in result.applications


# ----------------------------------------------------------------------
# §7.4 precedence and masking
# ----------------------------------------------------------------------


@pytest.fixture
def resolved(precedence_roots):
    return discover_applications(roots=roots_from(precedence_roots))


def test_highest_precedence_copy_wins(resolved):
    app = resolved.applications["org.example.Overridden.desktop"]
    assert app.name == "Overridden (data_home)"
    assert app.exec_argv == ["/usr/bin/overridden", "--from", "data_home"]
    assert app.source_root.name == "applications"
    assert app.source_root.parent.name == "data_home"


def test_nested_id_resolves_to_the_highest_precedence_copy(resolved):
    app = resolved.applications["nested-vendor-Deep.desktop"]
    assert app.name == "Deep Nested (data_home)"


def test_hidden_masks_all_lower_priority_copies(resolved):
    """Rule 3: never skip Hidden=true and then fall back to a lower copy."""
    assert "org.example.MaskedByUser.desktop" not in resolved.applications
    assert "org.example.MaskedByUser.desktop" in resolved.masked_ids
    masking_file = resolved.masked_ids["org.example.MaskedByUser.desktop"]
    assert masking_file.parent.parent.name == "data_home"

    # And the two lower-priority copies really were seen and rejected.
    shadowed_paths = [path for name, path in resolved.shadowed if "MaskedByUser" in name]
    assert len(shadowed_paths) == 2


def test_masking_works_from_a_middle_priority_root(resolved):
    assert "org.example.MaskedByLocal.desktop" not in resolved.applications
    assert "org.example.MaskedByLocal.desktop" in resolved.masked_ids
    assert resolved.masked_ids["org.example.MaskedByLocal.desktop"].parent.parent.name == (
        "data_dir_local"
    )


def test_non_application_entry_still_claims_its_id(resolved):
    """A higher-priority Type=Link must not be replaced by a lower Application."""
    app = resolved.applications["org.example.ShadowedByLink.desktop"]
    assert app.entry_type == "Link"
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.NOT_AN_APPLICATION
    assert app.name == "Shadowing Link"


def test_entries_unique_to_each_root_are_all_found(resolved):
    for desktop_id in (
        "org.example.UserOnly.desktop",
        "org.example.LocalOnly.desktop",
        "org.example.SystemOnly.desktop",
    ):
        assert desktop_id in resolved.applications


def test_resolution_counts(resolved):
    assert set(resolved.applications) == {
        "org.example.Overridden.desktop",
        "org.example.UserOnly.desktop",
        "org.example.ShadowedByLink.desktop",
        "nested-vendor-Deep.desktop",
        "org.example.LocalOnly.desktop",
        "org.example.SystemOnly.desktop",
    }
    assert set(resolved.masked_ids) == {
        "org.example.MaskedByUser.desktop",
        "org.example.MaskedByLocal.desktop",
    }
    assert len(resolved.shadowed) == 7
    assert resolved.errors == []
    assert len(resolved.importable) == 5


def test_reversing_root_order_changes_the_winner(precedence_roots):
    """Guards against an implementation that sorts or normalises root order."""
    result = discover_applications(roots=roots_from(list(reversed(precedence_roots))))
    assert result.applications["org.example.Overridden.desktop"].name == (
        "Overridden (data_dir_system)"
    )
    # With data_home last, its Hidden entry no longer masks anything.
    assert "org.example.MaskedByUser.desktop" in result.applications


def test_unparsable_file_does_not_claim_the_id(tmp_path):
    high = tmp_path / "high" / "applications"
    low = tmp_path / "low" / "applications"
    high.mkdir(parents=True)
    low.mkdir(parents=True)
    (high / "app.desktop").write_text("this file has no group header\n")
    (low / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Fallback\nExec=/bin/true\n"
    )

    result = discover_applications(roots=roots_from([high, low]))
    assert result.applications["app.desktop"].name == "Fallback"
    assert len(result.errors) == 1
    assert result.errors[0][0] == high / "app.desktop"


def test_non_desktop_files_are_ignored(tmp_path):
    root = tmp_path / "applications"
    root.mkdir()
    (root / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=A\nExec=/bin/true\n"
    )
    (root / "mimeinfo.cache").write_text("[MIME Cache]\n")
    (root / "notes.txt").write_text("hello\n")

    result = discover_applications(roots=roots_from([root]))
    assert list(result.applications) == ["app.desktop"]


def test_scan_order_is_deterministic(tmp_path):
    root = tmp_path / "applications"
    (root / "z").mkdir(parents=True)
    (root / "a").mkdir(parents=True)
    for name in ("b.desktop", "a.desktop"):
        for directory in (root, root / "a", root / "z"):
            (directory / name).write_text(
                "[Desktop Entry]\nType=Application\nName=X\nExec=/bin/true\n"
            )

    first = list(discover_applications(roots=roots_from([root])).applications)
    second = list(discover_applications(roots=roots_from([root])).applications)
    assert first == second

    # Ordering is lexical on the whole absolute path, not directory-by-
    # directory walk order, so a top-level file sorts against nested ones
    # rather than always preceding them: "a.desktop" < "a/a.desktop" because
    # "." < "/", and "a/b.desktop" < "b.desktop" because "a" < "b".
    #
    # The exact order matters beyond reproducibility. It is the tie-break that
    # decides which file wins a same-root desktop ID collision (OPEN-2), so it
    # has to be a property of the paths themselves and not of the order the
    # filesystem happened to hand directories to os.walk.
    assert first == [
        "a.desktop",
        "a-a.desktop",
        "a-b.desktop",
        "b.desktop",
        "z-a.desktop",
        "z-b.desktop",
    ]
