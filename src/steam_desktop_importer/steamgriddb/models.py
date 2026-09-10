"""SteamGridDB data types (IMPLEMENTATION.md §21–§23)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ArtworkKind",
    "GameResult",
    "GridAsset",
]


class ArtworkKind:
    """Logical artwork kinds this importer understands."""

    GRID = "grid"
    HERO = "hero"
    LOGO = "logo"
    ICON = "icon"


@dataclass(frozen=True)
class GameResult:
    """One hit from ``/search/autocomplete/{term}``."""

    id: int
    name: str
    types: tuple[str, ...] = ()
    verified: bool = False


@dataclass(frozen=True)
class GridAsset:
    """One downloadable artwork object.

    ``url`` is the full-size asset. ``thumb`` is a preview. Neither is
    fetched until :meth:`~steam_desktop_importer.steamgriddb.client.SteamGridDBClient.download_asset`.
    """

    id: int
    kind: str
    url: str
    thumb: str = ""
    style: str = ""
    score: int = 0
    width: int | None = None
    height: int | None = None
    mime: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    language: str = ""
    nsfw: bool = False
    humor: bool = False
    epilepsy: bool = False
    lock: bool = False
    author_name: str = ""
