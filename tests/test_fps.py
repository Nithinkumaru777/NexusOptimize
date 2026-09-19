"""Real FPS from PresentMon (ETW, out-of-process - nothing touches the game).

A fake PresentMon script stands in for the binary: it prints the same CSV that
`PresentMon --output_stdout` does, so the subprocess plumbing is exercised for real.
"""

import sys
import time

import psutil
import pytest

from core.telemetry import sensors

FAKE = """
import sys, time
open(sys.argv[1], "a").write("started\\n")
print("Application,ProcessID,MsCPUBusy,MsBetweenPresents", flush=True)
for _ in range(60):
    print("game.exe,1,1.0,10.0", flush=True)
for bad in ("nan", "inf", "-1.0"):   # never valid frame times; one NaN poisons every mean
    print(f"game.exe,1,1.0,{bad}", flush=True)
print("game.exe,1,1.0,NA", flush=True)
time.sleep(60)
"""


@pytest.fixture
def fake_pm(tmp_path):
    script, log = tmp_path / "fake_presentmon.py", tmp_path / "starts.log"
    script.write_text(FAKE)
    return [sys.executable, str(script), str(log)], log


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (v := fn()) is not None:
            return v
        time.sleep(0.05)
    return None


def fake_children():
    return [p for p in psutil.Process().children(recursive=True)
            if "fake_presentmon.py" in " ".join(p.cmdline())]


def test_fps_is_the_inverse_of_mean_frame_time():
    assert sensors.fps_from_frametimes([10.0] * 50) == pytest.approx(100.0)
    assert sensors.fps_from_frametimes([5.0, 15.0]) == pytest.approx(100.0)
    assert sensors.fps_from_frametimes([]) is None


def test_presentmon_is_scoped_to_the_game_and_outputs_csv():
    cmd = sensors.presentmon_command(["PresentMon.exe"], "Windrose-Win64-Shipping.exe")
    assert cmd[0] == "PresentMon.exe"
    for flag in ("--output_stdout", "--no_console_stats", "--stop_existing_session"):
        assert flag in cmd
    assert cmd[cmd.index("--process_name") + 1] == "Windrose-Win64-Shipping.exe"
    assert "--session_name" in cmd


def test_monitor_reports_real_fps_from_presentmon_output(fake_pm):
    exe, _ = fake_pm
    mon = sensors.FrameRateMonitor(exe=exe)
    try:
        mon.set_target("game.exe")
        assert wait_for(mon.fps) == pytest.approx(100.0)
    finally:
        mon.set_target(None)


def test_fps_goes_stale_when_frames_stop(fake_pm):
    exe, _ = fake_pm
    mon = sensors.FrameRateMonitor(exe=exe, window_s=0.5)
    try:
        mon.set_target("game.exe")
        assert wait_for(mon.fps) is not None
        time.sleep(0.8)
        assert mon.fps() is None
    finally:
        mon.set_target(None)


def test_same_target_does_not_restart_and_none_stops_presentmon(fake_pm):
    exe, log = fake_pm
    mon = sensors.FrameRateMonitor(exe=exe)
    mon.set_target("game.exe")
    mon.set_target("game.exe")
    assert wait_for(mon.fps) is not None
    assert log.read_text().count("started") == 1
    mon.set_target(None)
    assert fake_children() == []
    assert mon.fps() is None


def test_missing_presentmon_reports_why_instead_of_a_number():
    mon = sensors.FrameRateMonitor(exe=None)
    mon.set_target("game.exe")
    assert mon.fps() is None
    assert "PresentMon not found" in mon.status


def test_presentmon_failure_is_surfaced(tmp_path):
    exe = [sys.executable, "-c",
           "import sys; sys.stderr.write('error: failed to start trace session: access denied\\n'); sys.exit(4)"]
    mon = sensors.FrameRateMonitor(exe=exe)
    mon.set_target("game.exe")
    assert wait_for(lambda: mon.status if "access denied" in mon.status else None)
    assert mon.fps() is None
    mon.set_target(None)


# Verbatim output of third_party/PresentMon.exe 2.5.1 for a non-admin user (captured 2026-09-19):
# the error is NOT the last line.
REAL_DENIED = r'''
import sys
sys.stdout.write("""warning: PresentMon requires elevated privilege in order to query processes that are
         short-running or started on another account.  Without it, those processes will
         be listed as '<unknown>' and they can't be targeted by --process_name nor trigger
         --terminate_on_proc_exit.
error: failed to start trace session: access denied.
       PresentMon requires either administrative privileges or to be run by a user in the
       "Performance Log Users" user group.  View the readme for more details.
""")
sys.exit(6)
'''


def test_real_access_denied_output_names_the_error_and_the_fix(tmp_path):
    script = tmp_path / "pm_denied.py"
    script.write_text(REAL_DENIED)
    mon = sensors.FrameRateMonitor(exe=[sys.executable, str(script)])
    mon.set_target("game.exe")
    status = wait_for(lambda: mon.status if mon.status.startswith("PresentMon stopped") else None)
    assert "access denied" in status
    assert "Performance Log Users" in status
    assert mon.fps() is None
    mon.set_target(None)


def test_telemetry_endpoint_serves_presentmon_fps(fake_pm, monkeypatch):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    exe, _ = fake_pm
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "fps_monitor", sensors.FrameRateMonitor(exe=exe))
    monkeypatch.setattr(app_mod, "_client_running", lambda game, spec: True)
    with TestClient(app_mod.app) as c:
        get = lambda: c.get("/telemetry", headers={"X-Nexus-Token": "t"}).json()
        d = wait_for(lambda: (d := get())["fps"] is not None and d or None, timeout=8)
    assert d["fps"] == pytest.approx(100.0)
    assert d["fps_source"] == "presentmon"
    assert fake_children() == []   # shutdown stops PresentMon


def test_presentmon_location_env_override_wins(tmp_path, monkeypatch):
    exe = tmp_path / "PresentMon-2.3.0-x64.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("NEXUS_PRESENTMON", str(exe))
    assert sensors.find_presentmon() == [str(exe)]
    monkeypatch.setenv("NEXUS_PRESENTMON", str(tmp_path / "missing.exe"))
    assert sensors.find_presentmon() is None
