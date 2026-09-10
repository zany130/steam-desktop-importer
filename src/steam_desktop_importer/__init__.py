"""Steam Desktop Importer.

Imports FreeDesktop ``.desktop`` applications into Steam as non-Steam
shortcuts.

Implementation status: Phases 0–7 (discovery, launch adapters, GUI, Steam
targets, persistent state, AppID allocation, in-memory VDF read/update,
safe ``shortcuts.vdf`` commit). Live Steam writes go only through the
Phase 7 transaction in ``steam/commit.py``.
"""

__version__ = "0.0.1"
