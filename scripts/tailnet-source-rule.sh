#!/usr/bin/env bash
# Keep tailnet-range source addresses off Docker's LOCAL-source masquerade.
#
# Driven by homelab-tailnet-source.service and its -check timer.
#
# WHAT THIS ACTUALLY DOES, measured 2026-09-09 by removing the rule and
# probing from both sides:
#
#   source            without this rule        with it
#   ----------------  ----------------------  ---------------
#   a laptop, over the tunnel            preserved     preserved
#   the host's own tailnet address      10.201.7.1    preserved
#
# So the rule it defeats is Docker's
#
#     -A POSTROUTING -o br-<edge> -m addrtype --src-type LOCAL -j MASQUERADE
#
# which fires only when the source is one of the host's OWN addresses. A
# remote client is never LOCAL and is not affected by it. `homelab` probing its
# own tailnet address is, which is why the exemption exists.
#
# It also has to sit above Tailscale's
#
#     -A ts-postrouting -m mark --mark 0x40000/0xff0000 -j MASQUERADE
#
# and on this host that is the ordering that actually bites. VM 101 is both
# the subnet router and the host serving the vhosts, so a tailnet client's
# packet to the LAN address carries the subnet-route mark: it is DNATed to the
# container and then masqueraded by ts-postrouting to the bridge gateway,
# before the exemption below is ever reached.
#
# The file used to say that ordering did not matter, on the grounds that the
# exemption had been measured working while sitting below the jump. That
# measurement was of the LOCAL-source case only, which ts-postrouting does not
# touch. Measured 2026-09-22 on VM 101: with the exemption at position 2,
# below the jump, it matched ZERO packets and every remote_ip-gated vhost
# returned status=0 to a tailnet client while logging remote_ip 10.201.7.1.
# Moving it to position 1 restored all four immediately. That also explains
# the 2026-09-09 outage this file recorded as unexplained.
#
# Assert against BOTH, because only checking the LOCAL masquerade is what let
# a fully broken path report ok for days.
set -euo pipefail

RULE=(-s 100.64.0.0/10 -d 10.201.0.0/16 -j ACCEPT)

OURS='-s 100\.64\.0\.0/10 -d 10\.201\.0\.0/16 -j ACCEPT$'
LOCAL_MASQ='--src-type LOCAL -j MASQUERADE$'
TS_JUMP='^-A POSTROUTING -j ts-postrouting$'

# Whichever of the two appears first in the chain is the one that runs first.
ordered_ok() {
    iptables -t nat -S POSTROUTING \
        | grep -E -- "$OURS|$LOCAL_MASQ|$TS_JUMP" \
        | head -1 | grep -qE -- "$OURS"
}

docker_rules_present() {
    iptables -t nat -S POSTROUTING | grep -qE -- "$LOCAL_MASQ"
}

# Only waited for while tailscaled runs: with it stopped, the jump never comes.
ts_jump_pending() {
    systemctl is-active -q tailscaled.service \
        && ! iptables -t nat -S POSTROUTING | grep -qE -- "$TS_JUMP"
}

POLL=${TAILNET_SOURCE_POLL:-2}

# ponytail: polling, because systemd cannot order against work a service does
# after it reports ready. Docker and Tailscale both install their rules
# asynchronously, and asserting before they exist passes trivially and proves
# nothing. That is not hypothetical: on every boot until 2026-10-02 this ran
# about 2 s before tailscaled reached Running, found no jump, reported ok, and
# Tailscale then put its jump at position 1 above us. The gated vhosts stayed
# unreachable from the tailnet until the 15-minute check timer fired.
for _ in $(seq 30); do
    docker_rules_present && ! ts_jump_pending && break
    sleep "$POLL"
done

if ! docker_rules_present; then
    # Not a failure: with no masquerade rule there is nothing to sit above.
    # Insert anyway so the exemption is in place when Docker does start.
    iptables -t nat -D POSTROUTING "${RULE[@]}" 2>/dev/null || true
    iptables -t nat -I POSTROUTING 1 "${RULE[@]}"
    echo "ok: exemption inserted; no LOCAL-source masquerade present yet"
    exit 0
fi

# Retry rather than insert once: Docker may add rules above ours after we
# run, and re-asserting is cheaper than ordering against it.
for _ in $(seq 10); do
    iptables -t nat -D POSTROUTING "${RULE[@]}" 2>/dev/null || true
    iptables -t nat -I POSTROUTING 1 "${RULE[@]}"
    if ordered_ok; then
        echo "ok: exemption precedes the LOCAL-source masquerade and ts-postrouting"
        exit 0
    fi
    sleep "$POLL"
done

echo "FAILED: exemption sits below Docker's LOCAL-source masquerade or below" \
     "the jump to ts-postrouting, and will not be reached -- traffic will" \
     "arrive at the container as the bridge gateway, and every remote_ip-gated" \
     "vhost will be unreachable from the tailnet" >&2
exit 1
