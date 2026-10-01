#!/usr/bin/env bash
# Install the homelab-* timers on the server.
#
#   sudo ./scripts/install-systemd.sh --system
#   add --no-enable to write the units without starting any timer
#
# Server only. The laptop mode (user units) was retired 2026-09-25: every
# check it ran duplicated the server's, it could not alert (no ntfy
# credentials there), and its status capture recorded stacks that are down by
# design. --system is still required, so a habitual run without sudo fails
# loudly instead of installing a second copy.
#
# The units are templates: WorkingDirectory= expands specifiers (%h) but NOT
# environment variables, so this substitutes the resolved paths.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEM=0
ENABLE=1
for arg in "$@"; do
    case "$arg" in
        --system) SYSTEM=1 ;;
        # Write the units but start nothing: a host being built alongside the
        # live one must not archive, push offsite or alert until cutover.
        --no-enable) ENABLE=0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done
if [ "$SYSTEM" = 0 ]; then
    echo "The laptop mode is retired: server checks run on the server only." >&2
    echo "On the server: sudo ./scripts/install-systemd.sh --system" >&2
    exit 1
fi

# Whose environment the unit should use. Under sudo, SUDO_USER is the human.
OWNER="${SUDO_USER:-$USER}"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"

# uv is resolved to an absolute path now rather than trusted to PATH later.
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$OWNER_HOME/.local/bin/uv" ]; then
    UV="$OWNER_HOME/.local/bin/uv"
fi
if [ -z "$UV" ]; then
    echo "uv not found. Install it first: https://docs.astral.sh/uv/" >&2
    exit 1
fi

UNIT_PATH="$OWNER_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

DEST=/etc/systemd/system
SYSTEMCTL=(systemctl)
USERLINES="User=$OWNER"$'\n'"Group=$(id -gn "$OWNER")"
# The always-on host: a production stack that is down is worth waking up
# for, so alerting stays at its default (on).
ALERTLINE="# HOMELAB_ALERT unset: status.py alerts on the default."
UPDATESEXITLINE="# Cup is local here: exit 2 (unreachable) is a failure."

# Leftover user units from the retired laptop mode would run beside these: two
# status captures an hour writing one state file, one of them with
# HOMELAB_ALERT=0. The first server carried both for three days in 2026-08.
OTHER_DEST="$OWNER_HOME/.config/systemd/user"
OTHER_FIX="systemctl --user disable --now homelab-{status,derp-check,monitor-check,backup,verify,smart,updates,vhost-check,digest}.timer
  rm -f $OTHER_DEST/homelab-*"
if compgen -G "$OTHER_DEST/homelab-*" > /dev/null; then
    echo "REFUSING -- old user units are installed in $OTHER_DEST:" >&2
    compgen -G "$OTHER_DEST/homelab-*" | sed 's/^/    /' >&2
    echo >&2
    echo "  Installing over it would leave both running. Remove those first:" >&2
    echo "  $OTHER_FIX" >&2
    exit 1
fi

mkdir -p "$DEST"

render() {  # $1 = template, $2 = destination
    # USERLINES is multi-line, so it is substituted with awk rather than sed.
    sed -e "s|@REPO@|$REPO|g" \
        -e "s|@UV@|$UV|g" \
        -e "s|@PATH@|$UNIT_PATH|g" \
        -e "s|@HOME@|$OWNER_HOME|g" \
        -e "s|@ALERTLINE@|$ALERTLINE|g" \
        -e "s|@UPDATESEXITLINE@|$UPDATESEXITLINE|g" "$1" \
    | awk -v repl="$USERLINES" '{ if ($0 == "@USERLINES@") print repl; else print }' \
    > "$2"
}

# homelab-failure-notify@.service is an instance template and is never
# enabled: systemd starts it on demand, once per failing unit, from the
# OnFailure= lines in the services above.
UNITS=(homelab-status.service homelab-status.timer
       homelab-derp-check.service homelab-derp-check.timer
       homelab-monitor-check.service homelab-monitor-check.timer
       homelab-updates.service homelab-updates.timer
       homelab-vhost-check.service homelab-vhost-check.timer)
# The digest is the always-on host's alone. A laptop digest would report on
# stacks that are down by design, and could satisfy the phone's check on a
# morning the first server sent nothing.
# A VM's disk is virtio and has no SMART data, so the heartbeat there would
# report on nothing. On pve the physical disks are covered by pve-smart.timer,
# installed by hand (docs/smart.md).
SMART=1
if systemd-detect-virt -q --vm; then
    SMART=0
    echo "  VM detected: skipping homelab-smart (SMART lives on the hypervisor)"
else
    UNITS+=(homelab-smart.service homelab-smart.timer)
fi
# Backup and verify drive the backup and duplicati containers, which run
# only on the server. On the laptop the backup never once succeeded
# (2026-08-27 to 2026-09-25): nothing to archive, so it failed daily.
UNITS+=(homelab-backup.service homelab-backup.timer
        homelab-verify.service homelab-verify.timer)
# The alert path, started by the OnFailure= lines in the services above.
UNITS+=(homelab-failure-notify@.service)
UNITS+=(homelab-digest.service homelab-digest.timer)
# The always-on host's alone, for the same reason as the digest but
# inverted: this one DEPLOYS. On the laptop the production stacks are
# down by design, so a boot-time reconcile there would start all of them.
UNITS+=(homelab-reconcile.service)
# Root-only: keeps every remote_ip-gated vhost reachable from the tailnet
# ($SITE_DIR/docs/devices.md). A hand-install until 2026-09-25, forgotten twice.
UNITS+=(homelab-tailnet-source.service homelab-tailnet-source-check.service
        homelab-tailnet-source-check.timer)
install -m 755 "$REPO/scripts/tailnet-source-rule.sh" /usr/local/sbin/tailnet-source-rule.sh
echo "  wrote /usr/local/sbin/tailnet-source-rule.sh"
for unit in "${UNITS[@]}"; do
    src="$REPO/scripts/systemd/$unit.in"
    dst="$DEST/$unit"
    # An earlier install symlinked the unit straight into place. Replace it,
    # or the render below would write through the link into the repo.
    if [ -L "$dst" ]; then
        echo "  replacing old symlink $dst"
        rm "$dst"
    fi
    render "$src" "$dst"
    echo "  wrote $dst"
done

"${SYSTEMCTL[@]}" daemon-reload
if [ "$ENABLE" = 0 ]; then
    echo "--no-enable: units written, no timer enabled. At cutover, rerun without it."
    exit 0
fi
"${SYSTEMCTL[@]}" enable --now homelab-status.timer
"${SYSTEMCTL[@]}" enable --now homelab-derp-check.timer
"${SYSTEMCTL[@]}" enable --now homelab-monitor-check.timer
if [ "$SMART" = 1 ]; then
    "${SYSTEMCTL[@]}" enable --now homelab-smart.timer
fi
"${SYSTEMCTL[@]}" enable --now homelab-updates.timer
"${SYSTEMCTL[@]}" enable --now homelab-vhost-check.timer
"${SYSTEMCTL[@]}" enable --now homelab-backup.timer
"${SYSTEMCTL[@]}" enable --now homelab-verify.timer
"${SYSTEMCTL[@]}" enable --now homelab-digest.timer
# `enable`, NOT `enable --now`. This unit reconciles running stacks to
# their compose files, so starting it here would recreate anything the
# checkout has drifted from mid-install -- a deploy nobody asked for.
# It is a boot-time repair; let it run at the next boot.
"${SYSTEMCTL[@]}" enable homelab-reconcile.service

"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" enable --now homelab-tailnet-source.service
"${SYSTEMCTL[@]}" enable --now homelab-tailnet-source-check.timer

echo
"${SYSTEMCTL[@]}" list-timers homelab-status.timer homelab-derp-check.timer \
    homelab-monitor-check.timer homelab-backup.timer homelab-verify.timer homelab-smart.timer \
    homelab-updates.timer homelab-vhost-check.timer homelab-digest.timer \
    homelab-tailnet-source-check.timer \
    --no-pager || true
