# Changelog

## 1.1

Bug-fix release. No new features.

### Game files (backend)
- **ReShade uninstall no longer overwrites the injector's DLL.**
  - What happened in 1.0: ReShade was installed over a game's own `dxgi.dll`, and then OptiScaler or d4r was installed. Removing ReShade then put the game's file back over the injector's DLL, so the injector silently stopped working. Removing the injector afterwards deleted the game's original for good.
  - Now, when ReShade gives up a DLL name, the game's file goes back into that slot straight away and the injector backs it up itself. ReShade uninstall never overwrites a DLL that another tool is using.
- **Files a game update adds are no longer deleted on uninstall.** RAT now keeps a hash of every binary it installs (`.dll`, `.exe`, `.so`, `.asi`, `.addon64/32`), not only the ones that replaced a game file. A game patch that ships its own copy of a file RAT added (for example `dxgi.dll` or `libxess.dll`) is kept. Hashes are recomputed after d4r's `install.sh` runs.
- **A leftover backup is restored again.** In 1.0, if an interrupted install had left the game's original in `.rat/backup`, that file was never recorded and was lost on uninstall.
- **Removing a HelixSR Scaler that RAT did not install no longer loses files.** The game's file was deleted before its restore was tried, and the backup folder was deleted even when that restore failed.
- **Changing game files while a background job runs is refused.** Covers toggling injection, upscaler, options, saving .ini, enabling or removing ReShade, presets, and HDR toggle/settings/removal. Before, two writers could clobber `record.json` or `reshade.json`.

### Other backend fixes
- `steam.current_compat_tool`: a game set to "default" Proton (`name ""`) now reports the global default tool.
- `sources.latest`: pasted `releases/tag/X` links work for tags older than the 15 newest releases.
- `sources.extract`: the 7z path check now compares whole path components, and rejects links that point outside the folder.
- `util.run_streaming`: the subprocess pipe is drained after exit, so the last line (HelixSR's `@@RAT@@` result) is never lost.
- `profiles.paste`: profiles imported with the wrong types (a string `compositor`, a list `reshade`, and so on) are cleaned up instead of being stored and crashing every later game start.

### Frontend
- A result from one game can no longer overwrite another game's state when you switch games mid-call. Covers `act`, compat info and the launch plan.
- `syncLaunch` catches backend errors instead of causing unhandled promise rejections.
- Switching frame generation method clears the previous method's edits to keys that were missing from the .ini.
- The progress dialog auto-closes again after chained jobs (wipe, then install).
- Cancelling a text prompt now resolves it.
- "Remove ReShade" no longer touches launch options when ReShade was kept for HDR, or when removal failed.

### Game files, second pass
- **Case-insensitive file matching, as Wine does it.** A game's `D3DCompiler_47.dll`, `LibXeSS.dll`, `DXGI.dll` and so on are now treated as the same file as RAT's lowercase name.
  - Injector installs back the game's file up and restore it under its original spelling.
  - ReShade backs up a game DLL whose name differs only in case.
  - ReShade no longer adds a second d3dcompiler next to the game's own.
- **Turning d4r off only renames files d4r installed.** In 1.0, a game's own `dxgi.dll`, `d3d12.dll` or `d3d12core.dll` disappeared while d4r was off.
- **ReShade backs up a file again when a game update puts it back.** In 1.0, if an old backup already existed, `relink` overwrote the file without saving it.
- **"Close the game first" works for libraries reached through a symlink** (an SD card under `/run/media`).
- **Preset actions only work on ReShade presets.** Delete, import and "from library" no longer delete or overwrite `OptiScaler.ini`, `fakenvapi.ini` or the game's own .ini files.
- **Proxy names and option values are checked.** A ReShade proxy name outside the known list is refused; before, it became a file name in the game folder. Injector options with a fixed list of values reject anything else. The record's `proxy` now names the DLL that is actually installed.

### Other fixes, second pass
- **GPU detection on hybrid laptops.** With an AMD APU plus an NVIDIA or AMD discrete GPU, RAT now picks the discrete card, and KFD data from an AMD APU no longer overrides an NVIDIA card.
  - Also fixes a separate bug: the integrated-GPU penalty never worked, even for Intel. `len(cards)` was read inside the `sort()` key, and CPython empties the list while it sorts.
- **The main exe is found even when its name contains a helper word** (`Observer.exe` contains "server"). Filtered names are used only when nothing else is left.
- **Launch options:**
  - `env -u FOO` or `env -i` after gamescope is kept; only a bare, stray `env` is removed.
  - Backslash escapes split correctly (`FOO=a\ b`, `"x\"y"`).
- **Checksums:**
  - `./name` entries and BSD-format lines (`SHA256 (name) = …`) are understood.
  - An unusable checksum file is logged.
  - The job log says so when a release has no checksum and the download is unverified.
- **Downloads:**
  - Connection resets and short reads now fall back to curl.
  - A cancelled or failed download no longer leaves a `.part` file behind.
- **INI files:**
  - UTF-16 files (Unreal's `Engine.ini`) are read and written back in UTF-16 instead of being mangled.
  - Lines are split only on real line breaks; `\x0c`, `\x85` and similar characters no longer split a line.
- **Re-importing a fork link that fails** puts the existing source back instead of deleting it.
- **RenoDX uninstall** no longer crashes when the exe folder is unknown.
- **The process start-time parser** no longer raises errors its callers don't catch.

### Tests
- New: `tests/test_fixes_1_1.py`, with 16 tests. All 16 fail on 1.0 and pass on 1.1.
- The full suite now runs 150 tests.
