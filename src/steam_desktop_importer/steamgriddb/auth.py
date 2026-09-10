"""SteamGridDB API key resolution (IMPLEMENTATION.md §21).

Precedence: explicit argument, then ``SGDB_API_KEY``, then a user config
file with mode ``0600``. The key is never logged.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import MissingAPIKeyError

__all__ = [
    "ENV_KEY",
    "clear_stored_api_key",
    "default_key_path",
    "resolve_api_key",
    "save_api_key",
    "xdg_config_home",
]

ENV_KEY = "SGDB_API_KEY"


def xdg_config_home(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """``$XDG_CONFIG_HOME``, defaulting to ``~/.config``.

    A relative value is invalid and falls back to the default, matching the
    XDG data-home rule already used for discovery.
    """
    env = os.environ if environ is None else environ
    base = home if home is not None else Path.home()
    value = env.get("XDG_CONFIG_HOME")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return base / ".config"


def default_key_path(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    return xdg_config_home(environ, home) / "steam-desktop-importer" / "sgdb_api_key"


def resolve_api_key(
    explicit: str | None = None,
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    required: bool = False,
) -> str | None:
    """Return the resolved key, or ``None`` if nothing is configured."""
    if explicit is not None:
        stripped = explicit.strip()
        if stripped:
            return stripped
    env = os.environ if environ is None else environ
    from_env = (env.get(ENV_KEY) or "").strip()
    if from_env:
        return from_env
    path = default_key_path(environ, home)
    try:
        stored = path.read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if stored:
        return stored
    if required:
        raise MissingAPIKeyError(
            f"no SteamGridDB API key; set {ENV_KEY} or save one in Settings"
        )
    return None


def save_api_key(
    key: str,
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Write ``key`` to the config file with mode ``0600``."""
    stripped = key.strip()
    if not stripped:
        raise MissingAPIKeyError("refusing to store an empty SteamGridDB API key")
    path = default_key_path(environ, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stripped + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def clear_stored_api_key(
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> None:
    """Remove the config-file key. Does not unset ``SGDB_API_KEY``."""
    path = default_key_path(environ, home)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
