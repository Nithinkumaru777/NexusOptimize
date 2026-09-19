"""Config Time Machine (Epic P3 #3): per-file backup history, read-only lock, and a pinned
hash that tells you when the game rewrote your tweaks on exit."""

import os
import stat

import pytest

from tests.test_upgrade import backup, ue_game  # noqa: F401 (fixtures)


@pytest.fixture
def ini(tmp_path, monkeypatch, backup):
    from core.optimize import ini
    monkeypatch.setattr(ini, "PINS", str(tmp_path / "ini_pins.json"))
    return ini


def writable(path):
    return bool(os.stat(path).st_mode & stat.S_IWRITE)


def test_lock_and_unlock(tmp_path, ini):
    f = tmp_path / "Engine.ini"
    f.write_text("[A]\nx=1\n")
    assert ini.lock_ini(str(f)) and not writable(f)
    assert ini.lock_ini(str(f), readonly=False) and writable(f)


def test_app_write_keeps_a_locked_file_locked(tmp_path, ini):
    f = tmp_path / "Engine.ini"
    f.write_text("[A]\nx=1\n")
    ini.lock_ini(str(f))
    try:
        assert ini.update_ini_settings(str(f), [("A", "x", "2")]) == (True, 1)
        assert "x=2" in f.read_text()
        assert not writable(f)
    finally:
        ini.lock_ini(str(f), readonly=False)


def test_pin_detects_a_revert(tmp_path, ini):
    f = tmp_path / "Engine.ini"
    f.write_text("[A]\nx=1\n")
    assert ini.pinned(str(f)) is None
    ini.pin(str(f))
    assert not ini.is_reverted(str(f), ini.pinned(str(f)))
    f.write_text("[A]\nx=0\n")   # the game rewrote it on exit
    assert ini.is_reverted(str(f), ini.pinned(str(f)))


# ---------------------------------------------------------------- API
H = {"X-Nexus-Token": "t"}


@pytest.fixture
def api(monkeypatch, ue_game, ini):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    from core.telemetry import sensors
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "fps_monitor", sensors.FrameRateMonitor(exe=None))
    monkeypatch.setattr(app_mod, "_client_running", lambda g, s: False)
    spec = {**ue_game, "client_exe": "Game.exe"}
    with TestClient(app_mod.app) as c:
        app_mod.state.update(spec=spec, game={"id": "g", "name": "Game", "install_path": spec["install_path"]})
        yield c, os.path.join(os.path.dirname(spec["gus_path"]), "Engine.ini")


def files(c):
    return {f["name"]: f for f in c.get("/config/ini", headers=H).json()["files"]}


def test_api_lists_config_files_with_history(api, ini):
    c, engine = api
    assert files(c)["Engine.ini"] == {"name": "Engine.ini", "path": engine, "locked": False,
                                      "pinned": False, "reverted": None, "history": []}
    ini.update_ini_settings(engine, [("SystemSettings", "r.X", "1")])
    [h] = files(c)["Engine.ini"]["history"]
    assert c.post("/backups/restore", json={"id": h["id"]}, headers=H).json()["status"] == "success"
    assert "r.X" not in open(engine).read()


def test_api_pin_then_revert(api):
    c, engine = api
    assert c.post("/config/ini/pin", json={"name": "Engine.ini"}, headers=H).json()["status"] == "success"
    f = files(c)["Engine.ini"]
    assert f["pinned"] and f["reverted"] is False
    with open(engine, "a") as fh:
        fh.write("r.Y=0\n")
    assert files(c)["Engine.ini"]["reverted"] is True


def test_api_lock(api):
    c, engine = api
    try:
        assert c.post("/config/ini/lock", json={"name": "Engine.ini", "locked": True}, headers=H).json()["status"] == "success"
        assert not writable(engine) and files(c)["Engine.ini"]["locked"]
    finally:
        c.post("/config/ini/lock", json={"name": "Engine.ini", "locked": False}, headers=H)
    assert writable(engine)


@pytest.mark.parametrize("name", ["..\\..\\Game\\x.ini", "Missing.ini", ""])
def test_api_only_touches_ini_files_in_the_config_folder(api, name):
    c, _ = api
    assert c.post("/config/ini/lock", json={"name": name, "locked": True}, headers=H).status_code == 400
    assert c.post("/config/ini/pin", json={"name": name}, headers=H).status_code == 400
