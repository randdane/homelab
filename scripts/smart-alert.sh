#!/usr/bin/env bash
# smartd's '-M exec' target: turn a SMART warning into a phone notification.
#
# smartd runs this as root when a disk problem is detected, with the report on
# stdin and details in SMARTD_* environment variables. It is not run on a
# schedule and not run when nothing is wrong -- smartd's default directives
# (-a: -H -f -t -l error -l selftest, plus -C 197 -U 198) all fire on problems
# only, and '-M once' means one message per problem type, not one per check.
#
# Two rules from smartd.conf(5) shape this file:
#
#   "smartd will block until the executable PATH returns, so if your
#    executable hangs, then smartd will also hang."
#
#   "The executable is not expected to write to STDOUT or STDERR. If it does,
#    then this is interpreted as indicating a problem."
#
# So: everything is bounded by `timeout`, all output goes to the journal via
# logger, and the exit status is always 0. A broken notifier must not take the
# disk monitor down with it.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# stdin is the full report; SMARTD_MESSAGE is the one-line summary.
BODY="$(timeout 5 cat || true)"
DEVICE="${SMARTD_DEVICESTRING:-${SMARTD_DEVICE:-unknown device}}"
FAILTYPE="${SMARTD_FAILTYPE:-SMART problem}"
SUMMARY="${SMARTD_MESSAGE:-$BODY}"

{
    # 240 s: notify.py retries for up to 180 s. Still bounded, which smartd
    # needs -- it blocks until this script returns.
    # python3, not uv: notify.py imports only the standard library, and uv is
    # in a user's ~/.local/bin which is not on root's PATH under smartd.
    timeout 240 python3 "$REPO/scripts/notify.py" \
        --title "SMART: $FAILTYPE on $DEVICE" \
        --message "$SUMMARY"
    echo "notify.py exited $?"
} 2>&1 | logger -t homelab-smart-alert

exit 0
