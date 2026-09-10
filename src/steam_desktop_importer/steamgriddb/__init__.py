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
from .client import DEFAULT_BASE_URL, SteamGridDBClient, is_http_url
from .download import DEFAULT_MAX_BYTES, download_url
from .errors import (
    AuthenticationError,
    InvalidResponseError,
    MissingAPIKeyError,
    NotFoundError,
    RateLimitError,
    SteamGridDBError,
    SteamGridDBTimeoutError,
)
from .images import sniff_image, steam_filename_extension
from .models import ArtworkKind, GameResult, GridAsset
from .queries import search_queries

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_BYTES",
    "ENV_KEY",
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
    "clear_stored_api_key",
    "default_key_path",
    "download_url",
    "is_http_url",
    "resolve_api_key",
    "save_api_key",
    "search_queries",
    "sniff_image",
    "steam_filename_extension",
    "xdg_config_home",
]
