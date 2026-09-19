"""Live mod applier - file-based only (SPEC 7.2, 7.4).

Three kinds, all plain file copies with a backup of anything overwritten:
  * pak     - .pak/.utoc/.ucas into <Project>/Content/Paks/~mods (Unreal).
              UE mounts paks at startup, so these apply on next launch.
  * reshade - a preset .ini into the game's ReShade folder. Selectable live
              from ReShade's own preset list; with the game closed it also
              becomes the startup preset (PresetPath).
  * config  - replace one config file inside the install or the game's
              Saved/Config folder. Most games read these at startup.

Never touches a game process or its memory. Anti-cheat games get config
swaps only (SPEC 2.3). A per-game manifest records every placed file so
remove() restores the folder byte-for-byte.
"""

import json
import os
import re
import shutil
import time

from core.optimize import ini
from core.safety.backup import backup

STATE_ROOT = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize", "mods")

KINDS = {"pak", "reshade", "config"}
PAK_EXT = {".pak", ".utoc", ".ucas", ".sig"}
CONFIG_EXT = {".ini", ".cfg", ".json", ".xml", ".txt"}
_ANTICHEAT = re.compile(r"easyanticheat|battleye|\bbeservice|vanguard|nprotect|xigncode", re.I)


# ---------------------------------------------------------------- detection
def _walk(root, max_depth):
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for e in entries:
            yield e
            if depth < max_depth and e.is_dir(follow_symlinks=False):
                stack.append((e.path, depth + 1))


def anticheat(root):
    """Name of an anti-cheat found near the install root, else None."""
    hit = next((e.name for e in _walk(root, 1) if _ANTICHEAT.search(e.name)), None)
    return hit and os.path.splitext(hit)[0]


def pak_dir(spec):
    """<install>/<Project>/Content/Paks/~mods, or None."""
    root = spec["install_path"]
    for e in _walk(root, 1):
        paks = os.path.join(e.path, "Content", "Paks")
        if e.is_dir() and e.name.lower() != "engine" and os.path.isdir(paks):
            return os.path.join(paks, "~mods")
    return None


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:   # different drives
        return False


# ---------------------------------------------------------------- manifest
def _manifest_path(game_id):
    return os.path.join(STATE_ROOT, f"{re.sub(r'[^A-Za-z0-9_.-]', '_', game_id)}.json")


def installed(game_id):
    try:
        with open(_manifest_path(game_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(game_id, mods):
    os.makedirs(STATE_ROOT, exist_ok=True)
    with open(_manifest_path(game_id), "w", encoding="utf-8") as f:
        json.dump(mods, f, indent=1)


def _undo(files):
    for f in reversed(files):
        if f["backup"]:
            shutil.copy2(f["backup"], f["target"])
        elif os.path.exists(f["target"]):
            os.remove(f["target"])


# ---------------------------------------------------------------- apply / remove
def _plan(spec, kind, sources, target, running):
    """Validates a request. Returns (error, [(src, dest)], reshade_ini)."""
    root = spec["install_path"]
    exts = [os.path.splitext(s)[1].lower() for s in sources]
    if not sources or not all(os.path.isfile(s) for s in sources):
        return "Pick at least one existing file.", None, None
    ac = anticheat(root) if kind != "config" else None
    if ac:
        return f"Locked - this game uses {ac}. Only config swaps are allowed here.", None, None

    if kind == "pak":
        dest = pak_dir(spec) if spec.get("profile") == "unreal" else None
        if not dest:
            return "Pak mods need an Unreal Engine game with a Content/Paks folder.", None, None
        if ".pak" not in exts or not set(exts) <= PAK_EXT:
            return "Pak mods need a .pak file (plus its .utoc/.ucas if it has them).", None, None
        if running:
            return "Close the game first - Unreal loads paks at startup and locks them.", None, None
        return None, [(s, os.path.join(dest, os.path.basename(s))) for s in sources], None

    if kind == "reshade":
        folder = os.path.join(root, spec.get("reshade_dir") or "")
        reshade_ini = os.path.join(folder, "ReShade.ini")
        if not os.path.exists(reshade_ini):
            return "ReShade is not installed for this game (no ReShade.ini found).", None, None
        name = os.path.basename(sources[0])
        if len(sources) != 1 or exts[0] != ".ini" or name.lower() == "reshade.ini":
            return "Pick one ReShade preset .ini.", None, None
        return None, [(sources[0], os.path.join(folder, name))], None if running else reshade_ini

    # config
    if running:
        return "Close the game first - games rewrite their config files on exit.", None, None
    roots = [root] + ([os.path.dirname(os.path.dirname(spec["gus_path"]))] if spec.get("gus_path") else [])
    target = os.path.abspath(target) if target else None
    if not target or not any(_inside(target, r) for r in roots):
        return "The target must be inside the game folder or its Saved/Config folder.", None, None
    if len(sources) != 1 or exts[0] not in CONFIG_EXT or os.path.splitext(target)[1].lower() != exts[0]:
        return "Pick one config file with the same extension as the target.", None, None
    return None, [(sources[0], target)], None


def apply(spec, kind, sources, target=None, running=False):
    """Returns (ok, message, mod or None)."""
    if kind not in KINDS:
        return False, f"Unknown mod kind '{kind}'.", None
    error, copies, reshade_ini = _plan(spec, kind, list(sources), target, running)
    if error:
        return False, error, None

    mods = installed(spec["id"])
    owned = {os.path.normcase(f["target"]): m["name"] for m in mods for f in m["files"]}
    clash = next((d for _, d in copies if os.path.normcase(d) in owned), None)
    if clash:
        return False, f"{os.path.basename(clash)} already belongs to '{owned[os.path.normcase(clash)]}'. Remove it first.", None

    files = []
    try:
        for src, dest in copies:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            entry = {"target": dest, "backup": backup(dest) if os.path.exists(dest) else None}
            shutil.copy2(src, dest)
            files.append(entry)
    except OSError as e:
        _undo(files)
        return False, f"Copy failed, nothing changed: {e}", None

    mod = {"id": f"{kind}-{time.time_ns()}", "kind": kind, "name": os.path.basename(copies[0][0]),
           "files": files, "reshade_preset_was": None, "applied": time.time()}
    note = {"pak": "applies on next launch", "config": "applies on next launch",
            "reshade": "select it in ReShade's preset list (Home key)"}[kind]
    if reshade_ini:
        ok, old = ini.set_reshade_preset(reshade_ini, ".\\" + mod["name"])
        mod["reshade_preset_was"] = {"ini": reshade_ini, "value": old} if ok else None
        note = "set as ReShade's startup preset"
    _save(spec["id"], mods + [mod])
    return True, f"{mod['name']} applied - {note}. Originals backed up.", mod


def remove(spec, mod_id, running=False):
    """Deletes placed files and restores anything they overwrote."""
    mods = installed(spec["id"])
    mod = next((m for m in mods if m["id"] == mod_id), None)
    if not mod:
        return False, "Unknown mod."
    if running:
        return False, "Close the game first - its files are in use."
    try:
        _undo(mod["files"])
        was = mod.get("reshade_preset_was")
        if was and was["value"] is not None:
            ini.set_reshade_preset(was["ini"], was["value"])
    except OSError as e:
        return False, f"Remove failed: {e}"
    _save(spec["id"], [m for m in mods if m["id"] != mod_id])
    return True, f"{mod['name']} removed, originals restored."
