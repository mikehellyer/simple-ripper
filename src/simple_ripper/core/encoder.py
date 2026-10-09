"""Turning raw CD audio into FLAC, MP3, Ogg, Opus, AAC, ALAC or WAV.

Every format goes through one ffmpeg process per track: the ripper feeds
it CD-format PCM (44.1 kHz, 16-bit, stereo, little-endian) on stdin and
ffmpeg writes the file. The ffmpeg binary ships inside the app (from the
imageio-ffmpeg wheel), so nothing needs installing — a system ffmpeg on
the PATH is only a fallback.

ffmpeg is told to write no tags of its own; tagging.write_tags() does
that afterwards, so every format gets identical, Picard-style tags.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Quality:
    label: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    lossless: bool
    qualities: tuple[Quality, ...]
    default_quality: int = 0


FORMATS: tuple[Format, ...] = (
    Format(
        "flac", "FLAC", ".flac", True,
        (
            Quality("Level 5 (standard)", ("-c:a", "flac", "-compression_level", "5")),
            Quality("Level 8 (smallest, slower)", ("-c:a", "flac", "-compression_level", "8")),
            Quality("Level 0 (fastest)", ("-c:a", "flac", "-compression_level", "0")),
        ),
    ),
    Format(
        "mp3", "MP3", ".mp3", False,
        (
            Quality("VBR V0 (~245 kbps)", ("-c:a", "libmp3lame", "-q:a", "0")),
            Quality("VBR V2 (~190 kbps)", ("-c:a", "libmp3lame", "-q:a", "2")),
            Quality("CBR 320 kbps", ("-c:a", "libmp3lame", "-b:a", "320k")),
            Quality("CBR 256 kbps", ("-c:a", "libmp3lame", "-b:a", "256k")),
            Quality("CBR 192 kbps", ("-c:a", "libmp3lame", "-b:a", "192k")),
            Quality("CBR 128 kbps", ("-c:a", "libmp3lame", "-b:a", "128k")),
        ),
    ),
    Format(
        "aac", "AAC (.m4a)", ".m4a", False,
        (
            Quality("256 kbps", ("-c:a", "aac", "-b:a", "256k")),
            Quality("192 kbps", ("-c:a", "aac", "-b:a", "192k")),
            Quality("128 kbps", ("-c:a", "aac", "-b:a", "128k")),
        ),
    ),
    Format(
        "alac", "Apple Lossless (.m4a)", ".m4a", True,
        (Quality("Lossless", ("-c:a", "alac")),),
    ),
    Format(
        "ogg", "Ogg Vorbis", ".ogg", False,
        (
            Quality("Quality 6 (~192 kbps)", ("-c:a", "libvorbis", "-q:a", "6")),
            Quality("Quality 8 (~256 kbps)", ("-c:a", "libvorbis", "-q:a", "8")),
            Quality("Quality 4 (~128 kbps)", ("-c:a", "libvorbis", "-q:a", "4")),
        ),
    ),
    Format(
        "opus", "Opus", ".opus", False,
        (
            Quality("160 kbps", ("-c:a", "libopus", "-b:a", "160k")),
            Quality("128 kbps", ("-c:a", "libopus", "-b:a", "128k")),
            Quality("96 kbps", ("-c:a", "libopus", "-b:a", "96k")),
        ),
    ),
    Format(
        "wav", "WAV (uncompressed)", ".wav", True,
        (Quality("16-bit / 44.1 kHz", ("-c:a", "pcm_s16le")),),
    ),
)
FORMATS_BY_KEY = {f.key: f for f in FORMATS}
DEFAULT_FORMAT = "flac"

PCM_INPUT_ARGS = ("-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0")


class EncodeError(Exception):
    pass


def ffmpeg_path() -> Optional[str]:
    """The bundled ffmpeg if it's there, else one on the PATH."""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        path = None
    if path and os.path.exists(path):
        if sys.platform != "win32" and not os.access(path, os.X_OK):
            try:
                os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass
        return path
    return shutil.which("ffmpeg")


def _no_console_flags() -> int:
    # A windowed app on Windows otherwise flashes a console for every track.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def encode_command(ffmpeg: str, fmt: Format, quality_index: int, output: Path) -> list[str]:
    quality = fmt.qualities[quality_index if 0 <= quality_index < len(fmt.qualities) else fmt.default_quality]
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        *PCM_INPUT_ARGS,
        "-map_metadata", "-1",
        *quality.args,
        str(output),
    ]


class Encoder:
    """One track's ffmpeg process: write() PCM to it, then finish()."""

    def __init__(self, fmt: Format, quality_index: int, output: Path, ffmpeg: Optional[str] = None):
        ffmpeg = ffmpeg or ffmpeg_path()
        if not ffmpeg:
            raise EncodeError("ffmpeg wasn't found, so nothing can be encoded")
        self._output = Path(output)
        self._output.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._process = subprocess.Popen(
                encode_command(ffmpeg, fmt, quality_index, self._output),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_no_console_flags(),
            )
        except OSError as error:
            raise EncodeError(f"Couldn't start ffmpeg: {error}") from error

    def write(self, pcm: bytes) -> None:
        try:
            self._process.stdin.write(pcm)
        except (BrokenPipeError, OSError) as error:
            self._process.wait()
            raise EncodeError(self._error_text() or f"ffmpeg stopped unexpectedly: {error}") from error

    def finish(self) -> None:
        try:
            self._process.stdin.close()
        except OSError:
            pass
        code = self._process.wait()
        if code != 0:
            raise EncodeError(self._error_text() or f"ffmpeg failed (exit code {code})")

    def abort(self) -> None:
        try:
            self._process.kill()
        except OSError:
            pass
        self._process.wait()
        try:
            self._output.unlink()
        except OSError:
            pass

    def _error_text(self) -> str:
        if self._process.stderr is None:
            return ""
        try:
            return self._process.stderr.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""
