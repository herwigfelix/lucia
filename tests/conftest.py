"""Test-wide safety: self tests must never make a sound.

The real screen reader is kept out of every test: LUCIA_VOICEOVER=0 stops
lucia.output from routing speech to VoiceOver (announcements or the
osascript bridge), so a test that exercises speech routing can't speak
through the user's running VoiceOver. Tests that check the VoiceOver
routing mock lucia.voiceover and lift the switch themselves.
"""

import os

os.environ["LUCIA_VOICEOVER"] = "0"
