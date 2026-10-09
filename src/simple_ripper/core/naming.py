"""Where a ripped or tagged file goes, from a naming template.

Templates are paths with {fields} in them, e.g. the default
"{albumartist}/{album}/{discprefix}{track} {title}". "/" separates
folders on every OS. Each field's value is cleaned so it's safe in a file
name on Windows, macOS and Linux alike — a song called "What/If?" can't
create a stray folder or an unopenable file.
"""
from __future__ import annotations

import re
import string
from pathlib import Path

from simple_ripper.core.metadata import AlbumMeta, TrackMeta

DEFAULT_TEMPLATE = "{albumartist}/{album}/{discprefix}{track} {title}"

FIELDS = {
    "albumartist": "Album artist",
    "artist": "Track artist",
    "album": "Album title",
    "title": "Track title",
    "track": "Track number, two digits (01)",
    "disc": "Disc number",
    "discprefix": "\"2-\" on a multi-disc set, otherwise nothing",
    "year": "Release year",
    "date": "Release date",
    "genre": "Genre",
    "label": "Record label",
    "catalognumber": "Catalogue number",
}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_COMPONENT_LENGTH = 120


def safe_component(text: str) -> str:
    """One folder or file name, safe on every OS."""
    cleaned = _UNSAFE.sub("_", text).strip()
    cleaned = cleaned.rstrip(". ")  # Windows can't have trailing dots or spaces
    cleaned = cleaned.lstrip(".")  # no accidental hidden files
    if len(cleaned) > MAX_COMPONENT_LENGTH:
        cleaned = cleaned[:MAX_COMPONENT_LENGTH].rstrip(". ")
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned or "_"


def field_values(album: AlbumMeta, track: TrackMeta) -> dict[str, str]:
    return {
        "albumartist": album.artist,
        "artist": track.artist or album.artist,
        "album": album.title,
        "title": track.title,
        "track": f"{track.number:02d}",
        "disc": str(track.disc_number),
        "discprefix": f"{track.disc_number}-" if album.total_discs > 1 else "",
        "year": album.year,
        "date": album.date,
        "genre": album.genre,
        "label": album.label,
        "catalognumber": album.catalog_number,
    }


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render(template: str, album: AlbumMeta, track: TrackMeta, extension: str) -> Path:
    """The relative path (file name included) for one track. A template
    that won't format falls back to the default rather than failing."""
    values = _Blank({key: safe_component(value) if value else "" for key, value in field_values(album, track).items()})
    try:
        text = string.Formatter().vformat(template.strip(), (), values)
    except (ValueError, IndexError, KeyError, AttributeError):
        text = string.Formatter().vformat(DEFAULT_TEMPLATE, (), values)
    parts = [safe_component(part) for part in re.split(r"[/\\]", text) if part.strip()]
    if not parts:
        parts = [safe_component(f"{values['track']} {values['title']}")]
    parts[-1] = parts[-1] + extension
    return Path(*parts)
