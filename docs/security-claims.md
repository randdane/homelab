# Security claims

Every statement we make about how the Docker VM is secured, one row each, with
the command that proves it. A claim with no proof is a hope; this file is the
list a test suite has to cover, and a claim leaves the list only when the
control behind it is removed on purpose.

The controls are applied by `ansible/homelab-harden.yml`. Run on the VM unless
a row says "from another host".

**Status** is where the evidence stands, not how confident anyone feels:

- **proven** — the proof was run on a throwaway clone of the VM template and
  gave the expected result. Date in the row.
- **read back** — the setting was read and is what we intend, but nothing
  attacked it. Weaker than proven.
- **unproven** — claimed, never tested.

The statuses in the tables describe the throwaway VM. On production the
playbook was applied on 2026-10-02 and the read-only suite went from 8 passed,
9 failed to 17 passed, 0 failed, with no container restarted. That covers
SSH-1 to SSH-4, UPD-1, UPD-2, NET-1 to NET-3, NET-5, NET-6 and KRN-1 to KRN-3,
plus PLY-1 (the second run changed nothing). The fault-injecting and reboot
proofs (SSH-5, SSH-6, NET-4, NET-7, NET-8, PLY-2, PLY-3) have never run on
production and are not meant to, except the reboot.

## Rules for this file

- A new control in the playbook gets a row here in the same change.
- A proof is a command with one expected result, runnable without judgement.
  "Looks right" is not a proof.
- Prefer a proof that attacks the control over one that reads its config. SSH-3
  is the model: root had no key, so "root is refused" proved nothing until a
  valid key was installed for root first.
- Every row is automated in `scripts/prove-security-claims.sh`, keyed by ID.
  A row added here needs a check there.
- A proof that cannot fail is worse than none. Break the control and watch the
  check go red before trusting it: two of the first proofs here (KRN-1 and
  SSH-4) passed with their control removed.

## SSH

| ID | Claim | Proof | Expected | Status |
|---|---|---|---|---|
| SSH-1 | sshd offers public-key authentication and nothing else | From another host: `ssh -o BatchMode=yes -o PreferredAuthentications=none -o PubkeyAuthentication=no <user>@<vm>` | `Permission denied (publickey).` — no other method in the brackets | proven 2026-10-01 |
| SSH-2 | A password login is refused before any password is asked for | From another host: `ssh -o BatchMode=yes -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no <user>@<vm> true` | `Permission denied (publickey).` | proven 2026-10-01 |
| SSH-3 | root cannot log in over ssh, even holding a valid key | Copy the login user's `authorized_keys` to `/root/.ssh/`, then from another host `ssh root@<vm> true`; remove `/root/.ssh` afterwards | Refused, and `journalctl -u ssh` holds `ROOT LOGIN REFUSED` | proven 2026-10-01 |
| SSH-4 | X11 forwarding is refused | From another host: `ssh -X <user>@<vm> 'echo "[$DISPLAY]"'` | `X11 forwarding request failed`, and `[]`; and `sudo sshd -T` has `x11forwarding no`, because a server without `xauth` refuses the request with the control removed | proven 2026-10-01 |
| SSH-5 | Another drop-in cannot quietly undo SSH-1 to SSH-4. Global settings only — see `Match` under Not claimed | Write `PermitRootLogin yes` to `/etc/ssh/sshd_config.d/01-claims-test.conf`, run the playbook, remove the file | The play fails at "What sshd enforces" | proven 2026-10-02. The suite injects `PermitRootLogin yes`; the other three settings were each injected by hand once and are not automated |
| SSH-6 | A config that does not parse stops the play before ssh is reloaded, and takes our drop-in out only when this run just wrote it | Append `BogusOption claims-test` to `/etc/ssh/sshd_config` on a converged host, run the playbook, restore the file | The play fails at "Whole sshd config still parses", our unchanged drop-in is left in place, and logins still work | proven 2026-10-02, on a rebuilt throwaway VM, before and after a reboot. The other branch, a broken config on the run that first writes the drop-in, was run by hand once the same day: the drop-in was removed and the play said so. The suite cannot reach it, since it starts from a converged host. That the reload itself was skipped is read from the play's task list, not tested |

## Updates

| ID | Claim | Proof | Expected | Status |
|---|---|---|---|---|
| UPD-1 | Debian security updates install without anyone asking | `systemctl is-active unattended-upgrades apt-daily.timer apt-daily-upgrade.timer`; `systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer`; `apt-config dump APT::Periodic::Update-Package-Lists APT::Periodic::Unattended-Upgrade` | `active` three times; `enabled` twice; both `APT::Periodic` values are `"1"` in APT's merged config | read back 2026-10-02 — the machinery is switched on; no upgrade has been watched installing |
| UPD-2 | The security archive is among the origins it will install from. **Detected, not enforced**: the origins file is Debian's packaged one and the playbook leaves it alone, so a broken origin turns this red and stays red until someone fixes it | `sudo unattended-upgrade --dry-run -d \| grep 'Allowed origins'` | The line names `Debian-Security` | proven 2026-10-01 |

## Network

| ID | Claim | Proof | Expected | Status |
|---|---|---|---|---|
| NET-1 | The VM ignores ICMP redirects, on every interface, v4 and v6 | `grep -L '^0$' /proc/sys/net/ipv[46]/conf/*/accept_redirects` | No output | read back 2026-10-01 — no redirect was ever sent at it |
| NET-2 | The VM sends no ICMP redirects | `grep -L '^0$' /proc/sys/net/ipv4/conf/*/send_redirects` | No output | read back 2026-10-01 |
| NET-3 | Reverse-path filtering is loose, not off and not strict, on every interface. Only `all` is set and read: the kernel uses the higher of `all` and the interface's value, so `all = 2` settles it | `sysctl -n net.ipv4.conf.all.rp_filter` | `2` | read back 2026-10-02 |
| NET-4 | An interface created after the run gets NET-1 to NET-3 as well | `docker network create t`, then repeat the three proofs; `docker network rm t` | Same results with the new bridge present | proven 2026-10-01 |
| NET-5 | Nothing answers LLMNR | `sudo ss -lntuH \| grep -c ':5355 '`; and from another host on the same segment, a TCP connect to 5355 | `0`; connection refused | proven 2026-10-01 |
| NET-6 | Forwarding stays on, v4 and v6 — the VM routes for the tailnet, so this is an availability claim | `sysctl -n net.ipv4.ip_forward net.ipv6.conf.all.forwarding` | `1` and `1` | read back 2026-10-01 |
| NET-7 | A later file in `sysctl.d` cannot quietly undo a value the playbook sets: every plain key is read back, and the redirect keys are read back on every interface | Write `net.ipv4.conf.all.rp_filter = 1` to `/etc/sysctl.d/99-claims-test.conf`, run the playbook, remove the file. Repeat with `net.ipv4.conf.lo.send_redirects = 1` | The play fails at "What the kernel enforces", then at "What every interface enforces" | proven 2026-10-02 |
| NET-8 | None of the above breaks container networking | Run a container with a published port; fetch it from another host; fetch an outside URL from inside the container | Both succeed | proven 2026-10-01 |

## Kernel

| ID | Claim | Proof | Expected | Status |
|---|---|---|---|---|
| KRN-1 | Kernel addresses are hidden, from root too | `sudo cat /proc/kallsyms \| grep -vc '^0000000000000000 '` — not `head -1`: the first symbol is at address zero on any kernel, so that proof could not fail | `0` | proven 2026-10-01 |
| KRN-2 | An unprivileged user cannot read the kernel log | As a user outside `adm` and `sudo`: `dmesg` | `Operation not permitted` | proven 2026-10-01 |
| KRN-3 | A process may ptrace only its own children, not any process of the same user | `cat /proc/sys/kernel/yama/ptrace_scope` | `1` | read back 2026-10-02 — nothing tried to attach |

## The playbook itself

| ID | Claim | Proof | Expected | Status |
|---|---|---|---|---|
| PLY-1 | A second run changes nothing | Run the playbook twice | Second recap: `changed=0` | proven 2026-10-01 |
| PLY-2 | Everything above survives a reboot. This is the only proof of persistence: the other checks read live values, so a line missing from a drop-in shows up here and nowhere else | `--intrusive --reboot`: reboot, then rerun every proof in this file | Same results, and `systemctl --failed` is empty | proven 2026-10-02, every row. It has caught one real regression: NET-1 and NET-2 failed after a reboot on `lo` and on `eth0`'s IPv6 side until the redirect keys became globs |
| PLY-3 | A dry run is safe on a host that already has the controls. It is not a full test: `--check` runs the sshd parse check but skips "Apply sysctl" and all three read-backs, which would fail on a host the playbook has not yet touched | `ansible-playbook … --check` | `failed=0`, `changed=0` | proven 2026-10-02 |

## Not claimed

These are gaps, written down so nobody reads the tables above as the whole
story. Each becomes a claim when something enforces it.

| Gap | Why it is open |
|---|---|
| No inbound firewall on the VM | Belongs in the hypervisor's per-VM firewall file, which accepts inbound by default today. The VM routes for the tailnet, so a default-deny needs its own traffic list and test |
| Application ports published on every interface | LAN clients reach those apps directly, past the reverse proxy's allowlist and SSO. Per stack, in compose |
| The login user is root-equivalent | Passwordless sudo, and Docker group membership on the real VM. The ssh key is the only barrier |
| A `Match` block can re-enable a setting per user | SSH-5 reads global values only. A `Match User` block with `X11Forwarding yes` parsed and took effect in testing |
| Docker's own packages are not auto-updated | UPD-2's origins are Debian's. Adding Docker's would restart every container unattended |
| Nothing reboots after a kernel update, or says one is due | No automatic reboot, no notice |
| Tailnet routing under NET-1 to NET-3 | The throwaway VM had no tailnet identity; a routed network namespace stood in for it and worked. On production after the apply (2026-10-02) the routes are still advertised and approved and the kernel logged no dropped source, but subnet-route and exit-node traffic from outside the LAN has not been checked |
| NET-3 reads `all` only | systemd's packaged `50-default.conf` already sets every interface but `all` to 2. A host with `all = 0` fails NET-3 while filtering loosely on every interface, which is what production looked like before the apply. The proof is right about `all` and blind to the rest |
| Persistence on production | PLY-2 ran on the throwaway VM. Production has not been rebooted since the apply |
| IPv6 router advertisements with forwarding on | Not the kernel's call here: `systemd-networkd` owns the interface and holds `accept_ra` at 0 with forwarding on *or* off (measured 2026-10-02), handling advertisements itself. So `accept_ra = 2` would change nothing. networkd does still accept them with forwarding on: a test advertiser on a networkd-managed interface gave it an address and a default route. That was a stand-in interface under this VM's own config; the real LAN hands out no global IPv6 address, so the production path is unproven |
| Forwarded IPv6 is not filtered | Forwarding is on (NET-6) and there are no ip6tables rules. Part of the missing firewall, but worth its own line: the VM will route v6 for anyone who can send it a packet |
| The installer script, apt key and cloud image are fetched unpinned | In `ansible/homelab-vm.yml`, not the hardening playbook |
| NET-1 against a real redirect | The proof reads the setting. Nobody has sent the packet |
