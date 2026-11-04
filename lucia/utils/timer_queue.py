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

"""Named callback timers, fired from the main loop (NVGT timer_queue model).

	queue = lucia.utils.TimerQueue()
	queue.set("respawn", spawn_enemy, 5000)
	queue.set("heartbeat", play_heartbeat, 800, repeating=True)
	queue.set("echo", play_echo, 300, "echo.ogg", 0.5)  # extra args go to the callback

	while lucia.running:
		lucia.process_events()
		queue.loop()  # fires whatever is due, in the main thread

No threading is involved: callbacks run synchronously inside loop(), so
they may freely touch sounds, maps and other game state.

`repeating` is keyword-only: everything positional after `ms` is passed
to the callback. (It used to be the fourth positional parameter, which
silently turned `set(id, cb, ms, "file.ogg")` into a repeating timer that
called cb() without its argument.)

A failing callback does not kill the queue, but it is not silent either:
the first exception per timer id is printed with its traceback to stderr,
every exception is collected in `failures`, and when LUCIA_SELFTEST=1 is
set the exception propagates out of loop() so the self test fails.
"""

import os
import sys
import time
import traceback


class _TimerItem:
	__slots__ = (
		"deadline", "interval", "repeating", "callback", "args", "kwargs",
		"started", "paused_remaining",
	)

	def __init__(self, now, ms, repeating, callback, args, kwargs):
		self.deadline = now + ms / 1000.0
		self.interval = ms / 1000.0
		self.repeating = repeating
		self.callback = callback
		self.args = args
		self.kwargs = kwargs
		self.started = now
		self.paused_remaining = None  # seconds left while paused, else None


class TimerQueue:
	"""Named callback timers, fired by calling loop() once per frame.

	Callbacks run synchronously in the calling thread. Exceptions raised
	by callbacks are collected in `failures` (a list of (id, exception))
	and the first one per timer id is printed to stderr with its
	traceback. Under LUCIA_SELFTEST=1 they propagate instead, so a broken
	timer fails the self test rather than going unnoticed.

	time_func: the clock (seconds, float). Inject a fake clock in tests to
	step through cooldowns and intervals deterministically.
	"""

	def __init__(self, time_func=time.monotonic):
		self._time = time_func
		self._timers = {}
		self.failures = []
		self._reported = set()

	def set(self, id, callback, ms, *args, repeating=False, **kwargs):
		"""Schedule callback(*args, **kwargs) to fire after ms milliseconds.

		repeating must be passed by keyword; positional arguments after ms
		belong to the callback. When a timer with this id already exists
		it is replaced.
		"""
		if not callable(callback):
			raise TypeError("TimerQueue.set(%r): callback %r is not callable" % (id, callback))
		self._timers[id] = _TimerItem(self._time(), ms, bool(repeating), callback, args, kwargs)

	def exists(self, id):
		"""Whether a timer with this id is scheduled."""
		return id in self._timers

	def elapsed(self, id):
		"""Milliseconds since the timer was (re)started, 0 when unknown."""
		item = self._timers.get(id)
		if item is None:
			return 0
		if item.paused_remaining is not None:
			total = item.interval - item.paused_remaining
		else:
			total = self._time() - item.started
		return int(round(max(0.0, total) * 1000))

	def timeout(self, id):
		"""The scheduled interval of the timer in milliseconds, 0 when unknown."""
		item = self._timers.get(id)
		if item is None:
			return 0
		return int(round(item.interval * 1000))

	def restart(self, id):
		"""Restart the timer from zero. Returns False when unknown."""
		item = self._timers.get(id)
		if item is None:
			return False
		now = self._time()
		item.started = now
		item.deadline = now + item.interval
		item.paused_remaining = None
		return True

	def is_repeating(self, id):
		"""Whether the timer refires after each interval."""
		item = self._timers.get(id)
		return item.repeating if item is not None else False

	def set_timeout(self, id, ms, repeating=False):
		"""Change the interval (and repeat flag) of an existing timer, restarting it."""
		item = self._timers.get(id)
		if item is None:
			return False
		now = self._time()
		item.interval = ms / 1000.0
		item.repeating = repeating
		item.started = now
		item.deadline = now + item.interval
		item.paused_remaining = None
		return True

	def pause(self, id):
		"""Freeze the timer; resume() continues where it left off."""
		item = self._timers.get(id)
		if item is None or item.paused_remaining is not None:
			return False
		item.paused_remaining = max(0.0, item.deadline - self._time())
		return True

	def resume(self, id):
		"""Continue a paused timer."""
		item = self._timers.get(id)
		if item is None or item.paused_remaining is None:
			return False
		now = self._time()
		item.deadline = now + item.paused_remaining
		item.started = now - (item.interval - item.paused_remaining)
		item.paused_remaining = None
		return True

	def delete(self, id):
		"""Remove a timer without firing it. Returns False when unknown."""
		return self._timers.pop(id, None) is not None

	def flush(self):
		"""Fire every scheduled timer immediately (once) and empty the queue."""
		items = list(self._timers.items())
		self._timers.clear()
		for id, item in items:
			self._fire(id, item)

	def reset(self):
		"""Discard all timers without firing them."""
		self._timers.clear()

	def list_timers(self):
		"""The ids of all scheduled timers."""
		return list(self._timers.keys())

	def __len__(self):
		return len(self._timers)

	@property
	def size(self):
		return len(self._timers)

	def loop(self, max_timers=0, max_catchup=100):
		"""Fire all due timers. Call once per frame from the main loop.

		max_timers limits how many callbacks fire per call (0 = no limit).
		Repeating timers catch up at most max_catchup milliseconds of
		missed time; anything older is dropped and the timer realigns to
		now, so a long lag spike cannot cause a callback storm.

		Returns the number of callbacks fired.
		"""
		now = self._time()
		due = [
			(item.deadline, id)
			for id, item in self._timers.items()
			if item.paused_remaining is None and item.deadline <= now
		]
		due.sort()
		fired = 0
		for _, id in due:
			if max_timers and fired >= max_timers:
				break
			# A previous callback may have deleted or replaced this timer.
			item = self._timers.get(id)
			if item is None or item.paused_remaining is not None or item.deadline > now:
				continue
			if item.repeating:
				item.deadline += item.interval
				if item.deadline < now - max_catchup / 1000.0:
					item.deadline = now + item.interval
				item.started = now
			else:
				del self._timers[id]
			self._fire(id, item)
			fired += 1
		return fired

	def _fire(self, id, item):
		try:
			item.callback(*item.args, **item.kwargs)
		except Exception as exc:
			self.failures.append((id, exc))
			if os.environ.get("LUCIA_SELFTEST") == "1":
				raise
			if id not in self._reported:
				self._reported.add(id)
				sys.stderr.write(
					"lucia TimerQueue: timer %r raised (reported once per id, "
					"all in queue.failures):\n%s"
					% (id, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
				)
