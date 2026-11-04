"""Tests for clipboard, window control and native file dialogs."""

import os
import sys

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia import system


@pytest.fixture(scope="module")
def engine():
	lucia.initialize()
	lucia.show_window("system test")
	yield
	lucia.quit()


def test_clipboard_roundtrip(engine):
	if not lucia.clipboard_set_text("lucia clipboard test"):
		pytest.skip("no clipboard available on this machine")
	assert lucia.clipboard_get_text() == "lucia clipboard test"


def test_window_title_roundtrip(engine):
	lucia.set_window_title("A new title")
	assert lucia.get_window_title() == "A new title"


def test_window_state_queries(engine):
	assert isinstance(lucia.is_window_active(), bool)
	assert isinstance(lucia.is_fullscreen(), bool)
	# Under the dummy driver these are best-effort; they must not raise.
	lucia.minimize_window()
	lucia.restore_window()
	lucia.hide_window()
	lucia.focus_window()


def test_window_functions_without_display():
	# Before any window exists everything is a graceful no-op.
	import pygame

	was_init = pygame.display.get_init()
	if was_init:
		pytest.skip("display already open in this process")
	assert system.get_window_title() == ""
	assert system.is_window_active() is False
	assert system.set_fullscreen(True) is False
	assert system.minimize_window() is False


def test_dialog_command_macos():
	command = system._dialog_command(
		"open", "darwin", title="Pick", filters=[("Audio", ["*.ogg", "*.wav"])]
	)
	assert command[0] == "osascript"
	script = command[2]
	assert "choose file" in script
	assert '"Pick"' in script
	assert '"ogg"' in script and '"wav"' in script
	save = system._dialog_command("save", "darwin", title="Save", default_name="game.sav")
	assert "choose file name" in save[2]
	assert '"game.sav"' in save[2]
	folder = system._dialog_command("folder", "darwin", title="Folder")
	assert "choose folder" in folder[2]
	multi = system._dialog_command("open", "darwin", title="Pick", multiple=True)
	assert "multiple selections allowed" in multi[2]


def test_dialog_command_windows():
	command = system._dialog_command(
		"open", "win32", title="Pick", filters=[("Saves", ["*.sav"])], multiple=True
	)
	assert command[0] == "powershell"
	assert "-STA" in command
	script = command[-1]
	assert "OpenFileDialog" in script
	assert "Saves|*.sav" in script
	assert "Multiselect" in script
	save = system._dialog_command("save", "win32", title="Save", default_name="x.sav")
	assert "SaveFileDialog" in save[-1]
	folder = system._dialog_command("folder", "win32", title="F")
	assert "FolderBrowserDialog" in folder[-1]


def test_dialog_command_linux():
	command = system._dialog_command(
		"open", "linux", title="Pick", filters=[("Audio", ["*.ogg"])], multiple=True
	)
	assert command[0] in ("zenity", "kdialog")
	if command[0] == "zenity":
		assert "--file-selection" in command
		assert "--multiple" in command
		assert any("Audio | *.ogg" in part for part in command)
		save = system._dialog_command("save", "linux", title="Save")
		assert "--save" in save
		folder = system._dialog_command("folder", "linux", title="F")
		assert "--directory" in folder


def test_open_file_dialog_parses_output(monkeypatch):
	class FakeStdout:
		def read(self):
			return b"/tmp/a.ogg\n/tmp/b.ogg\n"

	class FakeProcess:
		returncode = 0
		stdout = FakeStdout()

		def poll(self):
			return 0

	monkeypatch.setattr(system.shutil, "which", lambda name: "/usr/bin/" + name)
	monkeypatch.setattr(system.subprocess, "Popen", lambda *a, **k: FakeProcess())
	assert system.open_file_dialog(multiple=True) == ["/tmp/a.ogg", "/tmp/b.ogg"]
	assert system.open_file_dialog() == "/tmp/a.ogg"


def test_dialog_cancel_returns_none(monkeypatch):
	class FakeProcess:
		returncode = 1
		stdout = None

		def poll(self):
			return 1

	monkeypatch.setattr(system.shutil, "which", lambda name: "/usr/bin/" + name)
	monkeypatch.setattr(system.subprocess, "Popen", lambda *a, **k: FakeProcess())
	assert system.open_file_dialog() is None
	assert system.save_file_dialog() is None
	assert system.select_folder_dialog() is None


def test_dialog_missing_tool_returns_none(monkeypatch):
	monkeypatch.setattr(system.shutil, "which", lambda name: None)
	assert system.open_file_dialog() is None
