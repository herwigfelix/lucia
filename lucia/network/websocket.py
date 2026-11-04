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

"""A WebSocket client (RFC 6455) for ws:// and wss:// URLs.

Pure standard library, no extra dependencies, and poll-based like the
rest of lucia's networking — the game loop stays in charge:

	ws = lucia.network.WebSocket("wss://example.com/game")
	while lucia.running:
		lucia.process_events()
		while (message := ws.receive()) is not None:
			handle(message)     # str for text frames, bytes for binary
		if fired:
			ws.send("pew")

Reading happens in a daemon thread; receive() never blocks. Use
WebSocket to talk to web/cloud servers (TLS via wss, firewall-friendly
on 443). For real-time game servers, lucia.network.Network (UDP)
remains the first choice.
"""

import base64
import hashlib
import os
import queue
import socket
import ssl
import struct
import threading
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_MESSAGE = 16 * 1024 * 1024  # guard against memory bombs

_OP_CONTINUATION = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WebSocket:
	"""One WebSocket client connection.

	Passing a url to the constructor connects immediately; otherwise
	call connect(url) later. Poll `connected`, `error` and receive()
	from the game loop.
	"""

	def __init__(self, url=None, headers=None, protocols=None, timeout=10):
		self.headers = headers or {}
		self.protocols = protocols or []
		self.timeout = timeout
		self.error = None
		self.close_code = None
		self.close_reason = None
		self._url = url
		self._socket = None
		self._connected = False
		self._connecting = False
		self._closing = False
		self._messages = queue.Queue()
		self._send_lock = threading.Lock()
		self._thread = None
		if url:
			self.connect()

	@property
	def connecting(self):
		"""True while the handshake is still in progress."""
		return self._connecting

	@property
	def connected(self):
		"""True while the connection is open and usable."""
		return self._connected

	def connect(self, url=None):
		"""Start connecting (handshake runs in the background). Returns True
		when the attempt was started."""
		if self._connected or self._connecting:
			return False
		if url:
			self._url = url
		if not self._url:
			self.error = "no URL to connect to"
			return False
		self.error = None
		self.close_code = None
		self.close_reason = None
		self._closing = False
		self._connecting = True
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()
		return True

	def send(self, data):
		"""Send a message: str becomes a text frame, bytes a binary frame.

		Returns False when not connected.
		"""
		if not self._connected:
			return False
		if isinstance(data, str):
			return self._send_frame(_OP_TEXT, data.encode("utf-8"))
		return self._send_frame(_OP_BINARY, bytes(data))

	def receive(self):
		"""The next received message (str for text, bytes for binary), or
		None when nothing is waiting. Never blocks."""
		try:
			return self._messages.get_nowait()
		except queue.Empty:
			return None

	def ping(self, data=b""):
		"""Send a ping frame (the server answers with a pong)."""
		if not self._connected:
			return False
		return self._send_frame(_OP_PING, bytes(data)[:125])

	def close(self, code=1000, reason=""):
		"""Start the closing handshake and shut the connection down."""
		if self._connected and not self._closing:
			self._closing = True
			payload = struct.pack(">H", code) + str(reason).encode("utf-8")[:123]
			self._send_frame(_OP_CLOSE, payload)
		self._shutdown()

	# -- internals -----------------------------------------------------------

	def _run(self):
		try:
			self._handshake()
		except Exception as exc:
			self.error = str(exc) or exc.__class__.__name__
			self._connecting = False
			self._shutdown()
			return
		self._connecting = False
		self._connected = True
		try:
			self._read_loop()
		except Exception as exc:
			if not self._closing:
				self.error = str(exc) or exc.__class__.__name__
		finally:
			self._shutdown()

	def _handshake(self):
		parsed = urllib.parse.urlsplit(self._url)
		if parsed.scheme not in ("ws", "wss"):
			raise ValueError("unsupported URL scheme %r" % parsed.scheme)
		host = parsed.hostname
		if not host:
			raise ValueError("no host in URL %r" % self._url)
		port = parsed.port or (443 if parsed.scheme == "wss" else 80)
		path = parsed.path or "/"
		if parsed.query:
			path += "?" + parsed.query
		sock = socket.create_connection((host, port), timeout=self.timeout)
		if parsed.scheme == "wss":
			context = ssl.create_default_context()
			sock = context.wrap_socket(sock, server_hostname=host)
		key = base64.b64encode(os.urandom(16)).decode("ascii")
		host_header = host if port in (80, 443) else "%s:%d" % (host, port)
		lines = [
			"GET %s HTTP/1.1" % path,
			"Host: %s" % host_header,
			"Upgrade: websocket",
			"Connection: Upgrade",
			"Sec-WebSocket-Key: %s" % key,
			"Sec-WebSocket-Version: 13",
		]
		if self.protocols:
			lines.append("Sec-WebSocket-Protocol: %s" % ", ".join(self.protocols))
		for name, value in self.headers.items():
			lines.append("%s: %s" % (name, value))
		sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
		response = b""
		while b"\r\n\r\n" not in response:
			chunk = sock.recv(4096)
			if not chunk:
				raise ConnectionError("connection closed during handshake")
			response += chunk
			if len(response) > 65536:
				raise ConnectionError("oversized handshake response")
		head, _, rest = response.partition(b"\r\n\r\n")
		header_lines = head.decode("iso-8859-1").split("\r\n")
		status_parts = header_lines[0].split(" ", 2)
		if len(status_parts) < 2 or status_parts[1] != "101":
			raise ConnectionError("handshake rejected: %s" % header_lines[0])
		response_headers = {}
		for line in header_lines[1:]:
			name, _, value = line.partition(":")
			response_headers[name.strip().lower()] = value.strip()
		expected = base64.b64encode(
			hashlib.sha1((key + _GUID).encode("ascii")).digest()
		).decode("ascii")
		if response_headers.get("sec-websocket-accept") != expected:
			raise ConnectionError("invalid Sec-WebSocket-Accept from server")
		sock.settimeout(None)
		self._socket = sock
		self._pending = rest  # bytes that arrived after the handshake

	def _read_loop(self):
		buffer = self._pending
		self._pending = b""
		fragments = []
		fragment_opcode = None
		while True:
			frame, buffer = self._read_frame(buffer)
			if frame is None:
				return
			fin, opcode, payload = frame
			if opcode == _OP_PING:
				self._send_frame(_OP_PONG, payload)
				continue
			if opcode == _OP_PONG:
				continue
			if opcode == _OP_CLOSE:
				if len(payload) >= 2:
					self.close_code = struct.unpack(">H", payload[:2])[0]
					self.close_reason = payload[2:].decode("utf-8", "replace")
				else:
					self.close_code = 1005
					self.close_reason = ""
				if not self._closing:
					self._closing = True
					self._send_frame(_OP_CLOSE, payload[:2])
				return
			if opcode in (_OP_TEXT, _OP_BINARY):
				if fin and not fragments:
					self._deliver(opcode, payload)
				else:
					fragments = [payload]
					fragment_opcode = opcode
			elif opcode == _OP_CONTINUATION and fragments:
				fragments.append(payload)
				if sum(len(f) for f in fragments) > _MAX_MESSAGE:
					raise ConnectionError("message too large")
				if fin:
					self._deliver(fragment_opcode, b"".join(fragments))
					fragments = []
					fragment_opcode = None

	def _deliver(self, opcode, payload):
		if opcode == _OP_TEXT:
			self._messages.put(payload.decode("utf-8", "replace"))
		else:
			self._messages.put(payload)

	def _read_frame(self, buffer):
		header = self._read_exact(buffer, 2)
		if header is None:
			return None, buffer
		data, buffer = header
		first, second = data[0], data[1]
		fin = bool(first & 0x80)
		opcode = first & 0x0F
		length = second & 0x7F
		if length == 126:
			result = self._read_exact(buffer, 2)
			if result is None:
				return None, buffer
			data, buffer = result
			length = struct.unpack(">H", data)[0]
		elif length == 127:
			result = self._read_exact(buffer, 8)
			if result is None:
				return None, buffer
			data, buffer = result
			length = struct.unpack(">Q", data)[0]
		if length > _MAX_MESSAGE:
			raise ConnectionError("frame too large")
		mask = b""
		if second & 0x80:  # servers must not mask, but tolerate it
			result = self._read_exact(buffer, 4)
			if result is None:
				return None, buffer
			mask, buffer = result
		payload = b""
		if length:
			result = self._read_exact(buffer, length)
			if result is None:
				return None, buffer
			payload, buffer = result
		if mask:
			payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
		return (fin, opcode, payload), buffer

	def _read_exact(self, buffer, n):
		"""Take n bytes from buffer, topping it up from the socket.

		Returns (data, remaining_buffer), or None on a clean EOF.
		"""
		while len(buffer) < n:
			try:
				chunk = self._socket.recv(65536)
			except (OSError, ValueError):
				return None
			if not chunk:
				return None
			buffer += chunk
		return buffer[:n], buffer[n:]

	def _send_frame(self, opcode, payload):
		sock = self._socket
		if sock is None:
			return False
		mask = os.urandom(4)
		masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
		header = bytes([0x80 | opcode])
		length = len(payload)
		if length < 126:
			header += bytes([0x80 | length])
		elif length < 65536:
			header += bytes([0x80 | 126]) + struct.pack(">H", length)
		else:
			header += bytes([0x80 | 127]) + struct.pack(">Q", length)
		try:
			with self._send_lock:
				sock.sendall(header + mask + masked)
			return True
		except (OSError, ValueError):
			return False

	def _shutdown(self):
		self._connected = False
		self._connecting = False
		sock = self._socket
		self._socket = None
		if sock is not None:
			try:
				sock.close()
			except OSError:
				pass
