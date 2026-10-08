"""Modpack share codes (Epic P3 #5, D-012).

export() turns the active game's enabled Nexus mods (mod id, file id, load order)
into a small JSON pack, parks it on dpaste.org for a week and returns NX-<slug>.
fetch() reverses that. Nothing but Nexus ids, names and versions leaves the
machine - no paths, no local archives. The pack is untrusted input on the way
back in: the slug, size, and every field are checked before anyone sees it.
Load order = manifest order (manager deploys in that order; clashes are refused).
"""

import json
import re

import httpx

from core.mods.nexus import _DOMAIN

PASTE = "https://dpaste.org"
EXPIRES = 604800                    # one week - dpaste's choices: 3600, 604800, 2592000, never
MAX_BYTES = 256 * 1024
MAX_MODS = 1000
TRANSPORT = None                    # tests swap in httpx.MockTransport
_SLUG = r"[A-Za-z0-9]{2,32}"
_CODE = re.compile(rf"NX-({_SLUG})", re.I)
_PASTE_URL = re.compile(rf"{re.escape(PASTE)}/({_SLUG})/?")


class ShareError(Exception):
    pass


def _client():
    return httpx.Client(transport=TRANSPORT, timeout=20, follow_redirects=False)


def build(domain, items):
    """(pack, skipped): enabled mods with Nexus ids for `domain`, in load order.
    `skipped` counts enabled mods that cannot be shared (no Nexus source)."""
    mods, skipped = [], 0
    for m in items:
        src = m.get("source") or {}
        if not m.get("enabled"):
            continue
        if src.get("nexus") != domain:
            skipped += not src.get("nexus")
            continue
        mods.append({"mod_id": src["mod_id"], "file_id": src["file_id"], "name": m["name"],
                     "version": src.get("version"), "order": len(mods)})
    return {"v": 1, "game": domain, "mods": mods}, skipped


def export(domain, items):
    """(code, pack, skipped). Raises ShareError."""
    pack, skipped = build(domain, items)
    if not pack["mods"]:
        raise ShareError("No Nexus mods are ON for this game - only mods installed from Nexus can be shared.")
    try:
        with _client() as c:
            r = c.post(PASTE + "/api/", data={"content": json.dumps(pack), "format": "url",
                                              "expires": str(EXPIRES), "lexer": "json"})
    except httpx.HTTPError as e:
        raise ShareError(f"Could not reach dpaste.org: {e}")
    m = _PASTE_URL.fullmatch(r.text.strip()) if r.status_code == 200 else None
    if not m:
        raise ShareError(f"dpaste.org did not accept the modpack (HTTP {r.status_code}).")
    return f"NX-{m[1]}", pack, skipped


def _int(v):
    if type(v) is not int or v < 0:   # bool is an int subclass - refuse it too
        raise ShareError("This share code does not hold a valid modpack.")
    return v


def parse(data):
    """Validated copy of a pack, sorted by load order. Raises ShareError."""
    bad = ShareError("This share code does not hold a valid modpack.")
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("game"), str) \
            or not _DOMAIN.match(data["game"]) or not isinstance(data.get("mods"), list) or len(data["mods"]) > MAX_MODS:
        raise bad
    mods = []
    for m in data["mods"]:
        if not isinstance(m, dict):
            raise bad
        mods.append({"mod_id": _int(m.get("mod_id")), "file_id": _int(m.get("file_id")),
                     "name": str(m.get("name") or "")[:120],
                     "version": None if m.get("version") is None else str(m["version"])[:40],
                     "order": _int(m.get("order"))})
    return {"v": 1, "game": data["game"], "mods": sorted(mods, key=lambda m: m["order"])}


def fetch(code):
    """NX-<slug> -> validated pack. Raises ShareError."""
    m = _CODE.fullmatch(code.strip()) if isinstance(code, str) else None
    if not m:
        raise ShareError("That is not a share code (it looks like NX-AbC1).")
    try:
        with _client() as c:
            r = c.get(f"{PASTE}/{m[1]}/raw")
    except httpx.HTTPError as e:
        raise ShareError(f"Could not reach dpaste.org: {e}")
    if r.status_code == 404:
        raise ShareError("Share code not found - it may have expired (codes last 7 days).")
    if r.status_code != 200:
        raise ShareError(f"dpaste.org error {r.status_code}.")
    if len(r.content) > MAX_BYTES:
        raise ShareError("Refused: that modpack is too large.")
    try:
        return parse(json.loads(r.content))
    except ValueError:
        raise ShareError("This share code does not hold a valid modpack.")


def compare(pack, items):
    """Pack mods tagged with state: installed (same file), other_file (same mod,
    different file), or missing - against this machine's manifest."""
    have = {(m["source"]["mod_id"], m["source"]["file_id"]) for m in items
            if (m.get("source") or {}).get("nexus") == pack["game"]}
    mod_ids = {mid for mid, _ in have}
    return [{**m, "state": "installed" if (m["mod_id"], m["file_id"]) in have
             else "other_file" if m["mod_id"] in mod_ids else "missing"} for m in pack["mods"]]
