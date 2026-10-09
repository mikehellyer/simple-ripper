# Simple-Ripper

A simple CD ripper for Linux, macOS, and Windows, with MusicBrainz Picard-style
tagging built in. It's a sister app to Simple-Streamer and Simple-Jukebox.

Created by [Mike Hellyer](https://github.com/mikehellyer), built with
[Claude Code](https://claude.com/claude-code).

## Status

**Rip CD.** Put a CD in the drive and it's read automatically (or click **Read
Disc**, Ctrl+R). The disc's MusicBrainz disc ID is worked out from its table of
contents and looked up straight away. If the disc matches several releases
(different countries, labels, box sets), you pick yours from a list showing
date, country, format, label, catalogue number and barcode. Ordinary
single-CD releases are listed first. On a multi-disc set the right disc is
chosen by its disc ID. The cover comes from the Cover Art Archive.

Everything is editable before you rip: album artist, album, date, genre, every
track title and artist, and the cover (click it to use your own image). Renaming
the album artist also renames the tracks credited to the old name, which is
handy for filling in an unknown disc. If MusicBrainz doesn't know the disc,
**Search…** finds the album by name, and **Submit Disc ID…** opens MusicBrainz
so you can add the disc there, as Picard does.

Tick the tracks you want, choose a format and quality, and click **Rip CD**:

| Format | Qualities |
| --- | --- |
| FLAC | level 5 (standard), 8 (smallest), 0 (fastest) |
| MP3 | VBR V0 / V2, CBR 320 / 256 / 192 / 128 |
| AAC (.m4a) | 256 / 192 / 128 kbps |
| Apple Lossless (.m4a) | lossless |
| Ogg Vorbis | q6 / q8 / q4 |
| Opus | 160 / 128 / 96 kbps |
| WAV | 16-bit / 44.1 kHz |

Files are named from a template. The default is
`{albumartist}/{album}/{discprefix}{track} {title}`, which gives
`Radiohead/In Rainbows/04 Weird Fishes_Arpeggi.flac`, and you can change it in
**File ▸ Settings**. Characters that aren't allowed in file names on any OS
are replaced with `_`. The tags keep the real text. Each track is written
to a `.part` file first and only renamed once it's finished and tagged, so a
cancelled rip never leaves half a song behind. Tracks already ripped are
skipped unless you choose to replace them. Sectors that can't be read even
after retries (deep scratches) become silence, and that track's status shows
how many there were.

**Tags** are written the way Picard writes them: title, artists, album, track
and disc numbers and totals, dates, original date, genre, label, catalogue
number, barcode, ISRC, media, release country, status and type, sort names,
and the MusicBrainz IDs (recording, release track, release, artist, album
artist, release group, disc ID). These use Vorbis comments for FLAC, Ogg and
Opus, ID3v2.4 for MP3 and WAV (v2.3 is optional), and iTunes atoms for M4A.
Cover art is embedded, and a `cover.jpg` is also saved in the album folder.
Both can be switched off.

**Tagger.** This tab is Picard for files you already have. Drop in an album's
files or folder (or use **Add Files…** / **Add Folder…**), then click **Look
Up on MusicBrainz…**. The search is pre-filled from the files' tags, or from
their `Artist/Album` folders. Pick the release, and each file is matched to a
track by title, length and track number. Unmatched files show in red.
Right-click a track to choose its file by hand. **Save Tags** writes the full
tag set and cover. It can also rename the files with the naming template, and
move them into your music folder (empty folders left behind are removed).
Work through one album at a time.

### How each OS reads the disc

- **Linux** reads `/dev/sr0` etc. directly through the kernel's CD-ROM
  interface. If `cdparanoia` is installed (the `.deb` recommends it), it
  reads the audio instead, for its extra error correction. If the drive
  can't be opened, add yourself to the `cdrom` group.
- **Windows** reads the drive directly (the same way Windows Media Player
  does). Nothing extra is needed.
- **macOS** mounts an audio CD as AIFF files plus a hidden table of
  contents, and Simple-Ripper reads those. The disc shows up as soon as
  Finder mounts it.

Encoding uses an ffmpeg that's bundled inside the app, so there's nothing to
install.

### Not yet

AccurateRip verification, drive read-offset correction, AcoustID (audio
fingerprint) lookup for untagged files, and CD-Text are all possible later
additions.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
```

Run the app:

```bash
python -m simple_ripper.app
```

Run the tests (core logic only, no display or CD drive needed; the encoding
tests use the bundled ffmpeg):

```bash
pytest
```

## Project layout

- `src/simple_ripper/core/`: plain Python with no Qt dependency, covered by
  `tests/`. It contains:
  - `toc.py`: the disc layout and MusicBrainz disc ID
  - `cdrom/`: one drive backend per OS
  - `musicbrainz.py`: lookups, search and cover art
  - `metadata.py`: the Picard-style album/track model
  - `tagging.py`: the per-format tag mapping
  - `encoder.py`: ffmpeg
  - `naming.py`: file-name templates
  - `ripper.py`: the rip loop
  - `matching.py` and `tagger.py`: the Tagger
  - settings and the update checker
- `src/simple_ripper/gui/`: the PySide6 window and widgets.
- `src/simple_ripper/app.py`: the entry point.
- `packaging/`: the icon generator, PyInstaller spec, and per-OS installer
  scripts (see Releasing, below).

## Updates

On startup the app checks the repo's latest GitHub Release in the background.
If a newer version is published, it shows a banner with an **Update** button.
Nothing is downloaded or installed until you click it. If GitHub can't be
reached there's simply no banner, and the app carries on normally.

## Releasing

1. Bump `__version__` in `src/simple_ripper/__init__.py` (and `pyproject.toml`).
2. Commit, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The [release workflow](.github/workflows/release.yml) builds all three
   platforms and attaches the installers to a GitHub Release for that tag:
   - **Windows**: PyInstaller → Inno Setup → `Simple-Ripper-X.Y.Z-Windows-Setup.exe`
   - **macOS**: PyInstaller → `.app` → `Simple-Ripper-X.Y.Z-macOS.dmg`
   - **Linux**: PyInstaller → `simple-ripper_X.Y.Z_amd64.deb`

To build one platform's installer locally (it must run on that OS):

```bash
pip install -r requirements-dev.txt
pyinstaller --noconfirm packaging/pyinstaller/simple-ripper.spec
# then, per platform:
bash packaging/macos/build-dmg.sh      # macOS
bash packaging/linux/build-deb.sh      # Linux
iscc packaging/windows/installer.iss   # Windows (Inno Setup installed)
```
