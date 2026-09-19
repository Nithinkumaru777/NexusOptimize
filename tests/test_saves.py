"""Auto save-game backup: zip the game's save folders on exit, restore with one click.

Layout mirrors Windrose on this machine: %LOCALAPPDATA%\\R5\\Saved\\SaveProfiles holds a
RocksDB (the real saves), Saved\\SaveGames only input settings, Saved\\Logs is noise.
"""

import os
import zipfile

import pytest

from tests.test_fps import wait_for
from tests.test_upgrade import tree_hash


@pytest.fixture
def saves(tmp_path, monkeypatch):
    from core.safety import saves
    monkeypatch.setattr(saves, "ROOT", str(tmp_path / "savebackups"))
    return saves


@pytest.fixture
def game(tmp_path):
    saved = tmp_path / "LocalAppData" / "R5" / "Saved"
    for rel, data in {"SaveProfiles/p1/RocksDB/000078.sst": b"world", "SaveProfiles/p1/RocksDB/CURRENT": b"MANIFEST-1",
                      "SaveGames/EnhancedInputUserSettings.sav": b"input", "Logs/R5.log": b"log",
                      "Config/Windows/GameUserSettings.ini": b"[x]"}.items():
        (saved / rel).parent.mkdir(parents=True, exist_ok=True)
        (saved / rel).write_bytes(data)
    return {"id": "windrose", "name": "Windrose",
            "gus_path": str(saved / "Config" / "Windows" / "GameUserSettings.ini")}, saved


def test_unreal_save_folders_are_the_save_dirs_next_to_config(saves, game):
    spec, saved = game
    assert saves.save_dirs(spec) == [str(saved / "SaveGames"), str(saved / "SaveProfiles")]


def test_no_known_save_folder_means_none_not_a_guess(saves):
    assert saves.save_dirs({"id": "hitman3", "gus_path": None}) == []


def test_custom_folder_overrides_detection(saves, game, tmp_path):
    spec, _ = game
    custom = tmp_path / "MySaves"
    custom.mkdir()
    ok, _ = saves.set_folder(spec["id"], str(custom))
    assert ok and saves.save_dirs(spec) == [str(custom)]
    assert not saves.set_folder(spec["id"], str(tmp_path / "missing"))[0]
    saves.set_folder(spec["id"], None)
    assert len(saves.save_dirs(spec)) == 2


def test_snapshot_zips_only_save_folders(saves, game):
    spec, _ = game
    ok, _, snap = saves.snapshot(spec)
    assert ok and snap["files"] == 3
    with zipfile.ZipFile(os.path.join(saves.ROOT, spec["id"], snap["id"])) as z:
        assert sorted(z.namelist()) == ["SaveGames/EnhancedInputUserSettings.sav",
                                        "SaveProfiles/p1/RocksDB/000078.sst", "SaveProfiles/p1/RocksDB/CURRENT"]


def test_unchanged_saves_are_not_snapshotted_twice(saves, game):
    spec, saved = game
    saves.snapshot(spec)
    ok, msg, snap = saves.snapshot(spec)
    assert ok and snap is None and "unchanged" in msg
    (saved / "SaveProfiles" / "p1" / "RocksDB" / "000079.sst").write_bytes(b"more")
    assert saves.snapshot(spec)[2] is not None
    assert len(saves.snapshots(spec["id"])) == 2


def test_refuses_while_game_runs_because_the_save_db_is_open(saves, game):
    spec, _ = game
    ok, msg, _ = saves.snapshot(spec, running=True)
    assert not ok and "Close" in msg
    assert saves.snapshots(spec["id"]) == []


def test_old_snapshots_are_pruned(saves, game, monkeypatch):
    spec, saved = game
    monkeypatch.setattr(saves, "KEEP", 3)
    for i in range(5):
        (saved / "SaveGames" / "slot.sav").write_bytes(bytes([i]))
        saves.snapshot(spec)
    assert len(saves.snapshots(spec["id"])) == 3


def test_restore_brings_back_exact_saves_and_is_itself_undoable(saves, game):
    spec, saved = game
    before = tree_hash(saved)
    snap = saves.snapshot(spec)[2]
    (saved / "SaveProfiles" / "p1" / "RocksDB" / "000078.sst").write_bytes(b"corrupted")
    (saved / "SaveProfiles" / "p1" / "RocksDB" / "000099.sst").write_bytes(b"new")
    ok, _ = saves.restore(spec, snap["id"])
    assert ok and tree_hash(saved) == before
    assert len(saves.snapshots(spec["id"])) == 2   # the pre-restore state was kept


def test_restore_refuses_unknown_ids_and_a_running_game(saves, game):
    spec, _ = game
    snap = saves.snapshot(spec)[2]
    assert not saves.restore(spec, "../../evil.zip")[0]
    assert not saves.restore(spec, "nope.zip")[0]
    assert not saves.restore(spec, snap["id"], running=True)[0]


def test_game_exit_triggers_an_automatic_backup(saves, game, monkeypatch):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    _, saved = game
    running = {"on": True}
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "_client_running", lambda g, s: running["on"])
    monkeypatch.setattr(saves, "save_dirs", lambda s: [str(saved / "SaveProfiles")])   # the discovered game's id
    h = {"X-Nexus-Token": "t"}
    with TestClient(app_mod.app) as c:
        assert wait_for(lambda: c.get("/telemetry", headers=h).json()["game_running"] or None, timeout=8)
        game_id = c.get("/game", headers=h).json()["id"]
        assert saves.snapshots(game_id) == []
        running["on"] = False
        assert wait_for(lambda: saves.snapshots(game_id) or None, timeout=10)
        listed = c.get("/saves", headers=h).json()
        assert listed["snapshots"] and listed["dirs"]


def test_backups_use_maximum_deflate_compression(saves, game):
    import random
    import zlib
    spec, saved = game
    rng = random.Random(7)   # 4-letter noise: level 9 finds matches the default level 6 gives up on
    data = bytes(rng.choice(b"ACGT") for _ in range(300_000))
    (saved / "SaveGames" / "slot.sav").write_bytes(data)
    level = lambda n: len(zlib.compress(data, n)) - 6   # zlib header/trailer vs raw deflate
    assert level(9) < level(6)
    snap = saves.snapshot(spec)[2]
    with zipfile.ZipFile(os.path.join(saves.ROOT, spec["id"], snap["id"])) as z:
        info = z.getinfo("SaveGames/slot.sav")
    assert info.compress_type == zipfile.ZIP_DEFLATED and info.compress_size == level(9)


def test_a_failed_backup_reports_the_error_and_leaves_no_partial_zip(saves, game, monkeypatch):
    spec, _ = game

    def locked(*_a, **_k):
        raise PermissionError("save file is locked")
    monkeypatch.setattr(zipfile.ZipFile, "write", locked)
    ok, msg, snap = saves.snapshot(spec)
    assert not ok and snap is None and "locked" in msg
    assert os.listdir(os.path.join(saves.ROOT, spec["id"])) == []


def test_restore_is_refused_when_the_current_saves_cannot_be_backed_up_first(saves, game, monkeypatch):
    spec, saved = game
    snap = saves.snapshot(spec)[2]
    (saved / "SaveGames" / "EnhancedInputUserSettings.sav").write_bytes(b"newer progress")
    before = tree_hash(saved)

    def locked(*_a, **_k):
        raise PermissionError("save file is locked")
    monkeypatch.setattr(zipfile.ZipFile, "write", locked)
    ok, msg = saves.restore(spec, snap["id"])
    assert not ok and "locked" in msg
    assert tree_hash(saved) == before   # nothing overwritten without a safety copy


def test_restore_into_an_emptied_save_folder_still_works(saves, game):
    import shutil
    spec, saved = game
    before = tree_hash(saved)
    snap = saves.snapshot(spec)[2]
    for d in ("SaveGames", "SaveProfiles"):
        shutil.rmtree(saved / d)
        (saved / d).mkdir()
    ok, _ = saves.restore(spec, snap["id"])
    assert ok and tree_hash(saved) == before
