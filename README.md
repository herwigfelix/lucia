# lucia

### An audio game engine written in Python

lucia is an engine for audio games: games played entirely by sound —
screen-reader/TTS speech, positional audio and keyboard input, designed
for blind and visually impaired players. It replaces the abandoned BGT
with modern Python, and version 2.0 is a full modernization of the
original engine.

## What's inside

- **Core** — window, game loop, keyboard state (`lucia.initialize()`,
  `lucia.process_events()`, `lucia.key_pressed(...)`), built on
  [pygame-ce](https://pyga.me).
- **Speech** — `lucia.output.speak(...)` via
  [Prism](https://github.com/ethindp/prism/) (prismatoid): NVDA, JAWS,
  SAPI, VoiceOver, Speech-dispatcher, Orca and more behind one API. On
  macOS, speech goes through the [Vosh](https://github.com/FriduxTech/Vosh)
  screen reader instead whenever it runs.
- **Audio** — miniaudio-based backend with lucia's own software mixer:
  wav/ogg/mp3/flac, unlimited voices, per-sound volume/pan/pitch, and
  the classic BGT-style `SoundPool` for 1D/2D/3D positional audio. No
  external DLLs.
- **Music** — `lucia.audio.MusicManager` with fades and crossfades.
- **UI** — self-voicing `Menu`, `AudioForm` (buttons, inputs, lists,
  checkboxes, sliders with full keyboard navigation), and
  `alert`/`question`/`input_box` dialogs.
- **Maps** — `lucia.Map` and `lucia.MapWalker`: a tile map engine for
  1D, 2D and 3D worlds. Text map files describe material tiles, walls,
  named zones, doors and ambient sound sources; the walker handles
  collision, material footsteps, wall bumps, zone changes and
  distance-managed ambience.
- **Networking** — `lucia.network`: `url_get`/`url_post`, an async
  `HttpRequest` with progress, a resumable large-file downloader, and an
  NVGT-style `Network` class for online games (reliable ordered channels
  over UDP, no native dependencies).
- **Persistence** — `lucia.Settings` (per-user JSON/INI settings) and
  the LURF `ResourceFile` pack format with encryption.
- **Utils** — timers, stat sets, rotation/bearing math, number-to-words,
  `NumberSpeaker`, token generation, byte-size formatting, single
  instance checking.

## Installation

Requires Python 3.10+.

```
pip install "lucia @ git+https://github.com/herwigfelix/lucia.git"
```

Or from source:

```
git clone https://github.com/herwigfelix/lucia.git
cd lucia
pip install -e .[test]
```

## A minimal game

```python
import lucia

def main():
	lucia.initialize()
	lucia.show_window("My Game")
	lucia.output.speak("Welcome! Press escape to quit.", True)
	pool = lucia.audio_backend.SoundPool()
	while lucia.running:
		lucia.process_events()
		if lucia.key_pressed(lucia.K_SPACE):
			pool.play_stationary("sounds/hello.ogg")
		if lucia.key_pressed(lucia.K_ESCAPE):
			break
	lucia.quit()

if __name__ == "__main__":
	main()
```

## Running tests

```
python -m pytest
```

## Origin

lucia was started by LuciaSoftware and its contributors (the original
repository, github.com/LuciaSoftware/lucia, is no longer online). This
repository continues it from version 1.1.1: version 2.0 replaces the
BASS/FMOD/OpenAL backends with miniaudio and a software mixer, moves to
pygame-ce and Prism, and adds the UI, map, networking and utility
modules listed above. Parts of the positional-audio code go back to the
BGT (Blastbay Game Toolkit) includes and the rotation helpers to
community BGT code; the original authors are credited in the file
headers.

## License

Copyright (C) 2018-2026 LuciaSoftware and it's contributors.

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, version 3 of the License. See
[LICENSE](LICENSE).
