#!/usr/bin/env bash
# Re-converge every RUNNING stack to its compose file, once, at boot.
#
# WHY. On 2026-09-23, the first cold boot after the NIC swap, dockerd logged
#
#     error locating sandbox id 91d2ec4d...: sandbox not found
#     error locating sandbox id e5e1c799...: sandbox not found
#
# and restored exactly two containers -- crowdsec and authentik-ldap -- with
# only ONE of the two networks their compose file declares. Both had lost
# their project `default` and kept `edge`.
#
# crowdsec then crash-looped 58 times: its acquisition config names
# `socket-proxy:2375`, the socket proxy lives on `crowdsec_default` alone, and
# with no shared network the name resolved nowhere. authentik-ldap survived
# only by luck -- what it needs happens to also sit on `edge` -- so it ran for
# half an hour in a state its compose file forbids, and nothing noticed.
#
# That is the failure this exists for: not a container that is down, but a
# container that is UP and wired wrong. Every health check it has was green.
#
# WHY `up -d` IS THE WHOLE FIX. Measured 2026-09-23 on a throwaway stack:
# disconnect a network from a live container, run a plain `docker compose
# up -d`, and Compose sees the divergence itself --
#
#     Container nettest-a Recreate / Recreated / Started
#     (network restored)
#
# -- while a second run on the converged stack prints only `Running` and
# changes nothing. So there is no diff to compute and no repair to write:
# Compose already knows what the file says and what the daemon has. This
# script is a loop, not an algorithm. `--force-recreate` is deliberately NOT
# used; it would recreate all 14 stacks on every boot for nothing.
#
# WHY ONLY RUNNING STACKS. The repo holds 42 stacks and this host runs 14.
# The other 28 are down on purpose. `docker compose ls` reports running
# projects only, which is exactly the right scope and costs nothing to get --
# iterating over stacks/*/ instead would DEPLOY 28 stacks at every boot.
#
# Not a timer. The defect is created by dockerd restoring containers, so boot
# is the only moment it appears. A periodic run would recreate containers in
# response to ordinary mid-day repo edits, which is a deploy, not a repair.
#
# Independent steps, so no `-e`: one wedged stack must not stop the other 13
# from being repaired. Every exit code is recorded and the summary is the
# exit status.
set -uo pipefail

DRY=()
DRYLABEL=""
case "${1-}" in
    --dry-run) DRY=(--dry-run); DRYLABEL=" (dry run)" ;;
    "") ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# Polling, because systemd cannot order against work a service does after it
# reports ready. `After=docker.service` is satisfied as soon as the socket is
# up, while the daemon goes on restoring containers for seconds afterwards.
#
# Waiting for the daemon merely to ANSWER is not enough, and that is the
# subtle one. A stack dockerd has not restored yet is absent from
# `docker compose ls`, so it would be skipped silently and the boot would
# report success having reconciled 14 of 15 -- a check that quietly covers
# less than it claims, which is the failure this whole area keeps producing.
#
# Measured at the 2026-09-23 09:57 boot: restore ran 09:57:29.34 to
# 09:57:32.04 and the listing was taken at 09:57:35.93. It saw all 15, with
# 3.9 s to spare. That margin is luck, not design.
#
# So wait for the count to STOP CHANGING rather than for the daemon to reply.
# ponytail: two equal readings, because dockerd exposes no "restore complete"
# signal to wait on properly. If one ever appears, wait on that instead.
SETTLE_READINGS=2
SETTLE_GAP=5

running_count() { docker compose ls --format json 2>/dev/null | grep -o '"Name":' | wc -l; }

stable=0
last=-1
for _ in $(seq 40); do
    if ! docker compose ls --format json >/dev/null 2>&1; then
        sleep "$SETTLE_GAP"
        continue
    fi
    now="$(running_count)"
    if [ "$now" = "$last" ] && [ "$now" != 0 ]; then
        stable=$((stable + 1))
        if [ "$stable" -ge "$((SETTLE_READINGS - 1))" ]; then
            break
        fi
    else
        stable=0
    fi
    last="$now"
    sleep "$SETTLE_GAP"
done
if [ "$stable" = 0 ]; then
    echo "FAILED: the set of running stacks never settled -- last count $last" >&2
    exit 1
fi
echo "running stack count settled at $last"

# ConfigFiles is comma-separated when a project was started with several -f
# flags. Every stack here uses one, and the FIRST is the one whose directory
# Compose treats as the project directory -- which is what we cd to, so the
# stack's own .env is read exactly as it was at deploy time.
mapfile -t configs < <(
    docker compose ls --format json \
    | python3 -c 'import json,sys
for p in json.load(sys.stdin):
    print(p["ConfigFiles"].split(",")[0])'
)

if [ "${#configs[@]}" = 0 ]; then
    echo "FAILED: docker answered but reported no running stacks" >&2
    exit 1
fi

echo "reconciling ${#configs[@]} running stack(s)$DRYLABEL"

failed=()
changed=()
for config in "${configs[@]}"; do
    dir="$(dirname "$config")"
    name="$(basename "$dir")"
    # Compose writes the interesting verbs (Recreate/Recreated/Started) to
    # stderr, so both streams are captured or the repair would be invisible
    # in the journal.
    out="$(cd "$dir" && docker compose up -d "${DRY[@]}" 2>&1)"
    rc=$?
    printf '%s\n' "$out" | sed "s/^/  $name: /"
    if [ "$rc" != 0 ]; then
        failed+=("$name")
    elif printf '%s' "$out" | grep -qE 'Recreated|Started|Created'; then
        # A converged stack prints only `Running`. Anything else means this
        # boot restored something that did not match its file -- the signal
        # worth carrying to the journal summary.
        changed+=("$name")
    fi
done

echo
if [ "${#changed[@]}" != 0 ]; then
    echo "REPAIRED${DRYLABEL}: ${changed[*]}"
else
    echo "all ${#configs[@]} stack(s) already matched their compose file"
fi

if [ "${#failed[@]}" != 0 ]; then
    echo "FAILED: ${failed[*]}" >&2
    exit 1
fi
