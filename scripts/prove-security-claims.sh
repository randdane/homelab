#!/usr/bin/env bash
# Prove every row of docs/security-claims.md against a live VM, from another
# host. One check per claim ID; the IDs are the contract between that file and
# this one, so a new row there needs a check here.
#
#   TARGET=user@host ./scripts/prove-security-claims.sh                # read-only
#   TARGET=user@host INVENTORY=inv.yml LIMIT=name \
#       ./scripts/prove-security-claims.sh --intrusive                 # + injects faults
#   ... --intrusive --reboot                                           # + reboots, reruns
#
# Read-only changes no config or state on the target; it does add log lines
# (sshd, sudo, and unattended-upgrade's own log from its dry run).
# --intrusive writes bad config onto the target to prove the playbook refuses
# it, runs the playbook, and starts a container: throwaway VMs only. Every
# injected fault is removed by the same check that placed it, and again on exit.
#
# SSH_OPTS adds ssh options (key, known_hosts). The login user needs
# passwordless sudo on the target.
#
# No `set -e`: every claim must be attempted, so each result is recorded and
# the exit status is the summary.
set -uo pipefail

TARGET=${TARGET:?set TARGET=user@host}
SSH_OPTS=${SSH_OPTS:-}
HOST=${TARGET#*@}
REPO=$(cd "$(dirname "$0")/.." && pwd)

intrusive=0
reboot=0
for arg in "$@"; do
  case $arg in
    --intrusive) intrusive=1 ;;
    --reboot) reboot=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
if [ "$reboot" = 1 ] && [ "$intrusive" = 0 ]; then
  echo "--reboot needs --intrusive" >&2
  exit 2
fi
if [ "$intrusive" = 1 ]; then
  INVENTORY=${INVENTORY:?--intrusive needs INVENTORY=path}
  LIMIT=${LIMIT:?--intrusive needs LIMIT=inventory hostname}
fi

pass=0
fail=0
failed_ids=()

# shellcheck disable=SC2086  # SSH_OPTS is a list of options, split on purpose
on_target() { ssh $SSH_OPTS -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" "$@"; }
# shellcheck disable=SC2086
ssh_as() { ssh $SSH_OPTS -o BatchMode=yes -o ConnectTimeout=10 "$@"; }

play() {
  (cd "$REPO/ansible" && ansible-playbook -i "$INVENTORY" -l "$LIMIT" homelab-harden.yml "$@" </dev/null 2>&1)
}

# check ID "what is claimed" "expected" "actual" -- exact match
check() {
  if [ "$3" = "$4" ]; then
    pass=$((pass + 1))
    printf 'PASS  %-6s %s\n' "$1" "$2"
  else
    fail=$((fail + 1))
    failed_ids+=("$1")
    printf 'FAIL  %-6s %s\n        expected: %s\n        actual:   %s\n' "$1" "$2" "$3" "$4"
  fi
}

# The task a play died in. Ansible prints the error's source lines between
# the TASK header and `fatal:`, so "a few lines above" is not a place.
failed_task() { awk '/^TASK \[/{t=$0} /^fatal:/{print t; exit}' <<<"$1" | sed 's/ \*\**$//'; }

# contains ID "what is claimed" "needle" "haystack"
contains() {
  if [[ $4 == *"$3"* ]]; then
    check "$1" "$2" ok ok
  else
    check "$1" "$2" "output containing: $3" "$(printf '%s' "$4" | tail -3 | tr '\n' '|')"
  fi
}

cleanup() {
  on_target 'sudo rm -f /etc/ssh/sshd_config.d/01-claims-test.conf /etc/sysctl.d/99-claims-test.conf
    sudo sed -i "/^BogusOption claims-test$/d" /etc/ssh/sshd_config
    if sudo test -f /root/.ssh/authorized_keys; then
      sudo sed -i "/ claims-test$/d" /root/.ssh/authorized_keys
      sudo test -s /root/.ssh/authorized_keys || sudo rm -f /root/.ssh/authorized_keys
    fi
    sudo rmdir /root/.ssh 2>/dev/null # only if empty
    sudo docker rm -f claims-web >/dev/null 2>&1
    sudo docker network rm claims-net >/dev/null 2>&1
    sudo sysctl --system >/dev/null 2>&1 # the file is gone; so must its value be
    true' >/dev/null 2>&1
}

# An interrupted intrusive run can stop between a fault and its repair.
# Converge again on the way out.
# Intrusive runs only: a read-only run places nothing, so it must remove nothing.
finish() {
  if [ "$intrusive" = 1 ]; then
    cleanup
    play >/dev/null
  fi
}

read_only() {
  local out

  out=$(ssh_as -o PreferredAuthentications=none -o PubkeyAuthentication=no "$TARGET" true 2>&1 | tail -1)
  contains SSH-1 "sshd offers public key only" "Permission denied (publickey)." "$out"

  out=$(ssh_as -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no "$TARGET" true 2>&1 | tail -1)
  contains SSH-2 "password login is refused" "Permission denied (publickey)." "$out"

  # The client only asks for X11 when it has a DISPLAY of its own.
  # shellcheck disable=SC2016  # $DISPLAY must expand on the target, not here
  out=$(DISPLAY=${DISPLAY:-:0} ssh_as -X "$TARGET" 'echo "remote-display=[${DISPLAY:-}]"' 2>&1)
  contains SSH-4 "X11 forwarding request is refused" "X11 forwarding request failed" "$out"
  contains SSH-4 "no DISPLAY is set in the session" "remote-display=[]" "$out"

  # A server without xauth refuses X11 whatever its config says, so the
  # refusal above can pass with the control gone. Read the setting as well.
  out=$(on_target 'sudo sshd -T | grep "^x11forwarding "')
  check SSH-4 "sshd's effective config forbids X11" "x11forwarding no" "$out"

  out=$(on_target 'sudo sshd -T | grep "^permitrootlogin "')
  check SSH-3 "sshd's effective config forbids root login" "permitrootlogin no" "$out"

  # apt-config reads the merged config, so a later apt.conf.d file that turns
  # a periodic off is seen; reading 20auto-upgrades alone would not see it.
  # shellcheck disable=SC2016  # $L$U must expand on the target, not here
  out=$(on_target 'systemctl is-active unattended-upgrades apt-daily.timer apt-daily-upgrade.timer; systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer; eval "$(apt-config shell U APT::Periodic::Unattended-Upgrade L APT::Periodic::Update-Package-Lists)"; echo "$L$U"' 2>&1 | tr '\n' ' ')
  check UPD-1 "shutdown guard and timers active, timers enabled, both periodics on" "active active active enabled enabled 11 " "$out"

  out=$(on_target 'sudo unattended-upgrade --dry-run -d 2>&1 | grep "Allowed origins"')
  contains UPD-2 "the security archive is an allowed origin" "Debian-Security" "$out"

  # grep -L exits 1 when it lists nothing, so END follows with `;`. A failed
  # ssh also prints nothing: without END, an unreachable target would pass.
  out=$(on_target 'grep -L "^0$" /proc/sys/net/ipv[46]/conf/*/accept_redirects; echo END' | tr '\n' ' ')
  check NET-1 "no interface accepts ICMP redirects" "END " "$out"

  out=$(on_target 'grep -L "^0$" /proc/sys/net/ipv4/conf/*/send_redirects; echo END' | tr '\n' ' ')
  check NET-2 "no interface sends ICMP redirects" "END " "$out"

  out=$(on_target 'cat /proc/sys/net/ipv4/conf/all/rp_filter')
  check NET-3 "reverse-path filtering is loose" "2" "$out"

  out=$(on_target 'sudo ss -lntuH | grep -c ":5355 "')
  check NET-5 "nothing listens on the LLMNR port" "0" "$out"
  # A dead host refuses nothing and is "closed" too, so a closed 5355 only
  # counts while ssh on the same host is answering.
  if ! timeout 5 bash -c "</dev/tcp/$HOST/22" 2>/dev/null; then out=unreachable
  elif timeout 5 bash -c "</dev/tcp/$HOST/5355" 2>/dev/null; then out=open; else out=closed; fi
  check NET-5 "LLMNR port does not accept a connection from here" "closed" "$out"

  # NET-9. Mirrors the loopback-bound host ports in stacks/*/compose.yaml
  # (homepage, forgejo web, karakeep, vikunja, n8n, mealie).
  # Keep in step by hand: nothing derives it. ntfy 2586, uptime-kuma 3004
  # (the phone pushes to it directly) and forgejo ssh 3003
  # are absent on purpose -- they stay published on every interface.
  # Same guard as NET-5: a dead host refuses nothing, so "none open" only
  # counts while ssh answers. On a host with no stacks nothing listens and
  # this passes trivially.
  local app_ports="3001 3002 3006 3456 5679 9925" p open_ports=""
  if ! timeout 5 bash -c "</dev/tcp/$HOST/22" 2>/dev/null; then open_ports=unreachable
  else
    for p in $app_ports; do
      if timeout 5 bash -c "</dev/tcp/$HOST/$p" 2>/dev/null; then open_ports+="$p "; fi
    done
    if [[ -z $open_ports ]]; then open_ports=none; fi
  fi
  check NET-9 "no application port accepts a connection from here" "none" "$open_ports"

  out=$(on_target 'cat /proc/sys/net/ipv4/ip_forward /proc/sys/net/ipv6/conf/all/forwarding' | tr '\n' ' ')
  check NET-6 "forwarding is on, v4 and v6" "1 1 " "$out"

  # Not `head -1`: the first symbol really is at address zero, restricted or not.
  out=$(on_target 'sudo cat /proc/kallsyms | grep -vc "^0000000000000000 "')
  check KRN-1 "kernel addresses are hidden from root" "0" "$out"

  out=$(on_target 'sudo -u nobody dmesg 2>&1 | tail -1')
  contains KRN-2 "an unprivileged user cannot read the kernel log" "Operation not permitted" "$out"

  out=$(on_target 'cat /proc/sys/kernel/yama/ptrace_scope')
  check KRN-3 "a process may only ptrace its own children" "1" "$out"
}

intrusive_checks() {
  local out

  # SSH-3: root has no key by default, so a refusal proves nothing until it does.
  local before
  before=$(on_target 'sudo journalctl -u ssh --no-pager | grep -c "ROOT LOGIN REFUSED"')
  on_target 'sudo install -d -m 700 /root/.ssh
    sed "s/$/ claims-test/" ~/.ssh/authorized_keys | sudo tee -a /root/.ssh/authorized_keys >/dev/null
    sudo chmod 600 /root/.ssh/authorized_keys'
  out=$(ssh_as "root@$HOST" 'echo ROOT-LOGIN-WORKED' 2>&1 | tail -1)
  contains SSH-3 "root is refused while holding a valid key" "Permission denied" "$out"
  out=$(on_target 'sudo journalctl -u ssh --no-pager | grep -c "ROOT LOGIN REFUSED"')
  # sshd writes more than one line per refused attempt, so: more than before.
  check SSH-3 "sshd logged this refusal" "more" "$([ "${out:-0}" -gt "${before:-0}" ] && echo more || echo "no new line ($before -> $out)")"
  cleanup

  on_target 'echo "PermitRootLogin yes" | sudo tee /etc/ssh/sshd_config.d/01-claims-test.conf >/dev/null'
  out=$(play)
  contains SSH-5 "an overriding drop-in fails the play" "failed=1" "$out"
  check SSH-5 "it fails at the sshd read-back" "TASK [What sshd enforces]" "$(failed_task "$out")"
  cleanup

  on_target 'echo "BogusOption claims-test" | sudo tee -a /etc/ssh/sshd_config >/dev/null'
  out=$(play)
  contains SSH-6 "a config that does not parse fails the play" "rescued=1" "$out"
  check SSH-6 "it fails at the parse check" "TASK [Whole sshd config still parses]" "$(failed_task "$out")"
  out=$(on_target 'test -e /etc/ssh/sshd_config.d/10-homelab.conf && echo present || echo absent')
  check SSH-6 "our unchanged drop-in was left in place" "present" "$out"
  out=$(on_target 'echo still-reachable' 2>&1)
  check SSH-6 "ssh still accepts logins" "still-reachable" "$out"
  cleanup

  on_target 'echo "net.ipv4.conf.all.rp_filter = 1" | sudo tee /etc/sysctl.d/99-claims-test.conf >/dev/null
    sudo sysctl --system >/dev/null 2>&1'
  out=$(play)
  contains NET-7 "an overriding sysctl file fails the play" "failed=1" "$out"
  check NET-7 "it fails at the kernel read-back" "TASK [What the kernel enforces]" "$(failed_task "$out")"
  cleanup

  # A glob key overridden for one interface: `all` still reads 0, so only the
  # per-interface read-back can see it. lo, because every host has one.
  on_target 'echo "net.ipv4.conf.lo.send_redirects = 1" | sudo tee /etc/sysctl.d/99-claims-test.conf >/dev/null'
  out=$(play)
  contains NET-7 "a per-interface override fails the play" "failed=1" "$out"
  check NET-7 "it fails at the interface read-back" "TASK [What every interface enforces]" "$(failed_task "$out")"
  cleanup

  on_target 'sudo docker network create claims-net >/dev/null'
  out=$(on_target 'ls -d /proc/sys/net/ipv4/conf/br-* >/dev/null 2>&1 || echo no-bridge-found
    grep -L "^0$" /proc/sys/net/ipv[46]/conf/*/accept_redirects /proc/sys/net/ipv4/conf/*/send_redirects; echo END' | tr '\n' ' ')
  check NET-4 "a bridge created after the run has the same settings" "END " "$out"

  on_target 'sudo docker run -d --name claims-web -p 18080:80 nginx:alpine >/dev/null 2>&1; sleep 3'
  out=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "http://$HOST:18080/")
  check NET-8 "a published container port answers from here" "200" "$out"
  out=$(on_target 'sudo docker exec claims-web wget -q -T 8 -O /dev/null http://deb.debian.org/ && echo ok')
  check NET-8 "a container reaches the outside" "ok" "$out"
  cleanup

  play >/dev/null
  out=$(play | grep -A1 'PLAY RECAP' | tail -1)
  contains PLY-1 "a second run changes nothing" "changed=0" "$out"
  contains PLY-1 "and fails nothing" "failed=0" "$out"

  out=$(play --check | grep -A1 'PLAY RECAP' | tail -1)
  contains PLY-3 "a dry run on a converged host changes nothing" "changed=0" "$out"
  contains PLY-3 "and fails nothing" "failed=0" "$out"
}

trap finish EXIT
# Without this, a Ctrl-C that lands in a playbook run is swallowed by Ansible
# and the script carries on to the next check.
trap 'exit 130' INT TERM

echo "== read-only proofs against $TARGET"
read_only

if [ "$intrusive" = 1 ]; then
  echo "== intrusive proofs"
  intrusive_checks
fi

if [ "$reboot" = 1 ]; then
  echo "== PLY-2: reboot, then every proof again"
  on_target 'sudo systemctl reboot' >/dev/null 2>&1
  sleep 15
  up=down
  for _ in $(seq 1 40); do
    if on_target true >/dev/null 2>&1; then up=up; break; fi
    sleep 3
  done
  check PLY-2 "the VM came back after a reboot" "up" "$up"
  out=$(on_target 'systemctl --failed --no-legend' | tr '\n' ' ')
  check PLY-2 "no unit failed at boot" "" "$out"
  read_only
  intrusive_checks
fi

echo
echo "== summary: $pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then
  echo "   failed: ${failed_ids[*]}"
  exit 1
fi
