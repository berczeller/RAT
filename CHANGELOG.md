# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-20

Bug-fix release focused on putting game files back exactly as they were.

### Changed
- Settings that change game files are refused while a background task runs, instead of two writers editing the same files
- Preset actions only work on ReShade presets, so delete and import can't touch `OptiScaler.ini`, `fakenvapi.ini` or the game's own .ini files
- Unknown ReShade proxy names and injector option values are rejected
- The job log says when a release publishes no checksum and the download can't be verified
- Release zips use the full version number (`RAT-1.1.0.zip`)

### Fixed
- Removing ReShade no longer overwrites the injector's `dxgi.dll`, and removing the injector afterwards brings the game's original back
- Uninstall keeps a file a game update shipped in place of one RAT added (`dxgi.dll`, `libxess.dll` and others)
- The game's original is restored even when an earlier install was interrupted
- Removing a HelixSR Scaler that RAT didn't install no longer loses the game's file when a restore fails
- Game DLLs that differ from RAT's only in letter case (`D3DCompiler_47.dll`, `LibXeSS.dll`) are backed up and restored like any other
- Turning d4r off only renames the DLLs d4r installed, not the game's own `dxgi.dll` or `d3d12.dll`
- ReShade backs up a game DLL again after a game update puts it back
- "Close the game first" works for libraries on the SD card or other symlinked folders
- Laptops with an AMD APU and a discrete NVIDIA or AMD GPU now detect the discrete card
- The main executable is found even when its name contains words like "server" (`Observer.exe`)
- A game set to the default Proton reports the global default tool
- Pasted `releases/tag/...` links work for releases older than the 15 newest
- `env -u FOO` after gamescope survives launch option cleanup, and backslash escapes are split correctly
- Checksum files in BSD format or with `./name` entries are understood
- Interrupted downloads retry with curl and leave no `.part` file behind
- Unreal's UTF-16 `Engine.ini` is no longer corrupted when RAT writes to it
- A failed re-import of a fork link keeps the source that was already there
- Profiles imported from a `.rat.json` with wrong types no longer break every later game start
- HelixSR's install result is no longer lost when its last output line arrives late
- Switching games during a call no longer shows the previous game's state, launch plan or Proton tool
- Switching the frame generation method clears the previous method's pending edits
- The progress dialog closes on its own again after chained tasks (wipe, then install)
- Cancelling a text prompt closes it cleanly, and **Remove ReShade** leaves launch options alone when ReShade stays for HDR

## [1.0.0] - 2026-10-20

First version: d4r, HelixSR and OptiScaler injection, OptiScaler frame generation, RenoDX HDR, ReShade with presets and per-shader parameters, game profiles and the launch status HUD.

[1.1.0]: https://github.com/berczeller/RAT/releases/tag/1.1.0
[1.0.0]: https://github.com/berczeller/RAT/commits/1777915
