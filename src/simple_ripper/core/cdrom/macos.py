"""macOS: the system mounts an audio CD as a volume under /Volumes with
one AIFF file per track ("1 Audio Track.aiff", …) and a hidden .TOC.plist
describing the disc. Reading those needs no special permissions or
libraries — the TOC comes from the plist and the audio from the AIFFs,
byte-swapped from the AIFF's big-endian samples to the little-endian PCM
the rest of the app expects.
"""
from __future__ import annotations

import plistlib
import re
import struct
import subprocess
from array import array
from pathlib import Path
from typing import Iterator, Optional

from simple_ripper.core.cdrom.base import DiscReader, Drive, DriveError, NoDiscError
from simple_ripper.core.toc import BYTES_PER_SECTOR, Toc, TocTrack

VOLUMES = Path("/Volumes")
TOC_FILE = ".TOC.plist"
CHUNK_BYTES = BYTES_PER_SECTOR * 64


def list_drives(volumes: Path = VOLUMES) -> list[Drive]:
    """Every mounted audio CD (an empty drive doesn't appear at all on a Mac)."""
    try:
        candidates = sorted(volumes.iterdir())
    except OSError:
        return []
    return [Drive(id=str(v), name=f"{v.name} (audio CD)") for v in candidates if (v / TOC_FILE).is_file()]


def toc_from_plist(data: dict) -> Toc:
    """Parse the .TOC.plist cddafs writes. Its "Start Block" values are
    already LBAs (no 150-sector lead-in)."""
    tracks = []
    leadout = None
    for session in data.get("Sessions", []):
        for entry in session.get("Track Array", []):
            number = entry.get("Point")
            start = entry.get("Start Block")
            if isinstance(number, int) and isinstance(start, int) and 1 <= number <= 99:
                tracks.append(TocTrack(number=number, start=start, is_audio=not entry.get("Data", False)))
        block = session.get("Leadout Block")
        if isinstance(block, int):
            leadout = block if leadout is None else max(leadout, block)
    if not tracks or leadout is None:
        raise NoDiscError("Couldn't make sense of the disc's track list")
    tracks.sort(key=lambda t: t.number)
    return Toc(tracks=tuple(tracks), leadout=leadout)


def _aiff_sound_data(path: Path) -> tuple[int, int]:
    """(offset, length) of the raw samples inside an AIFF file."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] != b"FORM" or header[8:12] not in (b"AIFF", b"AIFC"):
            raise DriveError(f"{path.name} isn't an AIFF file")
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                raise DriveError(f"{path.name} has no audio in it")
            chunk_id, size = chunk[:4], struct.unpack(">I", chunk[4:])[0]
            if chunk_id == b"SSND":
                offset = struct.unpack(">I", f.read(4))[0]
                f.read(4)  # block size
                return f.tell() + offset, size - 8 - offset
            f.seek(size + (size & 1), 1)


def _to_little_endian(samples: bytes) -> bytes:
    # Swapping each pair of bytes is the same operation whatever the
    # machine's own byte order — array just does it quickly.
    pcm = array("h")
    pcm.frombytes(samples)
    pcm.byteswap()
    return pcm.tobytes()


class MacReader(DiscReader):
    def __init__(self, drive: Drive):
        super().__init__(drive)
        self._volume = Path(drive.id)
        if not (self._volume / TOC_FILE).is_file():
            raise NoDiscError("That audio CD is no longer mounted")

    def read_toc(self) -> Toc:
        try:
            with open(self._volume / TOC_FILE, "rb") as f:
                data = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException) as error:
            raise DriveError(f"Couldn't read the disc's track list: {error}") from error
        toc = toc_from_plist(data)
        if not toc.audio_tracks:
            raise NoDiscError("That disc has no audio tracks")
        return toc

    def _track_file(self, number: int) -> Optional[Path]:
        for path in self._volume.iterdir():
            match = re.match(r"(\d+)\b", path.name)
            if match and int(match.group(1)) == number and path.suffix.lower() in (".aiff", ".aif"):
                return path
        return None

    def track_bytes(self, toc: Toc, number: int) -> int:
        path = self._track_file(number)
        if path is None:
            return super().track_bytes(toc, number)
        try:
            return _aiff_sound_data(path)[1]
        except (OSError, DriveError):
            return super().track_bytes(toc, number)

    def read_track(self, toc: Toc, number: int) -> Iterator[bytes]:
        self.unreadable_sectors = 0
        path = self._track_file(number)
        if path is None:
            raise DriveError(f"Track {number} isn't on the mounted disc")
        try:
            offset, length = _aiff_sound_data(path)
            with open(path, "rb") as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if len(chunk) & 1:
                        chunk = chunk[:-1]
                    yield _to_little_endian(chunk)
        except OSError as error:
            raise DriveError(f"Couldn't read track {number}: {error.strerror or error}") from error

    def eject(self) -> None:
        subprocess.run(["diskutil", "eject", str(self._volume)], check=False, capture_output=True)
