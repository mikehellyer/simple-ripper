"""Pairing existing files with a release's tracks, for the Tagger.

Like Picard's matching, each file/track pair is scored on how alike the
titles are, how close the lengths are, and whether the existing track
and disc numbers agree; then the best pairs are taken greedily, so each
file gets at most one track and vice versa. Pairs that score too low are
left unmatched for the user to sort out rather than guessed at.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from simple_ripper.core.metadata import TrackMeta
from simple_ripper.core.tagging import FileTags

MIN_SCORE = 0.45
TITLE_WEIGHT = 0.55
LENGTH_WEIGHT = 0.30
NUMBER_WEIGHT = 0.15
LENGTH_TOLERANCE_SECONDS = 30


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"^\d+[\s.\-_]+", "", text)  # "03 - Title" -> "Title"
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _file_title(file: FileTags) -> str:
    return file.title or file.path.stem


def score(file: FileTags, track: TrackMeta) -> float:
    a, b = _normalise(_file_title(file)), _normalise(track.title)
    title = SequenceMatcher(None, a, b).ratio() if a and b else 0.0
    if a and b and (a in b or b in a):
        title = max(title, 0.9)

    if file.length and track.length_ms:
        diff = abs(file.length - track.length_ms / 1000)
        length = max(0.0, 1 - diff / LENGTH_TOLERANCE_SECONDS)
    else:
        length = 0.5  # unknown: neither for nor against

    if file.tracknumber:
        number = 1.0 if file.tracknumber == track.number and (not file.discnumber or file.discnumber == track.disc_number) else 0.0
    else:
        number = 0.5
    return TITLE_WEIGHT * title + LENGTH_WEIGHT * length + NUMBER_WEIGHT * number


def match_files(files: list[FileTags], tracks: list[TrackMeta]) -> dict[int, int]:
    """file index -> track index, for every pair worth keeping."""
    pairs = sorted(
        ((score(f, t), fi, ti) for fi, f in enumerate(files) for ti, t in enumerate(tracks)),
        reverse=True,
    )
    matched: dict[int, int] = {}
    used_tracks: set[int] = set()
    for value, fi, ti in pairs:
        if value < MIN_SCORE:
            break
        if fi in matched or ti in used_tracks:
            continue
        matched[fi] = ti
        used_tracks.add(ti)
    return matched


def guess_search(files: list[FileTags]) -> tuple[str, str]:
    """(artist, album) to search MusicBrainz with: the most common values
    in the files' tags, falling back to the Artist/Album folder layout."""

    def most_common(values: list[str]) -> str:
        values = [v for v in values if v.strip()]
        return Counter(values).most_common(1)[0][0] if values else ""

    album = most_common([f.album for f in files])
    artist = most_common([f.albumartist for f in files]) or most_common([f.artist for f in files])
    if files and (not album or not artist):
        folder = files[0].path.parent
        album = album or folder.name
        artist = artist or folder.parent.name
    return artist, album


def group_by_album(files: list[FileTags]) -> list[list[FileTags]]:
    """Picard's "cluster": files that look like one album, by their album
    tag (or, untagged, their folder)."""
    groups: dict[tuple[str, str], list[FileTags]] = {}
    for f in files:
        key: tuple[str, str] = ("tag", f.album.lower().strip()) if f.album.strip() else ("dir", str(f.path.parent))
        groups.setdefault(key, []).append(f)
    return list(groups.values())

