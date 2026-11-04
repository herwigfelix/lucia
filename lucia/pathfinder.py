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

"""Callback-based A* pathfinding on integer grids (NVGT pathfinder model).

The cost callback is asked once per cell and returns:

	None (or a negative number)  the cell is blocked
	0                            the cell is free (base cost 1 per step)
	> 0                          extra cost added on top of the base cost
	                             (swamps, stairs, dangerous ground ...)

	def cost(x, y, z):
		return 0 if game_map.is_walkable(x, y, z) else None

	finder = lucia.Pathfinder(cost)
	path = finder.find((1, 1), (10, 4))  # [(x, y, z), ...] or []

For tile maps, `Map.find_path()` wraps this with `is_walkable` as the
default callback and the map's dimension already applied.
"""

import heapq
import math

_SQRT2 = math.sqrt(2.0)
_SQRT3 = math.sqrt(3.0)


def _neighbor_offsets(dimension, allow_diagonals):
	offsets = []
	if dimension == 1:
		xr, yr, zr = (-1, 0, 1), (0,), (0,)
	elif dimension == 2:
		xr, yr, zr = (-1, 0, 1), (-1, 0, 1), (0,)
	else:
		xr, yr, zr = (-1, 0, 1), (-1, 0, 1), (-1, 0, 1)
	for dx in xr:
		for dy in yr:
			for dz in zr:
				if dx == 0 and dy == 0 and dz == 0:
					continue
				axes = (dx != 0) + (dy != 0) + (dz != 0)
				if not allow_diagonals and axes > 1:
					continue
				if axes == 1:
					step = 1.0
				elif axes == 2:
					step = _SQRT2
				else:
					step = _SQRT3
				offsets.append((dx, dy, dz, step))
	return offsets


class Pathfinder:
	"""A* pathfinder over an arbitrary cost callback.

	callback(x, y, z) is queried at most once per cell per find() and
	returns None/negative for blocked, 0 for free, or extra cost > 0.
	"""

	def __init__(self, callback=None, allow_diagonals=False, search_range=0, max_expansions=0, dimension=3):
		self.callback = callback
		self.allow_diagonals = allow_diagonals
		# Maximum start-to-goal distance; anything farther fails fast. 0 = unlimited.
		self.search_range = search_range
		# Node-expansion cap against frame hangs on huge maps. 0 = unlimited.
		self.max_expansions = max_expansions
		self.dimension = dimension
		self.total_cost = 0.0
		self._cancelled = False

	def cancel(self):
		"""Abort the current find() (callable from inside the cost callback)."""
		self._cancelled = True

	def find(self, start, goal, callback=None):
		"""The cheapest path from start to goal, both included.

		start and goal are (x,), (x, y) or (x, y, z) tuples; missing axes
		are treated as 0. Returns a list of (x, y, z) tuples, or [] when
		no path exists, a limit was hit or cancel() was called.
		"""
		callback = callback if callback is not None else self.callback
		if callback is None:
			raise ValueError("Pathfinder needs a cost callback")
		start = self._pad(start)
		goal = self._pad(goal)
		self.total_cost = 0.0
		self._cancelled = False
		if self.search_range and self._distance(start, goal) > self.search_range:
			return []
		cost_cache = {}

		def cell_cost(pos):
			cached = cost_cache.get(pos)
			if cached is None and pos not in cost_cache:
				raw = callback(pos[0], pos[1], pos[2])
				cached = None if (raw is None or raw < 0) else float(raw)
				cost_cache[pos] = cached
			return cached

		if cell_cost(start) is None or cell_cost(goal) is None:
			return []
		if start == goal:
			return [start]
		offsets = _neighbor_offsets(self.dimension, self.allow_diagonals)
		open_heap = [(self._heuristic(start, goal), 0.0, start)]
		g_scores = {start: 0.0}
		came_from = {}
		closed = set()
		expansions = 0
		while open_heap:
			if self._cancelled:
				return []
			_, g, current = heapq.heappop(open_heap)
			if current in closed:
				continue
			if current == goal:
				self.total_cost = g
				return self._rebuild(came_from, current)
			closed.add(current)
			expansions += 1
			if self.max_expansions and expansions > self.max_expansions:
				return []
			for dx, dy, dz, step in offsets:
				neighbor = (current[0] + dx, current[1] + dy, current[2] + dz)
				if neighbor in closed:
					continue
				if self.search_range and self._distance(start, neighbor) > self.search_range:
					continue
				extra = cell_cost(neighbor)
				if extra is None:
					continue
				new_g = g + step + extra
				if new_g >= g_scores.get(neighbor, math.inf):
					continue
				g_scores[neighbor] = new_g
				came_from[neighbor] = current
				heapq.heappush(open_heap, (new_g + self._heuristic(neighbor, goal), new_g, neighbor))
		return []

	def _pad(self, pos):
		pos = tuple(int(v) for v in pos)
		return pos + (0,) * (3 - len(pos))

	def _distance(self, a, b):
		dx, dy, dz = abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])
		if self.allow_diagonals:
			return math.sqrt(dx * dx + dy * dy + dz * dz)
		return dx + dy + dz

	def _heuristic(self, a, b):
		dx, dy, dz = abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])
		if not self.allow_diagonals:
			return dx + dy + dz
		# Octile-style: pay the diagonal rate for shared steps, 1 for the rest.
		lo, mid, hi = sorted((dx, dy, dz))
		return lo * _SQRT3 + (mid - lo) * _SQRT2 + (hi - mid)

	def _rebuild(self, came_from, current):
		path = [current]
		while current in came_from:
			current = came_from[current]
			path.append(current)
		path.reverse()
		return path
