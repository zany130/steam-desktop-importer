"""Desktop Entry parsing tests (IMPLEMENTATION.md §8, Phase 1)."""

from __future__ import annotations

import pytest

from steam_desktop_importer.desktop.parser import (
    DesktopEntryError,
    DesktopEntryFile,
    build_application,
    locale_candidates,
    parse_desktop_entry,
    resolve_try_exec,
)
from steam_desktop_importer.models import UnsupportedCode


def app_for(directory, filename, locale=None):
    parsed = parse_desktop_entry(directory / filename, locale=locale)
    return build_application(parsed, desktop_id=filename)


# ----------------------------------------------------------------------
# Key file grammar
# ----------------------------------------------------------------------


def test_whitespace_around_equals_is_ignored(entry_semantics_dir):
    parsed = parse_desktop_entry(entry_semantics_dir / "semantics-whitespace-and-actions.desktop")
    assert parsed.entry_type == "Application"
    assert parsed.name == "Whitespace And Actions"
    assert parsed.exec_result is not None
    assert parsed.exec_result.argv == ("/usr/bin/whitespace", "--main")


def test_action_group_keys_do_not_leak_into_the_main_group(entry_semantics_dir):
    """A Desktop Action's Terminal=true and Icon must not be read as the app's."""
    parsed = parse_desktop_entry(entry_semantics_dir / "semantics-whitespace-and-actions.desktop")
    assert parsed.terminal is False
    assert parsed.icon_name == "whitespace-actions"
    assert "Desktop Action NewWindow" in parsed.entry.groups


def test_missing_desktop_entry_group_is_an_error(entry_semantics_dir):
    with pytest.raises(DesktopEntryError, match="no \\[Desktop Entry\\] group"):
        parse_desktop_entry(entry_semantics_dir / "semantics-no-group.desktop")


def test_missing_name_is_warned_and_falls_back_to_the_file_stem(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-no-name.desktop")
    assert any("Name is missing" in warning for warning in app.parse_warnings)
    assert app.name == "semantics-no-name"


def test_boolean_parsing_rules(entry_semantics_dir):
    parsed = parse_desktop_entry(entry_semantics_dir / "semantics-boolean-variants.desktop")
    # Legacy numeric forms are accepted leniently.
    assert parsed.terminal is True
    assert parsed.hidden is False
    # Only lowercase "true"/"false" are valid, so these fall back to the default.
    assert parsed.no_display is False
    assert parsed.dbus_activatable is False
    assert sum("not a valid boolean" in w for w in parsed.warnings) == 2
    assert sum("legacy numeric boolean" in w for w in parsed.warnings) == 2


def test_malformed_hidden_fixture_is_treated_as_masking(entry_semantics_dir):
    parsed = parse_desktop_entry(entry_semantics_dir / "semantics-hidden-malformed.desktop")
    assert parsed.hidden is True
    assert any("not a valid boolean" in warning for warning in parsed.warnings)


def test_duplicate_keys_keep_the_first_value(tmp_path):
    path = tmp_path / "dupe.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=First\nName=Second\nExec=/bin/true\n")
    parsed = parse_desktop_entry(path)
    assert parsed.name == "First"
    assert any("duplicate key" in warning for warning in parsed.warnings)


# ----------------------------------------------------------------------
# Localization
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("de_DE.UTF-8@euro", ["de_DE@euro", "de_DE", "de@euro", "de"]),
        ("de_DE.UTF-8", ["de_DE", "de"]),
        ("de_DE", ["de_DE", "de"]),
        ("de", ["de"]),
        ("sr@latin", ["sr@latin", "sr"]),
        ("C", []),
        ("POSIX", []),
        (None, []),
        ("", []),
    ],
)
def test_locale_candidates_follow_specification_order(locale, expected):
    assert locale_candidates(locale) == expected


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        (None, None),
        ("de_DE.UTF-8", "Lokalisierte Anwendung (DE)"),
        ("de_AT", "Lokalisierte Anwendung"),
        ("de", "Lokalisierte Anwendung"),
        ("sr@latin", "Lokalizovana Aplikacija (latin)"),
        ("sr", "Lokalizovana Aplikacija"),
        ("fr_CA", "Application Localisee (CA)"),
        # No fr key at all, and fr_CA must not be used for a bare fr locale.
        ("fr", None),
        ("ja_JP.UTF-8", None),
    ],
)
def test_localized_name_resolution(entry_semantics_dir, locale, expected):
    parsed = parse_desktop_entry(
        entry_semantics_dir / "semantics-localized.desktop", locale=locale
    )
    assert parsed.localized_name == expected
    # The unlocalized name is always kept alongside it (§5.1).
    assert parsed.name == "Localized Application"


def test_encoding_in_the_locale_is_stripped_not_matched(tmp_path):
    path = tmp_path / "enc.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Base\n"
        "Name[de.UTF-8]=Should Not Match\nName[de]=Should Match\nExec=/bin/true\n"
    )
    parsed = parse_desktop_entry(path, locale="de.UTF-8")
    assert parsed.localized_name == "Should Match"


# ----------------------------------------------------------------------
# Semantics
# ----------------------------------------------------------------------


def test_hidden_is_parsed(entry_semantics_dir):
    parsed = parse_desktop_entry(entry_semantics_dir / "semantics-hidden.desktop")
    assert parsed.hidden is True


def test_nodisplay_is_visibility_not_deletion(entry_semantics_dir):
    """Rule 4: NoDisplay must not be conflated with deletion."""
    app = app_for(entry_semantics_dir, "semantics-nodisplay.desktop")
    assert app.no_display is True
    assert app.hidden is False
    assert app.supported_for_import is True


def test_terminal_is_unsupported_for_import(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-terminal.desktop")
    assert app.terminal is True
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.TERMINAL_UNSUPPORTED


def test_dbus_activatable_with_exec_fallback_is_supported(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-dbus-with-exec.desktop")
    assert app.dbus_activatable is True
    assert app.supported_for_import is True


def test_dbus_activatable_without_exec_is_unsupported(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-dbus-no-exec.desktop")
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.DBUS_ACTIVATABLE_NO_EXEC


@pytest.mark.parametrize(
    "filename",
    ["semantics-tryexec-absolute-present.desktop", "semantics-tryexec-bare-present.desktop"],
)
def test_resolvable_tryexec_is_supported(entry_semantics_dir, filename):
    app = app_for(entry_semantics_dir, filename)
    assert app.supported_for_import is True
    assert app.is_available is True


def test_unresolvable_tryexec_marks_the_entry_unavailable(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-tryexec-unresolvable.desktop")
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.TRYEXEC_UNRESOLVABLE
    # §25.1 distinguishes "Unavailable" from "Unsupported".
    assert app.is_available is False


def test_absent_tryexec_is_not_treated_as_unavailable(entry_semantics_dir):
    app = app_for(entry_semantics_dir, "semantics-tryexec-absent.desktop")
    assert app.try_exec is None
    assert app.supported_for_import is True


def test_resolve_try_exec():
    assert resolve_try_exec(None) is None
    assert resolve_try_exec("") is None
    assert resolve_try_exec("/bin/sh") is not None
    assert resolve_try_exec("sh") is not None
    assert resolve_try_exec("/definitely/not/here/at/all") is None


def test_path_maps_to_working_directory(exec_grammar_dir):
    app = app_for(exec_grammar_dir, "exec-with-path.desktop")
    assert app.working_directory == "/opt/example/workdir"


def test_absent_path_leaves_working_directory_empty(exec_grammar_dir):
    """Rule 6: never infer StartDir from the executable's parent directory."""
    app = app_for(exec_grammar_dir, "exec-no-path.desktop")
    assert app.working_directory is None
    assert app.exec_argv[0] == "/opt/example/bin/worker"


def test_show_in_lists_are_preserved(entry_semantics_dir):
    only = parse_desktop_entry(entry_semantics_dir / "semantics-onlyshowin.desktop")
    assert only.only_show_in == ["GNOME", "Unity"]
    assert only.not_show_in == []

    not_in = parse_desktop_entry(entry_semantics_dir / "semantics-notshowin.desktop")
    assert not_in.not_show_in == ["KDE"]
    assert not_in.only_show_in == []


def test_escaped_semicolon_in_a_list_value(tmp_path):
    path = tmp_path / "list.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=L\nExec=/bin/true\n"
        "OnlyShowIn=One;Two\\;Half;Three;\n"
    )
    parsed = parse_desktop_entry(path)
    assert parsed.only_show_in == ["One", "Two;Half", "Three"]


@pytest.mark.parametrize(
    ("filename", "expected_type"),
    [
        ("semantics-type-link.desktop", "Link"),
        ("semantics-type-directory.desktop", "Directory"),
    ],
)
def test_non_application_types_are_not_import_candidates(
    entry_semantics_dir, filename, expected_type
):
    app = app_for(entry_semantics_dir, filename)
    assert app.entry_type == expected_type
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.NOT_AN_APPLICATION


@pytest.mark.parametrize("filename", ["exec-missing.desktop", "exec-empty.desktop"])
def test_missing_or_empty_exec_is_unsupported(exec_grammar_dir, filename):
    app = app_for(exec_grammar_dir, filename)
    assert app.supported_for_import is False
    assert app.unsupported_code == UnsupportedCode.NO_EXEC


# ----------------------------------------------------------------------
# Source kind detection (§10 inputs)
# ----------------------------------------------------------------------


def test_flatpak_detected_from_metadata_not_the_last_token(sources_dir):
    directory = sources_dir / "flatpak_user_exports" / "share" / "applications"
    app = app_for(directory, "org.example.FlatpakApp.desktop")
    assert app.source_kind == "flatpak"
    assert app.flatpak_id == "org.example.FlatpakApp"
    # The last argv token is a file-forwarding marker, not the app ID.
    assert app.exec_argv[-1] == "@@"


def test_flatpak_app_id_recovered_from_argv_when_metadata_is_absent(sources_dir):
    directory = sources_dir / "flatpak_user_exports" / "share" / "applications"
    app = app_for(directory, "org.example.FlatpakNoMetadata.desktop")
    assert app.source_kind == "flatpak"
    assert app.flatpak_id == "org.example.FlatpakNoMetadata"


def test_flatpak_full_ref_is_reduced_to_an_app_id(sources_dir):
    directory = sources_dir / "flatpak_user_exports" / "share" / "applications"
    app = app_for(directory, "org.example.FlatpakRef.desktop")
    assert app.source_kind == "flatpak"
    assert app.flatpak_id == "org.example.FlatpakRef"


def test_snap_detected_behind_an_env_wrapper(sources_dir):
    directory = sources_dir / "snapd_desktop" / "applications"
    app = app_for(directory, "example-snap_example-snap.desktop")
    assert app.source_kind == "snap"
    assert app.snap_instance == "example-snap"
    # The wrapper itself is still preserved verbatim.
    assert app.exec_argv[0] == "env"


def test_snap_detected_from_the_snap_bin_path(sources_dir):
    directory = sources_dir / "snapd_desktop" / "applications"
    app = app_for(directory, "other-snap_other-snap.desktop")
    assert app.source_kind == "snap"
    assert app.snap_instance == "other-snap"


def test_appimage_detected(sources_dir):
    directory = sources_dir / "native_data" / "applications"
    app = app_for(directory, "org.example.AppImage.desktop")
    assert app.source_kind == "appimage"
    assert app.exec_argv[0].endswith(".AppImage")


def test_native_application(sources_dir):
    directory = sources_dir / "native_data" / "applications"
    app = app_for(directory, "org.example.NativeApp.desktop")
    assert app.source_kind == "native"
    assert app.flatpak_id is None
    assert app.snap_instance is None


def test_entry_with_no_exec_has_unknown_source(exec_grammar_dir):
    app = app_for(exec_grammar_dir, "exec-missing.desktop")
    assert app.source_kind == "unknown"


# ----------------------------------------------------------------------
# Low-level file object
# ----------------------------------------------------------------------


def test_desktop_entry_file_accessors(entry_semantics_dir):
    entry = DesktopEntryFile.load(entry_semantics_dir / "semantics-localized.desktop")
    assert entry.raw("Type") == "Application"
    assert entry.string("Name") == "Localized Application"
    assert entry.has("GenericName")
    assert not entry.has("NoSuchKey")
    assert entry.string("NoSuchKey") is None
    assert entry.boolean("NoSuchKey", default=True) is True
