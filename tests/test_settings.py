import json

from simple_ripper.core.settings import SettingsStore


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(format="mp3", output_folder="/rips", embed_cover=False)
    store.set_quality("mp3", 2)
    again = SettingsStore(path)
    assert again.settings.format == "mp3"
    assert again.settings.output_folder == "/rips"
    assert again.settings.embed_cover is False
    assert again.quality_for("mp3") == 2


def test_bad_values_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"format": "cassette", "embed_cover": "yes", "qualities": {"flac": 99}, "template": " "}))
    store = SettingsStore(path)
    assert store.settings.format == "flac"
    assert store.settings.embed_cover is True
    assert store.quality_for("flac") == 0
    assert store.settings.template.strip()
