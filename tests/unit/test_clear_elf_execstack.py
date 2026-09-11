"""stdlib ELF GNU_STACK clearer used by the AppImage build."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "clear_elf_execstack.py"
_SPEC = importlib.util.spec_from_file_location("clear_elf_execstack", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
PT_GNU_STACK = _MOD.PT_GNU_STACK
clear_execstack = _MOD.clear_execstack


def _elf64_with_gnu_stack(*, executable: bool) -> bytes:
    ehdr_size = 64
    phdr_size = 56
    header = bytearray(ehdr_size + phdr_size)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELF64
    header[5] = 1  # little-endian
    header[6] = 1  # version
    struct.pack_into("<H", header, 16, 3)  # ET_DYN
    struct.pack_into("<H", header, 18, 62)  # EM_X86_64
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<Q", header, 32, ehdr_size)  # e_phoff
    struct.pack_into("<H", header, 52, ehdr_size)
    struct.pack_into("<H", header, 54, phdr_size)
    struct.pack_into("<H", header, 56, 1)
    flags = 0x6 | (0x1 if executable else 0)
    struct.pack_into("<I", header, ehdr_size, PT_GNU_STACK)
    struct.pack_into("<I", header, ehdr_size + 4, flags)
    return bytes(header)


def test_clear_execstack_drops_executable_bit(tmp_path):
    path = tmp_path / "libpython.so.1.0"
    path.write_bytes(_elf64_with_gnu_stack(executable=True))
    assert clear_execstack(path) is True
    assert clear_execstack(path) is False
    flags = struct.unpack_from("<I", path.read_bytes(), 64 + 4)[0]
    assert flags & 1 == 0
    assert flags & 6 == 6


def test_clear_execstack_ignores_non_elf(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not an elf")
    assert clear_execstack(path) is False
