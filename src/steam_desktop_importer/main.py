"""Command-line entry point.

IMPLEMENTATION.md §33 asks for project-native debug tooling "rather than
ad-hoc scripts that guess the first userdata directory".

Implemented: ``debug roots``, ``debug scan``, ``debug desktop-entry``,
``debug launch``, ``debug steam``, ``debug identity``,
``debug dump-shortcuts``, ``debug snapshot-shortcuts``,
``debug compare-snapshots``, ``debug collections`` and ``debug steamgriddb``.

``debug launch`` prints the command a shortcut *would* use. It never executes
it and never writes to Steam. ``debug steam`` reads Steam's configuration and
never writes to it.

With no subcommand the PySide6 GUI starts. Import writes ``shortcuts.vdf``
only through the Phase 7 transaction, and only while Steam is closed.
Optional collection membership is written afterwards through
``steam/collection_commit.py``. Flatpak Steam imports wrap host commands
with ``flatpak-spawn --host`` and never grant sandbox permissions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .desktop.discovery import (
    desktop_id_for,
    discover_applications,
    ordered_application_roots,
)
from .desktop.parser import DesktopEntryError, build_application, parse_desktop_entry
from .launch import (
    LaunchAdapterError,
    build_launch_vector,
    probe_host_launch_permission,
    wrap_for_flatpak_steam,
)
from .models import DesktopApplication
from .state import (
    StateStore,
    current_exec,
    current_name,
    default_state_path,
    import_status,
)
from .steam import (
    CollectionError,
    ShortcutDocument,
    allocate_appid,
    detect_steam_running,
    discover_accounts,
    discover_installations,
    first_import_candidate,
    game_id_64,
    grid_dir,
    list_existing_shortcuts,
    load_collections,
    select_account,
    select_installation,
    shortcuts_vdf_path,
    uint32_to_int32,
)
from .steam.snapshot import compare_snapshots, snapshot_from_json, snapshot_library


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
    print(f"ID collisions      {len(result.collisions)}")
    for collision in result.collisions:
        print(f"    {collision.desktop_id} in {collision.root}")
        for path in collision.paths:
            marker = "winner" if path == collision.winner else "shadowed"
            print(f"        [{marker}] {path}")
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

    _print_launch_section(app)
    return 0


def _print_launch_section(app: DesktopApplication) -> None:
    """Show the Phase 2 launch vector, or why there is not one."""
    try:
        vector = build_launch_vector(app)
    except LaunchAdapterError as error:
        print(f"launch vector       <none: {error}>")
        print(f"launch reason code  {error.code}")
        return

    print(f"launch adapter      {vector.adapter}")
    print(f"launch exe          {vector.exe}")
    print(f"launch arguments    {list(vector.arguments)}")
    print(f"launch StartDir     {vector.start_dir or '(empty)'}")
    for warning in vector.warnings:
        print(f"    launch warning  {warning}")


def _print_steam(args: argparse.Namespace) -> int:
    """Show Steam installations and accounts. Read-only (§12-§13)."""
    installations = discover_installations()
    selection = select_installation(installations)

    print(f"installations       {len(installations)}")
    if not installations:
        print("  none found (probed the §12 native and Flatpak locations)")
        return 1

    for installation in installations:
        marker = "*" if installation == selection.selected else " "
        print(f" {marker} [{installation.kind}] {installation.root}")
        print(f"      userdata      {installation.userdata_root}")
        print(f"      registry.vdf  {installation.registry_path or '(absent)'}")
        print(f"      key           {installation.key}")
        if installation.is_experimental:
            print("      note          Flatpak Steam is experimental (§11)")
            permission = probe_host_launch_permission()
            print(f"      host launch   {'granted' if permission.granted else 'not granted'}")
            print(f"      evidence      {permission.evidence}")
            if not permission.granted:
                print(f"      override      {permission.override_command}")
                print("      override      never run by this importer")

    print(f"selection           {selection.reason}")
    print(f"needs confirmation  {selection.requires_confirmation}")

    running = detect_steam_running()
    if running.running:
        print(f"steam process       running ({'; '.join(running.evidence)})")
    elif not running.is_certain:
        print(
            f"steam process       unclear ({running.inspection_failures} processes unreadable)"
        )
    else:
        print("steam process       not running")

    for installation in installations:
        print()
        print(f"accounts in {installation.root}")
        accounts = discover_accounts(installation)
        if not accounts:
            print("  none found")
            continue
        account_selection = select_account(accounts)
        for account in accounts:
            marker = "*" if account == account_selection.selected else " "
            label = account.persona_name or account.account_name or "(unknown)"
            print(f" {marker} {account.account_id32}  {label}")
            print(f"      account name  {account.account_name or '(unknown)'}")
            print(f"      steamID64     {account.steam_id64}")
            print(f"      hints         {account.selection_hints or '(none)'}")
        print(f"  selection         {account_selection.reason}")
        print(f"  needs confirmation {account_selection.requires_confirmation}")

    # "*" is a preselection, not a decision. Rules 7 and 8 forbid acting on it
    # without the user, so say so rather than letting the marker imply consent.
    print()
    print("* = preselected. Nothing is chosen until confirmed (rules 7 and 8).")
    return 0


def _print_identity(args: argparse.Namespace) -> int:
    """Show §33 identity for one desktop ID. Never writes Steam or state."""
    result = discover_applications()
    app = result.applications.get(args.desktop_id)
    if app is None:
        print(f"error: no resolved entry with desktop ID {args.desktop_id!r}", file=sys.stderr)
        return 1

    store = StateStore(default_state_path(), create=False)
    installations = discover_installations()
    installation_selection = select_installation(
        installations, remembered_key=store.remembered_installation()
    )
    installation = installation_selection.selected
    account = None
    existing = None
    occupied: set[int] = set()

    print(f"desktop ID          {app.desktop_id}")
    print(f"current Name        {current_name(app)}")
    print(f"current Exec        {current_exec(app)}")

    if installation is None:
        print("Steam installation  (none found)")
        print("account             (none)")
    else:
        confirmed = "confirmed" if installation_selection.is_resolved else "unconfirmed preselection"
        print(f"Steam installation  {installation.key} ({confirmed})")
        print(f"                    {installation_selection.reason}")
        accounts = discover_accounts(installation)
        account_selection = select_account(
            accounts,
            remembered_account_id32=store.remembered_account(installation.key),
        )
        account = account_selection.selected
        if account is None:
            print("account             (none)")
        else:
            account_state = (
                "confirmed" if account_selection.is_resolved else "unconfirmed preselection"
            )
            label = account.persona_name or account.account_name or "(unknown)"
            print(f"account             {account.account_id32}  {label} ({account_state})")
            print(f"                    {account_selection.reason}")

    mapping = None
    if installation is not None and account is not None:
        mapping = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
        occupied = store.occupied_appids(installation.key, account.account_id32)
        vdf_path = shortcuts_vdf_path(account)
        try:
            existing = list_existing_shortcuts(vdf_path)
        except ValueError as error:
            print(f"shortcuts.vdf       <unparsable: {error}>")
            print("                    occupied AppIDs from VDF are unknown; not treated as empty")
            existing = None
        else:
            print(f"shortcuts.vdf       {vdf_path} ({len(existing)} identities, read-only)")
            occupied = occupied | {item.appid_unsigned for item in existing}

    if mapping is not None:
        unsigned = mapping.steam_appid_unsigned
        print(f"persisted AppID     {unsigned} (0x{unsigned:08x})")
        print(f"signed VDF          {uint32_to_int32(unsigned)}")
        print(f"game_id_64          {game_id_64(unsigned)}")
        print(f"import status       {import_status(app, mapping, existing or ())}")
    else:
        candidate = first_import_candidate(app.desktop_id)
        print("persisted AppID     (none — this importer has never written a mapping)")
        print(f"first-import cand.  {candidate} (0x{candidate:08x})")
        if existing is None and installation is not None and account is not None:
            shown = candidate
            print("allocated AppID     (not computed; existing VDF identities are unknown)")
        elif installation is None or account is None:
            shown = candidate
            print(f"allocated AppID     {candidate} (0x{candidate:08x}) (no Steam target)")
        else:
            shown = allocate_appid(app.desktop_id, occupied)
            if shown != candidate:
                print(f"allocated AppID     {shown} (0x{shown:08x}) (candidate collided)")
            else:
                print(f"allocated AppID     {shown} (0x{shown:08x}) (candidate is free)")
        print(f"signed VDF          {uint32_to_int32(shown)}")
        print(f"game_id_64          {game_id_64(shown)}")
        print(f"import status       {import_status(app, None, existing or ())}")
        print("                    Imported means managed in state, not verified in VDF.")
    store.close()
    return 0


def _print_dump_shortcuts(args: argparse.Namespace) -> int:
    """Show parsed shortcuts.vdf without mutation (§33)."""
    store = StateStore(default_state_path(), create=False)
    installations = discover_installations()
    installation_selection = select_installation(
        installations,
        remembered_key=args.steam_installation or store.remembered_installation(),
    )
    installation = installation_selection.selected
    if installation is None:
        print("error: no Steam installation found", file=sys.stderr)
        store.close()
        return 1
    if not installation_selection.is_resolved and args.steam_installation is None:
        print(
            f"warning: installation is an unconfirmed preselection ({installation_selection.reason})",
            file=sys.stderr,
        )

    accounts = discover_accounts(installation)
    remembered = None
    if args.account is not None:
        remembered = args.account
    else:
        remembered = store.remembered_account(installation.key)
    account_selection = select_account(accounts, remembered_account_id32=remembered)
    account = account_selection.selected
    store.close()
    if account is None:
        print("error: no Steam account found", file=sys.stderr)
        return 1
    if not account_selection.is_resolved and args.account is None:
        print(
            f"warning: account is an unconfirmed preselection ({account_selection.reason})",
            file=sys.stderr,
        )

    path = shortcuts_vdf_path(account)
    print(f"installation        {installation.key}")
    print(f"account             {account.account_id32}")
    print(f"shortcuts.vdf       {path}")
    try:
        document = ShortcutDocument.load(path)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    entries = document.entries()
    print(f"indices             {document.indices()}")
    print(f"shortcuts           {len(entries)}")
    for entry in entries:
        print(f"[{entry.index}]")
        print(f"    appid unsigned  {entry.appid_unsigned} (0x{entry.appid_unsigned:08x})")
        print(f"    AppName         {entry.name}")
        print(f"    Exe             {entry.exe}")
        print(f"    StartDir        {entry.start_dir or '(empty)'}")
        print(f"    LaunchOptions   {entry.launch_options or '(empty)'}")
        if entry.extra_keys:
            print(f"    extra keys      {list(entry.extra_keys)}")
    return 0


def _debug_target_account(args: argparse.Namespace):
    """Resolve installation+account for read-only debug commands. Closes the store."""
    store = StateStore(default_state_path(), create=False)
    installations = discover_installations()
    installation_selection = select_installation(
        installations,
        remembered_key=args.steam_installation or store.remembered_installation(),
    )
    installation = installation_selection.selected
    if installation is None:
        print("error: no Steam installation found", file=sys.stderr)
        store.close()
        return None, None, 1
    if not installation_selection.is_resolved and args.steam_installation is None:
        print(
            f"warning: installation is an unconfirmed preselection ({installation_selection.reason})",
            file=sys.stderr,
        )

    accounts = discover_accounts(installation)
    remembered = args.account if args.account is not None else store.remembered_account(installation.key)
    account_selection = select_account(accounts, remembered_account_id32=remembered)
    account = account_selection.selected
    store.close()
    if account is None:
        print("error: no Steam account found", file=sys.stderr)
        return installation, None, 1
    if not account_selection.is_resolved and args.account is None:
        print(
            f"warning: account is an unconfirmed preselection ({account_selection.reason})",
            file=sys.stderr,
        )
    return installation, account, 0


def _print_snapshot_shortcuts(args: argparse.Namespace) -> int:
    """JSON fingerprints of shortcuts.vdf and grid/. No names. Never writes."""
    installation, account, status = _debug_target_account(args)
    if status != 0 or account is None or installation is None:
        return 1
    path = shortcuts_vdf_path(account)
    snap = snapshot_library(path, grid_dir(account))
    json.dump(snap.to_jsonable(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    print(
        f"{path}: {snap.shortcut_count} shortcuts, {len(snap.grid)} grid files, "
        f"sha256 {snap.vdf_digest}",
        file=sys.stderr,
    )
    return 0


def _print_collections(args: argparse.Namespace) -> int:
    """List live Steam collections. Read-only; never writes."""
    installation, account, status = _debug_target_account(args)
    if status != 0 or account is None or installation is None:
        return 1
    try:
        document = load_collections(account)
    except CollectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    path = document.namespace_path
    print(f"installation        {installation.key}")
    print(f"account             {account.account_id32}")
    print(f"namespace           {document.namespace_id}")
    print(f"namespace file      {path}")
    print(f"index file          {document.index_path}")
    live = document.live_collections()
    assignable = [item for item in live if item.assignable]
    print(f"live collections    {len(live)}")
    print(f"assignable          {len(assignable)}")
    for collection in live:
        flag = "assignable" if collection.assignable else "skipped"
        print(
            f"  {collection.collection_id:40} {flag:10} "
            f"added={len(collection.added):<5} {collection.name}"
        )
    return 0


def _print_compare_snapshots(args: argparse.Namespace) -> int:
    """Compare two snapshot JSON files. Exit 1 if unmanaged content changed."""
    before = snapshot_from_json(Path(args.before).read_text(encoding="utf-8"))
    after = snapshot_from_json(Path(args.after).read_text(encoding="utf-8"))
    report = compare_snapshots(
        before,
        after,
        managed_appids=tuple(args.ignore_appid or ()),
    )
    print(report.summary())
    if report.unmanaged_removed_appids:
        print(f"unmanaged removed appids: {list(report.unmanaged_removed_appids)}")
    if report.unmanaged_changed_appids:
        print(f"unmanaged changed appids: {list(report.unmanaged_changed_appids)}")
    if report.unmanaged_added_appids:
        print(f"unmanaged added appids: {list(report.unmanaged_added_appids)}")
    if report.unmanaged_grid_removed:
        print(f"unrelated grid removed: {list(report.unmanaged_grid_removed)}")
    if report.unmanaged_grid_changed:
        print(f"unrelated grid changed: {list(report.unmanaged_grid_changed)}")
    if report.unmanaged_grid_added:
        print(f"unrelated grid added: {list(report.unmanaged_grid_added)}")
    print(f"added appids: {list(report.added_appids)}")
    print(f"grid added: {len(report.grid_added)}")
    return 0 if report.ok else 1


def _print_launch(args: argparse.Namespace) -> int:
    """Show launch vectors for discovered entries, without writing Steam."""
    result = discover_applications()

    selected = list(result.applications.values())
    if args.desktop_id:
        selected = [app for app in selected if app.desktop_id == args.desktop_id]
        if not selected:
            print(f"error: no resolved entry with desktop ID {args.desktop_id!r}", file=sys.stderr)
            return 1
    elif not args.all:
        selected = [app for app in selected if not app.no_display]

    built = 0
    refused: list[tuple[str, str]] = []
    for app in selected:
        try:
            vector = build_launch_vector(app)
            if args.flatpak_steam:
                vector = wrap_for_flatpak_steam(vector)
        except LaunchAdapterError as error:
            refused.append((app.desktop_id, error.code))
            continue
        built += 1
        if args.desktop_id or args.verbose:
            print(app.desktop_id)
            print(f"    adapter     {vector.adapter}")
            print(f"    exe         {vector.exe}")
            print(f"    arguments   {list(vector.arguments)}")
            print(f"    StartDir    {vector.start_dir or '(empty)'}")
            for warning in vector.warnings:
                print(f"    warning     {warning}")

    if args.desktop_id:
        return 0

    print()
    print(f"considered          {len(selected)}")
    print(f"launch vectors      {built}")
    print(f"refused             {len(refused)}")
    counts: dict[str, int] = {}
    for _, code in refused:
        counts[code] = counts.get(code, 0) + 1
    for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {code}: {count}")
    return 0


def _sgdb_client():
    from .steamgriddb import MissingAPIKeyError, SteamGridDBClient, SteamGridDBError

    try:
        return SteamGridDBClient()
    except MissingAPIKeyError as error:
        print(error)
        return None
    except SteamGridDBError as error:
        print(error)
        return None


def _print_sgdb_search(args: argparse.Namespace) -> int:
    from .steamgriddb import SteamGridDBError, search_queries

    client = _sgdb_client()
    if client is None:
        return 2
    try:
        queries = search_queries(args.query)
        seen: set[int] = set()
        for index, query in enumerate(queries):
            print(f"query {index + 1}: {query}")
            try:
                games = client.search_games(query)
            except SteamGridDBError as error:
                print(f"  error: {error}")
                return 1
            if not games:
                print("  (no results)")
                continue
            for game in games:
                if game.id in seen:
                    continue
                seen.add(game.id)
                verified = "verified" if game.verified else "unverified"
                types = ",".join(game.types) if game.types else "-"
                print(f"  {game.id:>8}  {game.name}  [{types}]  {verified}")
    finally:
        client.close()
    return 0


def _print_sgdb_assets(args: argparse.Namespace) -> int:
    from .steamgriddb import SteamGridDBError

    client = _sgdb_client()
    if client is None:
        return 2
    kind = args.sgdb_command
    fetchers = {
        "grids": lambda: client.get_grids(
            args.game_id, dimensions=getattr(args, "dimensions", None) or None
        ),
        "heroes": lambda: client.get_heroes(args.game_id),
        "logos": lambda: client.get_logos(args.game_id),
        "icons": lambda: client.get_icons(args.game_id),
    }
    try:
        assets = fetchers[kind]()
    except SteamGridDBError as error:
        print(error)
        client.close()
        return 1
    client.close()
    if not assets:
        print("no artwork")
        return 0
    for asset in assets:
        size = f"{asset.width}x{asset.height}" if asset.width and asset.height else "-"
        print(f"{asset.id:>8}  {asset.style or '-':<12} {size:<12} {asset.url}")
    return 0


def _run_gui(_args: argparse.Namespace) -> int:
    from .ui.main_window import run_app

    return run_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steam-desktop-importer",
        description="Import .desktop applications into Steam as non-Steam shortcuts. "
        "With no subcommand, opens the GUI. Debug commands never write to Steam.",
    )
    subcommands = parser.add_subparsers(dest="command", required=False)
    parser.set_defaults(func=_run_gui)

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

    launch = debug_commands.add_parser(
        "launch",
        help="show launch vectors (Phase 2); never runs or writes anything",
    )
    launch.add_argument("desktop_id", nargs="?", help="limit to one desktop ID")
    launch.add_argument("--all", action="store_true", help="include NoDisplay entries")
    launch.add_argument("--verbose", action="store_true", help="print every vector")
    launch.add_argument(
        "--flatpak-steam",
        action="store_true",
        help="show the experimental flatpak-spawn --host wrap (Phase 11); never writes",
    )
    launch.set_defaults(func=_print_launch)

    steam = debug_commands.add_parser(
        "steam",
        help="show Steam installations and accounts (Phase 4); read-only",
    )
    steam.set_defaults(func=_print_steam)

    identity = debug_commands.add_parser(
        "identity",
        help="show §6/§16 identity for one desktop ID (Phase 5); never writes",
    )
    identity.add_argument("desktop_id")
    identity.set_defaults(func=_print_identity)

    dump = debug_commands.add_parser(
        "dump-shortcuts",
        help="show parsed shortcuts.vdf (Phase 6); read-only, never writes",
    )
    dump.add_argument("--steam-installation", help="SteamInstallation.key")
    dump.add_argument("--account", type=int, help="32-bit Steam account ID")
    dump.set_defaults(func=_print_dump_shortcuts)

    snap = debug_commands.add_parser(
        "snapshot-shortcuts",
        help="JSON fingerprints of shortcuts.vdf and grid/ (Phase 10); no names, never writes",
    )
    snap.add_argument("--steam-installation", help="SteamInstallation.key")
    snap.add_argument("--account", type=int, help="32-bit Steam account ID")
    snap.set_defaults(func=_print_snapshot_shortcuts)

    compare = debug_commands.add_parser(
        "compare-snapshots",
        help="compare two snapshot JSON files; exit 1 if unmanaged content changed",
    )
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument(
        "--ignore-appid",
        action="append",
        type=int,
        default=[],
        help="unsigned AppID owned by this importer; repeatable",
    )
    compare.set_defaults(func=_print_compare_snapshots)

    collections = debug_commands.add_parser(
        "collections",
        help="list Steam library collections (Phase 12); read-only, never writes",
    )
    collections.add_argument("--steam-installation", help="SteamInstallation.key")
    collections.add_argument("--account", type=int, help="32-bit Steam account ID")
    collections.set_defaults(func=_print_collections)

    sgdb = debug_commands.add_parser(
        "steamgriddb",
        help="search SteamGridDB (Phase 8); never writes Steam or artwork files",
    )
    sgdb_commands = sgdb.add_subparsers(dest="sgdb_command", required=True)
    sgdb_search = sgdb_commands.add_parser("search", help="search games by name")
    sgdb_search.add_argument("query")
    sgdb_search.set_defaults(func=_print_sgdb_search)
    for kind in ("grids", "heroes", "logos", "icons"):
        listing = sgdb_commands.add_parser(kind, help=f"list {kind} for a SteamGridDB game id")
        listing.add_argument("game_id", type=int)
        if kind == "grids":
            listing.add_argument(
                "--dimensions",
                action="append",
                help="repeatable, e.g. --dimensions 600x900",
            )
        listing.set_defaults(func=_print_sgdb_assets)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
