import math
import struct
import threading

import mutagen
import pytest
from conftest import release_json, requires_ffmpeg

from simple_ripper.core.cdrom.base import SectorReader
from simple_ripper.core.encoder import FORMATS, FORMATS_BY_KEY
from simple_ripper.core.metadata import album_from_release
from simple_ripper.core.ripper import COVER_FILE_NAME, Cancelled, RipOptions, rip
from simple_ripper.core.toc import BYTES_PER_SECTOR, Toc, TocTrack

SECTORS_PER_TRACK = 75  # one second each keeps the tests quick
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)


def _tone(sectors: int) -> bytes:
    samples = sectors * BYTES_PER_SECTOR // 4
    frames = (int(8000 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(samples))
    return b"".join(struct.pack("<hh", s, s) for s in frames)


class FakeReader(SectorReader):
    def __init__(self, toc: Toc, bad_sectors=()):
        self._toc = toc
        self._audio = _tone(toc.leadout)
        self._bad = set(bad_sectors)
        self.drive = None
        self.unreadable_sectors = 0

    def read_toc(self):
        return self._toc

    def read_sectors(self, lba, count):
        if any(s in self._bad for s in range(lba, lba + count)):
            raise OSError("read error")
        return self._audio[lba * BYTES_PER_SECTOR:(lba + count) * BYTES_PER_SECTOR]


def _three_track_toc():
    return Toc(tracks=tuple(TocTrack(n, (n - 1) * SECTORS_PER_TRACK) for n in (1, 2, 3)), leadout=3 * SECTORS_PER_TRACK)


def _options(tmp_path, key="flac", **kw):
    return RipOptions(output_dir=tmp_path, template="{albumartist}/{album}/{track} {title}", fmt=FORMATS_BY_KEY[key], cover=PNG, **kw)


@requires_ffmpeg
@pytest.mark.parametrize("fmt", [f.key for f in FORMATS])
def test_every_format_rips_and_tags(tmp_path, fmt):
    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0, disc_id="disc-1")
    results = rip(FakeReader(toc), toc, album, [2], _options(tmp_path, fmt))
    assert len(results) == 1 and not results[0].error, results[0].error
    path = results[0].path
    assert path.exists() and path.suffix == FORMATS_BY_KEY[fmt].extension
    assert not list(tmp_path.rglob("*.part*"))

    audio = mutagen.File(path, easy=fmt != "wav")
    assert 0.9 < audio.info.length < 1.2
    tags = audio.tags
    if fmt == "wav":
        assert str(tags["TIT2"]) == "Song 1.2"
        assert str(tags["TXXX:MusicBrainz Album Id"]) == "release-1"
    else:
        assert tags["title"] == ["Song 1.2"]
        assert tags["album"] == ["Test Album"]
        assert tags["musicbrainz_albumid"] == ["release-1"]
        assert tags["musicbrainz_trackid"] == ["rec-1-2"]  # Picard puts the recording ID here
    assert (path.parent / COVER_FILE_NAME).read_bytes() == PNG


@requires_ffmpeg
def test_flac_gets_the_full_picard_tag_set(tmp_path):
    from mutagen.flac import FLAC

    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0, disc_id="disc-1")
    path = rip(FakeReader(toc), toc, album, [1], _options(tmp_path))[0].path
    flac = FLAC(path)
    expected = {
        "TRACKNUMBER": "1", "TRACKTOTAL": "3", "DISCNUMBER": "1", "TOTALDISCS": "1",
        "ALBUMARTIST": "Test Artist", "ALBUMARTISTSORT": "Artist, Test", "DATE": "2007-10-10",
        "LABEL": "XL Recordings", "CATALOGNUMBER": "XL 001", "BARCODE": "1234567890123",
        "RELEASECOUNTRY": "GB", "RELEASESTATUS": "official", "RELEASETYPE": "album", "MEDIA": "CD",
        "ISRC": "GB0000000101", "MUSICBRAINZ_RELEASETRACKID": "rt-1-1", "MUSICBRAINZ_ARTISTID": "artist-1",
        "MUSICBRAINZ_RELEASEGROUPID": "rg-1", "MUSICBRAINZ_DISCID": "disc-1", "GENRE": "Alternative Rock",
    }
    for key, value in expected.items():
        assert flac[key] == [value], key
    assert flac.pictures[0].mime == "image/png"


@requires_ffmpeg
def test_mp3_id3_frames(tmp_path):
    from mutagen.id3 import ID3

    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0)
    path = rip(FakeReader(toc), toc, album, [3], _options(tmp_path, "mp3", id3_v23=True))[0].path
    tags = ID3(path)
    assert tags.version[:2] == (2, 3)
    assert str(tags["TRCK"]) == "3/3"
    assert tags["UFID:http://musicbrainz.org"].data == b"rec-1-3"
    assert str(tags["TXXX:MusicBrainz Release Group Id"]) == "rg-1"
    assert tags.getall("APIC")[0].data == PNG


@requires_ffmpeg
def test_unreadable_sectors_become_silence_and_are_counted(tmp_path):
    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0)
    result = rip(FakeReader(toc, bad_sectors={80, 81}), toc, album, [2], _options(tmp_path, "wav"))[0]
    assert not result.error
    assert result.unreadable_sectors == 2


@requires_ffmpeg
def test_existing_files_are_skipped_unless_overwriting(tmp_path):
    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0)
    first = rip(FakeReader(toc), toc, album, [1], _options(tmp_path))[0]
    again = rip(FakeReader(toc), toc, album, [1], _options(tmp_path))[0]
    assert again.skipped and again.path == first.path
    forced = rip(FakeReader(toc), toc, album, [1], _options(tmp_path, overwrite=True))[0]
    assert not forced.skipped


@requires_ffmpeg
def test_cancel_leaves_no_partial_files(tmp_path):
    toc = _three_track_toc()
    album = album_from_release(release_json(), medium_index=0)
    cancel = threading.Event()
    progress = []

    def on_progress(number, track, overall):
        progress.append(overall)
        if number == 2:
            cancel.set()

    from simple_ripper.core.ripper import RipCallbacks

    with pytest.raises(Cancelled):
        rip(FakeReader(toc), toc, album, [1, 2, 3], _options(tmp_path), RipCallbacks(progress=on_progress), cancel)
    files = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert files == ["01 Song 1.1.flac", COVER_FILE_NAME]
