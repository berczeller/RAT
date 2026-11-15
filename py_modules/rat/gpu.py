"""Graphics card detection for AMD, NVIDIA and Intel: vendor, architecture family and what it can run natively.

AMD: architecture from the KFD topology (gfx target) or known device ids. NVIDIA and Intel: architecture from PCI device
id ranges, the marketing name from the proprietary driver when it is loaded. The result also says whether the GPU can
run real DLSS (NVIDIA RTX), XeSS on XMX units (Intel Arc) and FSR 4 natively (RDNA4)."""
from __future__ import annotations

import glob
import os
from pathlib import Path

VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}

# AMD device ids for handhelds and APUs that are common under SteamOS, used when the KFD topology is unavailable
AMD_DEVICES = {
    "0x163f": ("Steam Deck (Van Gogh)", "gfx1033"),
    "0x1435": ("Steam Deck OLED (Sephiroth)", "gfx1033"),
    "0x15bf": ("Radeon 780M (Phoenix)", "gfx1103"),
    "0x15c8": ("Radeon 740M (Phoenix2)", "gfx1103"),
    "0x1900": ("Radeon 780M (Hawk Point)", "gfx1103"),
    "0x150e": ("Radeon 890M (Strix Point)", "gfx1150"),
    "0x1586": ("Radeon 8060S (Strix Halo)", "gfx1151"),
    "0x164e": ("Radeon (Raphael)", "gfx1036"),
    "0x1681": ("Radeon 680M (Rembrandt)", "gfx1035"),
}


def family_of(gfx: str | None) -> str:
    """gfx target -> architecture family name."""
    if not gfx or not gfx.startswith("gfx"):
        return "unknown"
    t = gfx[3:]
    if t.startswith("12"):
        return "RDNA4"
    if t.startswith("115"):
        return "RDNA3.5"
    if t.startswith("11"):
        return "RDNA3"
    if t.startswith("103"):
        return "RDNA2"
    if t.startswith("101"):
        return "RDNA1"
    if t.startswith("9"):
        return "GCN5"
    return "older"


def nvidia_family(device: str | None) -> str:
    """PCI device id -> NVIDIA architecture. Ranges are approximate but stable per generation."""
    try:
        d = int(device or "0", 16)
    except ValueError:
        return "NVIDIA"
    if d >= 0x2B00:
        return "Blackwell"
    if d >= 0x2600:
        return "Ada"
    if d >= 0x2200 or 0x2000 <= d < 0x2100:
        return "Ampere"
    if 0x2180 <= d < 0x2200 or 0x1F80 <= d < 0x2000:
        return "Turing-GTX"    # GTX 16 series: no tensor cores
    if 0x1E00 <= d < 0x1F80:
        return "Turing"
    return "Pascal"


def intel_family(device: str | None) -> str:
    try:
        d = int(device or "0", 16)
    except ValueError:
        return "Intel"
    if 0xE200 <= d <= 0xE2FF or 0x6420 <= d <= 0x64FF:
        return "Xe2"            # Battlemage, Lunar Lake
    if 0x5690 <= d <= 0x56BF:
        return "Alchemist"
    if 0x7D00 <= d <= 0x7DFF:
        return "Xe-LPG"         # Meteor Lake / Arrow Lake iGPU
    return "Intel"


DLSS_FAMILIES = {"Turing", "Ampere", "Ada", "Blackwell"}
XMX_FAMILIES = {"Alchemist", "Xe2"}


def capabilities(info: dict) -> dict:
    fam = info.get("family", "unknown")
    return {"dlss": fam in DLSS_FAMILIES, "xmx": fam in XMX_FAMILIES, "fsr4_native": fam == "RDNA4"}


def _gfx_from_version(v: int) -> str | None:
    if v <= 0:
        return None
    major, minor, step = v // 10000, (v // 100) % 100, v % 100
    return f"gfx{major}{minor}{step:x}"


def _kfd_nodes(root: str = "/sys/class/kfd/kfd/topology/nodes") -> list[dict]:
    out = []
    for props in glob.glob(os.path.join(root, "*", "properties")):
        vals: dict[str, int] = {}
        try:
            for line in Path(props).read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("-").isdigit():
                    vals[parts[0]] = int(parts[1])
        except OSError:
            continue
        if vals.get("simd_count", 0) > 0:
            out.append(vals)
    return out


def _read(p: str) -> str:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def detect(sys_root: str = "/") -> dict:
    """Returns {vendor, name, gfx, family, steam_deck, device_id}."""
    def sp(p: str) -> str:
        return os.path.join(sys_root, p.lstrip("/"))

    info = {"vendor": "unknown", "name": "Unknown GPU", "gfx": None, "family": "unknown", "steam_deck": False,
            "device_id": None, "kfd": False}
    board = _read(sp("/sys/devices/virtual/dmi/id/board_vendor"))
    product = _read(sp("/sys/devices/virtual/dmi/id/product_name"))
    if board.lower().startswith("valve") or product in ("Jupiter", "Galileo"):
        info["steam_deck"] = True

    # PCI display devices: prefer a discrete AMD/NVIDIA card over an integrated one
    cards = []
    for dev in sorted(glob.glob(sp("/sys/class/drm/card[0-9]*/device"))):
        vendor = _read(os.path.join(dev, "vendor"))
        device = _read(os.path.join(dev, "device"))
        if not vendor:
            continue
        boot_vga = _read(os.path.join(dev, "boot_vga")) == "1"
        cards.append((vendor, device, boot_vga))
    if cards:
        # a discrete card first: on hybrid laptops the iGPU (Intel, or an AMD APU from AMD_DEVICES) is the boot_vga
        # card, so boot_vga only breaks ties
        def integrated(c) -> bool:
            return c[0] == "0x8086" or (c[0] == "0x1002" and c[1] in AMD_DEVICES)
        several = len(cards) > 1   # read before sorting: CPython empties the list while sort() runs
        cards.sort(key=lambda c: (c[0] not in ("0x1002", "0x10de", "0x8086"), integrated(c) and several, not c[2]))
        vendor, device, _ = cards[0]
        info["vendor"] = VENDORS.get(vendor, vendor)
        info["device_id"] = device
        if vendor == "0x1002" and device in AMD_DEVICES:
            info["name"], info["gfx"] = AMD_DEVICES[device]
        elif info["vendor"] != "unknown":
            info["name"] = f"{info['vendor']} GPU ({device})"

    # KFD lists AMD GPUs only: never let an AMD APU's node override a chosen NVIDIA / Intel card
    nodes = _kfd_nodes(sp("/sys/class/kfd/kfd/topology/nodes")) if info["vendor"] in ("AMD", "unknown") else []
    if nodes:
        best = max(nodes, key=lambda n: n.get("simd_count", 0))
        gfx = _gfx_from_version(best.get("gfx_target_version", 0))
        if gfx:
            info["gfx"] = gfx
            info["kfd"] = True
            if info["vendor"] == "unknown":
                info["vendor"] = "AMD"
            if info["name"].startswith(("Unknown", "AMD GPU")):
                info["name"] = f"AMD Radeon ({gfx})"
    if info["steam_deck"] and not info["gfx"]:
        info["gfx"] = "gfx1033"
        info["name"] = "Steam Deck"
    if info["steam_deck"]:
        info["name"] = "Steam Deck OLED" if product == "Galileo" else "Steam Deck"
    if info["vendor"] == "AMD":
        info["family"] = family_of(info["gfx"])
    elif info["vendor"] == "NVIDIA":
        info["family"] = nvidia_family(info["device_id"])
        model = _nvidia_model(sp)
        info["name"] = model or f"NVIDIA {info['family']} ({info['device_id']})"
    elif info["vendor"] == "Intel":
        info["family"] = intel_family(info["device_id"])
        info["name"] = f"Intel {info['family']} ({info['device_id']})"
    else:
        info["family"] = "unknown"
    info.update(capabilities(info))
    return info


def _nvidia_model(sp) -> str | None:
    for f in sorted(glob.glob(sp("/proc/driver/nvidia/gpus/*/information"))):
        for line in _read(f).splitlines():
            if line.lower().startswith("model:"):
                return line.split(":", 1)[1].strip() or None
    return None
