"""The album-art square: shows the cover, and lets the user swap in an
image of their own (click) or drop it (right-click)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QLabel, QMenu

SIZE = 180


class CoverLabel(QLabel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QLabel { border: 1px solid palette(mid); border-radius: 4px; color: palette(mid); }")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to choose an image; right-click to remove it")
        self._data: Optional[bytes] = None
        self.set_cover(None)

    @property
    def cover(self) -> Optional[bytes]:
        return self._data

    def set_cover(self, data: Optional[bytes], placeholder: str = "No cover art") -> None:
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self._data = data
            self.setPixmap(pixmap.scaled(SIZE - 4, SIZE - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._data = None
            self.setPixmap(QPixmap())
            self.setText(placeholder)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._choose_file()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Choose Image…", self._choose_file)
        remove = menu.addAction("Remove Cover", lambda: (self.set_cover(None), self.changed.emit()))
        remove.setEnabled(self._data is not None)
        menu.exec(event.globalPos())

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Cover Art", "", "Images (*.jpg *.jpeg *.png)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return
        self.set_cover(data)
        self.changed.emit()
