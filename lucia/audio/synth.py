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

"""A procedural tone generator in the spirit of NVGT's tone_synth.

Build a phrase from notes, frequencies and rests, then play it or save
it — no sound files needed. Great for UI cues, sonar pings, countdowns
and retro melodies:

	synth = lucia.audio.ToneSynth()
	synth.waveform = lucia.audio.WAVEFORM_SQUARE
	synth.note("c4", 150)
	synth.rest(50)
	synth.note("e4", 150)
	synth.freq_bend(440, 880, 300)   # slide up an octave
	synth.play()                     # needs lucia.initialize()
	synth.save_wav("melody.wav")     # or export

Conventions match the rest of lucia: volume in dB (0 .. -100), pan
-100 .. 100, all lengths in milliseconds.
"""

import io
import wave

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2

WAVEFORM_SINE = 0
WAVEFORM_SQUARE = 1
WAVEFORM_TRIANGLE = 2
WAVEFORM_SAWTOOTH = 3
WAVEFORM_NOISE = 4

_NOTE_OFFSETS = {"c": -9, "d": -7, "e": -5, "f": -4, "g": -2, "a": 0, "b": 2}


def note_to_frequency(name):
	"""The frequency of a note name like "c4", "f#3" or "bb5" in Hz.

	Letters a-g, an optional # (sharp) or b (flat), then the octave
	(a4 = 440 Hz). Raises ValueError for anything else.
	"""
	text = str(name).strip().lower()
	if len(text) < 2 or text[0] not in _NOTE_OFFSETS:
		raise ValueError("unknown note %r" % (name,))
	semitones = _NOTE_OFFSETS[text[0]]
	rest = text[1:]
	if rest.startswith("#"):
		semitones += 1
		rest = rest[1:]
	elif rest.startswith("b"):
		semitones -= 1
		rest = rest[1:]
	try:
		octave = int(rest)
	except ValueError:
		raise ValueError("unknown note %r" % (name,))
	semitones += (octave - 4) * 12
	return 440.0 * 2.0 ** (semitones / 12.0)


def buffer_to_wav_bytes(buffer, sample_rate=SAMPLE_RATE):
	"""Pack an (n, 2) float32 buffer into 16-bit PCM WAV bytes."""
	buffer = np.asarray(buffer, dtype=np.float32).reshape(-1, CHANNELS)
	clipped = np.clip(buffer, -1.0, 1.0)
	pcm = (clipped * 32767.0).astype("<i2")
	out = io.BytesIO()
	with wave.open(out, "wb") as w:
		w.setnchannels(CHANNELS)
		w.setsampwidth(2)
		w.setframerate(sample_rate)
		w.writeframes(pcm.tobytes())
	return out.getvalue()


class ToneSynth:
	"""A tone synthesizer: append notes/frequencies/rests, then play or export.

	The synth's waveform, volume, pan, tempo and note_transpose apply to
	segments as they are appended, so they can change mid-phrase.
	"""

	def __init__(self):
		self._segments = []
		self._waveform = WAVEFORM_SINE
		self._volume = 0.0
		self._pan = 0.0
		self._tempo = 1.0
		self._note_transpose = 0.0
		self._fade_start_ms = 4.0
		self._fade_end_ms = 4.0
		self._noise_rng = np.random.default_rng()

	# -- state ---------------------------------------------------------------

	@property
	def waveform(self):
		"""One of the WAVEFORM_* constants (default WAVEFORM_SINE)."""
		return self._waveform

	@waveform.setter
	def waveform(self, value):
		if value not in (WAVEFORM_SINE, WAVEFORM_SQUARE, WAVEFORM_TRIANGLE, WAVEFORM_SAWTOOTH, WAVEFORM_NOISE):
			raise ValueError("unknown waveform %r" % (value,))
		self._waveform = value

	@property
	def volume(self):
		"""Volume in dB for segments appended from now on (0 full .. -100 silent)."""
		return self._volume

	@volume.setter
	def volume(self, db):
		self._volume = max(-100.0, min(0.0, float(db)))

	@property
	def pan(self):
		"""Pan for new segments, -100 (left) .. 100 (right)."""
		return self._pan

	@pan.setter
	def pan(self, value):
		self._pan = max(-100.0, min(100.0, float(value)))

	@property
	def tempo(self):
		"""Length factor for new segments: 2.0 halves every duration."""
		return self._tempo

	@tempo.setter
	def tempo(self, value):
		value = float(value)
		if value <= 0:
			raise ValueError("tempo must be positive")
		self._tempo = value

	@property
	def note_transpose(self):
		"""Semitones added to every note()/note_bend() from now on."""
		return self._note_transpose

	@note_transpose.setter
	def note_transpose(self, semitones):
		self._note_transpose = float(semitones)

	def set_edge_fades(self, start_ms, end_ms):
		"""Fade-in/out ramps applied to each segment, against clicks (default 4/4 ms)."""
		self._fade_start_ms = max(0.0, float(start_ms))
		self._fade_end_ms = max(0.0, float(end_ms))

	def seed_noise(self, seed):
		"""Seed the noise waveform generator (for reproducible output)."""
		self._noise_rng = np.random.default_rng(seed)

	# -- building ------------------------------------------------------------

	def note(self, name, ms):
		"""Append a note ("c4", "f#3", ...) lasting ms milliseconds."""
		self.freq(self._transposed(note_to_frequency(name)), ms)

	def note_bend(self, name, bend_semitones, ms, bend_start_ms=0, bend_length_ms=None):
		"""Append a note that bends by bend_semitones over its length.

		bend_start_ms delays the bend; bend_length_ms limits how long the
		glide takes (default: the rest of the segment).
		"""
		start = self._transposed(note_to_frequency(name))
		end = start * 2.0 ** (float(bend_semitones) / 12.0)
		self._append_bend(start, end, ms, bend_start_ms, bend_length_ms)

	def freq(self, hz, ms):
		"""Append a constant tone of hz Hertz lasting ms milliseconds."""
		frames = self._frames(ms)
		if frames <= 0:
			return
		self._append(np.full(frames, float(hz), dtype=np.float64))

	def freq_bend(self, hz, end_hz, ms, bend_start_ms=0, bend_length_ms=None):
		"""Append a tone gliding from hz to end_hz over ms milliseconds."""
		self._append_bend(float(hz), float(end_hz), ms, bend_start_ms, bend_length_ms)

	def rest(self, ms):
		"""Append silence lasting ms milliseconds."""
		frames = self._frames(ms)
		if frames > 0:
			self._segments.append(np.zeros((frames, CHANNELS), dtype=np.float32))

	def reset(self):
		"""Discard everything appended so far (state like waveform is kept)."""
		self._segments = []

	# -- output --------------------------------------------------------------

	@property
	def length(self):
		"""The total length of the phrase in milliseconds."""
		frames = sum(len(s) for s in self._segments)
		return int(round(frames * 1000.0 / SAMPLE_RATE))

	def to_buffer(self):
		"""The whole phrase as one (n, 2) float32 buffer (empty when nothing was added)."""
		if not self._segments:
			return np.zeros((0, CHANNELS), dtype=np.float32)
		return np.concatenate(self._segments)

	def to_wav_bytes(self):
		"""The phrase as 16-bit PCM WAV bytes."""
		return buffer_to_wav_bytes(self.to_buffer())

	def save_wav(self, filename):
		"""Write the phrase to a WAV file. Returns True on success."""
		if not self._segments:
			return False
		try:
			with open(filename, "wb") as f:
				f.write(self.to_wav_bytes())
			return True
		except OSError:
			return False

	def generate_sound(self):
		"""The phrase as a playable lucia Sound (needs lucia.initialize()).

		Returns None when the synth is empty.
		"""
		if not self._segments:
			return None
		import lucia

		sound = lucia.audio_backend.Sound()
		sound.stream(self.to_wav_bytes())
		return sound

	def play(self, wait=False):
		"""Convenience: generate_sound() and play it. Returns the Sound."""
		sound = self.generate_sound()
		if sound is None:
			return None
		if wait:
			sound.play_wait()
		else:
			sound.play()
		return sound

	# -- internals -----------------------------------------------------------

	def _transposed(self, hz):
		if self._note_transpose:
			return hz * 2.0 ** (self._note_transpose / 12.0)
		return hz

	def _frames(self, ms):
		return int(round(float(ms) / self._tempo * SAMPLE_RATE / 1000.0))

	def _append_bend(self, start_hz, end_hz, ms, bend_start_ms, bend_length_ms):
		frames = self._frames(ms)
		if frames <= 0:
			return
		start_frame = min(frames, self._frames(bend_start_ms)) if bend_start_ms else 0
		if bend_length_ms is None:
			end_frame = frames
		else:
			end_frame = min(frames, start_frame + self._frames(bend_length_ms))
		freqs = np.empty(frames, dtype=np.float64)
		freqs[:start_frame] = start_hz
		glide = max(1, end_frame - start_frame)
		freqs[start_frame:end_frame] = np.linspace(start_hz, end_hz, glide, endpoint=True)
		freqs[end_frame:] = end_hz
		self._append(freqs)

	def _append(self, freqs):
		frames = len(freqs)
		if self._waveform == WAVEFORM_NOISE:
			mono = self._noise_rng.uniform(-1.0, 1.0, frames).astype(np.float32)
		else:
			phase = 2.0 * np.pi * np.cumsum(freqs) / SAMPLE_RATE
			if self._waveform == WAVEFORM_SINE:
				mono = np.sin(phase)
			elif self._waveform == WAVEFORM_SQUARE:
				mono = np.sign(np.sin(phase))
			elif self._waveform == WAVEFORM_TRIANGLE:
				mono = 2.0 / np.pi * np.arcsin(np.sin(phase))
			else:  # WAVEFORM_SAWTOOTH
				mono = 2.0 * (np.mod(phase / (2.0 * np.pi) + 0.5, 1.0)) - 1.0
			mono = mono.astype(np.float32)
		amp = 0.0 if self._volume <= -100.0 else 10.0 ** (self._volume / 20.0)
		mono *= amp
		fade_in = min(frames, int(round(self._fade_start_ms * SAMPLE_RATE / 1000.0)))
		if fade_in > 0:
			mono[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
		fade_out = min(frames, int(round(self._fade_end_ms * SAMPLE_RATE / 1000.0)))
		if fade_out > 0:
			mono[frames - fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
		# Same pan semantic as the mixer (BGT/NVGT style): the pan value
		# is the dB attenuation of the far channel.
		magnitude = abs(self._pan)
		far = 0.0 if magnitude >= 100.0 else 10.0 ** (-magnitude / 20.0)
		left = mono * (far if self._pan > 0 else 1.0)
		right = mono * (far if self._pan < 0 else 1.0)
		self._segments.append(np.column_stack((left, right)).astype(np.float32))
