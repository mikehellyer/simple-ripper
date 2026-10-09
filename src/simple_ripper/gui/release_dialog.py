"""Choosing which MusicBrainz release is the right one — for a disc that
matches several pressings, or a free-text search for the Tagger."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from simple_ripper.core.metadata import describe_release
from simple_ripper.core.musicbrainz import MusicBrainzError, search_releases
from simple_ripper.gui.workers import BackgroundRunner

COLUMNS = [
    ("title", "Title"),
    ("artist", "Artist"),
    ("date", "Date"),
    ("country", "Country"),
    ("format", "Format"),
    ("tracks", "Tracks"),
    ("label", "Label / Cat. No."),
    ("barcode", "Barcode"),
]


class ReleaseDialog(QDialog):
    def __init__(
        self,
        runner: BackgroundRunner,
        releases: Optional[list[dict]] = None,
        *,
        title: str = "Choose a Release",
        intro: str = "",
        search: Optional[tuple[str, str]] = None,
        track_count: Optional[int] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 460)
        self._runner = runner
        self._releases: list[dict] = []
        self._track_count = track_count
        self.selected_release: Optional[dict] = None

        layout = QVBoxLayout(self)
        if intro:
            label = QLabel(intro)
            label.setWordWrap(True)
            layout.addWidget(label)

        search_row = QHBoxLayout()
        self._artist = QLineEdit()
        self._artist.setPlaceholderText("Artist")
        self._album = QLineEdit()
        self._album.setPlaceholderText("Album")
        self._search_button = QPushButton("Search MusicBrainz")
        self._search_button.clicked.connect(self._search)
        for edit in (self._artist, self._album):
            edit.returnPressed.connect(self._search)
        search_row.addWidget(QLabel("Artist:"))
        search_row.addWidget(self._artist, 1)
        search_row.addWidget(QLabel("Album:"))
        search_row.addWidget(self._album, 1)
        search_row.addWidget(self._search_button)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels([label for _, label in COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().hide()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.doubleClicked.connect(self.accept)
        layout.addWidget(self._table, 1)

        self._status = QLabel()
        layout.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use This Release")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if search is not None:
            self._artist.setText(search[0])
            self._album.setText(search[1])
        if releases:
            self.set_releases(releases)
        elif search is not None and (search[0] or search[1]):
            self._search()

    def set_releases(self, releases: list[dict]) -> None:
        self._releases = releases
        self._table.setRowCount(len(releases))
        for row, release in enumerate(releases):
            info = describe_release(release)
            for col, (key, _) in enumerate(COLUMNS):
                self._table.setItem(row, col, QTableWidgetItem(info[key]))
        if releases:
            self._table.selectRow(0)
            self._status.setText(f"{len(releases)} release{'s' if len(releases) != 1 else ''} found")
        else:
            self._status.setText("Nothing found — try fewer or different words")

    def _search(self) -> None:
        artist, album = self._artist.text(), self._album.text()
        if not (artist.strip() or album.strip()):
            return
        self._search_button.setEnabled(False)
        self._status.setText("Searching MusicBrainz…")
        track_count = self._track_count
        self._runner.run(
            lambda: search_releases(artist, album, track_count),
            self._on_search_done,
            self._on_search_failed,
        )

    def _on_search_done(self, releases) -> None:
        self._search_button.setEnabled(True)
        self.set_releases(releases)

    def _on_search_failed(self, error) -> None:
        self._search_button.setEnabled(True)
        self._status.setText(str(error) if isinstance(error, MusicBrainzError) else f"Search failed: {error}")

    def accept(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        self.selected_release = self._releases[rows[0].row()]
        super().accept()
