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

"""HTTP/HTTPS support for games, modeled after NVGT's url_get/http APIs.

Simple one-liners:

	text = lucia.network.url_get("https://example.com/motd.txt")
	text = lucia.network.url_post("https://example.com/api", "a=1&b=2")

Asynchronous requests with progress (poll inside your game loop):

	req = lucia.network.HttpRequest("https://example.com/bigfile.zip", path="bigfile.zip")
	while not req.complete:
		lucia.process_events()
	print(req.status_code)

Large-file downloader with resume support:

	lucia.network.download_file(url, "patch.zip", progress_callback=my_cb)
"""

import os
import threading
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = "lucia/2.0"
_CHUNK_SIZE = 64 * 1024


def url_encode(text):
	"""Percent-encode a string for use inside a URL."""
	return urllib.parse.quote(text, safe="")


def url_decode(text, plus_as_space=True):
	"""Decode a percent-encoded string."""
	if plus_as_space:
		return urllib.parse.unquote_plus(text)
	return urllib.parse.unquote(text)


def url_request(method, url, data=None, headers=None, timeout=30):
	"""Perform a blocking HTTP request and return the response body as text.

	Raises urllib.error.HTTPError / URLError on failure.
	"""
	if isinstance(data, str):
		data = data.encode("utf-8")
	request_headers = {"User-Agent": DEFAULT_USER_AGENT}
	if headers:
		request_headers.update(headers)
	req = urllib.request.Request(
		url, data=data, headers=request_headers, method=method.upper()
	)
	with urllib.request.urlopen(req, timeout=timeout) as response:
		body = response.read()
	charset = "utf-8"
	try:
		return body.decode(charset)
	except UnicodeDecodeError:
		return body.decode(charset, errors="replace")


def url_get(url, headers=None, timeout=30):
	"""Fetch a URL and return the response body as text."""
	return url_request("GET", url, None, headers, timeout)


def url_post(url, data, headers=None, timeout=30):
	"""POST data to a URL and return the response body as text."""
	return url_request("POST", url, data, headers, timeout)


class HttpRequest:
	"""An asynchronous HTTP request, polled from the game loop.

	* url: the URL to fetch
	* method: HTTP method (default GET)
	* data: optional request body (str or bytes)
	* headers: optional dict of extra request headers
	* path: when given, the response body is streamed to this file
	  instead of being kept in memory (for downloads)
	* timeout: socket timeout in seconds

	Properties: running, complete, status_code, response_body,
	response_headers, error, bytes_downloaded, download_size,
	download_percent. Call wait() to block until finished, cancel()
	to abort.
	"""

	def __init__(self, url, method="GET", data=None, headers=None, path=None, timeout=30):
		self.url = url
		self.method = method.upper()
		self.path = path
		self.timeout = timeout
		self.status_code = 0
		self.response_body = b""
		self.response_headers = {}
		self.error = None
		self.bytes_downloaded = 0
		self.download_size = 0
		self._data = data.encode("utf-8") if isinstance(data, str) else data
		self._headers = {"User-Agent": DEFAULT_USER_AGENT}
		if headers:
			self._headers.update(headers)
		self._cancel = threading.Event()
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	@property
	def running(self):
		return self._thread.is_alive()

	@property
	def complete(self):
		return not self.running

	@property
	def download_percent(self):
		if self.download_size <= 0:
			return 0
		return min(100, int(self.bytes_downloaded * 100 / self.download_size))

	@property
	def text(self):
		"""The response body decoded as text."""
		if isinstance(self.response_body, bytes):
			return self.response_body.decode("utf-8", errors="replace")
		return self.response_body

	def wait(self):
		"""Block until the request has finished, then return self."""
		self._thread.join()
		return self

	def cancel(self):
		"""Abort the request. An in-flight transfer stops at the next chunk."""
		self._cancel.set()

	def _run(self):
		try:
			req = urllib.request.Request(
				self.url, data=self._data, headers=self._headers, method=self.method
			)
			with urllib.request.urlopen(req, timeout=self.timeout) as response:
				self.status_code = response.status
				self.response_headers = dict(response.headers.items())
				length = response.headers.get("Content-Length")
				self.download_size = int(length) if length else 0
				if self.path:
					with open(self.path, "wb") as f:
						while not self._cancel.is_set():
							chunk = response.read(_CHUNK_SIZE)
							if not chunk:
								break
							f.write(chunk)
							self.bytes_downloaded += len(chunk)
				else:
					parts = []
					while not self._cancel.is_set():
						chunk = response.read(_CHUNK_SIZE)
						if not chunk:
							break
						parts.append(chunk)
						self.bytes_downloaded += len(chunk)
					self.response_body = b"".join(parts)
		except urllib.error.HTTPError as e:
			self.status_code = e.code
			try:
				self.response_body = e.read()
			except Exception:
				pass
			self.error = str(e)
		except Exception as e:
			self.error = str(e)


def download_file(url, path, progress_callback=None, resume=True, timeout=30, headers=None):
	"""Download a (large) file to disk, with resume support.

	* url: the file URL
	* path: destination file path
	* progress_callback: optional callable(bytes_downloaded, total_bytes)
	  called after every chunk; total_bytes is 0 when unknown
	* resume: when True and a partial file exists, continue it via a
	  HTTP Range request (falls back to a full download when the server
	  doesn't support ranges)

	Returns True on success. Raises on network errors.
	"""
	request_headers = {"User-Agent": DEFAULT_USER_AGENT}
	if headers:
		request_headers.update(headers)
	partial = path + ".part"
	offset = 0
	if resume and os.path.isfile(partial):
		offset = os.path.getsize(partial)
	if offset > 0:
		request_headers["Range"] = "bytes=%d-" % offset
	req = urllib.request.Request(url, headers=request_headers)
	try:
		response = urllib.request.urlopen(req, timeout=timeout)
	except urllib.error.HTTPError as e:
		if e.code == 416 and offset > 0:
			# Range not satisfiable: our partial file is already complete.
			os.replace(partial, path)
			return True
		raise
	with response:
		mode = "ab" if (offset > 0 and response.status == 206) else "wb"
		if mode == "wb":
			offset = 0
		length = response.headers.get("Content-Length")
		total = (int(length) + offset) if length else 0
		downloaded = offset
		with open(partial, mode) as f:
			while True:
				chunk = response.read(_CHUNK_SIZE)
				if not chunk:
					break
				f.write(chunk)
				downloaded += len(chunk)
				if progress_callback:
					progress_callback(downloaded, total)
	os.replace(partial, path)
	return True
