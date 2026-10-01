# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_compose.py -v"""

import subprocess
from pathlib import Path

import os

import pytest

import compose


class FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_stack_config_returns_parsed_json(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: FakeResult(0, stdout='{"name": "demo"}'),
    )
    assert compose.stack_config(Path("/nonexistent")) == {"name": "demo"}


def test_stack_config_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: FakeResult(1, stderr="boom\nsecond line"),
    )
    assert compose.stack_config(Path("/nonexistent/broken")) is None


def test_stack_config_skips_a_stack_whose_docker_hangs(monkeypatch):
    """A wedged dockerd must skip one stack, not take the sweep with it.

    Before this, the call had no timeout at all: `docker compose config`
    blocked forever, homelab-status.service hit TimeoutStartSec=300, and the
    alert said only "homelab-status.service failed".
    """
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker compose config", timeout=30)

    monkeypatch.setattr(subprocess, "run", hang)
    assert compose.stack_config(Path("/nonexistent/wedged")) is None


def test_stack_config_passes_a_timeout(monkeypatch):
    """The timeout is the point; assert it is actually handed to subprocess."""
    seen = {}

    def record(*a, **k):
        seen.update(k)
        return FakeResult(0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", record)
    compose.stack_config(Path("/nonexistent"))
    assert seen.get("timeout") == compose.DOCKER_TIMEOUT


def test_stack_config_survives_empty_stderr(monkeypatch):
    """A non-zero exit with no stderr must skip the stack, not crash the run."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: FakeResult(1, stderr="   \n  "),
    )
    assert compose.stack_config(Path("/nonexistent/quiet")) is None


def test_stack_dirs_finds_real_stacks():
    names = [d.name for d in compose.stack_dirs()]
    assert "backup" in names
    assert names == sorted(names)


def _run_needing(*required):
    """Fake `docker compose config` that demands each var in turn, like the
    real one, which reports only the first missing variable per run."""
    calls = []

    def run(*a, env=None, **k):
        calls.append(dict(env or {}))
        for name in required:
            if (env or {}).get(name) is None:
                return FakeResult(1, stderr=(
                    "error while interpolating services.x.environment.Y: "
                    f"required variable {name} is missing a value: set it"))
        return FakeResult(0, stdout='{"name": "stubbed"}')

    return run, calls


def test_stack_config_stubs_a_missing_required_variable(monkeypatch):
    """A `${VAR:?}` that only exists on the server must not hide the stack."""
    run, calls = _run_needing("AUTHENTIK_LDAP_TOKEN")
    monkeypatch.setattr(subprocess, "run", run)
    assert compose.stack_config(Path("/nonexistent")) == {"name": "stubbed"}
    assert calls[-1]["AUTHENTIK_LDAP_TOKEN"] == compose.STUB


def test_stack_config_stubs_several_one_at_a_time(monkeypatch):
    run, calls = _run_needing("ONE", "TWO", "THREE")
    monkeypatch.setattr(subprocess, "run", run)
    assert compose.stack_config(Path("/nonexistent")) == {"name": "stubbed"}
    assert len(calls) == 4


def test_stack_config_stub_is_not_numeric():
    """int() must raise rather than a fictional port reaching the registry."""
    with pytest.raises(ValueError):
        int(compose.STUB)


def test_stack_config_gives_up_rather_than_looping(monkeypatch):
    """If a variable stays 'missing' after being set, the file is broken for
    some other reason -- skip it instead of spinning forever."""
    def run(*a, env=None, **k):
        return FakeResult(1, stderr="required variable STUCK is missing a value")
    monkeypatch.setattr(subprocess, "run", run)
    assert compose.stack_config(Path("/nonexistent")) is None


def test_stack_config_does_not_shadow_a_real_env_value(monkeypatch):
    """Only names Compose reports absent get stubbed; the shell environment
    outranks .env, so stubbing a name that has a value would corrupt it."""
    monkeypatch.setenv("PG_PASS", "the-real-password")
    run, calls = _run_needing("PG_PASS")
    monkeypatch.setattr(subprocess, "run", run)
    assert compose.stack_config(Path("/nonexistent")) == {"name": "stubbed"}
    assert len(calls) == 1, "a variable that already has a value needs no retry"
    assert calls[0]["PG_PASS"] == "the-real-password"


def test_declared_only_bypasses_the_env_file(monkeypatch):
    """A committed file must not record whichever .env the generating machine
    happened to have -- a host may legitimately override a port."""
    seen = []

    def run(*a, **k):
        seen.append(a[0])
        return FakeResult(0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", run)
    compose.stack_config(Path("/nonexistent"))
    compose.stack_config(Path("/nonexistent"), declared_only=True)
    assert "--env-file" not in seen[0]
    assert seen[1][seen[1].index("--env-file") + 1] == os.devnull


# --- repo-wide .env --------------------------------------------------------
def test_shared_env_reads_a_value(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nDUCKDNS_HOST=example.duckdns.org\nOTHER=x\n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    assert compose.shared_env("DUCKDNS_HOST") == "example.duckdns.org"


def test_shared_env_treats_blank_as_unset(tmp_path, monkeypatch):
    """.env.example ships these empty, so a half-filled copy must not read as
    configured -- that is the difference between a loud failure and a monitor
    that silently checks nothing."""
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOST=\nPUBLIC_DOMAIN=   \n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    monkeypatch.delenv("PUBLIC_DOMAIN", raising=False)
    assert compose.shared_env("DUCKDNS_HOST") is None
    assert compose.shared_env("PUBLIC_DOMAIN") is None


def test_shared_env_prefers_the_real_environment(tmp_path, monkeypatch):
    """So a one-off override works and a systemd unit can set it without
    editing the file."""
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOST=from-file\n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.setenv("DUCKDNS_HOST", "from-env")
    assert compose.shared_env("DUCKDNS_HOST") == "from-env"


def test_shared_env_missing_file_is_not_an_error(tmp_path, monkeypatch):
    """A fresh clone has no .env; callers handle None, they do not crash."""
    monkeypatch.setattr(compose, "SHARED_ENV", tmp_path / "nope")
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    assert compose.shared_env("DUCKDNS_HOST") is None


def test_shared_env_ignores_a_prefix_collision(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOSTNAME=wrong\nDUCKDNS_HOST=right\n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    assert compose.shared_env("DUCKDNS_HOST") == "right"


def test_shared_env_strips_a_whitespace_only_process_value(tmp_path, monkeypatch):
    """The file path already treated blank as unset; the environment path did
    not, so an exported empty var read as configured."""
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOST=from-file\n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.setenv("DUCKDNS_HOST", "   ")
    assert compose.shared_env("DUCKDNS_HOST") == "from-file"


def test_shared_env_rejects_a_duplicate_key(tmp_path, monkeypatch):
    """Compose takes the LAST occurrence (measured), this would take the first.
    Guessing either way hands the script and the container different values."""
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOST=first\nDUCKDNS_HOST=second\n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    with pytest.raises(ValueError, match="defined 2 times"):
        compose.shared_env("DUCKDNS_HOST")


def test_shared_env_trims_surrounding_space_on_a_real_value(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DUCKDNS_HOST=  example.duckdns.org  \n")
    monkeypatch.setattr(compose, "SHARED_ENV", env)
    monkeypatch.delenv("DUCKDNS_HOST", raising=False)
    assert compose.shared_env("DUCKDNS_HOST") == "example.duckdns.org"
