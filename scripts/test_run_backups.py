"""Run: uv run --with pytest pytest scripts/test_run_backups.py -v

Drives the offsite half of run_backups.sh against a fake `curl`, because the
failure that matters -- Duplicati unreachable mid-job -- read as success.
"""

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("run_backups.sh")

FAKE_CURL = """#!/usr/bin/env bash
case "$*" in
  *auth/login*) echo '{"AccessToken":"t"}' ;;
  *systeminfo*) echo '{}' ;;
  *backup/1/run*) [ -n "$RUN_BODY" ] && echo "$RUN_BODY" || exit 22 ;;
  *api/v1/task/*) [ -n "$TASK_BODY" ] && echo "$TASK_BODY" || exit 7 ;;
  *) exit 7 ;;
esac
"""


def run(tmp_path, run_body, task_body):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "stacks/duplicati").mkdir(parents=True)
    (repo / "stacks/duplicati/.env").write_text("WEBSERVICE_PASSWORD=x\n")
    shutil.copy(SCRIPT, repo / "scripts/run_backups.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text(FAKE_CURL)
    (bindir / "curl").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               RUN_BODY=run_body, TASK_BODY=task_body, POLL_SECONDS="0")
    return subprocess.run(["bash", repo / "scripts/run_backups.sh", "--offsite-only"],
                          capture_output=True, text=True, env=env, timeout=60)


def test_completed_task_succeeds(tmp_path):
    r = run(tmp_path, '{"ID":5}', '{"Status":"Completed"}')
    assert r.returncode == 0, r.stderr
    assert "offsite task 5 completed" in r.stderr


def test_unreachable_duplicati_is_a_failure(tmp_path):
    # The regression: this used to log "offsite job finished (phase=idle)", exit 0.
    r = run(tmp_path, '{"ID":5}', "")
    assert r.returncode == 1, r.stderr
    assert "no readable status" in r.stderr


def test_failed_task_is_a_failure(tmp_path):
    r = run(tmp_path, '{"ID":5}', '{"Status":"Failed","ErrorMessage":"quota"}')
    assert r.returncode == 1
    assert "quota" in r.stderr


def test_completed_with_error_is_a_failure(tmp_path):
    r = run(tmp_path, '{"ID":5}', '{"Status":"Completed","ErrorMessage":"partial"}')
    assert r.returncode == 1


def test_run_request_failing_is_a_failure(tmp_path):
    r = run(tmp_path, "", '{"Status":"Completed"}')
    assert r.returncode == 1
    assert "could not start the offsite job" in r.stderr
