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
from .images import sniff_image, unrecognised_image_reason

__all__ = ["DEFAULT_MAX_BYTES", "DOWNLOAD_READ_TIMEOUT", "download_url"]

# Animated SteamGridDB heroes are often 20–50 MB APNGs. 20 MB aborted
# those; 100 MB still stops a runaway CDN stream.
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DOWNLOAD_READ_TIMEOUT = 120.0
_CHUNK = 64 * 1024


def _format_size(n: int) -> str:
    mib = n / (1024 * 1024)
    if mib >= 10:
        return f"{mib:.0f} MB"
    if mib >= 0.1:
        return f"{mib:.1f} MB"
    return f"{n} bytes"


def download_url(
    url: str,
    temp_path: Path,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, DOWNLOAD_READ_TIMEOUT),
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
        declared = _content_length(response.headers)
        if declared is not None and declared > limit:
            response.close()
            raise InvalidResponseError(
                f"artwork is {_format_size(declared)} (limit {_format_size(limit)})"
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
                        f"artwork exceeded {_format_size(limit)}; download aborted"
                    )
                os.write(fd, chunk)
        finally:
            os.close(fd)
            response.close()
        payload = part.read_bytes()
        if sniff_image(payload) is None:
            reason = unrecognised_image_reason(payload)
            raise InvalidResponseError(
                f"downloaded artwork is not a recognised image ({reason})"
            )
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


def _content_length(headers) -> int | None:
    raw = headers.get("Content-Length") or headers.get("content-length")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None
