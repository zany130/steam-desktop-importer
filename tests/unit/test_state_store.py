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


def test_collision_acknowledgements_are_global_to_the_desktop_id():
    with StateStore(":memory:") as store:
        assert store.acknowledged_collisions() == frozenset()
        store.acknowledge_collision("vendor-app.desktop")
        store.acknowledge_collision("vendor-app.desktop")
        assert store.acknowledged_collisions() == frozenset({"vendor-app.desktop"})


def test_file_backed_store_round_trips(tmp_path):
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        store.save_mapping("native:/a", 1, "app.desktop", 0x80000001, "A", "/bin/a", None)
        store.acknowledge_collision("app.desktop")
    with StateStore(path) as store:
        assert store.get_mapping("native:/a", 1, "app.desktop").last_known_name == "A"
        assert "app.desktop" in store.acknowledged_collisions()


def test_create_false_does_not_create_a_missing_file(tmp_path):
    path = tmp_path / "missing" / "state.sqlite3"
    with StateStore(path, create=False) as store:
        assert store.get_mapping("native:/a", 1, "app.desktop") is None
    assert not path.exists()
    assert not path.parent.exists()
