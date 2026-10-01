#!/usr/bin/env bash
# Monthly "the disk is still fine" report, and a failure if it is not.
#
#   ./scripts/smart-report.sh [DEVICE...]      default /dev/sda
#
# smartd already alerts on problems (see smart-alert.sh) and says nothing
# otherwise, which is correct but indistinguishable from smartd being dead --
# the exact failure this repo keeps finding. On the first server it was: installed,
# enabled, running, mailing root on a box with no MTA, silent since install.
#
# This is the heartbeat. It sends one notification a month saying the disk is
# healthy and what its numbers are, so silence for two months is a question
# rather than a comfort.
#
# Several devices are checked in one run and reported in one notification,
# so pve's HDD and NVMe arrive together. Exit status is the worst of them:
# 0 = all healthy (and reported), 1 = a disk has a problem, 2 = could not
# check one. A problem is notified here too, not only via OnFailure, because
# pve has no homelab-failure-notify unit. The unit sets SuccessExitStatus=2: "not checked" is not "passed", but
# it is not a 3am page either.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMARTCTL=/usr/sbin/smartctl

# Root under a system unit, sudo otherwise. The NOPASSWD rule for smartctl is
# the one this repo assumes; without it there is no reading SMART at all.
if [ "$(id -u)" = 0 ]; then
    SUDO=()
else
    SUDO=(sudo -n)
fi

# One device -> one summary line on stdout; status 0/1/2 as above.
check() {
    local dev="$1" out rc
    # Capture the status on the assignment itself. Inside `if ! cmd; then RC=$?`
    # the status read back is the one `!` already inverted -- always 0 -- so the
    # health-failed branch below could never be reached.
    out="$("${SUDO[@]}" "$SMARTCTL" -H -A "$dev" 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        # smartctl's exit status is a bitmask; bit 3 (8) is "health check failed"
        # and is a real finding, while bits 0-2 mean we never got an answer.
        if [ $((rc & 8)) -ne 0 ]; then
            echo "$out" >&2
            echo "$dev SMART health check FAILED"
            return 1
        fi
        echo "$out" >&2
        echo "$dev could not be read -- CHECK SKIPPED, not passed"
        return 2
    fi
    # The health line is the summary the drive itself stands behind.
    if ! grep -qi 'self-assessment test result: PASSED' <<<"$out"; then
        echo "$out" >&2
        echo "$dev SMART self-assessment did not report PASSED"
        return 1
    fi
    # Resolve first: a /dev/disk/by-id/nvme-* link doesn't start with /dev/nvme.
    if [[ "$(readlink -f "$dev")" == /dev/nvme* ]]; then
        # NVMe has no attribute table. Wear, spare and media errors are what
        # move before it dies.
        nv() { awk -F: -v want="$1" '$1 == want { gsub(/^ +| +$/, "", $2); print $2; found=1 } END { if (!found) print "?" }' <<<"$out"; }
        echo "$dev healthy. used=$(nv 'Percentage Used') spare=$(nv 'Available Spare') media_errors=$(nv 'Media and Data Integrity Errors') hours=$(nv 'Power On Hours')"
    else
        # Reallocated and pending sectors are the ones that move before a
        # drive dies; hours are context for whether the number is new.
        attr() { awk -v want="$1" '$2 == want { print $10; found=1 } END { if (!found) print "?" }' <<<"$out"; }
        echo "$dev healthy. reallocated=$(attr Reallocated_Sector_Ct) pending=$(attr Current_Pending_Sector) uncorrectable=$(attr Offline_Uncorrectable) hours=$(attr Power_On_Hours)"
    fi
}

[ "$#" -gt 0 ] || set -- /dev/sda
WORST=0
LINES=()
for dev in "$@"; do
    line="$(check "$dev")"; rc=$?
    LINES+=("$line")
    echo "$line"
    # 1 (a failing disk) outranks 2 (unchecked), which outranks 0.
    if [ "$rc" = 1 ] || { [ "$rc" = 2 ] && [ "$WORST" = 0 ]; }; then WORST=$rc; fi
done

case "$WORST" in
    0) TITLE="SMART monthly check: OK" ;;
    1) TITLE="SMART monthly check: DISK PROBLEM" ;;
    *) TITLE="SMART monthly check: incomplete" ;;
esac
MSG="$(printf '%s\n' "${LINES[@]}")"

# 240 s: notify.py retries for up to 180 s, through the nightly backup's ntfy stop.
# python3, not uv: notify.py is standard-library only and this may run as root.
timeout 240 python3 "$REPO/scripts/notify.py" --title "$TITLE" --message "$MSG" \
    || echo "notification failed" >&2
exit "$WORST"
