from pathlib import Path

from conftest import release_json

from simple_ripper.core.matching import group_by_album, guess_search, match_files
from simple_ripper.core.metadata import album_from_release
from simple_ripper.core.tagging import FileTags


def test_matches_by_title_and_length_regardless_of_order():
    tracks = album_from_release(release_json()).tracks
    files = [
        FileTags(path=Path("/m/x/c.mp3"), title="song 1.3", length=183),
        FileTags(path=Path("/m/x/a.mp3"), title="Song 1.1", length=181),
        FileTags(path=Path("/m/x/b.mp3"), title="", length=182, tracknumber=2),
    ]
    files[2].path = Path("/m/x/02 - Song 1.2.mp3")
    assert match_files(files, tracks) == {0: 2, 1: 0, 2: 1}


def test_unrelated_file_stays_unmatched():
    tracks = album_from_release(release_json()).tracks
    files = [FileTags(path=Path("/m/zzz.mp3"), title="Completely Different Thing", length=600, tracknumber=9)]
    assert match_files(files, tracks) == {}


def test_guess_search_from_tags_then_folders():
    tagged = [FileTags(path=Path("/m/A/B/1.mp3"), album="Album", albumartist="Band")]
    assert guess_search(tagged) == ("Band", "Album")
    untagged = [FileTags(path=Path("/music/Some Band/Some Album/1.mp3"))]
    assert guess_search(untagged) == ("Some Band", "Some Album")


def test_group_by_album():
    files = [
        FileTags(path=Path("/a/1.mp3"), album="One"),
        FileTags(path=Path("/b/2.mp3"), album="one"),
        FileTags(path=Path("/c/3.mp3")),
    ]
    assert [len(g) for g in group_by_album(files)] == [2, 1]
