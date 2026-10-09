"""The Rip CD tab: read the disc, look it up, edit, rip."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simple_ripper.core import musicbrainz
from simple_ripper.core.cdrom import Drive, DriveError, NoDiscError, list_drives, open_drive
from simple_ripper.core.diagnostics import note_current_track
from simple_ripper.core.encoder import FORMATS, FORMATS_BY_KEY, ffmpeg_path
from simple_ripper.core.metadata import AlbumMeta, album_from_release, blank_album, describe_release, pick_medium
from simple_ripper.core.ripper import Cancelled, RipCallbacks, RipOptions, TrackResult, planned_paths, rip
from simple_ripper.core.settings import SettingsStore
from simple_ripper.core.text import format_duration
from simple_ripper.core.toc import Toc
from simple_ripper.gui.cover_label import CoverLabel
from simple_ripper.gui.release_dialog import ReleaseDialog
from simple_ripper.gui.workers import BackgroundRunner, RipWorker

COL_NUMBER, COL_TITLE, COL_ARTIST, COL_LENGTH, COL_STATUS = range(5)
DRIVE_POLL_MS = 3000


class RipPage(QWidget):
    status_message = Signal(str)
    ripping_changed = Signal(bool)

    def __init__(self, settings: SettingsStore, runner: BackgroundRunner, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._runner = runner
        self._toc: Optional[Toc] = None
        self._album: Optional[AlbumMeta] = None
        self._candidates: list[dict] = []
        self._busy = False
        self._rip_thread: Optional[QThread] = None
        self._rip_worker: Optional[RipWorker] = None
        self._drive_ids: Optional[list[str]] = None  # None until the first look
        self._last_album_artist = ""

        self._build_ui()
        self._refresh_drives(read_new_disc=True)
        self._drive_timer = QTimer(self)
        self._drive_timer.timeout.connect(lambda: self._refresh_drives(read_new_disc=True))
        self._drive_timer.start(DRIVE_POLL_MS)

    # --- layout ---------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        drive_row = QHBoxLayout()
        drive_row.addWidget(QLabel("Drive:"))
        self._drive_combo = QComboBox()
        self._drive_combo.setMinimumWidth(260)
        drive_row.addWidget(self._drive_combo)
        self._read_button = QPushButton("Read Disc")
        self._read_button.clicked.connect(self.read_disc)
        drive_row.addWidget(self._read_button)
        self._eject_button = QPushButton("Eject")
        self._eject_button.clicked.connect(self._eject)
        drive_row.addWidget(self._eject_button)
        drive_row.addStretch(1)
        self._disc_label = QLabel()
        self._disc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        drive_row.addWidget(self._disc_label)
        layout.addLayout(drive_row)

        info_row = QHBoxLayout()
        self._cover = CoverLabel()
        info_row.addWidget(self._cover, 0, Qt.AlignTop)

        fields = QGridLayout()
        self._album_artist = QLineEdit()
        self._album_title = QLineEdit()
        self._date = QLineEdit()
        self._date.setPlaceholderText("YYYY or YYYY-MM-DD")
        self._genre = QLineEdit()
        self._album_artist.editingFinished.connect(self._on_album_artist_edited)
        form = QFormLayout()
        form.addRow("Album artist:", self._album_artist)
        form.addRow("Album:", self._album_title)
        small = QHBoxLayout()
        small.addWidget(self._date, 1)
        small.addWidget(QLabel("Genre:"))
        small.addWidget(self._genre, 2)
        form.addRow("Date:", small)
        fields.addLayout(form, 0, 0)

        self._release_label = QLabel("Insert a CD and click Read Disc.")
        self._release_label.setWordWrap(True)
        self._release_label.setTextFormat(Qt.RichText)
        self._release_label.setOpenExternalLinks(True)
        fields.addWidget(self._release_label, 1, 0)

        mb_row = QHBoxLayout()
        self._lookup_button = QPushButton("Look Up Disc")
        self._lookup_button.setToolTip("Look this disc up on MusicBrainz by its disc ID")
        self._lookup_button.clicked.connect(self.lookup)
        self._choose_button = QPushButton("Choose Release…")
        self._choose_button.setToolTip("Pick a different pressing or edition from the matches")
        self._choose_button.clicked.connect(self._choose_release)
        self._search_button = QPushButton("Search…")
        self._search_button.setToolTip("Search MusicBrainz by artist and album")
        self._search_button.clicked.connect(self._search)
        self._submit_button = QPushButton("Submit Disc ID…")
        self._submit_button.setToolTip("Add this disc to MusicBrainz, or attach it to the right release there")
        self._submit_button.clicked.connect(self._submit)
        for button in (self._lookup_button, self._choose_button, self._search_button, self._submit_button):
            mb_row.addWidget(button)
        mb_row.addStretch(1)
        fields.addLayout(mb_row, 2, 0)
        info_row.addLayout(fields, 1)
        layout.addLayout(info_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["#", "Title", "Artist", "Length", "Status"])
        self._table.verticalHeader().hide()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_NUMBER, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ARTIST, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_LENGTH, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        layout.addWidget(self._table, 1)

        select_row = QHBoxLayout()
        all_button = QPushButton("Select All")
        all_button.clicked.connect(lambda: self._set_all_checked(True))
        none_button = QPushButton("Select None")
        none_button.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(all_button)
        select_row.addWidget(none_button)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        for fmt in FORMATS:
            self._format_combo.addItem(fmt.label, fmt.key)
        self._format_combo.setCurrentIndex(max(0, self._format_combo.findData(self._settings.settings.format)))
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        out_row.addWidget(self._format_combo)
        self._quality_combo = QComboBox()
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        out_row.addWidget(self._quality_combo)
        out_row.addSpacing(16)
        out_row.addWidget(QLabel("Save to:"))
        self._folder_edit = QLineEdit(self._settings.settings.output_folder)
        self._folder_edit.editingFinished.connect(
            lambda: self._settings.update(output_folder=self._folder_edit.text().strip())
        )
        out_row.addWidget(self._folder_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_folder)
        out_row.addWidget(browse)
        self._rip_button = QPushButton("Rip CD")
        self._rip_button.setDefault(True)
        self._rip_button.setStyleSheet("QPushButton { font-weight: 600; padding: 4px 18px; }")
        self._rip_button.clicked.connect(self._rip_or_cancel)
        out_row.addWidget(self._rip_button)
        layout.addLayout(out_row)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._fill_qualities()
        self._update_buttons()

    # --- drives ---------------------------------------------------------

    def _refresh_drives(self, read_new_disc: bool = False) -> None:
        if self.is_ripping:
            return
        drives = list_drives()
        ids = [d.id for d in drives]
        if ids == self._drive_ids:
            return
        previous = self._drive_combo.currentData()
        added = [d for d in drives if d.id not in (self._drive_ids or [])]
        self._drive_ids = ids
        self._drive_combo.clear()
        for drive in drives:
            self._drive_combo.addItem(drive.name, drive)
        if not drives:
            self._drive_combo.addItem("No CD drive found", None)
        wanted = previous.id if isinstance(previous, Drive) else self._settings.settings.last_drive
        index = next((i for i, d in enumerate(drives) if d.id == wanted), 0)
        self._drive_combo.setCurrentIndex(index)
        self._update_buttons()
        # On a Mac a newly mounted audio CD shows up as a new "drive"; on
        # first launch any drive might already hold one. Either way, read it.
        if read_new_disc and added and not self._busy:
            self.read_disc(quiet=True)

    def _current_drive(self) -> Optional[Drive]:
        drive = self._drive_combo.currentData()
        return drive if isinstance(drive, Drive) else None

    def _eject(self) -> None:
        drive = self._current_drive()
        if drive is None:
            return

        def eject():
            with open_drive(drive, self._settings.settings.use_cdparanoia) as reader:
                reader.eject()

        self._runner.run(eject, lambda _: None, lambda error: self.status_message.emit(f"Couldn't eject: {error}"))
        self._clear_disc()

    # --- reading and looking up -----------------------------------------

    def read_disc(self, quiet: bool = False) -> None:
        drive = self._current_drive()
        if drive is None or self._busy or self.is_ripping:
            return
        self._settings.update(last_drive=drive.id)
        self._set_busy(True, "Reading disc…")

        def read():
            with open_drive(drive, self._settings.settings.use_cdparanoia) as reader:
                return reader.read_toc()

        def failed(error):
            self._set_busy(False)
            message = str(error) if isinstance(error, DriveError) else f"Couldn't read the disc: {error}"
            if quiet and isinstance(error, NoDiscError):
                self._release_label.setText("Insert a CD and click Read Disc.")
                return
            self._release_label.setText(message)
            self.status_message.emit(message)

        self._runner.run(read, self._on_toc_read, failed)

    def _on_toc_read(self, toc: Toc) -> None:
        self._set_busy(False)
        same_disc = self._toc is not None and self._toc.musicbrainz_disc_id == toc.musicbrainz_disc_id
        self._toc = toc
        tracks = len(toc.audio_tracks)
        total = sum(toc.track_seconds(t.number) for t in toc.audio_tracks)
        self._disc_label.setText(f"{tracks} tracks, {format_duration(total)}  ·  Disc ID {toc.musicbrainz_disc_id}")
        if same_disc and self._album is not None:
            return  # keep any edits already made
        self._candidates = []
        self._set_album(blank_album(toc), cover=None)
        self._release_label.setText("Not looked up yet.")
        if self._settings.settings.lookup_automatically:
            self.lookup()

    def lookup(self) -> None:
        if self._toc is None or self._busy:
            return
        toc = self._toc
        self._set_busy(True, "Looking up the disc on MusicBrainz…")
        self._runner.run(lambda: musicbrainz.lookup_disc(toc), self._on_lookup_done, self._on_mb_error)

    def _on_lookup_done(self, releases: list[dict]) -> None:
        self._set_busy(False)

        def plainness(release: dict):
            # Ordinary single-CD releases first, then the box sets and
            # multi-format bundles the same disc also turns up in.
            media = release.get("media") or []
            all_cd = all("CD" in (m.get("format") or "") for m in media)
            return (len(media) != 1, not all_cd, release.get("date") or "9999")

        self._candidates = releases = sorted(releases, key=plainness)
        if not releases:
            self._release_label.setText(
                "This disc isn't on MusicBrainz yet. Fill in the details below, "
                "use <b>Search…</b> to find the album, or <b>Submit Disc ID…</b> to add it."
            )
            self._update_buttons()
            return
        if len(releases) == 1:
            self._apply_release(releases[0]["id"])
            return
        self._choose_release()

    def _choose_release(self) -> None:
        if not self._candidates:
            return
        dialog = ReleaseDialog(
            self._runner,
            self._candidates,
            title="Choose a Release",
            intro=f"This disc matches {len(self._candidates)} releases on MusicBrainz. Pick the one you own "
            "— the country, label, catalogue number and barcode on the case help tell them apart.",
            parent=self,
        )
        if dialog.exec() and dialog.selected_release:
            self._apply_release(dialog.selected_release["id"])
        self._update_buttons()

    def _search(self) -> None:
        album = self._collect_album()
        artist = "" if not album or album.artist == "Unknown Artist" else album.artist
        title = "" if not album or album.title == "Unknown Album" else album.title
        dialog = ReleaseDialog(
            self._runner,
            title="Search MusicBrainz",
            search=(artist, title),
            track_count=len(self._toc.audio_tracks) if self._toc else None,
            parent=self,
        )
        if dialog.exec() and dialog.selected_release:
            self._apply_release(dialog.selected_release["id"])

    def _apply_release(self, release_id: str) -> None:
        toc = self._toc
        if toc is None:
            return
        self._set_busy(True, "Fetching release details…")

        def fetch():
            release = musicbrainz.get_release(release_id)
            index = pick_medium(release, toc.musicbrainz_disc_id, toc)
            cover = None
            if index is not None:
                group = (release.get("release-group") or {}).get("id", "")
                cover = musicbrainz.fetch_cover_art(release["id"], group)
            return release, index, cover

        self._runner.run(fetch, self._on_release_fetched, self._on_mb_error)

    def _on_release_fetched(self, result) -> None:
        self._set_busy(False)
        release, index, cover = result
        if index is None:
            QMessageBox.warning(
                self,
                "Release Doesn't Fit",
                f"None of the discs in “{release.get('title', '')}” has {len(self._toc.audio_tracks)} tracks, "
                "so it can't be this CD.",
            )
            return
        album = album_from_release(release, index, self._toc.musicbrainz_disc_id)
        self._set_album(album, cover)
        info = describe_release(release)
        disc = f" · disc {index + 1} of {album.total_discs}" if album.total_discs > 1 else ""
        details = " · ".join(x for x in (info["date"], info["country"], info["format"], info["label"], info["barcode"]) if x)
        url = f"https://musicbrainz.org/release/{release['id']}"
        self._release_label.setText(f'<b>{info["title"]}</b> — {info["artist"]}{disc}<br>{details} · <a href="{url}">view on MusicBrainz</a>')

    def _on_mb_error(self, error) -> None:
        self._set_busy(False)
        message = str(error) if isinstance(error, musicbrainz.MusicBrainzError) else f"MusicBrainz lookup failed: {error}"
        self._release_label.setText(message)
        self.status_message.emit(message)

    def _submit(self) -> None:
        if self._toc is not None:
            QDesktopServices.openUrl(QUrl(musicbrainz.submission_url(self._toc)))

    # --- the album form and track table ---------------------------------

    def _set_album(self, album: AlbumMeta, cover: Optional[bytes]) -> None:
        self._album = album
        self._album_artist.setText(album.artist)
        self._album_title.setText(album.title)
        self._date.setText(album.date)
        self._genre.setText(album.genre)
        self._cover.set_cover(cover, "No cover art" if album.release_id else "Click to add cover art")
        self._last_album_artist = album.artist

        self._table.setRowCount(len(album.tracks))
        for row, track in enumerate(album.tracks):
            number = QTableWidgetItem(str(track.number))
            number.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            number.setCheckState(Qt.Checked)
            self._table.setItem(row, COL_NUMBER, number)
            self._table.setItem(row, COL_TITLE, QTableWidgetItem(track.title))
            self._table.setItem(row, COL_ARTIST, QTableWidgetItem(track.artist))
            seconds = self._toc.track_seconds(self._toc.audio_tracks[row].number) if self._toc else track.length_ms / 1000
            for col, text in ((COL_LENGTH, format_duration(seconds)), (COL_STATUS, "")):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, col, item)
        self._update_buttons()

    def _on_album_artist_edited(self) -> None:
        """Tracks credited to the old album artist follow a rename —
        handy when filling in an unknown disc by hand."""
        new = self._album_artist.text().strip()
        old = self._last_album_artist
        if new == old:
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_ARTIST)
            if item and item.text() == old:
                item.setText(new)
        self._last_album_artist = new

    def _set_all_checked(self, checked: bool) -> None:
        for row in range(self._table.rowCount()):
            self._table.item(row, COL_NUMBER).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _checked_numbers(self) -> list[int]:
        return [
            row + 1
            for row in range(self._table.rowCount())
            if self._table.item(row, COL_NUMBER).checkState() == Qt.Checked
        ]

    def _collect_album(self) -> Optional[AlbumMeta]:
        """The album as currently shown, edits included."""
        if self._album is None:
            return None
        album = copy.deepcopy(self._album)
        album.artist = self._album_artist.text().strip() or "Unknown Artist"
        album.title = self._album_title.text().strip() or "Unknown Album"
        album.date = self._date.text().strip()
        album.genre = self._genre.text().strip()
        if album.artist != self._album.artist:
            album.artist_sort = ""
            album.artist_ids = []
        for row, track in enumerate(album.tracks):
            original = self._album.tracks[row]
            track.title = self._table.item(row, COL_TITLE).text().strip() or f"Track {track.number}"
            track.artist = self._table.item(row, COL_ARTIST).text().strip() or album.artist
            if track.artist != original.artist:
                track.artist_sort = ""
                track.artist_ids = []
        return album

    def _clear_disc(self) -> None:
        self._toc = None
        self._album = None
        self._candidates = []
        self._table.setRowCount(0)
        for edit in (self._album_artist, self._album_title, self._date, self._genre):
            edit.clear()
        self._cover.set_cover(None)
        self._disc_label.clear()
        self._release_label.setText("Insert a CD and click Read Disc.")
        self._update_buttons()

    # --- format and folder ----------------------------------------------

    def _fill_qualities(self) -> None:
        key = self._format_combo.currentData()
        fmt = FORMATS_BY_KEY[key]
        self._quality_combo.blockSignals(True)
        self._quality_combo.clear()
        for quality in fmt.qualities:
            self._quality_combo.addItem(quality.label)
        self._quality_combo.setCurrentIndex(self._settings.quality_for(key))
        self._quality_combo.setEnabled(len(fmt.qualities) > 1)
        self._quality_combo.blockSignals(False)

    def _on_format_changed(self) -> None:
        self._settings.update(format=self._format_combo.currentData())
        self._fill_qualities()

    def _on_quality_changed(self, index: int) -> None:
        if index >= 0:
            self._settings.set_quality(self._format_combo.currentData(), index)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save Rips To", self._folder_edit.text())
        if folder:
            self._folder_edit.setText(folder)
            self._settings.update(output_folder=folder)

    def settings_changed(self) -> None:
        self._folder_edit.setText(self._settings.settings.output_folder)

    # --- ripping --------------------------------------------------------

    @property
    def is_ripping(self) -> bool:
        return self._rip_thread is not None

    def _rip_or_cancel(self) -> None:
        if self.is_ripping:
            if self._rip_worker is not None:
                self._rip_worker.cancel_event.set()
                self._rip_button.setEnabled(False)
                self._rip_button.setText("Cancelling…")
            return
        self._start_rip()

    def _start_rip(self) -> None:
        drive, toc, album = self._current_drive(), self._toc, self._collect_album()
        numbers = self._checked_numbers()
        if drive is None or toc is None or album is None:
            return
        if not numbers:
            QMessageBox.information(self, "Nothing to Rip", "Tick at least one track to rip.")
            return
        if ffmpeg_path() is None:
            QMessageBox.critical(self, "Can't Encode", "ffmpeg, which does the encoding, couldn't be found.")
            return
        folder = self._folder_edit.text().strip()
        if not folder:
            self._browse_folder()
            folder = self._folder_edit.text().strip()
            if not folder:
                return
        s = self._settings.settings
        key = self._format_combo.currentData()
        options = RipOptions(
            output_dir=Path(folder).expanduser(),
            template=s.template,
            fmt=FORMATS_BY_KEY[key],
            quality_index=self._quality_combo.currentIndex(),
            cover=self._cover.cover,
            embed_cover=s.embed_cover,
            save_cover_file=s.save_cover_file,
            id3_v23=s.id3_v23,
        )

        existing = [p for p in planned_paths(album, numbers, options).values() if p.exists()]
        if existing:
            answer = QMessageBox.question(
                self,
                "Files Already There",
                f"{len(existing)} of these tracks have already been ripped to {existing[0].parent}.\n\n"
                "Replace them?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No,
            )
            if answer == QMessageBox.Cancel:
                return
            options.overwrite = answer == QMessageBox.Yes

        for row in range(self._table.rowCount()):
            self._table.item(row, COL_STATUS).setText("Waiting" if row + 1 in numbers else "")
        use_paranoia = s.use_cdparanoia
        eject = s.eject_when_done
        disc_id = toc.musicbrainz_disc_id

        def job(worker: RipWorker, cancel):
            with open_drive(drive, use_paranoia) as reader:
                if reader.read_toc().musicbrainz_disc_id != disc_id:
                    raise DriveError("The disc in the drive has changed — read it again first")
                results = rip(
                    reader, toc, album, numbers, options,
                    RipCallbacks(
                        track_started=worker.track_started.emit,
                        progress=worker.progress.emit,
                        track_finished=worker.track_finished.emit,
                    ),
                    cancel,
                )
                if eject and not any(r.error for r in results):
                    reader.eject()
                return results

        self._rip_worker = RipWorker(job)
        self._rip_thread = QThread()
        self._rip_worker.moveToThread(self._rip_thread)
        self._rip_thread.started.connect(self._rip_worker.run)
        self._rip_worker.track_started.connect(self._on_track_started, Qt.QueuedConnection)
        self._rip_worker.progress.connect(self._on_progress, Qt.QueuedConnection)
        self._rip_worker.track_finished.connect(self._on_track_finished, Qt.QueuedConnection)
        self._rip_worker.done.connect(self._on_rip_done, Qt.QueuedConnection)
        self._rip_worker.done.connect(self._rip_thread.quit)
        self._rip_output = options.output_dir
        self._progress.setValue(0)
        self._progress.show()
        self._rip_button.setText("Cancel")
        self._update_buttons()
        self.ripping_changed.emit(True)
        self._rip_thread.start()

    def _on_track_started(self, number: int) -> None:
        self._table.item(number - 1, COL_STATUS).setText("Ripping…")
        self._table.scrollToItem(self._table.item(number - 1, COL_NUMBER))
        note_current_track(f"track {number} of {self._album.title if self._album else '?'}")

    def _on_progress(self, number: int, track: float, overall: float) -> None:
        self._table.item(number - 1, COL_STATUS).setText(f"Ripping… {int(track * 100)}%")
        self._progress.setValue(int(overall * 100))
        self._progress.setFormat(f"Track {number} — %p%")

    def _on_track_finished(self, result: TrackResult) -> None:
        item = self._table.item(result.number - 1, COL_STATUS)
        if result.error:
            item.setText("Failed")
            item.setToolTip(result.error)
        elif result.skipped:
            item.setText("Already ripped")
        elif result.unreadable_sectors:
            item.setText(f"Done — {result.unreadable_sectors} unreadable sectors")
            item.setToolTip("Parts of this track couldn't be read (a scratch?) and were replaced with silence.")
        else:
            item.setText("Done ✓")
            item.setToolTip(str(result.path))

    def _on_rip_done(self, outcome) -> None:
        thread = self._rip_thread
        if thread is not None:
            thread.wait(2000)
            thread.deleteLater()
        if self._rip_worker is not None:
            self._rip_worker.deleteLater()
        self._rip_thread = None
        self._rip_worker = None
        self._progress.hide()
        self._rip_button.setEnabled(True)
        self._rip_button.setText("Rip CD")
        self._update_buttons()
        self.ripping_changed.emit(False)

        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_STATUS)
            if item.text() in ("Waiting",) or item.text().startswith("Ripping"):
                item.setText("Cancelled" if isinstance(outcome, Cancelled) else "")

        if isinstance(outcome, Cancelled):
            self.status_message.emit("Rip cancelled")
            return
        if isinstance(outcome, Exception):
            QMessageBox.warning(self, "Rip Stopped", str(outcome))
            return
        failed = [r for r in outcome if r.error]
        done = [r for r in outcome if not r.error]
        if failed:
            details = "\n".join(f"Track {r.number}: {r.error}" for r in failed[:5])
            QMessageBox.warning(self, "Some Tracks Failed", f"{len(failed)} track(s) couldn't be ripped:\n\n{details}")
        if done:
            folder = done[0].path.parent if done[0].path else self._rip_output
            self.status_message.emit(f"Ripped {len(done)} track(s) to {folder}")
            if self._settings.settings.eject_when_done and not failed:
                # Keep the finished track list on screen; just forget the disc.
                self._toc = None
                self._disc_label.setText("Disc ejected")
                self._update_buttons()

    def shutdown(self) -> None:
        self._drive_timer.stop()
        if self._rip_worker is not None:
            self._rip_worker.cancel_event.set()
        if self._rip_thread is not None:
            self._rip_thread.quit()
            self._rip_thread.wait(10_000)

    # --- state ----------------------------------------------------------

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if busy and message:
            self._release_label.setText(message)
        self._update_buttons()

    def _update_buttons(self) -> None:
        ripping = self.is_ripping
        idle = not self._busy and not ripping
        has_drive = self._current_drive() is not None
        has_disc = self._toc is not None
        self._drive_combo.setEnabled(idle)
        self._read_button.setEnabled(idle and has_drive)
        self._eject_button.setEnabled(idle and has_drive)
        self._lookup_button.setEnabled(idle and has_disc)
        self._choose_button.setEnabled(idle and len(self._candidates) > 1)
        self._search_button.setEnabled(idle and has_disc)
        self._submit_button.setEnabled(has_disc)
        if not ripping:
            self._rip_button.setEnabled(idle and has_disc)
        for edit in (self._album_artist, self._album_title, self._date, self._genre):
            edit.setReadOnly(ripping)
        self._format_combo.setEnabled(not ripping)
        self._quality_combo.setEnabled(not ripping and self._quality_combo.count() > 1)
        self._folder_edit.setReadOnly(ripping)
