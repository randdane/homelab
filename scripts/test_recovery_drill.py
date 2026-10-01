"""The version-count regex in recovery-drill.sh, checked against real output.

It shipped as '^ *[0-9]+ *:' and matched nothing, because duplicati-cli
indents fileset lines with a TAB. The drill then reported "no backup
versions" over a listing of seven and blamed the bucket path. The regex is
read out of the script rather than copied, so the two cannot drift.
"""
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "recovery-drill.sh"

# Captured from the 2026-08-27 run against the real bucket.
REAL_OUTPUT = (
    "  Listing remote folder ...\n"
    "  Downloading file duplicati-20260827T080207Z.dlist.zip.aes (3.61 KiB) ...\n"
    "Listing filesets:\n"
    "\t0    : 08/27/2026 08:02:07\n"
    "\t1    : 08/26/2026 20:50:01\n"
    "\t2    : 08/25/2026 08:08:12\n"
)


def drill_regex():
    line = next(l for l in SCRIPT.read_text().splitlines() if "VERSIONS=" in l)
    return re.search(r"grep -cE '([^']+)'", line).group(1)


def count(text, pattern):
    out = subprocess.run(["grep", "-cE", pattern], input=text,
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def test_counts_tab_indented_filesets():
    assert count(REAL_OUTPUT, drill_regex()) == 3


def test_empty_listing_still_counts_zero():
    assert count("  Listing remote folder ...\n", drill_regex()) == 0


# --- cleanup(), exercised for real -----------------------------------------
#
# The regex test above exists because a silently-wrong pattern made the drill
# lie. The cleanup trap had the same shape of defect and none of the coverage:
# it printed a warning about decrypted data it had failed to delete and then
# let the script exit 0, so `recovery-drill.sh && echo clean` said clean over a
# plaintext archive. A fileset regex cannot catch that; only running the thing
# can.
#
# The function is extracted from the script rather than copied, for the same
# anti-drift reason as REAL_OUTPUT above. An unremovable $WORK is produced by
# taking write permission off its PARENT -- no root, no container, and the
# same rm failure the real drill hit.
def _cleanup_source():
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("cleanup()"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def run_cleanup(tmp_path, removable, rc=0):
    """Run the real cleanup() over a scratch dir, return (exit status, dir survived)."""
    stub = tmp_path / "bin"
    stub.mkdir()
    # No docker here: the chown repair must be allowed to fail without that
    # deciding the outcome. That is the whole point of checking the directory
    # instead of the return code.
    (stub / "docker").write_text("#!/bin/sh\nexit 1\n")
    (stub / "docker").chmod(0o755)

    parent = tmp_path / "parent"
    work = parent / "work"
    work.mkdir(parents=True)
    (work / "restored.tar.gz").write_text("pretend this is decrypted backup data")
    if not removable:
        parent.chmod(0o555)
    try:
        proc = subprocess.run(
            ["bash", "-c", f'''set -euo pipefail
PATH="{stub}:$PATH"
IMAGE=stub
WORK="{work}"
{_cleanup_source()}
trap cleanup EXIT
exit {rc}'''],
            capture_output=True, text=True,
        )
        return proc.returncode, work.exists()
    finally:
        parent.chmod(0o755)


def test_cleanup_removes_and_preserves_success(tmp_path):
    assert run_cleanup(tmp_path, removable=True) == (0, False)


def test_cleanup_preserves_a_real_failure(tmp_path):
    """A drill that failed for its own reasons must not be relabelled."""
    assert run_cleanup(tmp_path, removable=True, rc=1) == (1, False)


def test_cleanup_fails_the_drill_when_plaintext_survives(tmp_path):
    """The regression: PASS on stdout, decrypted backup data still on disk."""
    status, survived = run_cleanup(tmp_path, removable=False)
    assert survived, "test setup failed to make the directory unremovable"
    assert status == 4, f"exited {status} -- a caller cannot tell plaintext remains"


def test_cleanup_failure_outranks_a_drill_failure(tmp_path):
    """Exit 4 is louder than exit 1: leftover plaintext is the worse outcome."""
    assert run_cleanup(tmp_path, removable=False, rc=1)[0] == 4


def test_drill_keep_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("DRILL_KEEP", "1")
    parent = tmp_path / "p"
    work = parent / "w"
    work.mkdir(parents=True)
    proc = subprocess.run(
        ["bash", "-c", f'''set -euo pipefail
IMAGE=stub
WORK="{work}"
{_cleanup_source()}
trap cleanup EXIT
exit 0'''],
        capture_output=True, text=True, env={"DRILL_KEEP": "1", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0
    assert work.exists(), "DRILL_KEEP must keep the directory"
    assert "DECRYPTED" in proc.stdout
