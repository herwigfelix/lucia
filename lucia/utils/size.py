# Copyright (C) 2026  LuciaSoftware and it's contributors.
#
# Modeled after NVGT's size include.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Byte size to human readable string conversion (and back)."""

_UNITS = ["bytes", "KB", "MB", "GB", "TB", "PB"]


def size_to_string(size, decimals=2):
	"""Convert a byte count to a human readable string, e.g. "1.5 MB"."""
	size = float(size)
	for unit in _UNITS:
		if size < 1024.0 or unit == _UNITS[-1]:
			if unit == "bytes":
				return "%d %s" % (int(size), unit)
			return ("%." + str(decimals) + "f %s") % (size, unit)
		size /= 1024.0


def string_to_size(text):
	"""Parse a string like "1.5 MB" back into a byte count."""
	parts = text.strip().split()
	if len(parts) == 1:
		return int(float(parts[0]))
	value = float(parts[0])
	unit = parts[1].upper()
	multipliers = {"BYTES": 1, "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
	return int(value * multipliers.get(unit, 1))
