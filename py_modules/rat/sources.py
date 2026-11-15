"""Where packages come from: official GitHub releases, pasted links (forks) and local archives (drive / USB).
Downloads are verified (GitHub's asset digest, a .sha256 / SHA256SUMS file when the release has one) and extracted
safely into RAT's package store."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import ssl
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .util import Paths, UserError, clean_env, is_within, log, read_json, run_streaming, sha256_of, which, write_json

PKG_META = ".rat-package.json"
# the package store's metadata file kept its earlier name until 1.0: still read (and renamed) so downloaded and already
# prepared packages (HelixSR's built network lives inside its package folder) are never lost or re-downloaded
OLD_PKG_META = ".ratswapper-package.json"
META_NAMES = (PKG_META, OLD_PKG_META)
UA = "RAT-Decky/1.0 (RAT - Radical Advanced Tools)"
ARCHIVE_EXT = (".zip", ".7z", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar")
BAD_ASSET = re.compile(r"(?i)(\.exe|\.msi|\.deb|\.rpm|\.appimage|\.dmg|\.pkg|-asi\.zip|\.sha256|sha256sums|\.sig|\.asc)$")
CACHE_TTL = 30 * 60


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # bundled with Decky's Python
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for ca in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
        if os.path.exists(ca):
            return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


def _open(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"
                                               if "api.github.com" in url else "*/*"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def get_text(url: str, limit: int = 8 << 20) -> str:
    """A small text resource (an index, a wiki page), with the same curl fallback as get_json."""
    try:
        with _open(url) as r:
            return r.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UserError(f"Not found: {url}") from e
        raise UserError(f"Could not reach {urllib.parse.urlparse(url).netloc}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        curl = which("curl")
        if curl:
            import subprocess
            r = subprocess.run([curl, "-fsSL", "-A", UA, url], capture_output=True, env=clean_env(), timeout=60)
            if r.returncode == 0:
                return r.stdout[:limit].decode("utf-8", "replace")
        raise UserError(f"No connection to {urllib.parse.urlparse(url).netloc}. Check the network.") from e


def cached_text(name: str, url: str, ttl: float = 6 * 3600, force: bool = False) -> str:
    """get_text with a file cache in RAT's runtime folder (indexes that change a few times a day)."""
    f = Paths.data("cache") / name
    try:
        if not force and f.exists() and time.time() - f.stat().st_mtime < ttl:
            return f.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        text = get_text(url)
    except UserError:
        if f.exists():   # offline: an old copy beats nothing
            return f.read_text(encoding="utf-8")
        raise
    f.write_text(text, encoding="utf-8")
    return text


def get_json(url: str) -> object:
    try:
        with _open(url) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403 and "api.github.com" in url:
            raise UserError("GitHub's rate limit was reached (60 requests per hour without an account). "
                            "Try again in a while.") from e
        if e.code == 404:
            raise UserError(f"Not found: {url}") from e
        raise UserError(f"Could not reach {urllib.parse.urlparse(url).netloc}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Decky's Python can lack working certificates on some systems: fall back to curl
        curl = which("curl")
        if curl:
            import subprocess
            r = subprocess.run([curl, "-fsSL", "-A", UA, "-H", "Accept: application/vnd.github+json", url],
                               capture_output=True, env=clean_env(), timeout=40)
            if r.returncode == 0:
                return json.loads(r.stdout.decode("utf-8"))
        raise UserError(f"No connection to {urllib.parse.urlparse(url).netloc}. Check the network.") from e


# --- GitHub releases ------------------------------------------------------------------------------------------------

def _cache_file() -> Path:
    return Paths.runtime_dir / "release-cache.json"


def releases(repo: str, force: bool = False) -> list[dict]:
    cache = read_json(_cache_file(), {})
    hit = cache.get(repo)
    if hit and not force and time.time() - hit.get("t", 0) < CACHE_TTL:
        return hit["data"]
    data = get_json(f"https://api.github.com/repos/{repo}/releases?per_page=15")
    if not isinstance(data, list):
        raise UserError(f"Unexpected answer from GitHub for {repo}")
    slim = []
    for r in data:
        slim.append({
            "tag": r.get("tag_name"), "name": r.get("name") or r.get("tag_name"), "draft": r.get("draft"),
            "prerelease": r.get("prerelease"), "published": r.get("published_at"), "url": r.get("html_url"),
            "body": (r.get("body") or "")[:1500],
            "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size"),
                        "digest": a.get("digest")} for a in r.get("assets") or []],
        })
    cache[repo] = {"t": time.time(), "data": slim}
    write_json(_cache_file(), cache)
    return slim


def release_by_tag(repo: str, tag: str) -> dict:
    """One release by its tag (for builds older than the cached release list)."""
    r = get_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    if not isinstance(r, dict) or not r.get("tag_name"):
        raise UserError(f"{repo} has no release {tag}")
    return {"tag": r.get("tag_name"), "name": r.get("name") or r.get("tag_name"), "draft": r.get("draft"),
            "prerelease": r.get("prerelease"), "published": r.get("published_at"), "url": r.get("html_url"),
            "body": (r.get("body") or "")[:1500],
            "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size"),
                        "digest": a.get("digest")} for a in r.get("assets") or []]}


def pick_asset(assets: list[dict], pattern: str | None, fallback: str | None = None,
               exclude: str | None = None) -> dict | None:
    """The release file to download: the add-on's own pattern first, then any Linux-usable archive. Windows
    installers, Debian packages, AppImages and checksum files are never picked."""
    def ok(a):
        n = a.get("name") or ""
        if BAD_ASSET.search(n) or (exclude and re.search(exclude, n)):
            return False
        return n.lower().endswith(ARCHIVE_EXT)
    usable = [a for a in assets if ok(a)]
    for pat in (pattern, fallback):
        if pat:
            m = [a for a in usable if re.search(pat, a["name"])]
            if m:
                return sorted(m, key=lambda a: ("linux" not in a["name"].lower(), a["name"]))[0]
    if usable:
        return sorted(usable, key=lambda a: ("linux" not in a["name"].lower(), -(a.get("size") or 0)))[0]
    return None


def major_of(version: str | None) -> int | None:
    """The major version number of a release tag ("v1.0.3" -> 1), None when the tag has no number."""
    m = re.search(r"(\d+)(?:\.\d+)*", version or "")
    return int(m.group(1)) if m else None


def above_major(version: str | None, max_major: int | None) -> bool:
    """True when a version is newer than the major line an add-on is pinned to."""
    mj = major_of(version)
    return max_major is not None and mj is not None and mj > max_major


def latest(repo: str, pattern: str | None = None, fallback: str | None = None, exclude: str | None = None,
           prerelease: bool = False, force: bool = False, tag: str | None = None, max_major: int | None = None) -> dict:
    for r in releases(repo, force):
        if r["draft"] or (r["prerelease"] and not prerelease and not tag):
            continue
        if tag and r["tag"] != tag:
            continue
        if not tag and above_major(r["tag"], max_major):
            continue
        a = pick_asset(r["assets"], pattern, fallback, exclude)
        if a:
            return {"repo": repo, "tag": r["tag"], "name": r["name"], "published": r["published"],
                    "page": r["url"], "notes": r["body"], "asset": a, "assets": r["assets"]}
    if tag:   # older than the cached release list: ask GitHub for that tag directly
        try:
            r = release_by_tag(repo, tag)
        except UserError:
            r = None
        a = pick_asset(r["assets"], pattern, fallback, exclude) if r and not r.get("draft") else None
        if r and a:
            return {"repo": repo, "tag": r["tag"], "name": r["name"], "published": r["published"],
                    "page": r["url"], "notes": r["body"], "asset": a, "assets": r["assets"]}
    raise UserError(f"No Linux-compatible release archive found in {repo}" + (f" ({tag})" if tag else "") +
                    (f" (v{max_major}.x)" if max_major is not None and not tag else ""))


def expected_sha256(rel: dict) -> str | None:
    """The asset's SHA-256 from GitHub's digest field or a checksum file published with the release."""
    a = rel["asset"]
    d = a.get("digest") or ""
    if d.startswith("sha256:"):
        return d.split(":", 1)[1].lower()
    for cand in rel.get("assets", []):
        n = (cand.get("name") or "").lower()
        if n in (a["name"].lower() + ".sha256", "sha256sums.txt", "sha256sums", "checksums.txt"):
            try:
                with _open(cand["url"]) as r:
                    text = r.read(1 << 16).decode("utf-8", "replace")
            except Exception:
                continue
            for line in text.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if len(parts) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                    return parts[0].lower()
                # GNU format: "<hash>  name" / "<hash> *name" / "<hash>  ./name"
                if re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) and _sum_name(parts[-1]) == a["name"]:
                    return parts[0].lower()
                # BSD format: "SHA256 (name) = <hash>"
                m = re.fullmatch(r"SHA256 \((.+)\) = ([0-9a-fA-F]{64})", line.strip())
                if m and _sum_name(m.group(1)) == a["name"]:
                    return m.group(2).lower()
            log.warning("checksum file %s has no entry for %s", cand.get("name"), a["name"])
    return None


def _sum_name(n: str) -> str:
    n = n.lstrip("*")
    return n[2:] if n.startswith("./") else n


GITHUB_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)(?:/(.*))?$", re.I)


def resolve_link(url: str, prerelease: bool = False) -> dict:
    """A pasted link -> {kind: 'github'|'direct', ...}. GitHub repository, releases or tag links resolve to the
    release archive; any other https link must point at an archive."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise UserError("Paste a full link starting with https://")
    m = GITHUB_URL.match(url)
    if m:
        owner, repo, rest = m.group(1), re.sub(r"\.git$", "", m.group(2)), m.group(3) or ""
        full = f"{owner}/{repo}"
        if rest.startswith("releases/download/"):
            name = urllib.parse.unquote(rest.rsplit("/", 1)[-1])
            tag = rest.split("/")[2]
            return {"kind": "direct", "url": url, "name": name, "tag": tag, "repo": full}
        tag = None
        tm = re.match(r"releases/tag/([^/?#]+)", rest)
        if tm:
            tag = urllib.parse.unquote(tm.group(1))
        rel = latest(full, prerelease=prerelease, tag=tag, force=True)
        return {"kind": "github", "repo": full, "release": rel}
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]) or "download"
    if not name.lower().endswith(ARCHIVE_EXT):
        raise UserError("The link must point to a GitHub repository/release or to a .zip, .7z or .tar archive.")
    return {"kind": "direct", "url": url, "name": name}


# --- download and extract -------------------------------------------------------------------------------------------

def download(url: str, dest: Path, progress: Callable[[float, str], None] | None = None,
             should_cancel: Callable[[], bool] | None = None, size_hint: int | None = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with _open(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) or (size_hint or 0)
            got = 0
            last = 0.0
            while True:
                if should_cancel and should_cancel():
                    raise UserError("Cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                now = time.monotonic()
                if progress and now - last > 0.25:
                    last = now
                    frac = got / total if total else -1
                    progress(frac, f"{got >> 20} / {total >> 20} MB" if total else f"{got >> 20} MB")
    except UserError:   # cancelled: no half-written file left behind
        tmp.unlink(missing_ok=True)
        raise
    except (OSError, http.client.HTTPException) as e:   # URLError, timeouts, TLS, connection reset, short reads
        tmp.unlink(missing_ok=True)
        curl = which("curl")
        if not curl:
            raise UserError(f"Download failed: {e}") from e
        log.warning("urllib download failed (%s), retrying with curl", e)
        try:
            code, tail = run_streaming([curl, "-fL", "--retry", "3", "-A", UA, "-o", str(tmp), url],
                                       should_cancel=should_cancel, timeout=3600)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if code != 0:
            tmp.unlink(missing_ok=True)
            raise UserError("Download failed: " + (tail[-1] if tail else f"curl exit {code}"))
    os.replace(tmp, dest)
    return dest


def _safe_target(root: Path, name: str) -> Path:
    name = name.replace("\\", "/")
    target = (root / name).resolve()
    if not str(target).startswith(str(root.resolve()) + os.sep) and target != root.resolve():
        raise UserError(f"Unsafe path in archive: {name}")
    return target


def seven_zip() -> str | None:
    bundled = Paths.bin_dir() / "7zz"
    if bundled.exists():
        try:
            bundled.chmod(0o755)
        except OSError:
            pass
        return str(bundled)
    for n in ("7zz", "7z", "7za"):
        p = which(n)
        if p:
            return p
    return None


def extract(archive: Path, dest: Path, should_cancel: Callable[[], bool] | None = None) -> Path:
    """Extracts into dest (emptied first). A single top-level folder is flattened away."""
    archive, dest = Path(archive), Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    low = archive.name.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if should_cancel and should_cancel():
                    raise UserError("Cancelled")
                target = _safe_target(dest, info.filename)
                # archives made on Windows can use backslashes, including for folder entries ("Licenses\\")
                if info.is_dir() or info.filename.endswith("\\"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o111:
                    target.chmod(0o755)
    elif low.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar")):
        with tarfile.open(archive) as t:
            members = []
            for m in t.getmembers():
                _safe_target(dest, m.name)
                if m.issym() or m.islnk():
                    continue
                members.append(m)
            try:
                t.extractall(dest, members=members, filter="data")
            except TypeError:
                t.extractall(dest, members=members)
    elif low.endswith(".7z"):
        tool = seven_zip()
        if not tool:
            raise UserError("7-Zip is needed for .7z archives and was not found (bin/7zz is missing from the plugin).")
        code, tail = run_streaming([tool, "x", "-y", "-bso0", "-bsp0", f"-o{dest}", str(archive)],
                                   should_cancel=should_cancel, timeout=1800)
        if code != 0:
            raise UserError("Could not extract the archive: " + (tail[-1] if tail else f"7-Zip exit {code}"))
        for p in dest.rglob("*"):   # 7-Zip does not refuse '..' paths or links pointing out by itself
            if p.is_symlink() and not is_within(p, dest):
                raise UserError("Unsafe link in archive")
            if not is_within(p, dest):
                raise UserError("Unsafe path in archive")
    else:
        raise UserError(f"Unsupported archive type: {archive.name}")
    entries = [e for e in dest.iterdir() if not e.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        tmp = dest.with_name(dest.name + ".flat")
        if tmp.exists():
            shutil.rmtree(tmp)
        inner.rename(tmp)
        dest.rmdir()
        tmp.rename(dest)
    return dest


def detect_kind(root: Path) -> str | None:
    """What a package is, from its files: 'd4r', 'helixsr' or 'optiscaler'."""
    root = Path(root)
    if (root / "d4r" / "nvngx.dll").exists() or (root / "d4r" / "d4r.ini").exists():
        return "d4r"
    if (root / "setup" / "helixsr_install.py").exists() or (root / "helixsr-setup.sh").exists():
        return "helixsr"
    if any((root / n).exists() for n in ("OptiScaler.dll", "OptiScaler.asi")):
        return "optiscaler"
    return None


def package_root(extracted: Path) -> Path:
    """The folder inside an extracted archive that holds the package (some forks nest it one or two levels)."""
    extracted = Path(extracted)
    if detect_kind(extracted):
        return extracted
    for d in sorted(p for p in extracted.rglob("*") if p.is_dir() and len(p.relative_to(extracted).parts) <= 2):
        if detect_kind(d):
            return d
    return extracted


def store_package(archive: Path, meta: dict, should_cancel: Callable[[], bool] | None = None) -> dict:
    """Extracts a downloaded or local archive into the package store and records its metadata."""
    pkg_id = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{meta['source_id']}-{meta['version']}")
    dest = Paths.packages() / pkg_id
    have = _meta_of(dest) if dest.is_dir() else None
    if have and have.get("archive_sha256") == sha256_of(archive) and Path(have.get("path", "")).is_dir():
        # the same archive is stored already: keep that folder (and anything built in it, like HelixSR's network)
        return have
    staging = Paths.packages() / (pkg_id + ".new")
    extract(archive, staging, should_cancel)
    root = package_root(staging)
    kind = meta.get("kind") or detect_kind(root)
    if not kind:
        shutil.rmtree(staging, ignore_errors=True)
        raise UserError("This archive does not look like d4r, HelixSR or an OptiScaler build (no OptiScaler.dll, d4r "
                        "folder or HelixSR setup found).")
    if root != staging:
        rel = root.relative_to(staging)
        meta["subdir"] = str(rel)
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)
    meta = dict(meta, id=pkg_id, kind=kind, path=str(dest / meta.get("subdir", "")) if meta.get("subdir") else str(dest),
                archive_sha256=sha256_of(archive), stored=time.time())
    write_json(dest / PKG_META, meta)
    return meta


def _meta_of(d: Path) -> dict | None:
    """A stored package's metadata."""
    m = read_json(d / PKG_META, None)
    if m is None and (d / OLD_PKG_META).is_file():
        m = read_json(d / OLD_PKG_META, None)
        if m:
            write_json(d / PKG_META, m)
            (d / OLD_PKG_META).unlink(missing_ok=True)
    if m and not Path(m.get("path", "")).is_dir() and d.is_dir():
        # the package store was moved (e.g. a new Decky data folder): paths follow the folder
        m["path"] = str(d / m["subdir"]) if m.get("subdir") else str(d)
    return m


def list_packages() -> list[dict]:
    out = []
    for d in sorted(Paths.packages().iterdir()):
        if d.is_dir() and not d.name.endswith(".new"):
            m = _meta_of(d)
            if m:
                out.append(m)
    return out


def get_package(pkg_id: str) -> dict | None:
    return _meta_of(Paths.packages() / pkg_id)


def save_package_meta(meta: dict) -> None:
    write_json(Paths.packages() / meta["id"] / PKG_META, meta)


def remove_package(pkg_id: str) -> None:
    d = Paths.packages() / pkg_id
    if d.exists() and d.parent == Paths.packages():
        shutil.rmtree(d)
