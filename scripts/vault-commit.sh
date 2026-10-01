#!/usr/bin/env bash
# Weekly commit + push of the Obsidian vault to Forgejo, then a healthchecks.io
# ping. Obsidian Sync replicates continuously; this is the self-owned offsite
# copy, and it went four weeks without a commit before anyone noticed
# (2026-08-30 to 2026-09-25). The success ping only fires after a push, so a
# week where the script never ran at all -- laptop off -- still arrives as an
# email from healthchecks.io rather than as silence.
#
# A run that fails says so at once, with a /fail ping, instead of waiting for
# the check's period to lapse. And the push is retried: Persistent= fires the
# timer in the same second the laptop resumes, before the network is back, and
# on 2026-09-27 that single attempt failed and left the commit stranded for a
# week.
#
# Run by laptop-vault-commit.timer (a user unit on the laptop). Safe to run by hand.
set -euo pipefail

VAULT="${VAULT:-$HOME/Documents/Obsidian_vault}"
KEY_FILE="${KEY_FILE:-${XDG_DATA_HOME:-$HOME/.local/share}/_r_/healthchecks/ping_key}"
PUSH_ATTEMPTS="${PUSH_ATTEMPTS:-10}"
PUSH_WAIT="${PUSH_WAIT:-60}"

ping_check() {
    curl -fsS -m 10 --retry 3 -o /dev/null \
        "https://hc-ping.com/$(cat "$KEY_FILE")/vault-commit$1"
}

report_failure() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        echo "vault commit failed with exit $status" >&2
        # With no network this ping fails too; the missed success ping is
        # then what raises the alarm, later.
        if ping_check /fail; then
            echo "told healthchecks.io it failed"
        else
            echo "could not reach healthchecks.io either" >&2
        fi
    fi
    exit "$status"
}
trap report_failure EXIT

cd "$VAULT"
git add -A
if git diff --cached --quiet; then
    echo "nothing to commit"
else
    git commit -q -m "vault: weekly commit $(date +%F)"
    echo "committed $(git log -1 --format=%h) ($(git show --stat --format= HEAD | tail -1))"
fi

# Push even when nothing was committed this week, so an earlier failed push
# is retried rather than stranded.
try=1
until git push -q origin HEAD; do
    if [ "$try" -ge "$PUSH_ATTEMPTS" ]; then
        echo "push failed $try times, giving up until the next run" >&2
        exit 1
    fi
    echo "push failed (attempt $try of $PUSH_ATTEMPTS), retrying in ${PUSH_WAIT}s"
    try=$((try + 1))
    sleep "$PUSH_WAIT"
done
echo "pushed; origin/main is $(git rev-parse --short origin/main)"

ping_check '?create=1'
echo "pinged healthchecks.io"
