"""Copy-before-change backups and one-click rollback (SPEC 9).

Layout: %APPDATA%\\NexusOptimize\\backups\\<stamp>\\<name> plus <name>.source,
which holds the original path. Every writer in the app backs up through here,
so the Backups tab lists everything the app has ever changed.
"""

import os
import shutil
import time

BACKUP_ROOT = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize", "backups")


def backup(path):
    """Copies path into a fresh stamp dir. Returns the backup file path."""
    name = os.path.basename(path)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder, n = os.path.join(BACKUP_ROOT, stamp), 0
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        folder = os.path.join(BACKUP_ROOT, f"{stamp}_{n}")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, name)
    shutil.copy2(path, dest)
    with open(dest + ".source", "w", encoding="utf-8") as f:
        f.write(path)
    return dest


def list_backups():
    """Newest first: [{id, original, size, created}]."""
    out = []
    if not os.path.isdir(BACKUP_ROOT):
        return out
    for folder in os.scandir(BACKUP_ROOT):
        if not folder.is_dir():
            continue
        for f in os.scandir(folder.path):
            if f.name.endswith(".source") or not os.path.exists(f.path + ".source"):
                continue
            with open(f.path + ".source", encoding="utf-8") as s:
                original = s.read().strip()
            # copy2 keeps the original's mtime; the .source sidecar is written at backup time.
            out.append({"id": f"{folder.name}/{f.name}", "original": original,
                        "size": f.stat().st_size, "created": os.stat(f.path + ".source").st_mtime})
    return sorted(out, key=lambda b: (b["created"], b["id"]), reverse=True)


def restore(backup_id):
    """Copies a backup over its original, backing up the current file first."""
    root = os.path.abspath(BACKUP_ROOT)
    path = os.path.abspath(os.path.join(root, str(backup_id)))
    if os.path.dirname(os.path.dirname(path)) != root or not os.path.isfile(path + ".source"):
        return False, "Unknown backup."
    with open(path + ".source", encoding="utf-8") as s:
        original = s.read().strip()
    try:
        if os.path.exists(original):
            backup(original)
        os.makedirs(os.path.dirname(original), exist_ok=True)
        shutil.copy2(path, original)
    except OSError as e:
        return False, f"Restore failed: {e}"
    return True, f"Restored {original}."
