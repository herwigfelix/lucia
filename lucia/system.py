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

"""Clipboard access, window control and native file dialogs.

Everything is re-exported flat on the lucia module:

	lucia.clipboard_set_text("hello")
	text = lucia.clipboard_get_text()
	lucia.set_fullscreen(True)
	if not lucia.is_window_active(): pause_game()
	path = lucia.open_file_dialog(filters=[("Savegames", ["*.sav"])])

The file dialogs are the native OS dialogs (osascript on macOS,
PowerShell/WinForms on Windows, zenity or kdialog on Linux), because
those are what screen readers handle best. They block until the player
picks something and return None when cancelled.
"""

import os
import shutil
import subprocess
import sys
import time
import warnings

import pygame


# -- clipboard ----------------------------------------------------------------

def clipboard_set_text(text):
	"""Put text on the system clipboard. Returns True on success."""
	text = str(text)
	try:
		pygame.scrap.put_text(text)
		return True
	except Exception:
		pass
	return _clipboard_set_fallback(text)


def clipboard_get_text():
	"""The text on the system clipboard, or "" when empty or unavailable."""
	try:
		text = pygame.scrap.get_text()
		if text:
			return text
	except Exception:
		pass
	return _clipboard_get_fallback()


def _clipboard_set_fallback(text):
	if sys.platform == "darwin":
		commands = [["pbcopy"]]
	elif sys.platform.startswith("win"):
		commands = [["clip"]]
	else:
		commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]]
	for command in commands:
		if shutil.which(command[0]) is None:
			continue
		try:
			subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
			return True
		except Exception:
			continue
	return False


def _clipboard_get_fallback():
	if sys.platform == "darwin":
		commands = [["pbpaste"]]
	elif sys.platform.startswith("win"):
		commands = [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
	else:
		commands = [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b", "-o"]]
	for command in commands:
		if shutil.which(command[0]) is None:
			continue
		try:
			result = subprocess.run(command, capture_output=True, check=True, timeout=5)
			return result.stdout.decode("utf-8", "replace")
		except Exception:
			continue
	return ""


# -- window control -----------------------------------------------------------

def _pg_window():
	"""The pygame.Window for the current display window, or None."""
	if not pygame.display.get_init() or pygame.display.get_surface() is None:
		return None
	try:
		with warnings.catch_warnings():
			warnings.simplefilter("ignore")
			return pygame.Window.from_display_module()
	except Exception:
		return None


def set_window_title(title):
	"""Change the game window's title."""
	if pygame.display.get_init():
		pygame.display.set_caption(str(title))


def get_window_title():
	"""The game window's current title, or ""."""
	if not pygame.display.get_init():
		return ""
	try:
		return pygame.display.get_caption()[0]
	except Exception:
		return ""


def is_fullscreen():
	"""True while the game window is fullscreen."""
	surface = pygame.display.get_surface() if pygame.display.get_init() else None
	if surface is None:
		return False
	return bool(surface.get_flags() & pygame.FULLSCREEN)


def set_fullscreen(on=True):
	"""Switch the game window to fullscreen (desktop resolution) or back."""
	window = _pg_window()
	if window is None:
		return False
	try:
		if on:
			window.set_fullscreen(True)
		else:
			window.set_windowed()
		return True
	except Exception:
		return False


def toggle_fullscreen():
	"""Toggle fullscreen. Returns True when the window is now fullscreen."""
	target = not is_fullscreen()
	set_fullscreen(target)
	return is_fullscreen()


def minimize_window():
	"""Minimize the game window to the taskbar/dock."""
	if pygame.display.get_init() and pygame.display.get_surface() is not None:
		try:
			pygame.display.iconify()
			return True
		except Exception:
			return False
	return False


def restore_window():
	"""Restore and show the game window after minimize/hide."""
	window = _pg_window()
	if window is None:
		return False
	try:
		window.show()
		window.restore()
		return True
	except Exception:
		return False


def hide_window():
	"""Hide the game window entirely (restore_window brings it back)."""
	window = _pg_window()
	if window is None:
		return False
	try:
		window.hide()
		return True
	except Exception:
		return False


def focus_window():
	"""Ask the OS to give the game window input focus."""
	window = _pg_window()
	if window is None:
		return False
	try:
		window.focus()
		return True
	except Exception:
		return False


def is_window_active():
	"""True while the game window has keyboard focus.

	Poll this to pause gameplay and mute cues when the player alt-tabs
	away.
	"""
	if not pygame.display.get_init():
		return False
	return bool(pygame.key.get_focused())


# -- native file dialogs ------------------------------------------------------

def open_file_dialog(title="Open", filters=None, default_dir=None, multiple=False):
	"""Show the native file-open dialog. Blocks until closed.

	filters is a list of (description, patterns) tuples, e.g.
	[("Audio", ["*.ogg", "*.wav"])]. Returns the chosen path (a list of
	paths when multiple=True), or None when cancelled.
	"""
	output = _run_dialog("open", title=title, filters=filters, default_dir=default_dir, multiple=multiple)
	if output is None:
		return None
	paths = [line for line in output.splitlines() if line.strip()]
	if not paths:
		return None
	return paths if multiple else paths[0]


def save_file_dialog(title="Save", default_name="", default_dir=None, filters=None):
	"""Show the native file-save dialog. Returns the chosen path or None."""
	output = _run_dialog("save", title=title, filters=filters, default_dir=default_dir, default_name=default_name)
	if output is None:
		return None
	path = output.strip()
	return path or None


def select_folder_dialog(title="Select folder", default_dir=None):
	"""Show the native folder picker. Returns the chosen path or None."""
	output = _run_dialog("folder", title=title, default_dir=default_dir)
	if output is None:
		return None
	path = output.strip()
	return path or None


def _dialog_command(kind, platform_name, title="", filters=None, default_dir=None, default_name="", multiple=False):
	"""Build the subprocess command for a native dialog (separate for testing)."""
	if platform_name == "darwin":
		return _dialog_command_macos(kind, title, filters, default_dir, default_name, multiple)
	if platform_name.startswith("win"):
		return _dialog_command_windows(kind, title, filters, default_dir, default_name, multiple)
	return _dialog_command_linux(kind, title, filters, default_dir, default_name, multiple)


def _applescript_quote(text):
	return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dialog_command_macos(kind, title, filters, default_dir, default_name, multiple):
	prompt = _applescript_quote(title)
	extensions = []
	for _, patterns in (filters or []):
		for pattern in patterns:
			ext = pattern.rsplit(".", 1)[-1].strip("*. ")
			if ext and ext != "*":
				extensions.append(ext)
	if kind == "open":
		script = "choose file with prompt %s" % prompt
		if extensions:
			script += " of type {%s}" % ", ".join(_applescript_quote(e) for e in extensions)
		if default_dir:
			script += " default location POSIX file %s" % _applescript_quote(default_dir)
		if multiple:
			script += " with multiple selections allowed"
			body = (
				"set fs to (%s)\n"
				"set out to \"\"\n"
				"repeat with f in fs\n"
				"set out to out & POSIX path of f & \"\\n\"\n"
				"end repeat\n"
				"out"
			) % script
			return ["osascript", "-e", body]
		return ["osascript", "-e", "POSIX path of (%s)" % script]
	if kind == "save":
		script = "choose file name with prompt %s" % prompt
		if default_name:
			script += " default name %s" % _applescript_quote(default_name)
		if default_dir:
			script += " default location POSIX file %s" % _applescript_quote(default_dir)
		return ["osascript", "-e", "POSIX path of (%s)" % script]
	script = "choose folder with prompt %s" % prompt
	if default_dir:
		script += " default location POSIX file %s" % _applescript_quote(default_dir)
	return ["osascript", "-e", "POSIX path of (%s)" % script]


def _powershell_quote(text):
	return "'" + str(text).replace("'", "''") + "'"


def _dialog_command_windows(kind, title, filters, default_dir, default_name, multiple):
	lines = ["Add-Type -AssemblyName System.Windows.Forms"]
	if kind == "folder":
		lines.append("$d = New-Object System.Windows.Forms.FolderBrowserDialog")
		lines.append("$d.Description = %s" % _powershell_quote(title))
		if default_dir:
			lines.append("$d.SelectedPath = %s" % _powershell_quote(default_dir))
		lines.append("if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")
	else:
		dialog = "OpenFileDialog" if kind == "open" else "SaveFileDialog"
		lines.append("$d = New-Object System.Windows.Forms.%s" % dialog)
		lines.append("$d.Title = %s" % _powershell_quote(title))
		if filters:
			parts = []
			for description, patterns in filters:
				parts.append("%s|%s" % (description, ";".join(patterns)))
			lines.append("$d.Filter = %s" % _powershell_quote("|".join(parts)))
		if default_dir:
			lines.append("$d.InitialDirectory = %s" % _powershell_quote(default_dir))
		if kind == "save" and default_name:
			lines.append("$d.FileName = %s" % _powershell_quote(default_name))
		if kind == "open" and multiple:
			lines.append("$d.Multiselect = $true")
			lines.append("if ($d.ShowDialog() -eq 'OK') { $d.FileNames -join \"`n\" }")
		else:
			lines.append("if ($d.ShowDialog() -eq 'OK') { $d.FileName }")
	# -STA is required for WinForms dialogs.
	return ["powershell", "-STA", "-NoProfile", "-Command", "; ".join(lines)]


def _dialog_command_linux(kind, title, filters, default_dir, default_name, multiple):
	if shutil.which("zenity") is not None or shutil.which("kdialog") is None:
		command = ["zenity", "--file-selection", "--title", str(title)]
		if kind == "save":
			command.append("--save")
			if default_name:
				start = os.path.join(default_dir or "", default_name)
				command.extend(["--filename", start])
			elif default_dir:
				command.extend(["--filename", default_dir.rstrip("/") + "/"])
		elif kind == "folder":
			command.append("--directory")
			if default_dir:
				command.extend(["--filename", default_dir.rstrip("/") + "/"])
		else:
			if multiple:
				command.extend(["--multiple", "--separator", "\n"])
			if default_dir:
				command.extend(["--filename", default_dir.rstrip("/") + "/"])
		for description, patterns in (filters or []):
			command.extend(["--file-filter", "%s | %s" % (description, " ".join(patterns))])
		return command
	start = default_dir or "."
	if kind == "save":
		start = os.path.join(default_dir or ".", default_name or "")
		return ["kdialog", "--title", str(title), "--getsavefilename", start]
	if kind == "folder":
		return ["kdialog", "--title", str(title), "--getexistingdirectory", start]
	command = ["kdialog", "--title", str(title), "--getopenfilename", start]
	if multiple:
		command.insert(1, "--multiple")
	return command


def _run_dialog(kind, **options):
	command = _dialog_command(kind, sys.platform, **options)
	if shutil.which(command[0]) is None:
		return None
	try:
		process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
	except Exception:
		return None
	# Keep pumping events so the OS doesn't flag the game window as
	# unresponsive while the (blocking) dialog is open.
	while process.poll() is None:
		if pygame.display.get_init():
			try:
				pygame.event.pump()
			except Exception:
				pass
		time.sleep(0.05)
	if process.returncode != 0:
		return None  # cancelled (or the dialog tool failed)
	output = process.stdout.read().decode("utf-8", "replace") if process.stdout else ""
	return output
