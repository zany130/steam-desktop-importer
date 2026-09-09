"""Import-status classification (IMPLEMENTATION.md §25.1, Phase 5).

``Imported`` here means *managed in importer state*, not *verified present in
``shortcuts.vdf``*. Confirming the VDF row exists is Phase 6. Until the
importer has written a mapping, every application is ``New`` or, if a
read-only VDF identity looks like the same launch, ``Possible Existing
Match``.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import DesktopApplication
from ..steam.shortcut_identities import ExistingShortcut, normalize_exe
from .store import ManagedMapping, StateStore

__all__ = [
    "STATUS_CHANGED",
    "STATUS_IMPORTED",
    "STATUS_NEW",
    "STATUS_POSSIBLE_MATCH",
    "STATUS_UNSCOPED",
    "classify_applications",
    "current_exec",
    "current_name",
    "import_status",
    "likely_existing_match",
]

STATUS_NEW = "New"
STATUS_IMPORTED = "Imported"
STATUS_CHANGED = "Changed"
STATUS_POSSIBLE_MATCH = "Possible Existing Match"
STATUS_UNSCOPED = "select Steam"
"""Shown when no installation+account is selected, so §6 identity is unknown."""


def current_name(app: DesktopApplication) -> str:
    return app.localized_name or app.name


def current_exec(app: DesktopApplication) -> str:
    return app.raw_exec or ""


def likely_existing_match(app: DesktopApplication, shortcut: ExistingShortcut) -> bool:
    """Whether ``shortcut`` is a plausible unmanaged match for ``app``.

    §17 allows ``(name, exe, launch options)`` as a heuristic and forbids
    taking ownership solely because ``(appname, exe)`` matches. This function
    is the heuristic; it never writes a mapping.
    """
    if current_name(app) != shortcut.name:
        return False
    app_exe = normalize_exe(app.exec_argv[0] if app.exec_argv else "")
    if not app_exe or normalize_exe(shortcut.exe) != app_exe:
        return False
    return True


def import_status(
    app: DesktopApplication,
    mapping: ManagedMapping | None,
    existing: Iterable[ExistingShortcut] = (),
) -> str:
    """Classify one application against state and optional existing shortcuts."""
    if mapping is not None:
        if (
            mapping.last_known_name != current_name(app)
            or mapping.last_known_exec != current_exec(app)
        ):
            return STATUS_CHANGED
        return STATUS_IMPORTED
    if any(likely_existing_match(app, shortcut) for shortcut in existing):
        return STATUS_POSSIBLE_MATCH
    return STATUS_NEW


def classify_applications(
    apps: Iterable[DesktopApplication],
    store: StateStore,
    installation_key: str | None,
    account_id32: int | None,
    existing: Iterable[ExistingShortcut] = (),
) -> dict[str, str]:
    """Map each desktop ID to a §25.1 status for the selected Steam identity.

    Without an installation and account there is no §6 row to consult, so
    every application is :data:`STATUS_UNSCOPED` rather than ``New``.
    """
    if installation_key is None or account_id32 is None:
        return {app.desktop_id: STATUS_UNSCOPED for app in apps}
    mappings = {
        mapping.desktop_id: mapping
        for mapping in store.list_mappings(installation_key, account_id32)
    }
    existing_list = list(existing)
    return {
        app.desktop_id: import_status(app, mappings.get(app.desktop_id), existing_list)
        for app in apps
    }
