"""Benchmark Lab: PresentMon frame times of a recorded run -> summary -> SQLite (SPEC 2.6, 3.1).

Raw frame times are stored with each run so later metrics (Doctor, graphs) can be
recomputed from real data instead of trusting old summaries.
"""

import json
import os
import sqlite3
import statistics
import time
from contextlib import closing

DB = os.path.join(os.path.expandvars("%APPDATA%"), "NexusOptimize", "nexus.db")
METRICS = ("avg_fps", "low1_fps", "low01_fps", "stutters")
_COLS = ("id", "game_id", "label", "created", "frames", "duration_s") + METRICS


def summarize(ms):
    """avg FPS, 1% / 0.1% lows (mean of the slowest 1% / 0.1% frames), stutters (> 2.5x median, SPEC 4.6)."""
    if not ms or sum(ms) <= 0:
        return None
    slow = sorted(ms, reverse=True)
    low = lambda share: 1000.0 / statistics.fmean(slow[:max(1, int(len(ms) * share))])
    median = statistics.median(ms)
    return {"frames": len(ms), "duration_s": sum(ms) / 1000.0, "avg_fps": 1000.0 * len(ms) / sum(ms),
            "low1_fps": low(0.01), "low01_fps": low(0.001), "stutters": sum(m > 2.5 * median for m in ms)}


def _db():
    """One short-lived connection per call: FastAPI runs routes on a thread pool and
    sqlite3 connections are not shareable across threads. The DDL below is idempotent and cheap."""
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")      # readers never block the writer; persists in the file
    db.execute("PRAGMA synchronous = NORMAL")    # safe under WAL, no fsync per insert
    db.execute("CREATE TABLE IF NOT EXISTS bench_runs (id INTEGER PRIMARY KEY, game_id TEXT, label TEXT,"
               " created REAL, frames INTEGER, duration_s REAL, avg_fps REAL, low1_fps REAL, low01_fps REAL,"
               " stutters INTEGER, frametimes TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS bench_runs_game ON bench_runs (game_id, id)")
    return db


def save(game_id, label, ms):
    """Stores a run; None when nothing was captured."""
    s = summarize(ms)
    if not s:
        return None
    run = {"game_id": game_id, "label": label, "created": time.time(), **s}
    with closing(_db()) as db, db:
        run["id"] = db.execute(
            f"INSERT INTO bench_runs ({', '.join(_COLS[1:])}, frametimes) VALUES ({', '.join('?' * len(_COLS))})",
            [run[k] for k in _COLS[1:]] + [json.dumps(ms)]).lastrowid
    return run


def runs(game_id):
    with closing(_db()) as db:
        rows = db.execute(f"SELECT {', '.join(_COLS)} FROM bench_runs WHERE game_id = ? ORDER BY id DESC", (game_id,))
        return [dict(r) for r in rows]


def compare(a_id, b_id):
    """{a, b, delta, delta_pct}: b relative to a. None if either run is gone."""
    with closing(_db()) as db:
        a, b = (db.execute(f"SELECT {', '.join(_COLS)} FROM bench_runs WHERE id = ?", (i,)).fetchone()
                for i in (a_id, b_id))
    if not a or not b:
        return None
    return {"a": dict(a), "b": dict(b),
            "delta": {k: b[k] - a[k] for k in METRICS},
            "delta_pct": {k: (b[k] - a[k]) * 100.0 / a[k] if a[k] else None for k in METRICS}}


def delete(run_id):
    with closing(_db()) as db, db:
        return db.execute("DELETE FROM bench_runs WHERE id = ?", (run_id,)).rowcount > 0
