#!/usr/bin/env python3
"""Phase 0 format-characterization tool.

Reads a real binary ``shortcuts.vdf`` and reports its *structure* only: key
names, key casing, key ordering, and value types. It never prints shortcut
names, executables, launch options or any other user content, and it never
opens the file for writing.

Usage:
    python scripts/characterize_shortcuts.py <path/to/shortcuts.vdf>
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import vdf

# Keys whose *values* are safe to print because they are numeric/boolean flags
# rather than user content.
SAFE_VALUE_KEYS = {
    "IsHidden",
    "AllowDesktopConfig",
    "AllowOverlay",
    "openvr",
    "Devkit",
    "LastPlayTime",
}


def describe(value: object) -> str:
    if isinstance(value, dict):
        return f"dict(len={len(value)})"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return f"int({value if -2 else ''})".replace("()", "")
    if isinstance(value, bytes):
        return f"bytes(len={len(value)})"
    if isinstance(value, str):
        return f"str(len={len(value)}, empty={value == ''})"
    return type(value).__name__


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    path = Path(argv[1]).expanduser()
    if not path.is_file():
        print(f"not a file: {path}")
        return 1

    # Read-only. Deliberately no 'r+'/'w' mode anywhere in this tool.
    with path.open("rb") as handle:
        data = vdf.binary_load(handle)

    print(f"file: {path}")
    print(f"size: {path.stat().st_size} bytes")
    print(f"top-level keys: {list(data.keys())}")

    shortcuts = data.get("shortcuts", {})
    print(f"shortcut count: {len(shortcuts)}")
    print(f"index keys: {list(shortcuts.keys())[:8]}{' ...' if len(shortcuts) > 8 else ''}")
    print(
        "index keys are contiguous 0..n-1: "
        f"{list(shortcuts.keys()) == [str(i) for i in range(len(shortcuts))]}"
    )

    key_counts: Counter[str] = Counter()
    key_order: list[tuple[str, ...]] = []
    type_by_key: dict[str, Counter[str]] = {}
    appid_samples: list[int] = []

    for entry in shortcuts.values():
        key_order.append(tuple(entry.keys()))
        for key, value in entry.items():
            key_counts[key] += 1
            type_by_key.setdefault(key, Counter())[describe_type(value)] += 1
        raw_appid = entry.get("appid")
        if isinstance(raw_appid, int):
            appid_samples.append(raw_appid)

    print("\n-- key presence (name, casing, count, value type) --")
    for key, count in key_counts.most_common():
        types = ", ".join(f"{t}x{n}" for t, n in type_by_key[key].most_common())
        print(f"  {key!r:28} present={count}/{len(shortcuts)}  types={types}")

    print("\n-- distinct key orderings --")
    for order, count in Counter(key_order).most_common():
        print(f"  x{count}: {list(order)}")

    print("\n-- appid representation (values are not user content) --")
    for value in appid_samples:
        unsigned = value & 0xFFFFFFFF
        print(
            f"  signed={value:>12}  unsigned={unsigned:>10}  "
            f"hex=0x{unsigned:08X}  high_bit={'yes' if unsigned & 0x80000000 else 'no'}"
        )

    print("\n-- safe flag values --")
    for key in sorted(SAFE_VALUE_KEYS & key_counts.keys()):
        values = Counter(
            entry[key] for entry in shortcuts.values() if key in entry and key != "LastPlayTime"
        )
        if values:
            print(f"  {key}: {dict(values)}")

    return 0


def describe_type(value: object) -> str:
    if isinstance(value, dict):
        return f"dict(len={len(value)})"
    if isinstance(value, int):
        return "int"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, str):
        return "str(empty)" if value == "" else "str"
    return type(value).__name__


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
