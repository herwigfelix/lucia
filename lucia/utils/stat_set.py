# Copyright (C) 2026  LuciaSoftware and it's contributors.
#
# Modeled after NVGT's stat_set include.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Statistic management for game characters and objects.

	stats = lucia.utils.StatSet()
	stats.add("health", 100, minimum=0, maximum=100)
	stats.add("gold", 0, minimum=0)
	stats.change("health", -30)
	if stats.get("health") <= 0: ...

An optional callback fires on every change:
	stats.add("health", 100, 0, 100, on_change=lambda stat, old, new: ...)
"""


class Stat:
	def __init__(self, name, value=0, minimum=None, maximum=None, on_change=None):
		self.name = name
		self.minimum = minimum
		self.maximum = maximum
		self.on_change = on_change
		self._value = self._clamp(value)

	def _clamp(self, value):
		if self.minimum is not None and value < self.minimum:
			value = self.minimum
		if self.maximum is not None and value > self.maximum:
			value = self.maximum
		return value

	@property
	def value(self):
		return self._value

	@value.setter
	def value(self, new):
		old = self._value
		self._value = self._clamp(new)
		if self._value != old and self.on_change:
			self.on_change(self, old, self._value)

	@property
	def at_minimum(self):
		return self.minimum is not None and self._value <= self.minimum

	@property
	def at_maximum(self):
		return self.maximum is not None and self._value >= self.maximum


class StatSet:
	def __init__(self):
		self._stats = {}

	def add(self, name, value=0, minimum=None, maximum=None, on_change=None):
		stat = Stat(name, value, minimum, maximum, on_change)
		self._stats[name] = stat
		return stat

	def remove(self, name):
		return self._stats.pop(name, None) is not None

	def exists(self, name):
		return name in self._stats

	def stat(self, name):
		return self._stats[name]

	def get(self, name, default=0):
		stat = self._stats.get(name)
		return stat.value if stat else default

	def set(self, name, value):
		self._stats[name].value = value

	def change(self, name, delta):
		"""Add delta to a stat (negative to subtract). Returns the new value."""
		stat = self._stats[name]
		stat.value = stat.value + delta
		return stat.value

	def names(self):
		return list(self._stats.keys())

	def __getitem__(self, name):
		return self.get(name)

	def __setitem__(self, name, value):
		if name in self._stats:
			self.set(name, value)
		else:
			self.add(name, value)

	def __contains__(self, name):
		return name in self._stats
