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

"""Joystick and gamepad support, in the same frame-based style as the
keyboard functions:

	pad = lucia.Joystick()          # first attached device
	while lucia.running:
		lucia.process_events()
		if pad.button_pressed(0):   # fires once, on the press frame
			fire_weapon()
		x = pad.axis(0)             # -1.0 .. 1.0, deadzone applied

Every method is safe with no device attached (returns False/0/"" and
never raises), and instances rebind automatically when a device is
plugged in or reconnected (hotplug).
"""

import pygame

# instance_id -> opened pygame Joystick
_devices = {}
# instance_ids in attach order; a lucia Joystick's `index` is a slot here
_order = []
# (instance_id, button) edges collected this frame
_pressed = set()
_released = set()
# live lucia.Joystick wrappers, so hotplug can rebind them
_instances = []


def joystick_count():
	"""The number of joysticks/gamepads currently attached."""
	return len(_order)


def _process(events):
	"""Per-frame update, called by lucia.process_events()."""
	_pressed.clear()
	_released.clear()
	for event in events:
		if event.type == pygame.JOYBUTTONDOWN:
			_pressed.add((event.instance_id, event.button))
		elif event.type == pygame.JOYBUTTONUP:
			_released.add((event.instance_id, event.button))
		elif event.type == pygame.JOYDEVICEADDED:
			_attach(event.device_index)
		elif event.type == pygame.JOYDEVICEREMOVED:
			_detach(event.instance_id)


def _attach(device_index):
	try:
		device = pygame.joystick.Joystick(device_index)
	except pygame.error:
		return
	instance_id = device.get_instance_id()
	if instance_id in _devices:
		return
	_devices[instance_id] = device
	_order.append(instance_id)
	for wrapper in _instances:
		wrapper._rebind()


def _detach(instance_id):
	device = _devices.pop(instance_id, None)
	if instance_id in _order:
		_order.remove(instance_id)
	if device is not None:
		try:
			device.quit()
		except pygame.error:
			pass
	for wrapper in _instances:
		wrapper._rebind()


class Joystick:
	"""The joystick/gamepad in slot `index` (0 = first attached device).

	Safe to construct before any device is attached; it binds
	automatically as soon as one is plugged in.
	"""

	def __init__(self, index=0):
		self.index = index
		self._device = None
		_instances.append(self)
		self._rebind()

	def _rebind(self):
		if 0 <= self.index < len(_order):
			self._device = _devices.get(_order[self.index])
		else:
			self._device = None

	@property
	def active(self):
		"""True while the underlying device is attached."""
		return self._device is not None

	@property
	def name(self):
		"""The device name, or "" when inactive."""
		if self._device is None:
			return ""
		try:
			return self._device.get_name()
		except pygame.error:
			return ""

	@property
	def buttons(self):
		"""The number of buttons (0 when inactive)."""
		if self._device is None:
			return 0
		try:
			return self._device.get_numbuttons()
		except pygame.error:
			return 0

	@property
	def axes(self):
		"""The number of axes (0 when inactive)."""
		if self._device is None:
			return 0
		try:
			return self._device.get_numaxes()
		except pygame.error:
			return 0

	@property
	def hats(self):
		"""The number of hats / d-pads (0 when inactive)."""
		if self._device is None:
			return 0
		try:
			return self._device.get_numhats()
		except pygame.error:
			return 0

	def button_down(self, button):
		"""True while the button is held."""
		if self._device is None:
			return False
		try:
			return bool(self._device.get_button(button))
		except pygame.error:
			return False

	def button_pressed(self, button):
		"""True only on the frame the button went down."""
		if self._device is None:
			return False
		return (self._device.get_instance_id(), button) in _pressed

	def button_released(self, button):
		"""True only on the frame the button went up."""
		if self._device is None:
			return False
		return (self._device.get_instance_id(), button) in _released

	def axis(self, axis, deadzone=0.15):
		"""The axis position, -1.0 to 1.0, with a deadzone.

		Inside the deadzone the result is 0.0; outside it the remaining
		travel is rescaled so the output stays continuous.
		"""
		if self._device is None:
			return 0.0
		try:
			value = float(self._device.get_axis(axis))
		except pygame.error:
			return 0.0
		if abs(value) <= deadzone:
			return 0.0
		if deadzone <= 0:
			return max(-1.0, min(1.0, value))
		sign = 1.0 if value > 0 else -1.0
		scaled = (abs(value) - deadzone) / (1.0 - deadzone)
		return sign * max(0.0, min(1.0, scaled))

	def hat(self, hat=0):
		"""The hat (d-pad) position as (x, y), each -1, 0 or 1."""
		if self._device is None:
			return (0, 0)
		try:
			return self._device.get_hat(hat)
		except pygame.error:
			return (0, 0)

	def rumble(self, low=1.0, high=1.0, duration_ms=200):
		"""Vibrate the device if it supports it. Returns True on success."""
		if self._device is None:
			return False
		try:
			return bool(self._device.rumble(low, high, duration_ms))
		except pygame.error:
			return False

	def stop_rumble(self):
		"""Stop an ongoing vibration."""
		if self._device is None:
			return False
		try:
			self._device.stop_rumble()
			return True
		except pygame.error:
			return False
