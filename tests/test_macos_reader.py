import plistlib
import struct

from simple_ripper.core.cdrom.base import Drive
from simple_ripper.core.cdrom.macos import MacReader, list_drives, toc_from_plist

PLIST = {
    "Sessions": [
        {
            "First Track": 1,
            "Last Track": 2,
            "Leadout Block": 300,
            "Session Number": 1,
            "Track Array": [
                {"Point": 1, "Start Block": 0, "Data": False},
                {"Point": 2, "Start Block": 150, "Data": False},
            ],
        }
    ]
}


def _aiff(samples_be: bytes) -> bytes:
    comm = struct.pack(">hIh", 2, len(samples_be) // 4, 16) + b"\x40\x0e\xac\x44" + bytes(6)
    ssnd = struct.pack(">II", 0, 0) + samples_be
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    return b"FORM" + struct.pack(">I", len(body)) + body


def _mount(tmp_path):
    volume = tmp_path / "Volumes" / "Audio CD"
    volume.mkdir(parents=True)
    (volume / ".TOC.plist").write_bytes(plistlib.dumps(PLIST))
    (volume / "1 Audio Track.aiff").write_bytes(_aiff(struct.pack(">hhhh", 1, -2, 300, -400)))
    (volume / "2 Audio Track.aiff").write_bytes(_aiff(b""))
    return volume


def test_toc_from_plist():
    toc = toc_from_plist(PLIST)
    assert [t.start for t in toc.tracks] == [0, 150]
    assert toc.leadout == 300


def test_lists_only_audio_cd_volumes(tmp_path):
    _mount(tmp_path)
    (tmp_path / "Volumes" / "USB Stick").mkdir()
    assert [d.name for d in list_drives(tmp_path / "Volumes")] == ["Audio CD (audio CD)"]


def test_reads_aiff_as_little_endian_pcm(tmp_path):
    volume = _mount(tmp_path)
    with MacReader(Drive(id=str(volume), name="Audio CD")) as reader:
        toc = reader.read_toc()
        assert reader.track_bytes(toc, 1) == 8
        assert b"".join(reader.read_track(toc, 1)) == struct.pack("<hhhh", 1, -2, 300, -400)
