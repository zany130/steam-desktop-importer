"""SteamGridDB listing filters (API v2 query params).

SteamGridDB defaults to ``types=static``, ``nsfw=false``, ``humor=false``,
and ``epilepsy=false``. Omitting the params hid joke, NSFW, and animated
artwork. These flags match Steam ROM Manager's allow-checkboxes: on means
include that category (``any``), off means exclude it (``false``).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DEFAULT_ARTWORK_FILTERS",
    "GRID_STYLES",
    "ArtworkFilters",
]

_TRI = frozenset({"false", "true", "any"})
_TYPES = ("static", "animated")
GRID_STYLES = ("alternate", "blurred", "white_logo", "material", "no_logo")


def _tri(value: str, default: str = "false") -> str:
    token = (value or default).strip().lower()
    return token if token in _TRI else default


def _types(values: tuple[str, ...]) -> tuple[str, ...]:
    chosen = tuple(item for item in values if item in _TYPES)
    return chosen or ("static",)


@dataclass(frozen=True)
class ArtworkFilters:
    """Query params for ``/grids|heroes|logos|icons/game/{id}``."""

    nsfw: str = "false"
    humor: str = "false"
    epilepsy: str = "false"
    types: tuple[str, ...] = ("static",)
    styles: tuple[str, ...] = ()
    mimes: tuple[str, ...] = ()

    @classmethod
    def from_allows(
        cls,
        *,
        nsfw: bool = False,
        humor: bool = False,
        epilepsy: bool = False,
        static: bool = True,
        animated: bool = False,
        styles: tuple[str, ...] = (),
        mimes: tuple[str, ...] = (),
    ) -> ArtworkFilters:
        kinds: list[str] = []
        if static:
            kinds.append("static")
        if animated:
            kinds.append("animated")
        return cls(
            nsfw="any" if nsfw else "false",
            humor="any" if humor else "false",
            epilepsy="any" if epilepsy else "false",
            types=tuple(kinds) or ("static",),
            styles=tuple(item for item in styles if item in GRID_STYLES),
            mimes=tuple(item for item in mimes if item),
        )

    @property
    def allow_nsfw(self) -> bool:
        return self.nsfw in {"true", "any"}

    @property
    def allow_humor(self) -> bool:
        return self.humor in {"true", "any"}

    @property
    def allow_epilepsy(self) -> bool:
        return self.epilepsy in {"true", "any"}

    @property
    def include_static(self) -> bool:
        return "static" in self.types

    @property
    def include_animated(self) -> bool:
        return "animated" in self.types

    @property
    def grid_style(self) -> str:
        return self.styles[0] if len(self.styles) == 1 else ""

    def as_query(self, *, include_styles: bool = True) -> dict[str, str]:
        params = {
            "nsfw": _tri(self.nsfw),
            "humor": _tri(self.humor),
            "epilepsy": _tri(self.epilepsy),
            "types": ",".join(_types(self.types)),
        }
        if include_styles and self.styles:
            params["styles"] = ",".join(item for item in self.styles if item in GRID_STYLES)
        if self.mimes:
            params["mimes"] = ",".join(self.mimes)
        return params


DEFAULT_ARTWORK_FILTERS = ArtworkFilters()
