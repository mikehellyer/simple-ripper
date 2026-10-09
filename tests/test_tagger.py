import mutagen
from conftest import release_json, requires_ffmpeg

from simple_ripper.core.encoder import FORMATS_BY_KEY, Encoder
from simple_ripper.core.metadata import album_from_release
from simple_ripper.core.naming import DEFAULT_TEMPLATE
from simple_ripper.core.tagger import save_file
from simple_ripper.core.tagging import read_tags


def _silent_file(path):
    encoder = Encoder(FORMATS_BY_KEY["mp3"], 0, path)
    encoder.write(bytes(44100 * 4))
    encoder.finish()
    return path


@requires_ffmpeg
def test_tags_and_renames_in_place(tmp_path):
    path = _silent_file(tmp_path / "Old" / "track.mp3")
    album = album_from_release(release_json())
    new = save_file(path, album, album.tracks[0], template=DEFAULT_TEMPLATE, rename=True)
    assert new == tmp_path / "Old" / "01 Song 1.1.mp3"
    tags = read_tags(new)
    assert (tags.title, tags.albumartist, tags.tracknumber) == ("Song 1.1", "Test Artist", 1)
    assert mutagen.File(new, easy=True)["musicbrainz_albumid"] == ["release-1"]


@requires_ffmpeg
def test_moves_into_library_and_tidies_up(tmp_path):
    path = _silent_file(tmp_path / "incoming" / "x" / "a.mp3")
    album = album_from_release(release_json())
    library = tmp_path / "Music"
    new = save_file(path, album, album.tracks[1], template=DEFAULT_TEMPLATE, move_to=library)
    assert new == library / "Test Artist" / "Test Album" / "02 Song 1.2.mp3"
    assert not (tmp_path / "incoming").exists()


@requires_ffmpeg
def test_never_overwrites_another_file(tmp_path):
    album = album_from_release(release_json())
    a = save_file(_silent_file(tmp_path / "a.mp3"), album, album.tracks[0], template=DEFAULT_TEMPLATE, rename=True)
    b = save_file(_silent_file(tmp_path / "b.mp3"), album, album.tracks[0], template=DEFAULT_TEMPLATE, rename=True)
    assert a.exists() and b.exists() and a != b
