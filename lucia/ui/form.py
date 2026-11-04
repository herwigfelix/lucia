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

"""AudioForm: a self-voicing, keyboard-driven form/dialog system,
modeled after NVGT's audio_form include.

Controls: buttons, input boxes (single and multiline), lists, tab
panels, checkboxes, sliders, progress bars, status bars, links,
keyboard areas and nested subforms.
Navigation: tab/shift+tab between controls; arrows, space, enter and
first-letter navigation inside them; enter descends into a subform,
escape (without a cancel button there) comes back out.

Example:

	form = lucia.ui.AudioForm("Options")
	name = form.create_input_box("Your name", "player")
	notes = form.create_input_box("Notes", multiline=True)
	fx = form.create_checkbox("Sound effects", True)
	vol = form.create_slider("Volume", 80, 0, 100, step=5)
	ok = form.create_button("Save", primary=True)
	cancel = form.create_button("Cancel", cancel=True)
	form.focus(name)
	while lucia.running:
		lucia.process_events()
		form.monitor()
		if form.is_pressed(ok):
			... form.get_text(name), form.is_checked(fx), form.get_slider(vol) ...
			break
		if form.is_pressed(cancel):
			break
"""

import string
import webbrowser

import lucia
from .virtualinput import WHITELIST_ALL

CT_BUTTON = 0
CT_INPUT = 1
CT_LIST = 2
CT_CHECKBOX = 3
CT_SLIDER = 4
CT_TAB_PANEL = 5
CT_PROGRESS = 6
CT_STATUS = 7
CT_LINK = 8
CT_KEYBOARD_AREA = 9
CT_FORM = 10


class _Control:
	def __init__(self, type, caption):
		self.type = type
		self.caption = caption
		self.enabled = True
		self.visible = True
		# button
		self.primary = False
		self.cancel = False
		self.pressed = False
		# input
		self.text = ""
		self.cursor = 0
		self.password = False
		self.max_length = 0
		self.read_only = False
		self.whitelist = WHITELIST_ALL
		self.hidden_message = "hidden"
		self.multiline = False
		self.multiline_enter = True
		# text selection: anchor position, or None when nothing is selected
		self.sel_anchor = None
		# user-interaction callback: fn(form, id), set via AudioForm.on_change
		self.on_change = None
		# list / tab panel / checklist
		self.items = []
		self.index = 0
		self.multiselect = False
		self.checklist = False
		self.selections = set()  # multiselect selections / checked indices
		# checkbox
		self.checked = False
		# slider / progress
		self.value = 0
		self.minimum = 0
		self.maximum = 100
		self.step = 1
		self.value_text = ""
		self.speak_interval = 5
		self.speak_global = True
		self._progress_bucket = 0
		# link
		self.url = ""
		# subform
		self.subform = None

	def type_name(self):
		return {
			CT_BUTTON: "button",
			CT_INPUT: "edit",
			CT_LIST: "list",
			CT_CHECKBOX: "checkbox",
			CT_SLIDER: "slider",
			CT_TAB_PANEL: "tab panel",
			CT_PROGRESS: "progress bar",
			CT_STATUS: "status bar",
			CT_LINK: "link",
			CT_KEYBOARD_AREA: "keyboard area",
			CT_FORM: "subform",
		}[self.type]


def _parse_access_key(caption):
	"""Windows convention: "&File" -> ("File", "f"). No "&" -> no key."""
	caption = str(caption)
	index = caption.find("&")
	letter = None
	if index != -1 and index + 1 < len(caption):
		letter = caption[index + 1].lower()
	return caption.replace("&", ""), letter


class _MenuEntry:
	"""One entry of the menu system: a top-level menu, a submenu or an item.

	Menus and submenus carry a `children` list; plain items carry an id
	(and optionally a callback) instead.
	"""

	def __init__(self, caption, children=None, id=None, callback=None):
		self.caption, self.letter = _parse_access_key(caption)
		self.children = children
		self.id = id
		self.callback = callback

	@property
	def is_submenu(self):
		return self.children is not None


def _say(text):
	"""Speak and braille feedback to a key press. The newest feedback cuts
	off the previous one, as in every screen reader, so fast typing or
	scrolling never piles up a queue of stale announcements."""
	lucia.output.output(text, True)


class AudioForm:
	"""A self-voicing form with keyboard navigation.

	Call monitor() once per frame (after lucia.process_events()); it
	handles navigation and interaction and speaks all feedback through
	lucia.output.
	"""

	def __init__(self, title="", say_title=True):
		self.title = title
		self.controls = []
		self.focused = -1
		self._announced = False
		self._say_title = say_title
		# id of the subform control the player has descended into, or None
		self._entered = None
		# menu bar (Windows model: alt opens it, see add_menu)
		self._menus = []
		self._menu_mode = None  # None (closed) | "bar" | "menu"
		self._menu_bar_index = 0
		self._menu_path = []  # stack of [menu_entry, selected_index]
		self._menu_activated = None
		self._menu_item_counter = 0

	# -- menu bar (Windows model) --------------------------------------------

	def add_menu(self, caption):
		"""Add a top-level menu to the form's menu bar, e.g. add_menu("&File").

		The "&" marks the access key (alt, then f). The menu bar follows
		the Windows model: alt opens it, left/right move between menus
		(spoken as "File submenu"), down/enter opens the focused menu,
		escape steps back out, alt closes everything. Returns a menu
		handle for add_menu_item()/add_submenu().
		"""
		entry = _MenuEntry(caption, children=[])
		self._menus.append(entry)
		return entry

	def add_menu_item(self, menu, caption, callback=None):
		"""Add an activatable item to a menu or submenu, e.g.
		add_menu_item(file_menu, "&Open"). Returns an item id; poll
		menu_activated() for it, or pass callback(form, id)."""
		self._menu_item_counter += 1
		entry = _MenuEntry(caption, id=self._menu_item_counter, callback=callback)
		menu.children.append(entry)
		return entry.id

	def add_submenu(self, menu, caption):
		"""Add a nested submenu (spoken as "<name> submenu"); right arrow
		or enter walks into it, left arrow walks back out. Returns a menu
		handle for further add_menu_item()/add_submenu() calls."""
		entry = _MenuEntry(caption, children=[])
		menu.children.append(entry)
		return entry

	def menu_activated(self):
		"""The id of the menu item the player activated this frame, or
		None (cleared on read — poll it once per frame)."""
		activated = self._menu_activated
		self._menu_activated = None
		return activated

	def close_menus(self):
		"""Programmatically close the menu bar (no-op when closed)."""
		self._menu_mode = None
		self._menu_path = []

	# -- control creation ----------------------------------------------------

	def _add(self, control):
		self.controls.append(control)
		if self.focused == -1:
			self.focused = 0
		return len(self.controls) - 1

	def create_button(self, caption, primary=False, cancel=False):
		"""A button. primary: activated by enter anywhere on the form;
		cancel: activated by escape anywhere on the form."""
		c = _Control(CT_BUTTON, caption)
		c.primary = primary
		c.cancel = cancel
		return self._add(c)

	def create_input_box(
		self,
		caption,
		default_text="",
		password=False,
		max_length=0,
		read_only=False,
		whitelist=None,
		multiline=False,
		multiline_enter=True,
	):
		"""A text input box. With multiline, up/down move between lines
		and (when multiline_enter) enter inserts a new line instead of
		pressing the primary button."""
		c = _Control(CT_INPUT, caption)
		c.text = str(default_text)
		c.cursor = len(c.text)
		c.password = password
		c.max_length = max_length
		c.read_only = read_only
		c.multiline = multiline
		c.multiline_enter = multiline_enter
		if whitelist is not None:
			c.whitelist = whitelist
		return self._add(c)

	def create_list(self, caption, items=None, multiselect=False):
		"""A list of items, browsed with the arrow keys. With
		multiselect, space toggles selection of the current item."""
		c = _Control(CT_LIST, caption)
		c.items = list(items) if items else []
		c.multiselect = multiselect
		return self._add(c)

	def create_checklist(self, caption, items=None, checked=None):
		"""A list whose items each carry an on/off state, toggled with
		space (spoken as "checked"/"unchecked"). Navigation works exactly
		like a normal list. checked: iterable of initially checked
		indices, or True to start with every item checked. Query with
		get_checked_items()/is_item_checked(), set with
		set_item_checked()."""
		c = _Control(CT_LIST, caption)
		c.items = list(items) if items else []
		c.checklist = True
		if checked is True:
			c.selections = set(range(len(c.items)))
		elif checked:
			c.selections = {i for i in checked if 0 <= i < len(c.items)}
		return self._add(c)

	def create_tab_panel(self, caption, tabs=None):
		"""A row of tabs, switched with the left/right arrow keys. Read
		the active tab with get_tab_index() and show/hide the controls of
		each page with set_state()."""
		c = _Control(CT_TAB_PANEL, caption)
		c.items = list(tabs) if tabs else []
		return self._add(c)

	def create_progress_bar(self, caption, speak_interval=5, speak_global=True):
		"""A progress bar fed via set_progress(id, percent). Progress is
		announced every speak_interval percent; with speak_global even
		while another control has focus."""
		c = _Control(CT_PROGRESS, caption)
		c.value = 0
		c.speak_interval = max(1, int(speak_interval))
		c.speak_global = speak_global
		return self._add(c)

	def create_status_bar(self, caption, text=""):
		"""A read-only text line (game state, hints); update it with
		set_status(id, text)."""
		c = _Control(CT_STATUS, caption)
		c.text = str(text)
		return self._add(c)

	def create_link(self, caption, url):
		"""A link; enter opens the URL in the default browser (and sets
		is_pressed, so the game can react too)."""
		c = _Control(CT_LINK, caption)
		c.url = str(url)
		return self._add(c)

	def create_keyboard_area(self, caption):
		"""An area that hands the keyboard to the game: while it has
		focus the form ignores enter, space and escape, so the game can
		read lucia.key_* itself (check form.focused == id). Tab still
		navigates away."""
		return self._add(_Control(CT_KEYBOARD_AREA, caption))

	def create_subform(self, caption, form):
		"""A nested AudioForm as a control. Enter descends into it;
		escape (when the subform has no cancel button) comes back out."""
		c = _Control(CT_FORM, caption)
		c.subform = form
		return self._add(c)

	def create_checkbox(self, caption, checked=False):
		"""A checkbox, toggled with space."""
		c = _Control(CT_CHECKBOX, caption)
		c.checked = bool(checked)
		return self._add(c)

	def create_slider(self, caption, default=50, minimum=0, maximum=100, step=1, text=""):
		"""A slider, adjusted with the arrow keys. text: optional unit
		spoken after the value (e.g. "percent")."""
		c = _Control(CT_SLIDER, caption)
		c.value = default
		c.minimum = minimum
		c.maximum = maximum
		c.step = step
		c.value_text = text
		return self._add(c)

	# -- state access --------------------------------------------------------

	def _control(self, id):
		return self.controls[id]

	def get_text(self, id):
		return self._control(id).text

	def set_text(self, id, text):
		c = self._control(id)
		c.text = str(text)
		c.cursor = min(c.cursor, len(c.text))
		c.sel_anchor = None

	def set_caption(self, id, caption):
		"""Change a control's caption after creation."""
		self._control(id).caption = str(caption)

	def set_list_items(self, id, items, keep_index=True):
		"""Replace the items of a list or tab panel; by default the cursor
		stays on the same index (clamped)."""
		c = self._control(id)
		index = c.index if keep_index else 0
		c.items = list(items)
		c.index = max(0, min(index, len(c.items) - 1)) if c.items else 0
		c.selections.clear()

	def on_change(self, id, callback):
		"""Call callback(form, id) whenever the PLAYER changes this control:
		list/tab cursor moves, checkbox toggles, slider moves, text edits,
		multiselect toggles. Programmatic setters do not fire it. Use it to
		reshape the form live, e.g. show the buttons that match the
		selected list item via set_state(). Pass None to remove."""
		self._control(id).on_change = callback

	def _notify_change(self, c):
		if c.on_change is not None:
			c.on_change(self, self.controls.index(c))

	def is_checked(self, id):
		return self._control(id).checked

	def set_checked(self, id, checked):
		self._control(id).checked = bool(checked)

	def get_slider(self, id):
		return self._control(id).value

	def set_slider(self, id, value):
		c = self._control(id)
		c.value = max(c.minimum, min(c.maximum, value))

	def add_list_item(self, id, item):
		self._control(id).items.append(item)

	def delete_list_item(self, id, index):
		c = self._control(id)
		if 0 <= index < len(c.items):
			del c.items[index]
			c.index = min(c.index, max(0, len(c.items) - 1))
			c.selections.discard(index)

	def clear_list(self, id):
		c = self._control(id)
		c.items.clear()
		c.index = 0
		c.selections.clear()

	def get_list_index(self, id):
		return self._control(id).index

	def set_list_index(self, id, index):
		c = self._control(id)
		if 0 <= index < len(c.items):
			c.index = index

	def get_list_selections(self, id):
		return sorted(self._control(id).selections)

	def get_checked_items(self, id):
		"""The indices of all checked items of a checklist, sorted."""
		return sorted(self._control(id).selections)

	def is_item_checked(self, id, index):
		"""Whether a checklist item is checked."""
		return index in self._control(id).selections

	def set_item_checked(self, id, index, checked=True):
		"""Check or uncheck a checklist item programmatically (silent)."""
		c = self._control(id)
		if not 0 <= index < len(c.items):
			return
		if checked:
			c.selections.add(index)
		else:
			c.selections.discard(index)

	def get_current_list_item(self, id):
		c = self._control(id)
		if 0 <= c.index < len(c.items):
			return c.items[c.index]
		return None

	def get_tab_index(self, id):
		"""The active tab of a tab panel."""
		return self._control(id).index

	def set_tab_index(self, id, index):
		c = self._control(id)
		if 0 <= index < len(c.items):
			c.index = index

	def get_progress(self, id):
		return self._control(id).value

	def set_progress(self, id, percent):
		"""Update a progress bar (0..100). Crossing a speak_interval
		boundary announces the new percentage."""
		c = self._control(id)
		c.value = max(0, min(100, percent))
		bucket = int(c.value // c.speak_interval)
		if bucket != c._progress_bucket:
			c._progress_bucket = bucket
			if c.speak_global or self.focused == id:
				lucia.output.output("%d percent" % int(c.value), False)

	def get_status(self, id):
		return self._control(id).text

	def set_status(self, id, text):
		"""Update a status bar's text (spoken when the player focuses it)."""
		self._control(id).text = str(text)

	def get_link(self, id):
		return self._control(id).url

	def set_link(self, id, url):
		self._control(id).url = str(url)

	def get_subform(self, id):
		"""The nested AudioForm of a subform control."""
		return self._control(id).subform

	def exit_subform(self, speak=True):
		"""Leave the currently entered subform, returning focus to it as
		a control on this form."""
		if self._entered is None:
			return False
		id = self._entered
		self._entered = None
		if speak:
			self._speak_control(self.controls[id])
		return True

	def set_state(self, id, enabled=None, visible=None):
		c = self._control(id)
		if enabled is not None:
			c.enabled = enabled
		if visible is not None:
			c.visible = visible

	def is_pressed(self, id):
		"""True when the button was pressed this frame (cleared on read)."""
		c = self._control(id)
		if c.pressed:
			c.pressed = False
			return True
		return False

	# -- focus and speech ----------------------------------------------------

	def focus(self, id, speak=True):
		if 0 <= id < len(self.controls):
			self.focused = id
			if speak:
				self._speak_control(self.controls[id])

	def _speak_control(self, c, interrupt=True):
		parts = [c.caption]
		if c.type == CT_INPUT:
			shown = c.hidden_message if (c.password and c.text) else (c.text or "blank")
			parts.append(shown)
			kind = "multiline edit" if c.multiline else "edit"
			parts.append("read only " + kind if c.read_only else kind)
		elif c.type == CT_LIST:
			parts.append("check list" if c.checklist else "list")
			if c.items:
				item = str(c.items[c.index])
				if c.checklist:
					item += ", checked" if c.index in c.selections else ", not checked"
				elif c.multiselect and c.index in c.selections:
					item += ", selected"
				parts.append("%s, %d of %d" % (item, c.index + 1, len(c.items)))
			else:
				parts.append("empty")
		elif c.type == CT_TAB_PANEL:
			parts.append("tab panel")
			if c.items:
				parts.append(
					"%s tab, %d of %d" % (c.items[c.index], c.index + 1, len(c.items))
				)
		elif c.type == CT_CHECKBOX:
			parts.append("checkbox")
			parts.append("checked" if c.checked else "not checked")
		elif c.type == CT_SLIDER:
			parts.append("slider")
			parts.append(str(c.value) + (" " + c.value_text if c.value_text else ""))
		elif c.type == CT_PROGRESS:
			parts.append("progress bar")
			parts.append("%d percent" % int(c.value))
		elif c.type == CT_STATUS:
			parts.append(c.text)
			parts.append("status bar")
		elif c.type == CT_LINK:
			parts.append("link")
		elif c.type == CT_KEYBOARD_AREA:
			parts.append("keyboard area")
		elif c.type == CT_FORM:
			parts.append("subform, press enter to open")
		elif c.type == CT_BUTTON:
			parts.append("button")
		if not c.enabled:
			parts.append("unavailable")
		lucia.output.output(", ".join(str(p) for p in parts), interrupt)

	def _focusable(self):
		return [
			i for i, c in enumerate(self.controls) if c.visible and c.enabled
		]

	# -- the frame monitor ---------------------------------------------------

	def _has_cancel(self):
		return any(c.cancel and c.enabled and c.visible for c in self.controls)

	# -- menu bar state machine ----------------------------------------------

	def _monitor_menus(self):
		"""One frame of menu-bar handling. Returns True when the menu
		system consumed the frame (it is open, or alt just toggled it)."""
		alt = lucia.key_pressed(lucia.K_LALT) or lucia.key_pressed(lucia.K_RALT)
		if self._menu_mode is None:
			if alt:
				self._menu_mode = "bar"
				self._menu_bar_index = 0
				self._menu_path = []
				_say(
					"menu bar, %s submenu" % self._menus[0].caption
				)
				return True
			return False
		if alt:  # alt closes everything, back to normal navigation
			self._leave_menus()
			return True
		if self._menu_mode == "bar":
			self._monitor_menu_bar()
		else:
			self._monitor_menu()
		return True

	def _leave_menus(self):
		self.close_menus()
		if 0 <= self.focused < len(self.controls):
			self._speak_control(self.controls[self.focused])

	def _menu_access_letter(self):
		typed = lucia.get_characters()
		return typed[0].lower() if typed else None

	def _monitor_menu_bar(self):
		if lucia.key_pressed(lucia.K_LEFT):
			self._menu_bar_index = (self._menu_bar_index - 1) % len(self._menus)
			self._speak_bar_menu()
			return
		if lucia.key_pressed(lucia.K_RIGHT):
			self._menu_bar_index = (self._menu_bar_index + 1) % len(self._menus)
			self._speak_bar_menu()
			return
		if lucia.key_pressed(lucia.K_DOWN) or lucia.key_pressed(lucia.K_RETURN):
			self._open_bar_menu(self._menu_bar_index)
			return
		if lucia.key_pressed(lucia.K_ESCAPE):
			self._leave_menus()
			return
		letter = self._menu_access_letter()
		if letter:
			for index, menu in enumerate(self._menus):
				if menu.letter == letter:
					self._open_bar_menu(index)
					return

	def _speak_bar_menu(self):
		_say("%s submenu" % self._menus[self._menu_bar_index].caption)

	def _open_bar_menu(self, index, announce_menu=False):
		menu = self._menus[index]
		if not menu.children:
			_say("%s submenu, empty" % menu.caption)
			return
		self._menu_bar_index = index
		self._menu_mode = "menu"
		self._menu_path = [[menu, 0]]
		prefix = "%s submenu, " % menu.caption if announce_menu else ""
		self._speak_menu_item(prefix)

	def _speak_menu_item(self, prefix=""):
		menu, index = self._menu_path[-1]
		entry = menu.children[index]
		text = entry.caption + (" submenu" if entry.is_submenu else "")
		_say(prefix + text)

	def _enter_submenu(self, entry):
		if not entry.children:
			_say("%s submenu, empty" % entry.caption)
			return
		self._menu_path.append([entry, 0])
		self._speak_menu_item()

	def _activate_menu_item(self, entry):
		self._menu_activated = entry.id
		self.close_menus()
		if entry.callback is not None:
			entry.callback(self, entry.id)

	def _monitor_menu(self):
		frame = self._menu_path[-1]
		menu = frame[0]
		entry = menu.children[frame[1]]
		if lucia.key_pressed(lucia.K_UP):
			frame[1] = (frame[1] - 1) % len(menu.children)
			self._speak_menu_item()
			return
		if lucia.key_pressed(lucia.K_DOWN):
			frame[1] = (frame[1] + 1) % len(menu.children)
			self._speak_menu_item()
			return
		if lucia.key_pressed(lucia.K_HOME):
			frame[1] = 0
			self._speak_menu_item()
			return
		if lucia.key_pressed(lucia.K_END):
			frame[1] = len(menu.children) - 1
			self._speak_menu_item()
			return
		if lucia.key_pressed(lucia.K_RETURN):
			if entry.is_submenu:
				self._enter_submenu(entry)
			else:
				self._activate_menu_item(entry)
			return
		if lucia.key_pressed(lucia.K_RIGHT):
			if entry.is_submenu:
				self._enter_submenu(entry)
			else:
				# Windows: right on a plain item jumps to the next menu
				self._open_bar_menu(
					(self._menu_bar_index + 1) % len(self._menus), announce_menu=True
				)
			return
		if lucia.key_pressed(lucia.K_LEFT):
			if len(self._menu_path) > 1:
				self._menu_path.pop()  # leave the submenu
				self._speak_menu_item()
			else:
				self._open_bar_menu(
					(self._menu_bar_index - 1) % len(self._menus), announce_menu=True
				)
			return
		if lucia.key_pressed(lucia.K_ESCAPE):
			# one level at a time: submenu -> parent menu -> menu bar
			if len(self._menu_path) > 1:
				self._menu_path.pop()
				self._speak_menu_item()
			else:
				self._menu_mode = "bar"
				self._menu_path = []
				self._speak_bar_menu()
			return
		letter = self._menu_access_letter()
		if letter:
			for index, candidate in enumerate(menu.children):
				if candidate.letter == letter:
					frame[1] = index
					if candidate.is_submenu:
						self._enter_submenu(candidate)
					else:
						self._activate_menu_item(candidate)
					return

	def monitor(self):
		"""Process one frame of form interaction. Call every loop iteration,
		after lucia.process_events(). Returns the id of a button activated
		this frame, or -1.

		While the player is inside a subform, monitoring is delegated to
		it and this returns -1 — poll the subform's own is_pressed()."""
		if not self._announced:
			self._announced = True
			if self.title and self._say_title:
				lucia.output.output(self.title)
			if 0 <= self.focused < len(self.controls):
				self._speak_control(self.controls[self.focused], interrupt=False)
		# the menu bar (alt) takes the keyboard before everything else
		if self._menus and self._entered is None and self._monitor_menus():
			return -1
		# inside a subform: the child owns the keyboard until escape
		if self._entered is not None:
			entered = self.controls[self._entered]
			child = entered.subform
			if child is None or not entered.visible or not entered.enabled:
				self._entered = None
			else:
				child.monitor()
				if (
					lucia.key_pressed(lucia.K_ESCAPE)
					and child._entered is None
					and not child._has_cancel()
				):
					self.exit_subform()
				return -1
		focusable = self._focusable()
		if not focusable:
			return -1
		if self.focused not in focusable:
			self.focused = focusable[0]
		c = self.controls[self.focused]
		result = -1
		# global navigation
		if lucia.key_pressed(lucia.K_TAB):
			shift = lucia.key_down(lucia.K_LSHIFT) or lucia.key_down(lucia.K_RSHIFT)
			pos = focusable.index(self.focused)
			pos = (pos - 1) % len(focusable) if shift else (pos + 1) % len(focusable)
			self.focus(focusable[pos])
			return -1
		# global enter/escape for primary/cancel buttons; a keyboard area
		# releases both keys (and space) to the game while it has focus
		if lucia.key_pressed(lucia.K_RETURN) and c.type != CT_KEYBOARD_AREA:
			if c.type == CT_INPUT and c.multiline and c.multiline_enter and not c.read_only:
				self._delete_selection(c)
				if not c.max_length or len(c.text) < c.max_length:
					c.text = c.text[: c.cursor] + "\n" + c.text[c.cursor:]
					c.cursor += 1
					_say("new line")
					self._notify_change(c)
				return -1
			if c.type == CT_LINK:
				c.pressed = True
				if c.url:
					try:
						webbrowser.open(c.url)
					except Exception:
						pass
				return self.focused
			if c.type == CT_FORM:
				if c.subform is not None and c.subform.controls:
					self._entered = self.focused
					c.subform._announced = False
				return -1
			if c.type == CT_BUTTON:
				c.pressed = True
				return self.focused
			for i, ctrl in enumerate(self.controls):
				if ctrl.primary and ctrl.enabled and ctrl.visible:
					ctrl.pressed = True
					return i
			return -1
		if lucia.key_pressed(lucia.K_ESCAPE) and c.type != CT_KEYBOARD_AREA:
			for i, ctrl in enumerate(self.controls):
				if ctrl.cancel and ctrl.enabled and ctrl.visible:
					ctrl.pressed = True
					return i
			return -1
		# space activates buttons and links, exactly like enter
		if lucia.key_pressed(lucia.K_SPACE) and c.type in (CT_BUTTON, CT_LINK):
			c.pressed = True
			if c.type == CT_LINK and c.url:
				try:
					webbrowser.open(c.url)
				except Exception:
					pass
			return self.focused
		# control-specific handling
		if c.type == CT_INPUT:
			self._monitor_input(c)
		elif c.type == CT_LIST:
			self._monitor_list(c)
		elif c.type == CT_TAB_PANEL:
			self._monitor_tab_panel(c)
		elif c.type == CT_CHECKBOX:
			if lucia.key_pressed(lucia.K_SPACE):
				c.checked = not c.checked
				_say("checked" if c.checked else "not checked")
				self._notify_change(c)
		elif c.type == CT_SLIDER:
			self._monitor_slider(c)
		return result

	def _speak_char(self, c, char):
		if c.password:
			_say("star")
		elif char == " ":
			_say("space")
		else:
			_say(char)

	def _line_bounds(self, c):
		"""(start, end) offsets of the line the cursor is on."""
		start = c.text.rfind("\n", 0, c.cursor) + 1
		end = c.text.find("\n", c.cursor)
		if end == -1:
			end = len(c.text)
		return start, end

	def _speak_line(self, c):
		start, end = self._line_bounds(c)
		line = c.text[start:end]
		if c.password and line:
			line = c.hidden_message
		_say(line or "blank")

	# -- text selection helpers ----------------------------------------------

	def _selection(self, c):
		"""(start, end) of the active selection, or None."""
		if c.sel_anchor is None or c.sel_anchor == c.cursor:
			return None
		return min(c.sel_anchor, c.cursor), max(c.sel_anchor, c.cursor)

	def _selected_text(self, c):
		sel = self._selection(c)
		return c.text[sel[0]:sel[1]] if sel else ""

	def _delete_selection(self, c):
		"""Remove the selected text (if any). Returns True when something was removed."""
		sel = self._selection(c)
		if sel is None:
			return False
		c.text = c.text[: sel[0]] + c.text[sel[1]:]
		c.cursor = sel[0]
		c.sel_anchor = None
		return True

	def _move_cursor(self, c, new_pos, shift):
		"""Move the cursor; with shift, grow/shrink the selection and speak
		the characters that were (un)selected."""
		new_pos = max(0, min(len(c.text), new_pos))
		if not shift:
			c.sel_anchor = None
			c.cursor = new_pos
			return
		if c.sel_anchor is None:
			c.sel_anchor = c.cursor
		old_size = abs(c.cursor - c.sel_anchor)
		crossed = c.text[min(c.cursor, new_pos):max(c.cursor, new_pos)]
		c.cursor = new_pos
		new_size = abs(c.cursor - c.sel_anchor)
		if crossed:
			spoken = c.hidden_message if c.password else crossed
			_say(
				"%s %s" % (spoken, "selected" if new_size > old_size else "unselected")
			)
		if c.sel_anchor == c.cursor:
			c.sel_anchor = None

	@staticmethod
	def _word_left(text, pos):
		while pos > 0 and text[pos - 1].isspace():
			pos -= 1
		while pos > 0 and not text[pos - 1].isspace():
			pos -= 1
		return pos

	@staticmethod
	def _word_right(text, pos):
		while pos < len(text) and not text[pos].isspace():
			pos += 1
		while pos < len(text) and text[pos].isspace():
			pos += 1
		return pos

	# -- clipboard on inputs (NVGT model: ctrl+A/C/X/V) ----------------------

	def _copy(self, c):
		if c.password:
			return
		text = self._selected_text(c) or c.text
		if not text:
			_say("nothing to copy")
		elif lucia.clipboard_set_text(text):
			_say("copied")

	def _cut(self, c):
		if c.password or c.read_only:
			return
		if self._selection(c) is None:
			_say("no selection")
			return
		if lucia.clipboard_set_text(self._selected_text(c)):
			self._delete_selection(c)
			_say("cut")
			self._notify_change(c)

	def _paste(self, c):
		if c.read_only:
			return
		text = lucia.clipboard_get_text()
		if not text:
			_say("clipboard is empty")
			return
		if c.multiline:
			text = text.replace("\r\n", "\n").replace("\r", "\n")
		else:
			text = text.replace("\r", " ").replace("\n", " ")
		text = "".join(
			ch for ch in text if (ch == "\n" and c.multiline) or ch in c.whitelist
		)
		self._delete_selection(c)
		if c.max_length:
			room = max(0, c.max_length - len(c.text))
			text = text[:room]
		if not text:
			_say("nothing to paste")
			return
		c.text = c.text[: c.cursor] + text + c.text[c.cursor:]
		c.cursor += len(text)
		if c.password:
			_say("pasted")
		elif len(text) <= 40:
			_say("pasted %s" % text)
		else:
			_say("pasted %d characters" % len(text))
		self._notify_change(c)

	def _monitor_input_ctrl(self, c, shift):
		"""Ctrl combos on an input box. Returns True when a key was handled."""
		if lucia.key_pressed(lucia.K_a) and not shift:
			if c.text:
				c.sel_anchor = 0
				c.cursor = len(c.text)
				_say("selected all")
			return True
		if lucia.key_pressed(lucia.K_c):
			self._copy(c)
			return True
		if lucia.key_pressed(lucia.K_x):
			self._cut(c)
			return True
		if lucia.key_pressed(lucia.K_v):
			self._paste(c)
			return True
		if lucia.key_pressed(lucia.K_LEFT):
			self._move_cursor(c, self._word_left(c.text, c.cursor), shift)
			if not shift:
				self._speak_word_at(c)
			return True
		if lucia.key_pressed(lucia.K_RIGHT):
			self._move_cursor(c, self._word_right(c.text, c.cursor), shift)
			if not shift:
				self._speak_word_at(c)
			return True
		return False

	def _speak_word_at(self, c):
		if c.password:
			_say(c.hidden_message)
			return
		end = c.cursor
		while end < len(c.text) and not c.text[end].isspace():
			end += 1
		_say(c.text[c.cursor:end] or "blank")

	def _monitor_input(self, c):
		shift = lucia.key_down(lucia.K_LSHIFT) or lucia.key_down(lucia.K_RSHIFT)
		ctrl = lucia.key_down(lucia.K_LCTRL) or lucia.key_down(lucia.K_RCTRL)
		if ctrl and self._monitor_input_ctrl(c, shift):
			return
		if c.multiline and lucia.key_pressed(lucia.K_UP):
			start, _ = self._line_bounds(c)
			target = c.cursor
			if start > 0:
				column = c.cursor - start
				previous_start = c.text.rfind("\n", 0, start - 1) + 1
				target = min(previous_start + column, start - 1)
			self._move_cursor(c, target, shift)
			if not shift:
				self._speak_line(c)
			return
		if c.multiline and lucia.key_pressed(lucia.K_DOWN):
			start, end = self._line_bounds(c)
			target = c.cursor
			if end < len(c.text):
				column = c.cursor - start
				next_end = c.text.find("\n", end + 1)
				if next_end == -1:
					next_end = len(c.text)
				target = min(end + 1 + column, next_end)
			self._move_cursor(c, target, shift)
			if not shift:
				self._speak_line(c)
			return
		if lucia.key_pressed(lucia.K_LEFT):
			self._move_cursor(c, c.cursor - 1, shift)
			if not shift and c.cursor < len(c.text):
				self._speak_char(c, c.text[c.cursor])
			return
		if lucia.key_pressed(lucia.K_RIGHT):
			self._move_cursor(c, c.cursor + 1, shift)
			if not shift:
				if c.cursor < len(c.text):
					self._speak_char(c, c.text[c.cursor])
				else:
					_say("blank")
			return
		if lucia.key_pressed(lucia.K_HOME):
			# multiline: start of the current line; single line: start of text
			self._move_cursor(c, self._line_bounds(c)[0] if c.multiline else 0, shift)
			if not shift and c.cursor < len(c.text):
				self._speak_char(c, c.text[c.cursor])
			return
		if lucia.key_pressed(lucia.K_END):
			self._move_cursor(
				c, self._line_bounds(c)[1] if c.multiline else len(c.text), shift
			)
			if not shift:
				_say("blank")
			return
		if lucia.key_pressed(lucia.K_BACKSPACE):
			if c.read_only:
				return
			if self._delete_selection(c):
				_say("selection deleted")
				self._notify_change(c)
				return
			if ctrl:
				start = self._word_left(c.text, c.cursor)
				if start < c.cursor:
					removed = c.text[start:c.cursor]
					c.text = c.text[:start] + c.text[c.cursor:]
					c.cursor = start
					_say(c.hidden_message if c.password else removed)
					self._notify_change(c)
				return
			if c.cursor == 0:
				return
			removed = c.text[c.cursor - 1]
			c.text = c.text[: c.cursor - 1] + c.text[c.cursor:]
			c.cursor -= 1
			self._speak_char(c, removed)
			self._notify_change(c)
			return
		if lucia.key_pressed(lucia.K_DELETE):
			if c.read_only:
				return
			if self._delete_selection(c):
				_say("selection deleted")
				self._notify_change(c)
				return
			if c.cursor >= len(c.text):
				return
			removed = c.text[c.cursor]
			c.text = c.text[: c.cursor] + c.text[c.cursor + 1:]
			self._speak_char(c, removed)
			self._notify_change(c)
			return
		if c.read_only:
			return
		# typed characters from this frame (SDL text input: IMEs and dead
		# keys come out right; falls back to KEYDOWN unicodes headless)
		typed = False
		for ch in lucia.get_characters():
			if not ch or ch in ("\r", "\n", "\t", "\x1b", "\x08"):
				continue
			if ch not in c.whitelist:
				continue
			self._delete_selection(c)         # typing replaces the selection
			if c.max_length and len(c.text) >= c.max_length:
				continue
			c.text = c.text[: c.cursor] + ch + c.text[c.cursor:]
			c.cursor += 1
			self._speak_char(c, ch)
			typed = True
		if typed:
			self._notify_change(c)

	def _monitor_list(self, c):
		if not c.items:
			return
		if lucia.key_pressed(lucia.K_UP) or lucia.key_pressed(lucia.K_LEFT):
			if c.index > 0:
				c.index -= 1
				self._notify_change(c)
			self._speak_list_item(c)
			return
		if lucia.key_pressed(lucia.K_DOWN) or lucia.key_pressed(lucia.K_RIGHT):
			if c.index < len(c.items) - 1:
				c.index += 1
				self._notify_change(c)
			self._speak_list_item(c)
			return
		if lucia.key_pressed(lucia.K_HOME):
			if c.index != 0:
				c.index = 0
				self._notify_change(c)
			self._speak_list_item(c)
			return
		if lucia.key_pressed(lucia.K_END):
			if c.index != len(c.items) - 1:
				c.index = len(c.items) - 1
				self._notify_change(c)
			self._speak_list_item(c)
			return
		if (c.multiselect or c.checklist) and lucia.key_pressed(lucia.K_SPACE):
			if c.index in c.selections:
				c.selections.discard(c.index)
				_say("unchecked" if c.checklist else "not selected")
			else:
				c.selections.add(c.index)
				_say("checked" if c.checklist else "selected")
			self._notify_change(c)
			return
		# first-letter navigation
		for event in _frame_keydown_events():
			ch = getattr(event, "unicode", "").lower()
			if len(ch) == 1 and ch in string.printable and ch.strip():
				n = len(c.items)
				for offset in range(1, n + 1):
					i = (c.index + offset) % n
					if str(c.items[i]).lower().startswith(ch):
						if c.index != i:
							c.index = i
							self._notify_change(c)
						self._speak_list_item(c)
						return

	def _monitor_tab_panel(self, c):
		if not c.items:
			return
		previous = c.index
		if lucia.key_pressed(lucia.K_LEFT):
			if c.index > 0:
				c.index -= 1
		elif lucia.key_pressed(lucia.K_RIGHT):
			if c.index < len(c.items) - 1:
				c.index += 1
		elif lucia.key_pressed(lucia.K_HOME):
			c.index = 0
		elif lucia.key_pressed(lucia.K_END):
			c.index = len(c.items) - 1
		else:
			return
		if c.index != previous:
			self._notify_change(c)
		self._speak_tab(c)

	def _speak_tab(self, c):
		_say(
			"%s tab, %d of %d" % (c.items[c.index], c.index + 1, len(c.items))
		)

	def _speak_list_item(self, c):
		item = str(c.items[c.index])
		if c.checklist:
			item += ", checked" if c.index in c.selections else ", not checked"
		elif c.multiselect and c.index in c.selections:
			item += ", selected"
		_say("%s, %d of %d" % (item, c.index + 1, len(c.items)))

	def _monitor_slider(self, c):
		changed = False
		if lucia.key_pressed(lucia.K_LEFT) or lucia.key_pressed(lucia.K_DOWN):
			if c.value > c.minimum:
				c.value = max(c.minimum, c.value - c.step)
				changed = True
		elif lucia.key_pressed(lucia.K_RIGHT) or lucia.key_pressed(lucia.K_UP):
			if c.value < c.maximum:
				c.value = min(c.maximum, c.value + c.step)
				changed = True
		elif lucia.key_pressed(lucia.K_HOME):
			c.value = c.minimum
			changed = True
		elif lucia.key_pressed(lucia.K_END):
			c.value = c.maximum
			changed = True
		if changed:
			_say(
				str(c.value) + (" " + c.value_text if c.value_text else "")
			)
			self._notify_change(c)


def _frame_keydown_events():
	"""The KEYDOWN events of the current frame.

	lucia.process_events() stores its event list; we re-read it via pygame's
	event queue mirror kept on the lucia module. Falls back to empty."""
	events = getattr(lucia, "_last_events", None)
	if events is None:
		return []
	import pygame

	return [e for e in events if e.type == pygame.KEYDOWN]
