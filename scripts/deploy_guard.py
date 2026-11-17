"""Read-only remote preflight before the legacy deployment updater.

Stream over SSH with ``python3 -``. An active or unverifiable containment
marker blocks *all* updater side effects, not merely the service restart.
This is a point-in-time gate, not a shared lock against concurrent operators.
"""
import os
from pathlib import Path
import subprocess
import sys


HOLD_ROOT = Path("/var/lib/t2-containment")


def main(hold_root=HOLD_ROOT, updater=None):
    """Return 78 when contained/unknown; otherwise propagate updater status."""
    try:
        with os.scandir(hold_root) as entries:
            held = any(entry.name == "HOLD" for entry in entries)
    except OSError:
        print("Deployment blocked: containment state unavailable.", file=sys.stderr)
        return 78
    if held:
        print("Deployment blocked: containment HOLD present.", file=sys.stderr)
        return 78
    if updater is None:
        updater = Path.home() / ".local/bin/update-trading-system"
    try:
        return subprocess.run([str(updater)], check=False).returncode
    except OSError:
        print("Deployment blocked: updater unavailable.", file=sys.stderr)
        return 78


if __name__ == "__main__":
    sys.exit(main())
