# Copyright (C) 2026  LuciaSoftware and it's contributors.
# LGPL-3.0 — see LICENSE.

"""Fast VoiceOver output on macOS through accessibility announcements.

prism talks to VoiceOver with AppleScript ("tell application VoiceOver to
output"): every line is an Apple Event to another process, which costs
noticeable time, and on current macOS prism often reports the backend as
unavailable (the Python process lacks the Automation permission that
osascript has), so lucia fell back to the system voice. Native Mac apps
make VoiceOver speak differently: they post an
NSAccessibilityAnnouncementRequestedNotification with the text, which
VoiceOver queues like its own output - no Apple Events, no permission,
and a real queue: high priority interrupts, medium priority is spoken
after what is being said.

Only standard frameworks are used (ctypes + the Objective-C runtime), so
there is no new dependency. The announcement needs the process to be a
GUI application (NSApp exists, which SDL sets up with the game window).
Before there is a window, lines go to VoiceOver through one long-running
osascript (JXA) process instead ("output", slower but working; lines
arriving within a moment are joined, because "output" cuts off what
VoiceOver is saying).
"""

import ctypes
import os
import ctypes.util
import queue
import subprocess
import sys
import threading
import time

PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH = 10, 50, 90

_lock = threading.Lock()
_api = None          # None = not loaded yet, False = unavailable
_running_cache = (0.0, False)


class _Api:
	def __init__(self):
		objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
		appkit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
		objc.objc_getClass.restype = ctypes.c_void_p
		objc.objc_getClass.argtypes = [ctypes.c_char_p]
		objc.sel_registerName.restype = ctypes.c_void_p
		objc.sel_registerName.argtypes = [ctypes.c_char_p]
		objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
		objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
		self.objc = objc
		addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
		vp, cp = ctypes.c_void_p, ctypes.c_char_p
		# objc_msgSend must be called through the exact prototype (arm64)
		self.send_id = ctypes.CFUNCTYPE(vp, vp, vp)(addr)
		self.send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, vp, vp)(addr)
		self.send_str = ctypes.CFUNCTYPE(vp, vp, vp, cp)(addr)
		self.send_long = ctypes.CFUNCTYPE(vp, vp, vp, ctypes.c_long)(addr)
		self.send_dict = ctypes.CFUNCTYPE(vp, vp, vp, ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.c_ulong)(addr)
		self.post = appkit.NSAccessibilityPostNotificationWithUserInfo
		self.post.restype = None
		self.post.argtypes = [vp, vp, vp]
		self.notification = vp.in_dll(appkit, "NSAccessibilityAnnouncementRequestedNotification").value
		self.key_text = vp.in_dll(appkit, "NSAccessibilityAnnouncementKey").value
		self.key_priority = vp.in_dll(appkit, "NSAccessibilityPriorityKey").value
		self.NSString = objc.objc_getClass(b"NSString")
		self.NSNumber = objc.objc_getClass(b"NSNumber")
		self.NSDictionary = objc.objc_getClass(b"NSDictionary")
		self.NSApplication = objc.objc_getClass(b"NSApplication")
		self.NSWorkspace = objc.objc_getClass(b"NSWorkspace")
		sel = objc.sel_registerName
		self.s_string = sel(b"stringWithUTF8String:")
		self.s_number = sel(b"numberWithLong:")
		self.s_dict = sel(b"dictionaryWithObjects:forKeys:count:")
		self.s_shared_app = sel(b"sharedApplication")
		self.s_shared_ws = sel(b"sharedWorkspace")
		self.s_vo_enabled = sel(b"isVoiceOverEnabled")
		self.s_main_window = sel(b"mainWindow")

	def voiceover_enabled(self):
		pool = self.objc.objc_autoreleasePoolPush()
		try:
			ws = self.send_id(self.NSWorkspace, self.s_shared_ws)
			return bool(ws) and bool(self.send_bool(ws, self.s_vo_enabled))
		finally:
			self.objc.objc_autoreleasePoolPop(pool)

	def announce(self, text, priority):
		pool = self.objc.objc_autoreleasePoolPush()
		try:
			app = self.send_id(self.NSApplication, self.s_shared_app)
			if not app:
				return False
			window = self.send_id(app, self.s_main_window)
			element = window or app
			string = self.send_str(self.NSString, self.s_string, text.encode("utf-8", "replace"))
			number = self.send_long(self.NSNumber, self.s_number, priority)
			objects = (ctypes.c_void_p * 2)(string, number)
			keys = (ctypes.c_void_p * 2)(self.key_text, self.key_priority)
			info = self.send_dict(self.NSDictionary, self.s_dict, objects, keys, 2)
			self.post(element, self.notification, info)
			return True
		finally:
			self.objc.objc_autoreleasePoolPop(pool)


def _load():
	global _api
	if _api is None:
		with _lock:
			if _api is None:
				if sys.platform != "darwin":
					_api = False
				else:
					try:
						_api = _Api()
					except (OSError, ValueError, AttributeError):
						_api = False
	return _api


def voiceover_running(max_age=2.0):
	"""Whether VoiceOver is on (cached for max_age seconds)."""
	global _running_cache
	now = time.monotonic()
	when, value = _running_cache
	if now - when < max_age:
		return value
	api = _load()
	value = bool(api) and api.voiceover_enabled()
	_running_cache = (now, value)
	return value


def gui_app():
	"""Whether this process is a GUI application (NSApp set up, e.g. by SDL
	when the game window was created). Without it announcements go nowhere."""
	if "pygame" in sys.modules:
		try:
			import pygame
			return bool(pygame.display.get_init() and pygame.display.get_surface() is not None)
		except Exception:
			return False
	return False


def available():
	"""Whether speech can go to VoiceOver (it is on; with or without window)."""
	return voiceover_running()


def _announce_enabled():
	# The accessibility-announcement route is faster but only reaches
	# VoiceOver when the game window is VoiceOver's focused window; a
	# benchmark could not verify it speaks, so it is opt-in until confirmed
	# by ear. The osascript route below is the default (measured ~10 ms).
	return os.environ.get("LUCIA_VO_ANNOUNCE") == "1"


def speak(text, interrupt=False):
	"""Speak text through VoiceOver: interrupting or appended. Returns False
	when VoiceOver is not usable. Uses the osascript route by default; the
	accessibility-announcement route only with LUCIA_VO_ANNOUNCE=1."""
	if not text or sys.platform != "darwin":
		return False
	if _announce_enabled() and gui_app():
		api = _load()
		if api and api.announce(str(text), PRIORITY_HIGH if interrupt else PRIORITY_MEDIUM):
			return True
	return _bridge().speak(str(text), interrupt)


def silence():
	"""Cut off what VoiceOver is saying."""
	if sys.platform != "darwin":
		return False
	if _announce_enabled() and gui_app():
		api = _load()
		if api and api.announce(" ", PRIORITY_HIGH):
			return True
	_bridge().silence()
	return True


# ---------------------------------------------------------------- osascript fallback

_JXA = r"""
ObjC.import('Foundation');
var vo = Application('VoiceOver');
var input = $.NSFileHandle.fileHandleWithStandardInput;
var buf = '';
while (true) {
	var data = input.availableData;
	if (data.length == 0) break;
	buf += $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding).js;
	var lines = buf.split('\n');
	buf = lines.pop();
	for (var i = 0; i < lines.length; i++) {
		try { vo.output(lines[i]); } catch (e) {}
	}
}
"""

JOIN_MS = 60


class _Bridge:
	def __init__(self):
		self.proc = None
		self.q = queue.Queue()
		self.thread = None
		self.lock = threading.Lock()

	def _start(self):
		if self.proc is not None and self.proc.poll() is None:
			return True
		try:
			self.proc = subprocess.Popen(["osascript", "-l", "JavaScript", "-e", _JXA], stdin=subprocess.PIPE,
										 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		except OSError:
			self.proc = None
			return False
		if self.thread is None:
			self.thread = threading.Thread(target=self._loop, daemon=True, name="lucia-voiceover")
			self.thread.start()
		return True

	def _send(self, text):
		with self.lock:
			if not self._start():
				return
			try:
				self.proc.stdin.write((text.replace("\r", " ").replace("\n", " ") + "\n").encode("utf-8"))
				self.proc.stdin.flush()
			except (OSError, ValueError):
				self.proc = None

	def _loop(self):
		while True:
			text, interrupt = self.q.get()
			parts = [text]
			if not interrupt:
				deadline = time.monotonic() + JOIN_MS / 1000.0
				while True:
					left = deadline - time.monotonic()
					if left <= 0:
						break
					try:
						t2, i2 = self.q.get(timeout=left)
					except queue.Empty:
						break
					if i2:
						parts = [t2]
						break
					parts.append(t2)
			self._send("  ".join(p for p in parts if p))

	def speak(self, text, interrupt):
		if not self._start():
			return False
		if interrupt:
			# drop what is still waiting, like an interrupting screen reader call
			try:
				while True:
					self.q.get_nowait()
			except queue.Empty:
				pass
		self.q.put((text, interrupt))
		return True

	def silence(self):
		try:
			while True:
				self.q.get_nowait()
		except queue.Empty:
			pass
		self._send("")


_bridge_instance = None


def _bridge():
	global _bridge_instance
	if _bridge_instance is None:
		_bridge_instance = _Bridge()
	return _bridge_instance


# ---------------------------------------------------------------- latency benchmark

_POLL_JXA = r"""
var vo = Application('VoiceOver');
var last = '';
var end = Date.now() + %d;
while (Date.now() < end) {
	var t = '';
	try { t = vo.lastPhrase.content(); } catch (e) {}
	if (t !== last) { last = t; console.log(Date.now() + '\t' + t); }
	delay(0.005);
}
"""


def benchmark(rounds=8, pause=1.2):
	"""Time from a speak call to VoiceOver taking the line (its "last
	phrase" changes), for the old osascript route and the announcements.
	SPEAKS ALOUD through VoiceOver - run it yourself:
	python -m lucia.voiceover --bench"""
	import statistics
	if not voiceover_running(0):
		print("VoiceOver is not running.")
		return 1
	os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
	import pygame
	pygame.init()
	pygame.display.set_mode((200, 100))
	pygame.display.set_caption("lucia VoiceOver benchmark")
	total_ms = int((rounds * pause * 2 + 8) * 1000)
	poller = subprocess.Popen(["osascript", "-l", "JavaScript", "-e", _POLL_JXA % total_ms],
							  stderr=subprocess.PIPE, text=True)  # JXA console.log goes to stderr
	seen = []
	threading.Thread(target=lambda: [seen.append(l.rstrip("\n")) for l in poller.stderr], daemon=True).start()
	time.sleep(1.5)
	results = {}
	routes = (("osascript output (old route)", lambda t: _bridge().speak(t, True)),
			  ("accessibility announcement (new)", lambda t: _load().announce(t, PRIORITY_HIGH)))
	for label, fn in routes:
		delays = []
		for i in range(rounds):
			for _ in range(5):
				pygame.event.pump()
				time.sleep(0.01)
			text = "Probe %s %d" % ("A" if "old" in label else "B", i + 1)
			t0 = time.time() * 1000.0
			fn(text)
			deadline = time.time() + pause
			while time.time() < deadline:
				pygame.event.pump()
				hit = [l for l in seen if l.endswith("\t" + text)]
				if hit:
					delays.append(float(hit[0].split("\t", 1)[0]) - t0)
					break
				time.sleep(0.002)
			time.sleep(max(0.0, deadline - time.time()))
		results[label] = delays
	poller.kill()
	pygame.quit()
	for label, delays in results.items():
		if delays:
			print("%-34s median %6.0f ms  min %5.0f  max %5.0f  (%d of %d seen)" % (
				label, statistics.median(delays), min(delays), max(delays), len(delays), rounds))
		else:
			print("%-34s VoiceOver's last phrase never changed (allow osascript to control VoiceOver)" % label)
	return 0


if __name__ == "__main__":
	if "--bench" in sys.argv:
		sys.exit(benchmark())
	print("usage: python -m lucia.voiceover --bench   (speaks through VoiceOver)")
