# Copyright (C) 2026  LuciaSoftware and it's contributors.
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

"""A background music manager with fades and crossfades, modeled after
NVGT's music include.

	music = lucia.audio.MusicManager(volume=-10)
	music.play("sounds/menu_theme.ogg")
	...
	music.play("sounds/battle_theme.ogg", fade_ms=1500)  # crossfades
	...
	music.update()  # call every frame for fades
	music.stop(fade_ms=1000)
"""

import time

import lucia


class MusicManager:
	def __init__(self, volume=0):
		"""volume: base music volume in dB (0 = full .. -100 = silent)."""
		self.volume = volume
		self._current = None
		self._current_file = None
		self._fading_out = []  # (sound, fade_start, fade_ms, start_volume)
		self._fade_in = None  # (fade_start, fade_ms)

	@property
	def current_track(self):
		return self._current_file

	def play(self, filename, looping=True, fade_ms=0):
		"""Play a music track. A currently playing track is stopped, or
		crossfaded out when fade_ms > 0."""
		if filename == self._current_file and self._current is not None:
			return True
		if self._current is not None:
			if fade_ms > 0:
				self._fading_out.append(
					(self._current, time.monotonic(), fade_ms, self._current.volume)
				)
			else:
				self._current.close()
		sound = lucia.audio_backend.Sound()
		if not sound.load(filename):
			self._current = None
			self._current_file = None
			return False
		self._current = sound
		self._current_file = filename
		source = sound.get_source_object()
		if source is not None and hasattr(source, "category"):
			source.category = "music"  # the audio probe keeps it out of the cue checks
		if fade_ms > 0:
			sound.volume = -100
			self._fade_in = (time.monotonic(), fade_ms)
		else:
			sound.volume = self.volume
			self._fade_in = None
		if looping:
			sound.play_looped()
		else:
			sound.play()
		return True

	def stop(self, fade_ms=0):
		"""Stop the music, optionally fading out."""
		if self._current is None:
			return
		if fade_ms > 0:
			self._fading_out.append(
				(self._current, time.monotonic(), fade_ms, self._current.volume)
			)
		else:
			self._current.close()
		self._current = None
		self._current_file = None
		self._fade_in = None

	def pause(self):
		if self._current is not None:
			self._current.pause()

	def resume(self):
		if self._current is not None:
			self._current.resume()

	def set_volume(self, volume):
		"""Change the base music volume (dB)."""
		self.volume = volume
		if self._current is not None and self._fade_in is None:
			self._current.volume = volume

	def update(self):
		"""Advance fades. Call once per frame."""
		now = time.monotonic()
		# fade in the current track
		if self._fade_in is not None and self._current is not None:
			start, fade_ms = self._fade_in
			progress = min(1.0, (now - start) * 1000.0 / fade_ms)
			# ramp from -60 dB up to the target volume
			self._current.volume = -60 + (self.volume + 60) * progress
			if progress >= 1.0:
				self._current.volume = self.volume
				self._fade_in = None
		# fade out old tracks
		remaining = []
		for sound, start, fade_ms, start_volume in self._fading_out:
			progress = min(1.0, (now - start) * 1000.0 / fade_ms)
			if progress >= 1.0:
				sound.close()
				continue
			sound.volume = start_volume + (-100 - start_volume) * progress
			remaining.append((sound, start, fade_ms, start_volume))
		self._fading_out = remaining
