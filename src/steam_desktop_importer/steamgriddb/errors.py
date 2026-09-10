"""SteamGridDB client errors (IMPLEMENTATION.md §21–§22)."""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "InvalidResponseError",
    "MissingAPIKeyError",
    "NotFoundError",
    "RateLimitError",
    "SteamGridDBError",
    "SteamGridDBTimeoutError",
]


class SteamGridDBError(Exception):
    """A SteamGridDB request could not be completed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MissingAPIKeyError(SteamGridDBError):
    """No API key was supplied and ``SGDB_API_KEY`` is unset."""


class AuthenticationError(SteamGridDBError):
    """SteamGridDB rejected the API key (HTTP 401)."""


class NotFoundError(SteamGridDBError):
    """The game or asset does not exist (HTTP 404)."""


class RateLimitError(SteamGridDBError):
    """HTTP 429 after bounded retries were exhausted."""


class SteamGridDBTimeoutError(SteamGridDBError):
    """Connect or read timeout."""


class InvalidResponseError(SteamGridDBError):
    """JSON, success flag, URL, or image payload failed validation."""
