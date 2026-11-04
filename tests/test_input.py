"""Tests for text input (get_characters), key repeat and joystick edges."""

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia import joystick


@pytest.fixture(scope="module")
def engine():
	lucia.initialize()
	lucia.show_window("input test")
	yield
	lucia.quit()


def drain(engine=None):
	"""One clean frame so leftover events don't bleed into a test."""
	lucia.process_events()


def test_get_characters_from_textinput(engine):
	drain()
	pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text="ab"))
	pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text="c"))
	lucia.process_events()
	assert lucia.get_characters() == "abc"
	lucia.process_events()
	assert lucia.get_characters() == ""


def test_keydown_fallback_before_first_textinput(engine, monkeypatch):
	drain()
	monkeypatch.setattr(lucia, "_seen_textinput", False)
	pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=lucia.K_x, unicode="x"))
	pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=lucia.K_RETURN, unicode="\r"))
	lucia.process_events()
	assert lucia.get_characters() == "x"


def test_textinput_wins_over_fallback(engine, monkeypatch):
	drain()
	monkeypatch.setattr(lucia, "_seen_textinput", False)
	pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=lucia.K_x, unicode="x"))
	pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text="x"))
	lucia.process_events()
	assert lucia.get_characters() == "x", "TEXTINPUT must not double with KEYDOWN"


class Held(dict):
	"""Sparse pygame key-state stand-in: missing keys read as not held."""

	def __missing__(self, key):
		return False

	def __len__(self):
		return 512


def test_key_repeat_timing(monkeypatch):
	monkeypatch.setattr(lucia, "_repeat_next", {})
	monkeypatch.setattr(lucia, "_keys_repeating", set())
	lucia.set_key_repeat(400, 50)
	key = lucia.K_DOWN
	held = Held({key: True})
	# press frame: fires immediately
	monkeypatch.setattr(lucia, "current_key_pressed", key)
	lucia._update_key_repeat(held, 10.0)
	assert lucia.key_repeating(key)
	# held, before the initial delay: quiet
	monkeypatch.setattr(lucia, "current_key_pressed", -1)
	lucia._update_key_repeat(held, 10.3)
	assert not lucia.key_repeating(key)
	# after the delay: fires, then at the interval rate
	lucia._update_key_repeat(held, 10.41)
	assert lucia.key_repeating(key)
	lucia._update_key_repeat(held, 10.44)
	assert not lucia.key_repeating(key)
	lucia._update_key_repeat(held, 10.47)
	assert lucia.key_repeating(key)
	# released: state is forgotten
	lucia._update_key_repeat(Held(), 10.5)
	assert not lucia.key_repeating(key)
	assert lucia._repeat_next == {}
	lucia.set_key_repeat()  # restore defaults


def test_joystick_inactive_is_safe(engine):
	drain()
	assert isinstance(lucia.joystick_count(), int)
	pad = lucia.Joystick(index=99)
	assert pad.active is False
	assert pad.name == ""
	assert pad.buttons == 0
	assert pad.axes == 0
	assert pad.hats == 0
	assert pad.button_down(0) is False
	assert pad.button_pressed(0) is False
	assert pad.button_released(0) is False
	assert pad.axis(0) == 0.0
	assert pad.hat() == (0, 0)
	assert pad.rumble() is False
	assert pad.stop_rumble() is False


def test_joystick_edges_from_events():
	joystick._process([
		pygame.event.Event(pygame.JOYBUTTONDOWN, instance_id=7, button=2),
		pygame.event.Event(pygame.JOYBUTTONUP, instance_id=7, button=3),
	])
	assert (7, 2) in joystick._pressed
	assert (7, 3) in joystick._released
	joystick._process([])
	assert not joystick._pressed
	assert not joystick._released


def test_joystick_hotplug_events_are_safe(engine):
	# No real device exists headless; added/removed events must not crash.
	joystick._process([
		pygame.event.Event(pygame.JOYDEVICEADDED, device_index=0),
		pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=12345),
	])


def test_axis_deadzone_rescaling():
	pad = lucia.Joystick(index=99)

	class FakeDevice:
		def get_instance_id(self):
			return 1

		def get_axis(self, n):
			return self.value

	pad._device = FakeDevice()
	pad._device.value = 0.1
	assert pad.axis(0) == 0.0
	pad._device.value = 1.0
	assert pad.axis(0) == pytest.approx(1.0)
	pad._device.value = -1.0
	assert pad.axis(0) == pytest.approx(-1.0)
	pad._device.value = 0.575  # halfway between deadzone 0.15 and 1.0
	assert pad.axis(0) == pytest.approx(0.5)
