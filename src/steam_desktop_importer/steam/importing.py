"""Import selected desktop entries through a safe VDF commit (Phases 7–12).

§26 / §18.4 order: prepare artwork temps, mutate the in-memory VDF, commit
the VDF, persist mappings, then place ``grid/`` files. Optional collection
membership is applied last. Artwork and collection failures do not roll
back a successful shortcut write.

Possible Existing Match is never auto-owned. Relink is an explicit call with
the existing unsigned AppID.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..launch import LaunchAdapterError, build_launch_vector, wrap_for_flatpak_steam
from ..models import DesktopApplication, SteamAccount, SteamInstallation
from ..state.status import (
    STATUS_POSSIBLE_MATCH,
    current_exec,
    current_name,
    import_status,
)
from ..state.store import StateStore
from .appid import allocate_appid
from .artwork import SLOT_ICON, commit_artwork_files, destination_for
from .collection_commit import commit_collections
from .collections import CollectionAssignment, CollectionDocument, CollectionError
from .commit import CommitError, CommitHooks, CommitResult, commit_shortcuts
from .running import SteamRunningStatus
from .shortcut_identities import ExistingShortcut, shortcuts_vdf_path
from .shortcuts import ShortcutDocument, format_launch_options

__all__ = [
    "ImportPlanningError",
    "ImportResult",
    "ImportedShortcut",
    "apply_applications",
    "relink_application",
]


class ImportPlanningError(ValueError):
    """The VDF was not written because the batch could not be prepared."""


@dataclass(frozen=True)
class ImportedShortcut:
    """One application that was committed in this transaction."""

    desktop_id: str
    appid_unsigned: int
    action: str
    """``created``, ``updated``, or ``relinked``."""


@dataclass(frozen=True)
class ImportResult:
    """VDF commit plus any state, artwork, or collection steps that could not finish."""

    commit: CommitResult
    imported: tuple[ImportedShortcut, ...]
    state_errors: tuple[str, ...] = ()
    artwork_errors: tuple[str, ...] = ()
    collection_errors: tuple[str, ...] = ()


def _fields_for(
    app: DesktopApplication,
    *,
    installation: SteamInstallation,
    host_launch: bool,
    spawn_path: str | None = None,
) -> tuple[str, str, str, str]:
    vector = build_launch_vector(app)
    if installation.kind == "flatpak" and host_launch:
        vector = wrap_for_flatpak_steam(vector, spawn_path=spawn_path)
    return (
        current_name(app),
        vector.exe,
        vector.start_dir,
        format_launch_options(vector.arguments),
    )


def _require_present(app: DesktopApplication) -> None:
    if not app.supported_for_import:
        reason = app.unsupported_reason or "not supported for import"
        raise ImportPlanningError(f"{app.desktop_id}: {reason}")
    if not app.desktop_path.is_file():
        raise ImportPlanningError(
            f"{app.desktop_id}: desktop file is missing ({app.desktop_path})"
        )
    try:
        build_launch_vector(app)
    except LaunchAdapterError as error:
        raise ImportPlanningError(f"{app.desktop_id}: {error}") from error


def _apply_one(
    app: DesktopApplication,
    document: ShortcutDocument,
    store: StateStore,
    installation_key: str,
    account_id32: int,
    occupied: set[int],
    relink_appids: Mapping[str, int],
    icon_paths: Mapping[str, str],
    *,
    installation: SteamInstallation,
    host_launch: bool,
    spawn_path: str | None,
) -> ImportedShortcut:
    mapping = store.get_mapping(installation_key, account_id32, app.desktop_id)
    name, exe, start_dir, launch_options = _fields_for(
        app,
        installation=installation,
        host_launch=host_launch,
        spawn_path=spawn_path,
    )
    existing = [
        ExistingShortcut(
            appid_unsigned=entry.appid_unsigned,
            name=entry.name,
            exe=entry.exe,
            launch_options=entry.launch_options,
        )
        for entry in document.entries()
    ]
    status = import_status(app, mapping, existing)

    if app.desktop_id in relink_appids:
        appid = relink_appids[app.desktop_id]
        document.update_by_appid(
            appid,
            name=name,
            exe=exe,
            start_dir=start_dir,
            launch_options=launch_options,
        )
        occupied.add(appid)
        icon = icon_paths.get(app.desktop_id)
        if icon is not None:
            document.update_by_appid(appid, icon=icon)
        return ImportedShortcut(app.desktop_id, appid, "relinked")

    if status == STATUS_POSSIBLE_MATCH:
        raise ImportPlanningError(
            f"{app.desktop_id}: Possible Existing Match must be relinked explicitly"
        )

    if mapping is not None:
        appid = mapping.steam_appid_unsigned
        if document.find_by_appid(appid) is not None:
            document.update_by_appid(
                appid,
                name=name,
                exe=exe,
                start_dir=start_dir,
                launch_options=launch_options,
            )
            action = "updated"
        else:
            if appid in occupied:
                raise ImportPlanningError(
                    f"{app.desktop_id}: persisted AppID {appid} is occupied by "
                    "another shortcut; refusing to duplicate it"
                )
            document.add_new(
                appid_unsigned=appid,
                name=name,
                exe=exe,
                start_dir=start_dir,
                launch_options=launch_options,
            )
            action = "created"
        occupied.add(appid)
        icon = icon_paths.get(app.desktop_id)
        if icon is not None:
            document.update_by_appid(appid, icon=icon)
        return ImportedShortcut(app.desktop_id, appid, action)

    appid = allocate_appid(app.desktop_id, occupied)
    document.add_new(
        appid_unsigned=appid,
        name=name,
        exe=exe,
        start_dir=start_dir,
        launch_options=launch_options,
    )
    occupied.add(appid)
    icon = icon_paths.get(app.desktop_id)
    if icon is not None:
        document.update_by_appid(appid, icon=icon)
    return ImportedShortcut(app.desktop_id, appid, "created")


def _persist_mappings(
    imported: Sequence[ImportedShortcut],
    applications: Sequence[DesktopApplication],
    store: StateStore,
    installation_key: str,
    account_id32: int,
) -> tuple[str, ...]:
    by_id = {app.desktop_id: app for app in applications}
    errors: list[str] = []
    for item in imported:
        app = by_id[item.desktop_id]
        try:
            store.save_mapping(
                installation_key,
                account_id32,
                app.desktop_id,
                item.appid_unsigned,
                current_name(app),
                current_exec(app),
                app.desktop_path,
            )
        except Exception as error:  # noqa: BLE001 — VDF already committed
            errors.append(f"{app.desktop_id}: {error}")
    return tuple(errors)


def _commit_collection_membership(
    account: SteamAccount,
    appids: Sequence[int],
    assignment: CollectionAssignment | None,
    *,
    steam_status: SteamRunningStatus | None,
    hooks: CommitHooks | None,
) -> tuple[str, ...]:
    """Add imported AppIDs to collections. Failures do not roll back the VDF."""
    if assignment is None or assignment.is_empty():
        return ()
    errors: list[str] = []
    try:
        document = CollectionDocument.load(account)
        namespace_path = document.namespace_path
        index_path = document.index_path
        if namespace_path is None or index_path is None:
            return ("collections document has no destination paths",)
        before_entries = deepcopy(document.entries)
        before_index = deepcopy(document.index)
        errors.extend(document.apply_assignment(assignment, appids))
        if document.entries == before_entries and document.index == before_index:
            return tuple(errors)
        commit_collections(
            document,
            original_namespace_bytes=document.original_namespace_bytes,
            original_index_bytes=document.original_index_bytes,
            steam_status=steam_status,
            hooks=hooks,
        )
    except (CollectionError, CommitError, OSError) as error:
        errors.append(str(error))
    return tuple(errors)


def apply_applications(
    applications: Sequence[DesktopApplication],
    *,
    installation: SteamInstallation,
    account: SteamAccount,
    store: StateStore,
    relink_appids: Mapping[str, int] | None = None,
    steam_status: SteamRunningStatus | None = None,
    hooks: CommitHooks | None = None,
    original_bytes: bytes | None = None,
    icon_paths: Mapping[str, str] | None = None,
    artwork_files: Mapping[str, Mapping[str, Path]] | None = None,
    collections: CollectionAssignment | None = None,
    host_launch: bool | None = None,
    flatpak_spawn: str | None = None,
) -> ImportResult:
    """Mutate, commit, then persist mappings, place artwork, and optionally collect.

    The VDF is not touched if planning fails. After a successful commit,
    mapping, artwork, and collection failures are returned rather than
    rolling back Steam's shortcut file. Artwork is committed last among
    shortcut-adjacent writes (IMPLEMENTATION.md §18.4); collections are a
    separate cloud-storage document after that.

    Flatpak Steam imports wrap the host command with ``flatpak-spawn --host``
    when host launching is enabled. That wrap is experimental and never
    grants sandbox permissions.
    """
    if not applications:
        raise ImportPlanningError("no applications selected")
    seen: set[str] = set()
    for app in applications:
        if app.desktop_id in seen:
            raise ImportPlanningError(f"duplicate desktop ID in batch: {app.desktop_id}")
        seen.add(app.desktop_id)
        _require_present(app)

    wrap_host = (
        store.flatpak_steam_host_launch() if host_launch is None else host_launch
    )
    vdf_path = shortcuts_vdf_path(account)
    document = ShortcutDocument.load(vdf_path)
    if original_bytes is None:
        original_bytes = vdf_path.read_bytes() if vdf_path.is_file() else b""

    occupied = document.occupied_appids() | store.occupied_appids(
        installation.key, account.account_id32
    )
    planned: list[ImportedShortcut] = []
    links = dict(relink_appids or {})
    icons = dict(icon_paths or {})
    prepared_art = dict(artwork_files or {})
    for app in applications:
        planned.append(
            _apply_one(
                app,
                document,
                store,
                installation.key,
                account.account_id32,
                occupied,
                links,
                icons,
                installation=installation,
                host_launch=wrap_host,
                spawn_path=flatpak_spawn,
            )
        )
    _assign_artwork_icons(document, account, planned, icons, prepared_art)

    commit = commit_shortcuts(
        vdf_path,
        document,
        original_bytes=original_bytes,
        steam_status=steam_status,
        hooks=hooks,
    )
    state_errors = _persist_mappings(
        planned,
        applications,
        store,
        installation.key,
        account.account_id32,
    )
    appids = {item.desktop_id: item.appid_unsigned for item in planned}
    artwork_errors = commit_artwork_files(account, appids, prepared_art)
    collection_errors = _commit_collection_membership(
        account,
        list(appids.values()),
        collections,
        steam_status=steam_status,
        hooks=hooks,
    )
    return ImportResult(
        commit=commit,
        imported=tuple(planned),
        state_errors=state_errors,
        artwork_errors=artwork_errors,
        collection_errors=collection_errors,
    )


def relink_application(
    app: DesktopApplication,
    appid_unsigned: int,
    *,
    installation: SteamInstallation,
    account: SteamAccount,
    store: StateStore,
    steam_status: SteamRunningStatus | None = None,
    hooks: CommitHooks | None = None,
    icon_paths: Mapping[str, str] | None = None,
    artwork_files: Mapping[str, Mapping[str, Path]] | None = None,
    collections: CollectionAssignment | None = None,
    host_launch: bool | None = None,
    flatpak_spawn: str | None = None,
) -> ImportResult:
    """Take ownership of an existing unmanaged shortcut. Never allocates."""
    vdf_path = shortcuts_vdf_path(account)
    document = ShortcutDocument.load(vdf_path)
    if document.find_by_appid(appid_unsigned) is None:
        raise ImportPlanningError(
            f"{app.desktop_id}: no shortcut with AppID {appid_unsigned} to relink"
        )
    existing = store.get_mapping(installation.key, account.account_id32, app.desktop_id)
    if existing is not None and existing.steam_appid_unsigned != appid_unsigned:
        raise ImportPlanningError(
            f"{app.desktop_id}: already mapped to AppID "
            f"{existing.steam_appid_unsigned}; refusing to retarget"
        )
    owner = next(
        (
            mapping
            for mapping in store.list_mappings(installation.key, account.account_id32)
            if mapping.steam_appid_unsigned == appid_unsigned
            and mapping.desktop_id != app.desktop_id
        ),
        None,
    )
    if owner is not None:
        raise ImportPlanningError(
            f"{app.desktop_id}: AppID {appid_unsigned} is already owned by "
            f"{owner.desktop_id}"
        )
    return apply_applications(
        [app],
        installation=installation,
        account=account,
        store=store,
        relink_appids={app.desktop_id: appid_unsigned},
        steam_status=steam_status,
        hooks=hooks,
        icon_paths=icon_paths,
        artwork_files=artwork_files,
        collections=collections,
        host_launch=host_launch,
        flatpak_spawn=flatpak_spawn,
    )


def _assign_artwork_icons(
    document: ShortcutDocument,
    account: SteamAccount,
    planned: Sequence[ImportedShortcut],
    icons: Mapping[str, str],
    artwork_files: Mapping[str, Mapping[str, Path]],
) -> None:
    """Write the persistent ``_icon`` path into the VDF before it is committed.

    Creating the grid file is not enough (IMPLEMENTATION.md §20). The path is
    known before placement; a later artwork failure leaves a dangling icon
    path and a still-valid shortcut.
    """
    for item in planned:
        if item.desktop_id in icons:
            continue
        source = artwork_files.get(item.desktop_id, {}).get(SLOT_ICON)
        if source is None:
            continue
        try:
            dest = destination_for(account, item.appid_unsigned, SLOT_ICON, source)
        except Exception:  # noqa: BLE001 — placement reports the same failure
            continue
        document.update_by_appid(item.appid_unsigned, icon=str(dest))
