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

"""Software mixer on top of miniaudio.

One miniaudio PlaybackDevice is opened; any number of voices are mixed
into it in the audio callback with per-voice volume, pan and pitch
(linear-interpolation resampling), all implemented with numpy.
"""

import atexit
import threading
import zlib

import miniaudio as _ma
import numpy as np

from .. import probe as _probe

SAMPLE_RATE = 44100
CHANNELS = 2

# Audio buffer size in milliseconds. This is the single most important
# number for how a game FEELS: it is the delay between calling play() and
# hearing the sound, and it quantizes the timing of everything you play.
# miniaudio's own default is 200 ms, which makes footsteps sound ragged
# (a step every 280 ms lands alternately on the 200/400 ms grid) and gives
# audible lag on every cue. 20 ms is responsive and still safe on the
# callback; raise it only if a slow machine crackles.
BUFFER_MSEC = 20

# Master limiter (opt-in, see Mixer.set_limiter). Attack is a short gain
# ramp at the start of a block, release is slow so the gain doesn't pump.
LIMITER_ATTACK_FRAMES = 64
LIMITER_RELEASE_DB_PER_SEC = 20.0

# Decoded-file cache so repeated plays (footsteps!) don't re-decode.
_decode_cache = {}
_decode_cache_order = []
_DECODE_CACHE_MAX = 128


def _to_stereo_float32(decoded):
	"""Convert a miniaudio DecodedSoundFile to an (n, 2) float32 numpy array."""
	frames = np.asarray(decoded.samples, dtype=np.float32)
	if decoded.sample_format == _ma.SampleFormat.SIGNED16:
		frames = frames / 32768.0
	elif decoded.sample_format == _ma.SampleFormat.SIGNED32:
		frames = frames / 2147483648.0
	return frames.reshape(-1, CHANNELS)


def decode_file(filename):
	"""Decode an audio file from disk to a stereo float32 buffer (cached)."""
	cached = _decode_cache.get(filename)
	if cached is not None:
		return cached
	decoded = _ma.decode_file(
		filename,
		output_format=_ma.SampleFormat.FLOAT32,
		nchannels=CHANNELS,
		sample_rate=SAMPLE_RATE,
	)
	buf = _to_stereo_float32(decoded)
	_decode_cache[filename] = buf
	_decode_cache_order.append(filename)
	if len(_decode_cache_order) > _DECODE_CACHE_MAX:
		oldest = _decode_cache_order.pop(0)
		_decode_cache.pop(oldest, None)
	return buf


def decode_memory(data):
	"""Decode audio data (wav/ogg/mp3/flac bytes) to a stereo float32 buffer.

	Cached like decode_file: sounds loaded from a resource pack are
	played over and over (footsteps, menu clicks), and re-decoding them
	on every load caused audible hiccups on the main thread.
	"""
	key = (len(data), zlib.crc32(data))
	cached = _decode_cache.get(key)
	if cached is not None:
		return cached
	decoded = _ma.decode(
		data,
		output_format=_ma.SampleFormat.FLOAT32,
		nchannels=CHANNELS,
		sample_rate=SAMPLE_RATE,
	)
	buf = _to_stereo_float32(decoded)
	_decode_cache[key] = buf
	_decode_cache_order.append(key)
	if len(_decode_cache_order) > _DECODE_CACHE_MAX:
		oldest = _decode_cache_order.pop(0)
		_decode_cache.pop(oldest, None)
	return buf


class Voice:
	"""A single playing (or paused) buffer inside the mixer."""

	def __init__(self, buffer):
		self.buffer = buffer  # (n, 2) float32
		self.pos = 0.0  # play head, in source frames (float, for resampling)
		self.playing = False
		self.looping = False
		self.finished = False  # reached the end of a non-looping buffer
		self.gain_left = 1.0
		self.gain_right = 1.0
		self.amp = 1.0  # linear amplitude from the dB volume
		self.pitch_factor = 1.0  # 1.0 = normal speed
		self.label = None  # filename, for the audio probe
		self.category = None  # "music" for MusicManager tracks (probe report)

	@property
	def frames(self):
		return len(self.buffer)

	def render(self, out, n):
		"""Mix n frames of this voice into out ((n,2) float32). Called on the audio thread."""
		if not self.playing or self.finished or self.frames == 0:
			return
		f = self.pitch_factor
		if f <= 0:
			return
		la = self.amp * self.gain_left
		ra = self.amp * self.gain_right
		end = float(self.frames)
		if la == 0.0 and ra == 0.0:
			# inaudible: just advance the play head, no mixing work
			self.pos += n * f
			if self.looping:
				self.pos %= end
			elif self.pos >= end - 1e-9:
				self.playing = False
				self.finished = True
			return
		if f == 1.0:
			# the common case (pitch 100): plain slices, no resampling,
			# no index arrays — keeps the audio callback allocation-light
			self._render_direct(out, n, la, ra)
			return
		idx = self.pos + np.arange(n, dtype=np.float64) * f
		if self.looping:
			idx = np.mod(idx, end)
			valid = n
		else:
			valid = int(np.searchsorted(idx, end - 1e-9))
			if valid <= 0:
				self.playing = False
				self.finished = True
				return
			idx = idx[:valid]
		i0 = idx.astype(np.int64)
		i1 = i0 + 1
		if self.looping:
			i1 = np.mod(i1, self.frames)
		else:
			i1 = np.minimum(i1, self.frames - 1)
		frac = (idx - i0).astype(np.float32).reshape(-1, 1)
		chunk = self.buffer[i0] * (1.0 - frac) + self.buffer[i1] * frac
		out[:valid, 0] += chunk[:, 0] * la
		out[:valid, 1] += chunk[:, 1] * ra
		self.pos = self.pos + valid * f
		if self.looping:
			self.pos = self.pos % end
		elif valid < n or self.pos >= end - 1e-9:
			self.playing = False
			self.finished = True

	def _render_direct(self, out, n, la, ra):
		"""Unit-pitch mixing via contiguous slices (loop-aware)."""
		pos = int(self.pos)
		frames = self.frames
		written = 0
		while written < n:
			if pos >= frames:
				if not self.looping:
					self.playing = False
					self.finished = True
					break
				pos -= frames
				continue
			take = min(n - written, frames - pos)
			chunk = self.buffer[pos:pos + take]
			target = out[written:written + take]
			if la == ra:
				if la == 1.0:
					target += chunk
				else:
					target += chunk * la
			else:
				target[:, 0] += chunk[:, 0] * la
				target[:, 1] += chunk[:, 1] * ra
			pos += take
			written += take
			if pos >= frames and not self.looping:
				self.playing = False
				self.finished = True
				break
		self.pos = float(pos % frames) if self.looping else float(pos)


class Mixer:
	"""The global software mixer: one playback device, many voices."""

	def __init__(self, buffer_msec=None, device_id=None, devices_ref=None):
		self._voices = []
		self._lock = threading.Lock()
		self._device = None
		self.buffer_msec = BUFFER_MSEC if buffer_msec is None else buffer_msec
		self._device_id = device_id
		# miniaudio device ids are CFFI pointers owned by a miniaudio.Devices
		# object; they die with it. Keep the Devices object alive here for
		# as long as its id is in use.
		self._devices_ref = devices_ref
		self.master_db = 0.0
		self._master_amp = 1.0
		# output meter (always on, costs one abs/max per block)
		self._meter_peak = 0.0
		self._meter_sum_sq = 0.0
		self._meter_samples = 0
		self._meter_clips = 0
		# master limiter (off by default: it changes the sound)
		self.limiter = False
		self.limiter_ceiling_db = -1.0
		self._limiter_ceiling = 10.0 ** (-1.0 / 20.0)
		self._limiter_gain = 1.0
		self.probe = None  # lucia.audio.probe.AudioProbe when measuring

	def start(self):
		if self._device is not None:
			return
		self._device = _ma.PlaybackDevice(
			output_format=_ma.SampleFormat.FLOAT32,
			nchannels=CHANNELS,
			sample_rate=SAMPLE_RATE,
			buffersize_msec=self.buffer_msec,
			device_id=self._device_id,
		)
		stream = self._stream()
		next(stream)  # prime the generator; miniaudio sends frame counts into it
		self._device.start(stream)

	def set_master_volume(self, db):
		"""Global volume in dB, multiplied onto every voice (0 full .. -100 silent)."""
		db = max(-100.0, min(0.0, float(db)))
		self.master_db = db
		self._master_amp = 0.0 if db <= -100.0 else 10.0 ** (db / 20.0)

	def get_master_volume(self):
		return self.master_db

	# -- metering -----------------------------------------------------------

	def peak_dbfs(self):
		"""Loudest sample of the mix since the last reset_meter(), in dBFS.

		Measured BEFORE the limiter and the final clip, so it shows what
		the game's mix really adds up to: above 0 means the output clipped
		(or the limiter had to step in). -200 when nothing played.
		"""
		return _to_db(self._meter_peak)

	def rms_dbfs(self):
		"""Average (RMS) level of the mix since the last reset_meter(), in dBFS."""
		if not self._meter_samples:
			return -200.0
		return _to_db((self._meter_sum_sq / self._meter_samples) ** 0.5)

	def clip_count(self):
		"""Audio blocks (~buffer_msec each) whose mix exceeded 0 dBFS since reset."""
		return self._meter_clips

	def reset_meter(self):
		self._meter_peak = 0.0
		self._meter_sum_sq = 0.0
		self._meter_samples = 0
		self._meter_clips = 0

	def set_limiter(self, enabled, ceiling_db=-1.0):
		"""Soft master limiter: catches peaks before they clip hard.

		It is a safety net, not a mixing tool — a mix whose base volumes
		are too hot stays too hot (squashed instead of clipped). Keep the
		peak_dbfs() of normal play under about -3 dBFS by choosing
		volumes; the limiter only rescues the rare pile-up.
		"""
		self.limiter = bool(enabled)
		self.limiter_ceiling_db = min(0.0, float(ceiling_db))
		self._limiter_ceiling = 10.0 ** (self.limiter_ceiling_db / 20.0)
		if not self.limiter:
			self._limiter_gain = 1.0

	def _measure(self, block):
		if not len(block):
			return 0.0
		peak = float(np.max(np.abs(block)))
		if peak > self._meter_peak:
			self._meter_peak = peak
		if peak > 1.0:
			self._meter_clips += 1
		self._meter_sum_sq += float(np.einsum("ij,ij->", block, block))
		self._meter_samples += block.size
		return peak

	def _limit(self, block, peak):
		"""Gain-riding limiter plus a soft knee for whatever the ramp misses."""
		n = len(block)
		ceiling = self._limiter_ceiling
		old = self._limiter_gain
		target = ceiling / peak if peak > ceiling else 1.0
		if target < old:
			new = target  # attack: reach the target within LIMITER_ATTACK_FRAMES
		else:
			step = 10.0 ** (LIMITER_RELEASE_DB_PER_SEC * n / SAMPLE_RATE / 20.0)
			new = min(target, old * step)
		if old != 1.0 or new != 1.0:
			if new < old:
				ramp = min(n, LIMITER_ATTACK_FRAMES)
				gains = np.full(n, new, dtype=np.float32)
				gains[:ramp] = np.linspace(old, new, ramp, dtype=np.float32)
			else:
				gains = np.linspace(old, new, n, dtype=np.float32)
			block *= gains[:, None]
		self._limiter_gain = new
		# soft knee: anything still above the ceiling (the attack ramp)
		# is bent towards 0 dBFS instead of being cut flat
		over = np.abs(block) > ceiling
		if over.any():
			head = 1.0 - ceiling
			x = block[over]
			block[over] = np.sign(x) * (ceiling + head * np.tanh((np.abs(x) - ceiling) / head))

	def set_device(self, device_id, devices_ref=None):
		"""Switch to another output device; playing voices carry on where
		they are (only their positions live in _voices, which is kept)."""
		if self._device is not None:
			try:
				self._device.close()
			except Exception:
				pass
			self._device = None
		self._device_id = device_id
		self._devices_ref = devices_ref
		self.start()

	def stop(self):
		if self._device is not None:
			try:
				self._device.close()
			except Exception:
				pass
			self._device = None
		with self._lock:
			self._voices.clear()

	def add_voice(self, voice):
		with self._lock:
			if voice not in self._voices:
				self._voices.append(voice)

	def remove_voice(self, voice):
		with self._lock:
			if voice in self._voices:
				self._voices.remove(voice)

	def _stream(self):
		required = yield b""
		# one reused output buffer: allocating a fresh array 50x per
		# second churns the GC, and GC pauses are audible as dropouts
		out = np.zeros((max(required, 1), CHANNELS), dtype=np.float32)
		while True:
			if required > len(out):
				out = np.zeros((required, CHANNELS), dtype=np.float32)
			block = out[:required]
			block.fill(0.0)
			with self._lock:
				voices = list(self._voices)
			for voice in voices:
				try:
					voice.render(block, required)
				except Exception:
					voice.playing = False
					voice.finished = True
			if self._master_amp != 1.0:
				block *= self._master_amp
			peak = self._measure(block)
			if self.probe is not None:
				try:
					self.probe.sample(voices, self._master_amp, block, peak)
				except Exception:
					pass
			if self.limiter:
				self._limit(block, peak)
			np.clip(block, -1.0, 1.0, out=block)
			required = yield block.tobytes()


def _to_db(amp):
	return 20.0 * np.log10(amp) if amp > 1e-10 else -200.0


_mixer = None


def get_mixer(buffer_msec=None, device_id=None, devices_ref=None):
	"""Return the global mixer, creating and starting it on first use."""
	global _mixer
	if _mixer is None:
		_mixer = Mixer(buffer_msec, device_id, devices_ref)
		_register_exit_shutdown()
		_probe.install_from_env(_mixer)  # registered later, so it runs first
		_mixer.start()
	return _mixer


_exit_hook_registered = False


def _register_exit_shutdown():
	"""Close the device at exit even when the game never called lucia.quit().

	Without this a program that ends by an uncaught exception hangs forever
	at interpreter shutdown: the device is closed from a finalizer while the
	audio thread still calls back into a half torn-down interpreter.
	"""
	global _exit_hook_registered
	if not _exit_hook_registered:
		_exit_hook_registered = True
		atexit.register(shutdown_mixer)


def shutdown_mixer():
	global _mixer
	if _mixer is not None:
		_mixer.stop()
		_mixer = None
