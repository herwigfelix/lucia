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

"""Game-friendly random helpers in the BGT/NVGT tradition.

The module functions share one default generator:

	lucia.utils.random_range(1, 6)      # inclusive on BOTH ends
	lucia.utils.random_bool(25)         # True 25% of the time
	lucia.utils.random_choice(items)    # None when items is empty
	lucia.utils.weighted_choice(items, weights)
	lucia.utils.roll_dice(2, 6)         # 2d6, returns the total
	lucia.utils.random_seed(42)         # reproducible runs

For independent, seedable streams (procedural levels, replays) create a
`RandomGenerator` and use the same methods on it.
"""

import random as _random


class RandomGenerator:
	"""A seedable random generator with its own independent state."""

	def __init__(self, seed=None):
		self._rng = _random.Random(seed)

	def seed(self, seed=None):
		"""Reseed the generator. None seeds from the OS."""
		self._rng.seed(seed)

	def get_state(self):
		"""An opaque, pickleable snapshot of the generator state."""
		return self._rng.getstate()

	def set_state(self, state):
		"""Restore a snapshot from get_state(). Returns False on invalid state."""
		try:
			self._rng.setstate(state)
			return True
		except (TypeError, ValueError):
			return False

	def range(self, minimum, maximum):
		"""A random integer between minimum and maximum, BOTH inclusive."""
		if minimum > maximum:
			minimum, maximum = maximum, minimum
		return self._rng.randint(minimum, maximum)

	def uniform(self, minimum=0.0, maximum=1.0):
		"""A random float between minimum and maximum."""
		return self._rng.uniform(minimum, maximum)

	def chance(self, percent=50):
		"""True `percent` percent of the time (0 = never, 100 = always)."""
		if percent <= 0:
			return False
		if percent >= 100:
			return True
		return self._rng.random() * 100.0 < percent

	def choice(self, items):
		"""A random element of items, or None when items is empty."""
		if not items:
			return None
		return self._rng.choice(items)

	def weighted_choice(self, items, weights):
		"""A random element of items, picked with the given relative weights.

		Returns None when items is empty, the lengths differ or all
		weights are zero.
		"""
		if not items or weights is None or len(items) != len(weights):
			return None
		try:
			return self._rng.choices(items, weights=weights, k=1)[0]
		except (ValueError, TypeError):
			return None

	def shuffle(self, items):
		"""Shuffle a mutable sequence in place."""
		self._rng.shuffle(items)

	def shuffled(self, items):
		"""A new shuffled list; the original sequence is left untouched."""
		result = list(items)
		self._rng.shuffle(result)
		return result

	def roll_dice(self, dice=1, sides=6):
		"""Roll `dice` dice with `sides` sides each and return the total."""
		if dice <= 0 or sides <= 0:
			return 0
		return sum(self._rng.randint(1, sides) for _ in range(dice))


_default = RandomGenerator()


def random_range(minimum, maximum):
	"""A random integer between minimum and maximum, BOTH inclusive."""
	return _default.range(minimum, maximum)


def random_float(minimum=0.0, maximum=1.0):
	"""A random float between minimum and maximum."""
	return _default.uniform(minimum, maximum)


def random_bool(percent=50):
	"""True `percent` percent of the time (0 = never, 100 = always)."""
	return _default.chance(percent)


def random_choice(items):
	"""A random element of items, or None when items is empty."""
	return _default.choice(items)


def weighted_choice(items, weights):
	"""A random element of items, picked with the given relative weights."""
	return _default.weighted_choice(items, weights)


def shuffle(items):
	"""Shuffle a mutable sequence in place."""
	_default.shuffle(items)


def shuffled(items):
	"""A new shuffled list; the original sequence is left untouched."""
	return _default.shuffled(items)


def roll_dice(dice=1, sides=6):
	"""Roll `dice` dice with `sides` sides each and return the total."""
	return _default.roll_dice(dice, sides)


def random_seed(seed=None):
	"""Reseed the shared default generator. None seeds from the OS."""
	_default.seed(seed)


def random_get_state():
	"""Snapshot of the shared default generator state."""
	return _default.get_state()


def random_set_state(state):
	"""Restore a snapshot from random_get_state(). Returns False on invalid state."""
	return _default.set_state(state)
