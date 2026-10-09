import shutil

import pytest

from simple_ripper.core.encoder import ffmpeg_path
from simple_ripper.core.toc import Toc

# Radiohead, In Rainbows (CD) — a real disc, checked against MusicBrainz.
IN_RAINBOWS_TOC = "1 10 192271 150 18009 36181 55335 79199 96355 106081 127847 152469 171136"
IN_RAINBOWS_DISC_ID = "SD2h6qACMGHinNO8OvIrlIa4RXc-"


@pytest.fixture
def in_rainbows() -> Toc:
    return Toc.from_musicbrainz_toc(IN_RAINBOWS_TOC)


requires_ffmpeg = pytest.mark.skipif(ffmpeg_path() is None, reason="no ffmpeg available")


def release_json(tracks_per_medium=(3,), disc_ids=((),)):
    """A trimmed-down MusicBrainz release, shaped like the real API's."""
    artist = [{"name": "Test Artist", "joinphrase": "", "artist": {"id": "artist-1", "sort-name": "Artist, Test"}}]
    media = []
    for m, (count, discs) in enumerate(zip(tracks_per_medium, disc_ids), start=1):
        media.append({
            "position": m,
            "format": "CD",
            "title": f"Disc {m} title" if len(tracks_per_medium) > 1 else "",
            "discs": [{"id": d} for d in discs],
            "track-count": count,
            "tracks": [
                {
                    "id": f"rt-{m}-{n}",
                    "position": n,
                    "title": f"Song {m}.{n}",
                    "length": 180000 + n * 1000,
                    "recording": {"id": f"rec-{m}-{n}", "title": f"Song {m}.{n}", "isrcs": [f"GB0000000{m}{n:02d}"]},
                }
                for n in range(1, count + 1)
            ],
        })
    return {
        "id": "release-1",
        "title": "Test Album",
        "date": "2007-10-10",
        "country": "GB",
        "status": "Official",
        "barcode": "1234567890123",
        "artist-credit": artist,
        "release-group": {
            "id": "rg-1",
            "primary-type": "Album",
            "first-release-date": "2007-10-10",
            "genres": [{"name": "art rock", "count": 3}, {"name": "alternative rock", "count": 9}],
        },
        "label-info": [{"catalog-number": "XL 001", "label": {"name": "XL Recordings"}}],
        "media": media,
    }
