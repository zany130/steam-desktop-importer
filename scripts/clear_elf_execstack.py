#!/usr/bin/env python3
"""Clear PT_GNU_STACK executable bits on ELF files.

uv's python-build-standalone currently ships libpython with an RWE GNU_STACK
segment. glibc 2.41+ refuses to dlopen those libraries, which breaks the
PyInstaller AppImage on Fedora. CPython does not need an executable stack.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

PT_GNU_STACK = 0x6474E551
PF_X = 1
ELF_MAGIC = b"\x7fELF"


def clear_execstack(path: Path) -> bool:
    """Return True if the file was modified."""
    data = bytearray(path.read_bytes())
    if len(data) < 64 or data[:4] != ELF_MAGIC or data[5] != 1:
        return False
    ei_class = data[4]
    if ei_class == 2:
        e_phoff = struct.unpack_from("<Q", data, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
        flags_off = 4
    elif ei_class == 1:
        e_phoff = struct.unpack_from("<I", data, 28)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
        flags_off = 24
    else:
        return False
    changed = False
    for index in range(e_phnum):
        header = e_phoff + index * e_phentsize
        if header + max(8, flags_off + 4) > len(data):
            return False
        p_type = struct.unpack_from("<I", data, header)[0]
        if p_type != PT_GNU_STACK:
            continue
        p_flags = struct.unpack_from("<I", data, header + flags_off)[0]
        if p_flags & PF_X:
            struct.pack_into("<I", data, header + flags_off, p_flags & ~PF_X)
            changed = True
    if changed:
        path.write_bytes(data)
    return changed


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args(argv)
    changed = 0
    for root in args.roots:
        if not root.exists():
            print(f"error: {root} does not exist", file=sys.stderr)
            return 1
        for path in iter_files(root):
            try:
                if clear_execstack(path):
                    print(f"cleared execstack {path}")
                    changed += 1
            except OSError as error:
                print(f"error: {path}: {error}", file=sys.stderr)
                return 1
    print(f"cleared {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
