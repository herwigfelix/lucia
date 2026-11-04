# Copyright (C) 2018-2026  LuciaSoftware and it's contributors.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see https://github.com/LuciaSoftware/lucia/blob/master/LICENSE.

"""The main Lucia module.

The functions here are responsible for initializing and quitting lucia,
showing the game window, handling global events and so on.
In addition, this part of lucia also contains most keyboard functions.
"""

import os
import platform
import sys
import time

__version__ = "2.0.0"

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"

import pygame
from pygame.locals import *

# Uppercase aliases for the letter keys: pygame only defines K_a..K_z,
# but BGT veterans (and AI code generators) habitually write K_A..K_Z.
# Both now work.
for _letter in "abcdefghijklmnopqrstuvwxyz":
	globals()["K_" + _letter.upper()] = globals()["K_" + _letter]
del _letter


def key_code(name):
	"""Look up a key constant by name, case-insensitively.

	key_code("a"), key_code("A"), key_code("SPACE") and key_code("k_space")
	all work. Returns None for unknown names."""
	name = str(name)
	if name.lower().startswith("k_"):
		name = name[2:]
	for candidate in ("K_" + name, "K_" + name.lower(), "K_" + name.upper()):
		value = globals().get(candidate)
		if isinstance(value, int):
			return value
	return None

# import subpackages..
from . import audio, ui, utils, network

# import submodules
from .output import *
from .packfile import *
from .settings import Settings
from .map import Door, Map, MapWalker, SoundSource, Tile, Zone
from .pathfinder import Pathfinder
from . import joystick as _joystick_module
from .joystick import Joystick, joystick_count
from .system import (
	clipboard_get_text,
	clipboard_set_text,
	set_window_title,
	get_window_title,
	is_fullscreen,
	set_fullscreen,
	toggle_fullscreen,
	minimize_window,
	restore_window,
	hide_window,
	focus_window,
	is_window_active,
	open_file_dialog,
	save_file_dialog,
	select_folder_dialog,
)

window = None
audio_backend = None
audio_backend_class = None
running = False
current_key_pressed = -1
current_key_released = -1
old_keys_held = []
keys_held = []
_resource_file = None
_clock = None
_last_events = []
fps = 120
_frame_text = ""
_seen_textinput = False
_keys_repeating = set()
_repeat_next = {}
_repeat_delay = 0.4
_repeat_interval = 0.05


class AudioBackendException(ValueError):
	pass


class AudioBackend:
	MINIAUDIO = 0


def initialize(audiobackend=AudioBackend.MINIAUDIO, audio_latency_ms=None, audio_device=None):
	"""Initialize lucia and the underlying graphic, audio, keyboard and interface engines.

	* audio_latency_ms: audio buffer size in milliseconds (default 20).
	  Lower means tighter timing, higher is safer on slow machines.
	* audio_device: index into lucia.audio.get_output_devices(), or None
	  for the system default output device.
	"""
	global audio_backend, audio_backend_class, running, _clock
	pygame.init()
	if audiobackend == AudioBackend.MINIAUDIO:
		from .audio import miniaudio as backend_miniaudio

		audio_backend_class = backend_miniaudio.MiniaudioAudioBackend(audio_latency_ms, audio_device)
		audio_backend_class.initialize()
		audio_backend = backend_miniaudio
	else:
		raise AudioBackendException(
			"Unknown audio backend. Available backend: lucia.AudioBackend.MINIAUDIO."
		)
	_clock = pygame.time.Clock()
	# warm up the speech helper process so the first spoken line doesn't
	# wait for its startup
	try:
		output._prestart()
	except Exception:
		pass
	running = True


def quit():
	"""Shutdown lucia and close underlying engines, freeing up system resources."""
	global running
	running = False
	if audio_backend_class is not None:
		audio_backend_class.quit()
	pygame.quit()


def get_global_resource_file():
	return _resource_file


def set_global_resource_file(file):
	global _resource_file
	if not isinstance(file, ResourceFile):
		raise ValueError('"file" must be an instance of "lucia.ResourceFile".')
	_resource_file = file


def show_window(title="LuciaGame", size=(640, 480)):
	"""Shows the main game window on the screen. This is most likely called at the start of a game."""
	global window
	window = pygame.display.set_mode(size)
	pygame.display.set_caption(title)
	# Explicitly enable SDL text input so get_characters() sees TEXTINPUT
	# events (IMEs, dead keys) regardless of platform defaults.
	try:
		pygame.key.start_text_input()
	except Exception:
		pass
	return window


def process_events():
	"""Process events for the window.

	This should be called in every loop iteration, to ensure that the
	window and application stay responsive. Returns the list of pygame
	events processed this frame.
	"""
	global current_key_pressed, current_key_released, old_keys_held, keys_held, running, _last_events
	global _frame_text, _seen_textinput
	current_key_pressed = -1
	current_key_released = -1
	old_keys_held = keys_held
	events = pygame.event.get()
	_last_events = events
	_joystick_module._process(events)
	keys_held = pygame.key.get_pressed()
	_frame_text = ""
	_fallback_text = ""
	for event in events:
		if event.type == QUIT:
			# pygame delivers QUIT for window close and Alt+F4.
			quit()
			sys.exit(0)
		if event.type == pygame.KEYDOWN:
			if len(old_keys_held) == 0 or not old_keys_held[event.key]:
				current_key_pressed = event.key
			for char in getattr(event, "unicode", ""):
				if char.isprintable():
					_fallback_text += char
		elif event.type == pygame.KEYUP:
			current_key_released = event.key
		elif event.type == pygame.TEXTINPUT:
			_seen_textinput = True
			_frame_text += event.text
	# Until the platform delivers real TEXTINPUT events (headless/dummy
	# never does), synthesize the frame text from KEYDOWN unicodes; once
	# TEXTINPUT works it wins exclusively, so nothing is ever doubled.
	if not _seen_textinput:
		_frame_text = _fallback_text
	_update_key_repeat(keys_held, time.monotonic())
	pygame.display.update()
	if audio_backend_class is not None:
		audio_backend_class.update_audio_system()
	if _clock is not None and fps > 0:
		# Keep the loop from spinning at 100% CPU; games can change lucia.fps.
		_clock.tick(fps)
	return events


def key_pressed(key_code):
	"""Check if a key was pressed down this frame (single key press).

	* key_code: A pygame.K_ key code

	returns: True if the specified key code was pressed, False otherwise.
	"""
	return current_key_pressed == key_code


def key_released(key_code):
	"""Check if a key was released this frame (single key release).

	* key_code: A pygame.K_ key code

	returns: True if the specified key code was released, False otherwise.
	"""
	return current_key_released == key_code


def key_down(key_code):
	"""Check if a key is being held down.

	* key_code: A pygame.K_ key code

	returns: True if the specified key code is being held down, False otherwise.
	"""
	if len(keys_held) == 0:
		return False
	return bool(keys_held[key_code])


def key_up(key_code):
	"""Check if a key isn't being held down (i.e. if it's not pressed and held).

	* key_code: A pygame.K_ key code

	returns: True if key is not held down, False otherwise.
	"""
	return not key_down(key_code)


def _update_key_repeat(held, now):
	"""Recompute which held keys fire a repeat this frame.

	Kept separate from process_events so tests can drive it with fake
	times. `held` is a pygame key-state sequence, `now` a monotonic time.
	"""
	global _keys_repeating
	_keys_repeating = set()
	if len(held) == 0:
		_repeat_next.clear()
		return
	for key in list(_repeat_next.keys()):
		if not held[key]:
			del _repeat_next[key]
	if current_key_pressed != -1:
		# A fresh press fires immediately, then waits the initial delay.
		_keys_repeating.add(current_key_pressed)
		_repeat_next[current_key_pressed] = now + _repeat_delay
	for key, due in _repeat_next.items():
		if key in _keys_repeating:
			continue
		if now >= due:
			_keys_repeating.add(key)
			_repeat_next[key] = now + _repeat_interval


def key_repeating(key_code):
	"""Like key_pressed, but fires again at the key-repeat rate while the
	key stays held. Ideal for scrolling menus and moving text cursors.

	* key_code: A pygame.K_ key code

	returns: True on the press frame and on every repeat frame after the
	initial delay, False otherwise.
	"""
	return key_code in _keys_repeating


def set_key_repeat(delay_ms=400, interval_ms=50):
	"""Configure key_repeating: the delay before repeating starts and the
	interval between repeats, both in milliseconds."""
	global _repeat_delay, _repeat_interval
	_repeat_delay = max(0, delay_ms) / 1000.0
	_repeat_interval = max(1, interval_ms) / 1000.0


def get_characters():
	"""The text the player typed this frame ("" when nothing was typed).

	Uses SDL text input (TEXTINPUT events), so dead keys, shifted
	characters and IMEs come out right. Use this for text fields instead
	of reading event.unicode yourself.
	"""
	return _frame_text
