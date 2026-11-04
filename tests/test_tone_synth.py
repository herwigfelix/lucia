"""Tests for the procedural tone synthesizer (pure numpy, no audio device)."""

import io
import os
import wave

import numpy as np
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from lucia.audio import synth
from lucia.audio.synth import (
	ToneSynth,
	WAVEFORM_NOISE,
	WAVEFORM_SQUARE,
	note_to_frequency,
)


def dominant_frequency(mono):
	spectrum = np.abs(np.fft.rfft(mono))
	freqs = np.fft.rfftfreq(len(mono), 1.0 / synth.SAMPLE_RATE)
	return freqs[spectrum.argmax()]


def test_note_to_frequency():
	assert note_to_frequency("a4") == pytest.approx(440.0)
	assert note_to_frequency("c4") == pytest.approx(261.63, abs=0.01)
	assert note_to_frequency("A5") == pytest.approx(880.0)
	assert note_to_frequency("c#4") == pytest.approx(277.18, abs=0.01)
	assert note_to_frequency("db4") == pytest.approx(277.18, abs=0.01)
	for bad in ("h4", "c", "x9", ""):
		with pytest.raises(ValueError):
			note_to_frequency(bad)


def test_length_and_frames():
	s = ToneSynth()
	s.freq(440, 500)
	assert s.length == 500
	assert len(s.to_buffer()) == int(round(0.5 * synth.SAMPLE_RATE))


def test_sine_frequency_is_right():
	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.freq(440, 1000)
	assert dominant_frequency(s.to_buffer()[:, 0]) == pytest.approx(440, abs=2)


def test_pan_attenuates_far_channel():
	s = ToneSynth()
	s.pan = -100
	s.freq(440, 100)
	buf = s.to_buffer()
	assert np.abs(buf[:, 1]).max() == 0.0
	assert np.abs(buf[:, 0]).max() > 0.1


def test_volume_scales_amplitude():
	loud = ToneSynth()
	loud.set_edge_fades(0, 0)
	loud.freq(440, 100)
	quiet = ToneSynth()
	quiet.set_edge_fades(0, 0)
	quiet.volume = -6.0206  # half amplitude
	quiet.freq(440, 100)
	ratio = np.abs(quiet.to_buffer()).max() / np.abs(loud.to_buffer()).max()
	assert ratio == pytest.approx(0.5, abs=0.01)
	silent = ToneSynth()
	silent.volume = -100
	silent.freq(440, 100)
	assert np.abs(silent.to_buffer()).max() == 0.0


def test_rest_is_silence():
	s = ToneSynth()
	s.rest(200)
	buf = s.to_buffer()
	assert len(buf) == int(round(0.2 * synth.SAMPLE_RATE))
	assert np.abs(buf).max() == 0.0


def test_freq_bend_raises_pitch():
	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.freq_bend(220, 880, 1000)
	buf = s.to_buffer()[:, 0]
	head = dominant_frequency(buf[: synth.SAMPLE_RATE // 10])
	tail = dominant_frequency(buf[-synth.SAMPLE_RATE // 10:])
	assert tail > head * 2


def test_note_bend_in_semitones():
	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.note_bend("a4", 12, 1000)  # one octave up
	buf = s.to_buffer()[:, 0]
	tail = dominant_frequency(buf[-synth.SAMPLE_RATE // 10:])
	assert tail == pytest.approx(880, rel=0.05)


def test_note_transpose():
	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.note_transpose = 12
	s.note("a4", 500)
	assert dominant_frequency(s.to_buffer()[:, 0]) == pytest.approx(880, abs=4)


def test_tempo_shortens_segments():
	s = ToneSynth()
	s.tempo = 2.0
	s.freq(440, 1000)
	assert s.length == 500


def test_square_wave_is_two_valued():
	s = ToneSynth()
	s.set_edge_fades(0, 0)
	s.waveform = WAVEFORM_SQUARE
	s.freq(440, 100)
	values = np.unique(np.round(np.abs(s.to_buffer()[:, 0]), 4))
	assert len(values) <= 3  # -amp, (0 at sign flips), +amp


def test_noise_is_seedable():
	a = ToneSynth()
	a.waveform = WAVEFORM_NOISE
	a.seed_noise(42)
	a.freq(0, 100)
	b = ToneSynth()
	b.waveform = WAVEFORM_NOISE
	b.seed_noise(42)
	b.freq(0, 100)
	assert np.array_equal(a.to_buffer(), b.to_buffer())


def test_unknown_waveform_rejected():
	with pytest.raises(ValueError):
		ToneSynth().waveform = 99


def test_wav_roundtrip(tmp_path):
	s = ToneSynth()
	s.note("c4", 100)
	data = s.to_wav_bytes()
	with wave.open(io.BytesIO(data)) as w:
		assert w.getnchannels() == 2
		assert w.getframerate() == synth.SAMPLE_RATE
		assert w.getsampwidth() == 2
		assert w.getnframes() == len(s.to_buffer())
	path = tmp_path / "tone.wav"
	assert s.save_wav(path)
	assert path.read_bytes() == data
	assert ToneSynth().save_wav(tmp_path / "empty.wav") is False


def test_empty_synth():
	s = ToneSynth()
	assert s.length == 0
	assert len(s.to_buffer()) == 0
	assert s.generate_sound() is None
	assert s.play() is None


def test_reset_keeps_state():
	s = ToneSynth()
	s.waveform = WAVEFORM_SQUARE
	s.note("c4", 100)
	s.reset()
	assert s.length == 0
	assert s.waveform == WAVEFORM_SQUARE
