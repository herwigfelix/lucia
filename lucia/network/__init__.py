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

"""lucia's networking: HTTP/HTTPS helpers, a large-file downloader, an
NVGT-style game networking layer (see lucia.network.Network) and a
WebSocket client (see lucia.network.WebSocket)."""

from .http import (
	HttpRequest,
	download_file,
	url_decode,
	url_encode,
	url_get,
	url_post,
	url_request,
)
from .game import (
	EVENT_CONNECT,
	EVENT_DISCONNECT,
	EVENT_NONE,
	EVENT_RECEIVE,
	Network,
	NetworkEvent,
)
from .websocket import WebSocket
