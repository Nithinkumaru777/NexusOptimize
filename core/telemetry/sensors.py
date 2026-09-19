"""CPU load (psutil), network latency (TCP handshake) and FPS (PresentMon).

FPS comes from Intel PresentMon run as a separate process: it reads the
DxgKrnl/DXGI ETW present events Windows already emits. Nothing is injected
into, hooked in, or read from the game process, so anti-cheat has nothing to
see. No PresentMon (or no ETW access) -> fps=None with a reason, never a guess.
"""

import collections
import glob
import math
import os
import shutil
import socket
import subprocess
import threading
import time

import psutil

_LATENCY_HOSTS = [("1.1.1.1", 443), ("8.8.8.8", 443)]


def get_cpu_stats():
    return {"cpu_percent": psutil.cpu_percent(interval=None)}


def get_system_stats(top_n=8):
    """Per-core CPU, RAM and the biggest processes by working set (no admin needed)."""
    mem = psutil.virtual_memory()
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        mi = p.info["memory_info"]
        if mi:
            procs.append({"pid": p.info["pid"], "name": p.info["name"] or "?", "ram_mb": mi.rss // 2**20})
    procs.sort(key=lambda p: p["ram_mb"], reverse=True)
    return {
        # Blocking sample: psutil's interval=None baseline is per-thread, and this
        # runs on arbitrary FastAPI pool threads (sync route, so the loop isn't blocked).
        "cpu_per_core": psutil.cpu_percent(interval=0.1, percpu=True),
        "ram_used_mb": (mem.total - mem.available) // 2**20,
        "ram_total_mb": mem.total // 2**20,
        "ram_percent": mem.percent,
        "top_processes": procs[:top_n],
    }


def get_network_latency_ms():
    for host, port in _LATENCY_HOSTS:
        try:
            start = time.perf_counter()
            with socket.create_connection((host, port), timeout=1.0):
                pass
            return round((time.perf_counter() - start) * 1000, 1)
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- FPS (PresentMon / ETW)
_SESSION = "NexusOptimize_FPS"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_presentmon():
    """[exe] from NEXUS_PRESENTMON, third_party/PresentMon*.exe or PATH; else None."""
    env = os.environ.get("NEXUS_PRESENTMON")
    if env:
        return [env] if os.path.isfile(env) else None
    bundled = sorted(glob.glob(os.path.join(_ROOT, "third_party", "PresentMon*.exe")))
    found = bundled[-1] if bundled else shutil.which("PresentMon")
    return [found] if found else None


def presentmon_command(exe, process_name):
    return [*exe, "--process_name", process_name, "--output_stdout", "--no_console_stats",
            "--session_name", _SESSION, "--stop_existing_session"]


def fps_from_frametimes(ms):
    total = sum(ms)
    return 1000.0 * len(ms) / total if ms and total > 0 else None


class FrameRateMonitor:
    """Runs PresentMon for one game exe and keeps a rolling window of frame times."""

    def __init__(self, exe, window_s=2.0):
        self.exe, self.window_s = exe, window_s
        self.status = "idle"
        self._target = self._proc = None
        self._frames = collections.deque()
        self._recording = None   # Benchmark Lab: every frame time since start_recording()
        self._lock = threading.Lock()

    def set_target(self, process_name):
        if process_name == self._target:
            return
        self._stop()
        self._target = process_name
        if not process_name:
            self.status = "idle"
        elif not self.exe:
            self.status = "PresentMon not found - put PresentMon.exe in third_party/ or set NEXUS_PRESENTMON."
        else:
            self._start(process_name)

    def fps(self):
        cutoff = time.monotonic() - self.window_s
        with self._lock:
            while self._frames and self._frames[0][0] < cutoff:
                self._frames.popleft()
            ms = [f[1] for f in self._frames]
        return fps_from_frametimes(ms)

    def start_recording(self):
        with self._lock:
            self._recording = []

    def stop_recording(self):
        """Frame times (ms) captured since start_recording(); None if not recording."""
        with self._lock:
            rec, self._recording = self._recording, None
        return rec

    def _start(self, process_name):
        try:
            proc = subprocess.Popen(
                presentmon_command(self.exe, process_name), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            self.status = f"PresentMon failed to start: {e}"
            return
        self._proc, self.status = proc, "capturing"
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="presentmon").start()

    def _read(self, proc):
        col, last = None, ""
        for line in proc.stdout:
            cells = line.strip().split(",")
            if col is None:
                if "MsBetweenPresents" in cells:
                    col = cells.index("MsBetweenPresents")
                elif line.strip() and not last.lower().startswith("error:"):
                    last = line.strip()   # keep PresentMon's "error:" line, not the help text after it
                continue
            try:
                ms = float(cells[col])
            except (IndexError, ValueError):   # "NA" rows, partial lines
                continue
            if not 0 <= ms < math.inf:   # nan/inf/negative: one bad value poisons every mean and the JSON
                continue
            with self._lock:
                if proc is self._proc:   # drop late lines from a stopped capture
                    self._frames.append((time.monotonic(), ms))
                    if self._recording is not None:
                        self._recording.append(ms)
        if proc is self._proc:   # exited on its own, not via _stop()
            hint = (" - add your account to 'Performance Log Users' (sign out/in) or run as admin."
                    if "access denied" in last.lower() else "")
            self.status = f"PresentMon stopped: {last or f'exit code {proc.wait()}'}{hint}"

    def _stop(self):
        proc, self._proc = self._proc, None
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        with self._lock:
            self._frames.clear()
