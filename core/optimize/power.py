"""Windows power plan switch via powercfg (no direct registry writes).

Known gap (Phase 3): switching back always selects Balanced instead of the
plan the user had before - snapshot/revert will fix that.
"""

import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Built-in scheme GUIDs: https://learn.microsoft.com/windows-hardware/customize/power-settings/configure-power-settings
POWER_PLAN_HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_PLAN_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"


def set_power_plan(high_performance: bool):
    scheme = POWER_PLAN_HIGH_PERFORMANCE if high_performance else POWER_PLAN_BALANCED
    try:
        result = subprocess.run(["powercfg", "/s", scheme], capture_output=True, text=True,
                                timeout=5, creationflags=NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        print(f"powercfg failed: {e}")
        return False
