import lucia.settings as settings_mod
from lucia.settings import Settings


def test_settings_roundtrip(tmp_path, monkeypatch):
	monkeypatch.setattr(
		settings_mod, "user_data_dir", lambda company, product: str(tmp_path)
	)
	settings = Settings("test", "game")
	settings["volume"] = -10
	settings["name"] = "player"
	assert settings.save()
	reloaded = Settings("test", "game")
	assert reloaded["volume"] == -10
	assert reloaded.get("name") == "player"
	assert reloaded.get("missing", "fallback") == "fallback"
	assert "volume" in reloaded
	del reloaded["volume"]
	assert "volume" not in reloaded


def test_settings_ini(tmp_path, monkeypatch):
	monkeypatch.setattr(
		settings_mod, "user_data_dir", lambda company, product: str(tmp_path)
	)
	settings = Settings("test", "game", format="ini")
	settings.set("difficulty", "hard")
	assert settings.save()
	reloaded = Settings("test", "game", format="ini")
	assert reloaded.get("difficulty") == "hard"
