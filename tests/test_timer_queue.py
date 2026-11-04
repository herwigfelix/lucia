"""Tests for the named callback timer queue, driven by a fake clock."""

import pytest

from lucia.utils import TimerQueue


@pytest.fixture
def queue():
	clock = [0.0]
	q = TimerQueue(time_func=lambda: clock[0])
	q.clock = clock  # test handle to advance time
	return q


def advance(queue, ms):
	queue.clock[0] += ms / 1000.0


def test_one_shot_fires_once(queue):
	fired = []
	queue.set("boom", lambda: fired.append(1), 500)
	assert queue.loop() == 0
	advance(queue, 499)
	assert queue.loop() == 0
	advance(queue, 1)
	assert queue.loop() == 1
	assert fired == [1]
	assert not queue.exists("boom")
	advance(queue, 1000)
	assert queue.loop() == 0


def test_repeating_fires_each_interval(queue):
	fired = []
	queue.set("tick", lambda: fired.append(1), 100, repeating=True)
	for _ in range(5):
		advance(queue, 100)
		queue.loop()
	assert len(fired) == 5
	assert queue.exists("tick")


def test_catchup_is_capped(queue):
	fired = []
	queue.set("tick", lambda: fired.append(1), 100, repeating=True)
	advance(queue, 1000)  # ten intervals of lag at once
	total = 0
	for _ in range(20):
		total += queue.loop(max_catchup=100)
	assert total <= 3, "a lag spike must not cause a callback storm"


def test_max_timers_limits_per_call(queue):
	fired = []
	queue.set("a", lambda: fired.append("a"), 100)
	queue.set("b", lambda: fired.append("b"), 200)
	advance(queue, 300)
	assert queue.loop(max_timers=1) == 1
	assert fired == ["a"], "the earlier deadline fires first"
	assert queue.loop() == 1
	assert fired == ["a", "b"]


def test_set_replaces_existing(queue):
	fired = []
	queue.set("x", lambda: fired.append("old"), 100)
	queue.set("x", lambda: fired.append("new"), 100)
	advance(queue, 100)
	queue.loop()
	assert fired == ["new"]
	assert len(queue) == 0


def test_elapsed_timeout_restart(queue):
	queue.set("t", lambda: None, 500)
	advance(queue, 200)
	assert queue.elapsed("t") == 200
	assert queue.timeout("t") == 500
	assert queue.restart("t")
	assert queue.elapsed("t") == 0
	advance(queue, 400)
	assert queue.loop() == 0  # restart pushed the deadline
	advance(queue, 100)
	assert queue.loop() == 1
	assert queue.elapsed("unknown") == 0
	assert queue.timeout("unknown") == 0
	assert not queue.restart("unknown")


def test_set_timeout_changes_interval(queue):
	fired = []
	queue.set("t", lambda: fired.append(1), 1000)
	assert queue.set_timeout("t", 100, repeating=True)
	assert queue.is_repeating("t")
	advance(queue, 100)
	queue.loop()
	advance(queue, 100)
	queue.loop()
	assert len(fired) == 2


def test_pause_and_resume(queue):
	fired = []
	queue.set("t", lambda: fired.append(1), 200)
	advance(queue, 100)
	assert queue.pause("t")
	frozen = queue.elapsed("t")
	advance(queue, 1000)
	assert queue.loop() == 0, "paused timers never fire"
	assert queue.elapsed("t") == frozen
	assert queue.resume("t")
	advance(queue, 100)
	assert queue.loop() == 1
	assert not queue.pause("unknown")
	assert not queue.resume("t")


def test_delete_from_callback(queue):
	fired = []

	def first():
		fired.append("first")
		queue.delete("second")

	queue.set("first", first, 100)
	queue.set("second", lambda: fired.append("second"), 100)
	advance(queue, 100)
	queue.loop()
	assert fired == ["first"]


def test_flush_fires_everything_once(queue):
	fired = []
	queue.set("a", lambda: fired.append("a"), 5000)
	queue.set("b", lambda: fired.append("b"), 9000, repeating=True)
	queue.flush()
	assert sorted(fired) == ["a", "b"]
	assert len(queue) == 0


def test_reset_discards_without_firing(queue):
	fired = []
	queue.set("a", lambda: fired.append("a"), 100)
	queue.reset()
	advance(queue, 1000)
	assert queue.loop() == 0
	assert fired == []


def test_args_and_kwargs(queue):
	seen = []
	queue.set("t", lambda a, b=None: seen.append((a, b)), 100, "pos", b="kw")
	advance(queue, 100)
	queue.loop()
	assert seen == [("pos", "kw")]


def test_positional_after_ms_goes_to_callback_not_repeating(queue):
	# a level-7 trap seen in a real game: set(id, cb, ms, "file.ogg") used to land
	# "file.ogg" in `repeating` and call cb() without its argument
	seen = []
	queue.set("echo", seen.append, 100, "wall.ogg")
	assert not queue.is_repeating("echo")
	advance(queue, 100)
	queue.loop()
	assert seen == ["wall.ogg"]
	assert queue.failures == []
	advance(queue, 1000)
	queue.loop()
	assert seen == ["wall.ogg"]  # fired once, not forever


def test_repeating_is_keyword_only_with_args(queue):
	seen = []
	queue.set("tick", seen.append, 100, "x", repeating=True)
	advance(queue, 100)
	queue.loop()
	advance(queue, 100)
	queue.loop()
	assert seen == ["x", "x"]


def test_uncallable_callback_is_rejected(queue):
	with pytest.raises(TypeError):
		queue.set("swapped", 100, lambda: None)


def test_failure_is_reported_once_per_id(queue, capsys, monkeypatch):
	monkeypatch.delenv("LUCIA_SELFTEST", raising=False)

	def broken():
		raise ValueError("kaputt")

	queue.set("b", broken, 100, repeating=True)
	for _ in range(3):
		advance(queue, 100)
		queue.loop()
	err = capsys.readouterr().err
	assert err.count("timer 'b' raised") == 1
	assert "ValueError: kaputt" in err
	assert len(queue.failures) == 3


def test_failure_propagates_under_selftest(queue, monkeypatch):
	monkeypatch.setenv("LUCIA_SELFTEST", "1")

	def broken():
		raise RuntimeError("boom")

	queue.set("b", broken, 100)
	advance(queue, 100)
	with pytest.raises(RuntimeError):
		queue.loop()
	assert queue.failures[0][0] == "b"


def test_callback_exception_is_contained(queue, monkeypatch):
	monkeypatch.delenv("LUCIA_SELFTEST", raising=False)
	fired = []

	def broken():
		raise RuntimeError("boom")

	queue.set("broken", broken, 100)
	queue.set("fine", lambda: fired.append(1), 100)
	advance(queue, 100)
	assert queue.loop() == 2
	assert fired == [1]
	assert len(queue.failures) == 1
	assert queue.failures[0][0] == "broken"
	assert isinstance(queue.failures[0][1], RuntimeError)


def test_list_timers_and_size(queue):
	queue.set("a", lambda: None, 100)
	queue.set("b", lambda: None, 100)
	assert sorted(queue.list_timers()) == ["a", "b"]
	assert queue.size == 2
