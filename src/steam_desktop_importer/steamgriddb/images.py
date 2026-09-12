"""Image sniffing for SteamGridDB downloads (IMPLEMENTATION.md §22).

Magic-byte detection is enough to reject obvious non-images. WebP payload
with a ``.png``/``.jpg`` filename is *not* treated as invalid: SteamGridDB
documents that compatibility rename (rule 22).
"""

from __future__ import annotations

__all__ = [
    "COMPAT_EXTENSIONS",
    "sniff_image",
    "steam_filename_extension",
    "unrecognised_image_reason",
]

COMPAT_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def sniff_image(payload: bytes) -> str | None:
    """Return ``png``/``jpeg``/``gif``/``webp``, or ``None`` if not an image."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        return "gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


def unrecognised_image_reason(payload: bytes) -> str:
    """Short description of bytes that failed :func:`sniff_image`."""
    if not payload:
        return "empty file"
    if payload.startswith(b"\x1aE\xdf\xa3"):
        return "WebM video, not a still image"
    stripped = payload.lstrip()
    if stripped.startswith(b"<!DOCTYPE") or stripped.startswith(b"<html") or stripped.startswith(b"<HTML"):
        return "HTML"
    if stripped[:1] in (b"{", b"["):
        return "JSON"
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        brand = payload[8:12].decode("latin1", "replace")
        if brand.startswith("avif") or brand.startswith("avis"):
            return "AVIF"
        return f"ISO-BMFF ({brand})"
    return "unrecognised format"


def steam_filename_extension(kind: str) -> str:
    """Extension Steam's grid folder should see.

    WebP is saved as ``.png`` while keeping the WebP payload (rule 22).
    JPEG uses ``.jpg``. Other recognised kinds keep their sniff name.
    """
    if kind == "webp":
        return "png"
    if kind == "jpeg":
        return "jpg"
    return kind
