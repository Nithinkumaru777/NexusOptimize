"""Phase 0 acceptance: no invented keys, no simulated telemetry, token-gated API,
encoding-safe INI writes with backups, detected (not hardcoded) core classes."""

import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = [p for d in ("core", "ui") for p in (ROOT / d).rglob("*")
           if p.suffix in {".py", ".js", ".html", ".json"} and "node_modules" not in p.parts]

# v1 keys with no source, or mislabeled (see docs/DECISIONS.md D-002).
INVENTED = ["NRUICorrection", "PipelinedHandoff", "AutoSavePreset", "EnableHooks", "FilmicCurve",
            "LODStreamingDistance", "HUDExpandedCombatInfo", "ShadowResolutionMultiplier",
            "RenoDX.ColorGrade", "get_simulated_frame_stats", "note_fullscreen"]


def test_sources_found():
    assert len(SOURCES) >= 10


@pytest.mark.parametrize("key", INVENTED)
def test_no_invented_keys(key):
    hits = [str(p.relative_to(ROOT)) for p in SOURCES if key in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == [], f"{key} still referenced in {hits}"


def test_every_ini_key_written_cites_a_source():
    ini_src = (ROOT / "core/optimize/ini.py").read_text(encoding="utf-8")
    written = set(re.findall(r'_set_value\(ini, \w+, "([^"]+)"', ini_src))
    assert written == {"FrameGeneratorMode", "PresetPath"}
    comments = re.findall(r"(?:^#.*\n)+", ini_src, re.M)
    for key in written:
        assert any(key in c and "Source:" in c for c in comments), f"{key} has no Source: comment"


# ---------------------------------------------------------------- INI I/O
@pytest.fixture
def ini_mod(tmp_path, monkeypatch):
    from core.optimize import ini
    from core.safety import backup
    monkeypatch.setattr(backup, "BACKUP_ROOT", str(tmp_path / "backups"))
    return ini


@pytest.mark.parametrize("enc,bom", [("utf-16-le", b"\xff\xfe"), ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf")])
def test_fg_cap_preserves_encoding_and_backs_up(tmp_path, ini_mod, enc, bom):
    gus = tmp_path / "GameUserSettings.ini"
    original = bom + "[/Script/R5.R5SettingsLocal]\r\nFrameGeneratorType=DLSS\r\nFrameGeneratorMode=On4x\r\n".encode(enc)
    gus.write_bytes(original)

    ok, msg = ini_mod.cap_frame_generation(str(gus))
    assert ok and "On4x -> 2x" in msg
    raw = gus.read_bytes()
    assert raw.startswith(bom)
    text = raw[len(bom):].decode(enc)
    assert "FrameGeneratorMode=On\r\n" in text and "FrameGeneratorType=DLSS" in text
    backups = list((tmp_path / "backups").rglob("GameUserSettings.ini"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_fg_cap_leaves_2x_and_off_alone(tmp_path, ini_mod):
    for mode in ("On", "Off"):
        gus = tmp_path / f"{mode}.ini"
        gus.write_text(f"[/Script/R5.R5SettingsLocal]\nFrameGeneratorMode={mode}\n")
        before = gus.read_bytes()
        assert ini_mod.cap_frame_generation(str(gus))[0]
        assert gus.read_bytes() == before
    assert not (tmp_path / "backups").exists()


# ---------------------------------------------------------------- API
@pytest.fixture(scope="module")
def client():
    os.environ["NEXUS_TOKEN"] = "test-token"
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    app_mod.TOKEN = "test-token"
    with TestClient(app_mod.app) as c:
        yield c


def test_health_is_open(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/telemetry", "/games", "/game", "/launch_optimized/status"])
def test_requires_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Nexus-Token": "wrong"}).status_code == 401


def test_post_requires_token(client):
    assert client.post("/optimize/fix", json={"fix": "fg_cap"}).status_code == 401


def test_telemetry_is_real_or_empty(client):
    d = client.get("/telemetry", headers={"X-Nexus-Token": "test-token"}).json()
    assert d["fps"] is None and d["fps_source"] == "none"


def test_removed_routes_are_gone(client):
    h = {"X-Nexus-Token": "test-token"}
    for path in ("/inject", "/mods/inject"):
        assert client.post(path, json={}, headers=h).status_code == 404
    assert client.get("/mods", headers=h).status_code == 404


# ---------------------------------------------------------------- CPU topology
def test_server_cores_are_the_detected_efficiency_class():
    from core.optimize import cpu
    layout = cpu.core_layout(with_server=True)
    eff = cpu._efficiency_classes(layout["logical"])
    assert set(layout["server_cpus"]).isdisjoint(layout["client_cpus"])
    if layout["hybrid"]:
        assert all(eff[i] == min(eff) for i in layout["server_cpus"])
