"""Steam Desktop Importer.

Imports FreeDesktop ``.desktop`` applications into Steam as non-Steam
shortcuts.

Implementation status: Phases 0–9 (discovery, launch adapters, GUI, Steam
targets, persistent state, AppID allocation, VDF read/update, safe
``shortcuts.vdf`` commit, SteamGridDB client, artwork UI and ``grid/``
placement). Live Steam writes go only through ``steam/commit.py`` (VDF) and
``steam/artwork.py`` (grid files).
"""

__version__ = "0.0.1"
