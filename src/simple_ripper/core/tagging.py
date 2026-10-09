"""Reading and writing tags, mapped the way MusicBrainz Picard maps them.

tag_values() flattens an album + track into Picard's internal tag names;
write_tags() turns those into each format's native frames — Vorbis
comments (FLAC, Ogg, Opus), ID3v2 (MP3, WAV) and iTunes atoms (M4A) —
using the same keys Picard writes, so other players and Picard itself
read them back as MusicBrainz-tagged files.

Only the tags listed here are touched: any other tag already in a file
is left alone, and a mapped tag with no value is removed rather than
left stale.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TXXX, UFID, Frames
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from simple_ripper.core.metadata import AlbumMeta, TrackMeta

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav"}

# Picard name -> Vorbis comment key(s).
VORBIS_KEYS = {
    "title": ["TITLE"],
    "artist": ["ARTIST"],
    "album": ["ALBUM"],
    "albumartist": ["ALBUMARTIST"],
    "tracknumber": ["TRACKNUMBER"],
    "totaltracks": ["TRACKTOTAL", "TOTALTRACKS"],
    "discnumber": ["DISCNUMBER"],
    "totaldiscs": ["DISCTOTAL", "TOTALDISCS"],
    "discsubtitle": ["DISCSUBTITLE"],
    "date": ["DATE"],
    "originaldate": ["ORIGINALDATE"],
    "originalyear": ["ORIGINALYEAR"],
    "genre": ["GENRE"],
    "label": ["LABEL"],
    "catalognumber": ["CATALOGNUMBER"],
    "barcode": ["BARCODE"],
    "isrc": ["ISRC"],
    "media": ["MEDIA"],
    "releasecountry": ["RELEASECOUNTRY"],
    "releasestatus": ["RELEASESTATUS"],
    "releasetype": ["RELEASETYPE"],
    "artistsort": ["ARTISTSORT"],
    "albumartistsort": ["ALBUMARTISTSORT"],
    "musicbrainz_recordingid": ["MUSICBRAINZ_TRACKID"],
    "musicbrainz_trackid": ["MUSICBRAINZ_RELEASETRACKID"],
    "musicbrainz_albumid": ["MUSICBRAINZ_ALBUMID"],
    "musicbrainz_artistid": ["MUSICBRAINZ_ARTISTID"],
    "musicbrainz_albumartistid": ["MUSICBRAINZ_ALBUMARTISTID"],
    "musicbrainz_releasegroupid": ["MUSICBRAINZ_RELEASEGROUPID"],
    "musicbrainz_discid": ["MUSICBRAINZ_DISCID"],
}

# Picard name -> ID3 text frame. (tracknumber/discnumber are written as
# "n/total" in TRCK/TPOS; the recording ID goes in a UFID frame.)
ID3_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "discsubtitle": "TSST",
    "date": "TDRC",
    "originaldate": "TDOR",
    "genre": "TCON",
    "label": "TPUB",
    "isrc": "TSRC",
    "media": "TMED",
    "artistsort": "TSOP",
    "albumartistsort": "TSO2",
}
ID3_TXXX = {
    "originalyear": "originalyear",
    "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "releasecountry": "MusicBrainz Album Release Country",
    "releasestatus": "MusicBrainz Album Status",
    "releasetype": "MusicBrainz Album Type",
    "musicbrainz_trackid": "MusicBrainz Release Track Id",
    "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_discid": "MusicBrainz Disc Id",
}
MUSICBRAINZ_UFID_OWNER = "http://musicbrainz.org"

MP4_ATOMS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "date": "\xa9day",
    "genre": "\xa9gen",
    "artistsort": "soar",
    "albumartistsort": "soaa",
}
MP4_FREEFORM = {
    "discsubtitle": "DISCSUBTITLE",
    "originaldate": "ORIGINALDATE",
    "originalyear": "ORIGINALYEAR",
    "label": "LABEL",
    "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "isrc": "ISRC",
    "media": "MEDIA",
    "releasecountry": "MusicBrainz Album Release Country",
    "releasestatus": "MusicBrainz Album Status",
    "releasetype": "MusicBrainz Album Type",
    "musicbrainz_recordingid": "MusicBrainz Track Id",
    "musicbrainz_trackid": "MusicBrainz Release Track Id",
    "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_discid": "MusicBrainz Disc Id",
}
MP4_FREEFORM_PREFIX = "----:com.apple.iTunes:"


class TagError(Exception):
    pass


def tag_values(album: AlbumMeta, track: TrackMeta) -> dict[str, list[str]]:
    """Picard tag name -> values (most have one; artist IDs can have several)."""
    total_tracks = track.total_tracks or len([t for t in album.tracks if t.disc_number == track.disc_number])
    values = {
        "title": [track.title],
        "artist": [track.artist or album.artist],
        "album": [album.title],
        "albumartist": [album.artist],
        "tracknumber": [str(track.number)],
        "totaltracks": [str(total_tracks) if total_tracks else ""],
        "discnumber": [str(track.disc_number)],
        "totaldiscs": [str(album.total_discs)],
        "discsubtitle": [track.disc_subtitle],
        "date": [album.date],
        "originaldate": [album.original_date],
        "originalyear": [album.original_date[:4]],
        "genre": [album.genre],
        "label": [album.label],
        "catalognumber": [album.catalog_number],
        "barcode": [album.barcode],
        "isrc": [track.isrc],
        "media": [track.media],
        "releasecountry": [album.country],
        "releasestatus": [album.status],
        "releasetype": [album.release_type],
        "artistsort": [track.artist_sort or (album.artist_sort if not track.artist or track.artist == album.artist else "")],
        "albumartistsort": [album.artist_sort],
        "musicbrainz_recordingid": [track.recording_id],
        "musicbrainz_trackid": [track.release_track_id],
        "musicbrainz_albumid": [album.release_id],
        "musicbrainz_artistid": list(track.artist_ids or album.artist_ids),
        "musicbrainz_albumartistid": list(album.artist_ids),
        "musicbrainz_releasegroupid": [album.release_group_id],
        "musicbrainz_discid": [album.disc_id],
    }
    return {key: [v for v in vals if v] for key, vals in values.items()}


def _image_mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _flac_picture(cover: bytes) -> Picture:
    picture = Picture()
    picture.type = 3  # front cover
    picture.mime = _image_mime(cover)
    picture.desc = "Cover"
    picture.data = cover
    return picture


def _write_vorbis(audio, values: dict[str, list[str]], cover: Optional[bytes]) -> None:
    if audio.tags is None:
        audio.add_tags()
    for name, keys in VORBIS_KEYS.items():
        for key in keys:
            if values.get(name):
                audio.tags[key] = values[name]
            elif key in audio.tags:
                del audio.tags[key]
    if cover is not None:
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            audio.add_picture(_flac_picture(cover))
        else:
            encoded = base64.b64encode(_flac_picture(cover).write()).decode("ascii")
            audio.tags["METADATA_BLOCK_PICTURE"] = [encoded]


def _write_id3(tags: ID3, values: dict[str, list[str]], cover: Optional[bytes]) -> None:
    for name, frame_id in ID3_FRAMES.items():
        tags.delall(frame_id)
        if values.get(name):
            tags.add(Frames[frame_id](encoding=3, text=values[name]))

    for frame_id, number, total in (
        ("TRCK", "tracknumber", "totaltracks"),
        ("TPOS", "discnumber", "totaldiscs"),
    ):
        tags.delall(frame_id)
        if values.get(number):
            text = values[number][0] + (f"/{values[total][0]}" if values.get(total) else "")
            tags.add(Frames[frame_id](encoding=3, text=[text]))

    for name, desc in ID3_TXXX.items():
        tags.delall(f"TXXX:{desc}")
        if values.get(name):
            tags.add(TXXX(encoding=3, desc=desc, text=values[name]))

    tags.delall(f"UFID:{MUSICBRAINZ_UFID_OWNER}")
    if values.get("musicbrainz_recordingid"):
        tags.add(UFID(owner=MUSICBRAINZ_UFID_OWNER, data=values["musicbrainz_recordingid"][0].encode("ascii")))

    if cover is not None:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=_image_mime(cover), type=3, desc="Cover", data=cover))


def _write_mp4(audio: MP4, values: dict[str, list[str]], cover: Optional[bytes]) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for name, atom in MP4_ATOMS.items():
        if values.get(name):
            tags[atom] = values[name]
        else:
            tags.pop(atom, None)

    for atom, number, total in (("trkn", "tracknumber", "totaltracks"), ("disk", "discnumber", "totaldiscs")):
        if values.get(number):
            tags[atom] = [(int(values[number][0]), int(values[total][0]) if values.get(total) else 0)]
        else:
            tags.pop(atom, None)

    for name, key in MP4_FREEFORM.items():
        atom = MP4_FREEFORM_PREFIX + key
        if values.get(name):
            tags[atom] = [MP4FreeForm(v.encode("utf-8")) for v in values[name]]
        else:
            tags.pop(atom, None)

    if cover is not None:
        fmt = MP4Cover.FORMAT_PNG if _image_mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover, imageformat=fmt)]


def write_tags(path: Path, album: AlbumMeta, track: TrackMeta, cover: Optional[bytes] = None, id3_v23: bool = False) -> None:
    """Tag one file. cover=None leaves any existing artwork alone."""
    values = tag_values(album, track)
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".flac":
            audio = FLAC(path)
            _write_vorbis(audio, values, cover)
            audio.save()
        elif suffix in (".ogg", ".oga"):
            audio = OggVorbis(path)
            _write_vorbis(audio, values, cover)
            audio.save()
        elif suffix == ".opus":
            audio = OggOpus(path)
            _write_vorbis(audio, values, cover)
            audio.save()
        elif suffix in (".m4a", ".mp4", ".aac"):
            audio = MP4(path)
            _write_mp4(audio, values, cover)
            audio.save()
        elif suffix == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            _write_id3(tags, values, cover)
            if id3_v23:
                tags.update_to_v23()
            tags.save(path, v2_version=3 if id3_v23 else 4)
        elif suffix == ".wav":
            audio = WAVE(path)
            if audio.tags is None:
                audio.add_tags()
            _write_id3(audio.tags, values, cover)
            if id3_v23:
                audio.tags.update_to_v23()
            audio.save(v2_version=3 if id3_v23 else 4)
        else:
            raise TagError(f"Don't know how to tag {suffix or 'this kind of'} files")
    except mutagen.MutagenError as error:
        raise TagError(f"Couldn't write tags to {Path(path).name}: {error}") from error


@dataclass
class FileTags:
    """What the tagger needs to know about an existing file."""

    path: Path
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    tracknumber: int = 0
    discnumber: int = 0
    length: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)


def _first_int(value: str) -> int:
    digits = ""
    for ch in value.strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def read_tags(path: Path) -> FileTags:
    """Basic tags for any supported file. Never raises — an unreadable
    file just comes back with nothing but its path."""
    result = FileTags(path=Path(path))
    try:
        audio = mutagen.File(path, easy=True)
    except (mutagen.MutagenError, OSError):
        return result
    if audio is None:
        return result
    if getattr(audio, "info", None) is not None:
        result.length = float(getattr(audio.info, "length", 0.0) or 0.0)
    tags = audio.tags or {}
    if isinstance(tags, ID3):
        # WAV has no "easy" wrapper, so its ID3 frames come back raw.
        frames = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
                  "tracknumber": "TRCK", "discnumber": "TPOS", "date": "TDRC"}
        raw = tags
        tags = {key: [str(raw[frame])] for key, frame in frames.items() if frame in raw}
        for txxx in raw.getall("TXXX:MusicBrainz Album Id"):
            tags["musicbrainz_albumid"] = [str(txxx)]

    def first(key: str) -> str:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            return ""
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value) if value else ""

    result.title = first("title")
    result.artist = first("artist")
    result.album = first("album")
    result.albumartist = first("albumartist")
    result.tracknumber = _first_int(first("tracknumber"))
    result.discnumber = _first_int(first("discnumber"))
    for key in ("musicbrainz_albumid", "date"):
        value = first(key)
        if value:
            result.extra[key] = value
    return result
