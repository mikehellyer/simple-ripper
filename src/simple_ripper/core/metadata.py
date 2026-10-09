"""Album and track metadata, in the same terms Picard uses.

AlbumMeta/TrackMeta are what the GUI edits and what the tag writer
writes. They're built from a MusicBrainz release by album_from_release()
— or left as blanks for a disc MusicBrainz doesn't know — and the field
names follow Picard's internal tag names (albumartist, catalognumber,
musicbrainz_recordingid, …) so tags come out the way Picard would write
them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from simple_ripper.core.toc import Toc

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


@dataclass
class TrackMeta:
    number: int  # position on its disc
    title: str = ""
    artist: str = ""
    artist_sort: str = ""
    artist_ids: list[str] = field(default_factory=list)
    recording_id: str = ""
    release_track_id: str = ""
    isrc: str = ""
    length_ms: int = 0
    disc_number: int = 1
    total_tracks: int = 0  # on this track's disc
    media: str = ""
    disc_subtitle: str = ""


@dataclass
class AlbumMeta:
    title: str = ""
    artist: str = ""
    artist_sort: str = ""
    artist_ids: list[str] = field(default_factory=list)
    date: str = ""
    original_date: str = ""
    genre: str = ""
    label: str = ""
    catalog_number: str = ""
    barcode: str = ""
    country: str = ""
    status: str = ""
    release_type: str = ""
    release_id: str = ""
    release_group_id: str = ""
    disc_id: str = ""
    total_discs: int = 1
    tracks: list[TrackMeta] = field(default_factory=list)

    @property
    def year(self) -> str:
        return self.date[:4]

    @property
    def is_various_artists(self) -> bool:
        return any(t.artist and t.artist != self.artist for t in self.tracks)


def _credit_name(credits) -> str:
    if not isinstance(credits, list):
        return ""
    return "".join(f"{c.get('name', '')}{c.get('joinphrase', '')}" for c in credits if isinstance(c, dict))


def _credit_sort_name(credits) -> str:
    if not isinstance(credits, list):
        return ""
    return "".join(
        f"{(c.get('artist') or {}).get('sort-name') or c.get('name', '')}{c.get('joinphrase', '')}"
        for c in credits
        if isinstance(c, dict)
    )


def _credit_ids(credits) -> list[str]:
    if not isinstance(credits, list):
        return []
    return [c["artist"]["id"] for c in credits if isinstance(c, dict) and (c.get("artist") or {}).get("id")]


def _top_genre(*sources) -> str:
    """The most-voted genre — Picard's default when genres are switched on
    is the release group's, so that's checked first."""
    for genres in sources:
        if isinstance(genres, list) and genres:
            best = max((g for g in genres if isinstance(g, dict) and g.get("name")), key=lambda g: g.get("count", 0), default=None)
            if best:
                return best["name"].title()
    return ""


def _medium_matches_toc(medium: dict, toc: Toc) -> bool:
    tracks = medium.get("tracks") or []
    if len(tracks) != len(toc.audio_tracks):
        return False
    # Within 3 seconds per track allows for pregap differences between pressings.
    for track, toc_track in zip(tracks, toc.audio_tracks):
        length = track.get("length") or (track.get("recording") or {}).get("length")
        if length and abs(length / 1000 - toc.track_seconds(toc_track.number)) > 3:
            return False
    return True


def pick_medium(release: dict, disc_id: str = "", toc: Optional[Toc] = None) -> Optional[int]:
    """Index into release['media'] of the disc that's in the drive: the
    one carrying its disc ID, else the first whose track count and lengths
    fit the TOC, else the first with the right number of tracks."""
    media = release.get("media") or []
    if disc_id:
        for i, medium in enumerate(media):
            if any(d.get("id") == disc_id for d in medium.get("discs") or []):
                return i
    if toc is not None:
        for i, medium in enumerate(media):
            if _medium_matches_toc(medium, toc):
                return i
        for i, medium in enumerate(media):
            if len(medium.get("tracks") or []) == len(toc.audio_tracks):
                return i
        return None
    return 0 if media else None


def album_from_release(release: dict, medium_index: Optional[int] = None, disc_id: str = "") -> AlbumMeta:
    """Metadata for a release. With medium_index, only that disc's tracks
    (ripping one CD of a set); without, every disc's (tagging files)."""
    group = release.get("release-group") or {}
    labels = [li for li in release.get("label-info") or [] if isinstance(li, dict)]
    label = next(((li.get("label") or {}).get("name", "") for li in labels if li.get("label")), "")
    catalog = next((li.get("catalog-number") or "" for li in labels if li.get("catalog-number")), "")
    media = release.get("media") or []

    album = AlbumMeta(
        title=release.get("title", ""),
        artist=_credit_name(release.get("artist-credit")),
        artist_sort=_credit_sort_name(release.get("artist-credit")),
        artist_ids=_credit_ids(release.get("artist-credit")),
        date=release.get("date", "") or "",
        original_date=group.get("first-release-date", "") or "",
        genre=_top_genre(group.get("genres"), release.get("genres")),
        label=label,
        catalog_number=catalog,
        barcode=release.get("barcode") or "",
        country=release.get("country") or "",
        status=(release.get("status") or "").lower(),
        release_type=(group.get("primary-type") or "").lower(),
        release_id=release.get("id", ""),
        release_group_id=group.get("id", ""),
        disc_id=disc_id,
        total_discs=len(media) or 1,
    )

    chosen = [media[medium_index]] if medium_index is not None and medium_index < len(media) else media
    for medium in chosen:
        tracks = medium.get("tracks") or []
        for track in tracks:
            recording = track.get("recording") or {}
            credits = track.get("artist-credit") or recording.get("artist-credit") or release.get("artist-credit")
            isrcs = recording.get("isrcs") or []
            album.tracks.append(
                TrackMeta(
                    number=track.get("position") or len(album.tracks) + 1,
                    title=track.get("title") or recording.get("title", ""),
                    artist=_credit_name(credits),
                    artist_sort=_credit_sort_name(credits),
                    artist_ids=_credit_ids(credits),
                    recording_id=recording.get("id", ""),
                    release_track_id=track.get("id", ""),
                    isrc=isrcs[0] if isrcs else "",
                    length_ms=track.get("length") or recording.get("length") or 0,
                    disc_number=medium.get("position") or 1,
                    total_tracks=len(tracks),
                    media=medium.get("format") or "",
                    disc_subtitle=medium.get("title") or "",
                )
            )
    return album


def blank_album(toc: Toc) -> AlbumMeta:
    """Placeholder metadata for a disc MusicBrainz doesn't know, for the
    user to fill in by hand."""
    tracks = toc.audio_tracks
    return AlbumMeta(
        title=UNKNOWN_ALBUM,
        artist=UNKNOWN_ARTIST,
        disc_id=toc.musicbrainz_disc_id,
        tracks=[
            TrackMeta(
                number=i + 1,
                title=f"Track {i + 1}",
                artist=UNKNOWN_ARTIST,
                length_ms=int(toc.track_seconds(t.number) * 1000),
                total_tracks=len(tracks),
                media="CD",
            )
            for i, t in enumerate(tracks)
        ],
    )


def describe_release(release: dict) -> dict[str, str]:
    """The columns shown when choosing between candidate releases."""
    media = release.get("media") or []
    formats = []
    for medium in media:
        fmt = medium.get("format") or "?"
        if fmt not in formats:
            formats.append(fmt)
    track_counts = "+".join(str(m.get("track-count") or len(m.get("tracks") or [])) for m in media)
    labels = [li for li in release.get("label-info") or [] if isinstance(li, dict)]
    label = next(((li.get("label") or {}).get("name", "") for li in labels if li.get("label")), "")
    catalog = next((li.get("catalog-number") or "" for li in labels if li.get("catalog-number")), "")
    disambiguation = release.get("disambiguation") or ""
    title = release.get("title", "")
    return {
        "title": f"{title} ({disambiguation})" if disambiguation else title,
        "artist": _credit_name(release.get("artist-credit")),
        "date": release.get("date", "") or "",
        "country": release.get("country", "") or "",
        "label": " ".join(x for x in (label, catalog) if x),
        "format": f"{len(media)}×{formats[0]}" if len(media) > 1 and len(formats) == 1 else " + ".join(formats),
        "tracks": track_counts or str(release.get("track-count", "")),
        "barcode": release.get("barcode") or "",
    }
