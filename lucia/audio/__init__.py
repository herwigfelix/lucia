# Copyright (C) 2018  LuciaSoftware and it's contributors.
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

from .backend import *
from .sound import *
from .soundpool import *
from .music import MusicManager
from .voice_channel import VoiceChannel
from .probe import failed_loads, clear_failed_loads
from .probe import section as probe_section
from .synth import (
	ToneSynth,
	WAVEFORM_SINE,
	WAVEFORM_SQUARE,
	WAVEFORM_TRIANGLE,
	WAVEFORM_SAWTOOTH,
	WAVEFORM_NOISE,
	note_to_frequency,
)


def _backend_or_raise():
	import lucia

	if lucia.audio_backend_class is None:
		raise RuntimeError("call lucia.initialize() first")
	return lucia.audio_backend_class


def set_master_volume(db):
	"""Global volume in dB (0 full .. -100 silent), applied on top of every
	sound's own volume — the knob for an options-menu volume slider."""
	_backend_or_raise().set_master_volume(db)


def get_master_volume():
	"""The current global volume in dB."""
	return _backend_or_raise().get_master_volume()


def get_output_devices():
	"""The names of all playback devices; the index is what you pass to
	lucia.initialize(audio_device=...) or set_output_device()."""
	return _backend_or_raise().get_output_devices()


def set_output_device(index):
	"""Switch playback to another output device at runtime; playing
	sounds carry on where they are."""
	_backend_or_raise().set_output_device(index)


def get_capture_devices():
	"""The names of all recording (microphone) devices."""
	return _backend_or_raise().get_capture_devices()


def peak_dbfs():
	"""Loudest sample of the whole mix since reset_meter(), in dBFS.

	Measured before the limiter and the final clip. A self test can use it
	as a hard limit: after a few real-time seconds of a busy scene,
	`assert lucia.audio.peak_dbfs() <= -3`. Above 0 the output clipped.
	-200 when nothing has played yet.
	"""
	return _backend_or_raise().get_meter().peak_dbfs()


def rms_dbfs():
	"""Average (RMS) level of the mix since reset_meter(), in dBFS."""
	return _backend_or_raise().get_meter().rms_dbfs()


def clip_count():
	"""Audio blocks (~20 ms each) in which the mix went over 0 dBFS."""
	return _backend_or_raise().get_meter().clip_count()


def reset_meter():
	"""Start a new measurement window for peak_dbfs/rms_dbfs/clip_count."""
	_backend_or_raise().get_meter().reset_meter()


def set_limiter(enabled, ceiling_db=-1.0):
	"""Switch the soft master limiter on or off (off by default).

	It keeps a rare pile-up of sounds from clipping hard, by riding the
	master gain down and bending the rest softly under ceiling_db. It is a
	safety net, NOT a substitute for a mixing budget: base volumes that are
	too hot stay too hot, just squashed instead of clipped. Check
	peak_dbfs() with the limiter's help in mind: it measures the mix
	before the limiter, so it still tells you when volumes are wrong.
	"""
	_backend_or_raise().get_meter().set_limiter(enabled, ceiling_db)


def get_limiter():
	"""Whether the master limiter is on."""
	return _backend_or_raise().get_meter().limiter
