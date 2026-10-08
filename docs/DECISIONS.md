# Decisions

## D-001 — Rebuild in place, keep a v1 copy (2026-09-18)
v1 lived in `Documents\NexusApp` with no git. Before Phase 0 a full source copy (without `venv`/`node_modules`)
was saved to `Documents\NexusApp_v1_backup_20260918`. The legacy `DLSS5_Optimizer` PowerShell tool, including
its config backups and CSV logs, was moved out of the source tree to
`%APPDATA%\NexusOptimize\legacy_v1\DLSS5_Optimizer`. User data is kept, just not in the app.

## D-002 — §0.3 audit: what the evidence actually shows (2026-09-18)
SPEC §0.3 says the v1 keys were invented. Checked against files on this machine:

| Key | Evidence | Verdict | Action |
|---|---|---|---|
| `[RenoDX.DLSS5] NRUICorrection` | String present in `Windrose\R5\Binaries\Win64\renodx-dlss5.addon64`. 007's ReShade.ini had `NRUICorrection=1` *before* v1 ever wrote to it (earliest backup `backups\007\20260917_204520`). | **Real key** of the installed RenoDX DLSS5 addon. What the value does, and whether `0` reduces menu stutter, is **unverified**. | Removed from all auto/manual paths. Can come back as a data-pack tweak only after a measured before/after (Phase 2 telemetry). |
| `[RenoDX.DLSS5] EnableHooks` | String present in `renodx-dlss5.addon64` (10×). | Real addon key; v1 set it to its existing value. | Removed (no reason for the app to manage it). |
| `[RenoDX.DLSS5] PipelinedHandoff` | **Not found** in any addon binary in the Windrose folder. | **Invented.** | Deleted. |
| `[OVERLAY] AutoSavePreset` | Real ReShade setting, but it only controls preset saving. v1 labelled it an "anti-tamper" measure. | Mislabelled. | Deleted. |
| `FrameGeneratorMode` (GUS) | Present in the game's own `%LOCALAPPDATA%\R5\Saved\Config\Windows\GameUserSettings.ini` (`[/Script/R5.R5SettingsLocal] FrameGeneratorMode=On4x`) and as a string in `Windrose-Win64-Shipping.exe`. | **Real game setting.** v1 lowered 3×/4× to 2× (it never forced FG *on*). "VRAM Guard" was a misleading name. | Kept as a **manual** button, renamed "Cap Frame Generation at 2×", source cited in `core/optimize/ini.py`. No longer auto-fired on VRAM thresholds or on launch/exit. The real VRAM guard (UE streaming pool, §6.4) is still Phase 3. |
| Fake mod catalog (`FilmicCurve`, `Bloom`, `ShadowResolutionMultiplier`, `LODStreamingDistance`, `HUDExpandedCombatInfo`, invented download counts) | No source. | Invented. | Deleted, along with the `/mods`, `/mods/inject` and `/inject` routes. |
| Simulated FPS / frame drops | v1 generated random numbers (labelled "simulated"). | Violates §2.6. | Deleted. The API returns `fps: null`, and the UI shows "—" until PresentMon (Phase 2). |
| Full-screen auto-add every ~6 s | Any full-screen app became a "game". | §0.3 / §5.3 | Deleted. Confidence-scored suggestions are Phase 1. |
| Hardcoded "CPU 20–23 = E-cores" | The Python port already used `GetSystemCpuSetInformation` → `EfficiencyClass`. Only the legacy .ps1 hardcoded cores. | Python side already correct. | .ps1 moved out (D-001). A test asserts server cores are the detected lowest efficiency class. |

**Correction to SPEC §0.3 for the record:** `NRUICorrection` is not invented, and `FrameGeneratorMode=On` does not
"force FG on". Both are real keys. The unproven part is the *claimed effect*, so they were pulled from automation rather
than treated as fabrications.

## D-003 — Transport security for the core service (2026-09-18)
Flask → FastAPI + uvicorn. Electron generates a 32-byte random token per session and passes it via `NEXUS_TOKEN`;
every route except `/health` requires `X-Nexus-Token` (constant-time compare). CORS allows only `Origin: null`
(the `file://` renderer). This replaces v1's "POST must be JSON" CSRF trick.

## D-004 — UI stays vanilla JS for Phase 0 (2026-09-18)
§3.1 targets React + TS + Tailwind. Phase 0 only moves files into `ui/`. The React rewrite starts with the
Phase 1 Library screen, where the UI is being rebuilt anyway, so nothing gets written twice.

## D-005 — Out of scope, noted
The Windrose install contains `OnlineFix64.dll` / `OnlineFix.ini` / a replacement `winmm.dll` (a third-party
online-play patch), and `fix anything\Windrose_Steam_Coop_Fix` bundles the same thing. Per §2.4 the app never
installs, manages or special-cases these files. Phase 1 anti-cheat/online-risk detection should treat such
folders as "modified install". It must not treat them as a supported configuration.

## D-006 — Archive mods + Nexus Mods, first cut (2026-09-19)
- **Auth:** personal API key stored with `keyring` (Windows Credential Manager), not SSO. SSO needs an app slug registered
  with Nexus (§7.3 allows the key during development). The key is never returned by the API.
- **Routing is deliberately conservative:** .pak/.utoc/.ucas/.sig → `~mods`; .ini/.cfg only when a file of that name already
  exists in the game's Saved/Config folder; ReShade presets (have `Techniques=`) next to ReShade.ini. Everything else is
  listed as skipped, not guessed. §7.2's "user maps the archive tree once" wizard is not built yet.
- **No code files:** .dll/.exe/.asi/... are never placed, so UE4SS / BepInEx / DLL plugins are out of scope for now (§7.2).
  They load code into the game process; revisit only with an explicit decision per AGENTS.md rule 4.
- **Copies, not hardlinks** (§7.1 allows either). Staging keeps the pristine copy; OFF deletes placed files and restores backups.
- **Nexus v1 has no search endpoint.** Browse = trending / latest added / latest updated. Search needs the v2 GraphQL API later.
- **Downloads are synchronous** (no progress bar or resume yet, §7.3). Size is verified against Nexus' `size_in_bytes`.
- **Anti-cheat games** get config files only, same rule as live mods (§2.3). Adult-flagged mods are hidden from the browser.
- Archive mods and quick-swap (`core/mods/live.py`) mods: archive ON refuses files a quick swap owns. The reverse check
  (quick swap over an ON archive file) is not enforced yet.

## D-007 — Real FPS via PresentMon / ETW (2026-09-19)
- **Source:** Intel PresentMon run as a child process with `--process_name <client_exe> --output_stdout`; FPS =
  1000 / mean `MsBetweenPresents` over a 2 s window. PresentMon reads the DxgKrnl/DXGI ETW events Windows already
  emits — nothing is injected into, hooked in, or read from the game process, so it is safe on anti-cheat games.
- **Rejected:** RTSS shared memory (reading it is passive, but RTSS itself hooks the game's Present to fill it) and
  DXGI `GetFrameStatistics` (only works on a swap chain you own). DLL injection / game-memory reads: never.
- **Lifecycle:** PresentMon runs only while the client exe is running; stopped on game exit and app shutdown.
  Fixed ETW session name + `--stop_existing_session` cleans up after a crash.
- **Privileges:** the app never elevates. ETW capture needs admin or membership in *Performance Log Users*;
  without it the API returns `fps: null` and `fps_status` carries the PresentMon error + that hint (shown on hover).
- **Binary:** not bundled/downloaded yet. Looked up in `NEXUS_PRESENTMON`, `third_party/PresentMon*.exe`, then PATH (§3.2).
- Lives in `core/telemetry/sensors.py` rather than the separate `presentmon.py` from §3.2 (small enough; split when
  1% / 0.1% lows and session reports arrive with Doctor, §4.6).

## D-008 — "Phase 3" feature epic: six approved features (2026-09-19)
The user approved six features beyond SPEC §11. **Naming:** the user calls this epic "Phase 3". That is *not*
SPEC §11 Phase 3 (Autopilot), which is unchanged and still open. In this repo the epic is "Epic P3".
Every feature follows AGENTS.md: TDD, no simulated data, no injection/admin without asking, acceptance output in TESTLOG.

| # | Feature | Block | Status |
|---|---|---|---|
| 1 | **Benchmark Lab** — record PresentMon runs to SQLite, compare A/B | 1 | Built (D-009). Real-game run still open. |
| 2 | **Auto Save-Game Backup** — zip saves on game exit, one-click restore | 1 | Built (D-010). Accepted on real Windrose saves. |
| 3 | **Config Time Machine** — per-file history, lock (read-only), pin + revert detection | 2 | Built (D-011). UI clicks on real files not yet accepted. |
| 4 | **Mod Bisect** — binary search over archive mods | 2 | Built (D-011). Real-game run still open. |
| 5 | **Modpack Share Codes** — NX-xxxx codes for a friend's Nexus modlist | 2 | Built (D-012). Real two-PC install still open. |
| 6 | AI Bottleneck Resolver | 3 | Not started. Needs Benchmark Lab data first. Decide the model/provider and what data leaves the machine before building. |

## D-009 — Benchmark Lab (2026-09-19)
- **Data:** PresentMon `MsBetweenPresents` only (D-007). `FrameRateMonitor.start_recording()/stop_recording()` collects
  every frame time between Start and Stop. No PresentMon, or no frames → the run is refused, never estimated.
- **Metrics** (`core/telemetry/bench.py`): avg FPS = 1000·n/Σms; 1% / 0.1% low = 1000 / mean of the slowest 1% / 0.1%
  frame times (at least one frame); stutters = frames > 2.5× median (§4.6).
- **Storage:** the §3.1 SQLite DB (`%APPDATA%\NexusOptimize\nexus.db`), table `bench_runs`. Raw frame times are stored as
  JSON with each run, so Doctor/graphs can recompute metrics later without trusting old summaries.
- Recording needs the game running (the loop only runs PresentMon then). Comparison is B relative to A, absolute and %.

## D-010 — Auto save-game backup (2026-09-19)
- **Which folders:** checked on this machine. Windrose's real saves are a **RocksDB in `R5\Saved\SaveProfiles`** (185 files,
  11 MB). `Saved\SaveGames` holds only `EnhancedInputUserSettings.sav`. So for Unreal games the backup covers every
  `Saved\Save*` folder (the UE default `SaveGames` plus variants like `SaveProfiles`), found next to the known
  `Saved\Config`. Other games get no guess: the user picks the folder (`folders.json`).
- **Only while the game is closed.** An open database copied mid-write is a torn backup. Auto snapshot fires on
  the running → stopped transition in the telemetry loop. Manual backup and restore are refused while the game runs.
  Pre-launch state = the last exit snapshot, so no separate pre-launch hook is needed.
- **Format:** one zip per snapshot in `%APPDATA%\NexusOptimize\savegames\<game_id>\`. Written as `.part`, then renamed.
  The comment holds `{created, files, fingerprint}`. The fingerprint is a per-file **content** hash: size + mtime missed a
  same-size rewrite within one Windows clock tick (caught by a test). Unchanged saves are skipped; the newest 20 are kept.
- **Restore** snapshots the current saves first, fully unpacks to a staging dir, then swaps folders by rename (a locked
  file fails cleanly instead of leaving a half-deleted save).
- Distinct from `core/safety/backup.py`, which covers config files the app itself changes.

## D-011 — Config Time Machine + Mod Bisect (2026-09-19)
- **Backends** by Antigravity (`core/optimize/ini.py` lock/hash, `core/mods/bisect.py`); routes + UI + tests by Claude.
- **Time Machine** covers the `.ini` files directly in the active game's config folder (`dirname(gus_path)`); routes take a
  bare file name only, so nothing outside that folder can be locked or pinned. History = `core/safety/backup.py` entries
  for the file (restore reuses `/backups/restore`). Lock = read-only attribute; the app's own INI writes now put it back.
  Pins (sha256) persist in `%APPDATA%\NexusOptimize\ini_pins.json`.
- **Bisect** works on archive mods only (live mods have no OFF state). Each step: suspects under test ON, other suspects OFF;
  finish: culprit OFF, all others ON; abort: all suspects ON. Refused while the game runs. Assumes one culprit.
  State lives in the §3.1 DB (`bisect_state`, created on demand). Fixed: a "still broken" report dropped the cleared half,
  so those mods could not be switched back on.
- **FPS '—' root cause (2026-09-19):** `third_party/PresentMon.exe` (2.5.1) is found and started, but exits with
  `access denied` — this account is not admin nor in *Performance Log Users*. The status only showed PresentMon's last
  help line, hiding the error + hint; it now keeps the `error:` line. 2.5.1's default CSV still has `MsBetweenPresents`.

## D-012 — Modpack share codes (2026-10-08)
- **Hosting:** no server of our own. `core/mods/share.py` posts the pack to **dpaste.org** (`POST /api/`, `format=url`,
  `expires=604800`, so codes last one week) and the code is `NX-<dpaste slug>`. Import reads `https://dpaste.org/<slug>/raw`.
  The host is fixed, so a code can't point the app anywhere else. If dpaste is down or deletes the paste, the code stops working.
- **What leaves the machine:** game domain, then per mod: Nexus mod id, file id, name, version, load-order index. No paths, no
  local archives. Only **enabled** mods installed from Nexus are shared. Enabled local mods are counted and reported as left out.
- **Load order** = manifest order. The archive manager deploys in that order and refuses file clashes, so there is no
  separate order field to read.
- **Import is untrusted input:** the slug has to match `[A-Za-z0-9]{2,32}`, the paste is capped at 256 KB, the schema is
  version 1, ids must be non-negative ints (bools refused), names are cut to 120 characters and the domain is checked by
  `nexus._DOMAIN`. A pack for another game is refused.
  The UI only *lists* the pack (installed / different file / not installed). Each install goes through the existing
  `/nexus/install`, so free accounts still get sent to Nexus' "Mod Manager Download" page. Nothing auto-downloads.
