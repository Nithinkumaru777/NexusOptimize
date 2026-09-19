"""Nexus Mods public API v1 client (SPEC 7.3).

Personal API key for now (SSO needs a registered app slug), kept in Windows
Credential Manager via keyring - never in a plain file. Free accounts need the
key/expires pair from an nxm:// "Mod Manager Download" link; Premium accounts
can request download links directly.
Docs: https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0
"""

import os
import re
import time
import urllib.parse

import httpx
import keyring
from keyring.errors import KeyringError

API = "https://api.nexusmods.com/v1"
HEADERS = {"Application-Name": "NexusOptimize", "Application-Version": "2.1.0", "Accept": "application/json"}
FEEDS = {"trending", "latest_added", "latest_updated"}
KEYRING = ("NexusOptimize", "nexusmods-api-key")
CACHE_SECONDS = 300
TRANSPORT = None                      # tests swap in httpx.MockTransport
_DOMAIN = re.compile(r"^[a-z0-9]+$")  # e.g. "cyberpunk2077", "skyrimspecialedition"
_cache = {}
limits = {"hourly": None, "daily": None}


class NexusError(Exception):
    pass


# ---------------------------------------------------------------- key storage
def get_key():
    try:
        return keyring.get_password(*KEYRING)
    except KeyringError:
        return None


def set_key(key):
    try:
        if key:
            keyring.set_password(*KEYRING, key)
        elif get_key():
            keyring.delete_password(*KEYRING)
    except KeyringError as e:
        raise NexusError(f"Could not use Windows Credential Manager: {e}")


# ---------------------------------------------------------------- requests
def _client():
    return httpx.Client(transport=TRANSPORT, timeout=30, follow_redirects=True)


_ERRORS = {
    401: "Nexus rejected the API key - check it in the Nexus panel.",
    403: "Nexus refused the download link. Free accounts: use 'Mod Manager Download' on the mod's Nexus page "
         "(Premium accounts can download in-app).",
    404: "Mod, file or game not found on Nexus Mods.",
    429: "Nexus rate limit reached - try again later.",
}


def _get(apikey, path, params=None):
    try:
        with _client() as c:
            r = c.get(API + path, params=params, headers={**HEADERS, "apikey": apikey})
    except httpx.HTTPError as e:
        raise NexusError(f"Could not reach Nexus Mods: {e}")
    for k in limits:
        v = r.headers.get(f"x-rl-{k}-remaining", "")
        if v.isdigit():
            limits[k] = int(v)
    if r.status_code in _ERRORS:
        raise NexusError(_ERRORS[r.status_code])
    if r.status_code >= 400:
        raise NexusError(f"Nexus Mods error {r.status_code}.")
    return r.json()


def _domain(domain):
    if not isinstance(domain, str) or not _DOMAIN.match(domain):
        raise NexusError(f"'{domain}' is not a Nexus game domain (lowercase letters and digits).")
    return domain


def domain_for(spec):
    """Nexus game domain for a library profile: explicit value, else the slugged name."""
    return spec.get("nexus_domain") or re.sub(r"[^a-z0-9]", "", spec["name"].lower())


# ---------------------------------------------------------------- endpoints
def validate(apikey):
    return _get(apikey, "/users/validate.json")


def _cached_get(apikey, path, params=None):
    ck = (path, str(params))
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = _get(apikey, path, params)
    _cache[ck] = (time.time(), data)
    return data


def browse(apikey, domain, feed):
    if feed not in FEEDS:
        raise NexusError(f"Unknown feed '{feed}'.")
    return _cached_get(apikey, f"/games/{_domain(domain)}/mods/{feed}.json")


def check_updates(apikey, domain, installed):
    """Ids of installed mods from this domain whose Nexus files changed after the file we
    have was uploaded (install time for older installs). One cached call to the past
    week's updated feed - never one request per mod - so older updates are not seen."""
    changed = {u["mod_id"]: u.get("latest_file_update") or 0
               for u in _cached_get(apikey, f"/games/{_domain(domain)}/mods/updated.json", {"period": "1w"})}
    out = []
    for m in installed:
        src = m.get("source") or {}
        if src.get("nexus") == domain and changed.get(src.get("mod_id"), 0) > (src.get("uploaded") or m.get("installed") or 0):
            out.append(m["id"])
    return out


def files(apikey, domain, mod_id):
    return _get(apikey, f"/games/{_domain(domain)}/mods/{int(mod_id)}/files.json").get("files", [])


def download_url(apikey, domain, mod_id, file_id, key=None, expires=None):
    params = {"key": key, "expires": expires} if key else None
    links = _get(apikey, f"/games/{_domain(domain)}/mods/{int(mod_id)}/files/{int(file_id)}/download_link.json", params)
    if not links:
        raise NexusError("Nexus returned no download link.")
    return links[0]["URI"]


def parse_nxm(url):
    """nxm://<domain>/mods/<mod>/files/<file>?key=..&expires=.. -> dict."""
    p = urllib.parse.urlsplit(url or "")
    m = re.fullmatch(r"/mods/(\d+)/files/(\d+)/?", p.path)
    if p.scheme != "nxm" or not _DOMAIN.match(p.netloc) or not m:
        raise NexusError("Not a valid nxm:// link.")
    q = urllib.parse.parse_qs(p.query)
    return {"domain": p.netloc, "mod_id": int(m[1]), "file_id": int(m[2]),
            "key": q.get("key", [None])[0], "expires": q.get("expires", [None])[0]}


def download(url, dest_dir, expected_size=None):
    """Streams an https download into dest_dir. Returns the file path."""
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise NexusError("Refused a non-https download link.")
    name = os.path.basename(urllib.parse.unquote(p.path).replace("\\", "/"))
    name = re.sub(r"[^A-Za-z0-9_.() -]", "_", name).strip(". ") or "download.zip"
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    part = path + ".part"
    try:
        with _client() as c, c.stream("GET", url, headers=HEADERS) as r:
            if r.status_code != 200:
                raise NexusError(f"Download failed (HTTP {r.status_code}).")
            with open(part, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        if expected_size is not None and os.path.getsize(part) != expected_size:
            raise NexusError("Download size does not match Nexus' file size - discarded.")
        os.replace(part, path)
    except httpx.HTTPError as e:
        raise NexusError(f"Download failed: {e}")
    finally:
        if os.path.exists(part):
            os.remove(part)
    return path
