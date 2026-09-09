"""Account confirmation dialog.

IMPLEMENTATION.md §13 and rule 8: when several accounts exist, rank and
preselect using hints, but require an explicit choice. This dialog is that
choice. It does not persist anything; remembering the answer is Phase 5.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..models import SteamAccount
from ..steam import AccountSelection
from .workers import account_label

__all__ = ["AccountDialog"]


class AccountDialog(QDialog):
    """Ask the user to confirm which Steam account to use."""

    def __init__(
        self,
        selection: AccountSelection,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a Steam account")
        self._accounts = list(selection.accounts)
        self._chosen: SteamAccount | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Several Steam accounts are available. The importer will not "
                "guess. Pick the account whose shortcuts should be managed.\n\n"
                f"{selection.reason}"
            )
        )

        self._list = QListWidget()
        for account in self._accounts:
            hints = ", ".join(account.selection_hints) or "no hints"
            item = QListWidgetItem(f"{account_label(account)}\n  {hints}")
            item.setData(Qt.ItemDataRole.UserRole, account.account_id32)
            self._list.addItem(item)
        if selection.selected is not None:
            for row, account in enumerate(self._accounts):
                if account.account_id32 == selection.selected.account_id32:
                    self._list.setCurrentRow(row)
                    break
        self._list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(480, 320)

    def chosen_account(self) -> SteamAccount | None:
        return self._chosen

    def accept(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._accounts):
            self._chosen = self._accounts[row]
        super().accept()
