"""PySide6 GUI (IMPLEMENTATION.md §25, Phases 3–12).

The window discovers applications and Steam targets. Shortcut, artwork, and
collection writes go through the Steam-closed commit modules, not from these
widgets directly.
"""

from __future__ import annotations

from .main_window import MainWindow, run_app

__all__ = ["MainWindow", "run_app"]
