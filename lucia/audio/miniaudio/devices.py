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

"""Audio device enumeration for the miniaudio backend.

The device ids handed out by miniaudio.Devices are CFFI pointers that
are only valid while the Devices object is alive. resolve_* therefore
returns (device_id, devices_obj); the caller must keep devices_obj
referenced for as long as it uses the id (the mixer stores it as
_devices_ref).
"""

import miniaudio as _ma


def get_playback_devices():
	"""The names of all playback devices; the index is the selection index."""
	try:
		return [d["name"] for d in _ma.Devices().get_playbacks()]
	except Exception:
		return []


def get_capture_devices():
	"""The names of all recording (microphone) devices."""
	try:
		return [d["name"] for d in _ma.Devices().get_captures()]
	except Exception:
		return []


def resolve_playback_device(index):
	"""Map a selection index (or None = default) to (device_id, devices_obj).

	Raises IndexError for an out-of-range index.
	"""
	return _resolve(index, "get_playbacks")


def resolve_capture_device(index):
	"""Map a selection index (or None = default) to (device_id, devices_obj)."""
	return _resolve(index, "get_captures")


def _resolve(index, getter):
	if index is None:
		return None, None
	devices = _ma.Devices()
	entries = getattr(devices, getter)()
	if not 0 <= index < len(entries):
		raise IndexError(
			"audio device index %r out of range (%d devices)" % (index, len(entries))
		)
	return entries[index]["id"], devices
