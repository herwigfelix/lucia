"""Regression tests for the two audio defects reported from a real game:
irregular footsteps (buffer far too large) and a mid-fight crash when a
failed play() result was fed back into the pool."""

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.audio.miniaudio import mixer


@pytest.fixture(scope="module")
def engine():
	"""Bring the audio engine up once for the whole module."""
	lucia.initialize()
	yield
	lucia.quit()


def test_buffer_is_low_latency():
	"""miniaudio defaults to 200 ms, which makes regular events sound
	ragged; lucia must ask for a small buffer."""
	assert mixer.BUFFER_MSEC <= 30
	m = mixer.Mixer()
	assert m.buffer_msec == mixer.BUFFER_MSEC
	assert mixer.Mixer(buffer_msec=50).buffer_msec == 50


def test_initialize_accepts_latency_override():
	import inspect

	signature = inspect.signature(lucia.initialize)
	assert "audio_latency_ms" in signature.parameters


def test_pool_tolerates_failed_play_results(engine):
	"""play_* returns -1/-2 when a sound cannot be placed; games store that
	value and pass it back. None of it may raise."""
	pool = lucia.audio_backend.SoundPool()
	bad = pool.play_3d("no-such-file.ogg", 0, 0, 0, 5, 5, 0)
	assert isinstance(bad, int)
	for value in (bad, None, -2):
		assert pool.update_sound_3d(value, 1, 2, 3) is False
		assert pool.update_sound_2d(value, 1, 2) is False
		assert pool.update_sound_1d(value, 1) is False
		assert pool.update_sound_start_values(value, 0, 0, 100) is False
		assert pool.update_sound_range_1d(value, 0, 0) is False
		assert pool.destroy_sound(value) is False
		assert pool.pause_sound(value) is False
		assert pool.resume_sound(value) is False
		assert pool.sound_is_active(value) is False
		assert pool.sound_is_playing(value) is False


def test_pool_still_works_with_real_slots(engine, tmp_path):
	"""The guards must not break normal operation."""
	import math
	import struct
	import wave

	path = str(tmp_path / "tone.wav")
	with wave.open(path, "w") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(44100)
		w.writeframes(
			b"".join(
				struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 44100)))
				for i in range(4410)
			)
		)
	pool = lucia.audio_backend.SoundPool(max_distance=50, pan_step=4, volume_step=2)
	slot = pool.play_2d(path, 0, 0, 5, 0, False)
	assert not isinstance(slot, int)
	assert pool.update_sound_2d(slot, 8, 0) is True
	assert pool.destroy_sound(slot) is True


def test_failed_play_is_falsy(engine):
	"""play_* failures must be falsy so `if slot:` is a safe check.

	A generated game once wrote `isinstance(slot, int)` with the polarity
	inverted, which silently disabled every branch behind it.
	"""
	from lucia.audio.miniaudio.soundpool import FailedSlot

	pool = lucia.audio_backend.SoundPool()
	failed = pool.play_2d("does-not-exist.ogg", 0, 0, 1, 1, False)
	assert isinstance(failed, FailedSlot)
	assert not failed, "a failed play must be falsy"
	assert failed == -1, "the historical int value stays for compatibility"
	assert isinstance(failed, int), "still an int for old comparisons"
	assert pool.sound_is_playing(failed) is False
	assert pool.sound_is_active(failed) is False


def test_real_slot_is_truthy(engine, tmp_path):
	import math
	import struct
	import wave

	path = str(tmp_path / "tone.wav")
	with wave.open(path, "w") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(44100)
		w.writeframes(
			b"".join(
				struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 44100)))
				for i in range(4410)
			)
		)
	pool = lucia.audio_backend.SoundPool(max_distance=50, pan_step=4, volume_step=2)
	slot = pool.play_2d(path, 0, 0, 2, 0, False)
	assert slot, "a real slot must be truthy"
	assert pool.sound_is_playing(slot) is True


def _tone(tmp_path, seconds=2.0):
	import math
	import struct
	import wave

	path = str(tmp_path / "tone.wav")
	frames = int(44100 * seconds)
	with wave.open(path, "w") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(44100)
		w.writeframes(
			b"".join(
				struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 44100)))
				for i in range(frames)
			)
		)
	return path


def test_play_restarts_an_already_playing_sound(engine, tmp_path):
	"""Repeated one-shots (wall bumps, UI denials) must be audible every time.

	Before this was fixed, play() on a still-playing Sound was a no-op, so
	running into a wall produced one bump per sound length instead of one
	per hit.
	"""
	import time

	sound = lucia.audio_backend.Sound()
	assert sound.load(_tone(tmp_path))
	sound.volume = -100
	sound.play()
	time.sleep(0.3)
	assert sound.position > 100, "sound should have advanced"
	sound.play()
	assert sound.position < 100, "play() must restart from the beginning"


def test_resume_continues_where_it_paused(engine, tmp_path):
	import time

	sound = lucia.audio_backend.Sound()
	sound.load(_tone(tmp_path))
	sound.volume = -100
	sound.play()
	time.sleep(0.3)
	sound.pause()
	paused_at = sound.position
	assert paused_at > 100
	sound.resume()
	assert abs(sound.position - paused_at) < 60, "resume() must not restart"


def test_play_looped_also_restarts(engine, tmp_path):
	import time

	sound = lucia.audio_backend.Sound()
	sound.load(_tone(tmp_path))
	sound.volume = -100
	sound.play_looped()
	time.sleep(0.3)
	sound.play_looped()
	assert sound.position < 100
