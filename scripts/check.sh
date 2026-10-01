#!/usr/bin/env bash
# Everything that can be checked without a Docker daemon running the stacks.
#
#   ./scripts/check.sh
#
# Not -e: every check must run even after one fails. A run that stops at the
# first problem tells you about one problem, and you fix it and run again.
set -uo pipefail

cd "$(dirname "$0")/.."

declare -a FAILED=()

step() {
    local name="$1"; shift
    printf '\n== %s\n' "$name"
    if "$@"; then
        printf '   ok\n'
    else
        printf '   FAILED\n'
        FAILED+=("$name")
    fi
}

# Every stack renders. This is the one that matters: a compose file that no
# longer interpolates goes missing from ports.md and from the backup coverage
# check, and both then report success over a stack they cannot see.
compose_renders() {
    # Via scripts/compose.py, not `docker compose config` directly: mandatory
    # vars that live only on the server are stubbed there on purpose, and this
    # check exists to enforce what the scripts need, not to demand every
    # secret be present on the machine running it.
    uv run python -c '
import sys
sys.path.insert(0, "scripts")
from compose import stack_config, stack_dirs
bad = [d.name for d in stack_dirs() if stack_config(d) is None]
print("   " + ", ".join(bad) if bad else "", end="")
sys.exit(1 if bad else 0)
'
}

# docs/ports.md is generated, so a stale copy is a lie that survives review.
# It has already shipped wrong once.
ports_current() {
    uv run scripts/ports.py >/dev/null 2>&1 || return 1
    git diff --quiet -- docs/ports.md && return 0
    printf '   docs/ports.md is stale -- commit the regenerated file:\n'
    git --no-pager diff --stat -- docs/ports.md | sed 's/^/     /'
    return 1
}

tests() {
    uv run --with pytest pytest scripts/ -q 2>&1 | tail -20
    return "${PIPESTATUS[0]}"
}

step "compose renders"     compose_renders
step "docs/ports.md fresh" ports_current
# Registration only -- check_backups.py skips its container-mount half when
# there is no docker here, so this is safe off the backup host.
step "backups registered"  uv run scripts/check_backups.py

step "python tests"        tests

# check_derp.py carries its own assertions rather than a test_*.py, so pytest
# never reaches them. Without this line the drift and missing-config checks --
# the ones standing between a wrong server_url and every node re-registering
# against a URL that does not work -- were unverified by every run of this
# script.
step "check_derp self-test" uv run scripts/check_derp.py --self-test

# Same reason, same gap: check_updates.py carries its assertions inline too, so
# nothing ran them. They cover the one stack that is built rather than pulled,
# where a wrong answer means the only internet-facing service goes unwatched.
step "check_updates self-test" uv run scripts/check_updates.py --self-test

# The word list CI scans for is a copy of .site-words, held in two secret
# stores that cannot be read back. Only the machine that owns the list can
# tell when the copies are behind, so everywhere else this says so and moves
# on rather than passing in silence.
if [ -s .site-words ] && command -v gh >/dev/null; then
    step "CI secrets current" ./scripts/ci-secrets.sh
else
    printf '\n== CI secrets current\n   skipped: no .site-words or no gh here\n'
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "all checks passed"
    exit 0
fi
printf 'FAILED: %s\n' "${FAILED[*]}"
exit 1
