"""Mod Bisect: Automated binary search to find a broken mod (SPEC 3.4)."""

import json
from contextlib import closing
from core.telemetry import bench

def _db():
    """bench's DB (SPEC 3.1), table created on demand so a moved/new DB file just works."""
    db = bench._db()
    db.execute("""CREATE TABLE IF NOT EXISTS bisect_state (
        game_id TEXT PRIMARY KEY,
        active INTEGER,
        good_mods TEXT,
        bad_mods TEXT,
        testing_mods TEXT,
        step INTEGER
    )""")
    return db

def get_state(game_id):
    with closing(_db()) as db:
        row = db.execute("SELECT * FROM bisect_state WHERE game_id = ?", (game_id,)).fetchone()
    if not row:
        return {"game_id": game_id, "active": False}
    return {
        "game_id": row["game_id"],
        "active": bool(row["active"]),
        "good_mods": json.loads(row["good_mods"]),
        "bad_mods": json.loads(row["bad_mods"]),
        "testing_mods": json.loads(row["testing_mods"]),
        "step": row["step"]
    }

def start(game_id, suspects):
    """Starts a bisect session with the list of suspect mod_ids."""
    if len(suspects) < 2:
        return False, "Need at least 2 mods to bisect."
    
    half = len(suspects) // 2
    testing = suspects[:half]
    
    state = {
        "game_id": game_id,
        "active": 1,
        "good_mods": json.dumps([]),
        "bad_mods": json.dumps(suspects),
        "testing_mods": json.dumps(testing),
        "step": 1
    }
    
    with closing(_db()) as db, db:
        db.execute("""
            INSERT OR REPLACE INTO bisect_state 
            (game_id, active, good_mods, bad_mods, testing_mods, step)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (game_id, 1, state["good_mods"], state["bad_mods"], state["testing_mods"], 1))
        
    return True, "Bisect started."

def report(game_id, works_fine):
    """Report the result of the current testing batch."""
    state = get_state(game_id)
    if not state["active"]:
        return False, "No active bisect session."
        
    good = set(state["good_mods"])
    bad = state["bad_mods"]
    testing = set(state["testing_mods"])
    
    if works_fine:
        # The tested mods are fine. The culprit is in the OTHER half of bad.
        good.update(testing)
        bad = [m for m in bad if m not in testing]
    else:
        # The tested mods caused the crash. The culprit is IN this half; the rest are cleared.
        good.update(m for m in bad if m not in testing)
        bad = [m for m in bad if m in testing]
        
    if len(bad) <= 1:
        # Found it!
        with closing(_db()) as db, db:
            db.execute("UPDATE bisect_state SET active = 0 WHERE game_id = ?", (game_id,))
        culprit = bad[0] if bad else None
        return True, {"finished": True, "culprit": culprit}
        
    # Next step
    half = len(bad) // 2
    next_testing = bad[:half]
    
    with closing(_db()) as db, db:
        db.execute("""
            UPDATE bisect_state 
            SET good_mods = ?, bad_mods = ?, testing_mods = ?, step = step + 1
            WHERE game_id = ?
        """, (json.dumps(list(good)), json.dumps(bad), json.dumps(next_testing), game_id))
        
    return True, {"finished": False, "testing_mods": next_testing}

def abort(game_id):
    with closing(_db()) as db, db:
        db.execute("UPDATE bisect_state SET active = 0 WHERE game_id = ?", (game_id,))
    return True
