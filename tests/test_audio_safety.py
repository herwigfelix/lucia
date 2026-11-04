"""Audio safety nets from player feedback on a generated game: output meter and opt-in
limiter (clipping), per-source volume floor (distance made mechanics
inaudible), per-file instance cap (identical copies add up), the voice
channel (speech arbitration by rank) and the audio probe (what actually
reaches the ear)."""

import math
import os
import struct
import wave

import numpy as np
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.audio import probe as probe_mod
from lucia.audio.miniaudio import mixer
from lucia.audio.voice_channel import VoiceChannel


def make_stream(m):
	stream = m._stream()
	next(stream)
	return stream


def read_frames(stream, n):
	return np.frombuffer(stream.send(n), dtype=np.float32).reshape(-1, 2)


def constant_voice(value=0.5, frames=200000):
	voice = mixer.Voice(np.full((frames, 2), value, dtype=np.float32))
	voice.playing = True
	return voice


def _tone(path, seconds=2.0):
	frames = int(44100 * seconds)
	with wave.open(str(path), "w") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(44100)
		w.writeframes(b"".join(
			struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 44100)))
			for i in range(frames)
		))
	return str(path)


@pytest.fixture(scope="module")
def engine():
	lucia.initialize()
	yield
	lucia.quit()


# -- meter ------------------------------------------------------------------

def test_meter_reports_peak_and_clips_before_the_final_clip():
	m = mixer.Mixer()
	for _ in range(3):
		m.add_voice(constant_voice(0.5))
	stream = make_stream(m)
	out = read_frames(stream, 256)
	assert out.max() == pytest.approx(1.0)  # hard clip, limiter is off
	assert m.peak_dbfs() == pytest.approx(20 * math.log10(1.5), abs=0.01)
	assert m.clip_count() == 1
	assert m.rms_dbfs() == pytest.approx(20 * math.log10(1.5), abs=0.01)
	m.reset_meter()
	assert m.peak_dbfs() == -200.0
	assert m.clip_count() == 0


def test_meter_quiet_mix_has_no_clips():
	m = mixer.Mixer()
	m.add_voice(constant_voice(0.25))
	stream = make_stream(m)
	read_frames(stream, 256)
	assert m.peak_dbfs() == pytest.approx(-12.04, abs=0.01)
	assert m.clip_count() == 0


# -- limiter ----------------------------------------------------------------

def test_limiter_is_off_by_default_and_leaves_quiet_mixes_untouched():
	m = mixer.Mixer()
	assert m.limiter is False
	m.set_limiter(True)
	m.add_voice(constant_voice(0.3))
	stream = make_stream(m)
	out = read_frames(stream, 512)
	assert np.allclose(out, 0.3)


def test_limiter_keeps_a_pile_up_under_the_ceiling():
	m = mixer.Mixer()
	m.set_limiter(True, ceiling_db=-1.0)
	for _ in range(4):
		m.add_voice(constant_voice(0.5))  # +6 dBFS summed
	stream = make_stream(m)
	blocks = [read_frames(stream, 882) for _ in range(5)]
	ceiling = 10 ** (-1 / 20)
	# only the 64-frame attack ramp of the first block goes over the
	# ceiling, and the soft knee bends it instead of cutting it flat
	assert np.abs(blocks[0][mixer.LIMITER_ATTACK_FRAMES:]).max() == pytest.approx(ceiling, rel=0.001)
	assert np.all(np.diff(blocks[0][:mixer.LIMITER_ATTACK_FRAMES, 0]) <= 1e-6)
	for block in blocks[1:]:
		assert np.abs(block).max() == pytest.approx(ceiling, rel=0.001)
	# the meter still shows the truth, so a too-hot mix stays visible
	assert m.peak_dbfs() > 5.9
	assert m.clip_count() == 5


def test_limiter_releases_after_the_pile_up():
	m = mixer.Mixer()
	m.set_limiter(True)
	loud = [constant_voice(0.5) for _ in range(4)]
	for v in loud:
		m.add_voice(v)
	stream = make_stream(m)
	read_frames(stream, 882)
	for v in loud[1:]:
		m.remove_voice(v)
	for _ in range(200):  # 4 s of 20 ms blocks
		out = read_frames(stream, 882)
	assert out.max() == pytest.approx(0.5, rel=0.01)


# -- probe ------------------------------------------------------------------

def test_probe_records_level_at_the_ear_and_quiet_plays():
	p = probe_mod.AudioProbe()
	near = constant_voice(0.5)
	near.label = "near.ogg"
	near.amp = 10 ** (-10 / 20)
	far = constant_voice(0.5)
	far.label = "far.ogg"
	far.amp = 10 ** (-70 / 20)
	block = np.zeros((10, 2), dtype=np.float32)
	p.sample([near, far], 1.0, block, 0.0)
	near.pos += 10
	p.sample([near], 1.0, block, 0.0)  # far finished
	report = p.report()
	assert report["files"]["near.ogg"]["plays"] == 1
	assert report["files"]["near.ogg"]["max_db"] == pytest.approx(-10.0, abs=0.1)
	assert report["files"]["far.ogg"]["quiet_plays"] == 1


def test_probe_counts_a_restarted_sound_as_a_new_play():
	p = probe_mod.AudioProbe()
	v = constant_voice(0.5)
	v.label = "step.ogg"
	block = np.zeros((10, 2), dtype=np.float32)
	v.pos = 100
	p.sample([v], 1.0, block, 0.0)
	v.pos = 0  # play() again
	p.sample([v], 1.0, block, 0.0)
	assert p.report()["files"]["step.ogg"]["plays"] == 2


def test_probe_sections_measure_the_mix():
	p = probe_mod.AudioProbe()
	loud = np.full((10, 2), 1.5, dtype=np.float32)
	p.sample([], 1.0, loud, 1.5)
	p.section("Level 2")
	p.sample([], 1.0, loud * 0.1, 0.15)
	sections = p.report()["sections"]
	assert [s["name"] for s in sections] == ["start", "Level 2"]
	assert sections[0]["clip_blocks"] == 1
	assert sections[1]["peak_dbfs"] == pytest.approx(-16.5, abs=0.1)


def test_probe_writes_json(tmp_path):
	import json
	p = probe_mod.AudioProbe(str(tmp_path / "probe.json"))
	p.write()
	data = json.loads((tmp_path / "probe.json").read_text())
	assert set(data) >= {"sections", "files", "failed_loads", "quiet_db"}


def test_failed_loads_are_collected_and_reported_under_selftest(capsys, monkeypatch):
	probe_mod.clear_failed_loads()
	snd = lucia.audio_backend.Sound() if lucia.audio_backend else None
	if snd is None:
		from lucia.audio.miniaudio.sound import Sound
		snd = Sound()
	assert snd.load("sounds/vo/rival_taunt99.ogg") is False
	assert snd.load("sounds/vo/rival_taunt99.ogg") is False
	assert lucia.audio.failed_loads() == ["sounds/vo/rival_taunt99.ogg"]
	monkeypatch.setenv("LUCIA_SELFTEST", "1")
	probe_mod._report_failed_loads_at_exit()
	assert "rival_taunt99.ogg" in capsys.readouterr().err
	probe_mod.clear_failed_loads()


# -- pool: floor and instance cap -------------------------------------------

def test_distance_is_manhattan_and_floor_keeps_mechanics_audible(engine, tmp_path):
	path = _tone(tmp_path / "breath.wav")
	pool = lucia.audio_backend.SoundPool(max_distance=100, pan_step=2, volume_step=2)
	plain = pool.play_2d(path, 0, 0, 10, 10, False)
	assert plain.handle.volume == pytest.approx(-40)  # 20 units, not 14
	far = pool.play_2d(path, 0, 0, 40, 0, False)
	assert far.handle.volume == pytest.approx(-80)
	floored = pool.play_2d(path, 0, 0, 40, 0, False, min_volume=-30)
	assert floored.handle.volume == pytest.approx(-30)
	pool.update_listener_2d(35, 0)  # close: distance shapes it again
	assert floored.handle.volume == pytest.approx(-10)
	pool.destroy_all()


def test_floor_never_exceeds_start_volume(engine, tmp_path):
	path = _tone(tmp_path / "knock.wav")
	pool = lucia.audio_backend.SoundPool(max_distance=100, pan_step=2, volume_step=2)
	slot = pool.play_extended_2d(
		path, 0, 0, 30, 0, 0, 0, 0, 0, False, 0, 0, -40, 100, min_volume=-20)
	assert slot.handle.volume == pytest.approx(-40)
	assert pool.set_min_volume(slot, None) is True
	assert slot.handle.volume == pytest.approx(-100)
	pool.destroy_all()


def test_max_same_file_refuses_extra_copies(engine, tmp_path):
	windup = _tone(tmp_path / "windup.wav")
	other = _tone(tmp_path / "other.wav")
	pool = lucia.audio_backend.SoundPool(max_distance=100, max_same_file=2)
	a = pool.play_2d(windup, 0, 0, 1, 0, False)
	b = pool.play_2d(windup, 0, 0, 2, 0, False)
	c = pool.play_2d(windup, 0, 0, 3, 0, False)
	assert a and b
	assert not c and c == lucia.audio.miniaudio.soundpool.TOO_MANY_INSTANCES
	assert pool.playing_count(windup) == 2
	assert pool.play_2d(other, 0, 0, 1, 0, False)
	pool.destroy_sound(a)
	assert pool.play_2d(windup, 0, 0, 1, 0, False)
	# loops are never refused, but they do count: the same file as
	# ambience AND as a one-shot is a doubling too
	assert pool.play_stationary(windup, looping=True)
	assert pool.playing_count(windup) == 3
	assert not pool.play_2d(windup, 0, 0, 1, 0, False)
	pool.destroy_all()


def test_max_same_file_is_off_by_default(engine, tmp_path):
	windup = _tone(tmp_path / "windup.wav")
	pool = lucia.audio_backend.SoundPool(max_distance=100)
	assert all(pool.play_2d(windup, 0, 0, 1, 0, False) for _ in range(6))
	pool.destroy_all()


def test_module_meter_functions(engine):
	lucia.audio.reset_meter()
	assert lucia.audio.clip_count() == 0
	assert lucia.audio.peak_dbfs() <= 0
	assert lucia.audio_backend.peak_dbfs() <= 0
	assert lucia.audio.get_limiter() is False
	lucia.audio.set_limiter(True)
	assert lucia.audio.get_limiter() is True
	lucia.audio.set_limiter(False)


# -- voice channel ----------------------------------------------------------

class FakeLine:
	def __init__(self, name):
		self.name = name
		self.is_playing = True

	def stop(self):
		self.is_playing = False


def test_voice_channel_drops_equal_or_lower_rank():
	ch = VoiceChannel()
	ouch = ch.say("hero", VoiceChannel.REACTION, lambda: FakeLine("ouch"))
	assert ouch
	assert ch.say("hero", VoiceChannel.REACTION, lambda: FakeLine("ouch2")) is None
	assert ch.dropped == 1
	assert ouch.is_playing


def test_voice_channel_higher_rank_interrupts_and_taunts_get_through():
	ch = VoiceChannel()
	heard = []
	for hit in range(20):
		# hero groans on every hit, rival taunts every fifth frame
		ch.say("hero", VoiceChannel.REACTION, lambda: FakeLine("ouch"))
		if hit % 5 == 0:
			line = ch.say("rival", VoiceChannel.TAUNT, lambda: FakeLine("taunt"))
			if line:
				heard.append(line)
				line.is_playing = False  # finishes before the next hit
	assert len(heard) == 4  # "only when free" would have let none through


def test_voice_channel_rank_before_speaker():
	ch = VoiceChannel()
	aside = ch.say("saskia", VoiceChannel.REACTION, lambda: FakeLine("hmpf"))
	help_call = ch.say("saskia", VoiceChannel.STORY, lambda: FakeLine("help"))
	assert help_call and not aside.is_playing
	assert ch.speaker == "saskia" and ch.priority == VoiceChannel.STORY
	assert ch.interrupted == 1


def test_voice_channel_failed_start_keeps_the_running_line():
	ch = VoiceChannel()
	running = ch.say("a", VoiceChannel.REACTION, lambda: FakeLine("x"))
	assert ch.say("b", VoiceChannel.STORY, lambda: None) is None
	assert running.is_playing and ch.speaker == "a"


def test_voice_channel_frees_itself_when_the_line_ends():
	ch = VoiceChannel()
	line = ch.say("a", VoiceChannel.STORY, lambda: FakeLine("x"))
	line.is_playing = False
	assert not ch.busy
	assert ch.can_say(VoiceChannel.REACTION)


def test_voice_channel_with_a_pool(engine, tmp_path):
	path = _tone(tmp_path / "line.wav")
	pool = lucia.audio_backend.SoundPool(max_distance=100)
	ch = VoiceChannel(pool)
	first = ch.say("a", VoiceChannel.TAUNT, lambda: pool.play_2d(path, 0, 0, 1, 0, False))
	assert ch.busy
	second = ch.say("b", VoiceChannel.STORY, lambda: pool.play_2d(path, 0, 0, 2, 0, False))
	assert second and not pool.sound_is_playing(first)
	ch.stop()
	assert not ch.busy and not pool.sound_is_playing(second)
	pool.destroy_all()


def test_probe_marks_music_and_counts_only_audible_voices():
	p = probe_mod.AudioProbe()
	music = constant_voice(0.5)
	music.label = "music/boss.mp3"
	music.category = "music"
	music.amp = 0.0  # fade-in not started yet
	loop = constant_voice(0.5)
	loop.label = "far_loop.ogg"
	loop.amp = 0.0  # -100 dB: out of reach
	near = constant_voice(0.5)
	near.label = "near.ogg"
	block = np.zeros((10, 2), dtype=np.float32)
	p.sample([music, loop, near], 1.0, block, 0.0)
	report = p.report()
	assert report["sections"][0]["max_voices"] == 1
	assert report["files"]["music/boss.mp3"]["category"] == "music"
	assert report["files"]["far_loop.ogg"]["category"] is None


def test_music_manager_tags_its_voice(engine, tmp_path):
	from lucia.audio import MusicManager
	music = MusicManager(volume=-20)
	assert music.play(_tone(tmp_path / "theme.wav"))
	assert music._current.get_source_object().category == "music"
	music.stop()
