"""Autopilot (Phase 0 scope): High CPU priority once per play session.

Priority is session-only by nature - Windows drops it when the process exits.
v1's threshold-triggered config writes (fake "UI Stutter Fix", FG "VRAM
Guard") were removed; the real rules engine with snapshot/revert is Phase 3.
"""

from core.optimize.cpu import boost_client

_state = {"priority_armed": True}


def reset():
    """Re-arm (called when the selected game changes)."""
    _state["priority_armed"] = True


def check_and_apply(telemetry, spec):
    """Returns a human-readable message if an action was taken, else None."""
    if not telemetry.get("game_running"):
        _state["priority_armed"] = True
        return None
    if _state["priority_armed"]:
        _state["priority_armed"] = False
        return "Autopilot: " + boost_client(spec)[1]
    return None
