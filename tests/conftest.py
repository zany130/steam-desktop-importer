"""Shared fixture locations."""

from __future__ import annotations

import os
from pathlib import Path

# GUI tests construct QApplication. Offscreen keeps them display-independent
# and stops a leftover window from grabbing the session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DESKTOP_ENTRIES = FIXTURES / "desktop_entries"
XDG_PRECEDENCE = DESKTOP_ENTRIES / "xdg_precedence"
EXEC_GRAMMAR = DESKTOP_ENTRIES / "exec_grammar" / "applications"
ENTRY_SEMANTICS = DESKTOP_ENTRIES / "entry_semantics" / "applications"
SOURCES = DESKTOP_ENTRIES / "sources"
SHORTCUTS_VDF = FIXTURES / "shortcuts_vdf"
STEAM_CONFIG = FIXTURES / "steam_config"


@pytest.fixture
def exec_grammar_dir() -> Path:
    return EXEC_GRAMMAR


@pytest.fixture
def entry_semantics_dir() -> Path:
    return ENTRY_SEMANTICS


@pytest.fixture
def sources_dir() -> Path:
    return SOURCES


@pytest.fixture
def shortcuts_vdf_dir() -> Path:
    return SHORTCUTS_VDF


@pytest.fixture
def steam_config_dir() -> Path:
    return STEAM_CONFIG


@pytest.fixture
def precedence_roots() -> list[Path]:
    """The three ``applications/`` roots, in descending precedence order."""
    return [
        XDG_PRECEDENCE / "data_home" / "applications",
        XDG_PRECEDENCE / "data_dir_local" / "applications",
        XDG_PRECEDENCE / "data_dir_system" / "applications",
    ]
