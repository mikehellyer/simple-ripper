"""The Tagger tab: Picard-style tagging of music files already on disk.

Add an album's files, look the album up on MusicBrainz, check how the
files were matched to its tracks (and fix any by right-clicking), then
save — which writes the tags and cover, and optionally renames/moves the
files with the same naming template rips use.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simple_ripper.core import musicbrainz
from simple_ripper.core.matching import guess_search, match_files
from simple_ripper.core.metadata import AlbumMeta, album_from_release, describe_release
from simple_ripper.core.settings import SettingsStore
from simple_ripper.core.tagger import save_file
from simple_ripper.core.tagging import AUDIO_EXTENSIONS, FileTags, TagError, read_tags
from simple_ripper.core.text import format_duration
from simple_ripper.gui.cover_label import CoverLabel
from simple_ripper.gui.release_dialog import ReleaseDialog
from simple_ripper.gui.workers import BackgroundRunner

UNMATCHED_COLOUR = QColor("#c0392b")


def _find_audio(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for root, _dirs, names in os.walk(path):
                found.extend(Path(root) / n for n in sorted(names) if Path(n).suffix.lower() in AUDIO_EXTENSIONS)
        elif path.suffix.lower() in AUDIO_EXTENSIONS:
            found.append(path)
    return found


class TaggerPage(QWidget):
    status_message = Signal(str)

    def __init__(self, settings: SettingsStore, runner: BackgroundRunner, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._runner = runner
        self._files: list[FileTags] = []
        self._album: Optional[AlbumMeta] = None
        self._matches: dict[int, int] = {}  # file index -> track index
        self._busy = False
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        for text, slot in (
            ("Add Files…", self._add_files),
            ("Add Folder…", self._add_folder),
            ("Remove", self._remove_selected),
            ("Clear", self._clear),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            top.addWidget(button)
        top.addStretch(1)
        self._lookup_button = QPushButton("Look Up on MusicBrainz…")
        self._lookup_button.setStyleSheet("QPushButton { font-weight: 600; padding: 4px 14px; }")
        self._lookup_button.clicked.connect(self._lookup)
        top.addWidget(self._lookup_button)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Files</b> — drop an album's files or folder here"))
        self._files_table = QTableWidget(0, 5)
        self._files_table.setHorizontalHeaderLabels(["File", "Title", "Artist", "Album", "Length"])
        self._files_table.verticalHeader().hide()
        self._files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self._files_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        left_layout.addWidget(self._files_table)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        release_row = QHBoxLayout()
        self._cover = CoverLabel()
        release_row.addWidget(self._cover, 0, Qt.AlignTop)
        self._release_label = QLabel("Add files, then look them up on MusicBrainz.")
        self._release_label.setWordWrap(True)
        self._release_label.setTextFormat(Qt.RichText)
        self._release_label.setOpenExternalLinks(True)
        release_row.addWidget(self._release_label, 1, Qt.AlignTop)
        right_layout.addLayout(release_row)
        self._tracks_table = QTableWidget(0, 4)
        self._tracks_table.setHorizontalHeaderLabels(["#", "Title", "Length", "File"])
        self._tracks_table.verticalHeader().hide()
        self._tracks_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tracks_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tracks_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tracks_table.customContextMenuRequested.connect(self._track_menu)
        self._tracks_table.setToolTip("Right-click a track to choose which file it is")
        theader = self._tracks_table.horizontalHeader()
        theader.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        theader.setSectionResizeMode(1, QHeaderView.Stretch)
        theader.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        theader.setSectionResizeMode(3, QHeaderView.Stretch)
        right_layout.addWidget(self._tracks_table)
        splitter.addWidget(right)
        splitter.setSizes([520, 620])
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        s = self._settings.settings
        self._rename_box = QCheckBox("Rename files using the naming template")
        self._rename_box.setChecked(s.tagger_rename)
        self._rename_box.toggled.connect(lambda on: self._settings.update(tagger_rename=on))
        self._move_box = QCheckBox("Move into the music folder")
        self._move_box.setChecked(s.tagger_move)
        self._move_box.toggled.connect(lambda on: self._settings.update(tagger_move=on))
        bottom.addWidget(self._rename_box)
        bottom.addWidget(self._move_box)
        bottom.addStretch(1)
        self._save_button = QPushButton("Save Tags")
        self._save_button.setStyleSheet("QPushButton { font-weight: 600; padding: 4px 18px; }")
        self._save_button.clicked.connect(self._save)
        bottom.addWidget(self._save_button)
        layout.addLayout(bottom)
        self._update_buttons()

    # --- adding files ---------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self._load([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def _add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Files", "", f"Audio files ({exts})")
        self._load(paths)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Folder")
        if folder:
            self._load([folder])

    def _load(self, paths: list[str]) -> None:
        if not paths or self._busy:
            return
        known = {f.path for f in self._files}
        self._set_busy(True)
        self.status_message.emit("Reading tags…")
        self._runner.run(
            lambda: [read_tags(p) for p in _find_audio(paths) if p not in known],
            self._on_loaded,
            lambda error: (self._set_busy(False), self.status_message.emit(f"Couldn't read files: {error}")),
        )

    def _on_loaded(self, files: list[FileTags]) -> None:
        self._set_busy(False)
        self._files.extend(files)
        self._files.sort(key=lambda f: (f.discnumber or 0, f.tracknumber or 0, str(f.path)))
        self._rematch()
        self.status_message.emit(f"Added {len(files)} file(s)")

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self._files_table.selectedIndexes()}, reverse=True)
        for row in rows:
            del self._files[row]
        self._rematch()

    def _clear(self) -> None:
        self._files = []
        self._album = None
        self._matches = {}
        self._cover.set_cover(None)
        self._release_label.setText("Add files, then look them up on MusicBrainz.")
        self._refresh_tables()

    # --- looking up -----------------------------------------------------

    def _lookup(self) -> None:
        if not self._files:
            return
        artist, album = guess_search(self._files)
        dialog = ReleaseDialog(
            self._runner,
            title="Look Up on MusicBrainz",
            search=(artist, album),
            track_count=len(self._files),
            parent=self,
        )
        if dialog.exec() and dialog.selected_release:
            release_id = dialog.selected_release["id"]
            self._set_busy(True)
            self._release_label.setText("Fetching release details…")

            def fetch():
                release = musicbrainz.get_release(release_id)
                group = (release.get("release-group") or {}).get("id", "")
                return release, musicbrainz.fetch_cover_art(release["id"], group)

            self._runner.run(fetch, self._on_release_fetched, self._on_mb_error)

    def _on_release_fetched(self, result) -> None:
        self._set_busy(False)
        release, cover = result
        self._album = album_from_release(release)
        self._cover.set_cover(cover)
        info = describe_release(release)
        details = " · ".join(x for x in (info["date"], info["country"], info["format"], info["label"]) if x)
        url = f"https://musicbrainz.org/release/{release['id']}"
        self._release_label.setText(
            f'<b>{info["title"]}</b><br>{info["artist"]}<br>{details}<br><a href="{url}">view on MusicBrainz</a>'
        )
        self._rematch()

    def _on_mb_error(self, error) -> None:
        self._set_busy(False)
        message = str(error) if isinstance(error, musicbrainz.MusicBrainzError) else f"Lookup failed: {error}"
        self._release_label.setText(message)
        self.status_message.emit(message)

    def _rematch(self) -> None:
        self._matches = match_files(self._files, self._album.tracks) if self._album else {}
        self._refresh_tables()

    def _track_menu(self, pos) -> None:
        if self._album is None:
            return
        row = self._tracks_table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        assign = menu.addMenu("This Track Is…")
        current = next((fi for fi, ti in self._matches.items() if ti == row), None)
        for fi, f in enumerate(self._files):
            action = assign.addAction(f.path.name)
            action.setCheckable(True)
            action.setChecked(fi == current)
            action.triggered.connect(lambda _=False, fi=fi: self._assign(fi, row))
        clear = menu.addAction("No File", lambda: self._assign(None, row))
        clear.setEnabled(current is not None)
        menu.exec(self._tracks_table.viewport().mapToGlobal(pos))

    def _assign(self, file_index: Optional[int], track_index: int) -> None:
        self._matches = {fi: ti for fi, ti in self._matches.items() if ti != track_index and fi != file_index}
        if file_index is not None:
            self._matches[file_index] = track_index
        self._refresh_tables()

    # --- display --------------------------------------------------------

    def _refresh_tables(self) -> None:
        self._files_table.setRowCount(len(self._files))
        for row, f in enumerate(self._files):
            values = [f.path.name, f.title, f.artist, f.album, format_duration(f.length) if f.length else ""]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setToolTip(str(f.path))
                if self._album is not None and row not in self._matches:
                    item.setForeground(UNMATCHED_COLOUR)
                self._files_table.setItem(row, col, item)

        tracks = self._album.tracks if self._album else []
        by_track = {ti: fi for fi, ti in self._matches.items()}
        multi_disc = self._album is not None and self._album.total_discs > 1
        self._tracks_table.setRowCount(len(tracks))
        for row, t in enumerate(tracks):
            fi = by_track.get(row)
            number = f"{t.disc_number}-{t.number}" if multi_disc else str(t.number)
            values = [number, t.title, format_duration(t.length_ms / 1000) if t.length_ms else "", self._files[fi].path.name if fi is not None else "—"]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if fi is None:
                    item.setForeground(QColor("gray"))
                self._tracks_table.setItem(row, col, item)
        self._update_buttons()

    # --- saving ---------------------------------------------------------

    def _save(self) -> None:
        if self._album is None or not self._matches or self._busy:
            return
        album = self._album
        cover = self._cover.cover
        s = self._settings.settings
        rename = self._rename_box.isChecked()
        move_to = Path(s.output_folder).expanduser() if self._move_box.isChecked() and s.output_folder else None
        jobs = [(fi, self._files[fi].path, album.tracks[ti]) for fi, ti in sorted(self._matches.items())]
        unmatched = len(self._files) - len(jobs)
        if unmatched:
            answer = QMessageBox.question(
                self,
                "Some Files Unmatched",
                f"{unmatched} file(s) aren't matched to a track and will be left as they are. Save the rest?",
            )
            if answer != QMessageBox.Yes:
                return

        def work():
            done, errors = {}, []
            for fi, path, track in jobs:
                try:
                    done[fi] = save_file(
                        path, album, track, cover if s.embed_cover else None,
                        template=s.template, rename=rename, move_to=move_to, id3_v23=s.id3_v23,
                    )
                except (TagError, OSError) as error:
                    errors.append(f"{path.name}: {error}")
            if s.save_cover_file and cover and done:
                folders = {p.parent for p in done.values()}
                for folder in folders:
                    target = folder / "cover.jpg"
                    if not target.exists():
                        try:
                            target.write_bytes(cover)
                        except OSError:
                            pass
            return done, errors

        self._set_busy(True)
        self.status_message.emit(f"Saving {len(jobs)} file(s)…")
        self._runner.run(work, self._on_saved, lambda error: (self._set_busy(False), self.status_message.emit(str(error))))

    def _on_saved(self, result) -> None:
        self._set_busy(False)
        done, errors = result
        for fi, new_path in done.items():
            self._files[fi] = read_tags(new_path)
        self._refresh_tables()
        if errors:
            QMessageBox.warning(self, "Some Files Weren't Saved", "\n".join(errors[:10]))
        self.status_message.emit(f"Saved tags to {len(done)} file(s)")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        self._lookup_button.setEnabled(not self._busy and bool(self._files))
        self._save_button.setEnabled(not self._busy and self._album is not None and bool(self._matches))
