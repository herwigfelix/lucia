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

"""One shared channel for spoken lines, arbitrated by priority.

Speech must not overlap (two lines at once are unintelligible AND add up
in level), but the obvious rule "a line only starts when nobody talks"
starves in combat: pain grunts on every hit keep the channel busy and the
lines that matter never get through. A rank decides instead:

	channel = lucia.audio.VoiceChannel(pool)
	channel.say("hero", VoiceChannel.REACTION,
	            lambda: pool.play_2d("vo/hero_ouch.ogg", px, py, hx, hy, False))
	channel.say("rival", VoiceChannel.TAUNT,
	            lambda: pool.play_2d("vo/rival_taunt3.ogg", px, py, rx, ry, False))

* A running line of the SAME or HIGHER rank wins: the new line is dropped.
* A LOWER-ranked running line is cut off and the new one starts.
* The rank check comes first, the speaker second: a speaker's important
  line interrupts their own small talk instead of waiting for it.

`start` is a callable that starts the sound and returns its handle: a
SoundPool slot (pass the pool to the constructor) or a Sound. If it
returns something falsy nothing started and the channel stays as it was.
The counters `played`, `dropped` and `interrupted` let a self test check
that e.g. taunts actually get heard in a fight.
"""


class VoiceChannel:
	REACTION = 1   # pain, hit and effort sounds
	TAUNT = 2      # banter, taunts, barks
	MECHANIC = 3   # lines a challenge depends on (hints, warnings)
	STORY = 4      # plot, rescue calls, level end, game over

	def __init__(self, pool=None):
		self.pool = pool
		self.speaker = None
		self.priority = 0
		self.handle = None
		self.played = 0
		self.dropped = 0
		self.interrupted = 0

	def _handle_playing(self, handle):
		if not handle:
			return False
		if self.pool is not None:
			return self.pool.sound_is_playing(handle)
		return bool(getattr(handle, "is_playing", False))

	def _stop_handle(self, handle):
		if not handle:
			return
		if self.pool is not None:
			self.pool.destroy_sound(handle)
		else:
			handle.stop()

	@property
	def busy(self):
		"""Whether a line is playing right now."""
		if self.handle is not None and not self._handle_playing(self.handle):
			self.handle = None
			self.speaker = None
			self.priority = 0
		return self.handle is not None

	def can_say(self, priority):
		"""Whether a line of this rank would start now."""
		return not self.busy or priority > self.priority

	def say(self, speaker, priority, start):
		"""Play a line if its rank allows it. Returns the handle or None."""
		if self.busy and self.priority >= priority:
			self.dropped += 1
			return None
		handle = start()
		if not handle:
			return None  # nothing started: leave the running line alone
		if self.handle is not None:
			self._stop_handle(self.handle)
			self.interrupted += 1
		self.handle = handle
		self.speaker = speaker
		self.priority = priority
		self.played += 1
		return handle

	def stop(self):
		"""Silence the current line (cutscene start, level change)."""
		if self.handle is not None:
			self._stop_handle(self.handle)
		self.handle = None
		self.speaker = None
		self.priority = 0
