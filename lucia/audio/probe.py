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

"""What actually reaches the player's ears — measurement for self tests.

"The sound was started" is no proof that it is audible: distance
attenuation can push a mechanic's cue to -70 dB, and five copies of one
file can drive the mix past 0 dBFS. Two tools:

* Failed loads are always collected: failed_loads() lists every file a
  Sound could not load (a runtime-built path pointing nowhere). Under
  LUCIA_SELFTEST=1 they are printed to stderr when the program exits.

* With the environment variable LUCIA_AUDIO_PROBE=<file.json> set, the
  mixer records, for every played sound, the loudest level it reached at
  the ear (volume, distance, pan and master volume included) and, per
  section, the peak/RMS level of the whole mix. The report is written to
  the file at exit, so a test tool can run a game this way and
  summarise it. Games mark sections from their self test:

	lucia.audio.probe_section("Level 3")

  (a no-op when no probe is active, so it can stay in the code).
"""

import atexit
import json
import os
import sys
import time

import numpy as np

PROBE_ENV = "LUCIA_AUDIO_PROBE"
QUIET_DB = -45.0  # below this a cue is effectively gone in a real mix
_COUNT_AMP = 10 ** (-60.0 / 20.0)  # voices louder than -60 dB count as "sounding"

_failed_loads = []
_failed_set = set()
_MAX_FAILED = 500
_MAX_LEVELS = 5000  # per file in the JSON report


def record_failed_load(filename):
	"""Called by Sound.load() when a file can't be loaded."""
	name = str(filename)
	if name in _failed_set or len(_failed_loads) >= _MAX_FAILED:
		return
	_failed_set.add(name)
	_failed_loads.append(name)


def failed_loads():
	"""Every distinct filename a Sound failed to load, in first-seen order."""
	return list(_failed_loads)


def clear_failed_loads():
	del _failed_loads[:]
	_failed_set.clear()


def _report_failed_loads_at_exit():
	if os.environ.get("LUCIA_SELFTEST") == "1" and _failed_loads:
		sys.stderr.write(
			"lucia: %d sound file(s) failed to load during the self test:\n%s\n"
			% (len(_failed_loads), "\n".join("  " + f for f in _failed_loads))
		)


atexit.register(_report_failed_loads_at_exit)


def _db(amp):
	return 20.0 * float(np.log10(amp)) if amp > 1e-10 else -200.0


class _Section:
	def __init__(self, name):
		self.name = name
		self.started = time.monotonic()
		self.ended = None
		self.peak = 0.0
		self.sum_sq = 0.0
		self.samples = 0
		self.clip_blocks = 0
		self.blocks = 0
		self.max_voices = 0

	def as_dict(self):
		end = self.ended if self.ended is not None else time.monotonic()
		rms = (self.sum_sq / self.samples) ** 0.5 if self.samples else 0.0
		return {
			"name": self.name,
			"seconds": round(end - self.started, 2),
			"blocks": self.blocks,
			"peak_dbfs": round(_db(self.peak), 1),
			"rms_dbfs": round(_db(rms), 1),
			"clip_blocks": self.clip_blocks,
			"max_voices": self.max_voices,
		}


class AudioProbe:
	"""Fed by the mixer once per audio block (audio thread)."""

	def __init__(self, path=None):
		self.path = path
		self.sections = [_Section("start")]
		self.files = {}  # label -> list of per-play max level (dB at the ear)
		self.categories = {}  # label -> "music" etc.
		self._live = {}  # id(voice) -> [voice, label, max_amp, last_pos]

	def section(self, name):
		now = time.monotonic()
		current = self.sections[-1]
		current.ended = now
		if current.name == "start" and current.blocks == 0 and len(self.sections) == 1:
			self.sections.pop()  # nothing happened before the first mark
		self.sections.append(_Section(str(name)))

	def sample(self, voices, master_amp, block, peak):
		sec = self.sections[-1]
		sec.blocks += 1
		if peak > sec.peak:
			sec.peak = peak
		if peak > 1.0:
			sec.clip_blocks += 1
		sec.sum_sq += float(np.einsum("ij,ij->", block, block))
		sec.samples += block.size
		seen = set()
		playing = 0
		for voice in voices:
			label = getattr(voice, "label", None)
			if not voice.playing or label is None:
				continue
			key = id(voice)
			seen.add(key)
			level = voice.amp * max(voice.gain_left, voice.gain_right) * master_amp
			if level > _COUNT_AMP:
				playing += 1  # far-away loops at -90 dB don't load the mix
			category = getattr(voice, "category", None)
			if category:
				self.categories[label] = category
			entry = self._live.get(key)
			if entry is not None and (entry[0] is not voice or voice.pos < entry[3]) and not voice.looping:
				# same Sound played again from the start: that's a new play
				self._finish(key)
				entry = None
			if entry is None:
				self._live[key] = [voice, label, level, voice.pos]
			else:
				if level > entry[2]:
					entry[2] = level
				entry[3] = voice.pos
		if playing > sec.max_voices:
			sec.max_voices = playing
		for key in [k for k in self._live if k not in seen]:
			self._finish(key)

	def _finish(self, key):
		entry = self._live.pop(key, None)
		if entry is not None:
			self.files.setdefault(entry[1], []).append(round(_db(entry[2]), 1))

	def report(self):
		for key in list(self._live):
			self._finish(key)
		files = {}
		for label, levels in sorted(self.files.items()):
			ordered = sorted(levels)
			files[label] = {
				"plays": len(levels),
				"median_db": ordered[len(ordered) // 2],
				"max_db": ordered[-1],
				"quiet_plays": sum(1 for v in levels if v < QUIET_DB),
				"levels": levels[:_MAX_LEVELS],
				"category": self.categories.get(label),
			}
		return {
			"quiet_db": QUIET_DB,
			"sections": [s.as_dict() for s in self.sections],
			"files": files,
			"failed_loads": failed_loads(),
		}

	def write(self):
		if not self.path:
			return
		try:
			with open(self.path, "w", encoding="utf-8") as handle:
				json.dump(self.report(), handle, indent=1)
		except OSError as exc:
			sys.stderr.write("lucia audio probe: cannot write %s: %s\n" % (self.path, exc))


_probe = None


def install_from_env(mixer):
	"""Attach a probe to the mixer when LUCIA_AUDIO_PROBE is set."""
	global _probe
	path = os.environ.get(PROBE_ENV)
	if not path or _probe is not None:
		if _probe is not None:
			mixer.probe = _probe
		return _probe
	_probe = AudioProbe(path)
	mixer.probe = _probe

	def write_at_exit():
		# detach first: the audio thread must not feed the probe while the
		# report iterates over its data
		from .miniaudio import mixer as mixer_module
		current = mixer_module._mixer
		if current is not None:
			current.probe = None
		mixer.probe = None
		_probe.write()

	atexit.register(write_at_exit)
	return _probe


def active_probe():
	return _probe


def section(name):
	"""Start a named measurement section (no-op without an active probe)."""
	if _probe is not None:
		_probe.section(name)
