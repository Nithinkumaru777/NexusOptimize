# AGENTS.md — Nexus Optimize & Mod

Read `SPEC.md` first. Rules that are easy to forget:

1. Nothing is done until its SPEC §11 acceptance test ran **on this machine** and the real output is in `docs/TESTLOG.md`.
2. No invented config keys. Every key written to a game file cites a `source` (file on disk, engine docs,
   PCGamingWiki, mod page). `tests/test_phase0.py` guards the known-bad v1 keys.
3. Unsure whether something works? Say so and stop. Never simulate data (FPS is PresentMon or `null`).
4. Ask before any feature that injects into a game process or needs admin rights.
5. Decisions that deviate from SPEC go in `docs/DECISIONS.md`.

## Run
```
python -m venv .venv && .venv\Scripts\python -m pip install -r core\requirements.txt
npm install
npm test        # pytest
npm start       # Electron; spawns `.venv\Scripts\python -m core.main` with a per-session token
npm run dist    # PyInstaller core (dist\backend) + electron-builder -> dist\app\Nexus Optimize & Mod Setup <ver>.exe
```
Core only: `set NEXUS_TOKEN=x && .venv\Scripts\python -m core.main` → http://127.0.0.1:5000 (header `X-Nexus-Token: x`).
