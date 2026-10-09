"""Preferences: the music folder, file naming, cover art and drive options."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from simple_ripper.core.metadata import AlbumMeta, TrackMeta
from simple_ripper.core.naming import DEFAULT_TEMPLATE, FIELDS, render
from simple_ripper.core.settings import SettingsStore

_SAMPLE_ALBUM = AlbumMeta(
    title="In Rainbows", artist="Radiohead", date="2007-12-28", genre="Alternative Rock", total_discs=1,
    label="XL Recordings", catalog_number="XLCD 324",
)
_SAMPLE_TRACK = TrackMeta(number=4, title="Weird Fishes/Arpeggi", artist="Radiohead", disc_number=1)


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self._store = settings
        s = settings.settings
        layout = QVBoxLayout(self)

        files = QGroupBox("Files")
        form = QFormLayout(files)
        folder_row = QHBoxLayout()
        self._folder = QLineEdit(s.output_folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self._folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Music folder:", folder_row)

        template_row = QHBoxLayout()
        self._template = QLineEdit(s.template)
        self._template.textChanged.connect(self._update_preview)
        reset = QPushButton("Default")
        reset.clicked.connect(lambda: self._template.setText(DEFAULT_TEMPLATE))
        template_row.addWidget(self._template, 1)
        template_row.addWidget(reset)
        form.addRow("File naming:", template_row)
        self._preview = QLabel()
        self._preview.setStyleSheet("color: palette(mid);")
        form.addRow("Example:", self._preview)
        fields = QLabel("Fields: " + ", ".join(f"<code>{{{name}}}</code>" for name in FIELDS) + ". Use / for folders.")
        fields.setWordWrap(True)
        fields.setToolTip("\n".join(f"{{{name}}} — {text}" for name, text in FIELDS.items()))
        form.addRow("", fields)
        layout.addWidget(files)

        tags = QGroupBox("Tags and cover art")
        tags_layout = QVBoxLayout(tags)
        self._embed = QCheckBox("Embed cover art in each file")
        self._embed.setChecked(s.embed_cover)
        self._cover_file = QCheckBox("Save cover.jpg in the album folder")
        self._cover_file.setChecked(s.save_cover_file)
        self._id3 = QCheckBox("Write MP3 tags as ID3v2.3 (for older players and Windows Explorer)")
        self._id3.setChecked(s.id3_v23)
        for box in (self._embed, self._cover_file, self._id3):
            tags_layout.addWidget(box)
        layout.addWidget(tags)

        disc = QGroupBox("Discs")
        disc_layout = QVBoxLayout(disc)
        self._auto_lookup = QCheckBox("Look discs up on MusicBrainz as soon as they're read")
        self._auto_lookup.setChecked(s.lookup_automatically)
        self._eject = QCheckBox("Eject the disc when a rip finishes")
        self._eject.setChecked(s.eject_when_done)
        disc_layout.addWidget(self._auto_lookup)
        disc_layout.addWidget(self._eject)
        self._paranoia = QCheckBox("Use cdparanoia for error correction when it's installed")
        self._paranoia.setChecked(s.use_cdparanoia)
        if sys.platform.startswith("linux"):
            disc_layout.addWidget(self._paranoia)
        layout.addWidget(disc)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_preview()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Music Folder", self._folder.text())
        if folder:
            self._folder.setText(folder)

    def _update_preview(self) -> None:
        template = self._template.text() or DEFAULT_TEMPLATE
        self._preview.setText(str(render(template, _SAMPLE_ALBUM, _SAMPLE_TRACK, ".flac")))

    def accept(self) -> None:
        self._store.update(
            output_folder=self._folder.text().strip(),
            template=self._template.text().strip() or DEFAULT_TEMPLATE,
            embed_cover=self._embed.isChecked(),
            save_cover_file=self._cover_file.isChecked(),
            id3_v23=self._id3.isChecked(),
            lookup_automatically=self._auto_lookup.isChecked(),
            eject_when_done=self._eject.isChecked(),
            use_cdparanoia=self._paranoia.isChecked(),
        )
        super().accept()
