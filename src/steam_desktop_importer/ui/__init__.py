"""PySide6 GUI (IMPLEMENTATION.md §25, Phase 3).

The window discovers applications and Steam targets and writes only the
Phase 5 SQLite store. It does not write shortcuts or artwork.
"""

from __future__ import annotations

from .main_window import MainWindow, run_app

__all__ = ["MainWindow", "run_app"]
