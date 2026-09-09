"""Steam Desktop Importer.

Imports FreeDesktop ``.desktop`` applications into Steam as non-Steam
shortcuts.

Implementation status: Phases 0–5 (discovery, launch adapters, GUI, Steam
targets, persistent state and AppID allocation). No code in this package
writes to any Steam directory. The Phase 5 store writes only
``$XDG_STATE_HOME/steam-desktop-importer/state.sqlite3``.
"""

__version__ = "0.0.1"
