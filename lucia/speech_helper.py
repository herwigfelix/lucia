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

"""The speech helper process.

lucia runs all screen reader / TTS output in this separate process,
talking to it over a JSON-lines pipe. The reason is measured, not
theoretical: speech backends can block the game process. On macOS,
AVSpeech's per-utterance work executes while the GIL is held (hundreds
of milliseconds on the first utterance, 30-45 ms on every one after
that), which starves the 20 ms audio callback and is audible as a
dropout on every spoken menu item. In its own process, none of that can
touch the game's audio.

This file is deliberately standalone: it imports only the standard
library and prism, never lucia, so the helper starts fast and cannot
drag the game engine into the subprocess.

Protocol (one JSON object per line on stdin):

	{"op": "speak",   "text": ..., "interrupt": bool}
	{"op": "output",  "text": ..., "interrupt": bool}
	{"op": "braille", "text": ...}
	{"op": "stop"} / {"op": "pause"} / {"op": "resume"}
	{"op": "set", "name": "rate"|"pitch"|"volume"|"voice", "value": ...}
	{"op": "get", "id": n, "name": ...}   -> {"id": n, "value": ...} on stdout

The first stdout line is {"ready": <backend name or null>}.
"""

import json
import os
import sys
import threading
import time


def _pump_runloop_forever():
	"""Run the macOS main run loop, which AVSpeech needs to make sound.

	AVSpeechSynthesizer schedules its per-utterance work on the process
	main run loop. Inside a game process that loop is SDL's event pump,
	which is exactly why speaking from the game stalled it; in this
	helper nobody would pump it at all and utterances would sit silent
	forever. So the helper's main thread does nothing but pump, via
	ctypes (no pyobjc dependency), while stdin is served from a thread.
	"""
	import ctypes
	import ctypes.util

	cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
	cf.CFStringCreateWithCString.restype = ctypes.c_void_p
	cf.CFStringCreateWithCString.argtypes = [
		ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
	]
	cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
	utf8 = 0x08000100  # kCFStringEncodingUTF8
	mode = cf.CFStringCreateWithCString(None, b"kCFRunLoopDefaultMode", utf8)
	while True:
		cf.CFRunLoopRunInMode(mode, 0.25, False)


def _serve(backend):
	"""Process protocol messages from stdin; exit the helper on EOF."""
	for line in sys.stdin:
		try:
			message = json.loads(line)
		except Exception:
			continue
		op = message.get("op")
		try:
			if op == "speak":
				backend.speak(str(message["text"]), bool(message.get("interrupt")))
			elif op == "output":
				backend.output(str(message["text"]), bool(message.get("interrupt")))
			elif op == "braille":
				backend.braille(str(message["text"]))
			elif op == "stop":
				backend.stop()
			elif op == "pause":
				backend.pause()
			elif op == "resume":
				backend.resume()
			elif op == "set":
				setattr(backend, str(message["name"]), message["value"])
			elif op == "get":
				value = getattr(backend, str(message["name"]), None)
				print(json.dumps({"id": message["id"], "value": value}), flush=True)
		except Exception:
			if op == "get":
				try:
					print(json.dumps({"id": message["id"], "value": None}), flush=True)
				except Exception:
					pass
	os._exit(0)  # parent went away (or closed us): stop pumping, leave


def _warm_up(backend):
	"""Load the voice and open the audio route with a near-silent line.

	Doing this lazily on the first real menu line costs several hundred
	ms; here it happens during startup. Volume 0 would skip audio output
	entirely and warm nothing, so use a 1% whisper. Never done on screen
	reader backends — a warmup must not interrupt the user's speech.
	Deliberately does NOT wait for completion: the stdin thread is
	already serving, and the game's first real line simply interrupts
	the warmup.
	"""
	try:
		old_volume = backend.volume
		backend.volume = 0.01
		backend.speak("ready", True)
		time.sleep(0.4)
		backend.volume = old_volume
	except Exception:
		pass


def main():
	backend = None
	try:
		import prism

		context = prism.Context()
		backend = context.acquire_best()
	except Exception:
		backend = None
	name = None
	if backend is not None:
		try:
			name = backend.name
		except Exception:
			name = "unknown"
	print(json.dumps({"ready": name}), flush=True)
	if backend is None:
		return
	if sys.platform == "darwin":
		# stdin in a worker, the main thread pumps the run loop
		threading.Thread(target=_serve, args=(backend,), daemon=True).start()
		if name == "AVSpeech":
			threading.Thread(target=_warm_up, args=(backend,), daemon=True).start()
		_pump_runloop_forever()
	else:
		_serve(backend)


if __name__ == "__main__":
	main()
