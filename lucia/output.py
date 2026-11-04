# Copyright (C) 2018-2026  LuciaSoftware and it's contributors.
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

"""Screen reader and TTS output, powered by prism (prismatoid).

Prism unifies every major screen reader and TTS backend (NVDA, JAWS,
SAPI, VoiceOver/AVSpeech, Speech-dispatcher, Orca and more) behind one
API. lucia exposes the same convenience functions games always used:

	lucia.output.speak(text, interrupt=False)
	lucia.output.braille(text)
	lucia.output.output(text, interrupt=False)  # speak + braille
	lucia.output.silence()
	lucia.output.is_active()

On macOS, when the Vosh screen reader is running with its programming
interface turned on (127.0.0.1:8674), speech goes through Vosh instead
of prism, so Vosh users hear their own screen reader voice rather than
a separate system voice. lucia notices Vosh starting and quitting while
the game runs and falls back to prism in between. LUCIA_VOSH=0 turns
this off, LUCIA_VOSH_PORT sets another port.

Backends that are real TTS engines (not screen readers) also expose
voice control and offline rendering — everything degrades gracefully
(None/False/[]) when the active backend can't do it:

	lucia.output.rate = 1.2            # also .pitch, .volume
	for v in lucia.output.list_voices(): ...
	lucia.output.set_voice("Anna")     # index or name
	lucia.output.is_speaking
	lucia.output.pause() / .resume()
	lucia.output.speak_to_file("Hello", "hello.wav")

Test runs can use a virtual, silent speech output that only logs to the
console (stdout) instead of talking through the screen reader. It is on
when LUCIA_SPEECH=log, and by default under LUCIA_SELFTEST=1 (set
LUCIA_SPEECH=real to keep real speech in a self test). It can also be
switched at runtime:

	lucia.output.set_log_mode(True)    # silent, prints "[speech] ..."
	lucia.output.log                   # list of (op, text) for assertions
"""

import array as _array
import atexit as _atexit
import json as _json
import os as _os
import queue as _queue
import socket as _socket
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import time as _time
import wave as _wave


class _SpeechHelper:
	"""The pipe to the speech helper process (see lucia/speech_helper.py).

	Fire-and-forget messages go through send(); the rare value reads
	(is_speaking) go through request() with a short timeout.
	"""

	def __init__(self):
		helper_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "speech_helper.py")
		# run the file directly so the helper never imports the lucia
		# package (and with it pygame) into the subprocess
		self._process = _subprocess.Popen(
			[
				_sys.executable,
				"-c",
				"import runpy; runpy.run_path(%r, run_name='__main__')" % helper_path,
			],
			stdin=_subprocess.PIPE,
			stdout=_subprocess.PIPE,
			stderr=_subprocess.DEVNULL,
			text=True,
			bufsize=1,
		)
		self._lock = _threading.Lock()
		self._ready = _threading.Event()
		self.backend_name = None
		self._responses = {}
		self._response_event = _threading.Event()
		self._request_counter = 0
		_threading.Thread(target=self._read_loop, name="lucia-speech-helper", daemon=True).start()
		_atexit.register(self.close)

	def _read_loop(self):
		try:
			for line in self._process.stdout:
				try:
					message = _json.loads(line)
				except Exception:
					continue
				if "ready" in message:
					self.backend_name = message["ready"]
					self._ready.set()
				elif "id" in message:
					self._responses[message["id"]] = message.get("value")
					self._response_event.set()
		except Exception:
			pass
		self._ready.set()  # process died: unblock waiters

	@property
	def alive(self):
		return self._process.poll() is None

	def has_backend(self, timeout=5.0):
		"""Whether the helper found a speech backend (waits for startup)."""
		self._ready.wait(timeout)
		return self.alive and self.backend_name is not None

	def send(self, message):
		try:
			with self._lock:
				self._process.stdin.write(_json.dumps(message) + "\n")
				self._process.stdin.flush()
			return True
		except Exception:
			return False

	def request(self, name, timeout=0.5):
		"""Read a backend attribute from the helper, or None on timeout."""
		with self._lock:
			self._request_counter += 1
			request_id = self._request_counter
		if not self.send({"op": "get", "id": request_id, "name": name}):
			return None
		deadline = _threading.TIMEOUT_MAX if timeout is None else timeout
		waited = 0.0
		while waited < deadline:
			if request_id in self._responses:
				return self._responses.pop(request_id)
			self._response_event.clear()
			self._response_event.wait(0.05)
			waited += 0.05
		return None

	def close(self):
		try:
			self._process.terminate()
		except Exception:
			pass


class _VoshClient:
	"""Speech through the programming interface of the Vosh screen reader.

	Vosh listens on 127.0.0.1 (port 8674 by default) for JSON lines and
	reports when speech started, finished or was interrupted. One
	connection is kept open; a reader thread collects those events, so
	sending never waits for Vosh. When Vosh isn't running or quits, every
	call returns False and the caller falls back to prism. Users start and
	quit Vosh at any time, so a failed connection is retried, at most
	every RETRY_INTERVAL seconds.
	"""

	PROBE_TIMEOUT = 0.5
	RETRY_INTERVAL = 3.0

	def __init__(self, port=8674, host="127.0.0.1"):
		self.host = host
		self.port = port
		self._lock = _threading.Lock()
		self._socket = None
		self._next_probe = 0.0
		self._counter = 0
		self._pending = set()  # ids of speech not yet finished or interrupted
		self._responses = {}
		self._response_event = _threading.Event()

	def _probe(self):
		"""Connect and check that the answer to `status` names Vosh."""
		connection = None
		try:
			connection = _socket.create_connection((self.host, self.port), timeout=self.PROBE_TIMEOUT)
			connection.sendall(b'{"id": "lucia-probe", "command": "status"}\n')
			reader = connection.makefile("r", encoding="utf-8")
			answer = _json.loads(reader.readline())
			if answer.get("name") != "Vosh":
				raise ValueError("not Vosh")
			connection.settimeout(None)
			return connection, reader
		except Exception:
			if connection is not None:
				connection.close()
			return None

	def connect(self):
		"""Whether Vosh is reachable, connecting first if needed."""
		with self._lock:
			if self._socket is not None:
				return True
			if _time.monotonic() < self._next_probe:
				return False
			probed = self._probe()
			if probed is None:
				self._next_probe = _time.monotonic() + self.RETRY_INTERVAL
				return False
			self._socket, reader = probed
			self._pending.clear()
		_threading.Thread(
			target=self._read_loop, args=(self._socket, reader), name="lucia-vosh", daemon=True
		).start()
		return True

	def _drop(self, connection):
		with self._lock:
			if self._socket is connection:
				self._socket = None
				self._pending.clear()
				self._next_probe = _time.monotonic() + self.RETRY_INTERVAL
		try:
			connection.close()
		except Exception:
			pass

	def _read_loop(self, connection, reader):
		try:
			for line in reader:
				try:
					event = _json.loads(line)
				except Exception:
					continue
				name = event.get("event")
				request_id = event.get("id")
				if name in ("finished", "interrupted"):
					self._pending.discard(request_id)
				elif name == "status" and request_id is not None:
					self._responses[request_id] = event
					self._response_event.set()
		except Exception:
			pass
		self._drop(connection)  # Vosh quit or turned its interface off

	def send(self, command, **fields):
		"""Send one request; returns its id, or None when Vosh isn't there."""
		if not self.connect():
			return None
		with self._lock:
			connection = self._socket
			if connection is None:
				return None
			self._counter += 1
			request_id = self._counter
			if command == "speak":
				self._pending.add(request_id)
			line = _json.dumps(dict(fields, id=request_id, command=command)) + "\n"
			try:
				connection.sendall(line.encode("utf-8"))
				return request_id
			except OSError:
				self._pending.discard(request_id)
		self._drop(connection)
		return None

	def speak(self, text, interrupt=False):
		return self.send("speak", text=text, interrupt=bool(interrupt)) is not None

	def stop(self):
		return self.send("stop") is not None

	@property
	def speaking(self):
		"""Whether Vosh is speaking anything, its own speech included."""
		request_id = self.send("status")
		if request_id is None:
			return False
		deadline = _time.monotonic() + self.PROBE_TIMEOUT
		while _time.monotonic() < deadline:
			if request_id in self._responses:
				return bool(self._responses.pop(request_id).get("speaking"))
			self._response_event.clear()
			self._response_event.wait(0.05)
		# no answer in time: at least report our own unfinished speech
		return bool(self._pending)

	def close(self):
		with self._lock:
			connection = self._socket
		if connection is not None:
			self._drop(connection)


def _make_vosh_client():
	"""The Vosh client, or None where Vosh can't run or it was turned off.

	Vosh only exists on macOS; other platforms don't even try, since a
	refused connection to localhost takes seconds on Windows.
	"""
	setting = _os.environ.get("LUCIA_VOSH", "")
	if setting == "0" or (_sys.platform != "darwin" and setting != "1"):
		return None
	try:
		port = int(_os.environ.get("LUCIA_VOSH_PORT", "8674"))
	except ValueError:
		port = 8674
	return _VoshClient(port)


def _log_mode_from_env():
	"""Virtual silent speech: LUCIA_SPEECH=log, or a self test unless LUCIA_SPEECH=real."""
	setting = _os.environ.get("LUCIA_SPEECH", "").strip().lower()
	if setting in ("log", "silent", "virtual"):
		return True
	if setting == "real":
		return False
	return _os.environ.get("LUCIA_SELFTEST") == "1"


def _supports(backend, name):
	"""Whether the backend advertises a feature; unknown flags count as yes
	(the call itself is still guarded by try/except)."""
	features = getattr(backend, "features", None)
	if features is None:
		return True
	return bool(getattr(features, name, True))


class _PrismOutput:
	"""Lazy wrapper around a prism Context/Backend.

	The prism context is created on first use, so importing lucia never
	fails on systems without any speech backend (e.g. CI servers).
	"""

	def __init__(self):
		self._context = None
		self._backend = None
		self._failed = False
		self._queue = None
		self._worker = None
		# the speech helper process: None = not tried, False = unavailable
		self._helper = None
		self._vosh = _make_vosh_client()
		# virtual silent speech for test runs: logs instead of speaking
		self._log_mode = _log_mode_from_env()
		self.log = []

	def set_log_mode(self, enabled=True):
		"""Switch the virtual silent speech on or off.

		When on, speak/braille/output/silence never reach a screen reader
		or TTS engine; each call prints one "[speech] ..." line to stdout
		and is recorded in lucia.output.log as (op, text).
		"""
		self._log_mode = bool(enabled)

	@property
	def log_mode(self):
		return self._log_mode

	def _log(self, op, text=""):
		self.log.append((op, text))
		del self.log[:-1000]
		label = {"speak": "speech", "output": "speech", "braille": "braille",
				 "stop": "speech stop"}.get(op, op)
		try:
			print("[%s] %s" % (label, text) if text else "[%s]" % label, flush=True)
		except Exception:
			pass
		return True

	def _vosh_active(self):
		"""Whether speech currently goes through the Vosh screen reader."""
		return self._vosh is not None and self._vosh.connect()

	def _voiceover_on(self):
		if _sys.platform != "darwin" or _os.environ.get("LUCIA_VOICEOVER") == "0":
			return False
		from . import voiceover

		return voiceover.voiceover_running()

	def _voiceover(self):
		"""lucia.voiceover when VoiceOver is on (macOS), else None: native
		announcements once the game window exists (much faster than prism's
		AppleScript route and independent of its Automation permission),
		an osascript bridge before that. LUCIA_VOICEOVER=0 turns it off."""
		if _sys.platform != "darwin" or _os.environ.get("LUCIA_VOICEOVER") == "0":
			return None
		from . import voiceover

		return voiceover if voiceover.available() else None

	def _needs_helper(self):
		"""Whether speech must be moved out of the game process.

		Screen reader backends (VoiceOver, NVDA, JAWS, ...) synthesize
		in the screen reader's OWN process — calls to them are cheap and
		they must stay in-process, otherwise the helper's prism would
		fall back to a plain TTS voice and the player would lose their
		screen reader voice. Only in-process TTS engines that stall the
		game loop (measured: AVSpeech) go to the helper.
		"""
		backend = self._ensure()
		if backend is None:
			return False
		try:
			return backend.name in ("AVSpeech",)
		except Exception:
			return False

	def _get_helper(self):
		"""The speech helper process, started on first use when needed.

		Speaking through AVSpeech from the game process stalls its GIL
		(30-900 ms per utterance) and starves the audio callback; the
		helper process makes that structurally impossible. Set
		LUCIA_SPEECH_INPROCESS=1 to force the old in-process path.
		"""
		if self._helper is None:
			if _os.environ.get("LUCIA_SPEECH_INPROCESS") == "1" or not self._needs_helper():
				self._helper = False
			else:
				try:
					self._helper = _SpeechHelper()
				except Exception:
					self._helper = False
		if self._helper is False:
			return None
		if not self._helper.alive:
			return None
		return self._helper

	def _prestart(self):
		"""Warm up the helper in the background (called by lucia.initialize).
		Not needed while Vosh speaks for the game."""

		if self._log_mode:
			return  # virtual silent speech: no helper, no screen reader

		def warm_up():
			if self._vosh_active() or self._voiceover_on():
				return  # the screen reader speaks; no helper voice needed
			self._get_helper()

		_threading.Thread(target=warm_up, daemon=True).start()

	def _ensure(self):
		if self._backend is not None or self._failed:
			return self._backend
		try:
			import prism

			self._context = prism.Context()
			self._backend = self._context.acquire_best()
		except Exception:
			self._failed = True
			self._backend = None
		return self._backend

	@property
	def backend_name(self):
		"""The name of the active speech backend, or None."""
		if self._log_mode:
			return "log"
		if self._vosh_active():
			return "Vosh"
		if self._voiceover() is not None:
			return "VoiceOver"
		helper = self._get_helper()
		if helper is not None and helper.has_backend():
			return helper.backend_name
		backend = self._ensure()
		return backend.name if backend is not None else None

	def _dispatch(self, method, *args):
		"""Run a fire-and-forget backend call on the speech worker thread.

		Speech calls made from the MAIN thread can stall the whole
		process: on macOS, AVSpeech schedules work that then executes
		inside SDL's event pump while the GIL is held — measured as
		multi-hundred-millisecond freezes of every Python thread,
		audible as music dropouts on each spoken menu item. The same
		calls issued from a worker thread run clean, so every speak /
		braille / silence goes through this one worker, in order.
		"""
		if self._queue is None:
			self._queue = _queue.SimpleQueue()
			self._worker = _threading.Thread(
				target=self._worker_loop, name="lucia-speech", daemon=True
			)
			self._worker.start()
		self._queue.put((method, args))

	def _worker_loop(self):
		while True:
			method, args = self._queue.get()
			backend = self._backend
			if backend is None:
				continue
			try:
				getattr(backend, method)(*args)
			except Exception:
				pass

	def _say(self, op, message):
		"""Route a fire-and-forget speech op: helper process first, the
		in-process worker thread as the fallback."""
		helper = self._get_helper()
		if helper is not None:
			if not helper.has_backend():
				return False
			helper.send(message)
			return True
		if self._ensure() is None:
			return False
		self._dispatch(*op)
		return True

	def speak(self, text, interrupt=False):
		"""Speak some text through the active screen reader / TTS."""
		text = str(text)
		if self._log_mode:
			return self._log("speak", text)
		if self._vosh is not None and self._vosh.speak(text, interrupt):
			return True
		vo = self._voiceover()
		if vo is not None and vo.speak(text, interrupt):
			return True
		return self._say(
			("speak", text, interrupt),
			{"op": "speak", "text": text, "interrupt": bool(interrupt)},
		)

	def braille(self, text, **options):
		"""Braille some text on a connected braille display."""
		text = str(text)
		if self._log_mode:
			return self._log("braille", text)
		return self._say(("braille", text), {"op": "braille", "text": text})

	def output(self, text, interrupt=False):
		"""Both speak and braille some text."""
		text = str(text)
		if self._log_mode:
			return self._log("output", text)
		# Vosh's interface only speaks; braille stays with Vosh itself
		if self._vosh is not None and self._vosh.speak(text, interrupt):
			return True
		vo = self._voiceover()  # VoiceOver brailles its announcements too
		if vo is not None and vo.speak(text, interrupt):
			return True
		return self._say(
			("output", text, interrupt),
			{"op": "output", "text": text, "interrupt": bool(interrupt)},
		)

	def silence(self):
		"""Silence the current output if speaking."""
		if self._log_mode:
			return self._log("stop")
		if self._vosh is not None and self._vosh.stop():
			return True
		vo = self._voiceover()
		if vo is not None and vo.silence():
			return True
		return self._say(("stop",), {"op": "stop"})

	def is_active(self):
		"""Check whether a speech backend is available."""
		if self._log_mode:
			return True
		if self._vosh_active() or self._voiceover() is not None:
			return True
		helper = self._get_helper()
		if helper is not None:
			return helper.has_backend()
		return self._ensure() is not None

	# -- direct TTS control ---------------------------------------------------
	# Everything below degrades gracefully: when the active backend (a
	# screen reader, say) can't do it, getters return None, methods False.
	# Vosh owns its voice the way every screen reader does.

	def _get(self, attribute, feature):
		if self._log_mode:
			return None
		if self._vosh_active():
			return None
		backend = self._ensure()
		if backend is None or not _supports(backend, feature):
			return None
		try:
			return getattr(backend, attribute)
		except Exception:
			return None

	def _set(self, attribute, feature, value):
		if self._log_mode:
			return False
		if self._vosh_active():
			return False
		# keep the helper process (which does the actual speaking) in sync
		helper = self._get_helper()
		if helper is not None:
			helper.send({"op": "set", "name": attribute, "value": value})
		backend = self._ensure()
		if backend is None or not _supports(backend, feature):
			return False
		try:
			setattr(backend, attribute, value)
			return True
		except Exception:
			return False

	@property
	def rate(self):
		"""The speech rate, normalized 0.0 (slowest) .. 1.0 (fastest), or None."""
		return self._get("rate", "supports_get_rate")

	@rate.setter
	def rate(self, value):
		self._set("rate", "supports_set_rate", float(value))

	@property
	def pitch(self):
		"""The voice pitch, normalized 0.0 .. 1.0, or None."""
		return self._get("pitch", "supports_get_pitch")

	@pitch.setter
	def pitch(self, value):
		self._set("pitch", "supports_set_pitch", float(value))

	@property
	def volume(self):
		"""The speech volume, normalized 0.0 .. 1.0, or None."""
		return self._get("volume", "supports_get_volume")

	@volume.setter
	def volume(self, value):
		self._set("volume", "supports_set_volume", float(value))

	@property
	def voice(self):
		"""The index of the current voice, or None."""
		return self._get("voice", "supports_get_voice")

	def list_voices(self):
		"""All voices of the active backend as dicts with index, name and
		language. Empty when the backend has no voice list."""
		if self._vosh_active():
			return []
		backend = self._ensure()
		if backend is None or not _supports(backend, "supports_count_voices"):
			return []
		try:
			if _supports(backend, "supports_refresh_voices"):
				try:
					backend.refresh_voices()
				except Exception:
					pass
			voices = []
			for index in range(backend.voices_count):
				name = ""
				language = ""
				try:
					name = backend.get_voice_name(index)
				except Exception:
					pass
				try:
					language = backend.get_voice_language(index)
				except Exception:
					pass
				voices.append({"index": index, "name": name, "language": language})
			return voices
		except Exception:
			return []

	def set_voice(self, voice):
		"""Switch the speaking voice, by index or by (case-insensitive,
		substring-matching) name. Returns True on success."""
		if self._vosh_active():
			return False
		backend = self._ensure()
		if backend is None or not _supports(backend, "supports_set_voice"):
			return False
		index = None
		if isinstance(voice, int):
			index = voice
		else:
			wanted = str(voice).lower()
			candidates = self.list_voices()
			for entry in candidates:
				if entry["name"].lower() == wanted:
					index = entry["index"]
					break
			if index is None:
				for entry in candidates:
					if wanted in entry["name"].lower():
						index = entry["index"]
						break
		if index is None:
			return False
		helper = self._get_helper()
		if helper is not None:
			helper.send({"op": "set", "name": "voice", "value": index})
		try:
			backend.voice = index
			return True
		except Exception:
			return False

	@property
	def is_speaking(self):
		"""True while the backend is speaking (False when it can't tell)."""
		if self._log_mode:
			return False
		if self._vosh_active():
			return self._vosh.speaking
		helper = self._get_helper()
		if helper is not None:
			if not helper.has_backend():
				return False
			return bool(helper.request("speaking"))
		backend = self._ensure()
		if backend is None or not _supports(backend, "supports_is_speaking"):
			return False
		try:
			return bool(backend.speaking)
		except Exception:
			return False

	def pause(self):
		"""Pause the current speech. Returns True on success."""
		if self._vosh_active():
			return False
		helper = self._get_helper()
		if helper is not None:
			if not helper.has_backend():
				return False
			helper.send({"op": "pause"})
			return True
		backend = self._ensure()
		if backend is None or not _supports(backend, "supports_pause"):
			return False
		self._dispatch("pause")
		return True

	def resume(self):
		"""Resume paused speech. Returns True on success."""
		if self._vosh_active():
			return False
		helper = self._get_helper()
		if helper is not None:
			if not helper.has_backend():
				return False
			helper.send({"op": "resume"})
			return True
		backend = self._ensure()
		if backend is None or not _supports(backend, "supports_resume"):
			return False
		self._dispatch("resume")
		return True

	def speak_to_memory(self, text):
		"""Render text to PCM without playing it.

		Returns (pcm_bytes, sample_rate, channels, bits) with 16-bit
		signed little-endian samples, or None when the backend can't
		render offline.
		"""
		text = str(text)
		backend = self._ensure()
		if backend is None or not text or not _supports(backend, "supports_speak_to_memory"):
			return None
		chunks = []
		meta = {"channels": 1, "rate": 0}

		def on_audio(samples, channels, rate):
			chunks.append(list(samples))
			meta["channels"] = int(channels) or 1
			meta["rate"] = int(rate)

		try:
			backend.speak_to_memory(text, on_audio)
		except TypeError:
			# prism 0.17.x's high-level wrapper passes the text to cffi
			# unencoded; call the C function directly instead.
			if not self._speak_to_memory_raw(backend, text, chunks, meta):
				return None
		except Exception:
			return None
		if not chunks:
			return None
		samples = [s for chunk in chunks for s in chunk]
		pcm = _array.array("h", (
			int(max(-1.0, min(1.0, sample)) * 32767) for sample in samples
		))
		if _array.array("h", [1]).itemsize == 2:
			import sys

			if sys.byteorder == "big":
				pcm.byteswap()
		rate = meta["rate"]
		if not rate and _supports(backend, "supports_get_sample_rate"):
			try:
				rate = backend.sample_rate
			except Exception:
				pass
		rate = rate or 22050
		return pcm.tobytes(), int(rate), meta["channels"], 16

	def _speak_to_memory_raw(self, backend, text, chunks, meta):
		try:
			from prism.core import ffi, lib

			@ffi.callback("void(void *, const float *, size_t, size_t, size_t)")
			def shim(userdata, samples, count, channels, rate):
				chunks.append(ffi.unpack(samples, count))
				meta["channels"] = int(channels) or 1
				meta["rate"] = int(rate)

			# The callback fires synchronously during this call; keep the
			# shim referenced until it returns so it isn't collected.
			result = lib.prism_backend_speak_to_memory(
				backend._raw, text.encode("utf-8"), shim, ffi.NULL
			)
			del shim
			return bool(result) or bool(chunks)
		except Exception:
			return False

	def speak_to_file(self, text, path):
		"""Render text to a 16-bit PCM WAV file. Returns True on success."""
		rendered = self.speak_to_memory(text)
		if rendered is None:
			return False
		pcm, rate, channels, _bits = rendered
		try:
			with _wave.open(str(path), "wb") as w:
				w.setnchannels(channels)
				w.setsampwidth(2)
				w.setframerate(rate)
				w.writeframes(pcm)
			return True
		except OSError:
			return False


output = _PrismOutput()
