"""Phase 12 collection document: parse, mask, mutate in memory."""

from __future__ import annotations

import json
from pathlib import Path

from steam_desktop_importer.models import SteamAccount
from steam_desktop_importer.steam.collections import (
    CollectionAssignment,
    CollectionDocument,
    is_assignable_collection_id,
    load_collections,
)


def _account(root: Path, account_id32: int = 11111111) -> SteamAccount:
    return SteamAccount(
        steam_id64="76561197971376839",
        account_id32=account_id32,
        account_name="tester",
        persona_name="Tester",
        userdata_dir=root / "userdata" / str(account_id32),
        selection_hints=[],
    )


def _value(collection_id: str, name: str, added: list[int], removed: list[int] | None = None) -> str:
    payload = {
        "id": collection_id,
        "name": name,
        "added": added,
        "removed": [] if removed is None else removed,
    }
    return json.dumps(payload, separators=(",", ":"))


def _live(key: str, value: str, version: str = "10") -> list:
    return [
        key,
        {
            "key": key,
            "timestamp": 1700000000,
            "value": value,
            "version": version,
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
        },
    ]


def seed_cloud_storage(account: SteamAccount) -> Path:
    cloud = account.userdata_dir / "config" / "cloudstorage"
    cloud.mkdir(parents=True)
    entries = [
        [
            "showcases.example",
            {
                "key": "showcases.example",
                "timestamp": 1,
                "value": '{"keep":true}',
                "version": "1973",
            },
        ],
        [
            "user-collections.uc-AAAA",
            {
                "key": "user-collections.uc-AAAA",
                "timestamp": 2,
                "is_deleted": True,
                "version": "8",
            },
        ],
        _live(
            "user-collections.uc-BBBB",
            _value("uc-BBBB", "Linux Apps", [10, 20]),
            version="12",
        ),
        _live(
            "user-collections.srm-RW11",
            _value("srm-RW11", "Emulation", [0x80000001]),
            version="12",
        ),
        _live(
            "user-collections.favorite",
            _value("favorite", "Favorites", [100]),
            version="12",
        ),
        _live(
            "user-collections.hidden",
            _value("hidden", "Hidden", [999]),
            version="12",
        ),
        _live(
            "user-collections.from-tag-Action",
            _value("from-tag-Action", "Action", [7]),
            version="12",
        ),
        _live(
            "user-collections.uc-CCCC",
            _value("uc-CCCC", "Tools", [0xD14E78F7], removed=[55]),
            version="1788993193",  # third-party timestamp-as-version; ignore for bump
        ),
    ]
    (cloud / "cloud-storage-namespace-1.json").write_text(
        json.dumps(entries, separators=(",", ":")), encoding="utf-8"
    )
    (cloud / "cloud-storage-namespaces.json").write_text(
        '[[3,"39"],[1,"12"]]', encoding="utf-8"
    )
    (cloud / "cloud-storage-namespace-1.modified.json").write_text("[]", encoding="utf-8")
    (cloud / "cloud-storage-namespace-3.json").write_text(
        json.dumps(
            [["495140", {"key": "495140", "timestamp": 1, "value": "{}", "version": "39"}]],
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return cloud


def test_assignable_filter_skips_hidden_and_from_tag():
    assert is_assignable_collection_id("uc-BBBB") is True
    assert is_assignable_collection_id("favorite") is True
    assert is_assignable_collection_id("srm-RW11") is True
    assert is_assignable_collection_id("sdi-abc") is True
    assert is_assignable_collection_id("hidden") is False
    assert is_assignable_collection_id("from-tag-Action") is False


def test_load_lists_live_collections_and_prefers_namespace_1(tmp_path):
    account = _account(tmp_path / "Steam")
    seed_cloud_storage(account)
    document = load_collections(account)
    assert document.namespace_id == 1
    live = {item.collection_id: item for item in document.live_collections()}
    assert "uc-AAAA" not in live  # deleted
    assert live["uc-BBBB"].name == "Linux Apps"
    assert live["uc-BBBB"].added == (10, 20)
    assert live["hidden"].assignable is False
    assert live["from-tag-Action"].assignable is False
    assignable = {item.collection_id for item in document.assignable_collections()}
    assert assignable == {"uc-BBBB", "srm-RW11", "favorite", "uc-CCCC"}


def test_add_to_existing_uses_unsigned_32_bit_and_clears_removed(tmp_path):
    account = _account(tmp_path / "Steam")
    seed_cloud_storage(account)
    document = load_collections(account)
    errors = document.apply_assignment(
        CollectionAssignment(existing_ids=("uc-CCCC",)),
        [0xD14E78F7, 55],
        now=1_800_000_000,
    )
    assert errors == []
    tools = next(item for item in document.live_collections() if item.collection_id == "uc-CCCC")
    assert 0xD14E78F7 in tools.added
    assert 55 in tools.added
    assert 55 not in tools.removed
    item = document._find_item("uc-CCCC")
    assert item is not None
    record = item[1]
    assert record["version"] == "13"  # steam counter 12 + 1, not timestamp+1
    assert record["timestamp"] == 1_800_000_000
    index = {int(pair[0]): pair[1] for pair in document.index}
    assert index[1] == "13"


def test_create_collection_uses_sdi_prefix_and_name_match(tmp_path):
    account = _account(tmp_path / "Steam")
    seed_cloud_storage(account)
    document = load_collections(account)
    showcase_before = json.loads(json.dumps(document.entries[0]))
    errors = document.apply_assignment(
        CollectionAssignment(create_names=("Linux Apps", "Desktop")),
        [3511661831],
        now=1_800_000_000,
    )
    assert errors == []
    linux = next(item for item in document.live_collections() if item.collection_id == "uc-BBBB")
    assert 3511661831 in linux.added
    created = [item for item in document.live_collections() if item.collection_id.startswith("sdi-")]
    assert len(created) == 1
    assert created[0].name == "Desktop"
    assert created[0].added == (3511661831,)
    assert document.entries[0] == showcase_before
    hidden = next(item for item in document.live_collections() if item.collection_id == "hidden")
    assert hidden.added == (999,)


def test_missing_cloud_storage_is_an_empty_document(tmp_path):
    account = _account(tmp_path / "Steam")
    document = CollectionDocument.load(account)
    assert document.live_collections() == []
    document.apply_assignment(
        CollectionAssignment(create_names=("Desktop",)),
        [1],
        now=10,
    )
    created = document.live_collections()
    assert len(created) == 1
    assert created[0].collection_id.startswith("sdi-")
    assert document.index[0][1] == "1"


def test_refuses_hidden_and_from_tag_even_if_requested(tmp_path):
    account = _account(tmp_path / "Steam")
    seed_cloud_storage(account)
    document = load_collections(account)
    errors = document.apply_assignment(
        CollectionAssignment(existing_ids=("hidden", "from-tag-Action", "missing")),
        [1],
        now=10,
    )
    assert any("hidden" in error for error in errors)
    assert any("from-tag-Action" in error for error in errors)
    assert any("missing" in error for error in errors)
    hidden = next(item for item in document.live_collections() if item.collection_id == "hidden")
    assert hidden.added == (999,)


def test_scalar_added_or_removed_is_skipped_without_crashing(tmp_path):
    account = _account(tmp_path / "Steam")
    cloud = seed_cloud_storage(account)
    path = cloud / "cloud-storage-namespace-1.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries.append(
        _live(
            "user-collections.uc-BAD1",
            json.dumps(
                {"id": "uc-BAD1", "name": "Bad1", "added": 7, "removed": []},
                separators=(",", ":"),
            ),
            version="12",
        )
    )
    entries.append(
        _live(
            "user-collections.uc-BAD2",
            json.dumps(
                {"id": "uc-BAD2", "name": "Bad2", "added": [], "removed": 8},
                separators=(",", ":"),
            ),
            version="12",
        )
    )
    path.write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")
    document = load_collections(account)
    ids = {collection.collection_id for collection in document.live_collections()}
    assert "uc-BAD1" not in ids
    assert "uc-BAD2" not in ids
