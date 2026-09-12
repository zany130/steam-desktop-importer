"""APNG frame extraction for artwork preview.

Qt's PNG plugin ignores ``acTL``/``fcTL``/``fdAT``, so ``QMovie`` and
``QImageReader`` only show the first frame. Steam still plays the payload
when it is stored with a ``.png`` name (IMPLEMENTATION.md §22.1). The
picker uses this module to reconstruct each frame as a normal PNG.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .images import sniff_image

__all__ = ["ApngAnimation", "ApngFrame", "parse_apng"]

PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PREFIX_CHUNKS = frozenset(
    {b"PLTE", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"bKGD"}
)


@dataclass(frozen=True)
class ApngFrame:
    """One APNG frame as a standalone PNG of the frame rectangle."""

    png_bytes: bytes
    x: int
    y: int
    width: int
    height: int
    delay_ms: int
    dispose_op: int
    blend_op: int


@dataclass(frozen=True)
class ApngAnimation:
    width: int
    height: int
    plays: int
    frames: tuple[ApngFrame, ...]


def parse_apng(payload: bytes) -> ApngAnimation | None:
    """Return animation data, or ``None`` if this is not a multi-frame APNG."""
    if sniff_image(payload) != "png":
        return None
    ihdr: bytes | None = None
    plays = 0
    prefix: list[tuple[bytes, bytes]] = []
    frames: list[ApngFrame] = []
    current_fctl: bytes | None = None
    current_idats: list[bytes] = []
    saw_actl = False

    def flush() -> None:
        nonlocal current_fctl, current_idats
        if ihdr is None or current_fctl is None or not current_idats:
            current_fctl = None
            current_idats = []
            return
        parsed = _parse_fctl(current_fctl)
        if parsed is None:
            current_fctl = None
            current_idats = []
            return
        x, y, width, height, delay_ms, dispose_op, blend_op = parsed
        png_bytes = _frame_png(ihdr, prefix, width, height, b"".join(current_idats))
        frames.append(
            ApngFrame(
                png_bytes=png_bytes,
                x=x,
                y=y,
                width=width,
                height=height,
                delay_ms=delay_ms,
                dispose_op=dispose_op,
                blend_op=blend_op,
            )
        )
        current_fctl = None
        current_idats = []

    for name, data in _iter_chunks(payload):
        if name == b"IHDR":
            ihdr = data
        elif name == b"acTL":
            saw_actl = True
            if len(data) >= 8:
                plays = struct.unpack(">I", data[4:8])[0]
        elif name == b"fcTL":
            flush()
            current_fctl = data
        elif name == b"IDAT":
            if current_fctl is not None:
                current_idats.append(data)
        elif name == b"fdAT":
            if current_fctl is not None and len(data) >= 4:
                current_idats.append(data[4:])
        elif name == b"IEND":
            flush()
        elif name in _PREFIX_CHUNKS and not frames:
            prefix.append((name, data))

    if not saw_actl or ihdr is None or len(ihdr) < 8 or len(frames) < 2:
        return None
    width, height = struct.unpack(">II", ihdr[:8])
    if width <= 0 or height <= 0:
        return None
    return ApngAnimation(width=width, height=height, plays=plays, frames=tuple(frames))


def _iter_chunks(payload: bytes):
    index = 8
    end = len(payload)
    while index + 12 <= end:
        length = struct.unpack(">I", payload[index : index + 4])[0]
        name = payload[index + 4 : index + 8]
        data_start = index + 8
        data_end = data_start + length
        if data_end + 4 > end:
            break
        yield name, payload[data_start:data_end]
        index = data_end + 4
        if name == b"IEND":
            break


def _parse_fctl(
    data: bytes,
) -> tuple[int, int, int, int, int, int, int] | None:
    if len(data) < 26:
        return None
    _seq, width, height, x, y, delay_num, delay_den, dispose_op, blend_op = struct.unpack(
        ">IIIIIHHBB", data[:26]
    )
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height, _delay_ms(delay_num, delay_den), dispose_op, blend_op


def _delay_ms(num: int, den: int) -> int:
    if den == 0:
        den = 100
    if num <= 0:
        return 100
    return max(10, int(round(num * 1000 / den)))


def _frame_png(
    ihdr: bytes,
    prefix: list[tuple[bytes, bytes]],
    width: int,
    height: int,
    idat: bytes,
) -> bytes:
    header = struct.pack(">II", width, height) + ihdr[8:]
    parts = [PNG_SIG, _chunk(b"IHDR", header)]
    parts.extend(_chunk(name, data) for name, data in prefix)
    parts.append(_chunk(b"IDAT", idat))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def _chunk(name: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    )
