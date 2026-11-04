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

"""The miniaudio audio backend (default).

Cross-platform, no external DLLs required: decoding and playback are
done by the bundled miniaudio library (via the `miniaudio` pip package),
mixing/panning/pitching by lucia's own software mixer.
"""

import lucia
from . import devices, mixer
from .recorder import Recorder
from .sound import Sound
from .soundpool import SoundPool, SoundPoolItem


class MiniaudioAudioBackend(lucia.audio.AudioBackend):
	def __init__(self, buffer_msec=None, device=None):
		"""buffer_msec: audio latency in milliseconds (default 20). Raise it
		only if a slow machine crackles; lower it for tighter timing.
		device: index into get_output_devices(), None = system default."""
		self.buffer_msec = buffer_msec
		self.device_index = device

	def initialize(self):
		device_id, devices_ref = devices.resolve_playback_device(self.device_index)
		mixer.get_mixer(self.buffer_msec, device_id, devices_ref)

	def update_audio_system(self):
		pass

	def quit(self):
		mixer.shutdown_mixer()

	def is_hrtf_compatible(self):
		return False

	def enable_hrtf(self, should_enable):
		raise lucia.audio.BackActionNotSupported(
			"The miniaudio backend does not support HRTF."
		)

	def set_master_volume(self, db):
		mixer.get_mixer().set_master_volume(db)

	def get_master_volume(self):
		return mixer.get_mixer().get_master_volume()

	def get_output_devices(self):
		return devices.get_playback_devices()

	def set_output_device(self, index):
		device_id, devices_ref = devices.resolve_playback_device(index)
		mixer.get_mixer().set_device(device_id, devices_ref)
		self.device_index = index

	def get_capture_devices(self):
		return devices.get_capture_devices()

	def get_meter(self):
		return mixer.get_mixer()


# Output metering and the opt-in limiter, also reachable as
# lucia.audio_backend.peak_dbfs() etc. (the same as lucia.audio.peak_dbfs()).

def peak_dbfs():
	return mixer.get_mixer().peak_dbfs()


def rms_dbfs():
	return mixer.get_mixer().rms_dbfs()


def clip_count():
	return mixer.get_mixer().clip_count()


def reset_meter():
	mixer.get_mixer().reset_meter()


def set_limiter(enabled, ceiling_db=-1.0):
	mixer.get_mixer().set_limiter(enabled, ceiling_db)
