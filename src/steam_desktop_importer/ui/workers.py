"""Background workers.

IMPLEMENTATION.md §25.4 requires desktop scanning to happen off the GUI
thread. Scanning the capture host walks six roots and parses 800+ files, which
is comfortably enough to freeze a window if done inline.

``QRunnable`` on the default ``QThreadPool``, as §25.4 suggests. Signals live
on a separate ``QObject`` because ``QRunnable`` is not one.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ..desktop.discovery import DiscoveryResult, discover_applications
from ..models import SteamAccount, SteamInstallation
from ..steam import discover_accounts, discover_installations
from ..steam.running import SteamRunningStatus, detect_steam_running

__all__ = [
    "ScanWorker",
    "SteamProbeWorker",
    "account_label",
    "installation_label",
]


class _ScanSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class ScanWorker(QRunnable):
    """Run desktop discovery off the GUI thread."""

    def __init__(self, acknowledged_collisions: set[str] | None = None) -> None:
        super().__init__()
        self.signals = _ScanSignals()
        self.setAutoDelete(True)
        self._acknowledged = acknowledged_collisions or set()

    @Slot()
    def run(self) -> None:
        try:
            result: DiscoveryResult = discover_applications(
                acknowledged_collisions=self._acknowledged
            )
        except Exception as error:  # noqa: BLE001 - a worker must never crash the pool
            self.signals.failed.emit(str(error))
            return
        self.signals.finished.emit(result)


class _SteamProbeSignals(QObject):
    finished = Signal(object, object)
    """(installations_with_accounts, running_status)"""

    failed = Signal(str)


class SteamProbeWorker(QRunnable):
    """Probe Steam installations, their accounts, and whether Steam is running.

    Grouped into one worker because the window shows all three together and
    they are all cheap filesystem or process reads.
    """

    def __init__(self) -> None:
        super().__init__()
        self.signals = _SteamProbeSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            installations = discover_installations()
            accounts: dict[str, list[SteamAccount]] = {}
            for installation in installations:
                accounts[installation.key] = discover_accounts(installation)
            running: SteamRunningStatus = detect_steam_running()
        except Exception as error:  # noqa: BLE001
            self.signals.failed.emit(str(error))
            return
        self.signals.finished.emit(
            [(installation, accounts[installation.key]) for installation in installations],
            running,
        )


def installation_label(installation: SteamInstallation) -> str:
    """Short label for the installation selector."""
    suffix = " (experimental)" if installation.is_experimental else ""
    return f"{installation.kind}: {installation.root}{suffix}"


def account_label(account: SteamAccount) -> str:
    """Short label for the account selector.

    Falls back through persona name, account name, then the bare ID, because
    §13 allows every one of those fields to be missing.
    """
    name = account.persona_name or account.account_name
    return f"{name} ({account.account_id32})" if name else str(account.account_id32)
