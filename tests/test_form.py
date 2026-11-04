"""Tests for the AudioForm controls, NVGT-parity set included (tab
panel, multiline input, progress/status bars, links, keyboard areas,
subforms). Keys are simulated by setting lucia's per-frame key globals;
speech is captured by replacing lucia.output."""

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import lucia
from lucia.ui import form as form_module
from lucia.ui.form import AudioForm


class Speech:
	def __init__(self):
		self.spoken = []
		self.interrupts = []  # the interrupt flag of every call, in order

	def output(self, text, interrupt=False):
		self.spoken.append(str(text))
		self.interrupts.append(interrupt)

	def last(self):
		return self.spoken[-1] if self.spoken else ""


class Held(dict):
	def __missing__(self, key):
		return False

	def __len__(self):
		return 512


@pytest.fixture
def speech(monkeypatch):
	s = Speech()
	monkeypatch.setattr(lucia, "output", s)
	return s


@pytest.fixture
def frame(monkeypatch):
	"""frame(form, key=..., shift=..., chars=...) simulates one monitor frame."""

	def run(form, key=None, shift=False, ctrl=False, chars=""):
		monkeypatch.setattr(lucia, "current_key_pressed", key if key is not None else -1)
		monkeypatch.setattr(
			lucia, "keys_held", Held({lucia.K_LSHIFT: shift, lucia.K_LCTRL: ctrl})
		)
		monkeypatch.setattr(lucia, "get_characters", lambda: chars)
		result = form.monitor()
		monkeypatch.setattr(lucia, "current_key_pressed", -1)
		return result

	return run


def make_form(**kwargs):
	f = AudioForm("Test form", **kwargs)
	f._announced = True  # skip the one-shot announcement in most tests
	return f


# -- tab panel ----------------------------------------------------------------

def test_tab_panel_left_right(speech, frame):
	f = make_form()
	tabs = f.create_tab_panel("Pages", ["General", "Audio", "Controls"])
	assert f.get_tab_index(tabs) == 0
	frame(f, key=lucia.K_RIGHT)
	assert f.get_tab_index(tabs) == 1
	assert speech.last() == "Audio tab, 2 of 3"
	frame(f, key=lucia.K_RIGHT)
	frame(f, key=lucia.K_RIGHT)  # clamps at the last tab
	assert f.get_tab_index(tabs) == 2
	frame(f, key=lucia.K_LEFT)
	assert f.get_tab_index(tabs) == 1
	frame(f, key=lucia.K_HOME)
	assert f.get_tab_index(tabs) == 0
	frame(f, key=lucia.K_END)
	assert f.get_tab_index(tabs) == 2
	f.set_tab_index(tabs, 1)
	assert f.get_tab_index(tabs) == 1
	f.set_tab_index(tabs, 99)  # ignored
	assert f.get_tab_index(tabs) == 1


def test_tab_panel_focus_speech(speech, frame):
	f = make_form()
	f.create_tab_panel("Pages", ["General", "Audio"])
	f.focus(0)
	assert "tab panel" in speech.last()
	assert "General tab, 1 of 2" in speech.last()


# -- multiline input ----------------------------------------------------------

def test_multiline_enter_inserts_newline(speech, frame):
	f = make_form()
	box = f.create_input_box("Notes", "ab", multiline=True)
	f.create_button("OK", primary=True)
	assert frame(f, key=lucia.K_RETURN) == -1  # newline, not the button
	assert f.get_text(box) == "ab\n"
	assert speech.last() == "new line"
	frame(f, chars="c")
	assert f.get_text(box) == "ab\nc"


def test_multiline_enter_false_presses_primary(speech, frame):
	f = make_form()
	f.create_input_box("Notes", multiline=True, multiline_enter=False)
	ok = f.create_button("OK", primary=True)
	assert frame(f, key=lucia.K_RETURN) == ok
	assert f.is_pressed(ok)


def test_multiline_line_navigation(speech, frame):
	f = make_form()
	box = f.create_input_box("Notes", "first\nsecond\nlast", multiline=True)
	c = f.controls[box]
	c.cursor = 2  # inside "first"
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "second"
	start, end = f._line_bounds(c)
	assert c.text[start:end] == "second"
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "last"
	frame(f, key=lucia.K_DOWN)  # clamps at the last line
	assert speech.last() == "last"
	frame(f, key=lucia.K_UP)
	assert speech.last() == "second"
	frame(f, key=lucia.K_END)
	assert c.cursor == c.text.find("\n", c.text.find("\n") + 1)
	frame(f, key=lucia.K_HOME)
	assert c.text[c.cursor - 1] == "\n"


def test_single_line_home_end_unchanged(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "hello")
	c = f.controls[box]
	frame(f, key=lucia.K_HOME)
	assert c.cursor == 0
	frame(f, key=lucia.K_END)
	assert c.cursor == 5


def test_multiline_respects_max_length(speech, frame):
	f = make_form()
	box = f.create_input_box("Notes", "abc", multiline=True, max_length=3)
	frame(f, key=lucia.K_RETURN)
	assert f.get_text(box) == "abc"  # full: no newline inserted


# -- progress bar -------------------------------------------------------------

def test_progress_announces_at_intervals(speech):
	f = make_form()
	bar = f.create_progress_bar("Loading", speak_interval=10)
	f.set_progress(bar, 3)
	assert speech.spoken == []  # still in the first bucket
	f.set_progress(bar, 10)
	assert speech.last() == "10 percent"
	f.set_progress(bar, 14)
	assert len(speech.spoken) == 1  # same bucket, quiet
	f.set_progress(bar, 47)
	assert speech.last() == "47 percent"
	f.set_progress(bar, 150)
	assert f.get_progress(bar) == 100


def test_progress_speak_global_false(speech):
	f = make_form()
	bar = f.create_progress_bar("Loading", speak_interval=10, speak_global=False)
	f.create_button("OK")
	f.focused = 1  # focus elsewhere
	f.set_progress(bar, 50)
	assert speech.spoken == []
	f.focused = bar
	f.set_progress(bar, 70)
	assert speech.last() == "70 percent"


def test_progress_focus_speech(speech):
	f = make_form()
	bar = f.create_progress_bar("Loading")
	f.controls[bar].value = 40
	f.focus(bar)
	assert "progress bar" in speech.last()
	assert "40 percent" in speech.last()


# -- status bar ---------------------------------------------------------------

def test_status_bar(speech, frame):
	f = make_form()
	status = f.create_status_bar("Score", "1200 points")
	assert f.get_status(status) == "1200 points"
	f.set_status(status, "1300 points")
	assert speech.spoken == []  # updates are silent
	f.focus(status)
	assert "1300 points" in speech.last()
	assert "status bar" in speech.last()


# -- link ---------------------------------------------------------------------

def test_link_opens_url(speech, frame, monkeypatch):
	opened = []
	monkeypatch.setattr(form_module.webbrowser, "open", lambda url: opened.append(url))
	f = make_form()
	link = f.create_link("Project homepage", "https://example.com")
	assert frame(f, key=lucia.K_RETURN) == link
	assert opened == ["https://example.com"]
	assert f.is_pressed(link)
	assert f.get_link(link) == "https://example.com"
	f.set_link(link, "https://other.example")
	assert f.get_link(link) == "https://other.example"


def test_link_focus_speech(speech):
	f = make_form()
	f.create_link("Homepage", "https://example.com")
	f.focus(0)
	assert "link" in speech.last()


# -- keyboard area ------------------------------------------------------------

def test_keyboard_area_releases_enter_and_escape(speech, frame):
	f = make_form()
	area = f.create_keyboard_area("Game area")
	ok = f.create_button("OK", primary=True)
	cancel = f.create_button("Back", cancel=True)
	f.focused = area
	assert frame(f, key=lucia.K_RETURN) == -1
	assert not f.is_pressed(ok)
	assert frame(f, key=lucia.K_ESCAPE) == -1
	assert not f.is_pressed(cancel)
	# tab still navigates away, and then enter works again
	frame(f, key=lucia.K_TAB)
	assert f.focused == ok
	assert frame(f, key=lucia.K_RETURN) == ok


# -- subform ------------------------------------------------------------------

def make_child(with_cancel=False):
	child = AudioForm("Child", say_title=False)
	child.create_checkbox("Music", True)
	if with_cancel:
		child.create_button("Back", cancel=True)
	child._announced = True
	return child


def test_subform_enter_and_escape(speech, frame):
	f = make_form()
	child = make_child()
	sub = f.create_subform("Advanced", child)
	assert f.get_subform(sub) is child
	assert frame(f, key=lucia.K_RETURN) == -1
	assert f._entered == sub
	# keys now go to the child: space toggles its checkbox
	frame(f, key=lucia.K_SPACE)
	assert child.is_checked(0) is False
	# escape (child has no cancel button) leaves the subform
	frame(f, key=lucia.K_ESCAPE)
	assert f._entered is None
	assert "subform" in speech.last()


def test_subform_child_cancel_button_wins(speech, frame):
	f = make_form()
	child = make_child(with_cancel=True)
	f.create_subform("Advanced", child)
	frame(f, key=lucia.K_RETURN)
	frame(f, key=lucia.K_ESCAPE)
	assert f._entered is not None, "the child's cancel button handles escape"
	assert child.is_pressed(1)
	f.exit_subform(speak=False)
	assert f._entered is None


def test_empty_subform_is_not_entered(speech, frame):
	f = make_form()
	f.create_subform("Empty", AudioForm("Empty child"))
	assert frame(f, key=lucia.K_RETURN) == -1
	assert f._entered is None


def test_exit_subform_without_entry():
	f = make_form()
	assert f.exit_subform() is False


# -- regression: the classic controls still work ------------------------------

def test_classic_controls_still_work(speech, frame):
	f = make_form()
	name = f.create_input_box("Name")
	fx = f.create_checkbox("Effects")
	vol = f.create_slider("Volume", 50, 0, 100, step=10)
	ok = f.create_button("OK", primary=True)
	frame(f, chars="hi")
	assert f.get_text(name) == "hi"
	frame(f, key=lucia.K_TAB)
	assert f.focused == fx
	frame(f, key=lucia.K_SPACE)
	assert f.is_checked(fx)
	frame(f, key=lucia.K_TAB)
	frame(f, key=lucia.K_RIGHT)
	assert f.get_slider(vol) == 60
	assert frame(f, key=lucia.K_RETURN) == ok


# -- space on buttons and links ----------------------------------------------

def test_space_presses_buttons_and_links(speech, frame, monkeypatch):
	opened = []
	monkeypatch.setattr(form_module.webbrowser, "open", lambda url: opened.append(url))
	f = make_form()
	ok = f.create_button("OK")
	link = f.create_link("Site", "https://example.com")
	f.focused = ok
	assert frame(f, key=lucia.K_SPACE) == ok
	assert f.is_pressed(ok)
	f.focused = link
	assert frame(f, key=lucia.K_SPACE) == link
	assert opened == ["https://example.com"]


def test_space_still_types_in_inputs(speech, frame):
	f = make_form()
	box = f.create_input_box("Name")
	f.create_button("OK", primary=True)
	frame(f, chars=" ")
	assert f.get_text(box) == " "


# -- text selection ------------------------------------------------------------

def test_shift_arrows_select_and_speak(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "hello")
	c = f.controls[box]
	c.cursor = 0
	frame(f, key=lucia.K_RIGHT, shift=True)
	frame(f, key=lucia.K_RIGHT, shift=True)
	assert f._selected_text(c) == "he"
	assert "selected" in speech.last()
	frame(f, key=lucia.K_LEFT, shift=True)
	assert f._selected_text(c) == "h"
	assert "unselected" in speech.last()
	# plain arrow collapses the selection
	frame(f, key=lucia.K_RIGHT)
	assert f._selected_text(c) == ""


def test_shift_end_and_ctrl_a(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "hello world")
	c = f.controls[box]
	c.cursor = 6
	frame(f, key=lucia.K_END, shift=True)
	assert f._selected_text(c) == "world"
	frame(f, key=lucia.K_a, ctrl=True)
	assert f._selected_text(c) == "hello world"
	assert speech.last() == "selected all"


def test_typing_replaces_selection(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "abcdef")
	c = f.controls[box]
	c.sel_anchor = 1
	c.cursor = 5
	frame(f, chars="X")
	assert f.get_text(box) == "aXf"


def test_backspace_deletes_selection(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "abcdef")
	c = f.controls[box]
	c.sel_anchor = 0
	c.cursor = 3
	frame(f, key=lucia.K_BACKSPACE)
	assert f.get_text(box) == "def"
	assert speech.last() == "selection deleted"


def test_ctrl_backspace_deletes_word(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "hello world")
	c = f.controls[box]
	c.cursor = len(c.text)
	frame(f, key=lucia.K_BACKSPACE, ctrl=True)
	assert f.get_text(box) == "hello "


def test_ctrl_arrows_move_by_word(speech, frame):
	f = make_form()
	box = f.create_input_box("Name", "one two three")
	c = f.controls[box]
	c.cursor = len(c.text)
	frame(f, key=lucia.K_LEFT, ctrl=True)
	assert c.cursor == 8
	assert speech.last() == "three"
	frame(f, key=lucia.K_LEFT, ctrl=True)
	assert c.cursor == 4
	frame(f, key=lucia.K_RIGHT, ctrl=True)
	assert c.cursor == 8


# -- clipboard on inputs -------------------------------------------------------

@pytest.fixture
def fake_clipboard(monkeypatch):
	store = {"text": ""}
	monkeypatch.setattr(lucia, "clipboard_set_text", lambda t: store.update(text=t) or True)
	monkeypatch.setattr(lucia, "clipboard_get_text", lambda: store["text"])
	return store


def test_copy_cut_paste(speech, frame, fake_clipboard):
	f = make_form()
	box = f.create_input_box("Name", "hello world")
	c = f.controls[box]
	c.sel_anchor = 0
	c.cursor = 5
	frame(f, key=lucia.K_c, ctrl=True)
	assert fake_clipboard["text"] == "hello"
	assert speech.last() == "copied"
	frame(f, key=lucia.K_x, ctrl=True)
	assert f.get_text(box) == " world"
	assert speech.last() == "cut"
	c.cursor = len(c.text)
	c.sel_anchor = None
	frame(f, key=lucia.K_v, ctrl=True)
	assert f.get_text(box) == " worldhello"
	assert "pasted" in speech.last()


def test_copy_without_selection_copies_all(speech, frame, fake_clipboard):
	f = make_form()
	f.create_input_box("Name", "whole text")
	frame(f, key=lucia.K_c, ctrl=True)
	assert fake_clipboard["text"] == "whole text"


def test_paste_respects_whitelist_and_maxlength(speech, frame, fake_clipboard):
	from lucia.ui.virtualinput import WHITELIST_DIGITS

	f = make_form()
	box = f.create_input_box("Pin", whitelist=WHITELIST_DIGITS, max_length=4)
	fake_clipboard["text"] = "a1b2c3d4e5"
	frame(f, key=lucia.K_v, ctrl=True)
	assert f.get_text(box) == "1234"


def test_password_box_refuses_copy(speech, frame, fake_clipboard):
	f = make_form()
	box = f.create_input_box("Secret", "hunter2", password=True)
	c = f.controls[box]
	c.sel_anchor = 0
	c.cursor = 7
	frame(f, key=lucia.K_c, ctrl=True)
	assert fake_clipboard["text"] == ""


def test_paste_into_multiline_keeps_newlines(speech, frame, fake_clipboard):
	f = make_form()
	box = f.create_input_box("Notes", multiline=True)
	fake_clipboard["text"] = "line one\r\nline two"
	frame(f, key=lucia.K_v, ctrl=True)
	assert f.get_text(box) == "line one\nline two"


# -- live form updates ---------------------------------------------------------

def test_on_change_fires_on_user_interaction(speech, frame):
	f = make_form()
	lst = f.create_list("Saves", ["alpha", "beta"])
	box = f.create_input_box("Name")
	fx = f.create_checkbox("Effects")
	vol = f.create_slider("Volume", 50)
	events = []
	for cid in (lst, box, fx, vol):
		f.on_change(cid, lambda form, id: events.append(id))
	frame(f, key=lucia.K_DOWN)                 # list index 0 -> 1
	f.focused = box
	frame(f, chars="x")
	f.focused = fx
	frame(f, key=lucia.K_SPACE)
	f.focused = vol
	frame(f, key=lucia.K_RIGHT)
	assert events == [lst, box, fx, vol]
	# programmatic changes stay silent
	events.clear()
	f.set_text(box, "quiet")
	f.set_checked(fx, False)
	f.set_slider(vol, 10)
	f.set_list_index(lst, 0)
	assert events == []


def test_on_change_reshapes_form_live(speech, frame):
	f = make_form()
	lst = f.create_list("Mode", ["easy", "custom"])
	custom_btn = f.create_button("Configure custom rules")
	f.set_state(custom_btn, visible=False)

	def sync(form, id):
		form.set_state(custom_btn, visible=form.get_list_index(id) == 1)

	f.on_change(lst, sync)
	frame(f, key=lucia.K_DOWN)                 # select "custom"
	assert f.controls[custom_btn].visible is True
	frame(f, key=lucia.K_UP)
	assert f.controls[custom_btn].visible is False


def test_set_list_items_and_caption(speech):
	f = make_form()
	lst = f.create_list("Files", ["a", "b", "c"])
	f.set_list_index(lst, 2)
	f.set_list_items(lst, ["x", "y", "z", "w"])
	assert f.get_list_index(lst) == 2          # index kept
	f.set_list_items(lst, ["only"])
	assert f.get_list_index(lst) == 0          # clamped
	f.set_caption(lst, "Sounds")
	assert f.controls[lst].caption == "Sounds"


# -- menu first-item announcement ---------------------------------------------

def test_menu_announces_first_item(engine_speech):
	speech, fake = engine_speech
	menu = lucia.ui.Menu()
	menu.add_item_tts("New game", "new")
	menu.add_item_tts("Quit", "quit")
	menu.add_speech_method(fake)
	menu.set_callback(lambda m: setattr(m, "running", False))
	result = menu.run("Main menu")
	assert result == "new"
	assert any("Main menu, New game" in s for s in fake.spoken)


@pytest.fixture
def engine_speech(monkeypatch):
	lucia.initialize()
	lucia.show_window("menu test")
	fake = Speech()
	fake.speak = fake.output
	monkeypatch.setattr(lucia, "output", fake)
	yield fake, fake
	lucia.quit()


def test_umlauts_and_unicode_type_correctly(speech, frame):
	"""Reported bug: WHITELIST_ALL came from string.printable (ASCII), so
	typing ö, ä, ü, ß was silently ignored."""
	f = make_form()
	box = f.create_input_box("Name")
	frame(f, chars="öäüß")
	assert f.get_text(box) == "öäüß"
	frame(f, chars="日本")
	assert f.get_text(box) == "öäüß日本"


def test_umlauts_paste(speech, frame, fake_clipboard):
	f = make_form()
	box = f.create_input_box("Name")
	fake_clipboard["text"] = "Straße"
	frame(f, key=lucia.K_v, ctrl=True)
	assert f.get_text(box) == "Straße"


def test_whitelist_all_membership():
	from lucia.ui.virtualinput import WHITELIST_ALL, WHITELIST_DIGITS

	assert "ö" in WHITELIST_ALL
	assert "ß" in WHITELIST_ALL
	assert "a" in WHITELIST_ALL
	assert " " in WHITELIST_ALL
	assert "\n" not in WHITELIST_ALL
	assert "\x1b" not in WHITELIST_ALL
	assert "ö" not in WHITELIST_DIGITS  # restricted whitelists stay restrictive
	assert list(WHITELIST_ALL)  # iterating still works for legacy code


# -- menu bar (Windows model) --------------------------------------------------

def make_menu_form():
	f = make_form()
	box = f.create_input_box("Name")
	file_menu = f.add_menu("&File")
	open_id = f.add_menu_item(file_menu, "&Open")
	recent = f.add_submenu(file_menu, "&Recent files")
	recent_id = f.add_menu_item(recent, "game1.sav")
	edit_menu = f.add_menu("&Edit")
	copy_id = f.add_menu_item(edit_menu, "&Copy")
	return f, box, open_id, recent_id, copy_id


def test_alt_opens_menu_bar(speech, frame):
	f, box, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	assert speech.last() == "menu bar, File submenu"
	frame(f, key=lucia.K_RIGHT)
	assert speech.last() == "Edit submenu"
	frame(f, key=lucia.K_RIGHT)  # wraps
	assert speech.last() == "File submenu"
	frame(f, key=lucia.K_LEFT)
	assert speech.last() == "Edit submenu"


def test_down_enters_menu_and_arrows_navigate(speech, frame):
	f, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "Open"
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "Recent files submenu"
	frame(f, key=lucia.K_DOWN)  # wraps
	assert speech.last() == "Open"
	frame(f, key=lucia.K_UP)
	assert speech.last() == "Recent files submenu"


def test_submenu_enter_and_leave(speech, frame):
	f, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_DOWN)          # on "Recent files submenu"
	frame(f, key=lucia.K_RIGHT)         # enter it
	assert speech.last() == "game1.sav"
	frame(f, key=lucia.K_LEFT)          # leave it
	assert speech.last() == "Recent files submenu"


def test_right_on_plain_item_jumps_to_next_menu(speech, frame):
	f, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)          # File menu, on "Open"
	frame(f, key=lucia.K_RIGHT)
	assert speech.last() == "Edit submenu, Copy"
	frame(f, key=lucia.K_LEFT)          # back: previous menu
	assert speech.last() == "File submenu, Open"


def test_escape_steps_out_one_level_at_a_time(speech, frame):
	f, box, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_RETURN)        # into Recent files
	assert speech.last() == "game1.sav"
	frame(f, key=lucia.K_ESCAPE)        # submenu -> parent menu
	assert speech.last() == "Recent files submenu"
	frame(f, key=lucia.K_ESCAPE)        # menu -> bar
	assert speech.last() == "File submenu"
	frame(f, key=lucia.K_ESCAPE)        # bar -> normal navigation
	assert f._menu_mode is None
	# normal navigation works again: typing reaches the input box
	frame(f, chars="x")
	assert f.get_text(box) == "x"


def test_enter_activates_item_and_polls(speech, frame):
	f, box, open_id, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_RETURN)        # activate "Open"
	assert f.menu_activated() == open_id
	assert f.menu_activated() is None   # cleared on read
	assert f._menu_mode is None         # menus closed


def test_menu_item_callback(speech, frame):
	f = make_form()
	menu = f.add_menu("&File")
	fired = []
	item = f.add_menu_item(menu, "&Quit", callback=lambda form, id: fired.append(id))
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_RETURN)
	assert fired == [item]


def test_access_keys_alt_f_o(speech, frame):
	"""alt, then f for File, then o for Open — the Windows habit."""
	f, box, open_id, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, chars="f")
	assert speech.last() == "Open"      # File opened, first item spoken
	frame(f, chars="o")
	assert f.menu_activated() == open_id
	# access key into a submenu enters it
	frame(f, key=lucia.K_LALT)
	frame(f, chars="f")
	frame(f, chars="r")
	assert speech.last() == "game1.sav"


def test_alt_closes_and_restores_focus_speech(speech, frame):
	f, box, *_ = make_menu_form()
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_LALT)          # close from anywhere
	assert f._menu_mode is None
	assert "edit" in speech.last()      # the focused input box re-announced


def test_open_menus_swallow_normal_navigation(speech, frame):
	f, box, *_ = make_menu_form()
	ok = f.create_button("OK", primary=True)
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_TAB)           # tab must not move focus
	assert f.focused == box
	frame(f, chars="x")                 # letters are access keys, not text
	assert f.get_text(box) == ""
	frame(f, key=lucia.K_DOWN)
	frame(f, key=lucia.K_RETURN)        # activates "Open", closes menus
	assert not f.is_pressed(ok)         # never reached the primary button


def test_empty_menu_is_not_entered(speech, frame):
	f = make_form()
	f.create_button("OK")
	f.add_menu("&Empty")
	frame(f, key=lucia.K_LALT)
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "Empty submenu, empty"
	assert f._menu_mode == "bar"


# -- checklist -----------------------------------------------------------------

def test_checklist_toggle_and_query(speech, frame):
	f = make_form()
	cl = f.create_checklist("Mods", ["music", "hardcore", "hints"], checked=[0])
	assert f.get_checked_items(cl) == [0]
	assert f.is_item_checked(cl, 0) is True
	frame(f, key=lucia.K_SPACE)          # cursor on item 0: uncheck it
	assert speech.last() == "unchecked"
	assert f.get_checked_items(cl) == []
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "hardcore, not checked, 2 of 3"
	frame(f, key=lucia.K_SPACE)
	assert speech.last() == "checked"
	assert f.get_checked_items(cl) == [1]
	frame(f, key=lucia.K_UP)
	assert speech.last() == "music, not checked, 1 of 3"


def test_checklist_focus_speech(speech):
	f = make_form()
	f.create_checklist("Mods", ["music"], checked=[0])
	f.focus(0)
	assert "check list" in speech.last()
	assert "music, checked, 1 of 1" in speech.last()


def test_checklist_programmatic_set_is_silent(speech, frame):
	f = make_form()
	cl = f.create_checklist("Mods", ["a", "b"])
	events = []
	f.on_change(cl, lambda form, id: events.append(id))
	f.set_item_checked(cl, 1)
	assert f.is_item_checked(cl, 1)
	assert speech.spoken == []
	assert events == []                  # programmatic: no on_change
	f.set_item_checked(cl, 1, checked=False)
	assert f.get_checked_items(cl) == []
	f.set_item_checked(cl, 99)           # out of range: ignored
	assert f.get_checked_items(cl) == []
	frame(f, key=lucia.K_SPACE)          # player toggle DOES notify
	assert events == [cl]


def test_checklist_does_not_break_normal_lists(speech, frame):
	f = make_form()
	lst = f.create_list("Files", ["a", "b"], multiselect=True)
	frame(f, key=lucia.K_SPACE)
	assert speech.last() == "selected"   # multiselect wording unchanged
	frame(f, key=lucia.K_DOWN)
	assert speech.last() == "b, 2 of 2"  # no checked/not checked suffix
	plain = f.create_list("Plain", ["x"])
	f.focused = plain
	frame(f, key=lucia.K_SPACE)          # space on a plain list: nothing
	assert f.get_list_selections(plain) == []


def test_checklist_initial_checked_clamped():
	f = make_form()
	cl = f.create_checklist("Mods", ["a"], checked=[0, 5, -1])
	assert f.get_checked_items(cl) == [0]


def test_checklist_checked_true_checks_everything():
	f = make_form()
	cl = f.create_checklist("Mods", ["a", "b", "c"], checked=True)
	assert f.get_checked_items(cl) == [0, 1, 2]


# -- key press feedback interrupts --------------------------------------------
# Feedback to a key press must cut off the previous feedback; queued
# (interrupt=False) it piled up behind fast typing and scrolling (seen
# with Vosh, which honors the flag exactly).


def feedback_after(speech, action):
	"""The (text, interrupt) calls one action produced."""
	start = len(speech.spoken)
	action()
	return list(zip(speech.spoken[start:], speech.interrupts[start:]))


def all_interrupt(calls):
	return calls and all(flag is True for _, flag in calls)


def test_typing_and_deleting_interrupt(speech, frame):
	f = make_form()
	box = f.create_input_box("Name")
	f.focus(box)
	assert all_interrupt(feedback_after(speech, lambda: frame(f, chars="ab c")))
	assert speech.spoken[-1] == "c"
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_BACKSPACE)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_HOME)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_DELETE)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_END)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_LEFT)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_RIGHT)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_LEFT, ctrl=True)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_RIGHT, shift=True)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_BACKSPACE)))


def test_password_typing_interrupts(speech, frame):
	f = make_form()
	box = f.create_input_box("Password", password=True)
	f.focus(box)
	calls = feedback_after(speech, lambda: frame(f, chars="xy"))
	assert calls == [("star", True), ("star", True)]


def test_multiline_line_reading_interrupts(speech, frame):
	f = make_form()
	box = f.create_input_box("Notes", "one\ntwo", multiline=True)
	f.focus(box)
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_UP)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_DOWN)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_RETURN)))
	assert speech.spoken[-1] == "new line"


def test_clipboard_feedback_interrupts(speech, frame, fake_clipboard):
	f = make_form()
	box = f.create_input_box("Name", "hello")
	f.focus(box)
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_a, ctrl=True)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_c, ctrl=True)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_x, ctrl=True)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_v, ctrl=True)))


def test_list_navigation_interrupts(speech, frame):
	f = make_form()
	lst = f.create_list("Sounds", ["alpha", "beta", "gamma"])
	f.focus(lst)
	for key in (lucia.K_DOWN, lucia.K_DOWN, lucia.K_UP, lucia.K_END, lucia.K_HOME):
		assert all_interrupt(feedback_after(speech, lambda: frame(f, key=key)))
	checklist = f.create_checklist("Options", ["a", "b"])
	f.focus(checklist)
	calls = feedback_after(speech, lambda: frame(f, key=lucia.K_SPACE))
	assert calls == [("checked", True)]


def test_tab_slider_and_checkbox_interrupt(speech, frame):
	f = make_form()
	tabs = f.create_tab_panel("Pages", ["General", "Audio"])
	slider = f.create_slider("Volume", 50, step=10)
	box = f.create_checkbox("Mute")
	f.focus(tabs)
	assert feedback_after(speech, lambda: frame(f, key=lucia.K_RIGHT)) == [
		("Audio tab, 2 of 2", True)
	]
	f.focus(slider)
	assert feedback_after(speech, lambda: frame(f, key=lucia.K_RIGHT)) == [("60", True)]
	f.focus(box)
	assert feedback_after(speech, lambda: frame(f, key=lucia.K_SPACE)) == [("checked", True)]
	# moving focus with tab speaks the newly focused control, interrupting
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_TAB)))


def test_menu_bar_navigation_interrupts(speech, frame, monkeypatch):
	f = make_form()
	f.create_button("OK")
	file_menu = f.add_menu("File")
	f.add_menu_item(file_menu, "Open")
	f.add_menu_item(file_menu, "Save")
	f.add_menu("Edit")
	monkeypatch.setattr(lucia, "keys_held", Held({lucia.K_LALT: True}))
	monkeypatch.setattr(lucia, "current_key_pressed", lucia.K_LALT)
	assert all_interrupt(feedback_after(speech, f._monitor_menus))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_RIGHT)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_LEFT)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_DOWN)))
	assert all_interrupt(feedback_after(speech, lambda: frame(f, key=lucia.K_DOWN)))
	assert speech.spoken[-1] == "Save"


def test_progress_does_not_interrupt(speech):
	"""Progress isn't feedback to a key press; it must not cut off what
	the player is listening to."""
	f = make_form()
	bar = f.create_progress_bar("Download", speak_interval=10)
	calls = feedback_after(speech, lambda: f.set_progress(bar, 50))
	assert calls == [("50 percent", False)]


def test_opening_announcement_title_then_control(speech, frame):
	"""On opening, the title is spoken first and the focused control is
	appended behind it instead of cutting the title off."""
	f = AudioForm("Settings")
	f.create_input_box("Name")
	calls = feedback_after(speech, lambda: frame(f))
	assert [text for text, _ in calls][0] == "Settings"
	assert calls[1][1] is False  # the control queues behind the title
	assert len(calls) == 2


class _StopLoop(BaseException):
	"""Breaks out of a UI loop (they catch Exception) once the script ends."""


def scripted_frames(monkeypatch, frames):
	"""Replace lucia.process_events: each call plays the next frame, a
	(key, chars) pair, and returns its KEYDOWN events."""
	import pygame

	frames = list(frames)

	def process_events():
		if not frames:
			raise _StopLoop()
		key, chars = frames.pop(0)
		monkeypatch.setattr(lucia, "current_key_pressed", key if key is not None else -1)
		monkeypatch.setattr(lucia, "get_characters", lambda: chars)
		if key is None:
			return []
		return [pygame.event.Event(pygame.KEYDOWN, key=key, unicode="")]

	monkeypatch.setattr(lucia, "process_events", process_events)
	monkeypatch.setattr(lucia, "keys_held", Held())


def test_menu2_navigation_interrupts(monkeypatch):
	from lucia.ui import menu2

	speech = Speech()
	speech.speak = speech.output
	monkeypatch.setattr(lucia, "output", speech)

	class Pool:
		def play_stationary(self, *args):
			pass

	class Backend:
		SoundPool = Pool

	monkeypatch.setattr(lucia, "audio_backend", Backend, raising=False)
	items = [
		menu2.MenuItem("Start"),
		menu2.MenuItem("Volume", has_value=True, value=5),
		menu2.MenuItem("Music", can_be_toggled=True),
	]
	menu = menu2.Menu(items, title="Main")
	scripted_frames(monkeypatch, [
		(lucia.K_DOWN, ""), (lucia.K_DOWN, ""), (lucia.K_SPACE, ""), (lucia.K_UP, ""),
	])
	with pytest.raises(_StopLoop):
		menu.run()
	assert speech.interrupts[0] is False  # the opening announcement
	assert speech.spoken[1].startswith("Volume")
	assert len(speech.spoken) == 5
	assert all(flag is True for flag in speech.interrupts[1:])


def test_virtualinput_feedback_interrupts(monkeypatch):
	from lucia.ui.virtualinput import VirtualInput

	speech = Speech()
	speech.speak = speech.output
	monkeypatch.setattr(lucia, "output", speech)
	scripted_frames(monkeypatch, [
		(None, "ab"), (lucia.K_LEFT, ""), (lucia.K_RIGHT, ""), (lucia.K_END, ""),
		(lucia.K_BACKSPACE, ""), (lucia.K_UP, ""),
	])
	with pytest.raises(_StopLoop):
		VirtualInput("Name").run()
	assert speech.spoken[1:] == ["a", "b", "b", "blank", "Blank", "b", "a"]
	assert speech.interrupts[0] is False  # the prompt
	assert all(flag is True for flag in speech.interrupts[1:])
