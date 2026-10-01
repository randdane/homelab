#!/usr/bin/env bash
# Weekly commit + push of the Obsidian vault to Forgejo, then a healthchecks.io
# ping. Obsidian Sync replicates continuously; this is the self-owned offsite
# copy, and it went four weeks without a commit before anyone noticed
# (2026-08-30 to 2026-09-25). The ping only fires after a successful push, so
# a missed week -- laptop off, push refused, script broken -- arrives as an
# email from healthchecks.io rather than as silence.
#
# Run by laptop-vault-commit.timer (a user unit on the laptop). Safe to run by hand.
set -euo pipefail

VAULT="$HOME/Documents/Obsidian_vault"
KEY_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/_r_/healthchecks/ping_key"

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
git push -q origin HEAD
echo "pushed; origin/main is $(git rev-parse --short origin/main)"

curl -fsS -m 10 --retry 3 -o /dev/null \
    "https://hc-ping.com/$(cat "$KEY_FILE")/vault-commit?create=1"
echo "pinged healthchecks.io"
