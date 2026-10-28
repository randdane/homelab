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


def run(tmp_path, run_body, task_body, jobs="DUPLICATI_JOBS=1\n", docker=None):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "stacks/duplicati").mkdir(parents=True)
    (repo / "stacks/duplicati/.env").write_text("WEBSERVICE_PASSWORD=x\n" + jobs)
    shutil.copy(SCRIPT, repo / "scripts/run_backups.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text(FAKE_CURL)
    (bindir / "curl").chmod(0o755)
    if docker is not None:
        (bindir / "docker").write_text(docker)
        (bindir / "docker").chmod(0o755)
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


def test_missing_jobs_refuses_to_run(tmp_path):
    # No silent default: job IDs change when a job is recreated, and a stale
    # one is exactly what this would fall back to.
    r = run(tmp_path, '{"ID":5}', '{"Status":"Completed"}', jobs="")
    assert r.returncode == 1
    assert "DUPLICATI_JOBS is empty" in r.stderr
    assert "triggering" not in r.stderr


# Records each call, and answers `inspect` with $DUPLICATI_RUNNING.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  inspect) echo "$DUPLICATI_RUNNING" ;;
esac
"""


def run_with_docker(tmp_path, running, task_body='{"Status":"Completed"}'):
    log = tmp_path / "docker.log"
    os.environ["DOCKER_LOG"], os.environ["DUPLICATI_RUNNING"] = str(log), running
    try:
        r = run(tmp_path, '{"ID":5}', task_body, docker=FAKE_DOCKER)
    finally:
        del os.environ["DOCKER_LOG"], os.environ["DUPLICATI_RUNNING"]
    return r, log.read_text().splitlines()


def test_stopped_duplicati_is_started_then_stopped(tmp_path):
    r, calls = run_with_docker(tmp_path, "false")
    assert r.returncode == 0, r.stderr
    assert calls[1:] == ["start duplicati", "stop duplicati"]


def test_stopped_duplicati_is_stopped_even_when_the_job_fails(tmp_path):
    r, calls = run_with_docker(tmp_path, "false", '{"Status":"Failed"}')
    assert r.returncode == 1
    assert calls[-1] == "stop duplicati"


def test_running_duplicati_is_left_running(tmp_path):
    """Someone is in its web UI; Sablier stops it when that session ends."""
    r, calls = run_with_docker(tmp_path, "true")
    assert r.returncode == 0, r.stderr
    assert not any(c.startswith(("start", "stop")) for c in calls)
