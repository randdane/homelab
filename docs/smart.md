# Disk health on `pve`

Since the 2026-09-20 cutover everything lives on `pve`'s two physical disks:
the 1 TB NVMe (root, VM 101 and CT 102 via `local-lvm`, so every stack, Headscale,
the local archives and Duplicati's settings DB) and the single 6 TB HDD
`tank`, which holds media. There is no redundancy on either yet (`tank`'s
mirror is #38). `docs/recovery.md` covers what to do after a drive dies;
this page is what tells you *before*.

Two halves, because they fail differently. Both run **on `pve`**, not in the
VM, because VM 101's disk is virtio and has no SMART data:

| | Runs | Says something when |
|---|---|---|
| `smartd` → `smart-alert.sh` | continuously, as a daemon | a drive reports a problem |
| `pve-smart.timer` → `smart-report.sh` on both `tank` mirror disks and the NVMe, by `/dev/disk/by-id` name | monthly, 1st at 09:00 (+≤1 h) | always — it is the heartbeat |

The second exists because the first is silent when healthy, and a dead alerter
is silent in exactly the same way. That is not hypothetical: `smartd` had been
installed, enabled and running on the first server since setup with the stock config,
mailing `root` on a box with no MTA. Every warning it ever produced went to a
dead address. Found 2026-08-28.

**On `pve` the paths differ from the rest of this page**, which was written
on an earlier Ubuntu host. The scripts live in `/usr/local/lib/homelab/scripts/`, not
`/opt/homelab/scripts/`. `pve` has no checkout; files are copied from the repo
with `scp`. `notify.py` reads `/etc/homelab/ntfy.env`, set with
`HOMELAB_NOTIFY_ENV`, as a drop-in for `smartmontools` and directly in
`pve-smart.service`. `ssh pve` is root, so drop the `sudo`.

**`homelab-smart.timer` is not installed in the VM.** `install-systemd.sh`
skips it under `systemd-detect-virt --vm`. It was replaced 2026-09-24,
before its first run inside VM 101 could report on a disk that has no SMART
data.

Install or refresh on `pve`:

```bash
scp scripts/smart-report.sh scripts/smart-alert.sh scripts/notify.py pve:/usr/local/lib/homelab/scripts/
scp scripts/systemd/pve-smart.service scripts/systemd/pve-smart.timer pve:/etc/systemd/system/
ssh pve 'systemd-analyze verify /etc/systemd/system/pve-smart.* && systemctl daemon-reload && systemctl enable --now pve-smart.timer'
```

First run on `pve`, 2026-09-24: both disks PASSED. `/dev/sda` had 0
reallocated, 0 pending and 0 uncorrectable sectors, at 772 h. `/dev/nvme0n1`
was 2% used with 100% spare, 0 media errors, at 13,117 h. The notification
arrived. An unreadable device makes the run exit 2 ("incomplete"), never a pass.

---

## The configuration

One line, in `/etc/smartd.conf`:

```
DEVICESCAN -d removable -n standby -m root -M exec /usr/local/lib/homelab/scripts/smart-alert.sh
```

- `-M exec PATH` — run this instead of sending mail. The `-m root` stays
  because `-M` directives *"only work in conjunction with the `-m` Directive
  and can not be used without it"*; the address is never used.
- `-n standby` — do not spin the disk up just to poll it.
- No monitoring directives, so `-a` is assumed:
  `-H -f -t -l error -l selftest -l selfteststs`, plus `-C 197 -U 198` on ATA.

**`smartd` re-reads this only on restart.** After editing:

```bash
sudo systemctl restart smartmontools
systemctl show smartmontools -p ActiveEnterTimestamp --value   # must be newer than the file
```

## What actually triggers an alert

Every default directive fires on a *problem*, never on a routine check. There
is no "checked, all fine" event, which is why the monthly heartbeat exists.

| Directive | Fires when |
|---|---|
| `-H` | the drive's own health self-assessment says FAILED |
| `-f` | a usage attribute drops below its threshold |
| `-l error` | new entries appear in the ATA error log |
| `-l selftest` | a self-test fails |
| `-C 197` | Current_Pending_Sector is non-zero |
| `-U 198` | Offline_Uncorrectable is non-zero |

## How often it repeats

Once per day, while a problem persists. It stops when the condition clears,
and a reappearance sends immediately.

This is `-M daily`, and it is in effect without being written anywhere.
`smartd.conf(5)` says `once` is the default *"unless state persistence (`-s`
option) is enabled"* — and Ubuntu builds `smartd` with a default savestates
directory, so persistence is on even though the command line is bare
(`/usr/sbin/smartd -n`). The tell is in the startup log:

```
Device: /dev/sda [SAT], state read from /var/lib/smartmontools/smartd.*.state
```

To change it, be explicit rather than relying on that default:

```bash
sudo sed -i 's|-M exec |-M once -M exec |' /etc/smartd.conf        # one message, ever
sudo sed -i 's|-M exec |-M diminishing -M exec |' /etc/smartd.conf # 1d, 2d, 4d … capped at 32d
sudo systemctl restart smartmontools
```

`once` is quietest and risks a single missed notification being the whole
warning. `daily` is the current behaviour: a dying drive keeps asking.

## Testing delivery

The real test, which exercises `smartd` itself. `-M test` sends one message at
startup **in addition to** normal warnings, so take it back out:

```bash
sudo sed -i 's|-M exec |-M test -M exec |' /etc/smartd.conf
sudo systemctl restart smartmontools
journalctl -u smartmontools -n 5 --no-pager          # then check the phone
sudo sed -i 's|-M test -M exec |-M exec |' /etc/smartd.conf
sudo systemctl restart smartmontools
```

Without `sudo`, you can still test everything downstream of `smartd` by
calling the script the way it does — `env -i` because `smartd`'s environment
is minimal and yours is not:

```bash
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  SMARTD_DEVICESTRING=/dev/sda SMARTD_FAILTYPE="TEST" \
  SMARTD_MESSAGE="ignore me" /opt/homelab/scripts/smart-alert.sh </dev/null
journalctl -t homelab-smart-alert -n 2 --no-pager
```

Correct output is **nothing**, exit 0, and `sent (200)` in the journal.

## Why the alert script looks paranoid

Two rules from `smartd.conf(5)` dictate its shape, and both are silent
failures if ignored:

> smartd will block until the executable PATH returns, so if your executable
> hangs, then smartd will also hang.

> The executable is not expected to write to STDOUT or STDERR. If it does,
> then this is interpreted as indicating a problem.

So every step is under `timeout`, all output goes to the journal via `logger`,
and it always exits 0. A broken notifier must not take the disk monitor down
with it. It calls `notify.py` with `python3`, not `uv`: `notify.py` is
standard-library only, and root's `PATH` under `smartd` has no `~/.local/bin`.

## The monthly heartbeat

`scripts/smart-report.sh`, run by `pve-smart.timer` on `pve`. Sends the health line
and the counters that move before a drive dies.

| Exit | Meaning | Unit result |
|---|---|---|
| 0 | healthy, notification sent | success |
| 1 | the drive says it is failing | **failed** → `OnFailure` → phone |
| 2 | SMART could not be read at all | success, via `SuccessExitStatus=2` |

Exit 2 is deliberate: "not checked" is not "passed", but it is not worth a
page either. It shows in the journal.

It runs as `r`, not root, so it needs the NOPASSWD rule in
`/etc/sudoers.d/audit-readonly`:

```
r ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/du
```

Without it, every month reports exit 2 and you learn nothing. Validate any
edit with `sudo visudo -cf <file>` **before** installing it.

Run it by hand any time: `./scripts/smart-report.sh`

## Reading the drive yourself

```bash
sudo smartctl -H -A /dev/sda
```

The three numbers that matter are `Reallocated_Sector_Ct` (5),
`Current_Pending_Sector` (197) and `Offline_Uncorrectable` (198). All zero is
healthy; any of them moving off zero, or climbing, is the drive telling you to
act. Record each reading in your site notes, so the next one has a
baseline.

## What this does not watch: a disk filling up

SMART reports on the *device*. A filesystem running out of space is not a
device fault — every counter stays zero and the drive reports `PASSED` right
up to the write that fails. So a healthy SMART report says nothing at all
about whether anything can still be written.

That gap is covered by `scripts/status.py`, which checks free space and free
inodes hourly on the filesystems holding Docker's images and the nightly
archives, and alerts below 10% of either. Inodes are checked separately
because they exhaust independently: a filesystem can be 3% full by bytes and
completely unwritable, and the symptom — `No space left on device` on a disk
with hundreds of gigabytes free — reads as a hardware fault until someone
thinks to run `df -i`.

At the time of writing the server sat at 75% used and 2% of inodes, so
neither fired. `/srv/media`
is on the same filesystem and grows deliberately, which is why the threshold
leaves ~91 GiB of runway rather than warning at the last moment.

## When nothing arrives

1. Is `smartd` running the current file?
   `stat -c %y /etc/smartd.conf` against its `ActiveEnterTimestamp`.
2. Does the script deliver? The `env -i` invocation above.
3. Are the credentials there? `notify.py` reads
   `/opt/homelab/stacks/ntfy/.env` (`/etc/homelab/ntfy.env` on `pve`); exit 2 from it means no usable
   credentials.
4. Did the heartbeat run? `ssh pve systemctl list-timers pve-smart.timer`.

**2026-09-25: moved to by-id names.** Adding the second `tank` disk moved the
old disk from `sda` to `sdb`, so `/dev/sda` meant the new disk. The unit now
names all three disks by `/dev/disk/by-id`. `smart-report.sh` resolves the
link before deciding a disk is NVMe. The new HPE MB6000GEQNK has no
`Current_Pending_Sector` or `Offline_Uncorrectable` attribute, so its line
shows `pending=? uncorrectable=?`. That means "not reported", not a fault;
its device statistics page reports 0 uncorrectable errors. First run after the
change: all three healthy, notification sent.

**2026-10-03: a second disk, mirrored.** A Seagate ST6000NM0024 (serial
S4D0CADY) passed its extended self-test at 9 power-on hours and was attached to
`tank`, which is now a two-way mirror. The unit names it alongside the old
disk and the NVMe.

**2026-09-26: the HPE disk is going back.** It passed its extended
self-test, but it had 87,530 power-on hours, too old to pair with a
780-hour mirror partner. It never joined `tank` and was removed from
`pve-smart.service` before being pulled. The unit now names the `tank` disk
and the NVMe.
