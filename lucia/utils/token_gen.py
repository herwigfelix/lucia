# Copyright (C) 2026  LuciaSoftware and it's contributors.
#
# Modeled after NVGT's token_gen include.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Random token/ID generation."""

import secrets
import string

TOKEN_CHARACTERS = 0  # letters + digits
TOKEN_LETTERS = 1
TOKEN_DIGITS = 2
TOKEN_SYMBOLS = 3  # letters + digits + punctuation


def generate_token(length=16, mode=TOKEN_CHARACTERS):
	"""Generate a cryptographically random token of the given length."""
	if mode == TOKEN_LETTERS:
		alphabet = string.ascii_letters
	elif mode == TOKEN_DIGITS:
		alphabet = string.digits
	elif mode == TOKEN_SYMBOLS:
		alphabet = string.ascii_letters + string.digits + string.punctuation
	else:
		alphabet = string.ascii_letters + string.digits
	return "".join(secrets.choice(alphabet) for _ in range(length))
