"""SQLite state store (IMPLEMENTATION.md §6, Phase 5)."""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.state import StateStore, default_state_path, xdg_state_home
from steam_desktop_importer.steam.appid import allocate_appid, first_import_candidate


def test_xdg_state_home_defaults_and_rejects_relative_values(tmp_path):
    home = tmp_path / "home"
    assert xdg_state_home({}, home) == home / ".local" / "state"
    assert xdg_state_home({"XDG_STATE_HOME": "relative"}, home) == home / ".local" / "state"
    assert xdg_state_home({"XDG_STATE_HOME": "/var/state"}, home) == Path("/var/state")
    assert default_state_path({}, home) == home / ".local" / "state" / "steam-desktop-importer" / "state.sqlite3"


def test_name_and_exec_changes_keep_the_appid():
    with StateStore(":memory:") as store:
        first = store.save_mapping(
            "native:/tmp/Steam",
            11111111,
            "org.example.App.desktop",
            0x80000001,
            "Old Name",
            "/usr/bin/old",
            "/tmp/org.example.App.desktop",
        )
        second = store.save_mapping(
            "native:/tmp/Steam",
            11111111,
            "org.example.App.desktop",
            0x89999999,
            "New Name",
            "/usr/bin/new %U",
            "/tmp/org.example.App.desktop",
        )
        assert second.steam_appid_unsigned == first.steam_appid_unsigned == 0x80000001
        assert second.last_known_name == "New Name"
        assert second.last_known_exec == "/usr/bin/new %U"
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at


def test_install_and_account_mappings_are_separate():
    with StateStore(":memory:") as store:
        store.save_mapping("native:/a", 1, "app.desktop", 0x80000001, "A", "/bin/a", None)
        store.save_mapping("native:/a", 2, "app.desktop", 0x80000002, "A", "/bin/a", None)
        store.save_mapping("flatpak:/b", 1, "app.desktop", 0x80000003, "A", "/bin/a", None)

        assert store.get_mapping("native:/a", 1, "app.desktop").steam_appid_unsigned == 0x80000001
        assert store.get_mapping("native:/a", 2, "app.desktop").steam_appid_unsigned == 0x80000002
        assert store.get_mapping("flatpak:/b", 1, "app.desktop").steam_appid_unsigned == 0x80000003
        assert store.occupied_appids("native:/a", 1) == {0x80000001}
        assert store.occupied_appids("native:/a", 2) == {0x80000002}


def test_allocation_avoids_appids_already_in_the_store():
    desktop_id = "org.example.Collide.desktop"
    with StateStore(":memory:") as store:
        taken = first_import_candidate(desktop_id)
        store.save_mapping("native:/a", 1, "other.desktop", taken, "Other", "/bin/other", None)
        occupied = store.occupied_appids("native:/a", 1)
        allocated = allocate_appid(desktop_id, occupied)
        assert allocated != taken
        store.save_mapping("native:/a", 1, desktop_id, allocated, "Collide", "/bin/c", None)
        assert store.get_mapping("native:/a", 1, desktop_id).steam_appid_unsigned == allocated


def test_same_appid_may_exist_under_a_different_account():
    """Collision checks are per installation+account, not global."""
    with StateStore(":memory:") as store:
        store.save_mapping("native:/a", 1, "one.desktop", 0x80000001, "One", "/bin/one", None)
        store.save_mapping("native:/a", 2, "two.desktop", 0x80000001, "Two", "/bin/two", None)
        assert store.get_mapping("native:/a", 1, "one.desktop").steam_appid_unsigned == 0x80000001
        assert store.get_mapping("native:/a", 2, "two.desktop").steam_appid_unsigned == 0x80000001


def test_remembered_installation_and_per_install_account():
    with StateStore(":memory:") as store:
        assert store.remembered_installation() is None
        store.remember_installation("native:/a")
        store.remember_account("native:/a", 111)
        store.remember_account("flatpak:/b", 222)
        assert store.remembered_installation() == "native:/a"
        assert store.remembered_account("native:/a") == 111
        assert store.remembered_account("flatpak:/b") == 222
        store.remember_installation("flatpak:/b")
        assert store.remembered_installation() == "flatpak:/b"


def test_steam_poll_preferences_default_and_round_trip():
    from steam_desktop_importer.state import (
        DEFAULT_STEAM_POLL_MS,
        MAX_STEAM_POLL_MS,
        MIN_STEAM_POLL_MS,
        clamp_steam_poll_ms,
    )

    assert clamp_steam_poll_ms(0) == MIN_STEAM_POLL_MS
    assert clamp_steam_poll_ms(999_999) == MAX_STEAM_POLL_MS
    with StateStore(":memory:") as store:
        assert store.steam_poll_enabled() is True
        assert store.steam_poll_ms() == DEFAULT_STEAM_POLL_MS
        store.set_steam_poll(enabled=False, interval_ms=5000)
        assert store.steam_poll_enabled() is False
        assert store.steam_poll_ms() == 5000
        store.set_steam_poll(enabled=True, interval_ms=50)
        assert store.steam_poll_enabled() is True
        assert store.steam_poll_ms() == MIN_STEAM_POLL_MS
        assert store.flatpak_steam_host_launch() is True
        store.set_flatpak_steam_host_launch(False)
        assert store.flatpak_steam_host_launch() is False
        store.set_steam_poll(enabled=True, interval_ms=2000)
        assert store.flatpak_steam_host_launch() is False
        store.set_flatpak_steam_host_launch(True)
        assert store.flatpak_steam_host_launch() is True


def test_collision_acknowledgements_are_bound_to_the_physical_winner(tmp_path):
    winner = tmp_path / "vendor-app.desktop"
    other = tmp_path / "vendor" / "app.desktop"
    winner.write_text("x")
    other.parent.mkdir()
    other.write_text("y")
    with StateStore(":memory:") as store:
        assert store.acknowledged_collisions() == ()
        first = store.acknowledge_collision("vendor-app.desktop", winner, (winner, other))
        store.acknowledge_collision("vendor-app.desktop", winner, (winner, other))
        acks = store.acknowledged_collisions()
        assert len(acks) == 1
        assert acks[0].desktop_id == "vendor-app.desktop"
        assert acks[0].matches("vendor-app.desktop", winner, (winner, other))
        assert acks[0].fingerprint == first.fingerprint
        assert not acks[0].matches("vendor-app.desktop", other, (winner, other))


def test_file_backed_store_round_trips(tmp_path):
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        store.save_mapping("native:/a", 1, "app.desktop", 0x80000001, "A", "/bin/a", None)
        store.acknowledge_collision(
            "app.desktop",
            tmp_path / "app.desktop",
            [tmp_path / "app.desktop", tmp_path / "nested" / "app.desktop"],
        )
    with StateStore(path) as store:
        assert store.get_mapping("native:/a", 1, "app.desktop").last_known_name == "A"
        acks = store.acknowledged_collisions()
        assert len(acks) == 1
        assert acks[0].desktop_id == "app.desktop"
        assert acks[0].matches(
            "app.desktop",
            tmp_path / "app.desktop",
            [tmp_path / "app.desktop", tmp_path / "nested" / "app.desktop"],
        )


def test_create_false_does_not_create_a_missing_file(tmp_path):
    path = tmp_path / "missing" / "state.sqlite3"
    with StateStore(path, create=False) as store:
        assert store.get_mapping("native:/a", 1, "app.desktop") is None
    assert not path.exists()
    assert not path.parent.exists()


def test_desktop_id_only_acknowledgement_rows_are_discarded(tmp_path):
    """Phase 5 rows that named only a desktop ID must not remain valid."""
    import sqlite3

    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE acknowledged_collisions (
            desktop_id TEXT PRIMARY KEY,
            acknowledged_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO acknowledged_collisions VALUES ('vendor-app.desktop', '2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    with StateStore(path) as store:
        assert store.acknowledged_collisions() == ()
        store.acknowledge_collision(
            "vendor-app.desktop",
            tmp_path / "vendor-app.desktop",
            [tmp_path / "vendor-app.desktop", tmp_path / "vendor" / "app.desktop"],
        )
    with StateStore(path) as store:
        assert store.acknowledged_collisions()[0].desktop_id == "vendor-app.desktop"
