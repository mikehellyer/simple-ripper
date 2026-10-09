"""Ripping a disc: read each chosen track, encode it, tag it, file it.

Plain Python with no Qt, so the whole flow can be tested with a fake
drive. The GUI runs rip() on a background thread and passes callbacks to
hear about progress, plus a threading.Event to cancel.

Each track is encoded to a ".part" file beside its final name and only
renamed into place once it's encoded and tagged, so a cancelled or
failed rip never leaves a half-written file looking like a finished one.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from simple_ripper.core.cdrom.base import DiscReader, DriveError
from simple_ripper.core.encoder import EncodeError, Encoder, Format
from simple_ripper.core.metadata import AlbumMeta
from simple_ripper.core.naming import render
from simple_ripper.core.tagging import TagError, write_tags
from simple_ripper.core.toc import Toc

COVER_FILE_NAME = "cover.jpg"


class Cancelled(Exception):
    pass


@dataclass
class RipOptions:
    output_dir: Path
    template: str
    fmt: Format
    quality_index: int = 0
    cover: Optional[bytes] = None
    embed_cover: bool = True
    save_cover_file: bool = True
    id3_v23: bool = False
    overwrite: bool = False


@dataclass
class TrackResult:
    number: int
    path: Optional[Path] = None
    error: str = ""
    unreadable_sectors: int = 0
    skipped: bool = False  # already there, and overwrite was off


@dataclass
class RipCallbacks:
    track_started: Callable[[int], None] = lambda number: None
    # (track number, fraction of this track, fraction of the whole rip)
    progress: Callable[[int, float, float], None] = lambda number, track, overall: None
    track_finished: Callable[[TrackResult], None] = lambda result: None


def _part_path(final: Path) -> Path:
    # Keep the real extension last so ffmpeg still picks the right format.
    return final.with_name(f"{final.stem}.part{final.suffix}")


def planned_paths(album: AlbumMeta, numbers: list[int], options: RipOptions) -> dict[int, Path]:
    by_number = {t.number: t for t in album.tracks}
    return {
        n: options.output_dir / render(options.template, album, by_number[n], options.fmt.extension)
        for n in numbers
        if n in by_number
    }


def rip(
    reader: DiscReader,
    toc: Toc,
    album: AlbumMeta,
    numbers: list[int],
    options: RipOptions,
    callbacks: Optional[RipCallbacks] = None,
    cancel: Optional[threading.Event] = None,
) -> list[TrackResult]:
    """Rip the given track numbers (1-based positions in album.tracks,
    which line up with toc.audio_tracks). Returns one result per track;
    raises Cancelled if cancel is set part-way."""
    callbacks = callbacks or RipCallbacks()
    cancel = cancel or threading.Event()
    audio = toc.audio_tracks
    by_number = {t.number: t for t in album.tracks}
    paths = planned_paths(album, numbers, options)

    total_bytes = sum(reader.track_bytes(toc, audio[n - 1].number) for n in numbers) or 1
    done_bytes = 0
    results: list[TrackResult] = []
    cover_written: set[Path] = set()

    for n in numbers:
        if cancel.is_set():
            raise Cancelled()
        toc_number = audio[n - 1].number
        track = by_number[n]
        final = paths[n]
        result = TrackResult(number=n, path=final)
        expected = reader.track_bytes(toc, toc_number) or 1
        callbacks.track_started(n)

        if final.exists() and not options.overwrite:
            result.skipped = True
            done_bytes += expected
            callbacks.progress(n, 1.0, done_bytes / total_bytes)
            results.append(result)
            callbacks.track_finished(result)
            continue

        part = _part_path(final)
        encoder = None
        track_bytes = 0
        try:
            encoder = Encoder(options.fmt, options.quality_index, part)
            for chunk in reader.read_track(toc, toc_number):
                if cancel.is_set():
                    raise Cancelled()
                encoder.write(chunk)
                track_bytes += len(chunk)
                callbacks.progress(n, min(1.0, track_bytes / expected), min(1.0, (done_bytes + track_bytes) / total_bytes))
            encoder.finish()
            encoder = None
            write_tags(part, album, track, options.cover if options.embed_cover else None, id3_v23=options.id3_v23)
            part.replace(final)
            result.unreadable_sectors = reader.unreadable_sectors
            if options.save_cover_file and options.cover and final.parent not in cover_written:
                cover_path = final.parent / COVER_FILE_NAME
                if options.overwrite or not cover_path.exists():
                    cover_path.write_bytes(options.cover)
                cover_written.add(final.parent)
        except Cancelled:
            if encoder is not None:
                encoder.abort()
            part.unlink(missing_ok=True)
            raise
        except (DriveError, EncodeError, TagError, OSError) as error:
            if encoder is not None:
                encoder.abort()
            part.unlink(missing_ok=True)
            result.path = None
            result.error = str(error)

        done_bytes += expected
        callbacks.progress(n, 1.0, min(1.0, done_bytes / total_bytes))
        results.append(result)
        callbacks.track_finished(result)
    return results
