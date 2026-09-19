"""Encoding-preserving INI editing with a backup before every write.

Never touches a running game's process or memory - it edits the plain-text
.ini files the game already reads from disk. Every file is copied to
%APPDATA%\\NexusOptimize\\backups\\<timestamp>\\ before it is changed.

Rule (SPEC.md 0.4): every key written by this app must cite a source.
"""

import hashlib
import json
import os
import re
import stat

from core.safety.backup import BACKUP_ROOT, backup

# FrameGeneratorMode: Off / On (2x) / On3x / On4x.
# Source: the game's own %LOCALAPPDATA%\R5\Saved\Config\Windows\GameUserSettings.ini
# ([/Script/R5.R5SettingsLocal] FrameGeneratorMode=On4x) and the string exists in
# Windrose-Win64-Shipping.exe. Verified 2026-09-18, see docs/DECISIONS.md.
FRAMEGEN_SECTION = "/Script/R5.R5SettingsLocal"   # Windrose; per-game value in discovery
FRAMEGEN_2X = "On"

# ReShade [GENERAL] PresetPath: the preset ReShade loads at startup.
# Source: the installed ReShade's own Windrose\R5\Binaries\Win64\ReShade.ini
# ([GENERAL] PresetPath=.\ReShadePreset.ini), checked 2026-09-18.
RESHADE_SECTION = "GENERAL"

_BOMS = [
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xef\xbb\xbf", "utf-8"),
]


def _read_ini(path):
    raw = open(path, "rb").read()
    bom, enc = b"", "utf-8"
    for b, e in _BOMS:
        if raw.startswith(b):
            bom, enc = b, e
            break
    text = raw[len(bom):].decode(enc, errors="surrogateescape")
    eol = "\r\n" if "\r\n" in text else "\n"
    return {"path": path, "bom": bom, "enc": enc, "eol": eol, "lines": re.split(r"\r?\n", text)}


def _write_ini(ini):
    path = ini["path"]
    mode = os.stat(path).st_mode
    if not mode & stat.S_IWRITE:
        os.chmod(path, mode | stat.S_IWRITE)
    backup(path)
    data = ini["eol"].join(ini["lines"]).encode(ini["enc"], errors="surrogateescape")
    with open(path, "wb") as f:
        f.write(ini["bom"] + data)
    os.chmod(path, mode)   # a Time Machine lock (read-only) survives our own writes


def _find_section(ini, key):
    """First section defining key (auto-discovered UE games don't know their
    settings class name up front)."""
    section = None
    for line in ini["lines"]:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        elif section and "=" in s and s.split("=", 1)[0].strip() == key:
            return section
    return None


def _get_value(ini, section, key):
    in_section = False
    for line in ini["lines"]:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s == f"[{section}]"
        elif in_section and "=" in s and s.split("=", 1)[0].strip() == key:
            return s.split("=", 1)[1].strip()
    return None


def _set_value(ini, section, key, value):
    """Returns True if the lines changed."""
    lines = ini["lines"]
    header = f"[{section}]"
    start = next((i for i, l in enumerate(lines) if l.strip() == header), -1)
    if start < 0:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", header, f"{key}={value}", ""]
        return True

    last_content = start
    for i in range(start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("["):
            break
        if "=" in s and s.split("=", 1)[0].strip() == key:
            if s.split("=", 1)[1].strip() == value:
                return False
            lines[i] = f"{key}={value}"
            return True
        if s:
            last_content = i
    lines.insert(last_content + 1, f"{key}={value}")
    return True


def update_ini_settings(filepath, entries):
    """entries: iterable of (section, key, value). Returns (ok, changed_count)."""
    if not filepath or not os.path.exists(filepath):
        return False, 0
    ini = _read_ini(filepath)
    changed = sum(_set_value(ini, s, k, v) for s, k, v in entries)
    if changed:
        _write_ini(ini)
    return True, changed


def cap_frame_generation(gus_path, section=FRAMEGEN_SECTION):
    """Lowers multi frame generation (On3x/On4x) to 2x. Leaves Off/On alone.
    Fewer generated frames = somewhat less VRAM; it is NOT a VRAM leak fix.
    The game must be closed (UE rewrites this file on exit).
    section=None searches every section. Returns (ok, message)."""
    if not gus_path:
        return False, "This game has no GameUserSettings.ini (not an Unreal Engine title)."
    if not os.path.exists(gus_path):
        return False, "GameUserSettings.ini not found - launch the game once to create it."
    ini = _read_ini(gus_path)
    section = section or _find_section(ini, "FrameGeneratorMode")
    mode = _get_value(ini, section, "FrameGeneratorMode") if section else None
    if mode is None:
        return True, "FrameGeneratorMode not set - nothing to change."
    if not re.fullmatch(r"On\d+x", mode):
        return True, f"FrameGeneratorMode={mode} - already 2x or off."
    _set_value(ini, section, "FrameGeneratorMode", FRAMEGEN_2X)
    _write_ini(ini)
    return True, f"Frame Generation {mode} -> 2x (FrameGeneratorMode={FRAMEGEN_2X}). Backup saved."


def set_reshade_preset(reshade_ini, preset_path):
    """Points ReShade's startup preset at preset_path (relative, like .\\X.ini).
    ReShade rewrites ReShade.ini while it runs, so call this with the game
    closed. Returns (ok, previous PresetPath or None)."""
    if not os.path.exists(reshade_ini):
        return False, None
    ini = _read_ini(reshade_ini)
    old = _get_value(ini, RESHADE_SECTION, "PresetPath")
    if _set_value(ini, RESHADE_SECTION, "PresetPath", preset_path):
        _write_ini(ini)
    return True, old


# ---------------------------------------------------------------- Config Time Machine
# History = core.safety.backup entries for the file. Lock = read-only attribute, so the
# game can't rewrite it on exit. Pin = sha256 of a known-good state, to spot a revert.
PINS = os.path.join(os.path.dirname(BACKUP_ROOT), "ini_pins.json")


def get_hash(path):
    if not os.path.exists(path): return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _pins():
    try:
        with open(PINS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def pinned(path):
    return _pins().get(os.path.normcase(os.path.abspath(path)))


def pin(path):
    pins = _pins()
    pins[os.path.normcase(os.path.abspath(path))] = get_hash(path)
    os.makedirs(os.path.dirname(PINS), exist_ok=True)
    with open(PINS, "w", encoding="utf-8") as f:
        json.dump(pins, f, indent=1)

def lock_ini(path, readonly=True):
    if not os.path.exists(path): return False
    mode = os.stat(path).st_mode
    if readonly:
        os.chmod(path, mode & ~stat.S_IWRITE)
    else:
        os.chmod(path, mode | stat.S_IWRITE)
    return True

def is_reverted(path, expected_hash):
    """Checks if the game undid our tweaks on exit."""
    return get_hash(path) != expected_hash
