"""Nexus Mods v1 client (SPEC 7.3). No network: httpx.MockTransport stands in
for api.nexusmods.com, so these tests pin the request shape we send."""

import httpx
import pytest


@pytest.fixture
def nexus(monkeypatch):
    from core.mods import nexus
    nexus._cache.clear()
    seen = []

    def serve(handler):
        def wrapped(request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(nexus, "TRANSPORT", httpx.MockTransport(wrapped))
        return seen
    nexus.serve = serve
    return nexus


RL = {"X-RL-Hourly-Remaining": "96", "X-RL-Daily-Remaining": "2488"}


def test_browse_sends_apikey_and_reads_rate_limits(nexus):
    seen = nexus.serve(lambda r: httpx.Response(200, json=[{"mod_id": 7, "name": "Cool"}], headers=RL))
    mods = nexus.browse("KEY", "cyberpunk2077", "trending")
    assert mods == [{"mod_id": 7, "name": "Cool"}]
    [req] = seen
    assert str(req.url) == "https://api.nexusmods.com/v1/games/cyberpunk2077/mods/trending.json"
    assert req.headers["apikey"] == "KEY"
    assert nexus.limits == {"hourly": 96, "daily": 2488}


def test_browse_is_cached(nexus):
    seen = nexus.serve(lambda r: httpx.Response(200, json=[]))
    nexus.browse("KEY", "g", "latest_added")
    nexus.browse("KEY", "g", "latest_added")
    assert len(seen) == 1


@pytest.mark.parametrize("domain,feed", [("../users", "trending"), ("g", "../../x"), ("", "trending")])
def test_bad_domain_or_feed_never_reaches_the_network(nexus, domain, feed):
    seen = nexus.serve(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(nexus.NexusError):
        nexus.browse("KEY", domain, feed)
    assert seen == []


@pytest.mark.parametrize("status,text", [(401, "API key"), (403, "Premium"), (404, "not found"), (429, "rate limit")])
def test_http_errors_become_plain_messages(nexus, status, text):
    nexus.serve(lambda r: httpx.Response(status, json={"message": "x"}))
    with pytest.raises(nexus.NexusError, match=text):
        nexus.files("KEY", "g", 1)


def test_download_link_passes_nxm_key_and_expires(nexus):
    seen = nexus.serve(lambda r: httpx.Response(200, json=[{"URI": "https://cf-files.nexusmods.com/x.zip"}]))
    assert nexus.download_url("KEY", "g", 5, 9, key="abc", expires="1700000000") == "https://cf-files.nexusmods.com/x.zip"
    assert seen[0].url.path == "/v1/games/g/mods/5/files/9/download_link.json"
    assert dict(seen[0].url.params) == {"key": "abc", "expires": "1700000000"}


def test_parse_nxm():
    from core.mods import nexus
    got = nexus.parse_nxm("nxm://cyberpunk2077/mods/107/files/12345?key=Ab-c_1&expires=1700000000&user_id=9")
    assert got == {"domain": "cyberpunk2077", "mod_id": 107, "file_id": 12345, "key": "Ab-c_1", "expires": "1700000000"}


@pytest.mark.parametrize("url", ["https://x/mods/1/files/2", "nxm://g/mods/x/files/2", "nxm://../mods/1/files/2", "nxm://g/mods/1"])
def test_parse_nxm_rejects_malformed(url):
    from core.mods import nexus
    with pytest.raises(nexus.NexusError):
        nexus.parse_nxm(url)


def test_download_streams_to_a_sanitized_name_and_checks_size(nexus, tmp_path):
    nexus.serve(lambda r: httpx.Response(200, content=b"zipbytes"))
    path = nexus.download("https://cf-files.nexusmods.com/a/..%5C..%5Cevil.zip", str(tmp_path), expected_size=8)
    assert path.startswith(str(tmp_path)) and open(path, "rb").read() == b"zipbytes"
    with pytest.raises(nexus.NexusError, match="size"):
        nexus.download("https://cf-files.nexusmods.com/b.zip", str(tmp_path), expected_size=99)
    assert not (tmp_path / "b.zip").exists()


def test_download_refuses_plain_http(nexus, tmp_path):
    with pytest.raises(nexus.NexusError):
        nexus.download("http://cf-files.nexusmods.com/b.zip", str(tmp_path))


def test_domain_for_prefers_profile_value_then_slugs_name():
    from core.mods import nexus
    assert nexus.domain_for({"name": "Cyberpunk 2077", "nexus_domain": "cyberpunk2077"}) == "cyberpunk2077"
    assert nexus.domain_for({"name": "Hogwarts Legacy"}) == "hogwartslegacy"


def _mod(id, mod_id, uploaded=None, installed=0, domain="g"):
    return {"id": id, "installed": installed,
            "source": {"nexus": domain, "mod_id": mod_id, "file_id": 1, "version": "1.0", "uploaded": uploaded}}


def test_check_updates_flags_mods_whose_nexus_files_changed_after_ours(nexus):
    seen = nexus.serve(lambda r: httpx.Response(200, json=[
        {"mod_id": 7, "latest_file_update": 200, "latest_mod_activity": 200},
        {"mod_id": 8, "latest_file_update": 200, "latest_mod_activity": 200}]))
    installed = [_mod("new", 7, uploaded=100), _mod("current", 8, uploaded=300), _mod("quiet", 9, uploaded=1),
                 _mod("other-game", 7, uploaded=1, domain="h"), {"id": "local", "installed": 0, "source": None}]
    assert nexus.check_updates("KEY", "g", installed) == ["new"]
    assert nexus.check_updates("KEY", "g", installed) == ["new"]
    [req] = seen   # one gentle, cached call - never one request per mod
    assert req.url.path == "/v1/games/g/mods/updated.json"
    assert dict(req.url.params) == {"period": "1w"}


def test_check_updates_falls_back_to_install_time_for_older_installs(nexus):
    nexus.serve(lambda r: httpx.Response(200, json=[{"mod_id": 7, "latest_file_update": 200}]))
    assert nexus.check_updates("KEY", "g", [_mod("a", 7, installed=100), _mod("b", 7, installed=300)]) == ["a"]


# ---------------------------------------------------------------- API
from tests.test_upgrade import H, client  # noqa: E402,F401 (fixture)


@pytest.mark.parametrize("path", ["/nexus/status", "/mods/archive"])
def test_nexus_routes_require_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=H).status_code == 200


def test_browse_without_a_key_asks_for_one(client, monkeypatch):
    from core.mods import nexus
    monkeypatch.setattr(nexus, "get_key", lambda: None)
    r = client.get("/nexus/mods", params={"feed": "trending", "domain": "g"}, headers=H)
    assert r.status_code == 400 and "API key" in r.json()["message"]


def test_status_never_returns_the_key(client, monkeypatch):
    from core.mods import nexus
    monkeypatch.setattr(nexus, "get_key", lambda: "SECRET-KEY")
    assert "SECRET-KEY" not in client.get("/nexus/status", headers=H).text


def test_toggle_unknown_archive_mod_is_an_error(client):
    r = client.post("/mods/archive/toggle", json={"id": "nope", "enabled": True}, headers=H)
    assert r.json()["status"] == "error"


def test_updates_route_lists_outdated_archive_mods(client, monkeypatch):
    from core.api import app as app_mod
    from core.mods import manager, nexus
    nexus._cache.clear()
    monkeypatch.setitem(app_mod.state, "spec", {"id": "g1", "name": "G", "nexus_domain": "g"})
    monkeypatch.setattr(nexus, "get_key", lambda: "KEY")
    monkeypatch.setattr(manager, "mods", lambda game_id: [_mod("m1", 7, uploaded=100)])
    monkeypatch.setattr(nexus, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=[{"mod_id": 7, "latest_file_update": 200}])))
    r = client.get("/nexus/updates", headers=H)
    assert r.status_code == 200 and r.json()["updates"] == ["m1"]


def test_nexus_install_persists_mod_id_version_and_upload_time(client, monkeypatch, tmp_path):
    from core.api import app as app_mod
    from core.mods import manager, nexus
    monkeypatch.setitem(app_mod.state, "spec", {"id": "g1", "name": "G"})
    monkeypatch.setattr(app_mod, "DOWNLOADS", str(tmp_path))
    monkeypatch.setattr(app_mod, "_client_running", lambda game, spec: False)
    monkeypatch.setattr(nexus, "get_key", lambda: "KEY")

    def serve(r):
        if r.url.path.endswith("/files.json"):
            return httpx.Response(200, json={"files": [{"file_id": 9, "version": "2.1", "uploaded_timestamp": 1700000000}]})
        if r.url.path.endswith("/download_link.json"):
            return httpx.Response(200, json=[{"URI": "https://cf-files.nexusmods.com/m.zip"}])
        return httpx.Response(200, content=b"zip")
    monkeypatch.setattr(nexus, "TRANSPORT", httpx.MockTransport(serve))
    got = {}
    monkeypatch.setattr(manager, "install", lambda spec, path, running, source: (got.update(source), (True, "ok", None))[1])
    assert client.post("/nexus/install", json={"domain": "g", "mod_id": 5, "file_id": 9}, headers=H).json()["status"] == "success"
    assert got == {"nexus": "g", "mod_id": 5, "file_id": 9, "version": "2.1", "uploaded": 1700000000}
