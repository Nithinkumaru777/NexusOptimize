"""Save-game snapshots: zip a game's save folders when it exits, restore with one click (SPEC 2.1, 9).

Unreal games keep saves in %LOCALAPPDATA%\\<Project>\\Saved\\Save* (engine default SaveGames;
Windrose puts its RocksDB world saves in SaveProfiles). Other games: the user picks the folder.
Snapshots are refused while the game runs - an open database copied mid-write is a torn backup.

Layout: %APPDATA%\\NexusOptimize\\savegames\\<game_id>\\<stamp>.zip, paths inside relative to the
folders' parent. The zip comment holds {created, files, fingerprint} so unchanged saves are skipped.
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile

ROOT = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize", "savegames")
KEEP = 20


def _folders_file():
    return os.path.join(ROOT, "folders.json")


def _folders():
    try:
        with open(_folders_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_folder(game_id, path):
    """Custom save folder for a game; None goes back to auto-detection."""
    folders = _folders()
    if path:
        path = os.path.abspath(path)
        if not os.path.isdir(path) or os.path.dirname(path) == path:
            return False, "Pick an existing folder (not a drive root)."
        folders[game_id] = path
    else:
        folders.pop(game_id, None)
    os.makedirs(ROOT, exist_ok=True)
    with open(_folders_file(), "w", encoding="utf-8") as f:
        json.dump(folders, f, indent=1)
    return True, f"Save folder set to {path}." if path else "Save folder back to auto-detect."


def save_dirs(spec):
    """Folders to back up (all share one parent). [] when unknown - never a guess."""
    custom = _folders().get(spec["id"])
    if custom:
        return [custom] if os.path.isdir(custom) else []
    if not spec.get("gus_path"):
        return []
    saved = os.path.dirname(os.path.dirname(os.path.dirname(spec["gus_path"])))   # ...\Saved\Config\<Platform>\GUS.ini
    if not os.path.isdir(saved):
        return []
    return sorted(e.path for e in os.scandir(saved) if e.is_dir() and e.name.startswith("Save"))


def _files(dirs):
    root = os.path.dirname(dirs[0])
    return [(p, os.path.relpath(p, root).replace(os.sep, "/"))
            for d in dirs for base, _, names in os.walk(d) for p in (os.path.join(base, n) for n in names)]


def _fingerprint(files):
    """Content hash: mtime can repeat for same-size rewrites within one Windows clock tick."""
    h = hashlib.sha256()
    for full, rel in sorted(files, key=lambda f: f[1]):
        h.update(rel.encode() + b"\0")
        with open(full, "rb") as f:
            h.update(hashlib.file_digest(f, "sha256").digest())
    return h.hexdigest()


def snapshots(game_id):
    """Newest first: [{id, created, files, size, fingerprint}]."""
    folder = os.path.join(ROOT, game_id)
    out = []
    if os.path.isdir(folder):
        for e in os.scandir(folder):
            if not e.name.endswith(".zip"):
                continue
            try:
                with zipfile.ZipFile(e.path) as z:
                    meta = json.loads(z.comment or b"{}")
            except (OSError, ValueError, zipfile.BadZipFile):
                continue
            out.append({"id": e.name, "size": e.stat().st_size, **meta})
    return sorted(out, key=lambda s: (s.get("created", 0), s["id"]), reverse=True)


def snapshot(spec, running=False):
    """(ok, message, snapshot or None). Skips (ok, no snapshot) when nothing changed since the last one."""
    if running:
        return False, f"Close {spec.get('name', 'the game')} first - its saves are open and a copy could be torn.", None
    dirs = save_dirs(spec)
    if not dirs:
        return False, "No save folder known for this game - pick it under Save Backups.", None
    files = _files(dirs)
    if not files:
        return False, "The save folder is empty.", None
    try:
        fp = _fingerprint(files)
    except OSError as e:
        return False, f"Backup failed: {e}", None
    existing = snapshots(spec["id"])
    if existing and existing[0].get("fingerprint") == fp:
        return True, "Saves unchanged since the last backup.", None

    folder = os.path.join(ROOT, spec["id"])
    os.makedirs(folder, exist_ok=True)
    stamp, n = time.strftime("%Y%m%d_%H%M%S"), 0
    name = f"{stamp}.zip"
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = f"{stamp}_{n}.zip"
    path = os.path.join(folder, name)
    meta = {"created": time.time(), "files": len(files), "fingerprint": fp}
    try:
        with zipfile.ZipFile(path + ".part", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for full, rel in files:
                z.write(full, rel)
            z.comment = json.dumps(meta).encode()
        os.replace(path + ".part", path)   # a crash mid-zip never leaves a half backup listed
    except OSError as e:
        if os.path.exists(path + ".part"):
            os.remove(path + ".part")
        return False, f"Backup failed: {e}", None
    for old in snapshots(spec["id"])[KEEP:]:
        os.remove(os.path.join(folder, old["id"]))
    return True, f"Backed up {len(files)} save file(s).", {"id": name, "size": os.path.getsize(path), **meta}


def restore(spec, snap_id, running=False):
    """Replaces the save folders with a snapshot. The current saves are snapshotted first."""
    if running:
        return False, f"Close {spec.get('name', 'the game')} before restoring saves."
    if snap_id not in {s["id"] for s in snapshots(spec["id"])}:
        return False, "Unknown save backup."
    dirs = save_dirs(spec)
    if not dirs:
        return False, "No save folder known for this game."
    root = os.path.dirname(dirs[0])
    ok, msg, _ = snapshot(spec)
    if not ok and _files(dirs):   # never overwrite saves we could not copy first
        return False, f"Restore cancelled - {msg}"
    try:
        with zipfile.ZipFile(os.path.join(ROOT, spec["id"], snap_id)) as z:
            tops = {n.split("/")[0] for n in z.namelist()}
            if any(t in ("", ".", "..") or ":" in t or "\\" in t for t in tops):
                return False, "That backup is damaged."
            staging = tempfile.mkdtemp(prefix=".nexus-restore-", dir=root)
            z.extractall(staging)   # fully unpacked before anything is deleted
        old = tempfile.mkdtemp(prefix=".nexus-old-", dir=root)
        for t in tops:   # rename aside (fails cleanly on a locked file) instead of deleting in place
            if os.path.exists(os.path.join(root, t)):
                os.replace(os.path.join(root, t), os.path.join(old, t))
            os.replace(os.path.join(staging, t), os.path.join(root, t))
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"Restore failed: {e}"
    return True, f"Saves restored from {snap_id}."
