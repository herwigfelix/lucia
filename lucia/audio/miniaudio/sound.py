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

"""The miniaudio-backed Sound class.

Keeps the BGT/Blastbay-style property conventions the rest of lucia
(and two decades of audio games) expect:

* volume: dB, 0 = full volume down to -100 = silence
* pan: -100 (left) .. 0 (center) .. 100 (right)
* pitch: percent, 100 = normal
* position: milliseconds
"""

import os

import lucia
from .. import probe as _probe
from . import mixer as _mixer


class Sound(lucia.audio.Sound):
	def __init__(self):
		self.voice = None
		self._volume = 0.0
		self._pan = 0.0
		self._pitch = 100.0

	def load(self, filename=""):
		"""Load a sound from the global resource file or from disk."""
		if self.is_active:
			self.close()
		data = None
		if lucia.get_global_resource_file() is not None:
			try:
				data = lucia.get_global_resource_file().get(filename)
			except KeyError:  # the file doesn't exist in the pack file.
				if not os.path.isfile(filename):
					_probe.record_failed_load(filename)
					return False
		try:
			if data is not None:
				buffer = _mixer.decode_memory(data)
			else:
				buffer = _mixer.decode_file(filename)
		except Exception:
			_probe.record_failed_load(filename)
			return False
		self.voice = _mixer.Voice(buffer)
		self.voice.label = str(filename)
		self._apply_all()
		_mixer.get_mixer().add_voice(self.voice)
		return True

	def stream(self, data):
		"""Load a sound from in-memory audio data (wav/ogg/mp3/flac bytes)."""
		if self.is_active:
			self.close()
		if not data:
			return False
		try:
			buffer = _mixer.decode_memory(bytes(data))
		except Exception:
			return False
		self.voice = _mixer.Voice(buffer)
		self.voice.label = "<stream>"
		self._apply_all()
		_mixer.get_mixer().add_voice(self.voice)
		return True

	def play(self):
		"""Play from the beginning.

		Calling play() on a sound that is already playing RESTARTS it —
		the same semantics BGT and NVGT have, and what repeated one-shots
		need: hitting a wall twice must be audible twice, not swallowed
		until the first bump happens to finish. Use resume() to continue
		a paused sound where it left off.
		"""
		if not self.is_active:
			return False
		self.voice.looping = False
		self.voice.pos = 0.0
		self.voice.finished = False
		self.voice.playing = True
		return True

	def play_wait(self):
		"""Play the sound and process events until it has finished."""
		if not self.play():
			return False
		while self.is_active and self.voice.playing:
			lucia.process_events()
		return True

	def play_looped(self):
		"""Loop from the beginning (restarts if already playing)."""
		if not self.is_active:
			return False
		self.voice.looping = True
		self.voice.pos = 0.0
		self.voice.finished = False
		self.voice.playing = True
		return True

	def stop(self):
		if self.is_active and self.voice.playing:
			self.voice.playing = False
			self.voice.pos = 0.0
			self.voice.finished = False
			return True

	def pause(self):
		if not self.is_active:
			return False
		self.voice.playing = False
		return True

	def resume(self):
		if not self.is_active:
			return False
		self._restart_if_finished()
		self.voice.playing = True
		return True

	def close(self):
		if self.voice is not None:
			self.voice.playing = False
			_mixer.get_mixer().remove_voice(self.voice)
			self.voice = None
			return True

	def get_source_object(self):
		return self.voice

	def _restart_if_finished(self):
		if self.voice.finished:
			self.voice.pos = 0.0
			self.voice.finished = False

	def _apply_all(self):
		self._apply_volume()
		self._apply_pan()
		self._apply_pitch()

	def _apply_volume(self):
		db = max(-100.0, min(0.0, float(self._volume)))
		self.voice.amp = 10 ** (db / 20.0) if db > -100.0 else 0.0

	def _apply_pan(self):
		# BGT/NVGT semantic (DirectSound heritage): the pan value IS the
		# dB attenuation of the far ear — pan 20 puts the far channel at
		# -20 dB. A linear 1-p/100 curve here made mid-range panning
		# nearly inaudible (pan 25 was only -2.5 dB) and broke ports.
		p = max(-100.0, min(100.0, float(self._pan)))
		magnitude = abs(p)
		far = 0.0 if magnitude >= 100.0 else 10.0 ** (-magnitude / 20.0)
		self.voice.gain_left = 1.0 if p <= 0 else far
		self.voice.gain_right = 1.0 if p >= 0 else far

	def _apply_pitch(self):
		self.voice.pitch_factor = max(0.0, float(self._pitch)) / 100.0

	@property
	def is_active(self):
		return self.voice is not None

	@property
	def is_playing(self):
		return self.is_active and self.voice.playing

	@property
	def volume(self):
		return self._volume

	@volume.setter
	def volume(self, value):
		"""Volume between 0 (full volume) and -100 (silence)."""
		self._volume = float(value)
		if self.is_active:
			self._apply_volume()

	@property
	def pan(self):
		return self._pan

	@pan.setter
	def pan(self, value):
		self._pan = float(value)
		if self.is_active:
			self._apply_pan()

	@property
	def pitch(self):
		return self._pitch

	@pitch.setter
	def pitch(self, value):
		self._pitch = float(value)
		if self.is_active:
			self._apply_pitch()

	@property
	def length(self):
		"""Total length of the loaded sound in milliseconds."""
		if not self.is_active:
			return 0
		return int(self.voice.frames / _mixer.SAMPLE_RATE * 1000)

	@property
	def position(self):
		"""Playback position in milliseconds."""
		if not self.is_active:
			return 0
		return int(self.voice.pos / _mixer.SAMPLE_RATE * 1000)

	@position.setter
	def position(self, ms):
		if self.is_active:
			frames = float(ms) / 1000.0 * _mixer.SAMPLE_RATE
			self.voice.pos = max(0.0, min(frames, float(self.voice.frames - 1)))
			self.voice.finished = False
