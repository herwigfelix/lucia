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

"""Self-voicing dialogs, modeled after NVGT's virtual_dialogs include.

These are blocking convenience functions built on AudioForm:

	lucia.ui.alert("Warning", "Your save file is corrupted.")
	answer = lucia.ui.question("Quit", "Really quit the game?")  # "yes"/"no"
	name = lucia.ui.input_box("Name", "What is your name?")  # str or None
"""

import lucia
from .form import AudioForm


def _run_form(form, buttons):
	"""Run a form until one of the given button ids is pressed.
	Returns the pressed id."""
	while lucia.running:
		lucia.process_events()
		form.monitor()
		for id in buttons:
			if form.is_pressed(id):
				return id
	return -1


def alert(title, text, button="OK"):
	"""Show a message with a single button. Blocks until dismissed."""
	form = AudioForm(title)
	lucia.output.output(str(text), True)
	ok = form.create_button(button, primary=True, cancel=True)
	form.focused = ok
	_run_form(form, [ok])
	return True


def question(title, text, yes="Yes", no="No"):
	"""Ask a yes/no question. Returns "yes" or "no"."""
	form = AudioForm(title)
	lucia.output.output(str(text), True)
	yes_id = form.create_button(yes, primary=True)
	no_id = form.create_button(no, cancel=True)
	form.focused = yes_id
	pressed = _run_form(form, [yes_id, no_id])
	return "yes" if pressed == yes_id else "no"


def input_box(title, message, default="", password=False):
	"""Ask the user for a line of text. Returns the text, or None when
	cancelled with escape."""
	form = AudioForm(title)
	lucia.output.output(str(message), True)
	field = form.create_input_box(message, default, password=password)
	ok = form.create_button("OK", primary=True)
	cancel = form.create_button("Cancel", cancel=True)
	form.focused = field
	pressed = _run_form(form, [ok, cancel])
	if pressed == cancel:
		return None
	return form.get_text(field)
