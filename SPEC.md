# Nexus Optimize & Mod — Rebuild Specification (v2)

> Drop this file into the repo root as `SPEC.md`. Claude Code reads it together with `AGENTS.md`.
> Rule zero: **nothing is "done" until it has been tested on a real game on this machine and the test result is written in `docs/TESTLOG.md`.**
>
> Note (2026-09-18): parts of §0.3 were checked against the files on this machine. See `docs/DECISIONS.md` D-002. `NRUICorrection` and `FrameGeneratorMode` turned out to be real keys; what was unproven is the effect v1 claimed for them.

---

## 0. Honest reset — why v1 feels broken

Read this before writing any code. v1 failed mostly because it was built on assumptions nobody verified.

### 0.1 What DLSS 5 actually is
- DLSS 5 is **not** a frame-generation feature. It is a *neural rendering* layer that re-lights the final image (skin, hair, materials). It sits on top of the normal DLSS stack (Super Resolution, Frame Generation / Multi Frame Generation, Ray Reconstruction, Reflex).
- NVIDIA says it costs roughly **50–60% performance** in an official implementation, and it is RTX 50-only. It officially launched on 3 Sept 2026 in NBA 2K27; other games are "announced", not shipped.
- If DLSS 5 is running in Windrose, it is almost certainly a **modded / leaked DLL**, not an official implementation. Instability is expected.

### 0.2 The real cause of the 24 FPS collapse (most likely)
Target machine: i7-14650HX, **RTX 5060 Laptop 8 GB**, 24 GB RAM, 180 Hz.
- 8 GB VRAM + neural rendering + frame generation (which itself needs extra VRAM) + high textures = VRAM near 100%.
- Opening a chest/menu loads new UI textures. VRAM overflows, data spills to system RAM over PCIe, and frame time explodes. That is not a "leak" — it's **VRAM saturation**.
- Running the dedicated server at the same time steals CPU and RAM, which makes it worse.
- **No background app can make DLSS 5 free.** What an app *can* do: keep VRAM under budget, pick the right DLSS preset/FG factor, keep the dGPU in direct mode, isolate the server, cap FPS sensibly, and warn the user.

### 0.3 v1 features to delete or rewrite
| v1 feature | Problem | Action |
|---|---|---|
| `NRUICorrection=0` "UI stutter fix" | No evidence this key exists anywhere. Invented. | Delete. |
| `FrameGeneratorMode=On` "VRAM Guard" | Unverified key; forcing FG *on* increases VRAM use. | Replace with real UE streaming-pool guard (§6.4). |
| ReShade `AutoSavePreset=0` "anti-tamper bypass" | That key only controls preset saving. Bypassing game anti-tamper/DRM is off-limits (ban + legal risk). | Delete. If the goal is hiding ReShade's own startup overlay, expose ReShade's own setting for that. |
| Hardcoded "CPU 20–23 = E-cores" | Correct on this chip by luck; wrong on others. | Detect core types via Windows API (§6.2). |
| Poll every 6 s for full-screen apps | Wasteful and misses short-lived launchers. | Event-driven process watcher (§5.2). |
| "Every unknown full-screen app is a game" | Adds browsers/video players. | Require confidence score (§5.3). |

### 0.4 Process rule for the AI agents
v1 was two AIs role-playing PM and Lead Dev, reporting success to each other. From now on:
1. Every feature has an **acceptance test** (in this spec). The agent must run it and paste the output.
2. **No invented config keys.** Any key written to a game file must cite a source (PCGamingWiki page, engine docs, the game's own default config, or a Nexus mod page) in `data/tweaks/*.json` → `source` field.
3. If an agent is not sure a thing is possible, it says so and stops. Uncertainty is not failure; fake success is.

---

## 1. Product vision (one paragraph)

A single desktop app where a PC gamer opens their library, sees every installed game automatically, and for each one gets: (a) **Autopilot** — invisible, reversible optimization that switches on when the game launches and switches off when it closes; (b) **Mods** — browse, install, enable/disable, and update mods (Nexus Mods style) with one click and zero manual file copying; (c) **Wand** — a hotkey quick-panel over the game to toggle live-capable tweaks and mods without alt-tabbing; (d) **Doctor** — plain-language diagnosis of stutter, low FPS, VRAM overflow and crashes, with a fix button next to each finding. Everything the app changes can be undone with one click.

---

## 2. Non-negotiable principles

1. **Reversible everything.** Before any change: snapshot. After game exit: revert session-only changes. Global "Undo timeline" of every change ever made.
2. **Safe by default.** Three tiers: `Safe` (on by default), `Balanced`, `Aggressive` (opt-in, with explanation).
3. **Anti-cheat aware.** If a game ships EasyAntiCheat, BattlEye, Vanguard, Ricochet, nProtect, etc., all injection/live-hook features are **locked** for it, with a visible reason. Config-file and driver-profile tweaks remain available.
4. **Never touch game executables or DRM.** No patching .exe files, no bypassing Denuvo/anti-tamper, no piracy tooling.
5. **Plain language.** Every toggle has a one-line "what this does" and a badge: `Live` / `Needs restart` / `Online-risk` / `Admin`.
6. **Evidence over vibes.** Every optimization shows before/after numbers (avg FPS, 1% low, frame-time spikes, VRAM peak).
7. **Low overhead.** App idle: <1% CPU, <150 MB RAM. During gameplay the app must never cost more than it saves.

---

## 3. Architecture

### 3.1 Stack (keep what exists, restructure it)
- **UI:** Electron + React + TypeScript + Tailwind + Zustand (state) + Recharts (graphs). Frameless window is fine, but keep native-size title-bar controls.
- **Core service:** Python 3.12, **FastAPI** (replaces Flask; gives WebSockets for live telemetry), packaged with PyInstaller as a sidecar exe. UI talks to it over `127.0.0.1` with a random per-session token.
- **Elevated helper:** a tiny separate process that runs as admin only when needed (priority/affinity on protected processes, power plans, standby purge). Main app runs **non-admin**. Communicates via named pipe. This avoids running the whole app elevated.
- **Storage:** SQLite (`%APPDATA%\NexusOptimize\nexus.db`) for games, mods, snapshots, telemetry sessions. JSON data packs in `data/` for game adapters and tweak definitions (versioned, updatable).
- **Installer:** electron-builder NSIS, registers `nxm://` protocol handler, optional "start with Windows (tray)".

### 3.2 Folder layout
```
NexusApp/
  AGENTS.md  SPEC.md  docs/TESTLOG.md  docs/DECISIONS.md
  ui/                      # Electron + React
    src/pages/  Home, Game, ModBrowser, Doctor, Autopilot, Undo, Settings, Onboarding
    src/components/  GameCard, TweakToggle, ModRow, TelemetryGraph, Badge, WandPanel
    electron/  main.ts, preload.ts, wand-window.ts, tray.ts, nxm-handler.ts
  core/                    # Python service
    api/        routes_games.py routes_mods.py routes_tweaks.py routes_telemetry.py ws.py
    discovery/  steam.py epic.py gog.py xbox.py ea.py ubisoft.py battlenet.py manual.py watcher.py
    detect/     engine.py anticheat.py dlss.py upscalers.py
    optimize/   autopilot.py cpu.py gpu_nvidia.py power.py windows.py memory.py rules.py
    mods/       staging.py deploy.py nexus_api.py nxm.py conflicts.py loaders.py profiles.py
    telemetry/  presentmon.py nvml.py sensors.py sessions.py analyzer.py
    safety/     snapshot.py undo.py backup.py
    helper/     elevated_helper.py
  data/
    adapters/   _generic.json unreal.json unity.json bethesda.json <game-slug>.json
    tweaks/     windows.json nvidia.json unreal.json per-game/*.json
    anticheat_signatures.json
  third_party/  (downloaded at first run, not committed: PresentMon, NVIDIA Profile Inspector, etc.)
  tests/
```

### 3.3 Core loop (event-driven)
```
Process start event ──► Game matcher ──► Known game? ──► Autopilot.apply(profile)
                                                        │   (snapshot first)
                                                        ▼
                                              Telemetry session starts
                                                        │
Process exit event ◄────────────────────────────────────┘
        │
        ▼
Autopilot.revert(session)  →  Save session report  →  Doctor analyses it
```

---

## 4. Screens & UX (how it should feel)

### 4.1 First-run wizard (≤ 60 seconds)
1. **Hardware scan** — shows CPU (with P/E core split), GPU + VRAM, RAM (and whether it's single/dual channel), display refresh rate, laptop vs desktop, MUX/Advanced Optimus status, driver version. One sentence of advice for each ("Your RAM is single-channel: expect ~10–20% lower FPS in CPU-heavy games.").
2. **Library scan** — animated list filling in as launchers are found. User can add a folder manually.
3. **Nexus Mods login** — optional; "Login with Nexus" button (SSO). Skip allowed.
4. **Safety level** — Safe / Balanced / Aggressive, with Safe preselected.
5. **Autopilot on?** — default ON for Safe tweaks.

### 4.2 Home (Library)
- Grid of game cards with cover art (Steam CDN art for Steam games; SteamGridDB API optional for others).
- Each card: title, engine badge (UE5 / Unity / etc.), DLSS badge showing detected versions (SR / FG / RR / DLSS 5), anti-cheat lock icon if applicable, mod count, "last session: 142 FPS avg, 1% low 88".
- Top bar: search, filter (Has mods / Has DLSS / Anti-cheat / Recently played), big **Autopilot: ON** pill.
- Clicking a card → Game page. A **Play** button launches via the correct launcher URI (`steam://rungameid/<id>`, `com.epicgames.launcher://apps/<id>?action=launch`).

### 4.3 Game page — 4 tabs
**Overview:** health score (0–100) with the three biggest issues and a "Fix all safe issues" button. Last-session graphs.

**Performance:** grouped toggles —
- *Upscaling & frame generation:* detected DLSS DLL versions, "Update DLSS" button, recommended preset for this GPU, FG/MFG recommendation, FPS cap recommendation.
- *VRAM guard:* current budget, recommended texture setting, engine-level streaming limit (UE only).
- *CPU:* priority, affinity plan, "Isolate server" (for games with a dedicated server).
- *Windows & driver:* per-game NVIDIA profile tweaks, GPU preference, power plan during session.
Every toggle: badge + "What this does" + "Undo".

**Mods:** installed mods list (drag to reorder load order), enable/disable switch per mod, conflict indicators, "Check for updates", "Browse Nexus" button, "Install from file" (drag-and-drop zip/7z/rar), mod-loader status (UE4SS/BepInEx/etc. installed or not, one-click install).

**Fixes:** curated known issues for this game (from PCGamingWiki + community data pack) — e.g., "Mouse acceleration", "Ultrawide FOV", "Stutter on menu open" — each with a button and source link.

### 4.4 Wand (in-game quick panel)
- Hotkey (default `Ctrl+Shift+W`, rebindable) opens a small always-on-top translucent panel. Works in borderless windowed; in exclusive fullscreen show a notification that the panel needs borderless (most modern DX12 games are effectively borderless anyway).
- Contents: live FPS/VRAM mini-graph, list of **Live** toggles only (ReShade effect toggles, UE4SS Lua mods that support hot reload, config values the game re-reads, FPS cap change, priority change, "Free VRAM now"-style actions that genuinely exist), plus "Queue for next launch" for restart-required mods.
- Command-palette style search ("fov", "cap 120", "disable reshade").
- It must be honest: things that need restart are shown greyed with "applies next launch".

### 4.5 Mod Browser
- Nexus Mods tab for the selected game: trending, latest, updated, search, categories, endorsements, file list with versions.
- "Mod Manager Download" button on the Nexus website opens `nxm://` links → app catches them → download → install → auto-enable (setting).
- Show requirements (e.g., "Requires UE4SS") and offer to install them first.

### 4.6 Doctor
- After every session: report card — avg FPS, 1% low, 0.1% low, stutter count (frames >2.5× median), VRAM peak, CPU busiest-thread %, GPU util, temperatures, throttling flags.
- Plain-language findings with fix buttons. Examples:
  - "VRAM hit 98% at 21:14 when frame time jumped to 41 ms. Lower textures one step or disable DLSS 5 neural rendering." [Apply]
  - "GPU sat at 60% while one CPU thread was at 100% — you are CPU-bound. Frame Generation will help more than lowering resolution."
  - "GPU clocks dropped at 87°C — thermal throttling. Raise laptop fan mode / use a cooling pad."
  - "Game running on integrated GPU." [Fix]

### 4.7 Undo timeline
Chronological list of every change: what, where, when, old value → new value. Revert any single item or "Revert everything for this game".

### 4.8 Settings
Autopilot tier, hotkeys, Nexus account, mod staging folder location, telemetry on/off, overlay on/off, data-pack update channel, logs export ("Copy diagnostics to clipboard").

---

## 5. Game discovery

### 5.1 Launcher sources
| Source | How |
|---|---|
| Steam | Registry `HKCU\Software\Valve\Steam\SteamPath` → `steamapps/libraryfolders.vdf` → each library's `appmanifest_*.acf` (parse with the `vdf` package). Gives appid, name, installdir. |
| Epic | `C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item` (JSON): DisplayName, InstallLocation, LaunchExecutable, AppName. |
| GOG | Registry `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\*`. |
| EA app | `C:\ProgramData\EA Desktop\` install data + registry uninstall keys. |
| Ubisoft Connect | Registry `HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\*`. |
| Battle.net | `C:\ProgramData\Battle.net\Agent\product.db` (protobuf) or uninstall keys. |
| Xbox / Game Pass | `XboxGames` folders + `Get-AppxPackage`. Mark as "limited modding" (protected folders). |
| Manual | User adds a folder or exe. |
Reference implementations: Playnite's library plugins, `legendary` (Epic), DLSS Swapper's game scanners.

### 5.2 Running-game watcher
- Use WMI `Win32_ProcessStartTrace` / `Win32_ProcessStopTrace` subscriptions (needs admin → run inside elevated helper) or ETW. Fallback: psutil diff every 3 s, but only compares PIDs (cheap).
- Match exe path against known game install dirs first (fast, exact).

### 5.3 Unknown-game heuristics (confidence score ≥ 60 to suggest, never auto-add silently)
+30 exe lives under a launcher library folder · +20 loads `d3d12.dll`/`d3d11.dll`/`vulkan-1.dll` · +20 has engine markers (§6.1) · +15 fullscreen/borderless covering monitor · +10 GPU usage >40% for 30 s · −50 exe in a denylist (browsers, video players, OBS, Discord, IDEs). Suggest via toast: "Looks like a game — add Hollow Deep to your library?"

---

## 6. Detection & optimization engine

### 6.1 Engine detection
- **Unreal:** `Engine/Binaries`, `*/Content/Paks/*.pak`, exe named `*-Win64-Shipping.exe`. UE version from exe version resource.
- **Unity:** `UnityPlayer.dll`, `*_Data/` folder; IL2CPP if `GameAssembly.dll`.
- **Bethesda/Creation:** `Data/*.esm`.
- **Source/Source 2, id Tech, RE Engine, REDengine, Frostbite, Decima**: signature files in `data/adapters/engines.json`.

### 6.2 CPU optimization (hybrid-core aware)
- Detect core classes with `GetLogicalProcessorInformationEx(RelationProcessorCore)` → `EfficiencyClass` (higher = P-core). Never assume indexes.
- On i7-14650HX this will typically give 8 P-cores (16 threads) + 8 E-cores (8 threads).
- Game: priority `High` (never `Realtime`). Affinity: leave default by default — Windows Thread Director usually does better than manual pinning. Offer "P-cores only" as Aggressive, with before/after benchmark to prove it helps.
- Dedicated server (Windrose server etc.): pin to a subset of E-cores, priority `BelowNormal`. Keep the existing idea — it's the one v1 feature that makes sense — but compute cores dynamically.
- Background hogs during session (optional): lower priority of known heavy processes (updaters, indexers, launchers' overlays), never kill anything without asking.

### 6.3 GPU & NVIDIA driver
- **Telemetry:** NVML via `nvidia-ml-py` (VRAM used/total, GPU util, clocks, temp, power, throttle reasons). Replace `nvidia-smi` subprocess polling.
- **Per-game driver profile:** use NVIDIA Profile Inspector's import (`-silentImport profile.nip`) or NVAPI DRS directly. Safe tweaks: frame rate limiter, Low Latency Mode (when game lacks Reflex), shader cache size, power management mode "Prefer maximum performance" during session. Export the existing profile first (snapshot).
- **GPU preference:** `HKCU\Software\Microsoft\DirectX\UserGpuPreferences` → `GpuPreference=2;` for the game exe (forces dGPU on laptops).
- **Laptop checks:** detect if the display is driven by the iGPU (Optimus) vs dGPU direct (MUX / Advanced Optimus). Recommend dGPU mode in the laptop's control software — often a large FPS gain. Detect plugged-in vs battery; warn on battery.
- **Hardware-accelerated GPU scheduling:** required for DLSS Frame Generation. Check it's on; if not, explain and offer to enable (reboot needed).

### 6.4 VRAM guard (the real version)
- Live budget from NVML. Thresholds: 85% amber, 92% red.
- **Unreal games:** write to `%LOCALAPPDATA%\<Project>\Saved\Config\Windows\Engine.ini` (location varies; adapter provides it):
  ```
  [SystemSettings]
  r.Streaming.LimitPoolSizeToVRAM=1
  r.Streaming.PoolSize=<computed MB>
  ```
  Compute pool size from VRAM total minus headroom for frame gen/UI (start: 8 GB card → ~3000–3500 MB, then tune from telemetry). Needs restart. Some games reset or ignore Engine.ini — verify per game and record in TESTLOG.
- **All games:** when red threshold is crossed during a session, Doctor records the moment and recommends the specific setting (textures, DLSS 5 neural rendering, resolution, FG factor).
- Do **not** claim the app "frees VRAM" live — Windows/driver own VRAM; the app can only reduce what the game asks for.

### 6.5 DLSS / upscaler module
- Scan game folder recursively for `nvngx_dlss.dll` (Super Resolution), `nvngx_dlssg.dll` (Frame Generation), `nvngx_dlssd.dll` (Ray Reconstruction), Streamline `sl.*.dll`, plus FSR/XeSS DLLs. For DLSS 5, **detect by reading the file version resource and product description, don't hardcode a filename** until it's confirmed.
- Show versions, offer update/rollback via a DLL library (the DLSS Swapper approach): back up the original, swap, keep a restore button. Mark online games with anti-cheat as `Online-risk` (some anti-cheats flag modified DLLs).
- Recommend settings per GPU tier. Starting rules for RTX 5060 Laptop 8 GB (tune from telemetry):
  - DLSS SR: Quality at 1080p; Balanced/Performance at 1440p.
  - Frame Gen: 2× if base FPS ≥ 50–60; MFG 3×/4× only if base FPS is already stable and VRAM < 85%.
  - DLSS 5 neural rendering: only when base FPS without it is ≥ ~90 and VRAM headroom ≥ 1.5 GB; otherwise recommend off. Say why.
  - Reflex: always on when available (On + Boost only if GPU-bound).
  - FPS cap: set slightly below refresh with FG (the NVIDIA App/Reflex usually handles this — don't double-cap).
- Driver-level DLSS overrides (preset, FG mode) are best done via the NVIDIA App; the app should link/explain rather than hack undocumented keys.

### 6.6 Windows session tweaks (applied on launch, reverted on exit)
- Power plan → High performance / Ultimate (or laptop vendor "Turbo" hint).
- Game Mode on (check), notifications → Do Not Disturb (Focus assist).
- Optional (Balanced): pause Windows Update / OneDrive sync during session.
- Optional (Aggressive, admin): standby-list purge when free RAM < 1 GB (ISLC-style). With 24 GB this rarely matters — only show if telemetry shows memory pressure.
- **Report only, never auto-change:** Memory Integrity/VBS (security trade-off), MPO, disabling Defender. Explain and let the user decide.

### 6.7 Rules engine
`data/tweaks/*.json` entries:
```json
{
  "id": "ue.streaming_pool_limit",
  "title": "Keep textures inside your VRAM",
  "explain": "Stops the game from overflowing VRAM, which causes big stutters when menus open.",
  "tier": "safe",
  "badges": ["needs_restart"],
  "applies_if": {"engine": "unreal", "vram_gb_lte": 10},
  "action": {"type": "ini_set", "file": "{ue_config}/Engine.ini", "section": "SystemSettings",
             "values": {"r.Streaming.LimitPoolSizeToVRAM": "1", "r.Streaming.PoolSize": "{calc.ue_pool_mb}"}},
  "revert": "snapshot",
  "source": "https://dev.epicgames.com/documentation/ (texture streaming cvars)",
  "verified_on": []
}
```
Action types: `ini_set`, `json_set`, `xml_set`, `reg_set`, `process_priority`, `process_affinity`, `nvidia_profile`, `power_plan`, `file_swap`. Each must implement `apply`, `verify`, `revert`.

---

## 7. Mod manager

### 7.1 Model (learn from Vortex and Mod Organizer 2)
- **Staging:** every mod is extracted into `Staging/<game>/<mod-id>/` — never directly into the game folder.
- **Deploy:** hardlinks (same drive) or copies (different drive) from staging into the game's mod path. A `deployment.json` manifest records every file placed. Purge = remove exactly those files.
- **Never overwrite** an original game file without backing it up into `Backups/<game>/`.
- **Conflicts:** two mods providing the same file → show conflict, user picks winner (default: later in load order wins).
- **Profiles:** per-game mod profiles ("Vanilla", "Graphics overhaul", "Co-op safe"). Switching profiles redeploys.
- **Online-safe profile:** automatically suggests disabling mods before joining multiplayer for games where mods break sessions.

### 7.2 Mod paths by engine (adapters)
- **Unreal pak mods:** `<Game>/Content/Paks/~mods/` (create folder). Include `.pak`, `.utoc`, `.ucas` together.
- **UE4SS (Lua/Blueprint mods):** install RE-UE4SS into `Binaries/Win64/`, mods into `ue4ss/Mods/` (layout varies by UE4SS version — read from the version installed), `mods.txt` for enable/disable.
- **Unity:** BepInEx (plugins in `BepInEx/plugins/`) or MelonLoader (`Mods/`). Detect Mono vs IL2CPP to choose the right build.
- **Bethesda:** `Data/` + `plugins.txt` load order (support later; complex).
- **ReShade:** installer places `dxgi.dll`/`d3d12.dll` etc. + `ReShade.ini`; presets in game folder. Use the ReShade build with add-on support only for single-player games.
- **Generic:** adapter JSON says where files go. If unknown, the install wizard shows the archive tree and lets the user map it once; that mapping is saved as a new adapter.

### 7.3 Nexus Mods integration
- Read Nexus's current API docs and acceptable-use policy before starting; register the app with Nexus to get an application slug for SSO. During development a personal API key works.
- Login via Nexus SSO (WebSocket flow). Store the key in Windows Credential Manager (`keyring`), never in plain files.
- `nxm://` handler: parse game domain, mod id, file id, key, expires → request download link → download with progress + resume → verify size → install.
- Free (non-Premium) users must click "Mod Manager Download" on the website; Premium users can download directly from the in-app browser. Implement both paths.
- Respect rate limits (read response headers), cache mod metadata, show endorsements, check updates on app start (batched).
- Also support Thunderstore and ModDB/manual archives later — keep a `ModSource` interface.

### 7.4 "Live" mods — be truthful
Truly live: ReShade effect toggles, UE4SS Lua mods with hot-reload, config files the game re-reads, FPS cap, priority. Everything else (pak files, DLL plugins) → **needs restart**. The Wand queues them and offers "Restart game now" (only after warning about unsaved progress).

---

## 8. Telemetry

- **Frame timing:** Intel PresentMon (console app or service API) — per-frame present time, displayed-frame time, and with FG the displayed vs rendered frames. Compute avg FPS, 1%/0.1% lows, stutters.
- **GPU:** NVML at 1 Hz (10 Hz only while Doctor "deep capture" is on).
- **CPU/RAM/disk:** psutil per-core usage; CPU temps via LibreHardwareMonitor (bundled lib, needs admin → elevated helper).
- **Session file:** compressed per-session JSON in SQLite; keep last 50 sessions per game.
- **Overlay (optional):** small corner FPS/VRAM readout in the Wand window. Don't reinvent RTSS; if RTSS is installed, offer to use it.

---

## 9. Safety & trust

- Snapshot before every change (file copy / registry export / profile export).
- Crash-safe: if the app is killed mid-session, next start detects an open session and reverts.
- Create a Windows System Restore point before the first Aggressive tweak (ask).
- Anti-cheat detection from `data/anticheat_signatures.json` (folders/DLLs: `EasyAntiCheat`, `BattlEye`, `vgk.sys` presence, etc.). Locked features show: "Locked — this game uses BattlEye. Injecting here can get your account banned."
- Code signing (later) so SmartScreen/antivirus don't flag the app.
- Logs in `%APPDATA%\NexusOptimize\logs`, rotated, no personal data.

---

## 10. Repos & resources to study (check each license before copying any code)

| Project | Use it for |
|---|---|
| Nexus-Mods/Vortex | Mod staging/deploy, game extensions, nxm handling. GPL-3.0 — learn from it; copying code makes your app GPL. |
| ModOrganizer2/modorganizer | Profiles, conflict UI, virtual file system ideas. |
| beeradmoore/dlss-swapper | Launcher scanning + DLSS DLL detection and swap. |
| Orbmu2k/nvidiaProfileInspector | Per-game NVIDIA driver profiles (can be invoked as a tool). |
| GameTechDev/PresentMon | Frame-time capture. |
| LibreHardwareMonitor/LibreHardwareMonitor | CPU/GPU temps and sensors. |
| UE4SS-RE/RE-UE4SS | Unreal Lua/Blueprint modding + hot reload. |
| BepInEx/BepInEx, LavaGang/MelonLoader | Unity mod loaders. |
| crosire/reshade | Post-processing injection, add-on API. |
| SpecialKO/SpecialK | Advanced frame pacing/HDR ideas (study, don't bundle). |
| OptiScaler (GitHub) | Cross-upscaler injection ideas (single-player only). |
| JosefNemec/Playnite | Library integrations for many launchers. |
| derrod/legendary | Epic Games library data. |
| ValvePython/vdf | Steam VDF/ACF parsing. |
| PCGamingWiki (Cargo API) | Per-game known issues, config file locations. |
| Nexus Mods API docs | Auth, downloads, rate limits. |

---

## 11. Build roadmap (each phase must pass its tests before the next)

**Phase 0 — Cleanup (1–2 days)**
Remove fake tweaks (§0.3). Move to folder layout (§3.2). Swap Flask → FastAPI, nvidia-smi → NVML. Add TESTLOG.
✅ Test: app starts, no feature references invented keys (`grep` proves it).

**Phase 1 — Library (2–4 days)**
All launchers in §5.1 that are installed on this PC; cover art; engine + anti-cheat + DLSS detection.
✅ Test: every installed game on this PC appears with correct engine; screenshot in TESTLOG. Scan < 5 s.

**Phase 2 — Telemetry + Doctor (4–6 days)**
PresentMon + NVML session capture, report card, findings.
✅ Test: play Windrose 10 min with DLSS 5 on and 10 min off; Doctor must show the VRAM-saturation moment when opening a chest.

**Phase 3 — Autopilot (4–6 days)**
Watcher, rules engine, snapshot/revert, Safe tier tweaks, server isolation with detected core classes.
✅ Test: launch/close game 5 times — every change reverts (Undo log shows it). Server-isolation benchmark: before/after 1% lows with server running.

**Phase 4 — Mods v1 (6–10 days)**
Staging/deploy/purge, drag-and-drop install, UE pak + UE4SS + BepInEx adapters, load order, conflicts, profiles.
✅ Test: install 3 mods, disable one, purge all → game folder byte-identical to before (hash comparison).

**Phase 5 — Nexus (4–6 days)**
SSO, nxm handler, browser, update checks.
✅ Test: click "Mod Manager Download" on nexusmods.com → mod installed and enabled with no manual steps.

**Phase 6 — Wand (3–5 days)**
Hotkey panel, live toggles, queue-for-restart.
✅ Test: toggle a ReShade effect and change FPS cap in-game without alt-tab.

**Phase 7 — DLSS module + polish (ongoing)**
DLL update/rollback, per-GPU recommendations, onboarding, installer, tray, auto-update of data packs.

---

## 12. API contract (core service)

```
GET  /games                         list games (+ engine, dlss, anticheat, mod count)
POST /games/rescan
GET  /games/{id}                    detail + recommended tweaks
POST /games/{id}/launch
GET  /games/{id}/tweaks             available tweaks with state
POST /tweaks/{tweak_id}/apply       {game_id}  → change_id
POST /changes/{change_id}/revert
GET  /changes?game_id=              undo timeline
GET  /games/{id}/mods               installed mods + order + conflicts
POST /games/{id}/mods/install       {path | nxm_url}
POST /mods/{mod_id}/toggle
PUT  /games/{id}/mods/order
POST /games/{id}/deploy   /purge
GET  /sessions?game_id=             past sessions
GET  /sessions/{id}/report          Doctor findings
WS   /ws/telemetry                  live FPS/frametime/VRAM/CPU stream
WS   /ws/events                     game_started, game_stopped, download_progress, finding
POST /autopilot                     {enabled, tier}
GET  /system                        hardware profile
```
All mutating endpoints return a `change_id` so the UI can offer instant undo.

---

## 13. Prompt to give Claude Code (copy-paste)

> You are the lead developer of Nexus Optimize & Mod. Read `SPEC.md` and `AGENTS.md` fully before touching code. Work phase by phase from §11 — start with Phase 0. Rules: surgical diffs; no invented config keys or registry values — every tweak needs a `source`; if something is uncertain or impossible, say so instead of simulating it. At the end of each phase, run its acceptance test on this machine, paste the real output into `docs/TESTLOG.md`, and list anything that failed. Do not report a phase complete if its test hasn't passed. Ask me before adding any feature that injects into a game process or needs admin rights.

---

## 14. Definition of "mind-blowing" (measurable)
- Open app → all games visible in < 5 s, no setup.
- Install a Nexus mod in 2 clicks, remove it with zero leftovers.
- Every session produces a report that tells you *why* it stuttered, in one sentence, with a fix button.
- Autopilot measurably improves 1% lows in at least the server-hosting scenario, proven by numbers.
- Nothing the app does can't be undone.
