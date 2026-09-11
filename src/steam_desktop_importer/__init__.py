"""Steam Desktop Importer.

Imports FreeDesktop ``.desktop`` applications into Steam as non-Steam
shortcuts.

Implementation status: Phases 0–10 on native Steam, plus Phase 12 collection
membership. Live Steam writes go only through ``steam/commit.py`` (VDF),
``steam/artwork.py`` (grid files), and ``steam/collection_commit.py``
(cloud-storage collections JSON).
"""

__version__ = "0.0.1"
