"""Import selected desktop entries through a safe VDF commit (Phase 7).

§26 workflow without artwork: confirm the entry, resolve a launch vector,
allocate or reuse a stable AppID, mutate the in-memory document, commit the
VDF, then persist mappings. §27 order is VDF first, state second.

Possible Existing Match is never auto-owned. Relink is an explicit call with
the existing unsigned AppID.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..launch import LaunchAdapterError, build_launch_vector
from ..models import DesktopApplication, SteamAccount, SteamInstallation
from ..state import (
    STATUS_POSSIBLE_MATCH,
    StateStore,
    current_exec,
    current_name,
    import_status,
)
from .appid import allocate_appid
from .commit import CommitHooks, CommitResult, commit_shortcuts
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
    """VDF commit plus any state rows that could not be persisted afterwards."""

    commit: CommitResult
    imported: tuple[ImportedShortcut, ...]
    state_errors: tuple[str, ...] = ()


def _fields_for(app: DesktopApplication) -> tuple[str, str, str, str]:
    vector = build_launch_vector(app)
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
) -> ImportedShortcut:
    mapping = store.get_mapping(installation_key, account_id32, app.desktop_id)
    name, exe, start_dir, launch_options = _fields_for(app)
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
) -> ImportResult:
    """Mutate, commit, then persist mappings for ``applications``.

    The VDF is not touched if planning fails. After a successful commit,
    mapping failures are returned in ``state_errors`` rather than rolling
    back Steam's file.
    """
    if not applications:
        raise ImportPlanningError("no applications selected")
    seen: set[str] = set()
    for app in applications:
        if app.desktop_id in seen:
            raise ImportPlanningError(f"duplicate desktop ID in batch: {app.desktop_id}")
        seen.add(app.desktop_id)
        _require_present(app)

    vdf_path = shortcuts_vdf_path(account)
    document = ShortcutDocument.load(vdf_path)
    if original_bytes is None:
        original_bytes = vdf_path.read_bytes() if vdf_path.is_file() else b""

    occupied = document.occupied_appids() | store.occupied_appids(
        installation.key, account.account_id32
    )
    planned: list[ImportedShortcut] = []
    links = dict(relink_appids or {})
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
            )
        )

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
    return ImportResult(commit=commit, imported=tuple(planned), state_errors=state_errors)


def relink_application(
    app: DesktopApplication,
    appid_unsigned: int,
    *,
    installation: SteamInstallation,
    account: SteamAccount,
    store: StateStore,
    steam_status: SteamRunningStatus | None = None,
    hooks: CommitHooks | None = None,
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
    )
