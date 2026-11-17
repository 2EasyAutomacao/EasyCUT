"""Widgets pequenos reutilizáveis."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog, QFileDialog, QHBoxLayout, QLineEdit, QToolButton, QWidget,
)


class PathEdit(QWidget):
    """Campo de caminho com botão de procurar e suporte a arrastar-e-soltar."""

    changed = Signal(str)

    def __init__(self, mode: str = "open", file_filter: str = "", placeholder: str = "",
                 parent=None):
        super().__init__(parent)
        self.mode = mode  # open | dir | save
        self.file_filter = file_filter
        self._last = ""
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        self.line.setClearButtonEnabled(True)
        self.line.setAcceptDrops(False)
        self.line.editingFinished.connect(self._commit)
        self.line.textChanged.connect(lambda t: t == "" and self._commit())
        btn = QToolButton()
        btn.setText("…")
        btn.setToolTip("Procurar")
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.line, 1)
        lay.addWidget(btn)
        self.setAcceptDrops(True)

    def path(self) -> str:
        return self.line.text().strip()

    def set_path(self, path: str) -> None:
        self._last = path or ""
        self.line.blockSignals(True)
        self.line.setText(self._last)
        self.line.blockSignals(False)

    def _commit(self):
        p = self.path()
        if p != self._last:
            self._last = p
            self.changed.emit(p)

    def _start_dir(self) -> str:
        p = Path(self.path()).expanduser()
        if p.is_dir():
            return str(p)
        if p.parent.is_dir() and str(p) not in ("", "."):
            return str(p.parent)
        return str(Path.home())

    def _browse(self):
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "Escolher pasta", self._start_dir())
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, "Salvar", self._start_dir(), self.file_filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Escolher arquivo", self._start_dir(),
                                               self.file_filter)
        if p:
            self.line.setText(p)
            self._commit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.line.setText(urls[0].toLocalFile())
            self._commit()


class ColorButton(QToolButton):
    colorChanged = Signal(str)

    def __init__(self, color: str = "#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = color
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = QColor(color).name().upper()
        self._refresh()

    def _refresh(self):
        pm = QPixmap(28, 16)
        pm.fill(QColor(self._color))
        p = QPainter(pm)
        p.setPen(QColor(128, 128, 128))
        p.drawRect(0, 0, 27, 15)
        p.end()
        self.setIcon(QIcon(pm))
        self.setText(self._color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Escolher cor")
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(self._color)
