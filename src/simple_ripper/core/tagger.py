"""Saving the Tagger's work to one existing file: write the tags, then
optionally rename it (and move it into the music folder) using the same
naming template rips use — Picard's "Rename files" / "Move files"."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from simple_ripper.core.metadata import AlbumMeta, TrackMeta
from simple_ripper.core.naming import render
from simple_ripper.core.tagging import write_tags


def _unused_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def save_file(
    path: Path,
    album: AlbumMeta,
    track: TrackMeta,
    cover: Optional[bytes] = None,
    *,
    template: str,
    rename: bool = False,
    move_to: Optional[Path] = None,
    id3_v23: bool = False,
) -> Path:
    """Tag one file and put it where it belongs. Returns where it ended up.
    Raises TagError or OSError."""
    path = Path(path)
    write_tags(path, album, track, cover, id3_v23=id3_v23)
    if not rename and move_to is None:
        return path

    relative = render(template, album, track, path.suffix.lower())
    if move_to is not None:
        target = Path(move_to) / relative
    else:
        target = path.with_name(relative.name)
    if target == path:
        return path
    target = _unused_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))

    # Leave no empty folders behind after a move.
    if move_to is not None:
        folder = path.parent
        try:
            while folder != folder.parent and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        except OSError:
            pass
    return target
