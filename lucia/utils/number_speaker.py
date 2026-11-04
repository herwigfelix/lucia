# Copyright (C) 2026  LuciaSoftware and it's contributors.
#
# Modeled after NVGT's number_speaker include.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Speak numbers using recorded sound files, with TTS fallback.

Point a NumberSpeaker at a folder of recordings named after the words
they contain ("one.ogg", "twenty.ogg", "hundred.ogg", ...). Numbers are
broken into words with lucia.utils.number_to_words and the matching
files are played in sequence; words without a recording fall back to TTS.

	speaker = lucia.utils.NumberSpeaker("sounds/numbers", extension=".ogg")
	speaker.speak(342)
"""

import os

import lucia
from .number_to_words import number_to_words


class NumberSpeaker:
	def __init__(self, sound_directory=None, extension=".ogg", word_gap_ms=0):
		self.sound_directory = sound_directory
		self.extension = extension
		self.word_gap_ms = word_gap_ms
		self._sounds = []

	def speak(self, number, interrupt=True):
		"""Speak a number. Uses recordings when available, TTS otherwise."""
		words = number_to_words(number).replace(" and ", " ").split()
		if not self.sound_directory:
			lucia.output.speak(" ".join(words), interrupt)
			return
		if interrupt:
			self.stop()
		for word in words:
			path = os.path.join(self.sound_directory, word + self.extension)
			if os.path.isfile(path):
				sound = lucia.audio_backend.Sound()
				if sound.load(path):
					self._sounds.append(sound)
					sound.play_wait()
					continue
			lucia.output.speak(word, False)

	def stop(self):
		for sound in self._sounds:
			sound.close()
		self._sounds.clear()
