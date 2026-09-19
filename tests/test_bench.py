"""Benchmark Lab: record PresentMon frame times for a run, store it in SQLite, compare runs."""

import sys
import time

import pytest

from core.telemetry import bench, sensors
from tests.test_fps import fake_pm, wait_for  # noqa: F401 (fixture)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "DB", str(tmp_path / "nexus.db"))
    return bench


def test_summary_reports_avg_lows_and_stutters():
    s = bench.summarize([10.0] * 99 + [50.0])
    assert s["frames"] == 100
    assert s["avg_fps"] == pytest.approx(1000 * 100 / 1040)
    assert s["low1_fps"] == pytest.approx(20.0)     # slowest 1% = the one 50 ms frame
    assert s["low01_fps"] == pytest.approx(20.0)
    assert s["stutters"] == 1                        # > 2.5x median (SPEC 4.6)
    assert s["duration_s"] == pytest.approx(1.04)


def test_no_frames_means_no_summary():
    assert bench.summarize([]) is None


def test_runs_are_stored_listed_and_compared(db):
    a = db.save("windrose", "DLSS Quality", [10.0] * 100)
    b = db.save("windrose", "DLSS Balanced", [8.0] * 100)
    db.save("other", "x", [10.0] * 10)
    assert [r["label"] for r in db.runs("windrose")] == ["DLSS Balanced", "DLSS Quality"]
    assert "frametimes" not in db.runs("windrose")[0]
    c = db.compare(a["id"], b["id"])
    assert c["delta"]["avg_fps"] == pytest.approx(25.0)
    assert c["delta_pct"]["avg_fps"] == pytest.approx(25.0)
    assert db.compare(a["id"], 999) is None


def test_empty_run_is_not_saved(db):
    assert db.save("windrose", "nothing", []) is None
    assert db.runs("windrose") == []


def test_delete_removes_a_run(db):
    r = db.save("windrose", "x", [10.0] * 10)
    assert db.delete(r["id"]) and db.runs("windrose") == []
    assert not db.delete(r["id"])


def test_monitor_records_every_frame_between_start_and_stop(fake_pm):
    exe, _ = fake_pm
    mon = sensors.FrameRateMonitor(exe=exe)
    mon.start_recording()
    try:
        mon.set_target("game.exe")
        assert wait_for(lambda: mon.fps())
        assert wait_for(lambda: len(mon._recording) == 60 or None)
    finally:
        mon.set_target(None)
    assert mon.stop_recording() == [10.0] * 60
    assert mon.stop_recording() is None


STREAM = """
import time
print("Application,ProcessID,MsBetweenPresents", flush=True)
while True:
    print("game.exe,1,10.0", flush=True)
    time.sleep(0.01)
"""


def test_bench_api_records_a_run_from_presentmon(tmp_path, db, monkeypatch):
    """Real PresentMon streams frames for as long as the game runs."""
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    (tmp_path / "stream_pm.py").write_text(STREAM)
    exe = [sys.executable, str(tmp_path / "stream_pm.py")]
    running = {"on": False}
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "fps_monitor", sensors.FrameRateMonitor(exe=exe))
    monkeypatch.setattr(app_mod, "_client_running", lambda game, spec: running["on"])
    h = {"X-Nexus-Token": "t"}
    with TestClient(app_mod.app) as c:
        r = c.post("/bench/start", json={"label": "A"}, headers=h)
        assert r.status_code == 400 and "not running" in r.json()["message"]
        running["on"] = True
        assert wait_for(lambda: c.get("/telemetry", headers=h).json()["game_running"] or None, timeout=8)
        assert c.post("/bench/start", json={"label": "A"}, headers=h).json()["status"] == "success"
        assert c.get("/bench", headers=h).json()["recording"] == "A"
        assert wait_for(lambda: app_mod.fps_monitor.fps(), timeout=8)
        time.sleep(0.3)
        run = c.post("/bench/stop", headers=h).json()["run"]
        assert run["avg_fps"] == pytest.approx(100.0) and run["label"] == "A"
        listed = c.get("/bench", headers=h).json()
        assert listed["recording"] is None and listed["runs"][0]["id"] == run["id"]
        assert c.post("/bench/stop", headers=h).status_code == 400


def test_db_runs_in_wal_mode_with_an_index_for_per_game_lists(db):
    db.save("windrose", "x", [10.0] * 10)
    with db.sqlite3.connect(db.DB) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert "bench_runs_game" in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def test_unexpected_backend_errors_come_back_as_a_readable_message(monkeypatch):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "_active", lambda: (None, {"id": "windrose"}))

    def boom(_):
        raise bench.sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(bench, "runs", boom)
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        r = c.get("/bench", headers={"X-Nexus-Token": "t"})
    assert r.status_code == 500
    assert r.json() == {"status": "error", "message": "Unexpected error: database is locked"}
