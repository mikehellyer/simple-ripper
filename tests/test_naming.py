from pathlib import Path

from conftest import release_json

from simple_ripper.core.metadata import album_from_release
from simple_ripper.core.naming import DEFAULT_TEMPLATE, render, safe_component


def test_default_template():
    album = album_from_release(release_json())
    assert render(DEFAULT_TEMPLATE, album, album.tracks[1], ".flac") == Path("Test Artist/Test Album/02 Song 1.2.flac")


def test_multi_disc_prefix():
    album = album_from_release(release_json(tracks_per_medium=(1, 1), disc_ids=((), ())))
    assert render(DEFAULT_TEMPLATE, album, album.tracks[1], ".mp3").name == "2-01 Song 2.1.mp3"


def test_unsafe_characters_never_make_folders():
    album = album_from_release(release_json())
    album.tracks[0].title = 'What/If? "Live": <1>'
    path = render(DEFAULT_TEMPLATE, album, album.tracks[0], ".flac")
    assert len(path.parts) == 3
    assert path.name == "01 What_If_ _Live__ _1_.flac"


def test_safe_component_edge_cases():
    assert safe_component("AC/DC") == "AC_DC"
    assert safe_component("Etc...") == "Etc"
    assert safe_component("CON") == "_CON"
    assert safe_component("...") == "_"


def test_bad_template_falls_back_to_default():
    album = album_from_release(release_json())
    assert render("{album", album, album.tracks[0], ".ogg") == Path("Test Artist/Test Album/01 Song 1.1.ogg")


def test_custom_template_with_year():
    album = album_from_release(release_json())
    assert render("{albumartist}/{year} - {album}/{track}. {title}", album, album.tracks[0], ".m4a") == Path(
        "Test Artist/2007 - Test Album/01. Song 1.1.m4a"
    )
