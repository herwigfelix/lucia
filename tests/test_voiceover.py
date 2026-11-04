"""lucia.output prefers VoiceOver announcements on macOS (mocked: silent)."""

import importlib
import sys

import pytest

from lucia import voiceover

output_module = importlib.import_module("lucia.output")  # lucia.output is also the instance


def test_output_routes_to_voiceover_when_available(monkeypatch):
	calls = []
	monkeypatch.setattr(sys, "platform", "darwin")
	monkeypatch.delenv("LUCIA_VOICEOVER", raising=False)
	monkeypatch.setattr(voiceover, "available", lambda: True)
	monkeypatch.setattr(voiceover, "speak", lambda text, interrupt=False: calls.append(("speak", text, interrupt)) or True)
	monkeypatch.setattr(voiceover, "silence", lambda: calls.append(("silence",)) or True)
	out = output_module._PrismOutput()
	out._vosh = None
	assert out.speak("hello", interrupt=True)
	assert out.output("world")
	assert out.silence()
	assert out.backend_name == "VoiceOver" and out.is_active()
	assert calls == [("speak", "hello", True), ("speak", "world", False), ("silence",)]


def test_voiceover_can_be_turned_off(monkeypatch):
	monkeypatch.setattr(sys, "platform", "darwin")
	monkeypatch.setenv("LUCIA_VOICEOVER", "0")
	monkeypatch.setattr(voiceover, "available", lambda: True)
	out = output_module._PrismOutput()
	assert out._voiceover() is None


def test_no_voiceover_elsewhere(monkeypatch):
	monkeypatch.setattr(sys, "platform", "linux")
	out = output_module._PrismOutput()
	assert out._voiceover() is None
	assert voiceover.gui_app() in (True, False)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_native_api_loads_without_speaking():
	api = voiceover._load()
	assert api and api.notification and api.key_text and api.key_priority
	assert isinstance(voiceover.voiceover_running(0), bool)
