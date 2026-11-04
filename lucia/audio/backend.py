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

from abc import ABC, abstractmethod


class AudioBackend(ABC):
	@abstractmethod
	def initialize(self):
		pass

	@abstractmethod
	def update_audio_system(self):
		pass

	@abstractmethod
	def quit(self):
		pass

	@abstractmethod
	def is_hrtf_compatible(self):
		pass

	@abstractmethod
	def enable_hrtf(self, should_enable):
		pass

	# Optional capabilities: backends override what they support; the
	# defaults raise so callers can catch BackActionNotSupported.

	def set_master_volume(self, db):
		"""Global volume in dB (0 full .. -100 silent), on top of per-sound volumes."""
		raise BackActionNotSupported("This backend does not support a master volume.")

	def get_master_volume(self):
		raise BackActionNotSupported("This backend does not support a master volume.")

	def get_output_devices(self):
		"""The names of all playback devices; the index is the selection index."""
		raise BackActionNotSupported("This backend does not support device enumeration.")

	def set_output_device(self, index):
		"""Switch playback to another output device at runtime."""
		raise BackActionNotSupported("This backend does not support device switching.")

	def get_capture_devices(self):
		"""The names of all recording devices."""
		raise BackActionNotSupported("This backend does not support device enumeration.")

	def get_meter(self):
		"""The object with peak_dbfs()/rms_dbfs()/clip_count()/reset_meter()/set_limiter()."""
		raise BackActionNotSupported("This backend does not support output metering.")


class BackActionNotSupported(ValueError):
	pass
