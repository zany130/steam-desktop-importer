"""Steam running detection (IMPLEMENTATION.md §14).

Used by the status indicator and by Phase 7 to gate VDF writes.
"""

from __future__ import annotations

from types import SimpleNamespace

from steam_desktop_importer.steam.running import detect_steam_running


def _process(**info):
    return SimpleNamespace(info=info)


def test_name_steam_is_not_the_only_signal(monkeypatch):
    processes = [
        _process(name="firefox", exe="/usr/lib64/firefox/firefox", cmdline=["firefox"]),
        _process(
            name="MainThread",
            exe="/home/user/.local/share/Steam/ubuntu12_64/steamwebhelper",
            cmdline=["steamwebhelper"],
        ),
    ]
    monkeypatch.setattr(
        "steam_desktop_importer.steam.running.psutil.process_iter",
        lambda attrs: processes,
    )
    status = detect_steam_running()
    assert status.running is True
    assert any("ubuntu12_64" in reason or "steamwebhelper" in reason for reason in status.evidence)


def test_flatpak_steam_is_detected_from_the_app_id(monkeypatch):
    processes = [
        _process(
            name="bwrap",
            exe="/usr/bin/bwrap",
            cmdline=["bwrap", "com.valvesoftware.Steam"],
        )
    ]
    monkeypatch.setattr(
        "steam_desktop_importer.steam.running.psutil.process_iter",
        lambda attrs: processes,
    )
    status = detect_steam_running()
    assert status.running is True
    assert any("com.valvesoftware.steam" in reason for reason in status.evidence)


def test_unrelated_processes_are_a_negative(monkeypatch):
    processes = [
        _process(name="bash", exe="/usr/bin/bash", cmdline=["bash"]),
        _process(name="firefox", exe="/usr/lib64/firefox/firefox", cmdline=["firefox"]),
    ]
    monkeypatch.setattr(
        "steam_desktop_importer.steam.running.psutil.process_iter",
        lambda attrs: processes,
    )
    status = detect_steam_running()
    assert status.running is False
    assert status.is_certain is True
    assert status.evidence == ()


def test_inspection_failure_is_counted(monkeypatch):
    import psutil

    class Raising:
        @property
        def info(self):
            raise psutil.AccessDenied(pid=9)

    monkeypatch.setattr(
        "steam_desktop_importer.steam.running.psutil.process_iter",
        lambda attrs: [Raising()],
    )
    status = detect_steam_running()
    assert status.running is False
    assert status.inspection_failures == 1
    assert status.is_certain is False
