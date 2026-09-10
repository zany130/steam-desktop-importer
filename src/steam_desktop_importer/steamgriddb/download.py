"""Download SteamGridDB assets to a temporary path (IMPLEMENTATION.md §22).

Never writes a Steam ``grid/`` destination. Callers pass a temp path;
:mod:`steam_desktop_importer.steam.artwork` places the validated file.
The API key is not sent to CDN hosts.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from .client import is_http_url
from .errors import InvalidResponseError, SteamGridDBError, SteamGridDBTimeoutError
from .images import sniff_image

__all__ = ["DEFAULT_MAX_BYTES", "download_url"]

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_CHUNK = 64 * 1024


def download_url(
    url: str,
    temp_path: Path,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 30.0),
    max_bytes: int | None = None,
) -> Path:
    """Stream ``url`` into ``temp_path`` after validating it is an image.

    The file is written to a sibling ``.part`` file first, then replaced onto
    ``temp_path`` only if sniffing succeeds.
    """
    if not is_http_url(url):
        raise InvalidResponseError("refusing to download non-HTTP artwork URL")
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    destination = Path(temp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    owned_session = session is None
    http = session or requests.Session()
    request = requests.Request(
        "GET",
        url,
        headers={"Accept": "image/*,application/octet-stream"},
    )
    prepared = http.prepare_request(request)
    prepared.headers.pop("Authorization", None)
    try:
        try:
            response = http.send(prepared, stream=True, timeout=timeout)
        except requests.Timeout as error:
            raise SteamGridDBTimeoutError("artwork download timed out") from error
        except requests.RequestException as error:
            raise SteamGridDBError(f"artwork download failed: {error}") from error
        if response.status_code >= 400:
            raise SteamGridDBError(
                f"artwork download returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        written = 0
        fd = os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise InvalidResponseError(
                        f"artwork exceeded {limit} bytes; download aborted"
                    )
                os.write(fd, chunk)
        finally:
            os.close(fd)
            response.close()
        payload = part.read_bytes()
        if sniff_image(payload) is None:
            raise InvalidResponseError("downloaded artwork is not a recognised image")
        os.replace(str(part), str(destination))
        return destination
    except Exception:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        try:
            os.unlink(destination)
        except FileNotFoundError:
            pass
        raise
    finally:
        if owned_session:
            http.close()
