"""Tests for master volume, output device switching and the recorder —
all without touching a real audio device (mocked miniaudio devices,
mixer callback fed directly)."""

import os

import numpy as np
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.audio.backend import AudioBackend, BackActionNotSupported
from lucia.audio.miniaudio import mixer
from lucia.audio.miniaudio.recorder import Recorder


def make_stream(m):
	stream = m._stream()
	next(stream)
	return stream


def read_frames(stream, n):
	return np.frombuffer(stream.send(n), dtype=np.float32).reshape(-1, 2)


def constant_voice(value=0.5, frames=100000):
	voice = mixer.Voice(np.full((frames, 2), value, dtype=np.float32))
	voice.playing = True
	return voice


def test_master_volume_scales_output():
	m = mixer.Mixer()
	m.add_voice(constant_voice())
	stream = make_stream(m)
	full = read_frames(stream, 64)
	m.set_master_volume(-6.0206)
	halved = read_frames(stream, 64)
	assert halved[0, 0] == pytest.approx(full[0, 0] / 2, rel=0.01)
	m.set_master_volume(-100)
	assert np.abs(read_frames(stream, 64)).max() == 0.0


def test_master_volume_clamps_and_roundtrips():
	m = mixer.Mixer()
	m.set_master_volume(10)
	assert m.get_master_volume() == 0.0
	m.set_master_volume(-500)
	assert m.get_master_volume() == -100.0
	m.set_master_volume(-12.5)
	assert m.get_master_volume() == -12.5


class FakePlaybackDevice:
	instances = []

	def __init__(self, **kwargs):
		self.kwargs = kwargs
		self.closed = False
		self.stream = None
		FakePlaybackDevice.instances.append(self)

	def start(self, stream):
		self.stream = stream

	def close(self):
		self.closed = True


def test_set_device_keeps_voices(monkeypatch):
	monkeypatch.setattr(mixer._ma, "PlaybackDevice", FakePlaybackDevice)
	FakePlaybackDevice.instances = []
	m = mixer.Mixer()
	m.start()
	voice = constant_voice()
	voice.pos = 1234.0
	m.add_voice(voice)
	first = FakePlaybackDevice.instances[-1]
	m.set_device("fake-id", devices_ref="fake-ref")
	second = FakePlaybackDevice.instances[-1]
	assert first.closed
	assert second is not first
	assert second.kwargs["device_id"] == "fake-id"
	assert m._voices == [voice]
	assert voice.pos == 1234.0
	assert m._devices_ref == "fake-ref"


class FakeCaptureDevice:
	def __init__(self, **kwargs):
		self.kwargs = kwargs
		self.generator = None
		self.closed = False

	def start(self, generator):
		self.generator = generator

	def close(self):
		self.closed = True


def test_recorder_collects_chunks(monkeypatch, tmp_path):
	from lucia.audio.miniaudio import recorder as recorder_module

	monkeypatch.setattr(recorder_module._ma, "CaptureDevice", FakeCaptureDevice)
	rec = Recorder()
	assert rec.start()
	assert rec.is_recording
	device = rec._device
	chunk = np.full(2 * 4410, 0.25, dtype=np.float32)  # 100 ms stereo
	device.generator.send(chunk.tobytes())
	device.generator.send(chunk.tobytes())
	assert rec.stop()
	assert not rec.is_recording
	assert device.closed
	buffer = rec.get_buffer()
	assert buffer.shape == (2 * 4410, 2)
	assert buffer[0, 0] == pytest.approx(0.25)
	assert rec.length == 200
	path = tmp_path / "clip.wav"
	assert rec.save_wav(path)
	back = mixer.decode_file(str(path))
	assert len(back) == len(buffer)
	assert back[100, 0] == pytest.approx(0.25, abs=0.01)


def test_recorder_without_capture_is_graceful(monkeypatch):
	from lucia.audio.miniaudio import recorder as recorder_module

	def broken(**kwargs):
		raise RuntimeError("no capture device")

	monkeypatch.setattr(recorder_module._ma, "CaptureDevice", broken)
	rec = Recorder()
	assert rec.start() is False
	assert rec.stop() is False
	assert rec.get_buffer() is None
	assert rec.to_wav_bytes() is None
	assert rec.save_wav("/nonexistent/clip.wav") is False
	assert rec.to_sound() is None


def test_backend_defaults_raise():
	class Bare(AudioBackend):
		def initialize(self):
			pass

		def update_audio_system(self):
			pass

		def quit(self):
			pass

		def is_hrtf_compatible(self):
			return False

		def enable_hrtf(self, should_enable):
			pass

	backend = Bare()
	for call in (
		lambda: backend.set_master_volume(0),
		backend.get_master_volume,
		backend.get_output_devices,
		lambda: backend.set_output_device(0),
		backend.get_capture_devices,
	):
		with pytest.raises(BackActionNotSupported):
			call()


def test_module_functions_require_initialize(monkeypatch):
	monkeypatch.setattr(lucia, "audio_backend_class", None)
	with pytest.raises(RuntimeError):
		lucia.audio.set_master_volume(-10)
	with pytest.raises(RuntimeError):
		lucia.audio.get_output_devices()


def test_initialize_accepts_audio_device_parameter():
	import inspect

	assert "audio_device" in inspect.signature(lucia.initialize).parameters


def test_device_name_listing_types():
	from lucia.audio.miniaudio import devices

	playbacks = devices.get_playback_devices()
	captures = devices.get_capture_devices()
	assert isinstance(playbacks, list)
	assert isinstance(captures, list)
	assert all(isinstance(name, str) for name in playbacks + captures)
	assert devices.resolve_playback_device(None) == (None, None)
	with pytest.raises(IndexError):
		devices.resolve_playback_device(10 ** 6)


def test_unit_pitch_fast_path_matches_slices():
	buffer = np.random.rand(1000, 2).astype(np.float32)
	v = mixer.Voice(buffer)
	v.playing = True
	v.amp = 0.5
	v.gain_left = 1.0
	v.gain_right = 0.25
	out = np.zeros((300, 2), dtype=np.float32)
	v.render(out, 300)
	assert np.allclose(out[:, 0], buffer[:300, 0] * 0.5)
	assert np.allclose(out[:, 1], buffer[:300, 1] * 0.125)
	assert v.pos == 300.0
	# runs to the end and finishes, remainder stays silent
	out2 = np.zeros((1000, 2), dtype=np.float32)
	v.render(out2, 1000)
	assert v.finished and not v.playing
	assert np.allclose(out2[:700, 0], buffer[300:, 0] * 0.5)
	assert np.abs(out2[700:]).max() == 0.0


def test_unit_pitch_fast_path_loops_across_wrap():
	buffer = np.random.rand(100, 2).astype(np.float32)
	v = mixer.Voice(buffer)
	v.playing = True
	v.looping = True
	v.pos = 90.0
	out = np.zeros((25, 2), dtype=np.float32)
	v.render(out, 25)
	expected = np.concatenate([buffer[90:], buffer[:15]])
	assert np.allclose(out, expected)
	assert v.pos == 15.0
	assert v.playing and not v.finished


def test_silent_voice_still_advances():
	buffer = np.ones((100, 2), dtype=np.float32)
	v = mixer.Voice(buffer)
	v.playing = True
	v.amp = 0.0                       # volume -100
	out = np.zeros((60, 2), dtype=np.float32)
	v.render(out, 60)
	assert np.abs(out).max() == 0.0
	assert v.pos == 60.0
	v.render(out, 60)                 # reaches the end silently
	assert v.finished


def test_pitched_path_still_interpolates():
	buffer = np.random.rand(500, 2).astype(np.float32)
	v = mixer.Voice(buffer)
	v.playing = True
	v.pitch_factor = 1.5
	out = np.zeros((100, 2), dtype=np.float32)
	v.render(out, 100)
	assert v.pos == pytest.approx(150.0)
	assert np.abs(out).max() > 0


def test_stream_reuses_buffer_across_blocks():
	m = mixer.Mixer()
	v = constant_voice(0.25, frames=100000)
	m.add_voice(v)
	stream = make_stream(m)
	first = read_frames(stream, 128)
	second = read_frames(stream, 128)
	assert first[0, 0] == pytest.approx(0.25)
	assert second[0, 0] == pytest.approx(0.25)
	# a larger block after a smaller one still works (buffer grows)
	third = read_frames(stream, 512)
	assert third.shape == (512, 2)
	smaller = read_frames(stream, 64)
	assert smaller.shape == (64, 2)


def test_decode_memory_is_cached(tmp_path):
	import io
	import wave

	out = io.BytesIO()
	with wave.open(out, "wb") as w:
		w.setnchannels(2)
		w.setsampwidth(2)
		w.setframerate(44100)
		w.writeframes(b"\x00\x00" * 4 * 100)
	data = out.getvalue()
	first = mixer.decode_memory(data)
	second = mixer.decode_memory(data)
	assert first is second            # decoded once, cached afterwards


def test_pan_is_db_attenuation_of_far_ear():
	"""Regression from porting a BGT game: BGT/NVGT treat the pan value
	as the dB attenuation of the far channel (DirectSound semantic).
	lucia's old linear curve made pan 25 only -2.5 dB, so ported games
	barely lateralized until the clamp and then snapped."""
	from lucia.audio.miniaudio.sound import Sound

	s = Sound()
	s.voice = mixer.Voice(np.zeros((10, 2), dtype=np.float32))
	s.pan = 20  # sound to the RIGHT: the far ear is the LEFT one
	assert s.voice.gain_right == 1.0
	assert s.voice.gain_left == pytest.approx(10 ** (-20 / 20.0))  # -20 dB
	s.pan = -40  # sound to the LEFT: the far ear is the RIGHT one
	assert s.voice.gain_left == 1.0
	assert s.voice.gain_right == pytest.approx(10 ** (-40 / 20.0))  # -40 dB
	s.pan = 100
	assert s.voice.gain_left == 0.0  # exact silence at the clamp
	s.pan = 0
	assert s.voice.gain_left == 1.0 and s.voice.gain_right == 1.0
	assert s.pan == 0  # getter still returns the set value


def test_synth_pan_matches_sound_pan_curve():
	from lucia.audio.synth import ToneSynth

	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.pan = -20
	s.freq(440, 50)
	buf = s.to_buffer()
	left = np.abs(buf[:, 0]).max()
	right = np.abs(buf[:, 1]).max()
	assert right / left == pytest.approx(10 ** (-20 / 20.0), rel=0.01)
