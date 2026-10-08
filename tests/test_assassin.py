"""Process Assassin (Ultra Game Mode): a manual toggle that drops heavy background
apps to Idle CPU priority and puts them back to Normal when switched off.

A copy of ping.exe renamed to a unique name stands in for a browser, so real
psutil priority calls are exercised without touching anything else on the machine.
"""

import shutil
import subprocess
import time

import psutil
import pytest

from core.optimize import cpu

NAME = "nexus_fake_browser.exe"


@pytest.fixture
def fake_app(tmp_path, monkeypatch):
    exe = tmp_path / NAME
    shutil.copy(r"C:\Windows\System32\PING.EXE", exe)
    monkeypatch.setattr(cpu, "BACKGROUND_APPS", frozenset({NAME}))
    procs = []

    def spawn():
        p = subprocess.Popen([str(exe), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL)
        procs.append(p)
        return psutil.Process(p.pid)

    yield spawn
    cpu.process_assassin(False)
    for p in procs:
        p.kill()
        p.wait()


def test_on_drops_background_apps_to_idle_and_off_restores_normal(fake_app):
    proc = fake_app()
    assert proc.nice() == psutil.NORMAL_PRIORITY_CLASS

    ok, msg = cpu.process_assassin(True)
    assert ok and "1" in msg
    assert proc.nice() == psutil.IDLE_PRIORITY_CLASS
    assert cpu.assassin_status()["enabled"] is True

    ok, msg = cpu.process_assassin(False)
    assert ok
    assert proc.nice() == psutil.NORMAL_PRIORITY_CLASS
    assert cpu.assassin_status()["enabled"] is False


ELEVATED = cpu._can_trim_others()


@pytest.mark.skipif(not ELEVATED, reason="trimming another process needs SeIncreaseQuotaPrivilege (admin)")
def test_on_flushes_the_working_set_of_throttled_apps(fake_app):
    proc = fake_app()
    time.sleep(0.5)   # let it finish loading, or its working set is still growing
    before = proc.memory_info().rss   # rss == working set on Windows
    ok, msg = cpu.process_assassin(True)
    assert proc.memory_info().rss < before / 2
    assert "MB of RAM" in msg


@pytest.mark.skipif(ELEVATED, reason="non-admin path")
def test_without_admin_the_ram_flush_is_reported_as_skipped(fake_app):
    proc = fake_app()
    ok, msg = cpu.process_assassin(True)
    assert ok and proc.nice() == psutil.IDLE_PRIORITY_CLASS   # throttling still works
    assert "RAM flush skipped" in msg and "administrator" in msg


def test_apps_the_user_already_tuned_are_left_alone(fake_app):
    proc = fake_app()
    proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    cpu.process_assassin(True)
    assert proc.nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
    cpu.process_assassin(False)
    assert proc.nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS


def test_off_does_not_touch_apps_it_never_lowered(fake_app):
    proc = fake_app()
    proc.nice(psutil.IDLE_PRIORITY_CLASS)   # set by the user, not by us
    cpu.process_assassin(False)
    assert proc.nice() == psutil.IDLE_PRIORITY_CLASS


def test_api_toggle_and_status(fake_app, monkeypatch):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    proc = fake_app()
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    h = {"X-Nexus-Token": "t"}
    with TestClient(app_mod.app) as c:
        res = c.post("/optimize/assassin", json={"enabled": True}, headers=h).json()
        assert res["status"] == "success"
        assert proc.nice() == psutil.IDLE_PRIORITY_CLASS
        assert c.get("/optimize/assassin", headers=h).json()["enabled"] is True
        c.post("/optimize/assassin", json={"enabled": False}, headers=h)
        assert proc.nice() == psutil.NORMAL_PRIORITY_CLASS


def test_app_shutdown_restores_lowered_apps(fake_app, monkeypatch):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    proc = fake_app()
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    with TestClient(app_mod.app) as c:
        c.post("/optimize/assassin", json={"enabled": True}, headers={"X-Nexus-Token": "t"})
        assert proc.nice() == psutil.IDLE_PRIORITY_CLASS
    assert proc.nice() == psutil.NORMAL_PRIORITY_CLASS


def test_throttled_count_is_exactly_the_live_processes_it_lowered(fake_app):
    first = fake_app()
    ok, msg = cpu.process_assassin(True)
    assert "1 background" in msg and cpu.assassin_status()["lowered"] == 1
    first.kill()
    first.wait()
    assert cpu.assassin_status()["lowered"] == 0   # a closed app is no longer throttled
    fake_app()
    ok, msg = cpu.process_assassin(True)
    assert "1 background" in msg and cpu.assassin_status()["lowered"] == 1
