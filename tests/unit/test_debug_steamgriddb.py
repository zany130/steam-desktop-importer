"""``debug steamgriddb`` (IMPLEMENTATION.md §21, Phase 8)."""

from __future__ import annotations

from steam_desktop_importer.main import build_parser, main


def test_steamgriddb_search_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(["debug", "steamgriddb", "search", "Kate"])
    assert args.func.__name__ == "_print_sgdb_search"
    assert args.query == "Kate"


def test_steamgriddb_grids_command_is_registered():
    parser = build_parser()
    args = parser.parse_args(
        ["debug", "steamgriddb", "grids", "2254", "--dimensions", "600x900"]
    )
    assert args.func.__name__ == "_print_sgdb_assets"
    assert args.game_id == 2254
    assert args.dimensions == ["600x900"]


def test_steamgriddb_search_without_a_key_does_not_write(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SGDB_API_KEY", raising=False)
    assert main(["debug", "steamgriddb", "search", "Kate"]) == 2
    output = capsys.readouterr().out
    assert "SGDB_API_KEY" in output
    assert list(tmp_path.rglob("sgdb_api_key")) == []
