# Originally written by Blastbay
# Python port and increased functionality courtesy of Americranian
# Modernized and debugged for the miniaudio backend.

from math import radians

import lucia
from .. import sound_positioning
from .sound import Sound


class SoundPoolItem:
	def __init__(self, filename, **kwargs):
		self.handle = Sound()
		self.filename = filename
		self.x = kwargs.get("x", 0)
		self.y = kwargs.get("y", 0)
		self.z = kwargs.get("z", 0)
		self.looping = kwargs.get("looping", False)
		self.pan_step = kwargs.get("pan_step", 0)
		self.volume_step = kwargs.get("volume_step", 0)
		self.behind_pitch_decrease = kwargs.get("behind_pitch_decrease", 0)
		self.start_pan = kwargs.get("start_pan", 0)
		self.start_volume = kwargs.get("start_volume", 0)
		self.start_pitch = kwargs.get("start_pitch", 100)
		self.start_offset = kwargs.get("start_offset", 0)
		self.upper_range = kwargs.get("upper_range", 0)
		self.lower_range = kwargs.get("lower_range", 0)
		self.left_range = kwargs.get("left_range", 0)
		self.right_range = kwargs.get("right_range", 0)
		self.backward_range = kwargs.get("backward_range", 0)
		self.forward_range = kwargs.get("forward_range", 0)
		self.is_3d = kwargs.get("is_3d", False)
		self.stationary = kwargs.get("stationary", False)
		self.persistent = kwargs.get("persistent", False)
		self.paused = kwargs.get("paused", False)
		# lowest volume (dB at the ear) distance may push this source to;
		# never above start_volume. None = plain distance attenuation.
		self.min_volume = kwargs.get("min_volume", None)

	def reset(self):
		if self.handle.is_active:
			self.handle.close()
		self.__init__("")

	def update(self, listener_x, listener_y, listener_z, rotation, max_distance):
		if max_distance > 0 and self.looping:
			total_distance = self.get_total_distance(listener_x, listener_y, listener_z)
			# Unload looping sounds that moved out of earshot, reload them when back in range.
			if total_distance > max_distance and self.handle.is_active:
				self.handle.close()
				return
			if total_distance <= max_distance and not self.handle.is_active:
				if not self.handle.load(self.filename):
					return
				if self.start_offset > 0:
					self.handle.position = self.start_offset
				self.update_listener_position(
					listener_x, listener_y, listener_z, radians(rotation)
				)
				if not self.paused:
					self.handle.play_looped()
				return
		self.update_listener_position(listener_x, listener_y, listener_z, radians(rotation))

	def update_listener_position(self, listener_x, listener_y, listener_z, rotation):
		if not self.handle.is_active:
			return
		if self.stationary:
			return
		self._position(listener_x, listener_y, listener_z, rotation)
		if self.min_volume is not None:
			floor = min(self.min_volume, self.start_volume)
			if self.handle.volume < floor:
				self.handle.volume = floor

	def _position(self, listener_x, listener_y, listener_z, rotation):
		delta_left = self.x - self.left_range
		delta_right = self.x + self.right_range
		delta_backward = self.y - self.backward_range
		delta_forward = self.y + self.forward_range
		delta_upper = self.z + self.upper_range
		delta_lower = self.z - self.lower_range
		true_x = listener_x
		true_y = listener_y
		true_z = listener_z
		if not self.is_3d:
			if delta_left <= listener_x <= delta_right:
				sound_positioning.position_sound_custom_1d(
					self.handle,
					listener_x,
					listener_x,
					self.pan_step,
					self.volume_step,
					self.start_pan,
					self.start_volume,
				)
				return
			if listener_x < delta_left:
				sound_positioning.position_sound_custom_1d(
					self.handle,
					listener_x,
					delta_left,
					self.pan_step,
					self.volume_step,
					self.start_pan,
					self.start_volume,
				)
			if listener_x > delta_right:
				sound_positioning.position_sound_custom_1d(
					self.handle,
					listener_x,
					delta_right,
					self.pan_step,
					self.volume_step,
					self.start_pan,
					self.start_volume,
				)
			return
		if listener_x < delta_left:
			true_x = delta_left
		elif listener_x > delta_right:
			true_x = delta_right
		if listener_y < delta_backward:
			true_y = delta_backward
		elif listener_y > delta_forward:
			true_y = delta_forward
		if listener_z < delta_lower:
			true_z = delta_lower
		elif listener_z > delta_upper:
			true_z = delta_upper
		sound_positioning.position_sound_custom_3d(
			self.handle,
			listener_x,
			listener_y,
			listener_z,
			true_x,
			true_y,
			true_z,
			rotation,
			self.pan_step,
			self.volume_step,
			self.behind_pitch_decrease,
			self.start_pan,
			self.start_volume,
			self.start_pitch,
			False,
		)

	def get_total_distance(self, listener_x, listener_y, listener_z):
		if self.stationary:
			return 0
		delta_left = self.x - self.left_range
		delta_right = self.x + self.right_range
		delta_backward = self.y - self.backward_range
		delta_forward = self.y + self.forward_range
		delta_lower = self.z - self.lower_range
		delta_upper = self.z + self.upper_range
		true_x = listener_x
		true_y = listener_y
		true_z = listener_z
		distance = 0
		if not self.is_3d:
			if delta_left <= listener_x <= delta_right:
				return distance
			if listener_x < delta_left:
				distance = delta_left - listener_x
			if listener_x > delta_right:
				distance = listener_x - delta_right
			return distance
		if listener_x < delta_left:
			true_x = delta_left
		elif listener_x > delta_right:
			true_x = delta_right
		if listener_y < delta_backward:
			true_y = delta_backward
		elif listener_y > delta_forward:
			true_y = delta_forward
		if listener_z < delta_lower:
			true_z = delta_lower
		elif listener_z > delta_upper:
			true_z = delta_upper
		distance += abs(true_x - listener_x)
		distance += abs(true_y - listener_y)
		distance += abs(true_z - listener_z)
		return distance


class FailedSlot(int):
	"""What play_1d/2d/3d return instead of a slot when a sound can't play.

	It is still the historical int (-1 = load failed, -2 = out of earshot),
	so `slot == -1` keeps working — but it is **falsy**, so the natural
	check does the right thing:

		slot = pool.play_2d(...)
		if slot:
			# really playing; safe to keep as a handle
		else:
			# nothing started

	Type checks like `isinstance(slot, int)` are easy to get backwards
	(a real slot is not an int, a failure is) and have caused silent bugs
	in generated games. Prefer truthiness, or `pool.sound_is_playing(slot)`,
	which tolerates anything you hand it.
	"""

	__slots__ = ()

	def __bool__(self):
		return False

	def __repr__(self):
		reason = {-1: "load failed", -2: "out of earshot"}.get(int(self), "too many instances")
		return "<FailedSlot %d: %s>" % (int(self), reason)


LOAD_FAILED = FailedSlot(-1)
OUT_OF_EARSHOT = FailedSlot(-2)
TOO_MANY_INSTANCES = FailedSlot(-3)  # SoundPool(max_same_file=N) refused it


def _is_slot(s):
	"""True when s is a real pool slot.

	play_1d/2d/3d return LOAD_FAILED / OUT_OF_EARSHOT instead of a slot when
	a sound cannot play, and games naturally store whatever they get back.
	Every method that takes a slot therefore has to tolerate those — and
	None — instead of crashing in the middle of a fight.
	"""
	return isinstance(s, SoundPoolItem)


class SoundPool(lucia.audio.SoundPool):
	"""Positional sounds around a listener.

	Distance attenuation: volume_step dB per unit of MANHATTAN distance
	(|dx| + |dy| + |dz|, not the straight line), counted from the edge of
	the source's range. A diagonal source 10 tiles away in x and y is 20
	units off. Rule of thumb: volume_step x largest relevant distance =
	maximum attenuation; keep the farthest point that gameplay depends on
	above about -45 dB, or give that sound a min_volume.

	max_same_file: when > 0, a one-shot whose file is already playing
	that many times is refused (TOO_MANY_INSTANCES, falsy). Copies of
	one file add up coherently: five enemies starting the same windup
	are ~7 dB louder than one and carry no extra information.
	"""

	def __init__(
		self, max_distance=70, pan_step=20.0, volume_step=2.0, behind_pitch_decrease=4.0,
		max_same_file=0,
	):
		self.max_distance = max_distance
		self.max_same_file = max_same_file
		self.pan_step = pan_step
		self.volume_step = volume_step
		self.behind_pitch_decrease = behind_pitch_decrease
		self.items = []
		self.last_listener_x = 0
		self.last_listener_y = 0
		self.last_listener_z = 0
		self.last_rotation = 0
		self.clean_frequency = 3

	def play_stationary(self, filename, looping=False, persistent=False):
		return self.play_stationary_extended(filename, looping, 0, 0, 0, 100, persistent)

	def playing_count(self, filename):
		"""How many sounds of this file are playing right now."""
		return sum(
			1 for i in self.items
			if i.filename == filename and i.handle.is_active and i.handle.is_playing
		)

	def _refuse(self, filename, looping):
		return (
			self.max_same_file > 0
			and not looping
			and self.playing_count(filename) >= self.max_same_file
		)

	def play_stationary_extended(
		self, filename, looping, offset, start_pan, start_volume, start_pitch, persistent=False
	):
		self._maybe_clean()
		if self._refuse(filename, looping):
			return TOO_MANY_INSTANCES
		s = SoundPoolItem(
			filename=filename,
			looping=looping,
			start_offset=offset,
			start_pan=start_pan,
			start_volume=start_volume,
			start_pitch=start_pitch,
			persistent=persistent,
			stationary=True,
		)
		if not s.handle.load(filename):
			s.reset()
			return LOAD_FAILED
		if s.start_offset > 0:
			s.handle.position = s.start_offset
		if start_pan != 0.0:
			s.handle.pan = start_pan
		if start_volume < 0.0:
			s.handle.volume = start_volume
		s.handle.pitch = start_pitch
		if looping:
			s.handle.play_looped()
		else:
			s.handle.play()
		self.items.append(s)
		return s

	def play_1d(self, filename, listener_x, sound_x, looping, persistent=False, *, min_volume=None):
		return self.play_extended_1d(
			filename, listener_x, sound_x, 0, 0, looping, 0, 0, 0, 100, persistent,
			min_volume=min_volume,
		)

	def play_extended_1d(
		self,
		filename,
		listener_x,
		sound_x,
		left_range,
		right_range,
		looping,
		offset,
		start_pan,
		start_volume,
		start_pitch,
		persistent=False,
		*,
		min_volume=None,
	):
		self._maybe_clean()
		if self._refuse(filename, looping):
			return TOO_MANY_INSTANCES
		s = SoundPoolItem(
			filename=filename,
			min_volume=min_volume,
			x=sound_x,
			looping=looping,
			start_pan=start_pan,
			start_volume=start_volume,
			start_pitch=start_pitch,
			persistent=persistent,
			pan_step=self.pan_step,
			volume_step=self.volume_step,
			behind_pitch_decrease=0.0,
			left_range=left_range,
			right_range=right_range,
			is_3d=False,
			start_offset=offset,
		)
		self.last_listener_x = listener_x
		if (
			self.max_distance > 0
			and s.get_total_distance(listener_x, 0, 0) > self.max_distance
		):
			if not looping:
				s.reset()
				return OUT_OF_EARSHOT
			s.update(listener_x, 0, 0, 0, self.max_distance)
			self.items.append(s)
			return s
		if not s.handle.load(filename):
			s.reset()
			return LOAD_FAILED
		if s.start_offset > 0:
			s.handle.position = s.start_offset
		s.handle.pitch = start_pitch
		s.update(listener_x, 0, 0, 0, self.max_distance)
		if looping:
			s.handle.play_looped()
		else:
			s.handle.play()
		self.items.append(s)
		return s

	def play_2d(
		self, filename, listener_x, listener_y, sound_x, sound_y, looping, persistent=False,
		*, min_volume=None,
	):
		return self.play_extended_2d(
			filename,
			listener_x,
			listener_y,
			sound_x,
			sound_y,
			0,
			0,
			0,
			0,
			looping,
			0,
			0,
			0,
			100,
			persistent,
			min_volume=min_volume,
		)

	def play_extended_2d(
		self,
		filename,
		listener_x,
		listener_y,
		sound_x,
		sound_y,
		left_range,
		right_range,
		backward_range,
		forward_range,
		looping,
		offset,
		start_pan,
		start_volume,
		start_pitch,
		persistent=False,
		*,
		min_volume=None,
	):
		self._maybe_clean()
		if self._refuse(filename, looping):
			return TOO_MANY_INSTANCES
		s = SoundPoolItem(
			filename=filename,
			min_volume=min_volume,
			x=sound_x,
			y=sound_y,
			looping=looping,
			start_pan=start_pan,
			start_volume=start_volume,
			start_pitch=start_pitch,
			persistent=persistent,
			pan_step=self.pan_step,
			volume_step=self.volume_step,
			behind_pitch_decrease=self.behind_pitch_decrease,
			left_range=left_range,
			right_range=right_range,
			backward_range=backward_range,
			forward_range=forward_range,
			is_3d=True,
			start_offset=offset,
		)
		self.last_listener_x = listener_x
		self.last_listener_y = listener_y
		if (
			self.max_distance > 0
			and s.get_total_distance(listener_x, listener_y, 0) > self.max_distance
		):
			if not looping:
				s.reset()
				return OUT_OF_EARSHOT
			s.update(listener_x, listener_y, 0, 0, self.max_distance)
			self.items.append(s)
			return s
		if not s.handle.load(filename):
			s.reset()
			return LOAD_FAILED
		if s.start_offset > 0:
			s.handle.position = s.start_offset
		s.update(listener_x, listener_y, 0, 0, self.max_distance)
		if looping:
			s.handle.play_looped()
		else:
			s.handle.play()
		self.items.append(s)
		return s

	def play_3d(
		self,
		filename,
		listener_x,
		listener_y,
		listener_z,
		sound_x,
		sound_y,
		sound_z,
		rotation=0,
		looping=False,
		persistent=False,
		keep_pitch=False,
		*,
		min_volume=None,
	):
		return self.play_extended_3d(
			filename,
			listener_x,
			listener_y,
			listener_z,
			sound_x,
			sound_y,
			sound_z,
			rotation,
			0,
			0,
			0,
			0,
			0,
			0,
			looping,
			0,
			0,
			0,
			100,
			persistent,
			keep_pitch,
			min_volume=min_volume,
		)

	def play_extended_3d(
		self,
		filename,
		listener_x,
		listener_y,
		listener_z,
		sound_x,
		sound_y,
		sound_z,
		rotation,
		left_range,
		right_range,
		backward_range,
		forward_range,
		upper_range,
		lower_range,
		looping,
		offset,
		start_pan,
		start_volume,
		start_pitch,
		persistent,
		keep_pitch,
		*,
		min_volume=None,
	):
		self._maybe_clean()
		if self._refuse(filename, looping):
			return TOO_MANY_INSTANCES
		s = SoundPoolItem(
			filename=filename,
			min_volume=min_volume,
			x=sound_x,
			y=sound_y,
			z=sound_z,
			looping=looping,
			pan_step=self.pan_step,
			volume_step=self.volume_step,
			behind_pitch_decrease=self.behind_pitch_decrease,
			start_pan=start_pan,
			start_volume=start_volume,
			start_pitch=start_pitch,
			left_range=left_range,
			right_range=right_range,
			backward_range=backward_range,
			forward_range=forward_range,
			lower_range=lower_range,
			upper_range=upper_range,
			is_3d=True,
			persistent=persistent,
			start_offset=offset,
		)
		self.last_listener_x = listener_x
		self.last_listener_y = listener_y
		self.last_listener_z = listener_z
		self.last_rotation = rotation
		if (
			self.max_distance > 0
			and s.get_total_distance(listener_x, listener_y, listener_z) > self.max_distance
		):
			if not looping:
				s.reset()
				return OUT_OF_EARSHOT
			s.update(listener_x, listener_y, listener_z, rotation, self.max_distance)
			self.items.append(s)
			return s
		if not s.handle.load(filename):
			s.reset()
			return LOAD_FAILED
		if s.start_offset > 0:
			s.handle.position = s.start_offset
		s.update(listener_x, listener_y, listener_z, rotation, self.max_distance)
		if looping:
			s.handle.play_looped()
		else:
			s.handle.play()
		self.items.append(s)
		return s

	def sound_is_active(self, s):
		if not _is_slot(s):
			return False
		if s.looping:
			return True
		if not s.handle.is_active:
			return False
		return s.handle.is_playing or s.paused

	def sound_is_playing(self, s):
		if not self.sound_is_active(s):
			return False
		return s.handle.is_playing

	def pause_sound(self, s):
		if not _is_slot(s) or not self.sound_is_active(s):
			return False
		if s.paused:
			return False
		s.paused = True
		if s.handle.is_playing:
			s.handle.pause()
		return True

	def resume_sound(self, s):
		if not _is_slot(s) or not s.paused:
			return False
		s.paused = False
		if (
			self.max_distance > 0
			and s.get_total_distance(
				self.last_listener_x, self.last_listener_y, self.last_listener_z
			)
			> self.max_distance
		):
			if s.handle.is_active:
				s.handle.close()
			return True
		s.update(
			self.last_listener_x,
			self.last_listener_y,
			self.last_listener_z,
			self.last_rotation,
			self.max_distance,
		)
		if s.handle.is_active and not s.handle.is_playing:
			if s.looping:
				s.handle.play_looped()
			else:
				s.handle.resume()
		return True

	def pause_all(self):
		for i in self.items:
			if self.sound_is_playing(i):
				self.pause_sound(i)

	def resume_all(self):
		for i in self.items:
			if i.paused:
				self.resume_sound(i)

	def destroy_all(self):
		for i in self.items:
			i.reset()
		self.items.clear()

	def update_listener_1d(self, listener_x):
		self.update_listener_3d(listener_x, 0, 0, 0)

	def update_listener_2d(self, listener_x, listener_y, rotation=0):
		self.update_listener_3d(listener_x, listener_y, 0, rotation)

	def update_listener_3d(self, listener_x, listener_y, listener_z, rotation=0):
		self.last_listener_x = listener_x
		self.last_listener_y = listener_y
		self.last_listener_z = listener_z
		self.last_rotation = rotation
		for i in self.items:
			i.update(listener_x, listener_y, listener_z, rotation, self.max_distance)

	def update_sound_1d(self, s, x):
		return self.update_sound_3d(s, x, 0, 0)

	def update_sound_2d(self, s, x, y):
		return self.update_sound_3d(s, x, y, 0)

	def update_sound_3d(self, s, x, y, z):
		if not _is_slot(s):
			return False
		s.x = x
		s.y = y
		s.z = z
		s.update(
			self.last_listener_x,
			self.last_listener_y,
			self.last_listener_z,
			self.last_rotation,
			self.max_distance,
		)
		return True

	def update_sound_start_values(self, s, start_pan, start_volume, start_pitch):
		if not _is_slot(s):
			return False
		s.start_pan = start_pan
		s.start_volume = start_volume
		s.start_pitch = start_pitch
		s.update(
			self.last_listener_x,
			self.last_listener_y,
			self.last_listener_z,
			self.last_rotation,
			self.max_distance,
		)
		if s.stationary and s.handle.is_active:
			s.handle.pan = start_pan
			s.handle.volume = start_volume
			s.handle.pitch = start_pitch
			return True
		if not s.is_3d and s.handle.is_active and s.handle.pitch != start_pitch:
			s.handle.pitch = start_pitch
		return True

	def set_min_volume(self, s, min_volume):
		"""Give a playing source a volume floor (dB, None removes it).

		Distance still shapes pan and volume, but it cannot push the sound
		below min_volume (and the floor never exceeds start_volume, so it
		can't make a sound louder than you asked for). For sounds gameplay
		depends on: a stealth cue that must stay audible across the map.
		"""
		if not _is_slot(s):
			return False
		s.min_volume = min_volume
		s.update(
			self.last_listener_x,
			self.last_listener_y,
			self.last_listener_z,
			self.last_rotation,
			self.max_distance,
		)
		return True

	def update_sound_range_1d(self, s, left_range, right_range):
		return self.update_sound_range_3d(s, left_range, right_range, 0, 0, 0, 0, 0)

	def update_sound_range_2d(
		self, s, left_range, right_range, backward_range, forward_range, rotation
	):
		return self.update_sound_range_3d(
			s, left_range, right_range, backward_range, forward_range, 0, 0, rotation
		)

	def update_sound_range_3d(
		self,
		s,
		left_range,
		right_range,
		backward_range,
		forward_range,
		lower_range,
		upper_range,
		rotation,
	):
		if not _is_slot(s):
			return False
		s.left_range = left_range
		s.right_range = right_range
		s.backward_range = backward_range
		s.forward_range = forward_range
		s.lower_range = lower_range
		s.upper_range = upper_range
		s.update(
			self.last_listener_x,
			self.last_listener_y,
			self.last_listener_z,
			rotation,
			self.max_distance,
		)
		return True

	def destroy_sound(self, s):
		if not _is_slot(s):
			return False
		s.reset()
		if s in self.items:
			self.items.remove(s)
		return True

	def clean_unused(self):
		self.clean_frequency = 3
		keep = []
		for i in self.items:
			if i.looping or i.persistent or i.paused:
				keep.append(i)
				continue
			if i.handle.is_active and i.handle.is_playing:
				keep.append(i)
				continue
			# finished one-shot: drop it
			if i.handle.is_active:
				i.handle.close()
		self.items = keep

	def _maybe_clean(self):
		self.clean_frequency -= 1
		if self.clean_frequency <= 0:
			self.clean_unused()

	def update_audio_system(self):
		self.clean_unused()

	def get_source_object(self, filename):
		for i in self.items:
			if i.filename == filename:
				return i
		return None
