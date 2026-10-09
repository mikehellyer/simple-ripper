"""Linux: talks to /dev/srN directly through the kernel's CD-ROM ioctls,
so no extra library is needed. If cdparanoia is installed it's used for
the audio itself instead, for its jitter and scratch correction — the
TOC still comes from the ioctls either way.
"""
from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

from simple_ripper.core.cdrom.base import Drive, DriveError, NoDiscError, SectorReader
from simple_ripper.core.toc import BYTES_PER_SECTOR, Toc, TocTrack

# From <linux/cdrom.h>
CDROMEJECT = 0x5309
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROMREADAUDIO = 0x530E
CDROM_DRIVE_STATUS = 0x5326
CDROM_LBA = 0x01
CDROM_LEADOUT = 0xAA
CDROM_DATA_TRACK = 0x04
CDSL_CURRENT = 0x7FFFFFFF
CDS_NO_DISC, CDS_TRAY_OPEN, CDS_DRIVE_NOT_READY, CDS_DISC_OK = 1, 2, 3, 4
MAX_FRAMES_PER_READ = 75  # the kernel's limit for CDROMREADAUDIO


class _TocHeader(ctypes.Structure):
    _fields_ = [("first", ctypes.c_uint8), ("last", ctypes.c_uint8)]


class _Msf(ctypes.Structure):
    _fields_ = [("minute", ctypes.c_uint8), ("second", ctypes.c_uint8), ("frame", ctypes.c_uint8)]


class _Addr(ctypes.Union):
    _fields_ = [("msf", _Msf), ("lba", ctypes.c_int)]


class _TocEntry(ctypes.Structure):
    _fields_ = [
        ("track", ctypes.c_uint8),
        ("adr_ctrl", ctypes.c_uint8),  # adr in the low nibble, ctrl in the high
        ("format", ctypes.c_uint8),
        ("addr", _Addr),
        ("datamode", ctypes.c_uint8),
    ]


class _ReadAudio(ctypes.Structure):
    _fields_ = [
        ("addr", _Addr),
        ("addr_format", ctypes.c_uint8),
        ("nframes", ctypes.c_int),
        ("buf", ctypes.c_void_p),
    ]


def _drive_name(device: str) -> str:
    sys_dir = Path("/sys/block") / Path(device).name / "device"
    parts = []
    for key in ("vendor", "model"):
        try:
            text = (sys_dir / key).read_text().strip()
        except OSError:
            text = ""
        if text:
            parts.append(text)
    label = " ".join(parts)
    return f"{label} ({device})" if label else device


def list_drives() -> list[Drive]:
    return [Drive(id=device, name=_drive_name(device)) for device in sorted(glob.glob("/dev/sr[0-9]*"))]


class LinuxReader(SectorReader):
    chunk_sectors = MAX_FRAMES_PER_READ

    def __init__(self, drive: Drive, use_cdparanoia: bool = True):
        super().__init__(drive)
        try:
            self._fd = os.open(drive.id, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError as error:
            raise DriveError(
                f"Not allowed to read {drive.id}. Add yourself to the 'cdrom' group "
                "(sudo usermod -aG cdrom $USER) and log in again."
            ) from error
        except OSError as error:
            raise DriveError(f"Couldn't open {drive.id}: {error.strerror}") from error
        self._cdparanoia = shutil.which("cdparanoia") if use_cdparanoia else None
        self._process: Optional[subprocess.Popen] = None

    @property
    def uses_cdparanoia(self) -> bool:
        return self._cdparanoia is not None

    def _check_disc(self) -> None:
        try:
            status = fcntl.ioctl(self._fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
        except OSError:
            return  # not every drive reports status; carry on and let the TOC read decide
        if status == CDS_TRAY_OPEN:
            raise NoDiscError("The drive's tray is open")
        if status == CDS_NO_DISC:
            raise NoDiscError("There's no disc in the drive")
        if status == CDS_DRIVE_NOT_READY:
            raise NoDiscError("The drive isn't ready yet — give it a few seconds")

    def read_toc(self) -> Toc:
        self._check_disc()
        header = _TocHeader()
        try:
            fcntl.ioctl(self._fd, CDROMREADTOCHDR, header, True)
        except OSError as error:
            raise NoDiscError("Couldn't read the disc — is it an audio CD?") from error

        def entry(track_number: int) -> _TocEntry:
            e = _TocEntry(track=track_number, format=CDROM_LBA)
            fcntl.ioctl(self._fd, CDROMREADTOCENTRY, e, True)
            return e

        try:
            tracks = []
            for n in range(header.first, header.last + 1):
                e = entry(n)
                tracks.append(TocTrack(number=n, start=e.addr.lba, is_audio=not ((e.adr_ctrl >> 4) & CDROM_DATA_TRACK)))
            leadout = entry(CDROM_LEADOUT).addr.lba
        except OSError as error:
            raise DriveError(f"Couldn't read the disc's track list: {error.strerror}") from error
        toc = Toc(tracks=tuple(tracks), leadout=leadout)
        if not toc.audio_tracks:
            raise NoDiscError("That disc has no audio tracks")
        return toc

    def read_sectors(self, lba: int, count: int) -> bytes:
        buffer = ctypes.create_string_buffer(count * BYTES_PER_SECTOR)
        request = _ReadAudio(addr_format=CDROM_LBA, nframes=count, buf=ctypes.addressof(buffer))
        request.addr.lba = lba
        fcntl.ioctl(self._fd, CDROMREADAUDIO, request, True)
        return buffer.raw

    def read_track(self, toc: Toc, number: int) -> Iterator[bytes]:
        if not self._cdparanoia:
            yield from super().read_track(toc, number)
            return
        self.unreadable_sectors = 0
        # -r: raw samples in the machine's byte order (little-endian on
        # every PC and Mac) to stdout. cdparanoia's default retry limit
        # stands, so a badly damaged disc can't hang the rip forever.
        command = [self._cdparanoia, "-q", "-r", "-d", self.drive.id, str(number), "-"]
        try:
            self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            self._cdparanoia = None
            yield from super().read_track(toc, number)
            return
        try:
            while True:
                chunk = self._process.stdout.read(BYTES_PER_SECTOR * 32)
                if not chunk:
                    break
                yield chunk
            if self._process.wait() != 0:
                detail = self._process.stderr.read().decode("utf-8", "replace").strip()
                raise DriveError(f"cdparanoia couldn't read track {number}" + (f": {detail}" if detail else ""))
        finally:
            if self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            self._process = None

    def eject(self) -> None:
        try:
            fcntl.ioctl(self._fd, CDROMEJECT, 0)
        except OSError:
            # The kernel refuses while anything else has the disc mounted
            # or open; the eject command knows how to unmount first.
            if shutil.which("eject"):
                subprocess.run(["eject", self.drive.id], check=False)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
