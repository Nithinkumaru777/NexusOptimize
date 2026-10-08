<div align="center">
  <img src="docs/assets/logo.jpg" alt="Nexus Optimize & Mod Logo" width="400"/>
  
  # Nexus Optimize & Mod (v3.0.0-dev)
  **The ultimate AAA Mod Manager, Performance Optimizer, and Telemetry Dashboard.**
  *(Currently in active development for Phase 1: GSAP & Glassmorphism UI Overhaul)*
</div>

## 🚀 Features (v3 Pipeline)
* **Obsidian Elite UI (Phase 1 Complete):** A buttery smooth, frosted-glass Cyberpunk interface built on Electron with GSAP 3D cinematic reveals and Playfair Display typography.
* **Universal Mod Router:** Intelligently routes mods for Unreal Engine, Glacier Engine, and automatically builds safe fallbacks for unsupported games.
* **Process Assassin (Ultra Game Mode):** Safely throttles heavy background apps (Chrome, Discord, Launchers) to IDLE priority while gaming to squeeze out every drop of CPU performance.
* **Benchmark Lab:** Records live ETW telemetry (via PresentMon) to accurately track your FPS, VRAM usage, and mathematically calculate 1% Lows.
* **Config Time Machine:** Automatically locks, pins, and snapshots your game's .ini files so you can instantly rollback if a patch breaks your settings.
* **Mod Bisect Engine:** A built-in binary search algorithm that automatically toggles halves of your modlist on/off to isolate exactly which mod is crashing your game.
* **Auto Save-Game Backups:** Automatically compresses your save files using maximum LZMA compression into a rolling 20-slot backup every time you close your game.

## 💾 Installation & Developer Guide

Since v3.0.0 is currently in active development on the 3-dev branch, there is no pre-compiled .exe for this specific branch yet. To run the new UI locally:

### Prerequisites
1. Install **[Node.js](https://nodejs.org/)** (v18 or higher)
2. Install **[Python](https://www.python.org/downloads/)** (v3.10 or higher)

### Setup Instructions
1. Clone the repository:
   `ash
   git clone https://github.com/Nithinkumaru777/NexusOptimize.git
   cd NexusOptimize
   git checkout v3-dev
   `
2. Install Python backend dependencies:
   `ash
   pip install -r core/requirements.txt
   `
3. Install Electron frontend dependencies:
   `ash
   npm install
   `
4. Run the application:
   `ash
   npm start
   `

## 🛠️ Built With
* **Frontend:** Electron, Vanilla JS, GSAP (GreenSock Animation Platform)
* **Backend:** Python, FastAPI, SQLite (WAL Mode)
* **AI Engineered:** Co-developed by Antigravity and Claude Code.
