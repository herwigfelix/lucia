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

"""Online-game networking, modeled after NVGT's enet-based network class,
implemented on plain UDP sockets (no native dependencies to build).

Server:

	net = lucia.network.Network()
	net.setup_server(12345, max_channels=4, max_peers=32)
	while lucia.running:
		event = net.request()
		if event.type == lucia.network.EVENT_CONNECT:
			print("peer", event.peer_id, "connected")
		elif event.type == lucia.network.EVENT_RECEIVE:
			net.send(event.peer_id, "echo: " + event.message, channel=0)
		elif event.type == lucia.network.EVENT_DISCONNECT:
			print("peer", event.peer_id, "left")
		lucia.process_events()

Client (as in NVGT, peer_id 1 is the server):

	net = lucia.network.Network()
	net.setup_client(max_channels=4, max_peers=1)
	net.connect("localhost", 12345)
	...
	net.send(1, "hello", channel=0, reliable=True)

Reliable packets are acknowledged, retransmitted and delivered in order
per channel; unreliable packets are fire-and-forget.
"""

import queue
import socket
import struct
import threading
import time

# event types (NVGT-compatible naming/values)
EVENT_NONE = 0
EVENT_CONNECT = 1
EVENT_DISCONNECT = 2
EVENT_RECEIVE = 3

# wire packet types
_PT_CONNECT = 1
_PT_CONNECT_ACK = 2
_PT_DISCONNECT = 3
_PT_DATA = 4
_PT_ACK = 5
_PT_PING = 6
_PT_PONG = 7

_MAGIC = 0xB5
# header: magic, type, channel, flags, seq
_HEADER = struct.Struct("!BBBBI")
_FLAG_RELIABLE = 1

_RESEND_INTERVAL = 0.2
_CONNECT_TIMEOUT = 5.0
_PEER_TIMEOUT = 10.0
_PING_INTERVAL = 2.0
_MAX_PACKET = 65000


class NetworkEvent:
	"""One network event: type, peer_id, channel and message."""

	def __init__(self, type=EVENT_NONE, peer_id=0, channel=0, message=""):
		self.type = type
		self.peer_id = peer_id
		self.channel = channel
		self.message = message

	def __repr__(self):
		names = {0: "none", 1: "connect", 2: "disconnect", 3: "receive"}
		return "<NetworkEvent %s peer=%d channel=%d len=%d>" % (
			names.get(self.type, self.type),
			self.peer_id,
			self.channel,
			len(self.message),
		)


class _Peer:
	def __init__(self, peer_id, address, max_channels):
		self.id = peer_id
		self.address = address
		self.connected = False
		self.last_heard = time.monotonic()
		self.last_ping = 0.0
		self.rtt = 0
		self._ping_sent = 0.0
		# per-channel sequence state
		self.send_seq = [0] * max_channels
		self.recv_seq = [0] * max_channels  # next expected reliable seq
		self.recv_buffer = [dict() for _ in range(max_channels)]  # out-of-order hold
		self.pending = {}  # (channel, seq) -> [payload_bytes, last_sent, first_sent]


class Network:
	"""An NVGT-style network endpoint (client or server) over UDP."""

	def __init__(self):
		self._socket = None
		self._thread = None
		self._events = queue.Queue()
		self._peers = {}  # peer_id -> _Peer
		self._by_address = {}  # address -> _Peer
		self._lock = threading.RLock()
		self._next_peer_id = 1
		self._is_server = False
		self._max_channels = 1
		self._max_peers = 1
		self._running = False
		self.bytes_sent = 0
		self.bytes_received = 0
		self.packets_sent = 0
		self.packets_received = 0

	# -- setup ---------------------------------------------------------------

	def setup_server(self, port, max_channels=1, max_peers=32, bind_address=""):
		"""Start listening for clients on the given UDP port."""
		return self._setup(True, port, max_channels, max_peers, bind_address)

	def setup_local_server(self, port, max_channels=1, max_peers=32):
		"""Like setup_server, but only reachable from this machine."""
		return self._setup(True, port, max_channels, max_peers, "127.0.0.1")

	def setup_client(self, max_channels=1, max_peers=1):
		"""Prepare this endpoint as a client (call connect() afterwards)."""
		return self._setup(False, 0, max_channels, max_peers, "")

	def _setup(self, is_server, port, max_channels, max_peers, bind_address):
		if self._socket is not None:
			return False
		self._is_server = is_server
		self._max_channels = max(1, min(255, int(max_channels)))
		self._max_peers = max(1, int(max_peers))
		sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			if is_server:
				sock.bind((bind_address, port))
			else:
				sock.bind((bind_address, 0))
		except OSError:
			sock.close()
			return False
		sock.settimeout(0.05)
		self._socket = sock
		self._running = True
		self._thread = threading.Thread(target=self._loop, daemon=True)
		self._thread.start()
		return True

	@property
	def active(self):
		return self._socket is not None

	# -- client connect ------------------------------------------------------

	def connect(self, host, port):
		"""Connect to a server (client mode). Returns the server's peer_id
		(always 1) immediately; wait for the EVENT_CONNECT event before
		sending. Returns 0 on error."""
		if self._socket is None or self._is_server:
			return 0
		try:
			address = (socket.gethostbyname(host), port)
		except OSError:
			return 0
		with self._lock:
			if self._by_address.get(address):
				return self._by_address[address].id
			peer = _Peer(1, address, self._max_channels)
			self._peers[1] = peer
			self._by_address[address] = peer
		# reliable connect: retransmitted by the pump until acked or timed out
		peer.pending[(-1, 0)] = [
			_HEADER.pack(_MAGIC, _PT_CONNECT, 0, 0, 0),
			0.0,
			time.monotonic(),
		]
		return 1

	# -- sending -------------------------------------------------------------

	def send(self, peer_id, message, channel=0, reliable=True):
		"""Send a message to a peer. Returns True when queued/sent."""
		if self._socket is None:
			return False
		if channel < 0 or channel >= self._max_channels:
			return False
		if isinstance(message, str):
			message = message.encode("utf-8")
		if len(message) > _MAX_PACKET:
			return False
		with self._lock:
			peer = self._peers.get(peer_id)
			if peer is None or not peer.connected:
				return False
			flags = _FLAG_RELIABLE if reliable else 0
			if reliable:
				seq = peer.send_seq[channel]
				peer.send_seq[channel] = (seq + 1) & 0xFFFFFFFF
			else:
				seq = 0
			packet = _HEADER.pack(_MAGIC, _PT_DATA, channel, flags, seq) + message
			if reliable:
				peer.pending[(channel, seq)] = [packet, 0.0, time.monotonic()]
			else:
				self._raw_send(packet, peer.address)
		return True

	def send_reliable(self, peer_id, message, channel=0):
		return self.send(peer_id, message, channel, True)

	def send_unreliable(self, peer_id, message, channel=0):
		return self.send(peer_id, message, channel, False)

	def broadcast(self, message, channel=0, reliable=True, exclude=None):
		"""Send a message to every connected peer (server convenience)."""
		with self._lock:
			ids = [p.id for p in self._peers.values() if p.connected]
		for peer_id in ids:
			if exclude is not None and peer_id == exclude:
				continue
			self.send(peer_id, message, channel, reliable)

	# -- events --------------------------------------------------------------

	def request(self, timeout=0):
		"""Return the next NetworkEvent. Waits up to `timeout` milliseconds;
		returns an EVENT_NONE event when nothing happened."""
		try:
			if timeout and timeout > 0:
				return self._events.get(timeout=timeout / 1000.0)
			return self._events.get_nowait()
		except queue.Empty:
			return NetworkEvent()

	# -- peers ---------------------------------------------------------------

	def get_peer_list(self):
		with self._lock:
			return [p.id for p in self._peers.values() if p.connected]

	@property
	def connected_peers(self):
		return len(self.get_peer_list())

	def get_peer_address(self, peer_id):
		with self._lock:
			peer = self._peers.get(peer_id)
			return ("%s:%d" % peer.address) if peer else ""

	def get_peer_average_round_trip_time(self, peer_id):
		"""Round trip time to a peer in milliseconds."""
		with self._lock:
			peer = self._peers.get(peer_id)
			return peer.rtt if peer else 0

	def disconnect_peer(self, peer_id):
		"""Disconnect a peer, notifying it."""
		with self._lock:
			peer = self._peers.get(peer_id)
			if peer is None:
				return False
			self._raw_send(_HEADER.pack(_MAGIC, _PT_DISCONNECT, 0, 0, 0), peer.address)
			self._drop_peer(peer, notify=False)
		return True

	def disconnect_peer_forcefully(self, peer_id):
		"""Drop a peer without notifying it."""
		with self._lock:
			peer = self._peers.get(peer_id)
			if peer is None:
				return False
			self._drop_peer(peer, notify=False)
		return True

	def destroy(self, flush=True):
		"""Shut the endpoint down, disconnecting all peers."""
		if self._socket is None:
			return
		with self._lock:
			for peer in list(self._peers.values()):
				try:
					self._raw_send(
						_HEADER.pack(_MAGIC, _PT_DISCONNECT, 0, 0, 0), peer.address
					)
				except Exception:
					pass
			self._peers.clear()
			self._by_address.clear()
		self._running = False
		if self._thread is not None:
			self._thread.join(timeout=1.0)
			self._thread = None
		try:
			self._socket.close()
		except Exception:
			pass
		self._socket = None

	# -- internals -----------------------------------------------------------

	def _raw_send(self, packet, address):
		try:
			self._socket.sendto(packet, address)
			self.bytes_sent += len(packet)
			self.packets_sent += 1
		except OSError:
			pass

	def _drop_peer(self, peer, notify=True):
		self._peers.pop(peer.id, None)
		self._by_address.pop(peer.address, None)
		if notify and peer.connected:
			self._events.put(NetworkEvent(EVENT_DISCONNECT, peer.id, 0, ""))

	def _loop(self):
		while self._running:
			try:
				packet, address = self._socket.recvfrom(65536)
				self.bytes_received += len(packet)
				self.packets_received += 1
				self._handle_packet(packet, address)
			except socket.timeout:
				pass
			except OSError:
				break
			self._pump()

	def _pump(self):
		"""Retransmit reliable packets, send pings, drop dead peers."""
		now = time.monotonic()
		with self._lock:
			for peer in list(self._peers.values()):
				# retransmits
				for key, entry in list(peer.pending.items()):
					packet, last_sent, first_sent = entry
					if now - first_sent > _CONNECT_TIMEOUT and not peer.connected:
						self._drop_peer(peer, notify=False)
						self._events.put(NetworkEvent(EVENT_DISCONNECT, peer.id, 0, ""))
						break
					if now - last_sent >= _RESEND_INTERVAL:
						self._raw_send(packet, peer.address)
						entry[1] = now
				# keepalive / timeout
				if peer.connected:
					if now - peer.last_heard > _PEER_TIMEOUT:
						self._drop_peer(peer)
						continue
					if now - peer.last_ping >= _PING_INTERVAL:
						peer.last_ping = now
						peer._ping_sent = now
						self._raw_send(
							_HEADER.pack(_MAGIC, _PT_PING, 0, 0, 0), peer.address
						)

	def _handle_packet(self, packet, address):
		if len(packet) < _HEADER.size:
			return
		magic, ptype, channel, flags, seq = _HEADER.unpack_from(packet)
		if magic != _MAGIC:
			return
		payload = packet[_HEADER.size:]
		with self._lock:
			peer = self._by_address.get(address)
			if ptype == _PT_CONNECT:
				if not self._is_server:
					return
				if peer is None:
					if len(self._peers) >= self._max_peers:
						return
					peer_id = self._next_peer_id + 1  # ids start at 2; 1 is "the server" from a client's view
					self._next_peer_id += 1
					peer = _Peer(peer_id, address, self._max_channels)
					peer.connected = True
					self._peers[peer_id] = peer
					self._by_address[address] = peer
					self._events.put(NetworkEvent(EVENT_CONNECT, peer_id, 0, ""))
				peer.last_heard = time.monotonic()
				self._raw_send(_HEADER.pack(_MAGIC, _PT_CONNECT_ACK, 0, 0, 0), address)
				return
			if peer is None:
				return
			peer.last_heard = time.monotonic()
			if ptype == _PT_CONNECT_ACK:
				peer.pending.pop((-1, 0), None)
				if not peer.connected:
					peer.connected = True
					self._events.put(NetworkEvent(EVENT_CONNECT, peer.id, 0, ""))
			elif ptype == _PT_DISCONNECT:
				self._drop_peer(peer)
			elif ptype == _PT_PING:
				self._raw_send(_HEADER.pack(_MAGIC, _PT_PONG, 0, 0, 0), address)
			elif ptype == _PT_PONG:
				if peer._ping_sent:
					peer.rtt = int((time.monotonic() - peer._ping_sent) * 1000)
					peer._ping_sent = 0.0
			elif ptype == _PT_ACK:
				peer.pending.pop((channel, seq), None)
			elif ptype == _PT_DATA:
				if channel >= self._max_channels:
					return
				if flags & _FLAG_RELIABLE:
					# always ack, even for duplicates
					self._raw_send(
						_HEADER.pack(_MAGIC, _PT_ACK, channel, 0, seq), address
					)
					expected = peer.recv_seq[channel]
					if seq == expected:
						self._deliver(peer, channel, payload)
						peer.recv_seq[channel] = (expected + 1) & 0xFFFFFFFF
						# flush any buffered successors
						while peer.recv_seq[channel] in peer.recv_buffer[channel]:
							next_seq = peer.recv_seq[channel]
							self._deliver(
								peer, channel, peer.recv_buffer[channel].pop(next_seq)
							)
							peer.recv_seq[channel] = (next_seq + 1) & 0xFFFFFFFF
					elif self._seq_newer(seq, expected):
						peer.recv_buffer[channel][seq] = payload
					# else: old duplicate, drop
				else:
					self._deliver(peer, channel, payload)

	@staticmethod
	def _seq_newer(seq, expected):
		return ((seq - expected) & 0xFFFFFFFF) < 0x80000000

	def _deliver(self, peer, channel, payload):
		try:
			message = payload.decode("utf-8")
		except UnicodeDecodeError:
			message = payload
		self._events.put(NetworkEvent(EVENT_RECEIVE, peer.id, channel, message))
