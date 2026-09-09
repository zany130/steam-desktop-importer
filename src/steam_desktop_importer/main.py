"""Command-line entry point.

IMPLEMENTATION.md §33 asks for project-native debug tooling "rather than
ad-hoc scripts that guess the first userdata directory".

Only the commands that Phase 1 can actually support are implemented:
``debug roots``, ``debug scan`` and ``debug desktop-entry``. The Steam-facing
commands from §33 (``debug dump-shortcuts``, ``debug identity``) need Phase 4,
5 and 6 and are deliberately absent rather than stubbed, so that no command
can appear to work while returning guessed data.

There is no GUI yet. §31 puts the GUI at Phase 3.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .desktop.discovery import (
    desktop_id_for,
    discover_applications,
    ordered_application_roots,
)
from .desktop.parser import DesktopEntryError, build_application, parse_desktop_entry
from .models import DesktopApplication


def _print_roots(args: argparse.Namespace) -> int:
    roots = ordered_application_roots(include_supplemental=not args.no_supplemental)
    if not roots:
        print("no applications/ directories found")
        return 1
    width = max(len(root.origin) for root in roots)
    for position, root in enumerate(roots, start=1):
        print(f"{position:>3}. [{root.origin:<{width}}] {root.path}")
    return 0


def _status(app: DesktopApplication) -> str:
    if app.supported_for_import:
        return "importable"
    return "unavailable" if not app.is_available else "unsupported"


def _print_scan(args: argparse.Namespace) -> int:
    result = discover_applications(include_supplemental=not args.no_supplemental)

    applications = list(result.applications.values())
    if not args.all:
        applications = [app for app in applications if not app.no_display]
    if args.importable_only:
        applications = [app for app in applications if app.supported_for_import]

    if args.nonstandard_only:
        applications = [app for app in applications if app.nonstandard_exec]

    for app in applications:
        print(f"{app.desktop_id}")
        print(f"    name        {app.localized_name or app.name}")
        print(f"    source      {app.source_kind}")
        print(f"    status      {_status(app)}")
        if app.unsupported_reason:
            print(f"    reason      {app.unsupported_reason}")
        if app.nonstandard_exec:
            print(f"    exec mode   {app.exec_parse_mode} (nonstandard)")
        print(f"    argv        {app.exec_argv}")

    nonstandard = sum(app.nonstandard_exec for app in result.applications.values())

    print()
    print(f"roots scanned      {len(result.roots)}")
    print(f"resolved           {len(result.applications)}")
    print(f"importable         {len(result.importable)}")
    print(f"nonstandard Exec   {nonstandard}")
    print(f"masked by Hidden   {len(result.masked_ids)}")
    print(f"shadowed copies    {len(result.shadowed)}")
    print(f"parse errors       {len(result.errors)}")
    for path, message in result.errors:
        print(f"    {path}: {message}")
    return 0


def _print_desktop_entry(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    try:
        parsed = parse_desktop_entry(path)
    except DesktopEntryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    # Match against both the literal and the fully resolved path. On systems
    # where /home is a symlink, the two differ and only comparing one of them
    # would wrongly report the file as being outside every root.
    candidates = {path, path.resolve()}
    desktop_id = None
    source_root = None
    for root in ordered_application_roots():
        for root_path in {root.path, root.path.resolve()}:
            for candidate in candidates:
                try:
                    desktop_id = desktop_id_for(root_path, candidate)
                except ValueError:
                    continue
                source_root = root.path
                break
            if desktop_id is not None:
                break
        if desktop_id is not None:
            break

    if desktop_id is None:
        # §7.3 and rule 2 forbid keying by basename. Say so rather than
        # inventing an ID that would not match a real discovery pass.
        print(f"path                {path}")
        print("desktop ID          <undefined: file is not under a discovered root>")
        print(f"                    (basename is {path.name}, which is NOT a valid ID here)")
        desktop_id = path.name
    else:
        print(f"path                {path}")
        print(f"desktop ID          {desktop_id}")
        print(f"source root         {source_root}")

    app = build_application(parsed, desktop_id=desktop_id, source_root=source_root)

    print(f"type                {app.entry_type}")
    print(f"name                {app.name}")
    print(f"localized name      {app.localized_name}")
    print(f"raw Exec            {app.raw_exec}")
    print(f"parsed argv         {app.exec_argv}")
    print(f"exec parse mode     {app.exec_parse_mode}")
    if app.nonstandard_exec:
        print("nonstandard exec    yes (strict grammar would mis-tokenize this value)")
    print(f"field codes         {app.field_codes or '(none)'}")
    if parsed.exec_result and parsed.exec_result.dropped_tokens:
        print(f"dropped tokens      {list(parsed.exec_result.dropped_tokens)}")
    print(f"working directory   {app.working_directory}")
    print(f"TryExec             {app.try_exec}")
    print(f"icon name           {app.icon_name}")
    print(f"source kind         {app.source_kind}")
    if app.flatpak_id:
        print(f"flatpak id          {app.flatpak_id}")
    if app.snap_instance:
        print(f"snap instance       {app.snap_instance}")
    print(f"Hidden              {app.hidden}")
    print(f"NoDisplay           {app.no_display}")
    print(f"Terminal            {app.terminal}")
    print(f"DBusActivatable     {app.dbus_activatable}")
    print(f"OnlyShowIn          {app.only_show_in or '(none)'}")
    print(f"NotShowIn           {app.not_show_in or '(none)'}")
    print(f"support status      {_status(app)}")
    if app.unsupported_reason:
        print(f"reason              {app.unsupported_reason}")
        print(f"reason code         {app.unsupported_code}")
    if app.parse_warnings:
        print("warnings")
        for warning in app.parse_warnings:
            print(f"    {warning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steam-desktop-importer",
        description="Import .desktop applications into Steam as non-Steam shortcuts. "
        "Phases 0-1 are implemented: discovery and parsing only, no Steam writes.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    debug = subcommands.add_parser("debug", help="inspection commands that never mutate state")
    debug_commands = debug.add_subparsers(dest="debug_command", required=True)

    roots = debug_commands.add_parser("roots", help="show the ordered applications/ roots")
    roots.add_argument("--no-supplemental", action="store_true")
    roots.set_defaults(func=_print_roots)

    scan = debug_commands.add_parser("scan", help="discover and resolve desktop entries")
    scan.add_argument("--no-supplemental", action="store_true")
    scan.add_argument("--all", action="store_true", help="include NoDisplay entries")
    scan.add_argument("--importable-only", action="store_true")
    scan.add_argument(
        "--nonstandard-only",
        action="store_true",
        help="only entries whose Exec needed the compatibility tokenizer",
    )
    scan.set_defaults(func=_print_scan)

    entry = debug_commands.add_parser("desktop-entry", help="explain one desktop file")
    entry.add_argument("path")
    entry.set_defaults(func=_print_desktop_entry)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
