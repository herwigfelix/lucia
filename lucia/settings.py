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

"""Persistent per-user settings storage, modeled after NVGT's settings include.

Usage:

	settings = lucia.Settings("my_company", "my_game")
	settings["master_volume"] = -10
	settings.save()
	...
	volume = settings.get("master_volume", 0)

Data is stored per user (``%APPDATA%`` on Windows, ``~/Library/Application
Support`` on macOS, ``$XDG_CONFIG_HOME``/``~/.config`` elsewhere), either as
JSON (default) or INI.
"""

import configparser
import json
import os
import platform


def user_data_dir(company, product):
	"""Return (and create) the per-user data directory for a game."""
	system = platform.system()
	if system == "Windows":
		base = os.environ.get("APPDATA", os.path.expanduser("~"))
	elif system == "Darwin":
		base = os.path.expanduser("~/Library/Application Support")
	else:
		base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
	path = os.path.join(base, company, product)
	os.makedirs(path, exist_ok=True)
	return path


class Settings:
	"""Dictionary-like persistent settings.

	* company: your studio/author name (used for the storage folder)
	* product: the game name (used for the storage folder)
	* format: "json" (default) or "ini"
	* autosave: when True, every change is written to disk immediately
	"""

	def __init__(self, company, product, format="json", autosave=False):
		if format not in ("json", "ini"):
			raise ValueError('format must be "json" or "ini"')
		self.company = company
		self.product = product
		self.format = format
		self.autosave = autosave
		self.directory = user_data_dir(company, product)
		self.filename = os.path.join(self.directory, "settings." + format)
		self._data = {}
		self.load()

	def load(self):
		"""(Re)load settings from disk. Missing files leave the settings empty."""
		self._data = {}
		if not os.path.isfile(self.filename):
			return False
		try:
			if self.format == "json":
				with open(self.filename, "r", encoding="utf-8") as f:
					data = json.load(f)
				if isinstance(data, dict):
					self._data = data
			else:
				parser = configparser.ConfigParser()
				parser.read(self.filename, encoding="utf-8")
				if parser.has_section("settings"):
					self._data = dict(parser.items("settings"))
			return True
		except Exception:
			return False

	def save(self):
		"""Write settings to disk."""
		try:
			if self.format == "json":
				with open(self.filename, "w", encoding="utf-8") as f:
					json.dump(self._data, f, indent="\t", ensure_ascii=False)
			else:
				parser = configparser.ConfigParser()
				parser["settings"] = {k: str(v) for k, v in self._data.items()}
				with open(self.filename, "w", encoding="utf-8") as f:
					parser.write(f)
			return True
		except Exception:
			return False

	def get(self, key, default=None):
		return self._data.get(key, default)

	def set(self, key, value):
		self._data[key] = value
		if self.autosave:
			self.save()

	def delete(self, key):
		if key in self._data:
			del self._data[key]
			if self.autosave:
				self.save()
			return True
		return False

	def clear(self):
		self._data.clear()
		if self.autosave:
			self.save()

	def exists(self, key):
		return key in self._data

	def keys(self):
		return list(self._data.keys())

	# dict-style access
	def __getitem__(self, key):
		return self._data[key]

	def __setitem__(self, key, value):
		self.set(key, value)

	def __delitem__(self, key):
		self.delete(key)

	def __contains__(self, key):
		return key in self._data

	def __len__(self):
		return len(self._data)
