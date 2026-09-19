"""FastAPI core service. The UI talks to it on 127.0.0.1 with a per-session
token (X-Nexus-Token) that Electron generates and passes in via NEXUS_TOKEN.
Only /health is open, so the launcher can poll readiness.
"""

import os
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.discovery import library as games
from core.mods import bisect, live, manager, nexus
from core.optimize import autopilot, cpu, ini
from core.optimize.power import set_power_plan
from core.safety import backup, saves
from core.telemetry import bench, nvml, sensors

TOKEN = os.environ.get("NEXUS_TOKEN") or secrets.token_hex(32)
VRAM_AMBER, VRAM_RED = 85, 92   # SPEC 6.4 thresholds


def _resolve_game(spec):
    """Active-game record (install location + config paths) for a discovered profile."""
    found = cpu.find_game(spec)
    install = found or {"path": spec["install_path"], "spec": spec}
    return {
        **games.summary(spec),
        "installed": bool(found),
        "install_path": install["path"],
        "config_path": cpu.game_paths(install)["reshade"],
        "gus_path": spec["gus_path"],
        "framegen_section": spec["framegen_section"],
    }


def _client_running(game, spec):
    return bool(cpu.client_procs({"path": game["install_path"], "spec": spec}))


fps_monitor = sensors.FrameRateMonitor(sensors.find_presentmon())

state = {"spec": None, "game": None, "autopilot": False, "latest": {}, "bench": None}
state_lock = threading.Lock()


def _init_state():
    spec = games.get(games.default_id())
    state.update(spec=spec, game=_resolve_game(spec) if spec else None)
    state["latest"] = {
        "gpu_name": "Detecting...", "vram_percent": 0.0, "cpu_percent": 0.0,
        "fps": None, "fps_source": "none", "fps_status": "idle", "latency_ms": None,
        "status_message": "Starting up...", "last_auto_action": None,
        "game_id": spec["id"] if spec else None, "game_running": False,
        "games_version": games.version(),
    }


def _active():
    with state_lock:
        return state["game"], state["spec"]


def telemetry_loop(stop):
    tick = 0
    while not stop.is_set():
        game, spec = _active()
        prev = state["latest"]
        fresh = game and (tick % 3 == 0 or prev.get("game_id") != game["id"])
        running = _client_running(game, spec) if fresh else prev.get("game_running", False)
        if spec and prev.get("game_running") and not running and prev.get("game_id") == spec["id"]:
            # Game just closed: saves are consistent on disk now, so snapshot them.
            threading.Thread(target=saves.snapshot, args=(spec,), daemon=True, name="save-backup").start()
        # PresentMon only while the game runs: ETW capture, out-of-process, no injection.
        fps_monitor.set_target(spec["client_exe"] if running and spec else None)
        fps = fps_monitor.fps()
        payload = {
            **nvml.get_gpu_stats(),
            **sensors.get_cpu_stats(),
            "fps": round(fps, 1) if fps is not None else None,
            "fps_source": "presentmon" if fps is not None else "none",
            "fps_status": fps_monitor.status,
            "latency_ms": sensors.get_network_latency_ms() if tick % 3 == 0 else prev.get("latency_ms"),
            "game_id": game["id"] if game else None,
            "game_running": running,
            "games_version": games.version(),
        }
        vram = payload["vram_percent"]
        payload["status_message"] = (
            "VRAM critical - expect stutter when new textures load." if vram >= VRAM_RED else
            "VRAM high - little headroom left." if vram >= VRAM_AMBER else
            "VRAM headroom OK.")

        with state_lock:
            action = None
            if state["autopilot"] and state["game"] is game and spec:
                action = autopilot.check_and_apply(payload, spec)
            payload["last_auto_action"] = action
            state["latest"] = payload
        tick += 1
        stop.wait(1.5)


@asynccontextmanager
async def lifespan(_app):
    _init_state()
    stop = threading.Event()
    loop = threading.Thread(target=telemetry_loop, args=(stop,), daemon=True, name="telemetry")
    loop.start()
    yield
    stop.set()
    loop.join(timeout=5)   # so the loop can't restart PresentMon after we stop it
    fps_monitor.set_target(None)
    cpu.process_assassin(False)   # never leave background apps stuck at Idle


app = FastAPI(title="Nexus Optimize core", lifespan=lifespan)
# Renderer is loaded from file:// -> Origin "null". The token header forces a
# preflight, and the token itself is what actually authorizes a request.
app.add_middleware(CORSMiddleware, allow_origins=["null"], allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", "X-Nexus-Token"])


@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    if not secrets.compare_digest(request.headers.get("x-nexus-token", ""), TOKEN):
        return JSONResponse({"status": "error", "message": "Missing or invalid session token."}, status_code=401)
    return await call_next(request)


def _result(ok, message, **extra):
    return {"status": "success" if ok else "error", "message": message, **extra}


@app.exception_handler(Exception)
async def unexpected_error(_request, exc):
    return JSONResponse(_result(False, f"Unexpected error: {exc}"), status_code=500)


@app.get("/health")
def health():
    return {"status": "ok"}


def _games_payload(found):
    game, _ = _active()
    return {
        "active": game["id"] if game else None,
        "version": games.version(),
        "games": sorted((games.summary(s) for s in found.values()), key=lambda g: g["name"].lower()),
    }


@app.get("/games")
def list_games():
    return _games_payload(games.discover())


@app.post("/games/rescan")
def rescan_games():
    found = games.discover(refresh=True)
    return {**_games_payload(found), **_result(True, f"Library scan: {len(found)} game(s) found.")}


@app.get("/game")
def get_game():
    return _active()[0]


@app.post("/game")
def select_game(body: dict = Body(...)):
    game_id = body.get("id")
    spec = games.get(game_id) if isinstance(game_id, str) else None
    if not spec:
        return JSONResponse(_result(False, f"Unknown game '{game_id}'."), status_code=400)
    game = _resolve_game(spec)
    with state_lock:
        state["spec"], state["game"] = spec, game
        autopilot.reset()
    msg = f"Target game switched to {game['name']}."
    if not game["installed"]:
        msg += " (install folder not found)"
    return _result(True, msg, game=game)


@app.get("/telemetry")
def get_telemetry():
    with state_lock:
        return state["latest"]


@app.post("/autopilot")
def set_autopilot(body: dict = Body(...)):
    enabled = bool(body.get("enabled", False))
    with state_lock:
        state["autopilot"] = enabled
    return {"status": "success", "enabled": enabled}


@app.post("/optimize/power")
def toggle_power_plan(body: dict = Body(...)):
    high = bool(body.get("high_performance", False))
    label = "High Performance" if high else "Balanced"
    ok = set_power_plan(high)
    return _result(ok, f"Power plan switched to {label}." if ok else f"Could not switch power plan to {label}.")


@app.get("/optimize/assassin")
def assassin_status():
    return cpu.assassin_status()


@app.post("/optimize/assassin")
def toggle_assassin(body: dict = Body(...)):
    return _result(*cpu.process_assassin(bool(body.get("enabled", False))), **cpu.assassin_status())


@app.post("/optimize/fix")
def manual_fix(body: dict = Body(...)):
    fix = body.get("fix")
    game, spec = _active()
    if not spec:
        return JSONResponse(_result(False, "No game selected."), status_code=400)
    if fix == "cpu_priority":
        ok, msg = cpu.boost_client(spec)
    elif fix == "fg_cap":
        if _client_running(game, spec):
            ok, msg = False, f"Close {game['name']} first - Unreal rewrites GameUserSettings.ini on exit."
        else:
            ok, msg = ini.cap_frame_generation(spec["gus_path"], spec["framegen_section"])
    else:
        return JSONResponse(_result(False, f"Unknown fix '{fix}'."), status_code=400)
    return _result(ok, msg)


@app.post("/launch_optimized")
def launch_optimized():
    spec = _active()[1]
    if not spec:
        return JSONResponse(_result(False, "No game selected."), status_code=400)
    return _result(*cpu.launch_optimized(spec))


@app.get("/launch_optimized/status")
def launch_optimized_status():
    return cpu.get_status()


@app.get("/system/resources")
def system_resources():
    stats = sensors.get_system_stats()
    eff = cpu._efficiency_classes(len(stats["cpu_per_core"]))
    hybrid = all(e >= 0 for e in eff) and len(set(eff)) > 1
    return {**stats, **nvml.get_gpu_stats(),
            "core_types": [("E" if e == min(eff) else "P") if hybrid else "" for e in eff]}


@app.get("/backups")
def list_backups():
    return {"backups": backup.list_backups()[:200]}


@app.post("/backups/restore")
def restore_backup(body: dict = Body(...)):
    return _result(*backup.restore(body.get("id", "")))


@app.get("/mods/live")
def live_mods():
    game, spec = _active()
    if not spec:
        return {"game_id": None, "mods": []}
    return {
        "game_id": spec["id"],
        "mods": live.installed(spec["id"]),
        "anticheat": live.anticheat(spec["install_path"]),
        "pak_dir": live.pak_dir(spec) if spec.get("profile") == "unreal" else None,
        "has_reshade": os.path.exists(game["config_path"]),
        "config_dir": os.path.dirname(spec["gus_path"]) if spec.get("gus_path") else spec["install_path"],
    }


@app.post("/mods/live/apply")
def apply_live_mod(body: dict = Body(...)):
    game, spec = _active()
    kind, paths = body.get("kind"), body.get("paths")
    if kind not in live.KINDS or not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return JSONResponse(_result(False, "Unknown mod kind or bad file list."), status_code=400)
    if not spec:
        return JSONResponse(_result(False, "No game selected."), status_code=400)
    ok, msg, mod = live.apply(spec, kind, paths, target=body.get("target"), running=_client_running(game, spec))
    return _result(ok, msg, mod=mod)


@app.post("/mods/live/remove")
def remove_live_mod(body: dict = Body(...)):
    game, spec = _active()
    if not spec:
        return JSONResponse(_result(False, "No game selected."), status_code=400)
    return _result(*live.remove(spec, str(body.get("id")), running=_client_running(game, spec)))


# ---------------------------------------------------------------- save-game backups
@app.get("/saves")
def list_saves():
    _, spec = _active()
    if not spec:
        return {"game_id": None, "dirs": [], "snapshots": []}
    return {"game_id": spec["id"], "dirs": saves.save_dirs(spec), "snapshots": saves.snapshots(spec["id"])}


@app.post("/saves/folder")
def set_save_folder(body: dict = Body(...)):
    _, spec = _active()
    if not spec:
        return _no_game()
    path = body.get("path")
    return _result(*saves.set_folder(spec["id"], path if isinstance(path, str) and path else None))


@app.post("/saves/backup")
def backup_saves():
    game, spec = _active()
    if not spec:
        return _no_game()
    ok, msg, snap = saves.snapshot(spec, running=_client_running(game, spec))
    return _result(ok, msg, snapshot=snap)


@app.post("/saves/restore")
def restore_saves(body: dict = Body(...)):
    game, spec = _active()
    if not spec:
        return _no_game()
    return _result(*saves.restore(spec, str(body.get("id")), running=_client_running(game, spec)))


# ---------------------------------------------------------------- Benchmark Lab
@app.get("/bench")
def bench_runs():
    _, spec = _active()
    rec = state["bench"]
    return {"recording": rec["label"] if rec else None, "runs": bench.runs(spec["id"]) if spec else []}


@app.post("/bench/start")
def bench_start(body: dict = Body(...)):
    game, spec = _active()
    if not spec:
        return _no_game()
    if not state["latest"].get("game_running"):
        return JSONResponse(_result(False, f"{game['name']} is not running - start it, then record."), status_code=400)
    label = str(body.get("label") or "").strip()[:80] or time.strftime("Run %H:%M")
    with state_lock:
        state["bench"] = {"label": label, "game_id": spec["id"]}
        fps_monitor.start_recording()
    return _result(True, f"Recording '{label}'. Play the same scene, then press Stop.")


@app.post("/bench/stop")
def bench_stop():
    with state_lock:
        rec, state["bench"] = state["bench"], None
        frames = fps_monitor.stop_recording()
    if not rec:
        return JSONResponse(_result(False, "Nothing is being recorded."), status_code=400)
    run = bench.save(rec["game_id"], rec["label"], frames)
    if not run:
        return JSONResponse(_result(False, f"No frames captured - PresentMon: {fps_monitor.status}"), status_code=400)
    return _result(True, f"Saved '{run['label']}': {run['avg_fps']:.1f} FPS avg, {run['low1_fps']:.1f} 1% low.", run=run)


@app.get("/bench/compare")
def bench_compare(a: int, b: int):
    c = bench.compare(a, b)
    return c if c else JSONResponse(_result(False, "Run not found."), status_code=400)


@app.post("/bench/delete")
def bench_delete(body: dict = Body(...)):
    try:
        ok = bench.delete(int(body.get("id")))
    except (TypeError, ValueError):
        ok = False
    return _result(ok, "Run deleted." if ok else "Run not found.")


# ---------------------------------------------------------------- archive mods (ON/OFF toggles)
DOWNLOADS = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize", "downloads")


def _no_game():
    return JSONResponse(_result(False, "No game selected."), status_code=400)


@app.get("/mods/archive")
def archive_mods():
    _, spec = _active()
    return {"game_id": spec["id"] if spec else None, "mods": manager.mods(spec["id"]) if spec else []}


@app.post("/mods/archive/install")
def install_archive(body: dict = Body(...)):
    game, spec = _active()
    path = body.get("path")
    if not spec:
        return _no_game()
    if not isinstance(path, str) or not os.path.isfile(path):
        return JSONResponse(_result(False, "Pick an existing archive file."), status_code=400)
    ok, msg, mod = manager.install(spec, path, running=_client_running(game, spec))
    return _result(ok, msg, mod=mod)


@app.post("/mods/archive/toggle")
def toggle_archive_mod(body: dict = Body(...)):
    game, spec = _active()
    if not spec:
        return _no_game()
    return _result(*manager.set_enabled(spec, str(body.get("id")), bool(body.get("enabled")),
                                        running=_client_running(game, spec)))


@app.post("/mods/archive/uninstall")
def uninstall_archive_mod(body: dict = Body(...)):
    game, spec = _active()
    if not spec:
        return _no_game()
    return _result(*manager.uninstall(spec, str(body.get("id")), running=_client_running(game, spec)))


# ---------------------------------------------------------------- Mod Bisect (archive mods only)
def _bad_request(msg):
    return JSONResponse(_result(False, msg), status_code=400)


def _bisect_apply(spec, on, suspects):
    """Suspects in `on` ON, the other suspects OFF (OFF first, so an ON never clashes with
    a file an outgoing mod still owns). Returns the toggle errors."""
    errors = []
    for mod_id in sorted(suspects, key=lambda m: m in on):
        ok, msg = manager.set_enabled(spec, mod_id, mod_id in on)
        if not ok:
            errors.append(msg)
    return errors


def _bisect_result(msg, errors, **extra):
    return _result(not errors, msg + (" Problems: " + " ".join(errors) if errors else ""), **extra)


def _bisect_guard():
    """(spec, None) if a bisect step may toggle mods now, else (None, error response)."""
    game, spec = _active()
    if not spec:
        return None, _no_game()
    if _client_running(game, spec):
        return None, _bad_request(f"Close {game['name']} first - its mod files are in use.")
    return spec, None


@app.get("/mods/bisect")
def bisect_state():
    _, spec = _active()
    return bisect.get_state(spec["id"]) if spec else {"game_id": None, "active": False}


@app.post("/mods/bisect/start")
def bisect_start(body: dict = Body(...)):
    ids = body.get("ids")
    _, spec = _active()
    known = {m["id"] for m in manager.mods(spec["id"])} if spec else set()
    if not isinstance(ids, list) or len(set(map(str, ids))) != len(ids) or not all(i in known for i in ids):
        return _bad_request("Pick installed archive mods to bisect.")
    spec, err = _bisect_guard()
    if err:
        return err
    ok, msg = bisect.start(spec["id"], ids)
    if not ok:
        return _bad_request(msg)
    s = bisect.get_state(spec["id"])
    errors = _bisect_apply(spec, set(s["testing_mods"]), ids)
    return _bisect_result(f"Step 1: {len(s['testing_mods'])} of {len(ids)} mods ON, the rest OFF. "
                          "Launch the game, then report whether it works.", errors, **s)


@app.post("/mods/bisect/report")
def bisect_report(body: dict = Body(...)):
    works = body.get("works")
    if not isinstance(works, bool):
        return _bad_request("Report works: true or false.")
    spec, err = _bisect_guard()
    if err:
        return err
    ok, res = bisect.report(spec["id"], works)
    if not ok:
        return _bad_request(res)
    s = bisect.get_state(spec["id"])
    suspects = s["good_mods"] + s["bad_mods"]
    if res["finished"]:
        names = {m["id"]: m["name"] for m in manager.mods(spec["id"])}
        errors = _bisect_apply(spec, set(suspects) - {res["culprit"]}, suspects)
        msg = (f"Found it: '{names.get(res['culprit'], res['culprit'])}' breaks the game. "
               "It is left OFF; every other mod is back ON.")
    else:
        errors = _bisect_apply(spec, set(res["testing_mods"]), suspects)
        msg = (f"Step {s['step']}: {len(res['testing_mods'])} of {len(s['bad_mods'])} remaining suspects ON. "
               "Launch the game, then report again.")
    return _bisect_result(msg, errors, **res)


@app.post("/mods/bisect/abort")
def bisect_abort():
    spec, err = _bisect_guard()
    if err:
        return err
    s = bisect.get_state(spec["id"])
    if not s["active"]:
        return _bad_request("No active bisect session.")
    bisect.abort(spec["id"])
    suspects = s["good_mods"] + s["bad_mods"]
    return _bisect_result("Bisect stopped - every mod is back ON.", _bisect_apply(spec, set(suspects), suspects))


# ---------------------------------------------------------------- Config Time Machine
def _config_dir():
    _, spec = _active()
    return os.path.dirname(spec["gus_path"]) if spec and spec.get("gus_path") else None


def _config_file(name):
    """Path of an existing .ini directly inside the active game's config folder, else None."""
    folder = _config_dir()
    if not folder or not isinstance(name, str) or os.path.basename(name) != name or not name.lower().endswith(".ini"):
        return None
    path = os.path.join(folder, name)
    return path if os.path.isfile(path) else None


@app.get("/config/ini")
def config_files():
    folder = _config_dir()
    if not folder or not os.path.isdir(folder):
        return {"dir": folder, "files": []}
    history = backup.list_backups()
    files = []
    for name in sorted(n for n in os.listdir(folder) if n.lower().endswith(".ini")):
        path = os.path.join(folder, name)
        pin = ini.pinned(path)
        files.append({"name": name, "path": path, "locked": not os.access(path, os.W_OK),
                      "pinned": pin is not None, "reverted": ini.is_reverted(path, pin) if pin else None,
                      "history": [h for h in history if os.path.normcase(h["original"]) == os.path.normcase(path)]})
    return {"dir": folder, "files": files}


@app.post("/config/ini/lock")
def config_lock(body: dict = Body(...)):
    path, locked = _config_file(body.get("name")), bool(body.get("locked"))
    if not path:
        return _bad_request("Unknown config file.")
    try:
        ini.lock_ini(path, readonly=locked)
    except OSError as e:
        return _bad_request(f"Could not change {body['name']}: {e}")
    return _result(True, f"{body['name']} locked (read-only) - the game can no longer rewrite it." if locked
                   else f"{body['name']} unlocked.")


@app.post("/config/ini/pin")
def config_pin(body: dict = Body(...)):
    path = _config_file(body.get("name"))
    if not path:
        return _bad_request("Unknown config file.")
    ini.pin(path)
    return _result(True, f"Pinned the current {body['name']}. You'll see 'reverted' if the game changes it.")


# ---------------------------------------------------------------- Nexus Mods
def _nexus_error(e):
    return JSONResponse(_result(False, str(e)), status_code=400)


def _nexus_key():
    key = nexus.get_key()
    if not key:
        raise nexus.NexusError("Add your Nexus Mods API key first (nexusmods.com > Site preferences > API Keys).")
    return key


@app.get("/nexus/status")
def nexus_status():
    _, spec = _active()
    return {"has_key": bool(nexus.get_key()), "user": state.get("nexus_user"),
            "domain": nexus.domain_for(spec) if spec else None, "limits": nexus.limits}


@app.post("/nexus/key")
def nexus_set_key(body: dict = Body(...)):
    key = str(body.get("key") or "").strip()
    try:
        if key:
            user = nexus.validate(key)
            state["nexus_user"] = {"name": user.get("name"), "is_premium": bool(user.get("is_premium"))}
        else:
            state["nexus_user"] = None
        nexus.set_key(key or None)
    except nexus.NexusError as e:
        return _nexus_error(e)
    return _result(True, f"Signed in as {state['nexus_user']['name']}." if key else "API key removed.",
                   user=state["nexus_user"])


@app.get("/nexus/mods")
def nexus_browse(domain: str, feed: str = "trending"):
    try:
        return {"mods": nexus.browse(_nexus_key(), domain, feed), "limits": nexus.limits}
    except nexus.NexusError as e:
        return _nexus_error(e)


@app.get("/nexus/files")
def nexus_files(domain: str, mod_id: int):
    try:
        return {"files": nexus.files(_nexus_key(), domain, mod_id)}
    except nexus.NexusError as e:
        return _nexus_error(e)


@app.get("/nexus/updates")
def nexus_updates():
    _, spec = _active()
    if not spec:
        return {"updates": []}
    try:
        return {"updates": nexus.check_updates(_nexus_key(), nexus.domain_for(spec), manager.mods(spec["id"])),
                "limits": nexus.limits}
    except nexus.NexusError as e:
        return _nexus_error(e)


@app.post("/nexus/install")
def nexus_install(body: dict = Body(...)):
    """{domain, mod_id, file_id} (Premium) or {domain, nxm} (free: 'Mod Manager Download')."""
    game, spec = _active()
    if not spec:
        return _no_game()
    try:
        key = _nexus_key()
        if body.get("nxm"):
            req = nexus.parse_nxm(body["nxm"])
            if req["domain"] != body.get("domain"):
                raise nexus.NexusError(f"That link is for '{req['domain']}', but the selected game is '{body.get('domain')}'.")
        else:
            req = {"domain": body.get("domain"), "mod_id": int(body.get("mod_id")),
                   "file_id": int(body.get("file_id")), "key": None, "expires": None}
        meta = next((f for f in nexus.files(key, req["domain"], req["mod_id"]) if f.get("file_id") == req["file_id"]), None)
        if not meta:
            raise nexus.NexusError("That file is no longer listed on Nexus Mods.")
        url = nexus.download_url(key, req["domain"], req["mod_id"], req["file_id"], req["key"], req["expires"])
        path = nexus.download(url, DOWNLOADS, meta.get("size_in_bytes"))
    except (TypeError, ValueError):
        return _nexus_error("Bad mod or file id.")
    except nexus.NexusError as e:
        return _nexus_error(e)
    try:
        source = {"nexus": req["domain"], "mod_id": req["mod_id"], "file_id": req["file_id"], "version": meta.get("version"),
                  "uploaded": meta.get("uploaded_timestamp")}
        ok, msg, mod = manager.install(spec, path, running=_client_running(game, spec), source=source)
    finally:
        os.remove(path)   # contents now live in staging
    return _result(ok, msg, mod=mod)
