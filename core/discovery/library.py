"""Game profiles - curated entries plus everything auto-discovered on this PC.

Discovery sources:
  * Steam  - every library in libraryfolders.vdf, one profile per appmanifest.
  * Epic   - the launcher's *.item manifests under %PROGRAMDATA%.
(v1 also auto-added every full-screen foreground app; removed in Phase 0 -
unknown-game suggestions with a confidence score are Phase 1, SPEC 5.3.)

Each discovered install is inspected on disk to pick an optimization profile:
  * "unreal"  - Unreal Engine build (<Project>/Binaries/Win64 + Engine/ or a
                *-Shipping.exe): Frame Generation cap on its
                GameUserSettings.ini, plus High CPU priority.
  * "generic" - anything else: High CPU priority.

Curated entries (CURATED) carry hand-verified paths and win over discovery
for the same Steam install dir. Every spec has the same keys, so the rest of
the backend only ever works with the currently selected entry.
"""

import glob
import json
import os
import re
import threading
import winreg

# Used for curated games when no store could be scanned at all.
STEAM_COMMON_FALLBACK = r"D:\Steam\steamapps\common"

CURATED = {
    "windrose": {
        "id": "windrose",
        "name": "Windrose",
        "engine": "Unreal Engine 5",
        "profile": "unreal",
        "install_dir": "Windrose",
        "reshade_dir": r"R5\Binaries\Win64",
        "gus_path": os.path.expandvars(r"%LOCALAPPDATA%\R5\Saved\Config\Windows\GameUserSettings.ini"),
        "framegen_section": "/Script/R5.R5SettingsLocal",
        "client_exe": "Windrose-Win64-Shipping.exe",
        "server_root": r"R5\Builds\WindowsServer",
        "server_exe": r"R5\Binaries\Win64\WindroseServer-Win64-Shipping.exe",  # relative to server_root
        "server_args": ["-log", "-nullrhi"],
    },
    "007": {
        "id": "007",
        "name": "007 First Light",
        "engine": "Glacier",
        "profile": "generic",
        "install_dir": "007 First Light",
        "reshade_dir": "Retail",
        "gus_path": None,          # Glacier engine, not UE5: no GameUserSettings.ini
        "framegen_section": None,
        "client_exe": "007FirstLight.exe",
        "server_root": None,       # no local dedicated server
        "server_exe": None,
        "server_args": [],
    },
}
DEFAULT_GAME = "windrose"

# Steam runtimes/redistributables that show up as "installed apps".
_STEAM_NON_GAMES = {
    "228980",   # Steamworks Common Redistributables
    "250820",   # SteamVR
    "1070560", "1391110", "1628350",             # Steam Linux Runtime
    "1493710", "2180100", "2348590", "3658110",  # Proton / Proton Experimental / Hotfix
    "1826330",  # Proton EasyAntiCheat Runtime
    "1161040",  # Proton BattlEye Runtime
}
_NON_GAME_NAME = re.compile(r"redistributable|dedicated server|\bsdk\b|soundtrack|steamvr|proton|linux runtime", re.I)

# Executables that are never the game itself.
_JUNK_EXE = re.compile(
    r"unins|setup|install|redist|vc_?redist|dxsetup|directx|crash|report|launcher|helper|"
    r"easyanticheat|battleye|beservice|ue4prereq|ueprereq|dotnet|physx|oalinst|touchup|cleanup|"
    r"unitycrashhandler|cefprocess|webhelper|update",
    re.I,
)

_lock = threading.Lock()
_cache = {"games": None, "version": 0}


# ---------------------------------------------------------------- engine detection
def _subdirs(path):
    try:
        return [e for e in os.scandir(path) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _exes(path):
    try:
        return [e for e in os.scandir(path) if e.is_file() and e.name.lower().endswith(".exe")]
    except OSError:
        return []


def _find_unreal(root):
    """Returns (project_dir_rel, shipping_exe_or_None) for an Unreal build, else None.
    UE packages games as <root>/<Project>/Binaries/Win64 next to <root>/Engine."""
    has_engine = os.path.isdir(os.path.join(root, "Engine"))
    candidates = [(d.name, d.path) for d in _subdirs(root)]
    # Some stores nest the build one level deeper (<root>/<Wrapper>/<Project>).
    for name, path in list(candidates):
        if name.lower() != "engine" and not os.path.isdir(os.path.join(path, "Binaries")):
            candidates += [(os.path.join(name, d.name), d.path) for d in _subdirs(path)]

    for rel, path in candidates:
        if os.path.basename(rel).lower() == "engine":
            continue
        win64 = os.path.join(path, "Binaries", "Win64")
        if not os.path.isdir(win64):
            continue
        shipping = next((e.name for e in _exes(win64) if e.name.lower().endswith("-shipping.exe")
                         and "server" not in e.name.lower()), None)
        if shipping or has_engine:
            return rel, shipping
    return None


def _unreal_gus_path(project):
    """%LOCALAPPDATA%\\<Project>\\Saved\\Config\\<Platform>\\GameUserSettings.ini -
    UE5 uses Windows, UE4 WindowsNoEditor; prefer whichever already exists."""
    base = os.path.join(os.path.expandvars("%LOCALAPPDATA%"), os.path.basename(project), "Saved", "Config")
    options = [os.path.join(base, p, "GameUserSettings.ini") for p in ("Windows", "WindowsNoEditor", "WinGDK")]
    return next((p for p in options if os.path.exists(p)), options[0])


def _find_reshade_dir(root, max_depth=3):
    """Relative dir holding ReShade.ini (where the DLSS5 fixes live), or None."""
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if os.path.isfile(os.path.join(path, "ReShade.ini")):
            return os.path.relpath(path, root)
        if depth < max_depth:
            stack += [(d.path, depth + 1) for d in _subdirs(path)]
    return None


def _main_exe(root, max_depth=2):
    """Best guess at the game executable: the largest non-helper .exe near the root."""
    best, stack = None, [(root, 0)]
    while stack:
        path, depth = stack.pop()
        for e in _exes(path):
            if _JUNK_EXE.search(e.name):
                continue
            try:
                size = e.stat().st_size
            except OSError:
                continue
            if not best or size > best[0]:
                best = (size, e.path)
        if depth < max_depth:
            stack += [(d.path, depth + 1) for d in _subdirs(path)]
    return best[1] if best else None


def _engine_label(root, main_exe):
    exe_dir = os.path.dirname(main_exe) if main_exe else root
    if os.path.exists(os.path.join(exe_dir, "UnityPlayer.dll")):
        return "Unity"
    if glob.glob(os.path.join(root, "*.pck")) or glob.glob(os.path.join(exe_dir, "*.pck")):
        return "Godot"
    if os.path.isdir(os.path.join(root, "bin", "win64")) and os.path.isdir(os.path.join(root, "game")):
        return "Source 2"
    return "Unknown engine"


def build_profile(game_id, name, root, store, launch_uri=None, appid=None, client_exe=None):
    """Inspects an install directory and returns a full game spec, or None if
    it holds nothing that looks like a Windows game."""
    spec = {
        "id": game_id,
        "name": name,
        "store": store,
        "appid": appid,
        "launch_uri": launch_uri,
        "install_dir": os.path.basename(root.rstrip("\\/")),
        "install_path": root,
        "server_root": None,
        "server_exe": None,
        "server_args": [],
        "gus_path": None,
        "framegen_section": None,   # None = locate FrameGeneratorMode in any section
    }
    unreal = _find_unreal(root)
    if unreal:
        project, shipping = unreal
        win64 = os.path.join(project, "Binaries", "Win64")
        spec.update(
            engine="Unreal Engine",
            profile="unreal",
            gus_path=_unreal_gus_path(project),
            client_exe=client_exe or shipping,
            reshade_dir=_find_reshade_dir(root) or win64,
        )
        return spec

    main = None if client_exe else _main_exe(root)
    if not client_exe and not main:
        return None   # tools/runtimes without a Windows executable
    exe_dir = os.path.relpath(os.path.dirname(main), root) if main else "."
    spec.update(
        engine=_engine_label(root, main),
        profile="generic",
        # None = any process running from the install dir (launchers often
        # hand off to a differently named binary).
        client_exe=client_exe,
        reshade_dir=_find_reshade_dir(root) or exe_dir,
    )
    return spec


# ---------------------------------------------------------------- Steam
def steam_libraries():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
    except OSError:
        return []
    libs = {os.path.normcase(steam): steam}
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                p = m.group(1).replace("\\\\", "\\")
                libs.setdefault(os.path.normcase(p), p)
    except OSError:
        pass
    return list(libs.values())


def steam_apps():
    """[{appid, name, path}] for every installed Steam app with a manifest."""
    apps = []
    for lib in steam_libraries():
        steamapps = os.path.join(lib, "steamapps")
        for manifest in glob.glob(os.path.join(steamapps, "appmanifest_*.acf")):
            try:
                with open(manifest, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except OSError:
                continue
            fields = dict(re.findall(r'^\s*"(appid|name|installdir)"\s+"([^"]*)"', txt, re.M))
            if "appid" not in fields or "installdir" not in fields:
                continue
            path = os.path.join(steamapps, "common", fields["installdir"])
            if os.path.isdir(path):
                apps.append({"appid": fields["appid"], "name": fields.get("name") or fields["installdir"], "path": path})
    return apps


def _discover_steam():
    found = {}
    curated_by_dir = {spec["install_dir"].lower(): spec for spec in CURATED.values()}
    for app in steam_apps():
        if app["appid"] in _STEAM_NON_GAMES or _NON_GAME_NAME.search(app["name"]):
            continue
        uri = f"steam://rungameid/{app['appid']}"
        curated = curated_by_dir.get(os.path.basename(app["path"]).lower())
        if curated:
            spec = {**curated, "store": "steam", "appid": app["appid"], "launch_uri": uri, "install_path": app["path"]}
        else:
            spec = build_profile(f"steam-{app['appid']}", app["name"], app["path"], "steam",
                                 launch_uri=uri, appid=app["appid"])
        if spec:
            found[spec["id"]] = spec
    return found


# ---------------------------------------------------------------- Epic
def _discover_epic():
    found = {}
    manifests = os.path.join(os.path.expandvars("%PROGRAMDATA%"), "Epic", "EpicGamesLauncher", "Data", "Manifests")
    for item in glob.glob(os.path.join(manifests, "*.item")):
        try:
            with open(item, encoding="utf-8", errors="replace") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        root = m.get("InstallLocation")
        cats = m.get("AppCategories") or []
        if not root or not os.path.isdir(root) or m.get("bIsIncompleteInstall") or (cats and "games" not in cats):
            continue
        app = m.get("AppName") or os.path.basename(root)
        uri = None
        if m.get("CatalogNamespace") and m.get("CatalogItemId"):
            uri = (f"com.epicgames.launcher://apps/{m['CatalogNamespace']}%3A{m['CatalogItemId']}%3A{app}"
                   "?action=launch&silent=true")
        exe = os.path.basename(m["LaunchExecutable"]) if m.get("LaunchExecutable") else None
        spec = build_profile(f"epic-{app}", m.get("DisplayName") or app, root, "epic", launch_uri=uri, client_exe=exe)
        if spec:
            found[spec["id"]] = spec
    return found


# ---------------------------------------------------------------- public API
def discover(refresh=False):
    """{id: spec} of every game found on this PC (cached; refresh=True rescans)."""
    with _lock:
        if _cache["games"] is not None and not refresh:
            return _cache["games"]
    games = {**_discover_steam(), **_discover_epic()}
    if not games:
        # No store could be read - keep the curated profiles on their default paths.
        games = {gid: {**spec, "store": "steam", "appid": None, "launch_uri": None,
                       "install_path": os.path.join(STEAM_COMMON_FALLBACK, spec["install_dir"])}
                 for gid, spec in CURATED.items()}
    with _lock:
        _cache["games"] = games
        _cache["version"] += 1
        return _cache["games"]


def get(game_id):
    return discover().get(game_id)


def default_id():
    games = discover()
    return DEFAULT_GAME if DEFAULT_GAME in games else next(iter(sorted(games, key=lambda g: games[g]["name"].lower())), None)


def version():
    """Bumped whenever the game list changes, so the UI knows to reload it."""
    with _lock:
        return _cache["version"]


def summary(spec):
    """JSON-safe description of a game's capabilities for the UI."""
    return {
        "id": spec["id"],
        "name": spec["name"],
        "engine": spec["engine"],
        "profile": spec["profile"],
        "store": spec["store"],
        "can_launch": bool(spec["launch_uri"]),
        "has_server": bool(spec["server_exe"]),
        "supports_fg_cap": bool(spec["gus_path"]),
    }
