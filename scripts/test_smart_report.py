# /// script
# requires-python = ">=3.11"
# ///
"""smart-report.sh must not exit 0 when its notification never left.

Healthy disks plus a broken ntfy used to exit 0, so the monthly heartbeat
vanished and the unit looked fine (Vikunja "smart-report.sh: return a failed
notification").
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("smart-report.sh")

HEALTHY = "SMART overall-health self-assessment test result: PASSED\\n"


def run(tmp_path, smart_rc, notify_rc):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "smart-report.sh")
    (repo / "scripts" / "notify.py").write_text(f"import sys; sys.exit({notify_rc})\n")
    smartctl = tmp_path / "smartctl"
    smartctl.write_text(f"#!/bin/sh\nprintf '{HEALTHY}'\nexit {smart_rc}\n")
    smartctl.chmod(0o755)
    return subprocess.run(
        ["bash", str(repo / "scripts" / "smart-report.sh"), "/dev/sdz"],
        env={"PATH": "/usr/bin:/bin", "SMARTCTL": str(smartctl), "SMARTCTL_NO_SUDO": "1"},
        capture_output=True, text=True,
    ).returncode


@pytest.mark.parametrize(
    "smart_rc, notify_rc, want",
    [
        (0, 0, 0),   # healthy, reported
        (0, 1, 3),   # healthy, but nobody heard: must fail the unit
        (8, 1, 1),   # failing disk outranks the failed send
        (2, 0, 2),   # unchecked, reported: SuccessExitStatus=2 keeps it quiet
        (2, 1, 3),   # unchecked and unreported: fail
    ],
)
def test_exit_status(tmp_path, smart_rc, notify_rc, want):
    assert run(tmp_path, smart_rc, notify_rc) == want
