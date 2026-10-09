"""Persisted user preferences: where rips go, how they're named and
encoded, and what to do with cover art."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir, user_music_dir

from simple_ripper.core.encoder import DEFAULT_FORMAT, FORMATS_BY_KEY
from simple_ripper.core.naming import DEFAULT_TEMPLATE


def _default_music_dir() -> str:
    try:
        return user_music_dir()
    except Exception:  # platformdirs can trip over odd XDG setups
        return str(Path.home() / "Music")


@dataclass
class Settings:
    output_folder: str = field(default_factory=_default_music_dir)
    template: str = DEFAULT_TEMPLATE
    format: str = DEFAULT_FORMAT
    qualities: dict[str, int] = field(default_factory=dict)  # format key -> quality index
    embed_cover: bool = True
    save_cover_file: bool = True
    use_cdparanoia: bool = True
    eject_when_done: bool = True
    id3_v23: bool = False
    lookup_automatically: bool = True
    last_drive: str = ""
    tagger_rename: bool = True
    tagger_move: bool = False


class SettingsStore:
    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or Path(user_config_dir("Simple-Ripper")) / "settings.json"
        self.settings = Settings()
        self.load()

    def quality_for(self, format_key: str) -> int:
        fmt = FORMATS_BY_KEY.get(format_key)
        index = self.settings.qualities.get(format_key)
        if fmt is None or not isinstance(index, int) or not 0 <= index < len(fmt.qualities):
            return fmt.default_quality if fmt else 0
        return index

    def update(self, **changes) -> None:
        for name, value in changes.items():
            if not hasattr(self.settings, name):
                raise AttributeError(name)
            setattr(self.settings, name, value)
        self.save()

    def set_quality(self, format_key: str, index: int) -> None:
        self.settings.qualities = {**self.settings.qualities, format_key: index}
        self.save()

    def load(self) -> None:
        try:
            data = json.loads(self._config_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        defaults = Settings()
        for f in fields(Settings):
            if f.name not in data:
                continue
            value, default = data[f.name], getattr(defaults, f.name)
            if f.name == "qualities":
                if isinstance(value, dict):
                    self.settings.qualities = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, int)}
            elif type(value) is type(default):
                setattr(self.settings, f.name, value)
        if self.settings.format not in FORMATS_BY_KEY:
            self.settings.format = DEFAULT_FORMAT
        if not self.settings.template.strip():
            self.settings.template = DEFAULT_TEMPLATE

    def save(self) -> None:
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps(asdict(self.settings), indent=2))
        except OSError:
            pass
