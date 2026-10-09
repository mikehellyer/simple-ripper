from conftest import release_json

from simple_ripper.core.metadata import album_from_release, blank_album, describe_release, pick_medium
from simple_ripper.core.toc import Toc, TocTrack


def test_album_fields_follow_picard():
    album = album_from_release(release_json(), medium_index=0, disc_id="disc-x")
    assert album.title == "Test Album"
    assert album.artist == "Test Artist"
    assert album.artist_sort == "Artist, Test"
    assert album.genre == "Alternative Rock"  # most votes wins
    assert album.label == "XL Recordings" and album.catalog_number == "XL 001"
    assert album.status == "official" and album.release_type == "album"
    assert album.disc_id == "disc-x"
    assert [t.title for t in album.tracks] == ["Song 1.1", "Song 1.2", "Song 1.3"]
    track = album.tracks[0]
    assert track.recording_id == "rec-1-1" and track.release_track_id == "rt-1-1"
    assert track.artist == "Test Artist"  # inherited from the release credit
    assert track.isrc == "GB0000000101"


def test_one_medium_or_all():
    release = release_json(tracks_per_medium=(2, 3), disc_ids=(("a",), ("b",)))
    assert len(album_from_release(release, medium_index=1).tracks) == 3
    every = album_from_release(release)
    assert [(t.disc_number, t.number) for t in every.tracks] == [(1, 1), (1, 2), (2, 1), (2, 2), (2, 3)]
    assert every.total_discs == 2
    assert every.tracks[2].disc_subtitle == "Disc 2 title"


def test_pick_medium_by_disc_id_then_by_layout():
    release = release_json(tracks_per_medium=(2, 3), disc_ids=(("a",), ("b",)))
    assert pick_medium(release, disc_id="b") == 1
    toc = Toc(tracks=(TocTrack(1, 0), TocTrack(2, 75 * 181), TocTrack(3, 75 * 363)), leadout=75 * 546)
    assert pick_medium(release, disc_id="unknown", toc=toc) == 1


def test_blank_album_has_a_row_per_audio_track(in_rainbows):
    album = blank_album(in_rainbows)
    assert len(album.tracks) == 10
    assert album.tracks[0].title == "Track 1"
    assert album.disc_id == in_rainbows.musicbrainz_disc_id


def test_describe_release():
    info = describe_release(release_json(tracks_per_medium=(2, 3), disc_ids=((), ())))
    assert info["format"] == "2×CD"
    assert info["tracks"] == "2+3"
    assert info["label"] == "XL Recordings XL 001"
