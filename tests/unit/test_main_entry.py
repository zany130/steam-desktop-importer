"""Package entry points."""

from steam_desktop_importer.__main__ import main as module_main
from steam_desktop_importer.main import main


def test_module_entry_exposes_main():
    assert module_main is main
    assert callable(main)
