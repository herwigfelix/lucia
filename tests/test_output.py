"""Tests for the direct TTS extension of lucia.output."""

import os
import wave

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.output import _PrismOutput


@pytest.fixture(autouse=True)
def no_real_vosh(monkeypatch):
	"""Keep a Vosh running on the developer's Mac out of these tests."""
	monkeypatch.setenv("LUCIA_VOSH", "0")
	monkeypatch.setattr(lucia.output, "_vosh", None)


@pytest.fixture
def dead_output():
	"""An output whose backend acquisition permanently failed."""
	o = _PrismOutput()
	o._failed = True
	o._helper = False  # no helper process either
	return o


def test_everything_is_defensive_without_backend(dead_output, tmp_path):
	o = dead_output
	assert o.rate is None
	assert o.pitch is None
	assert o.volume is None
	assert o.voice is None
	o.rate = 0.5  # setters silently no-op
	assert o.list_voices() == []
	assert o.set_voice(0) is False
	assert o.set_voice("Anna") is False
	assert o.is_speaking is False
	assert o.pause() is False
	assert o.resume() is False
	assert o.speak_to_memory("hello") is None
	assert o.speak_to_file("hello", tmp_path / "x.wav") is False
	# the pre-existing surface stays defensive too
	assert o.speak("hello") is False
	assert o.silence() is False


needs_backend = pytest.mark.skipif(
	not lucia.output.is_active(), reason="no speech backend available"
)


@needs_backend
def test_list_voices_and_properties():
	voices = lucia.output.list_voices()
	assert voices
	assert {"index", "name", "language"} <= set(voices[0].keys())
	rate = lucia.output.rate
	if rate is not None:
		lucia.output.rate = rate
		assert lucia.output.rate == pytest.approx(rate, abs=0.05)
	assert isinstance(lucia.output.is_speaking, bool)


@needs_backend
def test_speak_to_memory_renders_pcm():
	rendered = lucia.output.speak_to_memory("hello")
	if rendered is None:
		pytest.skip("backend cannot render to memory")
	pcm, rate, channels, bits = rendered
	assert len(pcm) > 0
	assert rate > 8000
	assert channels in (1, 2)
	assert bits == 16
	assert lucia.output.speak_to_memory("") is None


@needs_backend
def test_speak_to_file_writes_wav(tmp_path):
	path = tmp_path / "tts.wav"
	if not lucia.output.speak_to_file("hello world", path):
		pytest.skip("backend cannot render to memory")
	with wave.open(str(path)) as w:
		assert w.getnframes() > 0
		assert w.getsampwidth() == 2
		assert w.getframerate() > 8000


class FakeBackend:
	"""Records which thread the speech calls arrive on."""

	def __init__(self):
		self.calls = []

	def speak(self, text, interrupt=False):
		import threading

		self.calls.append(("speak", text, threading.current_thread().name))

	def output(self, text, interrupt=False):
		import threading

		self.calls.append(("output", text, threading.current_thread().name))

	def stop(self):
		import threading

		self.calls.append(("stop", "", threading.current_thread().name))


def wait_for_calls(backend, n, timeout=5.0):
	import time

	deadline = time.time() + timeout
	while time.time() < deadline:
		if len(backend.calls) >= n:
			return True
		time.sleep(0.01)
	return False


def test_speech_runs_on_worker_thread():
	"""Regression: speech from the main thread stalled the SDL event pump
	(and with it the audio callback) for hundreds of ms on macOS. All
	fire-and-forget speech must run on the dedicated worker thread."""
	o = _PrismOutput()
	o._helper = False  # force the in-process worker path
	fake = FakeBackend()
	o._backend = fake
	assert o.speak("one", True) is True
	assert o.output("two") is True
	assert o.silence() is True
	assert wait_for_calls(fake, 3)
	kinds = [c[0] for c in fake.calls]
	assert kinds == ["speak", "output", "stop"]  # order preserved
	threads = {c[2] for c in fake.calls}
	assert threads == {"lucia-speech"}           # never the caller's thread


def test_speech_worker_survives_backend_errors():
	o = _PrismOutput()
	o._helper = False  # force the in-process worker path

	class Broken(FakeBackend):
		def speak(self, text, interrupt=False):
			if text == "boom":
				raise RuntimeError("backend error")
			super().speak(text, interrupt)

	fake = Broken()
	o._backend = fake
	o.speak("boom")
	o.speak("still alive")
	assert wait_for_calls(fake, 1)
	assert fake.calls[-1][1] == "still alive"


def test_speech_helper_process_protocol():
	"""The helper process starts, reports its backend, answers get
	requests and applies settings — all without speaking aloud."""
	pytest.importorskip("prism")
	from lucia.output import _SpeechHelper

	helper = _SpeechHelper()
	try:
		assert helper.has_backend(timeout=15)
		assert isinstance(helper.backend_name, str)
		speaking = helper.request("speaking", timeout=5)
		assert speaking in (False, 0, None)
		assert helper.send({"op": "set", "name": "rate", "value": 0.5})
		rate = helper.request("rate", timeout=5)
		assert rate is None or abs(float(rate) - 0.5) < 0.1
	finally:
		helper.close()


def test_helper_failure_falls_back_to_worker():
	o = _PrismOutput()

	class DeadHelper:
		alive = False

	o._helper = DeadHelper()
	fake = FakeBackend()
	o._backend = fake
	assert o.speak("fallback works") is True
	assert wait_for_calls(fake, 1)
	assert fake.calls[0][2] == "lucia-speech"


def test_inprocess_env_var_disables_helper(monkeypatch):
	monkeypatch.setenv("LUCIA_SPEECH_INPROCESS", "1")
	o = _PrismOutput()
	fake = FakeBackend()
	o._backend = fake
	assert o.speak("in process") is True
	assert o._helper is False
	assert wait_for_calls(fake, 1)


def test_screen_reader_backends_stay_in_process(monkeypatch):
	"""Regression: routing ALL speech through the helper made prism in
	the subprocess fall back to AVSpeech, so VoiceOver users lost their
	screen reader voice. Only AVSpeech needs the helper."""

	class Named(FakeBackend):
		def __init__(self, name):
			super().__init__()
			self.name = name

	o = _PrismOutput()
	o._backend = Named("VoiceOver")
	assert o._needs_helper() is False
	assert o.speak("via screen reader") is True
	assert o._helper is False            # no helper process was started
	assert wait_for_calls(o._backend, 1)
	assert o._backend.calls[0][2] == "lucia-speech"  # in-process worker

	avspeech = _PrismOutput()
	avspeech._backend = Named("AVSpeech")
	assert avspeech._needs_helper() is True


# -- Vosh -------------------------------------------------------------------


class FakeVosh:
	"""A local server speaking Vosh's JSON lines protocol (api.md)."""

	def __init__(self, name="Vosh"):
		import socket
		import threading

		self.name = name
		self.requests = []
		self.speaking = False
		self.connections = []
		self._server = socket.socket()
		self._server.bind(("127.0.0.1", 0))
		self._server.listen()
		self.port = self._server.getsockname()[1]
		threading.Thread(target=self._accept_loop, daemon=True).start()

	def _accept_loop(self):
		import threading

		while True:
			try:
				connection, _ = self._server.accept()
			except OSError:
				return
			self.connections.append(connection)
			threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

	def _serve(self, connection):
		import json

		def send(**event):
			connection.sendall((json.dumps(event) + "\n").encode())

		try:
			for line in connection.makefile("r", encoding="utf-8"):
				request = json.loads(line)
				self.requests.append(request)
				command, request_id = request.get("command"), request.get("id")
				if command == "status":
					send(event="status", id=request_id, name=self.name, appVersion="2026.9",
						speaking=self.speaking, version=1)
				elif command == "speak":
					send(event="queued", id=request_id)
					send(event="started", id=request_id)
					send(event="finished", id=request_id)
				elif command == "stop":
					send(event="stopped", id=request_id)
		except OSError:
			pass

	def drop_clients(self):
		import socket

		for connection in self.connections:
			try:
				connection.shutdown(socket.SHUT_RDWR)
			except OSError:
				pass
			connection.close()
		self.connections.clear()

	def close(self):
		self._server.close()
		self.drop_clients()

	def wait_for(self, predicate, timeout=5.0):
		import time

		deadline = time.time() + timeout
		while time.time() < deadline:
			if predicate():
				return True
			time.sleep(0.01)
		return False


@pytest.fixture
def fake_vosh():
	server = FakeVosh()
	yield server
	server.close()


def vosh_output(port):
	from lucia.output import _VoshClient

	o = _PrismOutput()
	o._helper = False
	o._vosh = _VoshClient(port)
	return o


def test_speech_goes_through_vosh_when_it_runs(fake_vosh):
	o = vosh_output(fake_vosh.port)
	prism = FakeBackend()
	o._backend = prism
	assert o.is_active() is True
	assert o.backend_name == "Vosh"
	assert o.speak("Level 2", interrupt=True) is True
	assert o.output("Saved") is True
	assert o.silence() is True
	assert fake_vosh.wait_for(lambda: len(fake_vosh.requests) >= 4)
	spoken = [r for r in fake_vosh.requests if r["command"] != "status"]
	assert [(r["command"], r.get("text"), r.get("interrupt")) for r in spoken] == [
		("speak", "Level 2", True),
		("speak", "Saved", False),
		("stop", None, None),
	]
	assert prism.calls == []  # prism stayed silent


def test_vosh_is_speaking_comes_from_status(fake_vosh):
	o = vosh_output(fake_vosh.port)
	assert o.is_speaking is False
	fake_vosh.speaking = True  # e.g. Vosh reading the focus by itself
	assert o.is_speaking is True


def test_vosh_tracks_unfinished_speech(fake_vosh):
	o = vosh_output(fake_vosh.port)
	o.speak("hello")
	assert fake_vosh.wait_for(lambda: not o._vosh._pending)


def test_voice_controls_degrade_with_vosh(fake_vosh):
	"""Like every screen reader, Vosh owns its voice."""
	o = vosh_output(fake_vosh.port)
	o._backend = FakeBackend()
	assert o.rate is None
	o.rate = 0.5
	assert o.list_voices() == []
	assert o.set_voice(0) is False
	assert o.pause() is False
	assert o.resume() is False


def test_a_server_not_named_vosh_is_ignored(monkeypatch):
	server = FakeVosh(name="Something else")
	try:
		o = vosh_output(server.port)
		prism = FakeBackend()
		o._backend = prism
		assert o.speak("through prism") is True
		assert wait_for_calls(prism, 1)
		assert not [r for r in server.requests if r["command"] == "speak"]
	finally:
		server.close()


def test_no_vosh_falls_back_to_prism_at_once():
	import socket
	import time

	probe = socket.socket()
	probe.bind(("127.0.0.1", 0))
	port = probe.getsockname()[1]
	probe.close()  # nothing listens there now
	o = vosh_output(port)
	prism = FakeBackend()
	o._backend = prism
	started = time.monotonic()
	assert o.speak("fallback") is True
	assert time.monotonic() - started < 0.5
	assert wait_for_calls(prism, 1)
	assert o._vosh_active() is False


def test_vosh_quitting_and_returning(fake_vosh):
	"""Users start and quit Vosh at any time: speech falls back to prism
	and returns to Vosh once it answers again."""
	o = vosh_output(fake_vosh.port)
	o._vosh.RETRY_INTERVAL = 0.0
	prism = FakeBackend()
	o._backend = prism
	assert o.speak("one") is True
	fake_vosh.drop_clients()  # Vosh quits
	assert fake_vosh.wait_for(lambda: o._vosh._socket is None)
	fake_vosh._server.close()
	assert o.speak("two") is True
	assert wait_for_calls(prism, 1)
	assert prism.calls[0][1] == "two"
	# Vosh starts again on the same port
	import socket

	fake_vosh._server = socket.socket()
	fake_vosh._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	fake_vosh._server.bind(("127.0.0.1", fake_vosh.port))
	fake_vosh._server.listen()
	import threading

	threading.Thread(target=fake_vosh._accept_loop, daemon=True).start()
	assert o.speak("three") is True
	assert fake_vosh.wait_for(
		lambda: any(r.get("text") == "three" for r in fake_vosh.requests)
	)
	assert len(prism.calls) == 1


def test_vosh_is_only_tried_on_macos(monkeypatch):
	from lucia.output import _make_vosh_client

	monkeypatch.delenv("LUCIA_VOSH", raising=False)
	monkeypatch.setattr("sys.platform", "win32")
	assert _make_vosh_client() is None
	monkeypatch.setenv("LUCIA_VOSH", "1")
	assert _make_vosh_client() is not None
	monkeypatch.setattr("sys.platform", "darwin")
	monkeypatch.setenv("LUCIA_VOSH", "0")
	assert _make_vosh_client() is None
	monkeypatch.delenv("LUCIA_VOSH")
	monkeypatch.setenv("LUCIA_VOSH_PORT", "9000")
	assert _make_vosh_client().port == 9000
