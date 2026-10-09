"""What every OS's drive backend provides, plus the shared sector-reading
loop for the backends that read raw sectors (Linux and Windows)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from simple_ripper.core.toc import BYTES_PER_SECTOR, Toc

# Re-reading a bad chunk this many times before going sector by sector,
# and each lone sector this many times before giving up and writing
# silence in its place.
CHUNK_RETRIES = 3
SECTOR_RETRIES = 5


class DriveError(Exception):
    pass


class NoDiscError(DriveError):
    pass


@dataclass(frozen=True)
class Drive:
    id: str  # device path, drive letter, or mounted volume
    name: str

    def __str__(self) -> str:
        return self.name


class DiscReader(ABC):
    """An open drive. Use as a context manager so it's always closed."""

    def __init__(self, drive: Drive):
        self.drive = drive
        # Sectors that couldn't be read at all in the most recent track,
        # and were replaced with silence.
        self.unreadable_sectors = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @abstractmethod
    def read_toc(self) -> Toc: ...

    @abstractmethod
    def read_track(self, toc: Toc, number: int) -> Iterator[bytes]:
        """The track's audio as CD-format PCM (44.1 kHz 16-bit stereo,
        little-endian), in chunks."""

    def track_bytes(self, toc: Toc, number: int) -> int:
        """Roughly how much read_track() will yield — for progress bars."""
        return toc.track_sectors(number) * BYTES_PER_SECTOR

    def close(self) -> None:
        pass

    def eject(self) -> None:
        pass


class SectorReader(DiscReader):
    """A backend that can read raw audio sectors by address."""

    chunk_sectors = 26

    @abstractmethod
    def read_sectors(self, lba: int, count: int) -> bytes:
        """Exactly count * 2352 bytes, or raise OSError."""

    def read_track(self, toc: Toc, number: int) -> Iterator[bytes]:
        self.unreadable_sectors = 0
        start, end = toc.track_range(number)
        lba = start
        while lba < end:
            count = min(self.chunk_sectors, end - lba)
            yield self._read_with_retries(lba, count)
            lba += count

    def _read_with_retries(self, lba: int, count: int) -> bytes:
        for _ in range(CHUNK_RETRIES):
            try:
                return self.read_sectors(lba, count)
            except OSError:
                continue
        # Narrow the damage down to the sectors that really won't read.
        out = bytearray()
        for sector in range(lba, lba + count):
            for _ in range(SECTOR_RETRIES):
                try:
                    out += self.read_sectors(sector, 1)
                    break
                except OSError:
                    continue
            else:
                self.unreadable_sectors += 1
                out += bytes(BYTES_PER_SECTOR)
        return bytes(out)
