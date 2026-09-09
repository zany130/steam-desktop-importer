"""Launch adapter tests (IMPLEMENTATION.md §10, Phase 2).

Phase 2's requirement is to "verify command vectors without writing Steam", so
every test here asserts an argv and nothing touches a Steam directory. The
package-wide write guard in ``test_phase0_fixtures.py`` enforces the second
half of that independently.
"""

from __future__ import annotations

import pytest

from steam_desktop_importer.desktop.parser import build_application, parse_desktop_entry
from steam_desktop_importer.launch import (
    LaunchAdapterError,
    build_launch_vector,
    is_transient_appimage_path,
)

FLATPAK_EXPORTS = "flatpak_user_exports/share/applications"
SNAPD = "snapd_desktop/applications"
NATIVE = "native_data/applications"


def app_from(sources_dir, relative, filename):
    path = sources_dir / relative / filename
    return build_application(parse_desktop_entry(path), desktop_id=filename)


def vector_from(sources_dir, relative, filename):
    return build_launch_vector(app_from(sources_dir, relative, filename))


# ----------------------------------------------------------------------
# Native
# ----------------------------------------------------------------------


def test_native_uses_the_resolved_executable_and_arguments(sources_dir):
    vector = vector_from(sources_dir, NATIVE, "org.example.NativeApp.desktop")

    assert vector.adapter == "native"
    assert vector.exe == "/usr/bin/native-app"
    # %U expanded to nothing because no document is being passed.
    assert vector.arguments == ("--start",)
    assert vector.argv == ("/usr/bin/native-app", "--start")


def test_native_start_dir_comes_from_path_and_is_never_inferred(exec_grammar_dir):
    """Rule 6: StartDir is Path= or empty, never the executable's parent."""
    with_path = build_application(
        parse_desktop_entry(exec_grammar_dir / "exec-with-path.desktop"),
        desktop_id="exec-with-path.desktop",
    )
    assert build_launch_vector(with_path).start_dir == "/opt/example/workdir"

    without_path = build_application(
        parse_desktop_entry(exec_grammar_dir / "exec-quoting.desktop"),
        desktop_id="exec-quoting.desktop",
    )
    vector = build_launch_vector(without_path)
    assert vector.start_dir == ""
    # The tempting wrong answer, spelled out so a future change has to be
    # deliberate rather than accidental.
    assert vector.start_dir != str(without_path.desktop_path.parent)


def test_env_wrapper_stays_in_the_command(exec_grammar_dir):
    """Rule 5: env VAR=value must not migrate into Steam's LaunchOptions.

    The adapter keeps `env` as the executable and the assignments as leading
    arguments, so the variables survive into whatever Steam runs.
    """
    app = build_application(
        parse_desktop_entry(exec_grammar_dir / "exec-env-wrapper.desktop"),
        desktop_id="exec-env-wrapper.desktop",
    )
    vector = build_launch_vector(app)

    assert vector.exe == "env"
    assert vector.arguments == ("LANG=C", "GDK_BACKEND=x11", "/usr/bin/real-app", "--flag")
    # The wrong answer this rule exists to prevent: exe=/usr/bin/real-app with
    # the assignments hoisted out of the command.
    assert vector.exe != "/usr/bin/real-app"


# ----------------------------------------------------------------------
# Flatpak
# ----------------------------------------------------------------------


def test_flatpak_strips_uri_forwarding_scaffolding(sources_dir):
    """OPEN-3: @@u/@@ and --file-forwarding go once %U expands to nothing."""
    vector = vector_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakApp.desktop")

    assert vector.adapter == "flatpak"
    assert vector.exe == "/usr/bin/flatpak"
    assert vector.arguments == (
        "run",
        "--branch=stable",
        "--arch=x86_64",
        "--command=example-app",
        "org.example.FlatpakApp",
    )
    assert "@@u" not in vector.arguments
    assert "@@" not in vector.arguments
    assert "--file-forwarding" not in vector.arguments


def test_flatpak_strips_path_forwarding_scaffolding(sources_dir):
    """The @@ ... @@ variant is handled the same way as @@u ... @@."""
    vector = vector_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakPathForward.desktop")

    assert vector.arguments == (
        "run",
        "--branch=stable",
        "org.example.FlatpakPathForward",
    )


def test_flatpak_preserves_published_flags(sources_dir):
    """The exported command carries flags that the app ID alone cannot.

    Rebuilding as `flatpak run <id>` would silently drop --nosocket and --env,
    which change how the application runs.
    """
    vector = vector_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakNoMetadata.desktop")

    assert vector.exe == "flatpak"
    assert vector.arguments == (
        "run",
        "--nosocket=wayland",
        "--env=FOO=bar",
        "org.example.FlatpakNoMetadata",
        "--app-flag",
    )


def test_flatpak_does_not_assume_the_last_token_is_the_app_id(sources_dir):
    """§10 says so explicitly, and this fixture is the counterexample.

    `--app-flag` trails the application ID, so any adapter that treated the
    final token as the ID would build a broken command.
    """
    app = app_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakNoMetadata.desktop")
    vector = build_launch_vector(app)

    assert vector.arguments[-1] == "--app-flag"
    assert app.flatpak_id == "org.example.FlatpakNoMetadata"
    assert app.flatpak_id in vector.arguments
    assert vector.arguments.index(app.flatpak_id) < len(vector.arguments) - 1


def test_flatpak_full_ref_is_preserved_rather_than_normalised(sources_dir):
    """A full `app/<id>/<arch>/<branch>` ref is a valid argument to flatpak run."""
    vector = vector_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakRef.desktop")

    assert vector.arguments == ("run", "app/org.example.FlatpakRef/x86_64/stable")


def test_flatpak_warns_when_the_declared_id_is_absent_from_argv(sources_dir):
    """A mismatch is reported, not silently repaired in either direction."""
    app = app_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakRef.desktop")
    app.flatpak_id = "org.example.Different"

    vector = build_launch_vector(app)
    assert any("does not appear" in warning for warning in vector.warnings)
    # Warned about, but the command itself is untouched.
    assert vector.arguments == ("run", "app/org.example.FlatpakRef/x86_64/stable")


def test_markers_without_the_flag_are_ordinary_arguments(sources_dir):
    """flatpak only interprets @@ when --file-forwarding is present."""
    app = app_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakRef.desktop")
    app.exec_argv = ["/usr/bin/flatpak", "run", "org.example.X", "@@u", "@@"]

    vector = build_launch_vector(app)
    assert vector.arguments == ("run", "org.example.X", "@@u", "@@")


def test_a_non_empty_forwarding_region_is_left_alone(sources_dir):
    """Half-removing a forwarding block would be worse than not touching it."""
    app = app_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakApp.desktop")
    app.exec_argv = [
        "/usr/bin/flatpak",
        "run",
        "--file-forwarding",
        "org.example.X",
        "@@u",
        "/tmp/doc",
        "@@",
    ]

    vector = build_launch_vector(app)
    assert vector.arguments == (
        "run",
        "--file-forwarding",
        "org.example.X",
        "@@u",
        "/tmp/doc",
        "@@",
    )
    assert any("still contains arguments" in warning for warning in vector.warnings)


def test_an_unterminated_forwarding_region_is_left_alone(sources_dir):
    app = app_from(sources_dir, FLATPAK_EXPORTS, "org.example.FlatpakApp.desktop")
    app.exec_argv = ["/usr/bin/flatpak", "run", "--file-forwarding", "org.example.X", "@@u"]

    vector = build_launch_vector(app)
    assert vector.arguments == ("run", "--file-forwarding", "org.example.X", "@@u")
    assert any("unterminated" in warning for warning in vector.warnings)


# ----------------------------------------------------------------------
# Snap
# ----------------------------------------------------------------------


def test_snap_uses_the_exported_command_including_its_env_wrapper(sources_dir):
    """snapd generates these; the BAMF hint is part of the published command."""
    vector = vector_from(sources_dir, SNAPD, "example-snap_example-snap.desktop")

    assert vector.adapter == "snap"
    assert vector.exe == "env"
    hint = (
        "BAMF_DESKTOP_FILE_HINT=/var/lib/snapd/desktop/applications/"
        "example-snap_example-snap.desktop"
    )
    assert vector.arguments == (hint, "/snap/bin/example-snap")


def test_snap_does_not_invent_a_universal_executable_path(sources_dir):
    """§10 forbids synthesising /snap/bin/<name> when the entry has a command."""
    vector = vector_from(sources_dir, SNAPD, "other-snap_other-snap.desktop")

    assert vector.exe == "/snap/bin/other-snap"
    assert vector.arguments == ("--flag",)


# ----------------------------------------------------------------------
# AppImage
# ----------------------------------------------------------------------


def test_integrated_appimage_is_a_normal_executable(sources_dir):
    vector = vector_from(sources_dir, NATIVE, "org.example.AppImage.desktop")

    assert vector.adapter == "appimage"
    assert vector.exe == "/home/example/Applications/Example_Tool-1.2.3-x86_64.AppImage"
    assert vector.arguments == ()


def test_unintegrated_appimage_is_refused(sources_dir):
    """A path inside the runtime mount will not exist next time."""
    app = app_from(sources_dir, NATIVE, "org.example.AppImageTransient.desktop")

    with pytest.raises(LaunchAdapterError) as raised:
        build_launch_vector(app)
    assert raised.value.code == "appimage_not_integrated"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/tmp/.mount_Exampl3XY/AppRun", True),
        # The runtime honours $TMPDIR, so the mount is not always under /tmp.
        ("/run/user/1000/.mount_abc123/AppRun", True),
        ("/home/example/Applications/Tool.AppImage", False),
        ("/opt/tools/mounted/Tool.AppImage", False),
        ("/usr/bin/native-app", False),
    ],
)
def test_transient_mount_detection(path, expected):
    assert is_transient_appimage_path(path) is expected


# ----------------------------------------------------------------------
# Dispatch and refusal
# ----------------------------------------------------------------------


def test_an_unsupported_application_is_refused(sources_dir):
    """The adapter must not route around Phase 1's support determination."""
    app = app_from(sources_dir, NATIVE, "org.example.NativeApp.desktop")
    app.supported_for_import = False
    app.unsupported_reason = "Terminal=true"
    app.unsupported_code = "terminal_unsupported"

    with pytest.raises(LaunchAdapterError) as raised:
        build_launch_vector(app)
    assert raised.value.code == "terminal_unsupported"


def test_an_unknown_source_has_no_adapter(sources_dir):
    app = app_from(sources_dir, NATIVE, "org.example.NativeApp.desktop")
    app.source_kind = "unknown"

    with pytest.raises(LaunchAdapterError) as raised:
        build_launch_vector(app)
    assert raised.value.code == "no_adapter_for_source"


def test_every_source_kind_except_unknown_has_an_adapter():
    """SOURCE_KINDS and ADAPTERS must not drift apart."""
    from steam_desktop_importer.launch import ADAPTERS
    from steam_desktop_importer.models import SOURCE_KINDS

    assert set(ADAPTERS) == set(SOURCE_KINDS) - {"unknown"}
