"""Table delegates for source and status badges."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

_SOURCE_COLORS = {
    "native": "#3d6ea8",
    "flatpak": "#4a90d9",
    "snap": "#79c142",
    "appimage": "#d48806",
    "unknown": "#6d6d6d",
}

_STATUS_COLORS = {
    "Available": "#2e7d32",
    "Hidden": "#6d6d6d",
    "Unavailable": "#c05621",
    "Unsupported": "#b3261e",
    "unknown": "#6d6d6d",
}


class BadgeDelegate(QStyledItemDelegate):
    """Draw a compact coloured pill instead of plain text."""

    def __init__(self, palette: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._palette = palette

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        color = QColor(self._palette.get(str(text), "#6d6d6d"))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(option.font)
        font.setPointSize(max(font.pointSize() - 1, 8))
        font.setBold(True)
        metrics = QFontMetrics(font)
        label = str(text)
        width = metrics.horizontalAdvance(label) + 14
        height = metrics.height() + 4
        x = option.rect.x() + 6
        y = option.rect.y() + (option.rect.height() - height) // 2
        pill = QRect(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(pill, 8, 8)
        painter.setPen(QColor("white"))
        painter.setFont(font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setWidth(max(hint.width(), 96))
        return hint


def source_badge_delegate(parent=None) -> BadgeDelegate:
    return BadgeDelegate(_SOURCE_COLORS, parent)


def status_badge_delegate(parent=None) -> BadgeDelegate:
    return BadgeDelegate(_STATUS_COLORS, parent)
