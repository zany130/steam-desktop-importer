"""PySide6 GUI (IMPLEMENTATION.md §25, Phase 3).

Read-only. The window discovers applications and Steam targets; it does not
write shortcuts, state, or artwork.
"""

from __future__ import annotations

from .main_window import MainWindow, run_app

__all__ = ["MainWindow", "run_app"]
