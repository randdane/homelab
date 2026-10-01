# /// script
# requires-python = ">=3.11"
# ///
"""Shared Compose parsing for the scripts in this directory.

Parsing is delegated to `docker compose config`, which already expands ranges,
splits protocols, normalizes long syntax, resolves anchors and `extends`, and
interpolates .env. Hand-parsing the YAML gets all of that subtly wrong.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `${VAR:?message}` makes a variable mandatory, and Compose then refuses to
# render the whole file without it. That is right for `docker compose up` and
# wrong for us: a secret that only exists on the server would make a stack
# unreadable everywhere else, and the stack would go missing from ports.md and
# from every backup-coverage check with only a one-line SKIP to show for it.
# Structure does not depend on the value, so supply one and read the file.
MISSING_VAR = re.compile(r"required variable (\w+) is missing a value")
STUB = "unset-by-scripts/compose.py"
# HOMELAB_HOST also fills a published port's host_ip, where Compose rejects
# anything but an address. 0.0.0.0 is valid there and in a URL alike.
STUB_FOR = {"HOMELAB_HOST": "0.0.0.0"}


# The repo-wide .env, shared by every stack and every script here -- see
# .env.example. Only values used by more than one consumer live in it.
SHARED_ENV = ROOT / ".env"

# KEY=value, ignoring blank lines and comments. Deliberately not a dotenv
# parser: no `export `, no quote stripping, no interpolation, no multi-line
# values. Compose's own .env handling is richer than this, and if a value
# ever needs that richness it belongs in a stack's .env where Compose reads
# it, not here.
SHARED_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def shared_env(name):
    """One repo-wide value, or None if it is unset, empty or whitespace.

    Empty is treated as unset on purpose: `.env.example` ships these blank, so
    a half-filled copy must not read as configured.

    The real environment wins, which makes `DUCKDNS_HOST=x uv run ...` work.
    **That override reaches scripts only.** Containers get these values through
    `env_file`, which Compose resolves from the file itself; the shell
    environment does not feed it. So an override here makes this script reason
    about a different host than the containers are using -- fine for a one-off
    check, wrong as a permanent systemd `Environment=`.

    Raises ValueError if the file defines `name` more than once. Compose takes
    the LAST occurrence (measured) and the obvious loop here takes the first,
    so a duplicate would hand the script one value and the container another --
    precisely the divergence a shared file exists to prevent. Neither answer is
    safe to guess at, so refuse.
    """
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return shared_env_file(name)


def shared_env_file(name):
    """The value from the FILE only, ignoring the process environment.

    Separate from shared_env() because the two answer different questions.
    "What is configured here" is the file -- it is what `env_file` gives the
    containers. "What should this script use" may be an override. Anything
    reasoning about the deployment itself wants this one; a shell override
    must not be able to hide what is actually deployed.
    """
    try:
        text = SHARED_ENV.read_text()
    except OSError:
        return None
    found = [m.group(2).strip()
             for line in text.splitlines()
             if (m := SHARED_ENV_LINE.match(line.strip())) and m.group(1) == name]
    if len(found) > 1:
        raise ValueError(
            f"{name} is defined {len(found)} times in {SHARED_ENV} -- Compose "
            f"would use the last ({found[-1]!r}) and this script the first "
            f"({found[0]!r}). Delete the duplicates.")
    return (found[0] or None) if found else None


# Every docker invocation in this repo is bounded, because an unbounded one
# does not fail -- it hangs, and a hung check is indistinguishable from a quiet
# healthy one. 30 s is generous for `docker compose config` and `docker
# inspect`, which are local operations; it is not a budget for the whole run.
# There are 42 stacks, so 42 x 30 s would exceed homelab-status.service's
# TimeoutStartSec=300 on its own -- which is why status.capture() probes the
# daemon once up front and skips the per-stack work when it is down, rather
# than relying on these timeouts to bound anything.
DOCKER_TIMEOUT = 30


def stack_config(stack_dir, declared_only=False):
    """Return one stack's fully interpolated config, or None if it will not build.

    `declared_only` ignores the stack's .env and renders the defaults written
    in compose.yaml instead. Use it for anything committed to the repo: a
    host's .env legitimately overrides ports (a laptop cannot bind 80), so a
    file generated with it in effect records that machine rather than this
    repo, and no two hosts agree on it.

    Mandatory variables that are absent here are stubbed, not fatal -- see
    MISSING_VAR. Compose reports them one at a time, hence the loop. The stub
    is deliberately not numeric: if a published port ever depends on a variable
    we invented, `int()` raises rather than writing a fictional port into a
    registry that exists to be trusted."""
    env = dict(os.environ)
    argv = ["docker", "compose"]
    if declared_only:
        argv += ["--env-file", os.devnull]
    argv += ["config", "--format", "json"]
    while True:
        try:
            result = subprocess.run(
                argv, cwd=stack_dir, capture_output=True, text=True, env=env,
                timeout=DOCKER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            # Same outcome as a non-zero exit: skip this stack, keep the sweep
            # going. One wedged stack must not take the other 41 with it.
            print(f"  SKIP {stack_dir.name}: docker compose config did not "
                  f"answer within {DOCKER_TIMEOUT}s", file=sys.stderr)
            return None
        if result.returncode == 0:
            return json.loads(result.stdout)
        missing = MISSING_VAR.search(result.stderr)
        # Only stub a name Compose says is absent, so a real .env value is
        # never shadowed -- the shell environment outranks the .env file.
        if not missing or missing.group(1) in env:
            last_line = (result.stderr.strip().splitlines()[-1:] or ["(no stderr)"])[0]
            print(f"  SKIP {stack_dir.name}: {last_line}", file=sys.stderr)
            return None
        env[missing.group(1)] = STUB_FOR.get(missing.group(1), STUB)


def stack_dirs():
    """Every stack directory, sorted by name."""
    return [p.parent for p in sorted((ROOT / "stacks").glob("*/compose.yaml"))]


BACKUP_LABEL = "homelab.backup"


def volumes_needing_backup(config):
    """Top-level named (non-external) volumes a stack declares, as their
    actual project-prefixed volume names (e.g. `lgtm_loki-data`) -- that
    prefixed name is what an external declaration elsewhere refers to.

    Volumes labelled `homelab.backup: exclude` are left out. Some volumes
    should genuinely never be archived -- caches that regenerate, media
    libraries too large for a nightly tarball. Without a way to say so, those
    stacks report a backup gap forever, and a warning that is always wrong
    stops being read. Compose normalizes both the dict and the `k=v` list
    label syntax to a dict, so only the dict form is handled here.

    Note that Compose omits the entire top-level volumes block from its
    output when no service mounts them, so a declared-but-unused volume is
    invisible here."""
    return {
        (spec or {}).get("name", key)
        for key, spec in (config.get("volumes") or {}).items()
        if not (spec or {}).get("external")
        and ((spec or {}).get("labels") or {}).get(BACKUP_LABEL) != "exclude"
    }


def registered_volumes(backup_config):
    """External volumes stacks/backup/compose.yaml declares -- the other half
    of the two-entry registration."""
    return {
        (spec or {}).get("name", key)
        for key, spec in (backup_config.get("volumes") or {}).items()
        if (spec or {}).get("external")
    }
