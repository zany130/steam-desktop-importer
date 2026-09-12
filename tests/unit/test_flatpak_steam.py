"""Phase 11 Flatpak Steam host-launch wrap. Never writes Steam; never grants permissions."""

from __future__ import annotations

from subprocess import CompletedProcess, TimeoutExpired

from steam_desktop_importer.desktop.parser import build_application, parse_desktop_entry
from steam_desktop_importer.launch import (
    FLATPAK_PORTAL_INTERFACE,
    LaunchVector,
    MANUAL_OVERRIDE_COMMAND,
    build_launch_vector,
    is_host_wrapped,
    probe_host_launch_permission,
    wrap_for_flatpak_steam,
)

FLATPAK_EXPORTS = "flatpak_user_exports/share/applications"
SNAPD = "snapd_desktop/applications"
NATIVE = "native_data/applications"


def _vector_from(sources_dir, relative, filename):
    path = sources_dir / relative / filename
    app = build_application(parse_desktop_entry(path), desktop_id=filename)
    return build_launch_vector(app)

GRANTED = """
[Context]
shared=network;

[Session Bus Policy]
org.freedesktop.portal.*=talk
org.freedesktop.Flatpak=talk
"""

DENIED = """
[Context]
shared=network;

[Session Bus Policy]
org.freedesktop.portal.*=talk
"""


def _vector() -> LaunchVector:
    return LaunchVector(
        exe="/usr/bin/example",
        arguments=("--flag", "two words"),
        start_dir="/home/example",
        adapter="native",
    )


def test_wrap_prefixes_flatpak_spawn_host():
    wrapped = wrap_for_flatpak_steam(_vector(), spawn_path="/usr/bin/flatpak-spawn")
    assert wrapped.exe == "/usr/bin/flatpak-spawn"
    assert wrapped.arguments == ("--host", "/usr/bin/example", "--flag", "two words")
    assert wrapped.start_dir == "/home/example"
    assert wrapped.adapter == "native+flatpak-steam-host"
    assert is_host_wrapped(wrapped) is True
    assert wrap_for_flatpak_steam(wrapped, spawn_path="/other/flatpak-spawn") is wrapped


def test_probe_detects_granted_talk_permission():
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return CompletedProcess(argv, 0, stdout=GRANTED, stderr="")

    permission = probe_host_launch_permission(run=run)
    assert permission.granted is True
    assert FLATPAK_PORTAL_INTERFACE in permission.evidence
    assert permission.override_command == MANUAL_OVERRIDE_COMMAND
    for argv in calls:
        assert "--talk-name" not in argv
        if "override" in argv:
            assert "--show" in argv


def test_probe_reports_missing_permission_and_never_grants():
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return CompletedProcess(argv, 0, stdout=DENIED, stderr="")

    permission = probe_host_launch_permission(run=run)
    assert permission.granted is False
    assert "never" in permission.evidence or "manual" in permission.evidence
    assert permission.override_command == MANUAL_OVERRIDE_COMMAND
    for argv in calls:
        assert "--talk-name" not in argv
        joined = " ".join(argv)
        assert MANUAL_OVERRIDE_COMMAND not in joined


def test_probe_rejects_own_permission():
    def run(argv, **kwargs):
        return CompletedProcess(
            argv,
            0,
            stdout="[Session Bus Policy]\norg.freedesktop.Flatpak=own\n",
            stderr="",
        )

    permission = probe_host_launch_permission(run=run)
    assert permission.granted is False


def test_probe_treats_timeout_as_missing_probe():
    def run(*_args, **_kwargs):
        raise TimeoutExpired(cmd="flatpak", timeout=8)

    permission = probe_host_launch_permission(run=run)
    assert permission.granted is False
    assert "could not read" in permission.evidence


def test_wrap_keeps_host_flatpak_snap_and_appimage_argv(sources_dir):
    spawn = "/usr/bin/flatpak-spawn"
    cases = (
        (FLATPAK_EXPORTS, "org.example.FlatpakApp.desktop"),
        (SNAPD, "example-snap_example-snap.desktop"),
        (NATIVE, "org.example.AppImage.desktop"),
        (NATIVE, "org.example.NativeApp.desktop"),
    )
    for relative, filename in cases:
        original = _vector_from(sources_dir, relative, filename)
        wrapped = wrap_for_flatpak_steam(original, spawn_path=spawn)
        assert wrapped.exe == spawn
        assert wrapped.arguments == ("--host", original.exe, *original.arguments)
        assert wrapped.start_dir == original.start_dir


def test_probe_handles_missing_flatpak_binary():
    def run(*_args, **_kwargs):
        raise FileNotFoundError("flatpak")

    permission = probe_host_launch_permission(run=run)
    assert permission.granted is False
    assert "could not read" in permission.evidence
