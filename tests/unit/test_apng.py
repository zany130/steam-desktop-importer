"""APNG frame reconstruction for artwork preview."""

from __future__ import annotations

import struct
import zlib

from steam_desktop_importer.steamgriddb.apng import PNG_SIG, parse_apng
from steam_desktop_importer.steamgriddb.images import sniff_image

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _chunk(name: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    )


def _rgb_idat(rgb: bytes) -> bytes:
    return zlib.compress(b"\x00" + rgb, 9)


def two_frame_apng() -> bytes:
    """1×1 RGB APNG: red then blue, 100ms each."""
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    actl = _chunk(b"acTL", struct.pack(">II", 2, 0))
    fctl0 = _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 10, 100, 0, 0))
    idat0 = _chunk(b"IDAT", _rgb_idat(b"\xff\x00\x00"))
    fctl1 = _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 1, 1, 1, 0, 0, 10, 100, 0, 0))
    fdat1 = _chunk(b"fdAT", struct.pack(">I", 2) + _rgb_idat(b"\x00\x00\xff"))
    iend = _chunk(b"IEND", b"")
    return PNG_SIG + ihdr + actl + fctl0 + idat0 + fctl1 + fdat1 + iend


def test_parse_apng_ignores_static_png():
    assert sniff_image(PNG_1X1) == "png"
    assert parse_apng(PNG_1X1) is None
    assert parse_apng(b"not an image") is None


def test_parse_apng_reads_two_frames():
    payload = two_frame_apng()
    animation = parse_apng(payload)
    assert animation is not None
    assert animation.width == 1
    assert animation.height == 1
    assert len(animation.frames) == 2
    assert animation.frames[0].delay_ms == 100
    assert sniff_image(animation.frames[0].png_bytes) == "png"
    assert sniff_image(animation.frames[1].png_bytes) == "png"
    assert animation.frames[0].png_bytes != animation.frames[1].png_bytes
