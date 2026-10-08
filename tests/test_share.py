"""Modpack share codes (Epic P3 #5). No network: httpx.MockTransport stands in for dpaste.org."""

import json

import httpx
import pytest


@pytest.fixture
def share(monkeypatch):
    from core.mods import share
    seen = []

    def serve(handler):
        def wrapped(request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(share, "TRANSPORT", httpx.MockTransport(wrapped))
        return seen
    share.serve = serve
    return share


def _mod(id_, name, src=None, enabled=True):
    return {"id": id_, "name": name, "enabled": enabled, "source": src}


MANIFEST = [
    _mod("a", "Local Tweak"),                                                  # no Nexus source
    _mod("b", "HD Pack", {"nexus": "cyberpunk2077", "mod_id": 107, "file_id": 900, "version": "1.2"}),
    _mod("c", "Off Mod", {"nexus": "cyberpunk2077", "mod_id": 5, "file_id": 6}, enabled=False),
    _mod("d", "Other Game", {"nexus": "skyrim", "mod_id": 1, "file_id": 2}),
    _mod("e", "UI Fix", {"nexus": "cyberpunk2077", "mod_id": 33, "file_id": 44}),
]


def test_build_keeps_enabled_nexus_mods_of_this_game_in_load_order(share):
    pack, skipped = share.build("cyberpunk2077", MANIFEST)
    assert pack == {"v": 1, "game": "cyberpunk2077", "mods": [
        {"mod_id": 107, "file_id": 900, "name": "HD Pack", "version": "1.2", "order": 0},
        {"mod_id": 33, "file_id": 44, "name": "UI Fix", "version": None, "order": 1},
    ]}
    assert skipped == 1   # "Local Tweak" is ON but has no Nexus ids


def test_export_posts_json_and_prefixes_slug(share):
    seen = share.serve(lambda r: httpx.Response(200, text="https://dpaste.org/Ab3x\n"))
    code, pack, skipped = share.export("cyberpunk2077", MANIFEST)
    assert code == "NX-Ab3x"
    [req] = seen
    assert str(req.url) == "https://dpaste.org/api/"
    form = dict(httpx.QueryParams(req.content.decode()))
    assert form["format"] == "url" and form["expires"] == str(share.EXPIRES)
    assert json.loads(form["content"]) == pack


def test_export_refuses_empty_pack_without_network(share):
    seen = share.serve(lambda r: httpx.Response(200, text="https://dpaste.org/x"))
    with pytest.raises(share.ShareError, match="No Nexus"):
        share.export("cyberpunk2077", [_mod("a", "Local")])
    assert seen == []


def test_export_rejects_unexpected_paste_reply(share):
    share.serve(lambda r: httpx.Response(200, text="https://evil.example/../x"))
    with pytest.raises(share.ShareError):
        share.export("cyberpunk2077", MANIFEST)


def test_fetch_reads_raw_paste_and_validates(share):
    pack, _ = share.build("cyberpunk2077", MANIFEST)
    seen = share.serve(lambda r: httpx.Response(200, json=pack))
    assert share.fetch("  nx-Ab3x ") == pack
    assert str(seen[0].url) == "https://dpaste.org/Ab3x/raw"


@pytest.mark.parametrize("code", ["Ab3x", "NX-", "NX-../api", "NX-a/b", "NX-" + "a" * 40, None])
def test_bad_codes_never_reach_the_network(share, code):
    seen = share.serve(lambda r: httpx.Response(200, json={}))
    with pytest.raises(share.ShareError):
        share.fetch(code)
    assert seen == []


@pytest.mark.parametrize("body", [
    "not json",
    json.dumps([1, 2]),
    json.dumps({"v": 2, "game": "g", "mods": []}),
    json.dumps({"v": 1, "game": "../x", "mods": []}),
    json.dumps({"v": 1, "game": "g", "mods": [{"mod_id": "1; drop", "file_id": 2, "order": 0}]}),
    json.dumps({"v": 1, "game": "g", "mods": [{"mod_id": True, "file_id": 2, "order": 0}]}),
    json.dumps({"v": 1, "game": "g", "mods": [{"mod_id": -1, "file_id": 2, "order": 0}]}),
])
def test_fetch_rejects_malformed_payloads(share, body):
    share.serve(lambda r: httpx.Response(200, text=body))
    with pytest.raises(share.ShareError):
        share.fetch("NX-Ab3x")


def test_fetch_refuses_oversized_and_missing_pastes(share):
    share.serve(lambda r: httpx.Response(200, text="x" * (share.MAX_BYTES + 1)))
    with pytest.raises(share.ShareError, match="too large"):
        share.fetch("NX-Ab3x")
    share.serve(lambda r: httpx.Response(404, text="gone"))
    with pytest.raises(share.ShareError, match="expired"):
        share.fetch("NX-Ab3x")


def test_fetch_sorts_by_order_and_strips_names(share):
    share.serve(lambda r: httpx.Response(200, json={"v": 1, "game": "g", "mods": [
        {"mod_id": 2, "file_id": 3, "name": "<b>B</b>" * 50, "version": 7, "order": 1},
        {"mod_id": 1, "file_id": 1, "order": 0}]}))
    mods = share.fetch("NX-Ab3x")["mods"]
    assert [m["mod_id"] for m in mods] == [1, 2]
    assert mods[0]["name"] == "" and len(mods[1]["name"]) <= 120 and mods[1]["version"] == "7"


def test_compare_marks_installed_update_and_missing(share):
    pack = {"v": 1, "game": "cyberpunk2077", "mods": [
        {"mod_id": 107, "file_id": 900, "name": "HD Pack", "version": None, "order": 0},
        {"mod_id": 5, "file_id": 99, "name": "Off Mod", "version": None, "order": 1},
        {"mod_id": 77, "file_id": 1, "name": "New", "version": None, "order": 2},
    ]}
    assert [m["state"] for m in share.compare(pack, MANIFEST)] == ["installed", "other_file", "missing"]


# ---------------------------------------------------------------- API
H = {"X-Nexus-Token": "t"}


@pytest.fixture
def api(monkeypatch, share):
    from fastapi.testclient import TestClient
    from core.api import app as app_mod
    from core.mods import manager
    from core.telemetry import sensors
    monkeypatch.setattr(app_mod, "TOKEN", "t")
    monkeypatch.setattr(app_mod, "fps_monitor", sensors.FrameRateMonitor(exe=None))
    monkeypatch.setattr(manager, "mods", lambda game_id: MANIFEST)
    spec = {"id": "cp", "name": "Cyberpunk 2077", "nexus_domain": "cyberpunk2077"}
    with TestClient(app_mod.app) as c:
        app_mod.state.update(spec=spec, game={"id": "cp", "name": "Cyberpunk 2077"})
        yield c, app_mod


def test_api_export_returns_code(api, share):
    c, _ = api
    share.serve(lambda r: httpx.Response(200, text="https://dpaste.org/Zz9\n"))
    r = c.post("/share/export", json={}, headers=H).json()
    assert r["status"] == "success" and r["code"] == "NX-Zz9" and r["count"] == 2 and r["skipped"] == 1


def test_api_import_compares_against_installed(api, share):
    c, _ = api
    share.serve(lambda r: httpx.Response(200, json={"v": 1, "game": "cyberpunk2077", "mods": [
        {"mod_id": 107, "file_id": 900, "order": 0}, {"mod_id": 8, "file_id": 9, "order": 1}]}))
    r = c.post("/share/import", json={"code": "NX-Zz9"}, headers=H).json()
    assert r["status"] == "success" and r["game"] == "cyberpunk2077"
    assert [m["state"] for m in r["mods"]] == ["installed", "missing"]


def test_api_import_refuses_other_game_and_bad_code(api, share):
    c, _ = api
    share.serve(lambda r: httpx.Response(200, json={"v": 1, "game": "skyrim", "mods": []}))
    r = c.post("/share/import", json={"code": "NX-Zz9"}, headers=H)
    assert r.status_code == 400 and "skyrim" in r.json()["message"]
    r = c.post("/share/import", json={"code": "../../etc"}, headers=H)
    assert r.status_code == 400


def test_api_share_needs_a_game(api):
    c, app_mod = api
    app_mod.state.update(spec=None, game=None)
    assert c.post("/share/export", json={}, headers=H).status_code == 400
