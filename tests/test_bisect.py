"""Mod Bisect (Epic P3 #4): binary search over archive mods to find the one that breaks the game.
Each step switches the half under test ON and the other suspects OFF (file-based toggles only)."""

import pytest

from tests.test_modmanager import make_zip, mm, paks  # noqa: F401 (fixtures)
from tests.test_upgrade import backup, live, ue_game  # noqa: F401 (fixtures)


@pytest.fixture
def bis(tmp_path, monkeypatch):
    from core.mods import bisect
    from core.telemetry import bench
    monkeypatch.setattr(bench, "DB", str(tmp_path / "nexus.db"))   # fresh DB, table must be created on demand
    return bisect


def run_to_end(bis, suspects, culprit):
    ok, msg = bis.start("g", suspects)
    assert ok, msg
    for _ in range(len(suspects)):
        testing = bis.get_state("g")["testing_mods"]
        ok, res = bis.report("g", works_fine=culprit not in testing)
        assert ok
        if res["finished"]:
            return res["culprit"]
    pytest.fail("bisect did not converge")


def test_needs_two_mods(bis):
    assert bis.start("g", ["a"]) == (False, "Need at least 2 mods to bisect.")


@pytest.mark.parametrize("culprit", list("abcde"))
def test_finds_the_culprit_wherever_it_is(bis, culprit):
    assert run_to_end(bis, list("abcde"), culprit) == culprit
    assert not bis.get_state("g")["active"]


def test_every_suspect_stays_accounted_for(bis):
    """good + remaining suspects is always the original list, so the mods can be switched back on."""
    bis.start("g", list("abcd"))
    bis.report("g", works_fine=False)   # culprit is in [a, b] -> c, d are cleared
    s = bis.get_state("g")
    assert sorted(s["good_mods"] + s["bad_mods"]) == list("abcd")


def test_abort_ends_the_session(bis):
    bis.start("g", list("ab"))
    assert bis.abort("g")
    assert not bis.get_state("g")["active"]
    assert bis.report("g", True) == (False, "No active bisect session.")


# ---------------------------------------------------------------- API
H = {"X-Nexus-Token": "t"}


@pytest.fixture
def api(tmp_path, monkeypatch, ue_game, mm, bis):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    from core.telemetry import sensors
    running = {"on": False}
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "fps_monitor", sensors.FrameRateMonitor(exe=None))
    monkeypatch.setattr(app_mod, "_client_running", lambda g, s: running["on"])
    spec = {**ue_game, "client_exe": "Game.exe"}
    ids = []
    for i in range(4):
        ok, msg, mod = mm.install(spec, make_zip(tmp_path / f"Mod{i}.zip", {f"Mod{i}_P.pak": b"x"}))
        assert ok and mod["enabled"], msg
        ids.append(mod["id"])
    with TestClient(app_mod.app) as c:
        app_mod.state.update(spec=spec, game={"id": "g", "name": "Game", "install_path": spec["install_path"]})
        yield c, spec, ids, running


def enabled(mm, spec):
    return {m["id"] for m in mm.mods(spec["id"]) if m["enabled"]}


def test_api_bisect_toggles_mods_and_names_the_culprit(api, mm):
    c, spec, ids, _ = api
    r = c.post("/mods/bisect/start", json={"ids": ids}, headers=H).json()
    assert r["status"] == "success", r
    culprit = ids[2]
    for _ in range(4):
        state = c.get("/mods/bisect", headers=H).json()
        assert state["active"]
        assert enabled(mm, spec) == set(state["testing_mods"])   # only the half under test is ON
        r = c.post("/mods/bisect/report", json={"works": culprit not in state["testing_mods"]}, headers=H).json()
        assert r["status"] == "success", r
        if r["finished"]:
            break
    assert r["culprit"] == culprit
    assert enabled(mm, spec) == set(ids) - {culprit}   # everyone else back ON, culprit left OFF
    assert sorted(p.name for p in paks(spec).iterdir()) == ["Mod0_P.pak", "Mod1_P.pak", "Mod3_P.pak"]


def test_api_abort_switches_every_suspect_back_on(api, mm):
    c, spec, ids, _ = api
    c.post("/mods/bisect/start", json={"ids": ids}, headers=H)
    assert enabled(mm, spec) != set(ids)
    assert c.post("/mods/bisect/abort", headers=H).json()["status"] == "success"
    assert enabled(mm, spec) == set(ids)
    assert not c.get("/mods/bisect", headers=H).json()["active"]


def test_api_rejects_unknown_ids_and_a_running_game(api, mm):
    c, spec, ids, running = api
    assert c.post("/mods/bisect/start", json={"ids": [ids[0], "nope"]}, headers=H).status_code == 400
    running["on"] = True
    assert c.post("/mods/bisect/start", json={"ids": ids}, headers=H).status_code == 400
    assert enabled(mm, spec) == set(ids)   # nothing was touched
    assert not c.get("/mods/bisect", headers=H).json()["active"]
