"""Virtuelle stumme Sprachausgabe für Testläufe (lucia.output log mode)."""

import importlib
import sys


def fresh_output(monkeypatch, **env):
	for key in ("LUCIA_SPEECH", "LUCIA_SELFTEST"):
		monkeypatch.delenv(key, raising=False)
	for key, value in env.items():
		monkeypatch.setenv(key, value)
	import lucia  # noqa: F401  (lucia.output ist die Instanz, das Modul liegt in sys.modules)
	module = importlib.reload(sys.modules["lucia.output"])
	return module.output


def test_log_mode_from_env(monkeypatch, capsys):
	out = fresh_output(monkeypatch, LUCIA_SPEECH="log")
	assert out.log_mode
	assert out.backend_name == "log"
	assert out.is_active()
	assert out.speak("Hallo", True)
	assert out.output("Level 1")
	assert out.braille("nur Braille")
	assert out.silence()
	assert out.log == [("speak", "Hallo"), ("output", "Level 1"),
					   ("braille", "nur Braille"), ("stop", "")]
	printed = capsys.readouterr().out
	assert "[speech] Hallo" in printed
	assert "[speech] Level 1" in printed
	assert "[braille] nur Braille" in printed
	assert out.is_speaking is False


def test_selftest_defaults_to_log(monkeypatch):
	assert fresh_output(monkeypatch, LUCIA_SELFTEST="1").log_mode


def test_real_overrides_selftest(monkeypatch):
	assert not fresh_output(monkeypatch, LUCIA_SELFTEST="1", LUCIA_SPEECH="real").log_mode


def test_runtime_switch(monkeypatch):
	out = fresh_output(monkeypatch)
	assert not out.log_mode
	out.set_log_mode(True)
	assert out.speak("x") and out.log[-1] == ("speak", "x")
	out.set_log_mode(False)
	assert not out.log_mode
