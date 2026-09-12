"""Package entry points and version metadata."""

from pathlib import Path

from steam_desktop_importer import __version__
from steam_desktop_importer.__main__ import main as module_main
from steam_desktop_importer.main import main

ROOT = Path(__file__).resolve().parents[2]


def test_module_entry_exposes_main():
    assert module_main is main
    assert callable(main)


def test_version_is_1_0_0_and_matches_pyproject():
    assert __version__ == "1.0.0"
    text = ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.0.0"' in text
