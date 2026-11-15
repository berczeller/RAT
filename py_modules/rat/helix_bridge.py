#!/usr/bin/env python3
"""Runs HelixSR's own per-game installer (setup/helixsr_install.py from the official release) without its questions.

Called by RAT with the system Python (HelixSR's installer uses only the standard library):
    helix_bridge.py ready     HELIXSR_DIR
    helix_bridge.py status    HELIXSR_DIR GAME_FOLDER
    helix_bridge.py install   HELIXSR_DIR GAME_FOLDER ROUTE(auto|direct|scaler)
    helix_bridge.py uninstall HELIXSR_DIR GAME_FOLDER
The last output line is '@@RAT@@ ' followed by a JSON result. HelixSR's files are used unmodified.
"""
import json
import sys
from pathlib import Path

MARK = "@@RAT@@ "


def out(obj):
    print(MARK + json.dumps(obj), flush=True)


def load(here):
    sys.path.insert(0, str(Path(here) / "setup"))
    import helixsr_install as h   # noqa: E402  (HelixSR's module)
    return h


def game_for(h, folder):
    gl = h.games_from([folder], [])
    if not gl:
        return None
    return gl[0]


def describe(h, g):
    if g is None:
        return {"found": False}
    return {
        "found": True, "name": g.name, "folder": str(g.folder), "route": g.route, "current": g.current(),
        "ways": g.ways(), "inputs": g.inputs, "fsr_targets": [str(t) for t in g.fsr_targets],
        "target": str(g.target) if g.target else None, "anticheat": list(g.anticheat),
        "installed": bool(g.installed()), "label": g.label(),
    }


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, here = sys.argv[1], Path(sys.argv[2]).resolve()
    h = load(here)
    if cmd == "ready":
        out({"ready": bool(h.network_ready(here)), "version": h.version_of(here / h.DLL)})
        return 0
    folder = sys.argv[3]
    g = game_for(h, folder)
    if cmd == "status":
        out(describe(h, g))
        return 0
    if g is None:
        out({"ok": False, "error": "HelixSR found no DLSS, XeSS or FSR in this game"})
        return 1
    if cmd == "install":
        route = sys.argv[4] if len(sys.argv) > 4 else "auto"
        ways = g.ways()
        if route == "auto":
            route = g.route if g.route in ways else (ways[0] if ways else None)
        if route not in ways:
            why = g.label() if not ways else f"this game can use: {', '.join(ways)}"
            out({"ok": False, "error": f"HelixSR cannot use '{route}' here ({why})"})
            return 1
        if not h.network_ready(here):
            out({"ok": False, "error": "HelixSR's network is not built yet (prepare first)"})
            return 1
        g.route = route
        try:
            name = h.install(g, here, False)
        except (SystemExit, OSError) as e:
            out({"ok": False, "error": str(e)})
            return 1
        g2 = game_for(h, folder)
        res = describe(h, g2)
        res.update(ok=True, proxy=name if route == "scaler" else None,
                   launch=h.launch_options(name) if route == "scaler" and name else None)
        out(res)
        return 0
    if cmd == "uninstall":
        try:
            h.uninstall(g)
        except (SystemExit, OSError) as e:
            out({"ok": False, "error": str(e)})
            return 1
        out({"ok": True})
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
