# Test log

Machine: i7-14650HX · NVIDIA GeForce RTX 5060 Laptop GPU (8151 MB, driver 616.92) · Windows 11 26200 ·
Python 3.12.10 · Node 24.19.0 · Electron 44.

---

## Phase 0 — Cleanup · 2026-09-18 · **PASS**

Acceptance (SPEC §11): *app starts, no feature references invented keys (`grep` proves it).*

### 1. Grep proof: invented keys
Pattern: `NRUICorrection|PipelinedHandoff|AutoSavePreset|EnableHooks|FilmicCurve|LODStreamingDistance|HUDExpandedCombatInfo|ShadowResolutionMultiplier|get_simulated_frame_stats|note_fullscreen`
```
== v1 (backup) hits:
  NexusApp_v1_backup_20260918/backend/app.py
  NexusApp_v1_backup_20260918/backend/config_injector.py
  NexusApp_v1_backup_20260918/backend/DLSS5_Optimizer/DLSS5_Optimizer.ps1
  NexusApp_v1_backup_20260918/backend/games.py
  NexusApp_v1_backup_20260918/backend/mod_manager.py
  NexusApp_v1_backup_20260918/backend/telemetry.py
== v2 core/ ui/ hits:
  (none)
== nvidia-smi / flask references in v2:
NexusApp/core/telemetry/nvml.py:1:"""GPU telemetry through NVML (nvidia-ml-py) - replaces nvidia-smi polling.   <- docstring only
```
The only key still written to a game file is `FrameGeneratorMode`, with its source cited (see DECISIONS D-002).
`test_every_ini_key_written_cites_a_source` enforces this.

### 2. Automated tests: `npm test`
```
26 passed, 2 warnings in 0.28s
```
Covers: every invented key absent (11 parametrized), FG cap keeps UTF-16LE / UTF-8 / UTF-8-BOM encodings and writes an
exact byte backup, FG cap leaves `On`/`Off` untouched (no backup written), `/health` open, 4 GET routes + POST
return 401 without the token or with a wrong one, telemetry `fps is None`, removed routes (`/inject`, `/mods`,
`/mods/inject`) return 404, server CPUs = detected lowest EfficiencyClass and disjoint from client CPUs.
Warnings are Starlette's deprecation notices about httpx in TestClient; not ours.

### 3. Core over real HTTP (standalone, port 5055)
```
health: {"status":"ok"}
no token: {"status":"error","message":"Missing or invalid session token."} [401]
telemetry: {"gpu_name":"NVIDIA GeForce RTX 5060 Laptop GPU","vram_percent":5.0,"vram_used_mb":405,"vram_total_mb":8151,
            "gpu_util":0,"gpu_temp_c":42,"cpu_percent":3.3,"fps":null,"fps_source":"none","latency_ms":62.2,
            "game_id":"windrose","game_running":false,...,"status_message":"VRAM headroom OK."}
games: 007 First Light (Glacier), Forza Horizon 6 (Unknown engine), Windrose (Unreal Engine 5)
CORS preflight Origin:null            -> 200, access-control-allow-origin: null
CORS preflight Origin:evil.example    -> 400
```

### 4. App starts (Electron → spawns core with per-session token)
`npm start` with `NEXUS_SCREENSHOT` set → `[NOM] screenshot saved`. Screenshot: `testlog/phase0-dashboard.png`.
It shows: pill "connected" (renderer authenticated with the token), GPU = RTX 5060 Laptop, VRAM 5 %, GPU load 0 %,
temp 42 °C, latency 62 ms, FPS "—" (no simulated value), target game Windrose · UE.
Closing the window (WM_CLOSE) → `curl /health` fails → the backend exits with the app.

### Not tested / known gaps (carried forward)
- **Launch Server Optimized** was not run end-to-end with Windrose in this phase. The code path lost only its config
  writes; the CPU isolation logic is unchanged from v1. Phase 3's benchmark covers it.
- **Cap Frame Generation at 2×** is only verified on temp files (unit tests), not yet on the live Windrose
  GameUserSettings.ini (currently `On4x`). Nothing was written to live game files during Phase 0.
- Power-plan off-switch always selects Balanced, not the user's previous plan (Phase 3 snapshot/revert).
- Game discovery on this PC: 3 games. Whether that's *every* installed game is Phase 1's test.
- Forza Horizon 6 shows "Unknown engine" (ForzaTech isn't in the signature list; Phase 1).

## Upgrade (overlay, live mods, system resources, backup & rollback) — 2026-09-19

### 1. Automated tests: `npm test`
```
48 passed, 2 warnings in 2.80s
```
Covers backup → restore byte-exact, restore path traversal refused, pak/reshade/config apply + remove restore the
game tree byte-exact, anti-cheat blocks pak/reshade (config allowed), running game blocks swaps, two mods can't own
one file, unknown kind rejected, system stats are real readings, new routes 401 without token.
Note: system `python` lacks `pynvml`; tests must run under `.venv` (as `npm test` does).

### 2. Bug found and fixed: `/system/resources` reported every core as 0.0 %
psutil 7 keeps the `cpu_percent(interval=None)` baseline **per thread** (`psutil/__init__.py:1725`); FastAPI serves
the sync route from pool threads, so each new thread's first read was a fake 0.0. Now a 0.1 s blocking sample
(off the event loop). Regression test: `test_per_core_is_measured_from_a_fresh_thread` (failed before, passes after).
```
before: {"cpu_per_core":[0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],...
after:  {"cpu_per_core":[16.7,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,16.7,16.7,0.0,16.7,0.0,...],"ram_used_mb":12413,"ram_total_mb":24280,...
```

### 3. Core over real HTTP (port 5000)
```
/mods/live: {"game_id":"windrose","mods":[],"anticheat":null,
             "pak_dir":"d:\\steam\\steamapps\\common\\Windrose\\R5\\Content\\Paks\\~mods","has_reshade":true,
             "config_dir":"C:\\Users\\THUNDERX\\AppData\\Local\\R5\\Saved\\Config\\Windows"}
/backups: {"backups":[]}
/backups without token -> 401
POST /mods/live/apply kind=trainer -> {"status":"error","message":"Unknown mod kind or bad file list."}
```
No process-memory APIs in the tree (`WriteProcessMemory|ReadProcessMemory|CreateRemoteThread|VirtualAllocEx|LoadLibrary`:
0 hits). The only `ctypes` use is the read-only CPU-set query in `core/optimize/cpu.py`.

### 4. App starts
`npm start` → Electron up, core spawned, `/health` ok. `node --check` passes for `app.js`, `main.js`, `preload.js`.

### Not tested / known gaps
- **No screenshot of the new tabs or the overlay window.** Not captured this session; needs
  `NEXUS_SCREENSHOT` run per tab + a manual Ctrl+Shift+O check over Windrose (borderless).
- No live mod was applied to the real Windrose install; apply/remove verified on temp game trees only.
- Overlay FPS stays "—" until PresentMon (Phase 2), by design.

## 2026-09-19 — Compact HUD, archive mods with ON/OFF toggles, Nexus Mods browser

### 1. Tests (TDD: all new tests were run red first, then green)
```
pytest tests/test_modmanager.py tests/test_nexus.py -> 42 passed, 2 warnings in 0.59s
node --test tests/*.test.js                       -> tests 3, pass 3, fail 0
npm test (full suite)                             -> 90 passed, 2 warnings in 3.81s
```
Red-first failures that shaped the code: zip-slip / oversize / non-zip refusals; corrupt-CRC zip raised an
uncaught `BadZipFile` (now a clean error); tampered manifest could `rmtree` outside staging (now refused).

### 2. Real app (isolated Electron instance, CDP-driven, 2558x1504 @ 200% scale)
```
overlay: {"w":196,"h":36,"sx":1076,"sy":8,"sw":1280,"text":"FPS— VRAM— GPU— CPU—"}   right edge 1272 = 1280-8, top 8
mods view: Installed mods / Nexus Mods (key field, Trending|Latest|Updated, domain, nxm toggle) / Quick swaps render
```
The user's own V2 session held port 5000 and the single-instance lock, so the verify instance ran with a separate
`--user-data-dir` and **no working backend**: data panels were empty. Windows rounds the HUD window height to 36 px.

### Not tested / known gaps
- **No live call to api.nexusmods.com** (no API key on this machine). Request shapes are pinned by MockTransport tests only.
- No archive mod installed into the real Windrose tree; ON/OFF verified byte-identical on temp game trees.
- nxm:// registration (opt-in switch) not exercised; SPEC §11 Phase-5 test ("Mod Manager Download" → installed) still open.
- .7z/.rar path untested on this machine (7-Zip not installed); covered only by the "7-Zip missing" message test.

## 2026-09-19 — Epic P3, block 1: Benchmark Lab + Auto Save-Game Backup (D-008..D-010)

### 1. Tests (TDD: all run red first, then green)
```
pytest tests/test_bench.py tests/test_saves.py -> 17 passed
npm test (full suite)                         -> 123 passed, 2 warnings in 17.74s · node: tests 3, pass 3, fail 0
```
Red runs that changed the code: (a) the API test first got an empty recording because the fake PresentMon printed
all frames before Start. Real PresentMon streams, so the test now uses a streaming fake. (b) Pruning test: 5 same-size
rewrites in quick succession gave 1 snapshot, not 3. The size+mtime fingerprint missed real changes, so it is now a content hash.

### 2. Real saves on this machine (Windrose not running; snapshot written to a temp root)
```
save_dirs: ['C:\Users\THUNDERX\AppData\Local\R5\Saved\SaveGames', 'C:\Users\THUNDERX\AppData\Local\R5\Saved\SaveProfiles']
snapshot: True Backed up 186 save file(s). 0.21s {'id': '20260919_103520.zip', 'files': 186, 'size': 4598903}
second: (True, 'Saves unchanged since the last backup.')
zip == live saves (sha256 per file): True 186 files
```

### 3. Real app (isolated Electron instance, CDP-driven, live backend this time)
```
bench view: "Record a run in Windrose …", Runs: "No runs yet." · click Start recording ->
  bench-msg: Windrose is not running - start it, then record.
saves view: Backed up = …\R5\Saved\SaveGames, …\R5\Saved\SaveProfiles · click Back up now ->
  saves-msg: Backed up 186 save file(s).   saves-list: 9/19/2026, 10:36:15 AM  186  4491 KB  Restore
```
Screenshots: `docs/testlog/phase3-benchmark-lab.png`, `docs/testlog/phase3-save-backups.png`.
That real snapshot now sits in `%APPDATA%\NexusOptimize\savegames\windrose\`.

### Not tested / known gaps
- **No real benchmark run.** PresentMon is not installed here and Windrose was not running. Recording → SQLite → compare is
  verified with a streaming fake PresentMon only. Acceptance needs two real runs (e.g. FG 2× vs 4×) on this machine.
- **Auto snapshot on a real game exit** has not been observed yet: the running → stopped transition is covered only by a test
  with a faked process check. **Restore was not run on the real saves**, only on temp trees (byte-identical, pre-restore snapshot kept).
- Non-Unreal games (e.g. Hitman 3) have no auto-detected save folder; the user must pick one.

## Polish pass (2026-09-19): error handling, SQLite, save compression, 1% lows, Process Assassin count

### 1. Test suites on this machine
```
.venv\Scripts\python -m pytest -q tests  ->  164 passed          (157 before; 7 new tests, 1 fixture extended)
node --test tests/*.test.js              ->  tests 3 · pass 3 · fail 0
node --check ui/renderer/app.js          ->  OK
```
Red first: the extended fake PresentMon (`nan`, `inf`, `-1.0` rows) failed 5 tests, including `/telemetry` crashing
on a NaN FPS that can't be serialized. That's the real accuracy bug behind "1% lows": one bad row made every mean NaN.

### 2. Level-9 deflate on the real Windrose saves (read-only, zip written to a temp root)
```
level 9: Backed up 193 save file(s). 0.49s 5787352 bytes
level 6 (previous default): 0.25s 5823594 bytes      raw: 12795123 bytes
```
Only 0.6% smaller: RocksDB `.sst` files are already compressed. It still runs in the background after the game exits.
LZMA was ruled out because Windows Explorer can't open LZMA zips, and users should be able to open a backup by hand.

### Not tested / known gaps
- **UI changes weren't checked in the running Electron app.** That covers the spinners, the error tooltip (`title`), and the "N processes throttled" badge.
  They pass the JS syntax check only.
- A failed *automatic* backup (game exit) returns an error, but nothing shows it in the UI. It used to be an unhandled thread exception.
