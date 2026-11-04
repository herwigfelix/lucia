"""Tests for the RFC 6455 WebSocket client against a raw loopback server.

The test server implements just enough of the protocol: handshake,
unmasked server frames, echo, ping and close. Server frames may legally
be unmasked, which keeps it small.
"""

import base64
import hashlib
import socket
import struct
import threading
import time

import pytest

from lucia.network.websocket import WebSocket

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def wait_for(condition, timeout=5.0):
	deadline = time.time() + timeout
	while time.time() < deadline:
		if condition():
			return True
		time.sleep(0.01)
	return False


class EchoServer:
	"""A minimal single-connection WebSocket echo server."""

	def __init__(self, reject=False, fragment=False):
		self.reject = reject
		self.fragment = fragment
		self.pongs = []
		self.received_close = False
		self._listener = socket.socket()
		self._listener.bind(("127.0.0.1", 0))
		self._listener.listen(1)
		self.port = self._listener.getsockname()[1]
		self._conn = None
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	@property
	def url(self):
		return "ws://127.0.0.1:%d/" % self.port

	def close(self):
		for sock in (self._conn, self._listener):
			if sock is not None:
				try:
					sock.close()
				except OSError:
					pass

	def send_close(self, code=1000):
		self._send_frame(0x8, struct.pack(">H", code))

	def send_fragmented_text(self, parts):
		for i, part in enumerate(parts):
			fin = 0x80 if i == len(parts) - 1 else 0
			opcode = 0x1 if i == 0 else 0x0
			self._send_frame_raw(fin | opcode, part.encode("utf-8"))

	def _run(self):
		try:
			conn, _ = self._listener.accept()
		except OSError:
			return
		self._conn = conn
		request = b""
		while b"\r\n\r\n" not in request:
			chunk = conn.recv(4096)
			if not chunk:
				return
			request += chunk
		if self.reject:
			conn.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
			conn.close()
			return
		key = ""
		for line in request.decode("iso-8859-1").split("\r\n"):
			if line.lower().startswith("sec-websocket-key:"):
				key = line.split(":", 1)[1].strip()
		accept = base64.b64encode(
			hashlib.sha1((key + _GUID).encode("ascii")).digest()
		).decode("ascii")
		conn.sendall(
			(
				"HTTP/1.1 101 Switching Protocols\r\n"
				"Upgrade: websocket\r\nConnection: Upgrade\r\n"
				"Sec-WebSocket-Accept: %s\r\n\r\n" % accept
			).encode("ascii")
		)
		buffer = b""
		while True:
			try:
				frame, buffer = self._read_frame(conn, buffer)
			except (OSError, TypeError, struct.error):
				return  # client hung up mid-frame
			if frame is None:
				return
			opcode, payload = frame
			if opcode == 0x8:
				self.received_close = True
				return
			if opcode == 0x9:  # client ping -> pong
				self._send_frame(0xA, payload)
			elif opcode == 0xA:
				self.pongs.append(payload)
			elif opcode in (0x1, 0x2):
				if self.fragment and opcode == 0x1 and len(payload) > 4:
					half = len(payload) // 2
					self._send_frame_raw(0x01, payload[:half])
					self._send_frame_raw(0x80, payload[half:])
				else:
					self._send_frame(opcode, payload)

	def _read_frame(self, conn, buffer):
		def take(n):
			nonlocal buffer
			while len(buffer) < n:
				chunk = conn.recv(65536)
				if not chunk:
					return None
				buffer += chunk
			data, buffer = buffer[:n], buffer[n:]
			return data

		head = take(2)
		if head is None:
			return None, buffer
		opcode = head[0] & 0x0F
		length = head[1] & 0x7F
		masked = bool(head[1] & 0x80)
		if length == 126:
			length = struct.unpack(">H", take(2))[0]
		elif length == 127:
			length = struct.unpack(">Q", take(8))[0]
		mask = take(4) if masked else b""
		payload = take(length) if length else b""
		if payload is None:
			return None, buffer
		if mask:
			payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
		return (opcode, payload), buffer

	def _send_frame(self, opcode, payload):
		self._send_frame_raw(0x80 | opcode, payload)

	def _send_frame_raw(self, first_byte, payload):
		length = len(payload)
		if length < 126:
			header = bytes([first_byte, length])
		elif length < 65536:
			header = bytes([first_byte, 126]) + struct.pack(">H", length)
		else:
			header = bytes([first_byte, 127]) + struct.pack(">Q", length)
		try:
			self._conn.sendall(header + payload)
		except OSError:
			pass


@pytest.fixture
def server():
	s = EchoServer()
	yield s
	s.close()


def connect(url):
	ws = WebSocket(url)
	assert wait_for(lambda: ws.connected or ws.error), ws.error
	assert ws.connected, ws.error
	return ws


def test_text_echo(server):
	ws = connect(server.url)
	assert ws.send("hello über ✓")
	assert wait_for(lambda: not ws_receive_empty(ws))
	assert ws.receive() == "hello über ✓"
	ws.close()


def ws_receive_empty(ws):
	message = ws.receive()
	if message is None:
		return True
	ws._messages.put(message)  # put it back for the test
	return False


def test_binary_echo(server):
	ws = connect(server.url)
	payload = bytes(range(256)) * 4
	assert ws.send(payload)
	assert wait_for(lambda: not ws_receive_empty(ws))
	assert ws.receive() == payload
	ws.close()


def test_large_message_uses_64bit_length(server):
	ws = connect(server.url)
	payload = b"x" * (70 * 1024)  # > 64 KiB -> 64-bit length header
	assert ws.send(payload)
	assert wait_for(lambda: not ws_receive_empty(ws), timeout=10)
	assert ws.receive() == payload
	ws.close()


def test_fragmented_message_is_reassembled():
	server = EchoServer(fragment=True)
	try:
		ws = connect(server.url)
		text = "fragmented message payload"
		ws.send(text)
		assert wait_for(lambda: not ws_receive_empty(ws))
		assert ws.receive() == text
		ws.close()
	finally:
		server.close()


def test_server_fragments_arrive_whole(server):
	ws = connect(server.url)
	server.send_fragmented_text(["part one, ", "part two"])
	assert wait_for(lambda: not ws_receive_empty(ws))
	assert ws.receive() == "part one, part two"
	ws.close()


def test_ping_gets_ponged(server):
	ws = connect(server.url)
	server._send_frame(0x9, b"marco")
	assert wait_for(lambda: server.pongs == [b"marco"])
	ws.close()


def test_client_ping(server):
	ws = connect(server.url)
	assert ws.ping(b"hi")
	assert wait_for(lambda: not ws.connected or ws.receive() is None)
	ws.close()


def test_server_initiated_close(server):
	ws = connect(server.url)
	server.send_close(code=1000)
	assert wait_for(lambda: not ws.connected)
	assert ws.close_code == 1000


def test_client_close_sends_frame(server):
	ws = connect(server.url)
	ws.close(code=1000, reason="done")
	assert wait_for(lambda: server.received_close)
	assert not ws.connected
	assert ws.send("late") is False


def test_rejected_handshake_sets_error():
	server = EchoServer(reject=True)
	try:
		ws = WebSocket(server.url)
		assert wait_for(lambda: ws.error is not None)
		assert not ws.connected
		assert "404" in ws.error
	finally:
		server.close()


def test_bad_url():
	ws = WebSocket()
	assert ws.connect("http://example.com/") is True
	assert wait_for(lambda: ws.error is not None)
	assert not ws.connected
	assert ws.receive() is None
	assert WebSocket().connect() is False
