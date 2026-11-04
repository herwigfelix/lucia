"""Tests for the callback-based A* pathfinder and Map.find_path."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.pathfinder import Pathfinder


def open_grid(width, height, blocked=()):
	blocked = set(blocked)

	def cost(x, y, z):
		if not (0 <= x < width and 0 <= y < height):
			return None
		if (x, y) in blocked:
			return None
		return 0

	return cost


def test_straight_corridor():
	finder = Pathfinder(open_grid(10, 1), dimension=2)
	path = finder.find((0, 0), (9, 0))
	assert len(path) == 10
	assert path[0] == (0, 0, 0)
	assert path[-1] == (9, 0, 0)
	assert finder.total_cost == 9


def test_detour_around_wall():
	# A wall column with a gap only at the top.
	blocked = {(5, y) for y in range(0, 9)}
	finder = Pathfinder(open_grid(11, 10, blocked), dimension=2)
	path = finder.find((0, 0), (10, 0))
	assert path, "a path through the gap must exist"
	assert all((x, y) not in blocked for x, y, _ in path)
	assert any(y == 9 for _, y, _ in path), "must route through the gap"


def test_no_path():
	blocked = {(5, y) for y in range(0, 10)}
	finder = Pathfinder(open_grid(11, 10, blocked), dimension=2)
	assert finder.find((0, 0), (10, 0)) == []


def test_blocked_start_or_goal():
	finder = Pathfinder(open_grid(5, 5, blocked={(0, 0)}), dimension=2)
	assert finder.find((0, 0), (4, 4)) == []
	finder = Pathfinder(open_grid(5, 5, blocked={(4, 4)}), dimension=2)
	assert finder.find((0, 0), (4, 4)) == []


def test_start_equals_goal():
	finder = Pathfinder(open_grid(3, 3), dimension=2)
	assert finder.find((1, 1), (1, 1)) == [(1, 1, 0)]


def test_diagonals_shorten_paths():
	straight = Pathfinder(open_grid(10, 10), dimension=2)
	diagonal = Pathfinder(open_grid(10, 10), allow_diagonals=True, dimension=2)
	assert len(straight.find((0, 0), (9, 9))) == 19
	assert len(diagonal.find((0, 0), (9, 9))) == 10


def test_weighted_cells_are_avoided():
	# A swamp on the direct route: crossing costs far more than walking around.
	def cost(x, y, z):
		if not (0 <= x < 10 and 0 <= y < 3):
			return None
		if y == 0 and 2 <= x <= 7:
			return 10
		return 0

	finder = Pathfinder(cost, dimension=2)
	path = finder.find((0, 0), (9, 0))
	assert path
	assert all(not (y == 0 and 2 <= x <= 7) for x, y, _ in path)


def test_search_range_fails_fast():
	calls = []

	def cost(x, y, z):
		calls.append((x, y))
		return 0

	finder = Pathfinder(cost, search_range=5, dimension=2)
	assert finder.find((0, 0), (50, 0)) == []
	assert calls == [], "an out-of-range goal must not expand anything"


def test_max_expansions_aborts():
	finder = Pathfinder(open_grid(100, 100), max_expansions=5, dimension=2)
	assert finder.find((0, 0), (99, 99)) == []


def test_callback_called_once_per_cell():
	counts = {}

	def cost(x, y, z):
		counts[(x, y)] = counts.get((x, y), 0) + 1
		return 0 if 0 <= x < 6 and 0 <= y < 6 else None

	Pathfinder(cost, dimension=2).find((0, 0), (5, 5))
	assert max(counts.values()) == 1


def test_cancel_from_callback():
	finder = Pathfinder(dimension=2)

	def cost(x, y, z):
		if x > 3:
			finder.cancel()
		return 0 if 0 <= x < 100 and 0 <= y < 100 else None

	assert finder.find((0, 0), (99, 0), callback=cost) == []


def test_one_dimensional():
	def cost(x, y, z):
		return 0 if 0 <= x <= 5 else None

	finder = Pathfinder(cost, dimension=1)
	path = finder.find((0,), (5,))
	assert path == [(x, 0, 0) for x in range(6)]


def test_three_dimensional():
	def cost(x, y, z):
		return 0 if 0 <= x <= 2 and 0 <= y <= 2 and 0 <= z <= 2 else None

	finder = Pathfinder(cost, dimension=3)
	path = finder.find((0, 0, 0), (2, 2, 2))
	assert path[0] == (0, 0, 0)
	assert path[-1] == (2, 2, 2)
	assert len(path) == 7  # 6 orthogonal steps


def test_map_find_path_routes_around_walls():
	m = lucia.Map(dimension=2)
	m.maxx = m.maxy = 10
	m.add_tile(0, 10, 0, 10, 0, 0, "grass")
	m.add_tile(5, 5, 0, 8, 0, 0, "wall")
	path = m.find_path((0, 0), (10, 0))
	assert path
	assert all(m.is_walkable(x, y, z) for x, y, z in path)
	assert any(y > 8 for _, y, _ in path)


def test_map_find_path_respects_doors():
	m = lucia.Map(dimension=2)
	m.maxx = 10
	m.maxy = 2
	m.add_tile(0, 10, 0, 2, 0, 0, "stone")
	m.add_tile(5, 5, 0, 2, 0, 0, "wall")
	m.add_door(5, 1, 0)
	assert m.find_path((0, 1), (10, 1)) == []  # door starts closed
	m.doors[0].open = True
	path = m.find_path((0, 1), (10, 1))
	assert path
	assert (5, 1, 0) in path


def test_map_find_path_ignores_z_in_2d():
	m = lucia.Map(dimension=2)
	m.maxx = m.maxy = 3
	m.add_tile(0, 3, 0, 3, 0, 0, "grass")
	path = m.find_path((0, 0, 7), (3, 0, 9))
	assert path
	assert all(z == 0 for _, _, z in path)
