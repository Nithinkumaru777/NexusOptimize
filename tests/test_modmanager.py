"""Archive mods: safe unzip into staging, auto-routing by file type, and
ON/OFF toggles that copy files into / out of the game folder (SPEC 7.1, 7.2).
File-based only - code files (.dll/.exe/.asi) are never placed."""

import os
import pathlib
import zipfile

import pytest

from tests.test_upgrade import backup, live, mod_files, tree_hash, ue_game  # noqa: F401 (fixtures)


@pytest.fixture
def mm(tmp_path, monkeypatch, live):
    from core.mods import manager
    monkeypatch.setattr(manager, "STATE_ROOT", str(tmp_path / "archive-state"))
    monkeypatch.setattr(manager, "STAGING_ROOT", str(tmp_path / "staging"))
    return manager


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


@pytest.fixture
def archive(tmp_path):
    return make_zip(tmp_path / "CoolMod-123-1-0.zip", {
        "CoolMod/Cool_P.pak": b"pak", "CoolMod/Cool_P.utoc": b"utoc", "CoolMod/Cool_P.ucas": b"ucas",
        "CoolMod/Engine.ini": b"[SystemSettings]\nr.X=1\n",
        "CoolMod/Presets/Cinematic.ini": b"[GENERAL]\nTechniques=SMAA@SMAA.fx\n",
        "CoolMod/readme.txt": b"hi", "CoolMod/inject.dll": b"MZ",
    })


def paks(game):
    return pathlib.Path(game["install_path"], "Proj", "Content", "Paks", "~mods")


def cfg(game):
    return pathlib.Path(os.path.dirname(game["gus_path"]))


# ---------------------------------------------------------------- routing
def test_install_routes_each_file_type_to_its_folder_and_enables(ue_game, archive, mm):
    ok, msg, mod = mm.install(ue_game, archive)
    assert ok, msg
    assert mod["enabled"] and mod["name"] == "CoolMod-123-1-0"
    assert sorted(p.name for p in paks(ue_game).iterdir()) == ["Cool_P.pak", "Cool_P.ucas", "Cool_P.utoc"]
    assert (cfg(ue_game) / "Engine.ini").read_bytes() == b"[SystemSettings]\nr.X=1\n"
    assert pathlib.Path(ue_game["install_path"], ue_game["reshade_dir"], "Cinematic.ini").exists()


def test_code_and_docs_are_skipped_and_reported(ue_game, archive, mm):
    _, _, mod = mm.install(ue_game, archive)
    skipped = {s["file"]: s["reason"] for s in mod["skipped"]}
    assert set(skipped) == {"CoolMod/readme.txt", "CoolMod/inject.dll"}
    assert "never" in skipped["CoolMod/inject.dll"]
    assert not any(p.suffix == ".dll" for p in pathlib.Path(ue_game["install_path"]).rglob("*"))


def test_unknown_ini_is_not_guessed_into_the_game(ue_game, mm, tmp_path):
    z = make_zip(tmp_path / "m.zip", {"Cool_P.pak": b"p", "Mystery.ini": b"[A]\nb=1\n"})
    _, _, mod = mm.install(ue_game, z)
    assert [s["file"] for s in mod["skipped"]] == ["Mystery.ini"]


def test_archive_with_nothing_routable_is_refused_and_leaves_no_staging(ue_game, mm, tmp_path):
    z = make_zip(tmp_path / "m.zip", {"readme.txt": b"x", "a.dll": b"MZ"})
    ok, msg, _ = mm.install(ue_game, z)
    assert not ok and "Nothing" in msg
    assert mm.mods("g") == []
    assert not any(pathlib.Path(mm.STAGING_ROOT).rglob("*.*"))


def test_anticheat_games_only_get_config_files(ue_game, archive, mm):
    pathlib.Path(ue_game["install_path"], "BattlEye").mkdir()
    ok, _, mod = mm.install(ue_game, archive)
    assert ok
    assert not paks(ue_game).exists()
    assert (cfg(ue_game) / "Engine.ini").read_bytes() == b"[SystemSettings]\nr.X=1\n"
    assert any("BattlEye" in s["reason"] for s in mod["skipped"])


# ---------------------------------------------------------------- toggles
def test_toggle_off_restores_game_byte_identical_and_on_redeploys(ue_game, archive, mm):
    before_install, before_cfg = tree_hash(ue_game["install_path"]), tree_hash(cfg(ue_game))
    _, _, mod = mm.install(ue_game, archive)

    ok, msg = mm.set_enabled(ue_game, mod["id"], False)
    assert ok, msg
    assert tree_hash(ue_game["install_path"]) == before_install
    assert tree_hash(cfg(ue_game)) == before_cfg
    assert mm.mods("g")[0]["enabled"] is False

    assert mm.set_enabled(ue_game, mod["id"], True)[0]
    assert (paks(ue_game) / "Cool_P.pak").read_bytes() == b"pak"
    assert (cfg(ue_game) / "Engine.ini").read_bytes() == b"[SystemSettings]\nr.X=1\n"


def test_toggles_refused_while_game_runs(ue_game, archive, mm):
    _, _, mod = mm.install(ue_game, archive)
    ok, msg = mm.set_enabled(ue_game, mod["id"], False, running=True)
    assert not ok and "Close" in msg
    assert (paks(ue_game) / "Cool_P.pak").exists()


def test_install_while_running_stages_but_stays_off(ue_game, archive, mm):
    ok, msg, mod = mm.install(ue_game, archive, running=True)
    assert ok and not mod["enabled"] and "Close" in msg
    assert not paks(ue_game).exists()


def test_two_enabled_mods_cannot_own_the_same_file(ue_game, archive, mm, tmp_path):
    assert mm.install(ue_game, archive)[0]
    other = make_zip(tmp_path / "other.zip", {"Cool_P.pak": b"other"})
    ok, msg, mod = mm.install(ue_game, other)
    assert ok and not mod["enabled"] and "CoolMod" in msg
    assert (paks(ue_game) / "Cool_P.pak").read_bytes() == b"pak"


def test_uninstall_restores_game_and_deletes_staging(ue_game, archive, mm):
    before = tree_hash(ue_game["install_path"])
    _, _, mod = mm.install(ue_game, archive)
    ok, msg = mm.uninstall(ue_game, mod["id"])
    assert ok, msg
    assert tree_hash(ue_game["install_path"]) == before
    assert mm.mods("g") == []
    assert not any(pathlib.Path(mm.STAGING_ROOT).rglob("*.*"))


# ---------------------------------------------------------------- hostile archives
@pytest.mark.parametrize("name", ["../evil.pak", "..\\evil.pak", "/abs/evil.pak", "C:/evil.pak", "a/../../evil.pak"])
def test_zip_slip_entries_are_refused(ue_game, mm, tmp_path, name):
    z = make_zip(tmp_path / "bad.zip", {name: b"x", "ok.pak": b"p"})
    ok, msg, _ = mm.install(ue_game, z)
    assert not ok and "unsafe" in msg.lower()
    assert not list(tmp_path.rglob("evil.pak"))


def test_oversized_archive_is_refused(ue_game, mm, tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MAX_UNPACKED_BYTES", 10)
    z = make_zip(tmp_path / "big.zip", {"big.pak": b"x" * 11})
    ok, msg, _ = mm.install(ue_game, z)
    assert not ok and "too large" in msg


def test_non_archive_and_7z_without_7zip_give_clear_errors(ue_game, mm, tmp_path, monkeypatch):
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    assert "not a valid" in mm.install(ue_game, str(tmp_path / "x.zip"))[1]
    monkeypatch.setattr(mm, "_seven_zip", lambda: None)
    (tmp_path / "m.7z").write_bytes(b"7z")
    assert "7-Zip" in mm.install(ue_game, str(tmp_path / "m.7z"))[1]


def test_corrupt_or_encrypted_zip_members_fail_cleanly(ue_game, mm, tmp_path):
    z = tmp_path / "crc.zip"
    make_zip(z, {"a.pak": b"pakdata" * 50})
    raw = bytearray(z.read_bytes())
    raw[raw.index(b"a.pak") + 5] ^= 0xFF   # corrupt first byte of the stored member data
    z.write_bytes(bytes(raw))
    ok, msg, _ = mm.install(ue_game, str(z))
    assert not ok and "could not" in msg.lower()
    assert not any(pathlib.Path(mm.STAGING_ROOT).rglob("*.*"))


def test_uninstall_never_deletes_outside_staging(ue_game, archive, mm, tmp_path):
    _, _, mod = mm.install(ue_game, archive)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x")
    items = mm.mods("g")
    items[0]["staging"] = str(victim)
    mm._save("g", items)
    assert mm.uninstall(ue_game, mod["id"])[0]
    assert (victim / "keep.txt").exists()


# ---------------------------------------------------------------- Windows MAX_PATH
def test_staging_dir_name_is_short_regardless_of_archive_name(ue_game, mm, tmp_path):
    z = make_zip(tmp_path / ("VeryLongModName-" * 8 + ".zip"), {"Engine.ini": b"[SystemSettings]\n"})
    ok, msg, mod = mm.install(ue_game, z)
    assert ok, msg
    assert len(os.path.basename(mod["staging"])) <= 16
    assert mod["name"] == "VeryLongModName-" * 8


def test_deeply_nested_archive_installs_toggles_and_uninstalls_past_max_path(ue_game, mm, tmp_path):
    deep = "/".join(f"NestedFolderLevel{i:02}" for i in range(15))   # ~300 chars on its own
    z = make_zip(tmp_path / "Deep.zip", {f"{deep}/Engine.ini": b"[SystemSettings]\nr.Deep=1\n"})
    ok, msg, mod = mm.install(ue_game, z)
    assert ok, msg
    assert (cfg(ue_game) / "Engine.ini").read_bytes() == b"[SystemSettings]\nr.Deep=1\n"
    assert mm.set_enabled(ue_game, mod["id"], False)[0]
    assert mm.set_enabled(ue_game, mod["id"], True)[0]
    assert mm.uninstall(ue_game, mod["id"])[0]
    assert not os.path.exists(mod["staging"])


@pytest.mark.skipif(os.name != "nt", reason="the \\\\?\\ prefix is Windows-only")
@pytest.mark.parametrize("path, expected", [
    (r"C:\a\b", r"\\?\C:\a\b"),
    (r"\\?\C:\a\b", r"\\?\C:\a\b"),
    (r"\\server\share\a", r"\\?\UNC\server\share\a"),
])
def test_long_path_prefix(mm, path, expected):
    assert mm._long(path) == expected


# ---------------------------------------------------------------- Glacier / generic games
@pytest.fixture
def glacier_game(tmp_path):
    root = tmp_path / "007 First Light"
    (root / "Runtime").mkdir(parents=True)
    (root / "Runtime" / "chunk0.rpkg").write_bytes(b"base")
    (root / "Retail").mkdir()
    (root / "Retail" / "settings.cfg").write_bytes(b"orig")
    return {"id": "007", "name": "007 First Light", "engine": "Glacier", "profile": "generic",
            "install_path": str(root), "reshade_dir": "Retail", "gus_path": None}


def test_glacier_rpkg_goes_to_runtime_even_when_nested_in_the_archive(glacier_game, mm, tmp_path):
    z = make_zip(tmp_path / "Bond.zip", {"Bond Mod v2/files/chunk0patch9.rpkg": b"mod"})
    ok, msg, mod = mm.install(glacier_game, z)
    assert ok, msg
    assert pathlib.Path(glacier_game["install_path"], "Runtime", "chunk0patch9.rpkg").read_bytes() == b"mod"


def test_generic_fallback_mirrors_archive_into_root_and_strips_wrapper_folder(glacier_game, mm, tmp_path):
    before = tree_hash(glacier_game["install_path"])
    z = make_zip(tmp_path / "m.zip", {
        "MyMod 1.2/Retail/settings.cfg": b"modded", "MyMod 1.2/Retail/textures/new.tex": b"tex",
        "MyMod 1.2/readme.txt": b"docs", "MyMod 1.2/Retail/hook.dll": b"MZ", "MyMod 1.2/install.bat": b"x",
    })
    ok, msg, mod = mm.install(glacier_game, z)
    assert ok, msg
    root = pathlib.Path(glacier_game["install_path"])
    assert (root / "Retail" / "settings.cfg").read_bytes() == b"modded"
    assert (root / "Retail" / "textures" / "new.tex").read_bytes() == b"tex"
    assert not (root / "MyMod 1.2").exists() and not (root / "readme.txt").exists()
    assert {s["file"] for s in mod["skipped"]} == {"MyMod 1.2/readme.txt", "MyMod 1.2/Retail/hook.dll",
                                                   "MyMod 1.2/install.bat"}

    assert mm.set_enabled(glacier_game, mod["id"], False)[0]
    assert tree_hash(glacier_game["install_path"]) == before


def test_generic_fallback_keeps_unknown_top_folder_as_is(glacier_game, mm, tmp_path):
    z = make_zip(tmp_path / "m.zip", {"mods/cool.bin": b"b"})
    assert mm.install(glacier_game, z)[0]
    assert pathlib.Path(glacier_game["install_path"], "mods", "cool.bin").read_bytes() == b"b"


def test_generic_fallback_is_off_for_anticheat_and_unreal_games(glacier_game, ue_game, mm, tmp_path):
    z = make_zip(tmp_path / "m.zip", {"data/cool.bin": b"b"})
    pathlib.Path(glacier_game["install_path"], "EasyAntiCheat").mkdir()
    ok, msg, _ = mm.install(glacier_game, z)
    assert not ok and "EasyAntiCheat" in msg
    assert not mm.install(ue_game, z)[0]
