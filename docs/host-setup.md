# Host setup

Everything a machine needs **before** the stacks in this repo will run, and
that cloning the repo does not give it. Per-stack secrets are not here — those
are each stack's `.env`, described by `prerequisites:` in its `compose.yaml`.

Written to be executed by an agent or a person, in order. Every step is
idempotent and ends with a check that proves it worked. Nothing here is
generated from a tracked config file on purpose: these are host facts, and a
second copy in the repo would drift from the real machine silently.

---

## 0. Install Docker — from Docker's apt repo, not Debian's

Everything below assumes Docker is present; a fresh host has none. Debian's
`docker.io` lags and has no `docker compose` plugin, so use Docker's
repository. Debian 13 shown; on Ubuntu swap `debian`/`trixie` for
`ubuntu`/the codename.

```bash
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"     # log out and back in
```

**Check:** `docker version --format '{{.Server.Version}}' && docker compose version`

Go straight on to 1 — `daemon.json` must be in place before any container
exists.

---

## 1. Docker daemon defaults — address pools and log limits

**Do this first.** Without it, starting more than ~31 stacks fails, and the
error names an innocent stack:

```
failed to create network karakeep_default: Error response from daemon:
  all predefined address pools have been fully subnetted
```

Docker's defaults allow roughly 31 bridge networks (`172.16.0.0/12` in /16s,
`192.168.0.0/16` in /20s). This repo has 41 stacks and each gets its own
network, so the ceiling is reached before the roster is. Full reasoning in
`lessons-learned.md` §11.

`/etc/docker/daemon.json` does not exist on a fresh install. Create it:

**Log rotation belongs in the same file.** Docker's `json-file` driver is
unbounded by default: every container's log grows until the disk fills. It is
a slow failure that arrives as "the server stopped working" months later, and
an internet-facing service with access logging turned on reaches it fastest.
Setting it here covers every stack including ones not written yet — a
`logging:` block in a compose file only covers that stack, and silently opts
it out of any later change to this default.

**Two more keys belong here, and both were learned the hard way.**

`shutdown-timeout` is dockerd's graceful-stop budget: on shutdown it SIGTERMs
every container, waits this long, then SIGKILLs. The default is 15 s, and a
Postgres checkpoint on a 5400rpm disk can outrun that — silently, because an
abrupt shutdown looks exactly like a clean one until you next read the
database. `scripts/status.py` checks this hourly, so a host built without it
fails that check on day one.

`userland-proxy: false` is the difference between a container seeing its real
client and seeing a bridge gateway. With the default `true`, `docker-proxy`
accepts the connection and opens a *new* one to the container, so anything
arriving over `tailscale0` reached Caddy as `10.201.7.1`. That silently broke
every `remote_ip` allowlist and made CrowdSec reason about a rewritten
address. False switches Docker to plain netfilter DNAT, which preserves the
source.

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "default-address-pools": [
    { "base": "172.17.0.0/12", "size": 24 },
    { "base": "10.201.0.0/16", "size": 24 }
  ],
  "userland-proxy": false,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  },
  "shutdown-timeout": 30
}
EOF
sudo systemctl restart docker
```

If the file already exists with other keys, **merge** rather than overwrite —
clobbering someone's `data-root` or registry settings is a bad afternoon.

`size: 24` gives 4096 networks of 254 addresses each instead of 16 of 65k. No
stack here needs more than a handful.

**Check:**

```bash
docker info -f '{{json .DefaultAddressPools}}'
# [{"Base":"172.16.0.0/12","Size":24},{"Base":"10.201.0.0/16","Size":24}]
```

The daemon reports `172.16.0.0/12` for the `172.17.0.0/12` that was written —
it normalizes to the natural /12 boundary. Expected, not an error.

> **Before running this on a new host, check the second base against the real
> network:** `ip route` and `tailscale status`. If anything routes through
> `10.201.0.0/16`, pick a different range. An overlap is blackholed by
> Docker's routes and presents as "the internet is broken", not as a Docker
> problem — the most expensive way to learn this.

The restart stops every running container. Existing networks keep the subnets
they already have; the new sizing applies only to networks created afterwards,
so nothing breaks retroactively.

---

## 1b. Do not let a laptop server suspend itself — only if the host is a laptop

A laptop used as a server is the case here. Its default `HandleLidSwitch=suspend`
means closing the lid takes every service off the network, and it does not
come back on its own. Do not plan on Wake-on-LAN as the recovery path: the
consumer Inspiron BIOS has no Wake on LAN/WLAN option at all (that is a Dell
business-line feature), so once this machine is down, only the BIOS **Auto On
Time** — set to 09:00 **UTC**, which is 04:00 CDT / 03:00 CST — or the power
button brings it back. Preventing
the suspend is the entire mitigation.

This looked like "the server is powered off overnight" for weeks. It was a
lid switch.

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/99-server.conf <<'EOF'
[Login]
# This machine is a server that happens to be a laptop.
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
sudo systemctl reload systemd-logind
```

If a desktop session is installed, it has its own power management that
overrides none of the above but adds its own suspend on battery. The battery
is the machine's UPS — suspending when mains power fails defeats the entire
point:

```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
```

Run those as the desktop user on the machine itself; they need its session bus.

Verify, after a lid close:

```bash
journalctl -b -u systemd-logind | grep -i "lid"     # should say ignored
systemctl status sleep.target                        # should be inactive
```

**Also set the BIOS to restore power state after an outage** ("Wake on AC" /
"AC Recovery" -> Power On). Nothing in the OS can help if the battery drains
and mains returns — only firmware can. On the first server that was set, alongside
Auto On Time 09:00 UTC and battery charge limits (Primary AC Use, start 75 / stop 80)
to slow wear on a battery that is permanently on mains.

## 1c. The backup stack on a partial host — only if not every stack is deployed

`stacks/backup/compose.yaml` is the canonical registry: every volume in this
repo that must be archived, on any host. It declares them all `external: true`,
so Compose refuses to start the stack unless all of them already exist. On a
host running only part of the roster that means the backup stack cannot come
up at all — including a brand new host, where nothing is backed up yet and the
need is greatest.

Render a host-specific file containing only the volumes Docker actually has:

```bash
uv run scripts/backup_here.py            # writes stacks/backup/compose.host.yaml
docker compose -f compose.host.yaml up -d
```

The skipped volumes are printed on every run, by design. Do not silence that,
and do not "fix" the missing ones by creating them empty: a nightly archive of
empty directories succeeds and looks exactly like one that covered everything,
and you would not find out until a restore.

**Recreate the backup container whenever a stateful stack is added.** Its
mount list is fixed at creation: a container started before a stack existed
keeps backing up the old set forever and reports success while doing it. On
the first server this had silently excluded `authentik_database` and `caddy_data` —
the account database and the ACME keys. Check what it actually sees:

```bash
docker exec backup sh -c 'cd /backup && for d in *; do \
  printf "%-24s %s\n" "$d" "$(ls -A "$d" 2>/dev/null | wc -l)"; done'
```

---

## 2. Free port 53 for a containerised DNS server — only if running one

`systemd-resolved` holds 53 on most desktop Linux installs. On a **server**
that should serve LAN DNS, take it back:

```bash
sudo sed -i 's/^#\?DNSStubListener=.*/DNSStubListener=no/' /etc/systemd/resolved.conf
sudo ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
sudo systemctl restart systemd-resolved
```

**Check:** `sudo ss -lnup | grep ':53 '` shows nothing before the DNS server starts.

On a **laptop**, do not do this — leave resolved alone and publish the DNS
server's port 53 on a high port instead. `5353` is taken by mDNS; this
repo uses **5335**. The DNS server is not the machine's resolver there anyway.

---

## 3. Tailscale — natively, not in a container

Deliberate: containerizing it needs host networking, `NET_ADMIN`, and
`/dev/net/tun`, and complicates subnet routing and MagicDNS for no gain. Install from the distribution package, then:

```bash
sudo tailscale up
tailscale status
```

Note it **already listens on 443** on this laptop — check before assigning
that port to anything.

---

## 4. (retired) Elasticsearch memory map limit

Only TubeArchivist needed it, and that stack was removed 2026-10-05 in favour
of `stacks/ytdl-sub`, which has no database. No stack here runs
Elasticsearch, so leave `vm.max_map_count` at the kernel default.

---

## 5. Hardware transcoding — only if running Jellyfin on the server

Jellyfin's `prerequisites` name `/dev/dri`. Confirm the device exists and the
container's user can reach it:

```bash
ls -l /dev/dri/renderD128        # exists, and note its group
getent group render video        # the GID the container needs
```

If absent, Jellyfin still runs — transcoding falls back to CPU, badly.

---

## 6. `HOMELAB_HOST`, where systemd can see it

`homepage.href` labels and the status file name both use it. Set it in
`~/.config/environment.d/`, **not** `~/.bashrc` — the status timer never
sources `.bashrc`, and a value set only there splits one host's history
across two state files (`README.md` explains the failure):

```bash
mkdir -p ~/.config/environment.d
echo 'HOMELAB_HOST=<hostname-or-tailscale-name>' > ~/.config/environment.d/homelab.conf
systemctl --user daemon-reload
```

**Check:** `systemctl --user show-environment | grep HOMELAB_HOST` after a
re-login.

---

## 7. The timers — install them with the script, not by hand

One installer, server only. It renders the unit templates in
`scripts/systemd/*.in` and enables them:

```bash
sudo ./scripts/install-systemd.sh --system
```

**The laptop mode is retired, 2026-09-25.** Run without `--system`, the
script refuses and exits 1. On the laptop its units did nothing useful. The
status capture recorded stacks that are down by design. The DERP check was
an outdated copy that skipped on every recent run. And none of them could
alert, because `notify.py`'s ntfy credentials exist only on the server: 14
failed alerts in September, all silent. Every check already runs on the
server. The laptop job that matters, the weekly vault commit
(a separate timer), reports through healthchecks.io.

| Timer | What it is for |
|---|---|
| `homelab-status` | hourly capture, so a stack started and stopped between manual runs still leaves a trace |
| `homelab-backup` | the nightly archive |
| `homelab-verify` | monthly restore verification |
| `homelab-monitor-check` | Uptime Kuma is itself monitored |
| `homelab-derp-check` | a degraded DERP relay answers a plain HTTP check normally |
| `homelab-smart` | monthly SMART heartbeat — an alerter that has died is silent exactly like a healthy disk. **Skipped inside a VM**: on VM 101 it is `pve-smart.timer` on `pve` instead (`docs/smart.md`) |
| `homelab-updates` | what is out of date |

Also installed: `homelab-digest`, `homelab-reconcile`, the
`homelab-tailnet-source` units, and `homelab-failure-notify@`, the
`OnFailure=` alert path.

**Do not symlink the unit files.** Earlier revisions of this section did, and
they are now `.in` templates: systemd expands `%h` in `WorkingDirectory` but
not environment variables, so the installer writes the resolved paths in. A symlink to `scripts/systemd/homelab-status.service` now points at a
file that does not exist — and `ln -sf` **succeeds** creating a dangling link,
so the failure surfaces later at `systemctl enable`, looking unrelated. The
installer also replaces any symlink it finds, so an old install self-repairs.

It **refuses** to install while old `homelab-*` user units are left in
`~/.config/systemd/user`. They would run beside the system units, which is
what happened on the first server for three days in August.

**Check:** the timer list has **9** entries on VM 101 (10 on bare metal,
with `homelab-smart`) and the last status run is clean.

```bash
systemctl list-timers 'homelab-*' --no-pager
journalctl -u homelab-status -n 20
```

---

## 8. Backup archive destination

The `backup` stack writes outside the repo, to
`${BACKUP_ARCHIVE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/homelab/backups}`.
The default works with no `.env` entry. On a server, point it at storage that
is not the disk being backed up:

```bash
export BACKUP_ARCHIVE_DIR=/mnt/backups/homelab   # in stacks/backup/.env
```

Archives must survive `docker volume prune`, which is why this is a bind
mount and not a named volume — see `conventions.md`.

---

## 9. Point smartd at the phone — servers only

`smartmontools` installs enabled, monitoring the disk, and mailing `root` on a
box with no MTA. It watches correctly and reports into a void, which looks
identical to a healthy disk. On the first server it did that from setup
until 2026-08-28.

```bash
sudo sed -i 's|-M exec /usr/share/smartmontools/smartd-runner|-M exec /opt/homelab/scripts/smart-alert.sh|' /etc/smartd.conf
grep DEVICESCAN /etc/smartd.conf
sudo systemctl restart smartmontools
```

`scripts/smart-report.sh` runs as the owning user, so it also needs a NOPASSWD
rule for `smartctl`. Draft it, check it, then install it — a syntax error in a
sudoers file locks you out of `sudo`:

```bash
echo 'r ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/du' > /tmp/audit-readonly
sudo visudo -cf /tmp/audit-readonly && \
  sudo install -m 0440 -o root -g root /tmp/audit-readonly /etc/sudoers.d/audit-readonly
sudo -n smartctl -H /dev/sda      # proves both halves
```

Full operational detail, including how to change the repeat interval and how
to test delivery: `docs/smart.md`.

---

## 10. The repo-root `.env` — every host, before any stack

**Do this before the per-stack files below.** `caddy` and `headscale` read
their hostnames from `/.env`, not from their own `.env`, and neither will
start without it — Caddy fails on an invalid site address, headscale on a
missing `server_url`. `scripts/check_derp.py` exits 1 rather than reporting a
clean run.

```bash
cd "$REPO" && cp .env.example .env && chmod 600 .env
$EDITOR .env          # fill all three; none has a default
```

Verify before moving on:

```bash
grep -c '^[A-Z_]*=$' .env                     # 0 -- an unfilled key is a broken stack
uv run scripts/check_derp.py --self-test      # the drift assertions
uv run scripts/check_derp.py                  # 'clean:' or a named problem, not 'NOT RUN'
```

**The laptop needs this too**, even though it runs no production stack.
`homelab-derp-check.timer` is installed on both hosts, and without the file
its next run fails rather than skipping. That is deliberate — a monitor that
cannot read its own configuration should say so — but it means a laptop with
no `/.env` reports a failed unit daily.

`HEADSCALE_SERVER_URL` is `DUCKDNS_HOST` again with a scheme. They are
separate keys because Compose cannot build one variable from another across
files, and the consumers need different forms. `check_derp.py` fails if they
disagree, so a half-done edit is caught rather than silently re-registering
every tailnet node against a URL that does not work.

---

## 11. The `edge` network — create it with an explicit subnet

Compose will not create it: `edge` is `external: true` in every stack that
joins it (caddy, headscale, crowdsec, authentik, life-queue,
uptime-kuma, ntfy). **Do not use a bare `docker network create edge`.**

```bash
docker network create \
  --subnet   10.201.7.0/24 \
  --ip-range 10.201.7.0/25 \
  edge
```

Two flags, two different jobs:

- **`--subnet` makes the addresses reproducible.** Without it Docker takes the
  next free `/24` from its address pool, which depends on what order networks
  were created in. `stacks/caddy` pins itself to `10.201.7.240`, and a static
  address must belong to the network's subnet — land on `10.201.5.0/24`
  instead and Caddy refuses to start, on a freshly rebuilt host, for a reason
  that has nothing to do with Caddy.
- **`--ip-range` keeps dynamic allocation away from the pin.** `/25` confines
  it to `.1`–`.126`, so `.240` is genuinely reserved rather than merely
  improbable. Without this, `edge` has a subnet and no range, and Docker may
  hand `.240` to anything.

Verify:

```bash
docker network inspect edge --format '{{json .IPAM.Config}}'
# [{"Subnet":"10.201.7.0/24","IPRange":"10.201.7.0/25","Gateway":"10.201.7.1"}]
```

**The first server's existing network predated this and had no `IPRange`.** Adding one
means recreating the network, which requires disconnecting every attached
container — an edge outage covering Caddy and headscale. It is not
worth scheduling on its own; do it at the next rebuild, or fold it into a
maintenance window that was happening anyway. Until then `.240` is above
expected occupancy rather than reserved, which with eight containers on a
`/24` has held fine.

---

## 12. The `admin` network — Caddy and the UIs with no login

`dozzle` and `cup` have no authentication, so their host ports are bound to
`127.0.0.1` and Caddy serves them behind the same `remote_ip` gate as the
other internal vhosts. Caddy has to reach them by container name, which means
a shared network — and it must not be `edge`, because every public-facing
container is on `edge` and would then reach Dozzle's logs directly.

```bash
docker network create admin
```

No explicit subnet: nothing pins an address on it, and `remote_ip` never sees
these addresses (clients arrive through Caddy's published ports). Create it on
any host that runs either stack — the laptop included, since `dozzle` is
`host: both` and will not start without it.

Verify that only these three are attached:

```bash
docker network inspect admin --format '{{range .Containers}}{{.Name}} {{end}}'
# caddy cup dozzle   (in any order)
```

## 13. (retired) The `jellyfin` network and its egress rule

Removed at the move to `pve`: Jellyfin is no longer a container on this host,
so there is no `jellyfin` network to create and no egress rule to install. Its
isolation is now the Proxmox firewall on its LXC, `$SITE_DIR/pve/firewall/102.fw`. Why
it existed, and what replaced it: `docs/lessons-learned.md` §32.

---

## 14. Cap the journal — servers only

`journald.conf` ships empty, so the default cap is **10% of the filesystem**:
about 91 GB on a 1 TB disk. Nothing enforces a smaller number, and nothing watches
journal size. Measured 2026-09-17: 263 MB holding five weeks, so the default
would never have bitten — but a chatty unit changes that, and the first
symptom would be disk pressure on the drive everything else lives on.

```bash
printf '[Journal]\nSystemMaxUse=500M\nMaxRetentionSec=3month\n' | sudo tee -a /etc/systemd/journald.conf
sudo systemctl restart systemd-journald
journalctl --disk-usage
```

Both limits, because they answer different questions: `SystemMaxUse` bounds
the disk, `MaxRetentionSec` bounds how far back an investigation can read.
Whichever is hit first wins.

**This deletes nothing today** — it only applies from the restart onward, and
at 263 MB the cap is not close. That is deliberate. The alternative,
`journalctl --vacuum-time=14d`, would have thrown away the period covering
the Authentik upgrade, the Jellyfin containment and every backup run this
repo's incident notes refer back to.

What prompted it was noise, not size: a desktop snap
(`firmware-updater.firmware-notifier`) failed on a loop on this headless
server, 1,877 journal lines in seven days. **The fix for noise is removing its
source** (`sudo snap remove firmware-updater`), not trimming the journal —
journald deletes whole files by age or size and cannot drop one unit's lines.
It also never appeared in `systemctl --failed`, because it was a *user* unit;
`journalctl --since '2 days ago' -p err` is what found it.

## 15. Disable TX offloads on an `e1000e` NIC — only on Intel I218/I219

**Symptom this prevents.** The NIC wedges under sustained transmit. The link
stays up, `ethtool` reports `Link detected: yes`, every interface stays `UP`,
and no packets move. On a virtualisation host this takes the guests with it,
because they bridge through the same uplink. `dmesg` fills with:

```
e1000e 0000:00:1f.6 nic0: Detected Hardware Unit Hang:
  TDH  <b8>        next_to_use    <1>
  TDT  <1>         next_to_clean  <b7>
```

`TDH` is the hardware's position in the transmit ring and `TDT` is the
driver's. When they diverge and `next_to_watch.status` is `<0>`, the MAC has
stopped completing work the driver queued. It does not recover on its own.

It happened here on 2026-09-21: `pve` was unreachable for ten hours, triggered
within 20 seconds of a 1.3 G file transfer, while the machine itself was
perfectly healthy with a two-day uptime.

**Does this host need it?**

```bash
lspci -nn | grep -i ethernet          # I218-LM / I219-LM / I219-V -> yes
ethtool -i "$(ip -br link | awk '/UP/ && !/lo|vmbr|tap|veth|docker/ {print $1; exit}')" | grep driver
```

`driver: e1000e` means yes. Other Intel NICs (`igb`, `igc`, `ixgbe`) are not
affected by this bug and should be left alone — the offloads are a genuine
performance win where they work.

**Why disabling offloads fixes it.** TCP segmentation offload hands the NIC a
buffer larger than one packet and asks the hardware to split it. The hang is in
that segmentation path: with TSO off, the kernel does the splitting in software
and hands the NIC only ready-to-send frames, so the failing path is never
entered. The cost is a little CPU, which any modern processor absorbs without
noticing.

This is a **mitigation, not a cure** — the defect is in the NIC's
firmware/driver interaction and Intel has never fully fixed it. If hangs recur
with offloads off, put a cheap Intel PCIe NIC (an I210-T1 on a gigabit LAN) in
a spare slot and stop using the onboard port. Fit it at the console rather than
over SSH: interface naming changes, and a bridge pinned to `bridge-ports nic0`
needs editing in the same sitting or the host comes back with no network.

**Apply it now, for the running system:**

```bash
ethtool -K nic0 tso off gso off gro off
```

**And persist it**, or it is lost at the next reboot. In
`/etc/network/interfaces`, on the stanza that is marked `auto` — on Proxmox
that is the bridge, not the NIC, because the NIC is `iface nic0 inet manual`
with no `auto` line and is brought up as a bridge port:

```
auto vmbr0
iface vmbr0 inet static
	address 10.0.0.10/24
	gateway 10.0.0.1
	bridge-ports nic0
	bridge-stp off
	bridge-fd 0
	# e1000e TX hang mitigation -- see section 15 above
	post-up /sbin/ethtool -K nic0 tso off gso off gro off
```

Putting the hook on a stanza with no `auto` is the trap here: it parses
cleanly, and silently never runs.

**Check it before trusting it:**

```bash
ifreload -a --syntax-check        # compare against a copy of the pre-change file
ethtool -k nic0 | grep -E '^(tcp-segmentation|generic-segmentation|generic-receive)'
```

Two notes on that first command. It **does not apply anything**, unlike a bare
`ifreload -a`. And on a stock Proxmox bridge it exits non-zero with
`bridge-fd: value of out range "0"` regardless of your change — `bridge-fd 0`
is normal with STP off. Syntax-check the pre-change file too and compare: an
identical warning means your edit introduced nothing.

**The offload state only proves the running config.** That the `post-up` hook
actually fires is proven at the next reboot and not before, so re-run the
`ethtool -k` check after the first reboot rather than assuming.

**Do not restart networking to test this.** You are almost certainly connected
over the interface in question. The hook costs nothing until the next boot;
there is no reason to take the risk.

## Verify the host is ready

```bash
docker info -f '{{json .DefaultAddressPools}}'   # size 24, not 16
docker network ls -q | wc -l                     # headroom for 41 stacks
docker compose version                           # v2 plugin, not docker-compose
uv --version                                     # scripts/ are PEP 723
df -h /var/lib/docker                            # images alone are tens of GB
df -i /var/lib/docker                            # inodes exhaust independently
systemctl list-timers 'homelab-*' --no-pager     # 7 on a server, see host-setup §7
grep -c smart-alert /etc/smartd.conf             # 1, or SMART alerts go nowhere
ls -l stacks/*/.env | grep -cv '^-rw-------'     # 0, or credentials are readable
ls -l .env                                       # exists and 0600, see §10
uv run scripts/check_derp.py --self-test         # repo-wide .env is usable
```

Then, per stack: copy `.env.example` to `.env`, **`chmod 600` it**, then fill
**every *required* key left empty**, leave keys documented as optional empty
unless you are turning that feature on, and replace any remaining `CHANGEME`.
Read that stack's README before first run — several keys are unrecoverable if
set after first start (`lessons-learned.md` §3).

**What to fill each one with is in the comment above it**, and only some of
them are random. `openssl rand -base64 32` is right for a password or a
signing key; it is nonsense for `arr`'s `VPN_SERVICE_PROVIDER` (pick from the
list), `duckdns`'s `DUCKDNS_SUBDOMAINS` and `DUCKDNS_TOKEN` (your account's),
`caddy`'s `PORKBUN_API_KEY` (issued by Porkbun), a URL, or `_template`'s
`PORT`. Generate only what the comment tells you to generate.

An empty value is not an oversight in these files: a variable the compose file
guards with `${VAR:?}` is deliberately left blank, because `:?` fails on unset
*or empty* and a placeholder would satisfy it. Miss one and the stack refuses
to come up and names it. See `lessons-learned.md` §3.

```bash
cp .env.example .env && chmod 600 .env
```

The `chmod` is not optional politeness. Ubuntu's default `umask 002` writes
`0664`, so a `.env` holding a Porkbun API key or a Duplicati recovery
credential is readable by anyone with an account on the box unless you say
otherwise. `scripts/status.py` checks this hourly and names the files. To fix
a host that already drifted:

```bash
chmod 600 stacks/*/.env
```
