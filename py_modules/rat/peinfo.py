"""What a Windows game is made of, read from its files without running it: the main executable's architecture and the
graphics APIs it imports (PE import and delay-import tables), the engine (Unreal 4 / 5, Unity, Source, others) and, while
the game runs, the API it actually loaded (from /proc/<pid>/maps under Proton).

Used for the ReShade proxy name, the RenoDX auto profile and the launch status banner. Everything is best effort and
never raises: unknown values come back as None.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path

API_DLLS = {
    "d3d12.dll": "DX12", "d3d11.dll": "DX11", "d3d10.dll": "DX10", "d3d10_1.dll": "DX10", "d3d9.dll": "DX9",
    "d3d8.dll": "DX8", "vulkan-1.dll": "Vulkan", "opengl32.dll": "OpenGL", "dxgi.dll": "DXGI",
}
API_ORDER = ["DX12", "Vulkan", "DX11", "DX10", "DX9", "DX8", "OpenGL"]


def _rva_to_off(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for va, vsize, raw, rsize in sections:
        if va <= rva < va + max(vsize, rsize):
            return raw + (rva - va)
    return None


def _cstr(data: bytes, off: int, limit: int = 256) -> str:
    end = data.find(b"\0", off, off + limit)
    return data[off:end if end >= 0 else off + limit].decode("ascii", "replace")


def pe_info(path: str | os.PathLike, max_header: int = 64 << 20) -> dict:
    """{arch: x64|x86|None, imports: [dll names, lower case]}. Reads only the headers and the import tables."""
    out: dict = {"arch": None, "imports": []}
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if head[:2] != b"MZ" or len(head) < 0x40:
                return out
            pe = struct.unpack_from("<I", head, 0x3C)[0]
            f.seek(pe)
            hdr = f.read(24)
            if hdr[:4] != b"PE\0\0":
                return out
            machine, nsec = struct.unpack_from("<HH", hdr, 4)
            opt_size = struct.unpack_from("<H", hdr, 20)[0]
            out["arch"] = {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(machine)
            opt = f.read(opt_size)
            magic = struct.unpack_from("<H", opt, 0)[0]
            dd_off = 112 if magic == 0x20B else 96
            dirs = []
            for i in range(16):
                if dd_off + i * 8 + 8 <= len(opt):
                    dirs.append(struct.unpack_from("<II", opt, dd_off + i * 8))
            secs = []
            sec_raw = f.read(40 * nsec)
            for i in range(nsec):
                vsize, va, rsize, raw = struct.unpack_from("<IIII", sec_raw, i * 40 + 8)
                secs.append((va, vsize, raw, rsize))
            size = os.fstat(f.fileno()).st_size
            names: list[str] = []
            for idx, entry_size, name_field in ((1, 20, 12), (13, 32, 4)):   # import, delay import
                if idx >= len(dirs) or not dirs[idx][0]:
                    continue
                off = _rva_to_off(dirs[idx][0], secs)
                if off is None or off > min(size, max_header):
                    continue
                f.seek(off)
                table = f.read(min(dirs[idx][1] or 4096, 64 * 1024) + entry_size)
                for j in range(0, len(table) - entry_size + 1, entry_size):
                    rec = table[j:j + entry_size]
                    if not any(rec):
                        break
                    rva = struct.unpack_from("<I", rec, name_field)[0]
                    if idx == 13 and not (struct.unpack_from("<I", rec, 0)[0] & 1):   # old VA-based delay tables
                        continue
                    noff = _rva_to_off(rva, secs)
                    if noff is None or noff >= size:
                        continue
                    f.seek(noff)
                    names.append(_cstr(f.read(256), 0).lower())
            out["imports"] = sorted(set(n for n in names if n.endswith(".dll")))
    except (OSError, struct.error, ValueError):
        pass
    return out


def apis_from_imports(imports: list[str]) -> list[str]:
    found = {API_DLLS[n] for n in imports if n in API_DLLS}
    return [a for a in API_ORDER if a in found]


def _strings_hint(path: Path, limit: int = 48 << 20) -> set[str]:
    """APIs named inside a binary (Unreal and Unity load their RHI with LoadLibrary, which no import table shows)."""
    found: set[str] = set()
    pats = {b"d3d12.dll": "DX12", b"D3D12.dll": "DX12", b"d3d11.dll": "DX11", b"D3D11.dll": "DX11",
            b"vulkan-1.dll": "Vulkan", b"d3d9.dll": "DX9", b"opengl32.dll": "OpenGL"}
    try:
        with open(path, "rb") as f:
            tail = b""
            read = 0
            while read < limit:
                chunk = f.read(4 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                for p, api in pats.items():
                    if p in buf:
                        found.add(api)
                tail = buf[-16:]
                read += len(chunk)
    except OSError:
        pass
    return found


_UE_BIN = ("win64", "wingdk", "winrt")
_UE_REL = re.compile(rb"\+\+UE(\d)\+Release-(\d+\.\d+)")


def ue_binaries(game_folder: Path) -> tuple[Path, str] | None:
    """An Unreal game's real executable: <Project>/Binaries/<Win64|WinGDK>/<Project>-<Platform>-Shipping.exe (the .exe
    in the install root is only a bootstrapper that starts it, and injected DLLs must sit next to the real one)."""
    game_folder = Path(game_folder)
    best = None
    try:
        projects = [d for d in game_folder.iterdir() if d.is_dir() and d.name.lower() != "engine"]
    except OSError:
        return None
    for proj in [game_folder] + projects:
        try:
            plats = [d for d in (proj / "Binaries").iterdir() if d.is_dir() and d.name.lower() in _UE_BIN]
        except OSError:
            continue
        for bd in plats:
            try:
                exes = [f.name for f in bd.iterdir() if f.is_file() and f.suffix.lower() == ".exe"]
            except OSError:
                continue
            ship = [e for e in exes if e.lower().endswith("-shipping.exe")]
            pick = (ship or [e for e in exes if not re.search(r"(?i)crash|unins|launcher", e)] or [None])[0]
            if pick:
                rank = (0 if ship else 1, 0 if bd.name.lower() == "win64" else 1)
                if best is None or rank < best[0]:
                    best = (rank, bd, pick)
    return (best[1], best[2]) if best else None


def _ue_version(exe: Path | None) -> tuple[str | None, str | None]:
    """('5', '5.3') from the release branch string compiled into an Unreal executable."""
    if not exe or not exe.is_file():
        return None, None
    try:
        with open(exe, "rb") as f:
            blob = f.read(160 << 20)
    except OSError:
        return None, None
    m = _UE_REL.search(blob) or _UE_REL.search(blob.replace(b"\0", b""))
    if m:
        return m.group(1).decode(), m.group(2).decode()
    for major in (b"5", b"4"):
        if b"++UE" + major in blob or ("++UE" + major.decode()).encode("utf-16-le") in blob:
            return major.decode(), None
    return None, None


def engine_of(game_folder: Path, exe_dir: Path | None, exe_name: str | None) -> dict:
    """{engine: unreal|unity|source|other, version: '5'|'4'|None, version_full: '5.3'|None, project: str|None,
    binaries: the folder of Unreal's real executable (may differ from exe_dir when a bootstrapper was picked)}"""
    game_folder = Path(game_folder)
    out = {"engine": "other", "version": None, "version_full": None, "project": None, "binaries": None}
    ed = Path(exe_dir) if exe_dir else None
    name = (exe_name or "").lower()
    in_bin = bool(ed and ed.name.lower() in _UE_BIN and ed.parent.name.lower() == "binaries")
    ue = ue_binaries(game_folder) if (game_folder / "Engine").is_dir() or in_bin or name.endswith("-shipping.exe") \
        else None
    if re.search(r"-(win64|wingdk|winrt)-shipping\.exe$", name) or (in_bin and (game_folder / "Engine").is_dir()) or \
            (ue and (game_folder / "Engine").is_dir()):
        out["engine"] = "unreal"
        bdir, bexe = (ed, exe_name) if in_bin or not ue else ue
        out["binaries"] = str(bdir) if bdir else None
        if bdir:
            out["project"] = Path(bdir).parent.parent.name
        out["version"], out["version_full"] = _ue_version(Path(bdir) / bexe if bdir and bexe else None)
        return out
    for d in [ed, game_folder] if ed else [game_folder]:
        if d and (d / "UnityPlayer.dll").exists():
            out["engine"] = "unity"
            return out
    if (game_folder / "bin" / "engine.dll").exists() or any((game_folder / n).exists() for n in ("hl2.exe",)):
        out["engine"] = "source"
    return out


def game_profile(game_folder: str | os.PathLike, exe_dir: str | os.PathLike | None, exe_name: str | None) -> dict:
    """Everything above for one game: {arch, apis, api, engine, version, project}. For Unreal games the real
    executable in Binaries/<Platform> is read, even when the bootstrapper in the install root was picked."""
    game_folder = Path(game_folder)
    ed = Path(exe_dir) if exe_dir else None
    eng = engine_of(game_folder, ed, exe_name)
    if eng["engine"] == "unreal" and eng.get("binaries") and (not ed or Path(eng["binaries"]) != ed):
        ue = ue_binaries(game_folder)
        if ue:
            ed, exe_name = ue
    exe = ed / exe_name if ed and exe_name else None
    info = pe_info(exe) if exe and exe.is_file() else {"arch": None, "imports": []}
    apis = apis_from_imports(info["imports"])
    hints = set()
    if exe and exe.is_file() and (not apis or apis == ["DXGI"] or eng["engine"] != "other"):
        hints = _strings_hint(exe)
    allapis = [a for a in API_ORDER if a in set(apis) | hints]
    api = allapis[0] if allapis else None
    if eng["engine"] == "unreal":
        # Unreal compiles every RHI in; Vulkan is opt-in (-vulkan) on Windows. A D3D12 Agility SDK folder next to
        # the exe means DX12; otherwise UE5 defaults to DX12 and UE4 to DX11.
        agility = bool(ed and ((ed / "D3D12").is_dir() or any((ed / n).exists() for n in ("D3D12Core.dll",))))
        if agility and "DX12" not in allapis:
            allapis.insert(0, "DX12")
        if agility or (eng["version"] == "5" and "DX12" in allapis):
            api = "DX12"
        elif "DX11" in allapis:
            api = "DX11"
        elif "DX12" in allapis:
            api = "DX12"
    elif eng["engine"] == "unity":
        api = "DX11" if ("DX11" in allapis or not allapis) else api   # Unity's Windows default
    return {"arch": info["arch"], "imports": info["imports"][:40], "apis": allapis, "api": api, **eng}


def running_api(pid: int) -> str | None:
    """The graphics API a running Proton game loaded, from the DLLs mapped into its process."""
    try:
        maps = Path(f"/proc/{pid}/maps").read_text(errors="replace").lower()
    except OSError:
        return None
    have = set(re.findall(r"[/\\](d3d12|d3d11|d3d10|d3d9|vulkan-1|opengl32)\.dll", maps))
    for dll, api in (("d3d12", "DX12"), ("d3d11", "DX11"), ("d3d10", "DX10"), ("d3d9", "DX9"),
                     ("vulkan-1", "Vulkan"), ("opengl32", "OpenGL")):
        if dll in have:
            return api
    return None
