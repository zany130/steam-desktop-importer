"""Steam Desktop Importer.

Imports FreeDesktop ``.desktop`` applications into Steam as non-Steam
shortcuts.

Implementation status: Phases 0–12 on native Steam. Phase 11 Flatpak Steam
host launching is experimental and fixture-tested. Live Steam writes go only
through ``steam/commit.py`` (VDF), ``steam/artwork.py`` (grid files), and
``steam/collection_commit.py`` (cloud-storage collections JSON).
"""

__version__ = "1.0.0"
