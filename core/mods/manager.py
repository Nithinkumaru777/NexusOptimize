"""Archive mods with ON/OFF toggles (SPEC 7.1, 7.2) - file-based only.

install() unpacks an archive into Staging/<game>/<mod-id>/ (never straight into
the game), then routes each file by type:
  * .pak/.utoc/.ucas/.sig      -> <Project>/Content/Paks/~mods   (Unreal)
  * .ini/.cfg matching a file  -> the game's Saved/Config folder (replaces it)
    already in that folder
  * ReShade preset .ini        -> next to ReShade.ini
  * .rpkg                      -> <install>/Runtime            (Glacier)
  * anything else, "generic"   -> mirrored into the install root, following the
    profile games only            archive's own folder layout (a lone wrapper
                                  folder like "MyMod v1.2/" is stripped when its
                                  contents match folders the game already has)
Code (.dll/.exe/.asi/...) is never placed, and nothing is guessed: anything
unrouted is listed as skipped. Anti-cheat games get config files only.

ON copies staged files into the game (backing up anything overwritten), OFF
removes them and restores the backups, so the game folder goes back
byte-for-byte. Never touches a game process or its memory.
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile

from core.mods import live
from core.safety.backup import backup

_DATA = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize")
STATE_ROOT = os.path.join(_DATA, "archive-mods")
STAGING_ROOT = os.path.join(_DATA, "staging")
MAX_UNPACKED_BYTES = 8 * 1024 ** 3   # zip-bomb guard
MAX_FILES = 20000
CODE_EXT = {".dll", ".exe", ".asi", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".com", ".scr", ".msi"}
CONFIG_EXT = {".ini", ".cfg"}
GLACIER_EXT = {".rpkg"}
DOC_EXT = {".txt", ".md", ".pdf", ".rtf", ".nfo", ".url", ".htm", ".html", ".jpg", ".jpeg", ".png"}


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def _long(path):
    """Extended-length form (\\\\?\\) on Windows so deeply nested archives get past MAX_PATH."""
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path


# ---------------------------------------------------------------- manifest
def _manifest_path(game_id):
    return os.path.join(STATE_ROOT, f"{_slug(game_id)}.json")


def mods(game_id):
    try:
        with open(_manifest_path(game_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(game_id, items):
    os.makedirs(STATE_ROOT, exist_ok=True)
    with open(_manifest_path(game_id), "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)


# ---------------------------------------------------------------- unpacking
def _seven_zip():
    for p in (shutil.which("7z"), os.path.expandvars(r"%ProgramFiles%\7-Zip\7z.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\7-Zip\7z.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def _unsafe(name):
    rel = os.path.normpath(name.replace("\\", "/"))
    return (rel.startswith(("..", "\\", "/")) or os.path.isabs(rel)
            or os.path.splitdrive(rel)[0] or ":" in rel)


def _unzip(archive, dest):
    try:
        z = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError):
        raise ValueError(f"{os.path.basename(archive)} is not a valid zip archive.")
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        bad = next((i.filename for i in infos if _unsafe(i.filename)), None)
        if bad:
            raise ValueError(f"Refused: the archive contains an unsafe path ({bad}).")
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise ValueError("Refused: the archive is too large when unpacked.")
        try:
            for i in infos:
                out = os.path.join(dest, os.path.normpath(i.filename.replace("\\", "/")))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with z.open(i) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # Corrupt data, Deflate64 / other unsupported methods, encrypted members.
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as e:
            raise ValueError(f"Could not unpack {os.path.basename(archive)}: {e}")


def _un7z(archive, dest):
    exe = _seven_zip()
    if not exe:
        raise ValueError("Install 7-Zip to unpack .7z/.rar archives (or pick a .zip).")
    r = subprocess.run([exe, "x", archive, f"-o{dest}", "-y", "-snl-", "-bso0", "-bsp0"],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise ValueError(f"7-Zip could not unpack the archive: {r.stderr.strip()[:200]}")
    total = 0
    for base, dirs, files in os.walk(dest):
        for n in dirs + files:
            p = os.path.join(base, n)
            if os.path.islink(p) or os.path.isjunction(p) or not live._inside(os.path.realpath(p), os.path.realpath(dest)):
                raise ValueError(f"Refused: the archive contains an unsafe path ({n}).")
            total += os.path.getsize(p) if n in files else 0
    if total > MAX_UNPACKED_BYTES:
        raise ValueError("Refused: the archive is too large when unpacked.")


def _extract(archive, dest):
    ext = os.path.splitext(archive)[1].lower()
    if ext == ".zip":
        _unzip(archive, dest)
    elif ext in (".7z", ".rar"):
        _un7z(archive, dest)
    else:
        raise ValueError("Unsupported archive - pick a .zip, .7z or .rar.")


# ---------------------------------------------------------------- routing
def _is_reshade_preset(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return any(line.strip().lower().startswith("techniques=") for line in f)
    except OSError:
        return False


def _runtime_dir(root):
    """Glacier keeps its .rpkg packages in <install>/Runtime (or one level down)."""
    hits = [e.path for e in live._walk(root, 1) if e.is_dir() and e.name.lower() == "runtime"]
    return min(hits, key=len) if hits else None


def _wrapper(rels, root):
    """'MyMod v1.2/' if every file sits in that one folder, the game has no folder by
    that name, and what is inside it matches the game's root - else ''."""
    tops = {r.split("/", 1)[0] for r in rels}
    if len(tops) != 1 or not all("/" in r for r in rels):
        return ""
    top = tops.pop()
    if os.path.exists(os.path.join(root, top)):
        return ""
    inner = {r.split("/", 2)[1] for r in rels}
    return top + "/" if any(os.path.exists(os.path.join(root, n)) for n in inner) else ""


def _route_by_type(spec, staged, ac):
    """(target, None) or (None, reason) for one staged file."""
    name = os.path.basename(staged)
    ext = os.path.splitext(name)[1].lower()
    if ext in CODE_EXT:
        return None, "code file - never installed (file-based mods only)"
    if ext in GLACIER_EXT:
        if ac:
            return None, f"rpkg mods blocked - this game uses {ac}"
        root = spec["install_path"]
        dest = _runtime_dir(root) or (root if spec.get("engine") == "Glacier" else None)
        return (os.path.join(dest, name), None) if dest else (None, "needs a Glacier game with a Runtime folder")
    if ext in live.PAK_EXT:
        if ac:
            return None, f"pak mods blocked - this game uses {ac}"
        dest = live.pak_dir(spec) if spec.get("profile") == "unreal" else None
        return (os.path.join(dest, name), None) if dest else (None, "needs an Unreal game with a Content/Paks folder")
    if ext in CONFIG_EXT:
        cfg_dir = os.path.dirname(spec["gus_path"]) if spec.get("gus_path") else None
        if cfg_dir and os.path.isfile(os.path.join(cfg_dir, name)):
            return os.path.join(cfg_dir, name), None
        if _is_reshade_preset(staged):
            if ac:
                return None, f"ReShade blocked - this game uses {ac}"
            folder = os.path.join(spec["install_path"], spec.get("reshade_dir") or "")
            if name.lower() != "reshade.ini" and os.path.isfile(os.path.join(folder, "ReShade.ini")):
                return os.path.join(folder, name), None
            return None, "ReShade preset, but ReShade is not installed for this game"
        return None, "config file with no matching file in the game - not guessed"
    return None, "not a game file (docs/images/unknown type)"


def _route(spec, staged, rel, ac):
    """_route_by_type, then - for "generic" games only - the archive's own layout
    under the install root. `rel` is the file's path in the archive, wrapper stripped."""
    target, reason = _route_by_type(spec, staged, ac)
    ext = os.path.splitext(rel)[1].lower()
    if target or ext in CODE_EXT or spec.get("profile") != "generic":
        return target, reason
    if ac:
        return None, reason if ac in reason else f"loose files blocked - this game uses {ac}"
    if "/" not in rel and ext in DOC_EXT:
        return None, "readme/preview - not copied into the game"
    root = os.path.abspath(spec["install_path"])
    target = os.path.normpath(os.path.join(root, *rel.split("/")))
    if not live._inside(target, root):
        return None, "unsafe path - outside the game folder"
    return target, None


# ---------------------------------------------------------------- deploy / purge
def _enable(spec, mod, items):
    owned = {os.path.normcase(f["target"]): m["name"] for m in items if m["enabled"] and m["id"] != mod["id"]
             for f in m["files"]}
    owned.update({os.path.normcase(f["target"]): m["name"] for m in live.installed(spec["id"]) for f in m["files"]})
    clash = next((f["target"] for f in mod["files"] if os.path.normcase(f["target"]) in owned), None)
    if clash:
        return False, f"{os.path.basename(clash)} already belongs to '{owned[os.path.normcase(clash)]}'. Switch that off first."
    placed = []
    try:
        for f in mod["files"]:
            os.makedirs(os.path.dirname(f["target"]), exist_ok=True)
            f["backup"] = backup(f["target"]) if os.path.exists(f["target"]) else None
            shutil.copy2(f["staged"], f["target"])
            placed.append(f)
    except OSError as e:
        live._undo(placed)
        return False, f"Copy failed, nothing changed: {e}"
    mod["enabled"] = True
    _save(spec["id"], items)
    return True, f"{mod['name']} is ON - {len(placed)} file(s) placed, applies on next game launch."


def _disable(spec, mod, items):
    try:
        live._undo(mod["files"])
    except OSError as e:
        return False, f"Switching off failed: {e}"
    for f in mod["files"]:
        f["backup"] = None
    mod["enabled"] = False
    _save(spec["id"], items)
    return True, f"{mod['name']} is OFF - its files were removed and originals restored."


# ---------------------------------------------------------------- public API
def install(spec, archive, running=False, source=None):
    """Unpack, route and (unless the game runs) switch on. Returns (ok, message, mod)."""
    name = os.path.splitext(os.path.basename(archive))[0]
    mod_id = uuid.uuid4().hex[:12]   # short: the archive name can eat the whole MAX_PATH budget
    staging = _long(os.path.join(STAGING_ROOT, _slug(spec["id"]), mod_id))
    try:
        _extract(archive, staging)
    except (ValueError, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        return False, str(e), None

    ac = live.anticheat(spec["install_path"])
    staged_files = [os.path.join(base, n) for base, _, names in os.walk(staging) for n in sorted(names)]
    rels = [os.path.relpath(s, staging).replace("\\", "/") for s in staged_files]
    wrap = _wrapper(rels, spec["install_path"])
    files, skipped, taken = [], [], set()
    for staged, rel in zip(staged_files, rels):
        target, reason = _route(spec, staged, rel[len(wrap):], ac)
        if target and os.path.normcase(target) in taken:
            target, reason = None, "duplicate - another file in this archive already goes there"
        if target:
            taken.add(os.path.normcase(target))
            files.append({"rel": rel, "staged": staged, "target": target, "backup": None})
        else:
            skipped.append({"file": rel, "reason": reason})
    if not files:
        shutil.rmtree(staging, ignore_errors=True)
        why = "; ".join(sorted({s["reason"] for s in skipped})) or "empty archive"
        return False, f"Nothing in this archive can be placed for this game ({why}).", None

    items = mods(spec["id"])
    mod = {"id": mod_id, "name": name, "source": source, "enabled": False, "installed": time.time(),
           "staging": staging, "files": files, "skipped": skipped}
    items.append(mod)
    _save(spec["id"], items)
    if running:
        return True, f"{name} installed but left OFF. Close the game, then switch it on.", mod
    ok, msg = _enable(spec, mod, items)
    return True, (msg if ok else f"{name} installed but left OFF - {msg}"), mod


def set_enabled(spec, mod_id, on, running=False):
    items = mods(spec["id"])
    mod = next((m for m in items if m["id"] == mod_id), None)
    if not mod:
        return False, "Unknown mod."
    if mod["enabled"] == bool(on):
        return True, f"{mod['name']} is already {'ON' if on else 'OFF'}."
    if running:
        return False, "Close the game first - its files are in use."
    return (_enable if on else _disable)(spec, mod, items)


def uninstall(spec, mod_id, running=False):
    items = mods(spec["id"])
    mod = next((m for m in items if m["id"] == mod_id), None)
    if not mod:
        return False, "Unknown mod."
    if mod["enabled"]:
        ok, msg = set_enabled(spec, mod_id, False, running)
        if not ok:
            return False, msg
    if live._inside(_long(mod["staging"]), _long(STAGING_ROOT)):
        shutil.rmtree(mod["staging"], ignore_errors=True)
    _save(spec["id"], [m for m in mods(spec["id"]) if m["id"] != mod_id])
    return True, f"{mod['name']} uninstalled, originals restored."
