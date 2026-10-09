"""A CD's table of contents, and the MusicBrainz disc ID worked out from it.

Every drive backend boils a disc down to the same thing: where each track
starts and where the disc ends, in sectors ("frames", 75 per second).
Everything else — which sectors make up a track, how long it plays, the
disc ID MusicBrainz looks it up by — comes from that, so it lives here
where it can be tested without a drive.

Addresses are LBAs: sector 0 is the start of the first track's audio.
MusicBrainz offsets count the 2-second (150-sector) lead-in as well, so
they're LBA + 150.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

SECTORS_PER_SECOND = 75
BYTES_PER_SECTOR = 2352  # 588 stereo 16-bit samples at 44.1 kHz
LEAD_IN_SECTORS = 150
# An Enhanced CD keeps its data track in a second session; the gap
# between sessions (lead-out + lead-in + pregap) is this many sectors.
SESSION_GAP_SECTORS = 11400


@dataclass(frozen=True)
class TocTrack:
    number: int
    start: int  # LBA
    is_audio: bool = True


@dataclass(frozen=True)
class Toc:
    tracks: tuple[TocTrack, ...]
    leadout: int  # LBA just past the last sector on the disc

    @property
    def audio_tracks(self) -> list[TocTrack]:
        return [t for t in self.tracks if t.is_audio]

    def _audio_end(self) -> int:
        """Where the audio session ends. On an Enhanced CD (audio, then a
        data track in a later session) that's well before the lead-out."""
        last = self.tracks[-1]
        if not last.is_audio and len(self.tracks) > 1 and self.tracks[-2].is_audio:
            return last.start - SESSION_GAP_SECTORS
        return self.leadout

    def track_range(self, number: int) -> tuple[int, int]:
        """(first sector, sector after the last) of a track. Any gap before
        the next track is counted as part of this one, which is what most
        rippers do by default."""
        index = next(i for i, t in enumerate(self.tracks) if t.number == number)
        start = self.tracks[index].start
        if index + 1 < len(self.tracks):
            nxt = self.tracks[index + 1]
            end = nxt.start if nxt.is_audio else self._audio_end()
        else:
            end = self.leadout
        return start, end

    def track_sectors(self, number: int) -> int:
        start, end = self.track_range(number)
        return end - start

    def track_seconds(self, number: int) -> float:
        return self.track_sectors(number) / SECTORS_PER_SECOND

    # --- MusicBrainz ----------------------------------------------------

    def _musicbrainz_layout(self) -> tuple[int, int, int, list[int]]:
        """(first track, last track, lead-out offset, track offsets) as
        MusicBrainz defines them: audio tracks only, with a trailing data
        track's session gap taken off the lead-out."""
        audio = self.audio_tracks
        first, last = audio[0].number, audio[-1].number
        leadout = self._audio_end()
        offsets = [t.start + LEAD_IN_SECTORS for t in audio]
        return first, last, leadout + LEAD_IN_SECTORS, offsets

    @property
    def musicbrainz_disc_id(self) -> str:
        first, last, leadout, offsets = self._musicbrainz_layout()
        text = f"{first:02X}{last:02X}{leadout:08X}"
        by_number = {t.number: off for t, off in zip(self.audio_tracks, offsets)}
        for n in range(1, 100):
            text += f"{by_number.get(n, 0):08X}"
        digest = hashlib.sha1(text.encode("ascii")).digest()
        return base64.b64encode(digest).decode("ascii").translate(str.maketrans("+/=", "._-"))

    @property
    def musicbrainz_toc(self) -> str:
        """The "toc" lookup parameter, used for a fuzzy match when the
        disc ID itself isn't in the database yet."""
        first, last, leadout, offsets = self._musicbrainz_layout()
        return " ".join(str(n) for n in [first, last, leadout, *offsets])

    @classmethod
    def from_musicbrainz_toc(cls, toc: str) -> "Toc":
        """The reverse of musicbrainz_toc — handy for tests."""
        numbers = [int(n) for n in toc.split()]
        first, _last, leadout, offsets = numbers[0], numbers[1], numbers[2], numbers[3:]
        tracks = tuple(
            TocTrack(number=first + i, start=off - LEAD_IN_SECTORS) for i, off in enumerate(offsets)
        )
        return cls(tracks=tracks, leadout=leadout - LEAD_IN_SECTORS)
