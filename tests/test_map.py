import os

import pytest

import lucia
from lucia.map import Map, MapWalker

MAP_TEXT = """
# a small test map
name:Test Park
dimension:2
size:20:10:0
start:1:1:0
tile:0:20:0:10:0:0:grass
tile:5:5:0:10:0:0:wallbrick
tile:10:14:2:6:0:0:concrete
zone:10:14:2:6:0:0:courtyard
source:18:18:5:5:0:0:sounds/fountain.ogg:1:-12
door:5:0:0:sounds/dooropen1.ogg:sounds/doorclose1.ogg:0
"""


class FakePool:
	"""Records SoundPool calls so tests can assert on the audio."""

	def __init__(self):
		self.calls = []
		self.listener = None

	def _slot(self, name):
		class Slot:
			def __init__(self):
				class Handle:
					volume = 0
					is_active = True
					is_playing = True

				self.handle = Handle()
				self.name = name

		return Slot()

	def play_stationary(self, path, looping=False, persistent=False):
		self.calls.append(("stationary", path, looping))
		return self._slot(path)

	def play_1d(self, path, lx, sx, looping=False, persistent=False):
		self.calls.append(("1d", path, sx, looping))
		return self._slot(path)

	def play_2d(self, path, lx, ly, sx, sy, looping=False, persistent=False):
		self.calls.append(("2d", path, sx, sy, looping))
		return self._slot(path)

	def play_3d(self, path, lx, ly, lz, sx, sy, sz, rotation=0, looping=False, **kw):
		self.calls.append(("3d", path, sx, sy, sz, looping))
		return self._slot(path)

	def update_listener_1d(self, x):
		self.listener = ("1d", x)

	def update_listener_2d(self, x, y, rotation=0):
		self.listener = ("2d", x, y, rotation)

	def update_listener_3d(self, x, y, z, rotation=0):
		self.listener = ("3d", x, y, z, rotation)

	def destroy_sound(self, slot):
		self.calls.append(("destroy", slot.name))
		return True

	def played(self, kind=None):
		return [c for c in self.calls if kind is None or c[0] == kind]

	def clear(self):
		self.calls = []


def test_parse_map():
	game_map = Map.loads(MAP_TEXT)
	assert game_map.name == "Test Park"
	assert game_map.dimension == 2
	assert game_map.maxx == 20 and game_map.maxy == 10
	assert game_map.start == (1, 1, 0)
	assert len(game_map.tiles) == 3
	assert len(game_map.zones) == 1
	assert len(game_map.sources) == 1
	assert len(game_map.doors) == 1
	source = game_map.sources[0]
	assert source.sound == "sounds/fountain.ogg"
	assert source.looping and source.volume == -12


def test_tile_lookup_and_layering():
	game_map = Map.loads(MAP_TEXT)
	assert game_map.tile_at(1, 1) == "grass"
	# later tiles override earlier ones
	assert game_map.tile_at(12, 4) == "concrete"
	assert game_map.tile_at(5, 3) == "wallbrick"
	assert game_map.is_wall(5, 3)
	assert not game_map.is_wall(1, 1)
	# outside every tile
	assert game_map.tile_at(50, 50) == ""


def test_walkable_and_bounds():
	game_map = Map.loads(MAP_TEXT)
	assert game_map.is_walkable(1, 1)
	assert not game_map.is_walkable(5, 3)  # wall
	assert not game_map.is_walkable(-1, 1)  # outside
	assert not game_map.is_walkable(1, 99)  # outside
	assert not game_map.is_walkable(50, 5)  # no tile
	# closed door blocks
	assert not game_map.is_walkable(5, 0)


def test_zone_and_door_lookup():
	game_map = Map.loads(MAP_TEXT)
	assert game_map.zone_at(12, 4).name == "courtyard"
	assert game_map.zone_at(1, 1) is None
	assert game_map.door_at(5, 0) is not None
	assert game_map.door_at(1, 1) is None


def test_roundtrip_serialization(tmp_path):
	game_map = Map.loads(MAP_TEXT)
	path = tmp_path / "test.map"
	game_map.save(str(path))
	reloaded = Map.from_file(str(path))
	assert reloaded.name == game_map.name
	assert reloaded.dimension == game_map.dimension
	assert len(reloaded.tiles) == len(game_map.tiles)
	assert reloaded.tile_at(12, 4) == "concrete"
	assert reloaded.zone_at(12, 4).name == "courtyard"
	assert reloaded.doors[0].open_sound == "sounds/dooropen1.ogg"


def test_walker_movement_and_footsteps():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	assert walker.position == (1, 1, 0)
	pool.clear()
	assert walker.walk(1, 0)
	assert walker.position == (2, 1, 0)
	steps = pool.played("stationary")
	assert steps and "grassstep" in steps[0][1]
	assert pool.listener == ("2d", 2, 1, 0)


def test_walker_wall_bump_latches():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	walker.teleport(4, 3)
	pool.clear()
	assert not walker.walk(1, 0)  # into wallbrick
	bumps = [c for c in pool.played("stationary") if "wallbrick" in c[1]]
	assert len(bumps) == 1
	assert not walker.walk(1, 0)  # held key: no second bump
	bumps = [c for c in pool.played("stationary") if "wallbrick" in c[1]]
	assert len(bumps) == 1
	assert walker.walk(0, 1)  # a successful move clears the latch
	pool.clear()
	walker.teleport(4, 3)
	assert not walker.walk(1, 0)
	assert [c for c in pool.played("stationary") if "wallbrick" in c[1]]


def test_walker_zone_callback():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	changes = []
	walker.on_zone_change = lambda new, old: changes.append(
		(new.name if new else None, old.name if old else None)
	)
	walker.teleport(12, 4)
	assert changes and changes[-1] == ("courtyard", None)
	walker.teleport(1, 1)
	assert changes[-1] == (None, "courtyard")


def test_walker_ambient_sources():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool, hearing_distance=5)
	walker.teleport(17, 5)
	pool.clear()
	walker.update()
	started = [c for c in pool.played("2d") if "fountain" in c[1]]
	assert started and started[0][-1] is True  # looping
	assert game_map.sources[0].slot is not None
	# walking away stops it
	walker.teleport(1, 1)
	pool.clear()
	walker.update()
	assert pool.played("destroy")
	assert game_map.sources[0].slot is None


def test_walker_doors():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	walker.teleport(4, 0)
	assert not walker.walk(1, 0)  # closed door blocks
	door = walker.use_door()
	assert door is not None and door.open
	assert walker.walk(1, 0)  # now passable
	assert walker.position == (5, 0, 0)


def test_1d_map():
	game_map = Map.loads(
		"dimension:1\nsize:30\nstart:0\n"
		"tile:0:30:0:0:0:0:metal\n"
		"tile:10:10:0:0:0:0:wallmetal\n"
	)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	assert game_map.dimension == 1
	assert walker.walk(1)
	assert pool.listener[0] == "1d"
	walker.teleport(9)
	assert not walker.walk(1)  # wall at 10
	# 1d maps ignore y/z bounds
	assert game_map.is_walkable(5, 99, 99)


def test_3d_map():
	game_map = Map.loads(
		"dimension:3\nsize:10:10:3\nstart:0:0:0\n"
		"tile:0:10:0:10:0:0:concrete\n"
		"tile:0:10:0:10:1:1:grate\n"
	)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	assert game_map.tile_at(5, 5, 1) == "grate"
	assert walker.walk(0, 0, 1)  # climb to z=1
	assert walker.position == (0, 0, 1)
	assert pool.listener[0] == "3d"
	assert not walker.walk(0, 0, 1)  # z=2 has no tile


def test_walker_describe_and_land():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	walker.teleport(12, 4)
	text = walker.describe_position()
	assert "12, 4" in text and "concrete" in text and "courtyard" in text
	pool.clear()
	walker.land()
	assert "concreteland" in pool.played("stationary")[0][1]
	pool.clear()
	walker.land(hard=True)
	assert "concretehardland" in pool.played("stationary")[0][1]


def test_walker_turn_and_forward():
	game_map = Map.loads(MAP_TEXT)
	pool = FakePool()
	walker = MapWalker(game_map, pool)
	walker.teleport(3, 3)
	walker.facing = 90  # east
	assert walker.walk_forward()
	assert walker.position[0] == 4
	walker.turn(180)
	assert walker.facing == 270
	assert walker.walk_forward()
	assert walker.position[0] == 3


def test_map_building_api():
	game_map = Map(name="Built", dimension=2)
	game_map.add_tile(0, 10, 0, 10, type="sand")
	game_map.add_zone(0, 5, 0, 5, name="beach")
	assert game_map.maxx == 10
	assert game_map.tile_at(3, 3) == "sand"
	assert game_map.zone_at(3, 3).name == "beach"
	text = game_map.dumps()
	assert "tile:0:10:0:10:0:0:sand" in text
	assert Map.loads(text).tile_at(3, 3) == "sand"
