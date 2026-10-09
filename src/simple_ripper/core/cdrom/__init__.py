"""Finding CD drives and opening them, whichever OS this is."""
from __future__ import annotations

import sys

from simple_ripper.core.cdrom.base import DiscReader, Drive, DriveError, NoDiscError

__all__ = ["DiscReader", "Drive", "DriveError", "NoDiscError", "list_drives", "open_drive"]


def list_drives() -> list[Drive]:
    """Never raises: a machine with no drive (or no permission to look)
    just has an empty list."""
    try:
        if sys.platform == "win32":
            from simple_ripper.core.cdrom.windows import list_drives as _list
        elif sys.platform == "darwin":
            from simple_ripper.core.cdrom.macos import list_drives as _list
        else:
            from simple_ripper.core.cdrom.linux import list_drives as _list
        return _list()
    except OSError:
        return []


def open_drive(drive: Drive, use_cdparanoia: bool = True) -> DiscReader:
    if sys.platform == "win32":
        from simple_ripper.core.cdrom.windows import WindowsReader

        return WindowsReader(drive)
    if sys.platform == "darwin":
        from simple_ripper.core.cdrom.macos import MacReader

        return MacReader(drive)
    from simple_ripper.core.cdrom.linux import LinuxReader

    return LinuxReader(drive, use_cdparanoia=use_cdparanoia)
