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

"""Microphone recording for the miniaudio backend.

	recorder = lucia.audio_backend.Recorder()
	recorder.start()
	# ... player speaks ...
	recorder.stop()
	recorder.save_wav("clip.wav")
	recorder.to_sound().play()

Recording happens in float32/44100/stereo — the same format the mixer
plays, so playback needs no conversion. Pick a microphone by index from
lucia.audio.get_capture_devices() (None = system default).
"""

import miniaudio as _ma
import numpy as np

from ..synth import buffer_to_wav_bytes
from . import devices
from .mixer import CHANNELS, SAMPLE_RATE


class Recorder:
	"""Records from a microphone into memory.

	device: index into lucia.audio.get_capture_devices(), or None for
	the system default microphone.
	"""

	def __init__(self, device=None):
		self.device_index = device
		self._device = None
		self._devices_ref = None
		self._chunks = []
		self._buffer = None

	@property
	def is_recording(self):
		return self._device is not None

	def start(self):
		"""Open the microphone and start recording. Returns True on success.

		A new start() discards the previous recording.
		"""
		if self._device is not None:
			return True
		try:
			device_id, devices_ref = devices.resolve_capture_device(self.device_index)
			device = _ma.CaptureDevice(
				input_format=_ma.SampleFormat.FLOAT32,
				nchannels=CHANNELS,
				sample_rate=SAMPLE_RATE,
				device_id=device_id,
			)
		except Exception:
			return False
		self._chunks = []
		self._buffer = None
		generator = self._collect()
		next(generator)
		try:
			device.start(generator)
		except Exception:
			try:
				device.close()
			except Exception:
				pass
			return False
		self._device = device
		self._devices_ref = devices_ref  # keep the CFFI device id alive
		return True

	def stop(self):
		"""Stop recording and finalize the buffer. Returns True when audio was captured."""
		if self._device is not None:
			try:
				self._device.close()
			except Exception:
				pass
			self._device = None
			self._devices_ref = None
		if self._chunks:
			data = b"".join(bytes(chunk) for chunk in self._chunks)
			self._chunks = []
			frames = np.frombuffer(data, dtype=np.float32)
			self._buffer = frames.reshape(-1, CHANNELS)
		return self._buffer is not None and len(self._buffer) > 0

	def _collect(self):
		chunk = yield
		while True:
			self._chunks.append(chunk)
			chunk = yield

	@property
	def length(self):
		"""The length of the finished recording in milliseconds."""
		if self._buffer is None:
			return 0
		return int(round(len(self._buffer) * 1000.0 / SAMPLE_RATE))

	def get_buffer(self):
		"""The recording as an (n, 2) float32 numpy buffer, or None."""
		return self._buffer

	def to_wav_bytes(self):
		"""The recording as 16-bit PCM WAV bytes, or None."""
		if self._buffer is None or len(self._buffer) == 0:
			return None
		return buffer_to_wav_bytes(self._buffer)

	def save_wav(self, filename):
		"""Write the recording to a WAV file. Returns True on success."""
		data = self.to_wav_bytes()
		if data is None:
			return False
		try:
			with open(filename, "wb") as f:
				f.write(data)
			return True
		except OSError:
			return False

	def to_sound(self):
		"""The recording as a playable lucia Sound, or None."""
		data = self.to_wav_bytes()
		if data is None:
			return None
		from .sound import Sound

		sound = Sound()
		sound.stream(data)
		return sound
