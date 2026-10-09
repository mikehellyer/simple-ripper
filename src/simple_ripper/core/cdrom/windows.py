"""Windows: reads the drive directly through DeviceIoControl (the same
IOCTLs Windows Media Player uses), via ctypes, so no driver or extra
library is needed. Only imported on Windows.
"""
from __future__ import annotations

import ctypes
import string
from ctypes import wintypes

from simple_ripper.core.cdrom.base import Drive, DriveError, NoDiscError, SectorReader
from simple_ripper.core.toc import BYTES_PER_SECTOR, LEAD_IN_SECTORS, Toc, TocTrack

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DRIVE_CDROM = 5

IOCTL_CDROM_READ_TOC = 0x00024000
IOCTL_CDROM_RAW_READ = 0x0002403E
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_CHECK_VERIFY = 0x002D4800
CDDA_TRACK_MODE = 2
DATA_TRACK_FLAG = 0x04
LEADOUT_TRACK = 0xAA
# Most drives refuse raw reads much bigger than 64 KB in one go.
SECTORS_PER_READ = 20


class _TrackData(ctypes.Structure):
    _fields_ = [
        ("reserved", ctypes.c_ubyte),
        ("control_adr", ctypes.c_ubyte),  # Control in the low nibble, Adr in the high (MSVC bitfield order)
        ("track_number", ctypes.c_ubyte),
        ("reserved1", ctypes.c_ubyte),
        ("address", ctypes.c_ubyte * 4),  # 0, minutes, seconds, frames
    ]


class _CdromToc(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ubyte * 2),
        ("first_track", ctypes.c_ubyte),
        ("last_track", ctypes.c_ubyte),
        ("track_data", _TrackData * 100),
    ]


class _RawReadInfo(ctypes.Structure):
    _fields_ = [
        ("disk_offset", ctypes.c_longlong),
        ("sector_count", wintypes.ULONG),
        ("track_mode", ctypes.c_int),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.DeviceIoControl.restype = wintypes.BOOL
_kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]


def _volume_label(root: str) -> str:
    buffer = ctypes.create_unicode_buffer(261)
    if _kernel32.GetVolumeInformationW(root, buffer, len(buffer), None, None, None, None, 0):
        return buffer.value
    return ""


def list_drives() -> list[Drive]:
    drives = []
    mask = _kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if _kernel32.GetDriveTypeW(root) == DRIVE_CDROM:
            label = _volume_label(root)
            drives.append(Drive(id=f"{letter}:", name=f"{letter}: {label}".strip() if label else f"{letter}: CD drive"))
    return drives


class WindowsReader(SectorReader):
    chunk_sectors = SECTORS_PER_READ

    def __init__(self, drive: Drive):
        super().__init__(drive)
        handle = _kernel32.CreateFileW(
            f"\\\\.\\{drive.id}", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None,
        )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            raise DriveError(f"Couldn't open drive {drive.id} (error {ctypes.get_last_error()})")
        self._handle = handle

    def _ioctl(self, code: int, in_buffer=None, out_buffer=None) -> int:
        returned = wintypes.DWORD(0)
        ok = _kernel32.DeviceIoControl(
            self._handle,
            code,
            ctypes.byref(in_buffer) if in_buffer is not None else None,
            ctypes.sizeof(in_buffer) if in_buffer is not None else 0,
            ctypes.byref(out_buffer) if out_buffer is not None else None,
            ctypes.sizeof(out_buffer) if out_buffer is not None else 0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "DeviceIoControl failed")
        return returned.value

    def read_toc(self) -> Toc:
        try:
            self._ioctl(IOCTL_STORAGE_CHECK_VERIFY)
        except OSError as error:
            raise NoDiscError("There's no disc in the drive") from error
        toc = _CdromToc()
        try:
            self._ioctl(IOCTL_CDROM_READ_TOC, out_buffer=toc)
        except OSError as error:
            raise NoDiscError("Couldn't read the disc — is it an audio CD?") from error

        def lba(entry: _TrackData) -> int:
            _, minutes, seconds, frames = entry.address
            return (minutes * 60 + seconds) * 75 + frames - LEAD_IN_SECTORS

        tracks = []
        count = toc.last_track - toc.first_track + 1
        for i in range(count):
            entry = toc.track_data[i]
            tracks.append(
                TocTrack(number=entry.track_number, start=lba(entry), is_audio=not (entry.control_adr & DATA_TRACK_FLAG))
            )
        leadout = toc.track_data[count]
        if leadout.track_number != LEADOUT_TRACK:
            raise DriveError("The drive returned a damaged track list")
        result = Toc(tracks=tuple(tracks), leadout=lba(leadout))
        if not result.audio_tracks:
            raise NoDiscError("That disc has no audio tracks")
        return result

    def read_sectors(self, lba: int, count: int) -> bytes:
        info = _RawReadInfo(disk_offset=lba * 2048, sector_count=count, track_mode=CDDA_TRACK_MODE)
        buffer = (ctypes.c_ubyte * (count * BYTES_PER_SECTOR))()
        returned = self._ioctl(IOCTL_CDROM_RAW_READ, in_buffer=info, out_buffer=buffer)
        if returned != count * BYTES_PER_SECTOR:
            raise OSError(0, "short read")
        return bytes(buffer)

    def eject(self) -> None:
        try:
            self._ioctl(IOCTL_STORAGE_EJECT_MEDIA)
        except OSError:
            pass

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
