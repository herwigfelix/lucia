import time

from lucia.network import game as g
from lucia.network import url_decode, url_encode


def test_url_encoding():
	assert url_encode("a b&c") == "a%20b%26c"
	assert url_decode("a%20b%26c") == "a b&c"
	assert url_decode("a+b") == "a b"


def _pump(net, want, timeout=5):
	deadline = time.time() + timeout
	events = []
	while time.time() < deadline and len(events) < want:
		event = net.request(50)
		if event.type != g.EVENT_NONE:
			events.append(event)
	return events


def test_client_server_roundtrip():
	server = g.Network()
	assert server.setup_server(38491, max_channels=4, max_peers=4)
	client = g.Network()
	assert client.setup_client(max_channels=4)
	assert client.connect("127.0.0.1", 38491) == 1
	try:
		client_events = _pump(client, 1)
		server_events = _pump(server, 1)
		assert client_events and client_events[0].type == g.EVENT_CONNECT
		assert server_events and server_events[0].type == g.EVENT_CONNECT
		peer = server_events[0].peer_id

		# reliable ordered delivery
		for i in range(10):
			assert client.send(1, "msg%d" % i, channel=2, reliable=True)
		received = _pump(server, 10)
		assert [e.message for e in received] == ["msg%d" % i for i in range(10)]
		assert all(e.channel == 2 for e in received)

		# server -> client
		assert server.send(peer, "welcome", channel=0)
		back = _pump(client, 1)
		assert back and back[0].message == "welcome"

		# unreliable
		assert client.send_unreliable(1, "fast", channel=1)
		fast = _pump(server, 1)
		assert fast and fast[0].message == "fast"

		assert server.connected_peers == 1
		assert server.get_peer_address(peer).startswith("127.0.0.1")
	finally:
		client.destroy()
		server.destroy()


def test_send_to_unknown_peer_fails():
	net = g.Network()
	assert net.setup_client()
	try:
		assert not net.send(99, "nope")
	finally:
		net.destroy()
