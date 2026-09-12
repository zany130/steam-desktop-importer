"""SteamGridDB client (IMPLEMENTATION.md §21–§23, Phase 8).

Search, list artwork, and download to a caller-supplied temp path. Steam
``grid/`` placement lives in :mod:`steam_desktop_importer.steam.artwork`.
"""

from __future__ import annotations

from .auth import (
    ENV_KEY,
    clear_stored_api_key,
    default_key_path,
    resolve_api_key,
    save_api_key,
    xdg_config_home,
)
from .client import (
    DEFAULT_BASE_URL,
    SteamGridDBClient,
    asset_download_url,
    is_http_url,
    preview_download_url,
)
from .filters import DEFAULT_ARTWORK_FILTERS, GRID_STYLES, ArtworkFilters
from .download import DEFAULT_MAX_BYTES, DOWNLOAD_READ_TIMEOUT, download_url
from .errors import (
    AuthenticationError,
    InvalidResponseError,
    MissingAPIKeyError,
    NotFoundError,
    RateLimitError,
    SteamGridDBError,
    SteamGridDBTimeoutError,
)
from .images import sniff_image, steam_filename_extension, unrecognised_image_reason
from .models import ArtworkKind, GameResult, GridAsset
from .queries import search_queries

__all__ = [
    "DEFAULT_ARTWORK_FILTERS",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_BYTES",
    "DOWNLOAD_READ_TIMEOUT",
    "ENV_KEY",
    "GRID_STYLES",
    "ArtworkFilters",
    "ArtworkKind",
    "AuthenticationError",
    "GameResult",
    "GridAsset",
    "InvalidResponseError",
    "MissingAPIKeyError",
    "NotFoundError",
    "RateLimitError",
    "SteamGridDBClient",
    "SteamGridDBError",
    "SteamGridDBTimeoutError",
    "asset_download_url",
    "clear_stored_api_key",
    "preview_download_url",
    "default_key_path",
    "download_url",
    "is_http_url",
    "resolve_api_key",
    "save_api_key",
    "search_queries",
    "sniff_image",
    "steam_filename_extension",
    "unrecognised_image_reason",
    "xdg_config_home",
]
