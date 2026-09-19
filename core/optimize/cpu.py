"""Launch + server/client CPU isolation (hybrid-core aware).

Flow of an optimized launch for the selected game (runs on a background
thread, progress is exposed through get_status()):
  1. Take the install location from the game's discovered profile.
  2. Games with a local dedicated server (Windrose): start it (unless already
     running), pin it to E-cores (detected via GetSystemCpuSetInformation
     EfficiencyClass, never hardcoded) at BelowNormal priority.
  3. Launch the client through its store URI, wait for it, give it every
     other core (or every core, when there is no server) at High priority,
     and re-assert that policy until the client exits.
No config files are written here (v1 auto-applied unverified ini tweaks).
"""

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes

import psutil

SERVER_CORES = 4
SERVER_PRIORITY = psutil.BELOW_NORMAL_PRIORITY_CLASS
CLIENT_PRIORITY = psutil.HIGH_PRIORITY_CLASS
SERVER_WARMUP_S = 15
CLIENT_WAIT_S = 180
REASSERT_EVERY_S = 10


_PRIORITY_NAMES = {
    psutil.IDLE_PRIORITY_CLASS: "Idle",
    psutil.BELOW_NORMAL_PRIORITY_CLASS: "BelowNormal",
    psutil.NORMAL_PRIORITY_CLASS: "Normal",
    psutil.ABOVE_NORMAL_PRIORITY_CLASS: "AboveNormal",
    psutil.HIGH_PRIORITY_CLASS: "High",
}

_lock = threading.Lock()
_status = {"running": False, "phase": "idle", "game": None, "message": "Not started.", "log": []}


# ---------------------------------------------------------------- status
def get_status():
    with _lock:
        return {**_status, "log": list(_status["log"])}


def _set(phase=None, message=None, running=None):
    with _lock:
        if phase is not None:
            _status["phase"] = phase
        if running is not None:
            _status["running"] = running
        if message is not None:
            _status["message"] = message
            _status["log"].append(f"{time.strftime('%H:%M:%S')}  {message}")
            del _status["log"][:-50]
    if message:
        print(f"[server_optimizer] {message}", flush=True)


# ---------------------------------------------------------------- install lookup
def find_game(spec):
    """{"path", "spec"} for a games.discover() profile whose install exists, else None."""
    if spec.get("install_path") and os.path.isdir(spec["install_path"]):
        return {"path": spec["install_path"], "spec": spec}
    return None


def has_server(game):
    return bool(game["spec"]["server_exe"])


def game_paths(game):
    spec, root = game["spec"], game["path"]
    paths = {
        "reshade": os.path.join(root, spec["reshade_dir"], "ReShade.ini"),
        "server_root": None,
        "server_exe": None,
    }
    if has_server(game):
        server_root = os.path.join(root, spec["server_root"])
        paths.update(server_root=server_root, server_exe=os.path.join(server_root, spec["server_exe"]))
    return paths


# ---------------------------------------------------------------- processes
def _procs_under(directory, exe_name):
    """Processes named exe_name (None = any name) running from under directory."""
    prefix = os.path.normcase(os.path.abspath(directory)) + os.sep
    found = []
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if exe_name and (p.info["name"] or "").lower() != exe_name.lower():
                continue
            exe = p.info["exe"]
            if exe and os.path.normcase(exe).startswith(prefix):
                found.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def client_procs(game):
    procs = _procs_under(game["path"], game["spec"]["client_exe"])
    if game["spec"]["server_exe"]:
        server = os.path.basename(game["spec"]["server_exe"]).lower()
        procs = [p for p in procs if (p.info["name"] or "").lower() != server]
    return procs


def server_procs(game):
    exe = game_paths(game)["server_exe"]
    if not exe:
        return []
    return _procs_under(os.path.dirname(exe), os.path.basename(exe))


# ---------------------------------------------------------------- CPU topology
class _CpuSet(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("Id", wintypes.DWORD),
        ("Group", wintypes.WORD),
        ("LogicalProcessorIndex", ctypes.c_ubyte),
        ("CoreIndex", ctypes.c_ubyte),
        ("LastLevelCacheIndex", ctypes.c_ubyte),
        ("NumaNodeIndex", ctypes.c_ubyte),
        ("EfficiencyClass", ctypes.c_ubyte),
    ]


def _efficiency_classes(n):
    """Efficiency class per logical CPU in group 0 (higher = faster), -1 if unknown."""
    result = [-1] * n
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.GetSystemCpuSetInformation
        fn.argtypes = [ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.HANDLE, wintypes.ULONG]
        fn.restype = wintypes.BOOL
        length = wintypes.ULONG(0)
        fn(None, 0, ctypes.byref(length), None, 0)
        if not length.value:
            return result
        buf = ctypes.create_string_buffer(length.value)
        if not fn(buf, length, ctypes.byref(length), None, 0):
            return result
        off = 0
        while off + ctypes.sizeof(_CpuSet) <= length.value:
            info = _CpuSet.from_buffer_copy(buf, off)
            if info.Size == 0:
                break
            if info.Type == 0 and info.Group == 0 and info.LogicalProcessorIndex < n:
                result[info.LogicalProcessorIndex] = info.EfficiencyClass
            off += info.Size
    except (OSError, AttributeError, ValueError):
        pass
    return result


def core_layout(with_server=True):
    """Without a server the client keeps every CPU (priority boost only)."""
    n = min(psutil.cpu_count(logical=True) or os.cpu_count() or 1, 64)
    eff = _efficiency_classes(n)
    hybrid = all(e >= 0 for e in eff) and len(set(eff)) > 1
    pool = [i for i in range(n) if eff[i] == min(eff)] if hybrid else list(range(n))
    server = pool[-min(SERVER_CORES, len(pool)):] if with_server else []
    client = [i for i in range(n) if i not in server] or list(range(n))
    return {"logical": n, "hybrid": hybrid, "e_cores": len(pool) if hybrid else 0,
            "server_cpus": server, "client_cpus": client}


def _fmt_cpus(cpus):
    if len(cpus) > 1 and cpus[-1] - cpus[0] == len(cpus) - 1:
        return f"CPU {cpus[0]}-{cpus[-1]}"
    return "CPU " + ",".join(map(str, cpus))


def apply_policy(proc, cpus, priority, label, quiet=False):
    """Pins affinity + priority. Returns True on success (changed or already set)."""
    try:
        changed = False
        if sorted(proc.cpu_affinity()) != sorted(cpus):
            proc.cpu_affinity(cpus)
            changed = True
        if proc.nice() != priority:
            proc.nice(priority)
            changed = True
        if changed or not quiet:
            _set(message=f"{label} (pid {proc.pid}): {_fmt_cpus(cpus)}, priority {_PRIORITY_NAMES.get(priority, priority)}.")
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        _set(message=f"{label} (pid {proc.pid}): access denied - it runs elevated; start NexusApp as administrator.")
        return False


def isolate(game, layout, quiet=False):
    name = game["spec"]["name"]
    if has_server(game):
        servers = server_procs(game)
        if not servers:
            return   # nothing to isolate from; leave the client on default scheduling
        for p in servers:
            apply_policy(p, layout["server_cpus"], SERVER_PRIORITY, f"{name} server", quiet)
    for p in client_procs(game):
        apply_policy(p, layout["client_cpus"], CLIENT_PRIORITY, f"{name} client", quiet)


# ---------------------------------------------------------------- launch sequence
def _start_server(game, layout):
    paths = game_paths(game)
    label = f"{game['spec']['name']} server"
    args = [*game["spec"]["server_args"], f"-corelimit={SERVER_CORES}"]
    running = server_procs(game)
    if running:
        _set(message=f"Server already running (pid {running[0].pid}) - isolating it.")
        apply_policy(running[0], layout["server_cpus"], SERVER_PRIORITY, label)
        return False
    if not os.path.exists(paths["server_exe"]):
        raise RuntimeError(f"Dedicated server not found: {paths['server_exe']}")
    # Own console window: it shows the invite code, same as the stock launcher.
    popen = subprocess.Popen([paths["server_exe"], *args], cwd=paths["server_root"],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    _set(message=f"Server started (pid {popen.pid}) with {' '.join(args)}.")
    # Pin immediately so the engine's worker threads start on the E-cores.
    apply_policy(psutil.Process(popen.pid), layout["server_cpus"], SERVER_PRIORITY, label)
    return True


def _run(game):
    spec = game["spec"]
    name = spec["name"]
    try:
        server = has_server(game)
        layout = core_layout(with_server=server)
        kind = f"hybrid, {layout['e_cores']} E-cores" if layout["hybrid"] else "no hybrid layout"
        if server:
            _set("preparing", f"{layout['logical']} logical CPUs ({kind}): server -> {_fmt_cpus(layout['server_cpus'])}, "
                              f"client -> {_fmt_cpus(layout['client_cpus'])}.")
        else:
            _set("preparing", f"{layout['logical']} logical CPUs ({kind}): {name} has no local server - "
                              f"client -> {_fmt_cpus(layout['client_cpus'])} at High priority.")

        if server:
            _set("server", "Starting local dedicated server...")
            if _start_server(game, layout) and SERVER_WARMUP_S:
                _set(message=f"Letting the server load its world for {SERVER_WARMUP_S}s...")
                time.sleep(SERVER_WARMUP_S)

        if not client_procs(game):
            if spec["launch_uri"]:
                _set("launching", f"Launching {name} via {spec['store'].capitalize()}...")
                os.startfile(spec["launch_uri"])
            else:
                _set("launching", f"{name} has no store launcher - start it manually now.")

        _set("waiting", f"Waiting for the {name} client...")
        deadline = time.monotonic() + CLIENT_WAIT_S
        while not client_procs(game):
            if time.monotonic() > deadline:
                _set("error", f"{name} client never appeared (3 min timeout).", running=False)
                return
            time.sleep(2)

        isolate(game, layout)
        policy = "isolation" if server else "client priority"
        _set("monitoring", f"Optimized: {policy} active - re-asserted every 10s while the game runs.")
        while client_procs(game):
            time.sleep(REASSERT_EVERY_S)
            isolate(game, layout, quiet=True)

        note = f" {name} server is still running - close its console when done (not killed, to protect the save)." \
            if server_procs(game) else ""
        _set("done", f"Game closed.{note}", running=False)
    except Exception as e:  # surface every failure to the UI instead of dying silently
        _set("error", f"Optimized launch failed: {e}", running=False)


def boost_client(spec):
    """High CPU priority for a running game; affinity is left as is (an
    optimized launch may have isolated a server). Returns (ok, message)."""
    game = find_game(spec)
    procs = client_procs(game) if game else []
    if not procs:
        return False, f"{spec['name']} is not running - start it first."
    done = [p for p in procs if _boost(p)]
    if not done:
        return False, f"Could not raise {spec['name']} priority - start NexusApp as administrator."
    return True, f"{spec['name']}: High CPU priority set on {len(done)} process(es)."


def _boost(proc):
    try:
        if proc.nice() != CLIENT_PRIORITY:
            proc.nice(CLIENT_PRIORITY)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


# ---------------------------------------------------------------- Process Assassin (Ultra Game Mode)
# Manual toggle only. Lowers apps that sit at Normal priority to Idle and on
# OFF restores exactly the processes it lowered (pid + create time, so a
# reused pid is never touched). steam.exe itself is left alone: Steam games
# depend on it; its web helpers are the heavy part.
BACKGROUND_APPS = frozenset(n.lower() for n in (
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "Discord.exe", "Spotify.exe", "Teams.exe", "ms-teams.exe", "slack.exe",
    "steamwebhelper.exe", "EpicGamesLauncher.exe", "EpicWebHelper.exe", "EADesktop.exe",
    "Battle.net.exe", "GalaxyClient.exe", "upc.exe", "RiotClientServices.exe",
))

_assassin_lock = threading.Lock()
_assassin = {"enabled": False, "lowered": {}}   # lowered: pid -> create_time


def _prune_lowered():
    """Forget lowered processes that exited (or whose pid was reused) so counts stay exact."""
    lowered = _assassin["lowered"]
    for pid, created in list(lowered.items()):
        try:
            if psutil.Process(pid).create_time() == created:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        del lowered[pid]


def assassin_status():
    with _assassin_lock:
        _prune_lowered()
        return {"enabled": _assassin["enabled"], "lowered": len(_assassin["lowered"])}


def process_assassin(enabled):
    """ON: background apps at Normal -> Idle. OFF: the ones we lowered -> Normal.
    Returns (ok, message)."""
    with _assassin_lock:
        return _assassin_on() if enabled else _assassin_off()


def _assassin_on():
    _prune_lowered()
    lowered, denied = _assassin["lowered"], 0
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() not in BACKGROUND_APPS or p.pid in lowered:
                continue
            if p.nice() == psutil.NORMAL_PRIORITY_CLASS:
                p.nice(psutil.IDLE_PRIORITY_CLASS)
                lowered[p.pid] = p.create_time()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            denied += 1
    _assassin["enabled"] = True
    msg = f"Ultra Game Mode on: {len(lowered)} background process(es) at Idle priority."
    if denied:
        msg += f" {denied} skipped (access denied)."
    return True, msg


def _assassin_off():
    restored = 0
    for pid, created in _assassin["lowered"].items():
        try:
            p = psutil.Process(pid)
            # Changed by someone else since? Leave it.
            if p.create_time() == created and p.nice() == psutil.IDLE_PRIORITY_CLASS:
                p.nice(psutil.NORMAL_PRIORITY_CLASS)
                restored += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _assassin.update(enabled=False, lowered={})
    return True, f"Ultra Game Mode off: {restored} process(es) back to Normal priority."


def launch_optimized(spec):
    """Starts the launch sequence for a games.discover() profile on a background
    thread. Returns (ok, message)."""
    with _lock:
        if _status["running"]:
            return False, "An optimized launch is already in progress."
        _status.update(running=True, phase="starting", game=spec["name"], log=[])

    game = find_game(spec)
    if not game:
        _set("error", f"{spec['name']} install folder not found - rescan your games.", running=False)
        return False, get_status()["message"]

    _set(message=f"{spec['name']} found at {game['path']}.")
    threading.Thread(target=_run, args=(game,), daemon=True, name="server-optimizer").start()
    if has_server(game):
        return True, "Optimized launch started - server first, then the client."
    return True, f"Optimized launch started for {spec['name']}."
