import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")


def test_struct_layouts_match_linux_cdrom_h():
    from simple_ripper.core.cdrom import linux

    assert ctypes.sizeof(linux._TocHeader) == 2
    assert ctypes.sizeof(linux._TocEntry) == 12
    assert linux._TocEntry.addr.offset == 4
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(linux._ReadAudio) == 24
        assert linux._ReadAudio.nframes.offset == 8
        assert linux._ReadAudio.buf.offset == 16


def test_no_drives_is_just_an_empty_list():
    from simple_ripper.core.cdrom import list_drives

    assert isinstance(list_drives(), list)
