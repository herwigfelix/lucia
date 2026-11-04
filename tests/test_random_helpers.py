"""Tests for the seedable random helpers."""

from lucia.utils import random_helpers
from lucia.utils.random_helpers import RandomGenerator


def test_seeded_generators_repeat():
	a = RandomGenerator(seed=42)
	b = RandomGenerator(seed=42)
	assert [a.range(0, 100) for _ in range(20)] == [b.range(0, 100) for _ in range(20)]


def test_state_roundtrip():
	gen = RandomGenerator(seed=7)
	state = gen.get_state()
	first = [gen.range(0, 1000) for _ in range(10)]
	assert gen.set_state(state)
	assert [gen.range(0, 1000) for _ in range(10)] == first


def test_set_state_rejects_garbage():
	assert RandomGenerator().set_state("garbage") is False


def test_range_is_inclusive_both_ends():
	gen = RandomGenerator(seed=1)
	seen = {gen.range(1, 3) for _ in range(1000)}
	assert seen == {1, 2, 3}
	assert gen.range(5, 5) == 5
	# swapped bounds are tolerated
	assert 1 <= gen.range(3, 1) <= 3


def test_chance_extremes():
	gen = RandomGenerator()
	assert gen.chance(0) is False
	assert gen.chance(100) is True
	assert gen.chance(-5) is False
	assert gen.chance(150) is True


def test_choice_empty_is_none():
	gen = RandomGenerator()
	assert gen.choice([]) is None
	assert gen.choice(["only"]) == "only"


def test_weighted_choice():
	gen = RandomGenerator(seed=3)
	assert all(
		gen.weighted_choice(["a", "b", "c"], [0, 1, 0]) == "b" for _ in range(50)
	)
	assert gen.weighted_choice([], []) is None
	assert gen.weighted_choice(["a"], [1, 2]) is None
	assert gen.weighted_choice(["a", "b"], [0, 0]) is None


def test_shuffle_keeps_elements():
	gen = RandomGenerator(seed=9)
	items = list(range(50))
	copy = list(items)
	gen.shuffle(copy)
	assert sorted(copy) == items


def test_shuffled_leaves_original():
	gen = RandomGenerator(seed=9)
	items = list(range(50))
	result = gen.shuffled(items)
	assert items == list(range(50))
	assert sorted(result) == items


def test_roll_dice_bounds():
	gen = RandomGenerator(seed=5)
	for _ in range(100):
		total = gen.roll_dice(3, 6)
		assert 3 <= total <= 18
	assert gen.roll_dice(0, 6) == 0
	assert gen.roll_dice(2, 0) == 0


def test_module_level_functions_share_default():
	random_helpers.random_seed(123)
	first = [random_helpers.random_range(0, 100) for _ in range(5)]
	random_helpers.random_seed(123)
	assert [random_helpers.random_range(0, 100) for _ in range(5)] == first
	assert random_helpers.random_bool(100) is True
	assert random_helpers.random_bool(0) is False
	assert 0.0 <= random_helpers.random_float() <= 1.0
	assert random_helpers.random_choice([]) is None
	state = random_helpers.random_get_state()
	value = random_helpers.random_range(0, 10 ** 9)
	assert random_helpers.random_set_state(state)
	assert random_helpers.random_range(0, 10 ** 9) == value
	assert 1 <= random_helpers.roll_dice(1, 6) <= 6
	items = [1, 2, 3]
	assert sorted(random_helpers.shuffled(items)) == items


def test_exported_via_lucia_utils():
	import lucia

	assert lucia.utils.RandomGenerator is RandomGenerator
	assert callable(lucia.utils.random_range)
	assert callable(lucia.utils.weighted_choice)
