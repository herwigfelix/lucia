import os

from .util import here

import lucia

test_data = b"This is some sample data, to use in a resource file."


def _pack_path():
	return os.path.join(here, "test.dat")


def test_packfile_creation():
	try:
		os.remove(_pack_path())
	except FileNotFoundError:
		pass
	pack = lucia.ResourceFile("test123")
	pack.add_memory("sample.mem", test_data)
	pack.save(_pack_path())
	assert os.path.isfile(_pack_path())


def test_load_pack_with_load_all_policy():
	test_packfile_creation()
	pack = lucia.ResourceFile("test123")
	pack.load(_pack_path(), lucia.LoadPolicy.LOAD_ALL)
	assert pack.get("sample.mem") == test_data
	assert pack.exist("sample.mem")


def test_load_pack_with_load_index_policy():
	test_packfile_creation()
	pack = lucia.ResourceFile("test123")
	pack.load(_pack_path(), lucia.LoadPolicy.LOAD_INDEX)
	assert pack.get("sample.mem") == test_data
