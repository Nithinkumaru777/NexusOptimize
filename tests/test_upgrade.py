"""Upgrade acceptance: backup/rollback, file-based live mods (no process/memory
access), system resource stats and their token-gated routes."""

import hashlib
import os
import pathlib

import psutil
import pytest


def tree_hash(root):
    """{relative path: sha256} for every file under root."""
    root = pathlib.Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def backup(tmp_path, monkeypatch):
    from core.safety import backup
    monkeypatch.setattr(backup, "BACKUP_ROOT", str(tmp_path / "backups"))
    return backup


# ---------------------------------------------------------------- backup / rollback
def test_backup_then_restore_brings_back_exact_bytes(tmp_path, backup):
    f = tmp_path / "Engine.ini"
    f.write_bytes(b"[SystemSettings]\r\nr.Foo=1\r\n")
    backup.backup(str(f))
    f.write_bytes(b"changed")

    [entry] = backup.list_backups()
    assert entry["original"] == str(f)
    ok, _ = backup.restore(entry["id"])
    assert ok and f.read_bytes() == b"[SystemSettings]\r\nr.Foo=1\r\n"


def test_restore_backs_up_the_current_file_first(tmp_path, backup):
    f = tmp_path / "a.ini"
    f.write_text("v1")
    backup.backup(str(f))
    f.write_text("v2")
    first = backup.list_backups()[0]["id"]
    backup.restore(first)
    assert sorted(b.read_text() for b in (tmp_path / "backups").rglob("a.ini")) == ["v1", "v2"]


def test_same_second_backups_do_not_overwrite_each_other(tmp_path, backup):
    f = tmp_path / "a.ini"
    for v in ("one", "two", "three"):
        f.write_text(v)
        backup.backup(str(f))
    assert len(backup.list_backups()) == 3


def test_restore_rejects_ids_outside_the_backup_root(tmp_path, backup):
    (tmp_path / "secret.ini").write_text("x")
    (tmp_path / "secret.ini.source").write_text(str(tmp_path / "victim.ini"))
    ok, _ = backup.restore("../secret.ini")
    assert not ok and not (tmp_path / "victim.ini").exists()


# ---------------------------------------------------------------- live mods
@pytest.fixture
def live(tmp_path, monkeypatch, backup):
    from core.mods import live
    monkeypatch.setattr(live, "STATE_ROOT", str(tmp_path / "state"))
    return live


@pytest.fixture
def ue_game(tmp_path):
    root = tmp_path / "Game"
    (root / "Proj" / "Content" / "Paks").mkdir(parents=True)
    (root / "Proj" / "Content" / "Paks" / "pakchunk0-Windows.pak").write_bytes(b"base")
    win64 = root / "Proj" / "Binaries" / "Win64"
    win64.mkdir(parents=True)
    (win64 / "ReShade.ini").write_text("[GENERAL]\nPresetPath=.\\ReShadePreset.ini\n")
    cfg = tmp_path / "LocalAppData" / "Proj" / "Saved" / "Config" / "Windows"
    cfg.mkdir(parents=True)
    (cfg / "Engine.ini").write_text("[SystemSettings]\n")
    return {"id": "g", "name": "Game", "profile": "unreal", "install_path": str(root),
            "reshade_dir": os.path.join("Proj", "Binaries", "Win64"),
            "gus_path": str(cfg / "GameUserSettings.ini")}


@pytest.fixture
def mod_files(tmp_path):
    src = tmp_path / "downloads"
    src.mkdir()
    for name, data in {"Cool_P.pak": b"pak", "Cool_P.utoc": b"utoc", "Cool_P.ucas": b"ucas",
                       "Cinematic.ini": b"Techniques=SMAA\n", "Engine.ini": b"[SystemSettings]\nr.X=1\n",
                       "evil.dll": b"MZ"}.items():
        (src / name).write_bytes(data)
    return src


def test_pak_mod_goes_to_tilde_mods_and_purges_byte_identical(ue_game, mod_files, live):
    before = tree_hash(ue_game["install_path"])
    ok, msg, mod = live.apply(ue_game, "pak", [str(mod_files / n) for n in ("Cool_P.pak", "Cool_P.utoc", "Cool_P.ucas")])
    assert ok, msg
    paks = pathlib.Path(ue_game["install_path"], "Proj", "Content", "Paks", "~mods")
    assert sorted(p.name for p in paks.iterdir()) == ["Cool_P.pak", "Cool_P.ucas", "Cool_P.utoc"]
    assert [m["id"] for m in live.installed("g")] == [mod["id"]]

    ok, msg = live.remove(ue_game, mod["id"])
    assert ok, msg
    assert tree_hash(ue_game["install_path"]) == before
    assert live.installed("g") == []


def test_pak_needs_a_pak_file_and_rejects_other_extensions(ue_game, mod_files, live):
    assert not live.apply(ue_game, "pak", [str(mod_files / "Cool_P.utoc")])[0]
    assert not live.apply(ue_game, "pak", [str(mod_files / "Cool_P.pak"), str(mod_files / "evil.dll")])[0]


def test_pak_refused_for_non_unreal_games(ue_game, mod_files, live):
    ok, msg, _ = live.apply({**ue_game, "profile": "generic"}, "pak", [str(mod_files / "Cool_P.pak")])
    assert not ok and "Unreal" in msg


def test_nothing_is_swapped_while_the_game_runs_except_reshade_presets(ue_game, mod_files, live):
    before = tree_hash(ue_game["install_path"])
    assert not live.apply(ue_game, "pak", [str(mod_files / "Cool_P.pak")], running=True)[0]
    target = os.path.join(os.path.dirname(ue_game["gus_path"]), "Engine.ini")
    assert not live.apply(ue_game, "config", [str(mod_files / "Engine.ini")], target=target, running=True)[0]
    assert tree_hash(ue_game["install_path"]) == before

    ok, msg, _ = live.apply(ue_game, "reshade", [str(mod_files / "Cinematic.ini")], running=True)
    assert ok, msg
    win64 = pathlib.Path(ue_game["install_path"], ue_game["reshade_dir"])
    assert (win64 / "Cinematic.ini").exists()
    # ReShade rewrites ReShade.ini while it runs, so it is left alone.
    assert "PresetPath=.\\ReShadePreset.ini" in (win64 / "ReShade.ini").read_text()


def test_reshade_preset_is_activated_and_fully_reverted(ue_game, mod_files, live):
    before = tree_hash(ue_game["install_path"])
    ok, msg, mod = live.apply(ue_game, "reshade", [str(mod_files / "Cinematic.ini")])
    assert ok, msg
    reshade_ini = pathlib.Path(ue_game["install_path"], ue_game["reshade_dir"], "ReShade.ini")
    assert "PresetPath=.\\Cinematic.ini" in reshade_ini.read_text()

    assert live.remove(ue_game, mod["id"])[0]
    assert tree_hash(ue_game["install_path"]) == before


def test_reshade_needs_an_existing_reshade_install(ue_game, mod_files, live, tmp_path):
    os.remove(os.path.join(ue_game["install_path"], ue_game["reshade_dir"], "ReShade.ini"))
    ok, msg, _ = live.apply(ue_game, "reshade", [str(mod_files / "Cinematic.ini")])
    assert not ok and "ReShade" in msg


def test_config_swap_backs_up_and_restores(ue_game, mod_files, live):
    target = os.path.join(os.path.dirname(ue_game["gus_path"]), "Engine.ini")
    ok, msg, mod = live.apply(ue_game, "config", [str(mod_files / "Engine.ini")], target=target)
    assert ok, msg
    assert pathlib.Path(target).read_bytes() == b"[SystemSettings]\nr.X=1\n"
    assert live.remove(ue_game, mod["id"])[0]
    assert pathlib.Path(target).read_text() == "[SystemSettings]\n"


@pytest.mark.parametrize("target", [r"C:\Windows\System32\drivers\etc\hosts", "..\\..\\outside.ini", None])
def test_config_target_must_be_inside_the_game_or_its_config_dir(ue_game, mod_files, live, target):
    if target and not os.path.isabs(target):
        target = os.path.join(ue_game["install_path"], target)
    ok, _, _ = live.apply(ue_game, "config", [str(mod_files / "Engine.ini")], target=target)
    assert not ok


def test_anticheat_games_block_pak_and_reshade_but_allow_config(ue_game, mod_files, live):
    pathlib.Path(ue_game["install_path"], "EasyAntiCheat").mkdir()
    ok, msg, _ = live.apply(ue_game, "pak", [str(mod_files / "Cool_P.pak")])
    assert not ok and "EasyAntiCheat" in msg
    assert not live.apply(ue_game, "reshade", [str(mod_files / "Cinematic.ini")])[0]
    target = os.path.join(os.path.dirname(ue_game["gus_path"]), "Engine.ini")
    assert live.apply(ue_game, "config", [str(mod_files / "Engine.ini")], target=target)[0]


def test_two_mods_cannot_own_the_same_file(ue_game, mod_files, live):
    assert live.apply(ue_game, "pak", [str(mod_files / "Cool_P.pak")])[0]
    ok, msg, _ = live.apply(ue_game, "pak", [str(mod_files / "Cool_P.pak")])
    assert not ok and "Cool_P.pak" in msg


# ---------------------------------------------------------------- system resources
def test_system_stats_are_real_readings():
    from core.telemetry import sensors
    s = sensors.get_system_stats()
    assert len(s["cpu_per_core"]) == psutil.cpu_count(logical=True)
    assert all(0 <= c <= 100 for c in s["cpu_per_core"])
    assert 0 < s["ram_used_mb"] <= s["ram_total_mb"]
    top = [p["ram_mb"] for p in s["top_processes"]]
    assert 0 < len(top) <= 8 and top == sorted(top, reverse=True)


def test_per_core_is_measured_from_a_fresh_thread():
    # FastAPI serves sync routes from pool threads; psutil keeps its interval=None
    # baseline per thread, so a new thread's first read was all 0.0 (not a measurement).
    import threading
    from core.telemetry import sensors
    stop, out = threading.Event(), {}
    burner = threading.Thread(target=lambda: [None for _ in iter(stop.is_set, True)])
    burner.start()
    try:
        t = threading.Thread(target=lambda: out.update(sensors.get_system_stats()))
        t.start()
        t.join()
    finally:
        stop.set()
        burner.join()
    assert any(c > 0 for c in out["cpu_per_core"])


# ---------------------------------------------------------------- API
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    app_mod.TOKEN = "test-token"
    with TestClient(app_mod.app) as c:
        yield c


H = {"X-Nexus-Token": "test-token"}


@pytest.mark.parametrize("path", ["/system/resources", "/backups", "/mods/live", "/bench", "/saves"])
def test_new_routes_require_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=H).status_code == 200


def test_live_mod_rejects_unknown_kind(client):
    r = client.post("/mods/live/apply", json={"kind": "trainer", "paths": []}, headers=H)
    assert r.status_code == 400
