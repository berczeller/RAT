"""A virtual keyboard (Linux uinput) that taps one key into the running game, so ReShade applies changes RAT just wrote
to its files without anyone touching a keyboard.

ReShade never re-reads a preset file on its own, but a preset shortcut key does: pressing it starts a "preset
transition", during which ReShade re-reads the preset from disk every frame (runtime::switch_to_next_preset /
load_current_preset; its ini cache reloads a file whose timestamp changed). RAT binds F24, a key no keyboard has, as a
preset shortcut to the game's active preset when ReShade is installed, and taps it after each change made from Decky:
effect toggles and shader parameters then apply live.

Decky's backend runs as root, so /dev/uinput is writable; gamescope picks the new keyboard up like any hot-plugged one.
When uinput is missing, xdotool on the game's X display is tried. Nothing here is fatal: callers fall back to
"applies at the next start".
"""
from __future__ import annotations

import fcntl
import os
import struct
import subprocess
import threading
import time

from .util import clean_env, log, which

EV_SYN, EV_KEY, SYN_REPORT = 0x00, 0x01, 0x00
KEY_F24 = 194
VK_F24 = 0x87   # what Wine reports to the game for KEY_F24
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_SETUP, UI_DEV_CREATE = 0x405C5503, 0x5501
_EVENT = struct.Struct("llHHi")

_lock = threading.Lock()
_fd: int | None = None


def _device() -> int | None:
    global _fd
    if _fd is not None:
        return _fd
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        log.info("uinput unavailable: %s", e)
        return None
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_KEYBIT, KEY_F24)
        # struct uinput_setup { struct input_id { u16 bustype, vendor, product, version }; char name[80]; u32 ff }
        setup = struct.pack("HHHH80sI", 0x06, 0x1209, 0x5241, 1, b"RAT live apply", 0)
        fcntl.ioctl(fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(fd, UI_DEV_CREATE)
    except OSError as e:
        log.info("uinput device not created: %s", e)
        os.close(fd)
        return None
    time.sleep(0.4)   # let udev / gamescope see the new keyboard before the first key
    _fd = fd
    return fd


def _emit(fd: int, etype: int, code: int, value: int) -> None:
    now = time.time()
    os.write(fd, _EVENT.pack(int(now), int((now % 1) * 1e6), etype, code, value))


def tap_f24() -> bool:
    """Presses and releases F24 in the running game. True when a key event was sent."""
    global _fd
    with _lock:
        fd = _device()
        if fd is not None:
            try:
                _emit(fd, EV_KEY, KEY_F24, 1)
                _emit(fd, EV_SYN, SYN_REPORT, 0)
                time.sleep(0.05)
                _emit(fd, EV_KEY, KEY_F24, 0)
                _emit(fd, EV_SYN, SYN_REPORT, 0)
                return True
            except OSError as e:
                log.info("uinput write failed: %s", e)
                try:
                    os.close(fd)
                except OSError:
                    pass
                _fd = None
        xdo = which("xdotool")
        if xdo:
            for display in (":1", ":0"):
                env = dict(clean_env(), DISPLAY=display)
                try:
                    r = subprocess.run([xdo, "key", "F24"], env=env, capture_output=True, timeout=5)
                    if r.returncode == 0:
                        return True
                except (OSError, subprocess.SubprocessError):
                    continue
        return False
