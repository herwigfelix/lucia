from lucia.utils import (
	StatSet,
	generate_token,
	number_to_words,
	size_to_string,
	string_to_size,
)
from lucia.utils.rotation import (
	calculate_angle,
	get_2d_distance,
	get_3d_distance,
	getdir,
	move,
	turnleft,
	turnright,
)


def test_number_to_words():
	assert number_to_words(0) == "zero"
	assert number_to_words(7) == "seven"
	assert number_to_words(15) == "fifteen"
	assert number_to_words(42) == "forty two"
	assert number_to_words(-3) == "minus three"
	assert number_to_words(100) == "one hundred"
	assert number_to_words(101, include_and=True) == "one hundred and one"
	assert number_to_words(2000000000).startswith("two billion")


def test_rotation_distances():
	assert get_2d_distance(0, 0, 3, 4) == 5.0
	assert get_3d_distance(0, 0, 0, 0, 0, 5) == 5.0


def test_rotation_getdir():
	assert getdir(0) == 0
	assert getdir(44) == 45
	assert getdir(93) == 90
	assert getdir(359) == 0
	assert getdir(180) == 180


def test_rotation_turning():
	assert turnright(355, 10) == 5
	assert turnleft(5, 10) == 355


def test_rotation_move():
	v = move((0, 0, 0), 90)  # facing east
	assert round(v.x, 6) == 1.0
	assert round(v.y, 6) == 0.0


def test_calculate_angle_straight_ahead():
	assert calculate_angle(0, 0, 0, 5, 0) == 0


def test_stat_set():
	stats = StatSet()
	changes = []
	stats.add(
		"health", 100, minimum=0, maximum=100,
		on_change=lambda stat, old, new: changes.append((old, new)),
	)
	stats.change("health", -30)
	assert stats["health"] == 70
	stats.change("health", -1000)
	assert stats["health"] == 0
	assert stats.stat("health").at_minimum
	stats["health"] = 100
	assert stats.stat("health").at_maximum
	assert changes[0] == (100, 70)


def test_token_gen():
	token = generate_token(32)
	assert len(token) == 32
	assert token != generate_token(32)


def test_size_strings():
	assert size_to_string(512) == "512 bytes"
	assert size_to_string(1536) == "1.50 KB"
	assert string_to_size("1.5 MB") == 1572864
	assert string_to_size("100") == 100
