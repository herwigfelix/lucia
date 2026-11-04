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

"""The lucia map engine: tile maps for 1D, 2D and 3D audio games.

A map is a set of rectangular regions ("tiles") that give every
coordinate a material, plus named zones, positional ambient sound
sources and doors. Maps are plain text, so they can be written by hand,
generated, saved by a level editor or shipped inside a resource pack.

	game_map = lucia.Map.from_file("maps/park.map")
	walker = lucia.MapWalker(game_map, pool)
	walker.walk(1, 0)        # step east; handles walls and footsteps
	walker.update()          # per frame: ambience, listener, zones

The engine is dimension-aware: a 1D map only uses x (sidescrollers), a
2D map x and y (top-down), a 3D map adds z (floors, flying). The walker
positions audio with the matching SoundPool calls automatically.

Material naming follows the default soundset: a tile of type "grass"
plays grassstep1..grassstep5.ogg when walked on, grassland.ogg on a
normal landing and grasshardland.ogg on a hard one. Any type starting
with "wall" is solid: walking into it plays <type>.ogg (e.g.
wallbrick.ogg) and the move is refused.
"""

import os
import random

_AXES = ("x", "y", "z")


def _to_number(text, default=0):
	try:
		value = float(text)
	except (TypeError, ValueError):
		return default
	return int(value) if value == int(value) else value


class Box:
	"""An axis-aligned region. Unused axes in 1D/2D maps stay at 0."""

	def __init__(self, minx=0, maxx=0, miny=0, maxy=0, minz=0, maxz=0):
		self.minx, self.maxx = min(minx, maxx), max(minx, maxx)
		self.miny, self.maxy = min(miny, maxy), max(miny, maxy)
		self.minz, self.maxz = min(minz, maxz), max(minz, maxz)

	def contains(self, x, y=0, z=0):
		return (
			self.minx <= x <= self.maxx
			and self.miny <= y <= self.maxy
			and self.minz <= z <= self.maxz
		)

	@property
	def center(self):
		return (
			(self.minx + self.maxx) / 2.0,
			(self.miny + self.maxy) / 2.0,
			(self.minz + self.maxz) / 2.0,
		)

	def distance_to(self, x, y=0, z=0):
		"""Distance from a point to the nearest edge of the box (0 inside)."""
		dx = max(self.minx - x, 0, x - self.maxx)
		dy = max(self.miny - y, 0, y - self.maxy)
		dz = max(self.minz - z, 0, z - self.maxz)
		return (dx * dx + dy * dy + dz * dz) ** 0.5

	def coords(self):
		return (self.minx, self.maxx, self.miny, self.maxy, self.minz, self.maxz)


class Tile(Box):
	"""A region of a single material. Later tiles override earlier ones."""

	def __init__(self, minx, maxx, miny, maxy, minz, maxz, type):
		super().__init__(minx, maxx, miny, maxy, minz, maxz)
		self.type = type

	@property
	def is_wall(self):
		return self.type.startswith("wall")


class Zone(Box):
	"""A named area, for announcements, triggers and music changes."""

	def __init__(self, minx, maxx, miny, maxy, minz, maxz, name):
		super().__init__(minx, maxx, miny, maxy, minz, maxz)
		self.name = name


class SoundSource(Box):
	"""A positional sound anchored to a region of the map.

	Looping sources are started when the listener comes within
	`hearing_distance` and stopped again when they leave, so a map can
	hold hundreds of them.
	"""

	def __init__(
		self, minx, maxx, miny, maxy, minz, maxz, sound, looping=True, volume=0
	):
		super().__init__(minx, maxx, miny, maxy, minz, maxz)
		self.sound = sound
		self.looping = looping
		self.volume = volume
		self.slot = None  # active SoundPool item while audible


class Door(Box):
	"""A door at one coordinate: blocks movement until it is opened."""

	def __init__(self, x, y, z, open_sound="", close_sound="", locked=False):
		super().__init__(x, x, y, y, z, z)
		self.x, self.y, self.z = x, y, z
		self.open_sound = open_sound
		self.close_sound = close_sound
		self.locked = locked
		self.open = False


class Map:
	"""A tile map for a 1D, 2D or 3D audio game."""

	def __init__(self, name="", dimension=2):
		if dimension not in (1, 2, 3):
			raise ValueError("dimension must be 1, 2 or 3")
		self.name = name
		self.dimension = dimension
		self.maxx = 0
		self.maxy = 0
		self.maxz = 0
		self.start = (0, 0, 0)
		self.tiles = []
		self.zones = []
		self.sources = []
		self.doors = []

	# -- building ------------------------------------------------------------

	def add_tile(self, minx, maxx, miny=0, maxy=0, minz=0, maxz=0, type="air"):
		tile = Tile(minx, maxx, miny, maxy, minz, maxz, type)
		self.tiles.append(tile)
		self.maxx = max(self.maxx, tile.maxx)
		self.maxy = max(self.maxy, tile.maxy)
		self.maxz = max(self.maxz, tile.maxz)
		return tile

	def add_zone(self, minx, maxx, miny=0, maxy=0, minz=0, maxz=0, name=""):
		zone = Zone(minx, maxx, miny, maxy, minz, maxz, name)
		self.zones.append(zone)
		return zone

	def add_source(
		self, minx, maxx, miny=0, maxy=0, minz=0, maxz=0, sound="", looping=True, volume=0
	):
		source = SoundSource(minx, maxx, miny, maxy, minz, maxz, sound, looping, volume)
		self.sources.append(source)
		return source

	def add_door(self, x, y=0, z=0, open_sound="", close_sound="", locked=False):
		door = Door(x, y, z, open_sound, close_sound, locked)
		self.doors.append(door)
		return door

	# -- queries -------------------------------------------------------------

	def _normalize(self, x, y, z):
		"""Zero out the axes this map's dimension doesn't use, so a 1D map
		ignores y/z and a 2D map ignores z."""
		if self.dimension == 1:
			return x, 0, 0
		if self.dimension == 2:
			return x, y, 0
		return x, y, z

	def tile_at(self, x, y=0, z=0):
		"""The material at a coordinate, or "" when nothing is there.

		Later tiles win, so a map can be layered: a big floor first, then
		walls and details on top.
		"""
		x, y, z = self._normalize(x, y, z)
		for tile in reversed(self.tiles):
			if tile.contains(x, y, z):
				return tile.type
		return ""

	def is_wall(self, x, y=0, z=0):
		"""True when the coordinate is solid (material starts with "wall")."""
		return self.tile_at(x, y, z).startswith("wall")

	def is_inside(self, x, y=0, z=0):
		"""True when the coordinate is within the map bounds."""
		if x < 0 or x > self.maxx:
			return False
		if self.dimension >= 2 and (y < 0 or y > self.maxy):
			return False
		if self.dimension >= 3 and (z < 0 or z > self.maxz):
			return False
		return True

	def is_walkable(self, x, y=0, z=0):
		"""True when a character may stand at this coordinate: inside the
		map, on a real tile, not a wall, not behind a closed door.

		A door usually sits inside a wall, so an OPEN door makes its
		coordinate passable even though the tile there is solid; a closed
		one always blocks.
		"""
		if not self.is_inside(x, y, z):
			return False
		door = self.door_at(x, y, z)
		if door is not None:
			return door.open
		tile = self.tile_at(x, y, z)
		if not tile or tile.startswith("wall"):
			return False
		return True

	def zone_at(self, x, y=0, z=0):
		"""The innermost zone containing a coordinate, or None."""
		x, y, z = self._normalize(x, y, z)
		for zone in reversed(self.zones):
			if zone.contains(x, y, z):
				return zone
		return None

	def door_at(self, x, y=0, z=0):
		x, y, z = self._normalize(x, y, z)
		for door in self.doors:
			if door.contains(x, y, z):
				return door
		return None

	def sources_near(self, x, y=0, z=0, distance=50):
		x, y, z = self._normalize(x, y, z)
		return [s for s in self.sources if s.distance_to(x, y, z) <= distance]

	def find_path(self, start, goal, allow_diagonals=False, search_range=0, cost_callback=None):
		"""The cheapest walkable path from start to goal (A*), both included.

		start and goal are (x,), (x, y) or (x, y, z) tuples. By default a
		cell costs 1 when Map.is_walkable says yes (open doors count) and
		is blocked otherwise; pass cost_callback(x, y, z) to override
		(None/negative = blocked, 0 = free, > 0 = extra cost).

		Returns a list of (x, y, z) tuples, or [] when no path exists.
		The map's dimension decides which axes are searched.
		"""
		from .pathfinder import Pathfinder

		if cost_callback is None:
			def cost_callback(x, y, z):
				return 0 if self.is_walkable(x, y, z) else None
		finder = Pathfinder(
			cost_callback,
			allow_diagonals=allow_diagonals,
			search_range=search_range,
			dimension=self.dimension,
		)
		start = self._normalize(*(tuple(start) + (0, 0))[:3])
		goal = self._normalize(*(tuple(goal) + (0, 0))[:3])
		return finder.find(start, goal)

	# -- text format ---------------------------------------------------------

	@classmethod
	def loads(cls, text):
		"""Parse a map from the colon-delimited text format."""
		game_map = cls()
		for raw_line in text.splitlines():
			line = raw_line.strip()
			if not line or line.startswith("#"):
				continue
			parts = line.split(":")
			record = parts[0].strip().lower()
			values = [p.strip() for p in parts[1:]]
			if record == "name":
				game_map.name = ":".join(values)
			elif record == "dimension" and values:
				game_map.dimension = max(1, min(3, int(_to_number(values[0], 2))))
			elif record == "size" and len(values) >= 1:
				game_map.maxx = _to_number(values[0])
				if len(values) > 1:
					game_map.maxy = _to_number(values[1])
				if len(values) > 2:
					game_map.maxz = _to_number(values[2])
			elif record == "start" and values:
				game_map.start = (
					_to_number(values[0]),
					_to_number(values[1]) if len(values) > 1 else 0,
					_to_number(values[2]) if len(values) > 2 else 0,
				)
			elif record == "tile" and len(values) >= 7:
				game_map.add_tile(*[_to_number(v) for v in values[:6]], type=values[6])
			elif record == "zone" and len(values) >= 7:
				game_map.add_zone(*[_to_number(v) for v in values[:6]], name=":".join(values[6:]))
			elif record == "source" and len(values) >= 7:
				looping = _to_number(values[7], 1) != 0 if len(values) > 7 else True
				volume = _to_number(values[8], 0) if len(values) > 8 else 0
				game_map.add_source(
					*[_to_number(v) for v in values[:6]],
					sound=values[6],
					looping=looping,
					volume=volume,
				)
			elif record == "door" and len(values) >= 3:
				game_map.add_door(
					_to_number(values[0]),
					_to_number(values[1]),
					_to_number(values[2]),
					values[3] if len(values) > 3 else "",
					values[4] if len(values) > 4 else "",
					_to_number(values[5], 0) != 0 if len(values) > 5 else False,
				)
		return game_map

	@classmethod
	def from_file(cls, filename):
		"""Load a map from disk, or from the global resource file when set."""
		import lucia

		pack = lucia.get_global_resource_file()
		if pack is not None:
			try:
				data = pack.get(filename)
				if isinstance(data, bytes):
					data = data.decode("utf-8")
				return cls.loads(data)
			except KeyError:
				pass
		with open(filename, "r", encoding="utf-8") as f:
			return cls.loads(f.read())

	def dumps(self):
		"""Serialize the map back to the text format."""
		lines = []
		if self.name:
			lines.append("name:%s" % self.name)
		lines.append("dimension:%d" % self.dimension)
		lines.append("size:%s:%s:%s" % (self.maxx, self.maxy, self.maxz))
		lines.append("start:%s:%s:%s" % self.start)
		for tile in self.tiles:
			lines.append("tile:%s:%s:%s:%s:%s:%s:%s" % (tile.coords() + (tile.type,)))
		for zone in self.zones:
			lines.append("zone:%s:%s:%s:%s:%s:%s:%s" % (zone.coords() + (zone.name,)))
		for source in self.sources:
			lines.append(
				"source:%s:%s:%s:%s:%s:%s:%s:%d:%s"
				% (source.coords() + (source.sound, 1 if source.looping else 0, source.volume))
			)
		for door in self.doors:
			lines.append(
				"door:%s:%s:%s:%s:%s:%d"
				% (door.x, door.y, door.z, door.open_sound, door.close_sound, 1 if door.locked else 0)
			)
		return "\n".join(lines) + "\n"

	def save(self, filename):
		with open(filename, "w", encoding="utf-8") as f:
			f.write(self.dumps())
		return True


class MapWalker:
	"""Moves a character through a Map and produces all the audio for it.

	Handles collision, footsteps by material, wall bumps (with a latch so
	a held key doesn't machine-gun the sound), jump/landing sounds, doors,
	zone changes and the looping ambient sources of the map — and keeps
	the SoundPool listener in sync using the map's dimension.

	* game_map: the Map to walk
	* pool: a lucia SoundPool
	* sound_dir: folder the material sounds live in
	* extension: file extension of the sound files
	* hearing_distance: radius in which map sources are audible
	"""

	def __init__(
		self,
		game_map,
		pool,
		sound_dir="sounds",
		extension=".ogg",
		hearing_distance=40,
		step_variations=5,
	):
		self.map = game_map
		self.pool = pool
		self.sound_dir = sound_dir
		self.extension = extension
		self.hearing_distance = hearing_distance
		self.step_variations = step_variations
		self.x, self.y, self.z = game_map.start
		self.facing = 0
		self.on_wall = False  # latch, so a held key bumps only once
		self.zone = None
		self.on_zone_change = None  # callback(new_zone, old_zone)
		self.on_move = None  # callback(x, y, z)
		self.silent = False  # True disables all walker audio
		self.update_listener()
		self._refresh_zone(announce=False)

	# -- sound helpers -------------------------------------------------------

	def sound_path(self, name):
		return os.path.join(self.sound_dir, name + self.extension)

	def play_here(self, name):
		"""Play a sound at the character's own position (centered)."""
		if not self.silent:
			self.pool.play_stationary(self.sound_path(name))

	def play_at(self, name, x, y=0, z=0, looping=False):
		"""Play a sound positioned somewhere on the map."""
		if self.silent:
			return None
		path = self.sound_path(name) if not name.endswith(self.extension) else name
		return self._play_positional(path, x, y, z, looping)

	def _play_positional(self, path, x, y=0, z=0, looping=False):
		if self.map.dimension == 1:
			return self.pool.play_1d(path, self.x, x, looping)
		if self.map.dimension == 2:
			return self.pool.play_2d(path, self.x, self.y, x, y, looping)
		return self.pool.play_3d(
			path, self.x, self.y, self.z, x, y, z, self.facing, looping
		)

	def update_listener(self):
		"""Push the character's position into the pool. Called after every
		move; call it yourself if you move the walker directly."""
		if self.map.dimension == 1:
			self.pool.update_listener_1d(self.x)
		elif self.map.dimension == 2:
			self.pool.update_listener_2d(self.x, self.y, self.facing)
		else:
			self.pool.update_listener_3d(self.x, self.y, self.z, self.facing)

	# -- movement ------------------------------------------------------------

	@property
	def position(self):
		return (self.x, self.y, self.z)

	@property
	def tile(self):
		"""The material the character is standing on."""
		return self.map.tile_at(self.x, self.y, self.z)

	def play_step(self):
		"""Play a footstep for the current material."""
		material = self.tile
		if not material or self.silent:
			return
		index = random.randint(1, self.step_variations)
		self.play_here("%sstep%d" % (material, index))

	def bump(self, x, y, z):
		"""React to a blocked move: play the wall sound once per attempt."""
		if self.on_wall or self.silent:
			return
		self.on_wall = True
		material = self.map.tile_at(x, y, z)
		if material:
			self.play_here(material)

	def walk(self, dx=0, dy=0, dz=0):
		"""Try to move by a delta. Returns True when the character moved.

		Blocked moves play the wall sound (once, until a successful move
		clears the latch) and leave the position unchanged.
		"""
		target = (self.x + dx, self.y + dy, self.z + dz)
		if not self.map.is_walkable(*target):
			self.bump(*target)
			return False
		self.on_wall = False
		self.x, self.y, self.z = target
		self.play_step()
		self.update_listener()
		self._refresh_zone()
		if self.on_move:
			self.on_move(self.x, self.y, self.z)
		return True

	def walk_forward(self, distance=1):
		"""Step in the direction the character is facing (2D/3D maps)."""
		from .utils import rotation

		if self.map.dimension == 1:
			return self.walk(distance)
		moved = rotation.move((self.x, self.y, self.z), self.facing, factor=distance)
		return self.walk(round(moved.x) - self.x, round(moved.y) - self.y, 0)

	def turn(self, degrees):
		"""Turn by a relative angle and update the listener orientation."""
		self.facing = (self.facing + degrees) % 360
		self.update_listener()
		return self.facing

	def teleport(self, x, y=0, z=0, announce=True):
		"""Move without walking (spawning, checkpoints, level exits)."""
		self.x, self.y, self.z = x, y, z
		self.on_wall = False
		self.update_listener()
		self._refresh_zone(announce=announce)
		if self.on_move:
			self.on_move(self.x, self.y, self.z)

	def land(self, hard=False):
		"""Play the landing sound of the current material."""
		material = self.tile
		if material:
			self.play_here(material + ("hardland" if hard else "land"))

	# -- doors ---------------------------------------------------------------

	def door_in_front(self):
		"""The door at the character's position or one step ahead."""
		door = self.map.door_at(self.x, self.y, self.z)
		if door is not None:
			return door
		if self.map.dimension == 1:
			candidates = [(self.x + 1, self.y, self.z), (self.x - 1, self.y, self.z)]
		else:
			candidates = [
				(self.x + 1, self.y, self.z),
				(self.x - 1, self.y, self.z),
				(self.x, self.y + 1, self.z),
				(self.x, self.y - 1, self.z),
			]
		for position in candidates:
			door = self.map.door_at(*position)
			if door is not None:
				return door
		return None

	def use_door(self):
		"""Open or close the nearest door. Returns the door, or None."""
		door = self.door_in_front()
		if door is None:
			return None
		if door.locked and not door.open:
			return door
		door.open = not door.open
		sound = door.open_sound if door.open else door.close_sound
		if sound and not self.silent:
			self._play_positional(sound, door.x, door.y, door.z, False)
		return door

	# -- zones and ambience --------------------------------------------------

	def _refresh_zone(self, announce=True):
		zone = self.map.zone_at(self.x, self.y, self.z)
		if zone is not self.zone:
			old, self.zone = self.zone, zone
			if announce and self.on_zone_change:
				self.on_zone_change(zone, old)

	def update(self):
		"""Per-frame upkeep: start and stop the map's ambient sources by
		distance. Call once per frame, after lucia.process_events()."""
		if self.silent:
			return
		for source in self.map.sources:
			distance = source.distance_to(self.x, self.y, self.z)
			audible = distance <= self.hearing_distance
			if audible and source.slot is None:
				cx, cy, cz = source.center
				slot = self._play_positional(source.sound, cx, cy, cz, source.looping)
				if slot is not None and not isinstance(slot, int):
					source.slot = slot
					if source.volume:
						slot.handle.volume = source.volume
			elif not audible and source.slot is not None:
				self.pool.destroy_sound(source.slot)
				source.slot = None

	def stop_ambience(self):
		"""Stop every running map source (level change, pause)."""
		for source in self.map.sources:
			if source.slot is not None:
				self.pool.destroy_sound(source.slot)
				source.slot = None

	# -- orientation helpers -------------------------------------------------

	def describe_position(self):
		"""A short spoken description: coordinates, material, zone."""
		if self.map.dimension == 1:
			text = "%d" % round(self.x)
		elif self.map.dimension == 2:
			text = "%d, %d" % (round(self.x), round(self.y))
		else:
			text = "%d, %d, %d" % (round(self.x), round(self.y), round(self.z))
		material = self.tile
		if material:
			text += ", " + material
		if self.zone is not None:
			text += ", " + self.zone.name
		return text
