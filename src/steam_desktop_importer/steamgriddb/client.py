"""SteamGridDB HTTP client (IMPLEMENTATION.md §21, §23).

Auth is a Bearer token. GET JSON is cached in-process for the life of the
client. Transient failures and HTTP 429 are retried with ``Retry-After``
when SteamGridDB sends it. There is no hardcoded 0.35s "official" delay.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests

from .auth import resolve_api_key
from .errors import (
    AuthenticationError,
    InvalidResponseError,
    MissingAPIKeyError,
    NotFoundError,
    RateLimitError,
    SteamGridDBError,
    SteamGridDBTimeoutError,
)
from .models import ArtworkKind, GameResult, GridAsset

__all__ = [
    "DEFAULT_BASE_URL",
    "SteamGridDBClient",
    "is_http_url",
]

DEFAULT_BASE_URL = "https://www.steamgriddb.com/api/v2"
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 30.0
_MAX_RETRIES = 3
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
# Fallback only when Retry-After is missing. Not an official SteamGridDB limit.
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if when.tzinfo is None:
            return None
        return max(0.0, when.timestamp() - time.time())


def _redact(message: str, api_key: str) -> str:
    if api_key and api_key in message:
        return message.replace(api_key, "<redacted>")
    return message


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, (bool, int)) else False


def _parse_game(raw: Any) -> GameResult | None:
    if not isinstance(raw, dict):
        return None
    game_id = _as_int(raw.get("id"))
    name = _as_str(raw.get("name")).strip()
    if game_id is None or not name:
        return None
    types = raw.get("types") or []
    type_names = tuple(item for item in types if isinstance(item, str))
    return GameResult(
        id=game_id,
        name=name,
        types=type_names,
        verified=bool(raw.get("verified")),
    )


def _parse_asset(raw: Any, kind: str) -> GridAsset | None:
    if not isinstance(raw, dict):
        return None
    asset_id = _as_int(raw.get("id"))
    url = _as_str(raw.get("url")).strip()
    if asset_id is None or not url or not is_http_url(url):
        return None
    thumb = _as_str(raw.get("thumb")).strip()
    if thumb and not is_http_url(thumb):
        thumb = ""
    tags = raw.get("tags") or []
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    return GridAsset(
        id=asset_id,
        kind=kind,
        url=url,
        thumb=thumb,
        style=_as_str(raw.get("style")),
        score=_as_int(raw.get("score")) or 0,
        width=_as_int(raw.get("width")),
        height=_as_int(raw.get("height")),
        mime=_as_str(raw.get("mime")),
        tags=tuple(item for item in tags if isinstance(item, str)),
        notes=_as_str(raw.get("notes")),
        language=_as_str(raw.get("language")),
        nsfw=_as_bool(raw.get("nsfw")),
        humor=_as_bool(raw.get("humor")),
        epilepsy=_as_bool(raw.get("epilepsy")),
        lock=_as_bool(raw.get("lock")),
        author_name=_as_str(author.get("name")),
    )


class SteamGridDBClient:
    """Synchronous SteamGridDB v2 client."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        session: requests.Session | None = None,
        base_url: str = DEFAULT_BASE_URL,
        connect_timeout: float = _CONNECT_TIMEOUT,
        read_timeout: float = _READ_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        sleeper: Callable[[float], None] = time.sleep,
        cache: bool = True,
        environ: dict[str, str] | None = None,
    ) -> None:
        if api_key is None:
            resolved = resolve_api_key(environ=environ)
        else:
            resolved = api_key.strip() or None
        if not resolved:
            raise MissingAPIKeyError(
                "no SteamGridDB API key; set SGDB_API_KEY or pass api_key="
            )
        self._api_key = resolved
        self.base_url = base_url.rstrip("/") + "/"
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max_retries
        self._sleep = sleeper
        self._cache_enabled = cache
        self._cache: dict[tuple[str, str, tuple[tuple[str, str], ...]], Any] = {}
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "User-Agent": "steam-desktop-importer/0.0.1",
            }
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> SteamGridDBClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search_games(self, query: str) -> list[GameResult]:
        """Search by name. Empty ``data`` is a successful no-result."""
        term = query.strip()
        if not term:
            return []
        payload = self._get_json(f"search/autocomplete/{quote(term, safe='')}")
        return self._games_from_data(payload)

    def get_grids(
        self,
        game_id: int,
        dimensions: Sequence[str] | None = None,
        *,
        styles: Sequence[str] | None = None,
    ) -> list[GridAsset]:
        params = self._list_params(dimensions=dimensions, styles=styles)
        payload = self._get_json(f"grids/game/{int(game_id)}", params)
        return self._assets_from_data(payload, ArtworkKind.GRID)

    def get_heroes(self, game_id: int) -> list[GridAsset]:
        payload = self._get_json(f"heroes/game/{int(game_id)}")
        return self._assets_from_data(payload, ArtworkKind.HERO)

    def get_logos(self, game_id: int) -> list[GridAsset]:
        payload = self._get_json(f"logos/game/{int(game_id)}")
        return self._assets_from_data(payload, ArtworkKind.LOGO)

    def get_icons(self, game_id: int) -> list[GridAsset]:
        payload = self._get_json(f"icons/game/{int(game_id)}")
        return self._assets_from_data(payload, ArtworkKind.ICON)

    def download_asset(self, asset: GridAsset, temp_path, *, max_bytes: int | None = None):
        """Download ``asset.url`` to ``temp_path``. See :mod:`.download`."""
        from .download import download_url

        return download_url(
            asset.url,
            temp_path,
            session=self.session,
            timeout=(self.connect_timeout, self.read_timeout),
            max_bytes=max_bytes,
        )

    def _list_params(
        self,
        *,
        dimensions: Sequence[str] | None = None,
        styles: Sequence[str] | None = None,
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        if styles:
            params["styles"] = ",".join(styles)
        return params

    def _games_from_data(self, payload: dict[str, Any]) -> list[GameResult]:
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise InvalidResponseError("SteamGridDB search data is not a list")
        games: list[GameResult] = []
        for item in data:
            parsed = _parse_game(item)
            if parsed is not None:
                games.append(parsed)
        return games

    def _assets_from_data(self, payload: dict[str, Any], kind: str) -> list[GridAsset]:
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise InvalidResponseError("SteamGridDB artwork data is not a list")
        assets: list[GridAsset] = []
        for item in data:
            parsed = _parse_asset(item, kind)
            if parsed is not None:
                assets.append(parsed)
        return assets

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = urljoin(self.base_url, path)
        cache_key = (
            "GET",
            url,
            tuple(sorted((params or {}).items())),
        )
        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]
        payload = self._request_json("GET", url, params=params)
        if self._cache_enabled:
            self._cache[cache_key] = payload
        return payload

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self._request(method, url, params=params)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise InvalidResponseError(
                _redact(
                    f"SteamGridDB returned non-JSON (HTTP {response.status_code})",
                    self._api_key,
                ),
                status_code=response.status_code,
            ) from error
        if not isinstance(payload, dict):
            raise InvalidResponseError(
                "SteamGridDB JSON root is not an object",
                status_code=response.status_code,
            )
        if "success" in payload and payload["success"] is False:
            errors = payload.get("errors")
            detail = "; ".join(str(item) for item in errors) if isinstance(errors, list) else "request failed"
            raise InvalidResponseError(
                _redact(detail, self._api_key),
                status_code=response.status_code,
            )
        return payload

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        stream: bool = False,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        last_error: SteamGridDBError | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    timeout=(self.connect_timeout, self.read_timeout),
                    stream=stream,
                    headers=headers,
                )
            except requests.Timeout as error:
                last_error = SteamGridDBTimeoutError(
                    _redact("SteamGridDB request timed out", self._api_key)
                )
                if attempt + 1 >= attempts:
                    raise last_error from error
                self._sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue
            except requests.RequestException as error:
                last_error = SteamGridDBError(
                    _redact(f"SteamGridDB request failed: {error}", self._api_key)
                )
                if attempt + 1 >= attempts:
                    raise last_error from error
                self._sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue

            status = response.status_code
            if status == 401:
                raise AuthenticationError(
                    "SteamGridDB rejected the API key",
                    status_code=401,
                )
            if status == 404:
                raise NotFoundError(
                    _redact(self._error_detail(response) or "SteamGridDB resource not found", self._api_key),
                    status_code=404,
                )
            if status in _TRANSIENT_STATUSES:
                last_error = (
                    RateLimitError("SteamGridDB rate-limited the request", status_code=429)
                    if status == 429
                    else SteamGridDBError(
                        f"SteamGridDB returned HTTP {status}",
                        status_code=status,
                    )
                )
                if attempt + 1 >= attempts:
                    raise last_error
                delay = _retry_after_seconds(response.headers)
                if delay is None:
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                self._sleep(delay)
                continue
            if status >= 400:
                raise SteamGridDBError(
                    _redact(
                        self._error_detail(response) or f"SteamGridDB returned HTTP {status}",
                        self._api_key,
                    ),
                    status_code=status,
                )
            return response
        assert last_error is not None
        raise last_error

    def _error_detail(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        errors = payload.get("errors")
        if isinstance(errors, list):
            return "; ".join(str(item) for item in errors)
        return ""
