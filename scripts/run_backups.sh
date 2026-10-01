#!/usr/bin/env bash
# Run the nightly local archive, then push it offsite -- in that order.
#
#   ./scripts/run_backups.sh [--dry-run]
#   ./scripts/run_backups.sh --verify     # read the offsite copy back, hash it
#
# Why this exists instead of two independent schedules: the offsite job is
# only worth running *after* the local archive it reads has been written.
# Cron inside the backup container fired at 03:00 and Duplicati at 04:00,
# which relied on an hour being enough. That assumption breaks the moment
# either one catches up after a boot -- and on 2026-08-23 both ran within
# four minutes of each other for exactly that reason.
#
# It also fixes a harder failure: container cron does not catch up. The first server is
# a laptop and was powered off at 03:00 on 2026-08-22 and 2026-08-23, so two
# nightly archives were never written and nothing reported a problem. The
# systemd timer that calls this uses Persistent=true, so a missed run fires
# after the next boot instead of vanishing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0
OFFSITE_ONLY=0
VERIFY=0
case "${1:-}" in
    --dry-run)      DRY=1 ;;
    --offsite-only) OFFSITE_ONLY=1 ;;   # recover the second half without re-archiving
    # Downloads, decrypts and hashes what is actually in the bucket. Lives here
    # rather than in its own script only to reuse the readiness wait and login
    # below; it writes nothing and touches no archive.
    --verify)       VERIFY=1 ;;
    "")             ;;
    *)              echo "unknown argument: $1" >&2; exit 2 ;;
esac

DUPLICATI_ENV="$REPO/stacks/duplicati/.env"
DUPLICATI_URL="http://127.0.0.1:${DUPLICATI_PORT:-8200}"
BACKUP_CONTAINER=backup

log() { printf '%s %s\n' "$(date -Is)" "$*" >&2; }
fail() { log "FAILED: $*"; exit 1; }

# The container must be up before `docker exec` can reach it. After a boot
# this unit can start before Docker has finished starting containers, so wait
# rather than failing on a race.
wait_for_container() {
    for _ in $(seq 1 60); do
        if [ "$(docker inspect -f '{{.State.Running}}' "$BACKUP_CONTAINER" 2>/dev/null)" = "true" ]; then
            return 0
        fi
        sleep 5
    done
    return 1
}

if [ "$VERIFY" = 1 ]; then
    log "--verify: reading the offsite copy back, no archive written"
elif [ "$OFFSITE_ONLY" = 1 ]; then
    log "--offsite-only: skipping the local archive"
else
    log "waiting for container $BACKUP_CONTAINER"
    if ! wait_for_container; then
        fail "$BACKUP_CONTAINER is not running after 5 minutes"
    fi
    # The container's mount list is fixed at creation, so a volume registered
    # after it started is silently left out of every archive -- ntfy_data was,
    # on 2026-09-14. Refuse to write an archive that is known to be incomplete.
    log "checking the container mounts every registered volume"
    (cd "$REPO" && uv run scripts/check_backups.py --require-mounts) \
        || fail "backup coverage check failed -- see above"
fi

if [ "$OFFSITE_ONLY" = 1 ] || [ "$VERIFY" = 1 ]; then
    :
elif [ "$DRY" = 1 ]; then
    log "dry-run: would run 'docker exec $BACKUP_CONTAINER backup'"
else
    log "running local archive"
    docker exec "$BACKUP_CONTAINER" backup || fail "local archive failed"
    log "local archive done"
fi

# --- offsite ---------------------------------------------------------------
# Duplicati's own scheduler is disabled for this job; this is the only
# trigger. Keeping both would double-run it.
if [ ! -f "$DUPLICATI_ENV" ]; then
    fail "no $DUPLICATI_ENV -- cannot authenticate to Duplicati"
fi
PW="$(grep -E '^WEBSERVICE_PASSWORD=' "$DUPLICATI_ENV" | cut -d= -f2-)"
[ -n "$PW" ] || fail "WEBSERVICE_PASSWORD is empty in $DUPLICATI_ENV"

# Duplicati carries docker-volume-backup.stop-during-backup=true, so the
# archive step above just stopped and restarted it. Its web service needs a
# moment before it will answer; without this wait the login returns an empty
# body and the whole run fails *after* a successful archive.
log "waiting for Duplicati to accept connections"
DUPLICATI_READY=0
for _ in $(seq 1 60); do
    if curl -sf -m 5 -o /dev/null "$DUPLICATI_URL/api/v1/systeminfo" \
       || curl -s -m 5 -o /dev/null -w '%{http_code}' "$DUPLICATI_URL/" | grep -qE '^[23]'; then
        DUPLICATI_READY=1
        break
    fi
    sleep 5
done
[ "$DUPLICATI_READY" = 1 ] || fail "Duplicati did not become reachable at $DUPLICATI_URL"

LOGIN_BODY="$(curl -sf -m 20 -X POST "$DUPLICATI_URL/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"Password":sys.argv[1]}))' "$PW")" || true)"
# Parse defensively: an empty or non-JSON body means "not ready" or "wrong
# password", and a raw traceback here buries the real cause.
TOKEN="$(printf '%s' "$LOGIN_BODY" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("AccessToken", ""))
except Exception:
    print("")
' 2>/dev/null)"
[ -n "$TOKEN" ] || fail "could not authenticate to Duplicati at $DUPLICATI_URL"

if [ "$DRY" = 1 ]; then
    if [ "$VERIFY" = 1 ]; then
        log "dry-run: authenticated OK; would POST /api/v1/backup/1/verify"
    else
        log "dry-run: authenticated OK; would POST /api/v1/backup/1/run"
    fi
    log "dry-run: all prerequisites present"
    exit 0
fi

if [ "$VERIFY" = 1 ]; then
    # The only check that reads the remote bytes back. status.py asks how OLD
    # the newest archive is, which a nightly run producing corrupt output
    # passes perfectly.
    TASK="$(curl -sf -m 20 -X POST "$DUPLICATI_URL/api/v1/backup/1/verify" \
        -H "Authorization: Bearer $TOKEN" \
        | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("ID",""))
except Exception: print("")' 2>/dev/null || echo "")"
    [ -n "$TASK" ] || fail "could not start the verify task"
    log "verify task $TASK started"

    # Poll the TASK, not /progressstate. progressstate still reports
    # Phase=Verify_Running after the task has finished -- measured on the first server
    # 2026-08-27, task 4 Completed at 20:33:09 while progressstate said it was
    # still running. Polling that would hang for an hour and then fail a
    # verify that had already passed.
    for _ in $(seq 1 120); do
        BODY="$(curl -sf -m 20 "$DUPLICATI_URL/api/v1/task/$TASK" \
            -H "Authorization: Bearer $TOKEN" || true)"
        read -r STATUS ERRMSG <<<"$(printf '%s' "$BODY" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("Status", ""), (d.get("ErrorMessage") or "-"))
except Exception:
    print("", "-")
' 2>/dev/null)"
        case "$STATUS" in
            Completed)
                if [ "$ERRMSG" != "-" ]; then
                    fail "verify completed with an error: $ERRMSG"
                fi
                log "verify OK -- the offsite copy downloads, decrypts and hashes clean"
                exit 0
                ;;
            Failed|Aborted)
                fail "verify task $TASK ended $STATUS: $ERRMSG"
                ;;
        esac
        sleep 10
    done
    fail "verify task $TASK did not finish within 20 minutes"
fi

log "triggering offsite job"
TASK="$(curl -sf -m 20 -X POST "$DUPLICATI_URL/api/v1/backup/1/run" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("ID",""))
except Exception: print("")' 2>/dev/null || echo "")"
[ -n "$TASK" ] || fail "could not start the offsite job"
log "offsite task $TASK started"

# Poll the task, as --verify does. This used to poll /progressstate and treat
# an empty phase as finished -- but a failed request or unreadable body is
# also empty, so a dead Duplicati reported "offsite job finished" and exit 0.
# Only an explicit Completed counts; a run of unreadable answers is a failure.
UNREADABLE=0
for _ in $(seq 1 240); do
    BODY="$(curl -sf -m 20 "$DUPLICATI_URL/api/v1/task/$TASK" \
        -H "Authorization: Bearer $TOKEN" || true)"
    read -r STATUS ERRMSG <<<"$(printf '%s' "$BODY" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("Status", ""), (d.get("ErrorMessage") or "-"))
except Exception:
    print("", "-")
' 2>/dev/null)"
    case "$STATUS" in
        Completed)
            if [ "$ERRMSG" != "-" ]; then
                fail "offsite task $TASK completed with an error: $ERRMSG"
            fi
            log "offsite task $TASK completed"
            exit 0
            ;;
        Failed|Aborted)
            fail "offsite task $TASK ended $STATUS: $ERRMSG"
            ;;
        # An unreadable body prints " -", and read strips the leading space,
        # so STATUS arrives as "-" rather than empty.
        ""|-)
            UNREADABLE=$((UNREADABLE + 1))
            # 8 x 15 s: long enough to ride out a restart, short enough that
            # a dead Duplicati is not mistaken for a slow upload for an hour.
            if [ "$UNREADABLE" -ge 8 ]; then
                fail "offsite task $TASK: no readable status for 2 minutes"
            fi
            ;;
        *)
            UNREADABLE=0
            ;;
    esac
    sleep "${POLL_SECONDS:-15}"
done
fail "offsite task $TASK did not finish within an hour"
