# Uptime Kuma

**What:** Probes each service on a schedule and notifies when one stops
answering.
**Why I care:** Nothing else here watches. `restart: unless-stopped` will
happily crash-loop a container all night, and `docker ps` calls it `Up`. The
first report of an outage was going to be family texting about Jellyfin.
**URL:** https://kuma.${PUBLIC_DOMAIN} — tailnet and LAN only, `remote_ip`-gated
in Caddy. `http://<tailnet-ip>:3004` still answers and is the break-glass path:
this is the monitor that reports Caddy being down, so reaching it *through*
Caddy cannot be the only way in.

## First-run setup, which is not automated

Uptime Kuma has no config file — monitors and notifications live in its
SQLite database. The UI is the normal way to create them; see "Adding a
monitor by SQL" below for the other way. On first visit it asks you to
create the admin account. **Do that promptly:** the setup page is open to
whoever loads it first, the same trap as Authentik's initial-setup flow.

Then add the notification channel *before* the monitors, so it can be attached
as each one is created — but leave **Default enabled** off until the monitors
exist, because in 2.5.3 a default notification breaks monitor creation. See
"Reading Kuma's ntfy notifier, and three traps in 2.5.3" below, then turn it
on afterwards with **Apply on all existing monitors**.

- Type **Ntfy**, friendly name `ntfy homelab-alerts`, server `http://ntfy:80`
  (by container name over `edge`, like every internal monitor), topic
  `homelab-alerts`, priority 4
- Authentication **Access Token**: the `homelab` token, the same one
  `scripts/notify.py` reads from `stacks/ntfy/.env`.

### Reading Kuma's ntfy notifier, and three traps in 2.5.3

**A successful send logs nothing.** `server/notification-providers/ntfy.js`
only produces a log line by throwing — `throwGeneralAxiosError` — so silence
in `docker logs uptime-kuma` means the notification went out. Measured
2026-09-12: three `401 unauthorized` lines while the access token was wrong,
then not one line after it was fixed, while the phone received every message.
Do not read "no log line" as "did not send"; check the phone, or
`dumpsys usagestats | grep channelId=ntfy` on it.

`notification_sent_history` stays at **0 rows** regardless, so the database
never answers this either. The phone is the only witness.

**Adding a monitor fails while a default notification exists.** With
`ntfy homelab-alerts` set to *Default enabled* (`isDefault`), Save on a new
monitor returns an error the UI renders as a foreign-key complaint, while the
log says `Error adding Monitor: undefined User ID: 1`. The `monitor` row is
written and committed, but the notifier attach and the probe start never
happen: the result is an inert row with zero heartbeats, zero notifiers and
zero `stat_*` rows. Six accumulated from six retries on 2026-09-12, ids 14-19.
The database itself was clean throughout — `PRAGMA foreign_key_check` empty,
`quick_check ok`, no orphaned `monitor_notification` rows.

So a failed Save is **not** a no-op. Check for debris before retrying:

```bash
ssh homelab "docker exec uptime-kuma sqlite3 /app/data/kuma.db \"SELECT m.id,m.name,(SELECT COUNT(*) FROM heartbeat h WHERE h.monitor_id=m.id) beats FROM monitor m WHERE (SELECT COUNT(*) FROM heartbeat h WHERE h.monitor_id=m.id)=0;\""
```

**The strays cannot be deleted from the UI.** The sidebar is rendered from
Kuma's in-memory monitor registry, and the failed Save never registered
them — so they exist only as database rows and appear nowhere in the
interface, under any filter. SQL with the container stopped is the only route:

```bash
ssh homelab 'docker exec uptime-kuma sqlite3 /app/data/kuma.db ".backup /tmp/kb.db" \
  && docker cp uptime-kuma:/tmp/kb.db ~/.local/state/homelab/kuma-pre-straydelete-$(date +%Y%m%dT%H%M%S).db \
  && docker exec uptime-kuma rm -f /tmp/kb.db'
ssh homelab 'docker stop uptime-kuma'
ssh homelab 'docker run --rm -v uptime-kuma_data:/app/data --entrypoint sqlite3 \
  louislam/uptime-kuma:2.5.3 /app/data/kuma.db "
PRAGMA foreign_keys=ON;
DELETE FROM monitor_notification WHERE monitor_id IN (<ids>);
DELETE FROM heartbeat WHERE monitor_id IN (<ids>);
DELETE FROM stat_daily WHERE monitor_id IN (<ids>);
DELETE FROM stat_hourly WHERE monitor_id IN (<ids>);
DELETE FROM stat_minutely WHERE monitor_id IN (<ids>);
DELETE FROM monitor_tag WHERE monitor_id IN (<ids>);
DELETE FROM monitor WHERE id IN (<ids>);
PRAGMA foreign_key_check;"'
ssh homelab 'docker start uptime-kuma'
```

Back up first — `data: precious` — and take it with `.backup` while Kuma is
still running, which is the consistent way to copy a live SQLite database. The
stopped container cannot be `docker exec`'d, hence the one-off `docker run`
against the same image, which carries `sqlite3`. Done 2026-09-12 for ids
14-19; `foreign_key_check` clean, `quick_check ok`, 11 monitors left, and all
11 beat within a minute of the restart.

Deleting rows while Kuma runs is the thing to avoid: it holds the monitor list
in memory, so a row removed underneath it stays live until a restart.

**A manual Pause or Resume sends no notification.** Only a probe-detected
state change does. Pausing a monitor to test a notifier proves nothing and
leaves the service unwatched; the log records `Pause Monitor: <id>` and
nothing else.

## What to monitor, and from where

This container joins `edge`, so it reaches proxied stacks by container name.
The live list -- names, targets, state -- is Kuma's database, not this file:

```bash
uv run scripts/check_monitors.py --list
```

A copy here drifted (two monitors missing, retries never recorded) and a copy
that agrees with the database proves nothing -- see the docstring of
`scripts/check_monitors.py`. What the database cannot say is *why*:

- **Probe both directions.** An internal target (container name, or the LXC's
  LAN address) catches the app dying; a public URL catches Caddy, DNS, certs
  and the WAN. Jellyfin has both, plus the dynamic-DNS name, which is the path
  that breaks.
- **Monitor what the alerts depend on.** Authentik (login broken while the app
  looks fine), CrowdSec (the bouncer going deaf), ntfy (the path every other
  alert takes), Headscale (the tailnet control plane).
- **Use the health path when there is one**, the root page when there is not
  (Mealie). Homepage needs a `Host` header -- see below.

Every HTTP monitor uses **Retries 2** at 60 s, so a single failure while a
stack is still starting after a boot does not page. Mealie, KaraKeep and ntfy
were at 0 until 2026-09-30, and Mealie paged 24 s after `homelab` booted.

### The phone's push monitor

`Vhosts from phone` is a **push** monitor, not an HTTP one. Kuma cannot
reach the gated vhosts itself -- its container sits on the `edge` bridge,
whose address the allowlist correctly refuses -- so the phone probes them and
reports in.

| Setting | Value | Why |
|---|---|---|
| type | push | the probe runs on the phone, not here |
| interval | 172800 (2 days) | tolerates exactly one missed day; alerts on two |
| maxretries | 0 | Kuma applies retries to pushed failures, which would delay a notification the phone has already decided to send |

The push URL is `http://<tailnet-ip>:3004/api/push/<PUSH_TOKEN>` -- the published
port directly, **never** `kuma.{$PUBLIC_DOMAIN}`. That vhost is itself gated,
so routing the alarm through it means a vhost outage suppresses its own alert.

`<PUSH_TOKEN>` lives in Kuma's database and in Tasker, and nowhere else. It is
also kept in `~/.local/state/homelab/kuma-push-token`, mode 0600, so it can be
re-read without dumping the database. It is a capability URL: anyone holding
it can forge an "all clear".

**That copy did not survive the migration**, and was not missed until it was
wanted. It was restored on `homelab` on 2026-09-22 from `kuma.db`:

```bash
docker exec uptime-kuma sqlite3 /app/data/kuma.db \
  'SELECT push_token FROM monitor WHERE name LIKE "%Vhosts from phone%";' \
  > ~/.local/state/homelab/kuma-push-token && chmod 600 ~/.local/state/homelab/kuma-push-token
```

A convenience copy of a secret is the kind of thing a host move drops
silently, because nothing depends on it until an incident does.

The Tasker contract, which is the part that is not in this repo:

| Setting | Value |
|---|---|
| schedule | 07:00 daily, retried hourly until one run succeeds |
| guard | GET `http://<tailnet-ip>:3004/`, timeout 5 s -- if it fails, push nothing |
| per-probe timeout | 10 s |
| probes | every host in `PHONE_PROBES`, by their real names through real DNS |
| pass | any HTTP status received |
| fail | transport error or timeout |
| retry before pushing `down` | once, after 60 s |
| detection bound | <= ~24 h, given one successful run |

"Once per day" means one *successful* check per day. A single 07:00 attempt
would be lost whenever the phone happened to be off-tailnet at that moment,
and two unlucky days running would trip the two-day heartbeat over nothing.

The guard and the probes use different addressing deliberately: the guard uses
the tailnet address and a published port, bypassing Caddy, while the probes
resolve the public names through real DNS and go through Caddy. That
difference is what makes "tunnel up but vhost down" distinguishable from
"tunnel down". A failed guard strictly means "tailnet **or Kuma**
unreachable" -- accepted rather than engineered away, since if Kuma is
unreachable nothing can be reported anyway.

`scripts/check_vhosts.py` holds `PHONE_PROBES` and fails when it no longer
matches the gated set in the Caddyfile. Tasker cannot read this repo, so that
comparison is the only thing keeping the two in step: when it fires, update
the Tasker task and `PHONE_PROBES` together -- or, for a host deliberately not
probed from the phone (`dozzle.`, `cup.`), record it with a reason in
`NOT_PHONE_PROBED`.

**Detection verified end to end, 2026-09-09.** Not just that it reports `up`
correctly -- that proves nothing about whether it can notice a failure:

| Time (UTC) | Event |
|---|---|
| 00:51:41 | run with probe 1 pointed at a name that does not resolve |
| 00:52:41 | `status=0`, msg `,authentik.example.com` -- 60 s later, so the retry ran and the second pass failed too |
| 00:53:37 | URL restored, `status=1`, msg `OK` |

The notifications for both transitions arrived on the phone -- through Home Assistant, which was Kuma's notifier until the ntfy move on 2026-09-12 -- confirming `maxretries=0` notifies immediately rather than waiting out a retry count.

**The schedule fired on its own, 2026-09-10.** Those runs were triggered by
hand, which proves detection and nothing about the Tasker profile. The first
unattended morning:

| Time (CDT) | Event |
|---|---|
| 06:10:45 | `homelab-vhost-check.service` on the server: 7 hosts served, 4 gated and all refusing the bridge, 3 declared public |
| 06:59:59 | phone push, `status=1`, msg `OK` |

Both checks have now run on their own schedules, so the install is complete.

**That last hop could only be confirmed by asking a person.** Kuma's `notification_sent_history` table was empty for this monitor and the container logged nothing, so nothing on disk records whether an alert reached a phone. A notifier that has quietly stopped delivering is indistinguishable from a healthy system that has nothing to say -- the same shape as the outage this check exists for. Answered by the morning digest watchdog below.

The failing name used was `nosuch.example.com`, which only became a valid
test after the DNS wildcard was removed the same day. Before that it resolved
to Porkbun's parking host and answered 302, so the probe correctly reported UP
and the test measured nothing.

## The morning digest watchdog

`scripts/digest.py` sends `homelab digest YYYY-MM-DD: OK` (or `: N problems`)
at 07:15 Chicago time, through ntfy, the same path every alert uses. Two
Tasker profiles on the phone turn "it did not arrive" into an alarm that
needs neither `homelab`, ntfy nor the phone's subscription to be working. This is
the answer to the unobservable last hop above: a daily notification that proves the last
hop, and notices when it stops.

| Profile | Trigger | Task |
|---|---|---|
| `Digest received` | Event → UI → Notification, Owner Application ntfy, every other filter empty | `Digest record`: one JavaScriptlet |
| `Digest missing` | Time → 09:00 | `Digest check`: one JavaScriptlet |
| — | started by `Digest check` | `Digest alarm`: one Notify, title `no homelab digest today` |

The profiles are imported from XML, kept in `stacks/uptime-kuma/tasker/`.
To rebuild them on a phone, copy those files to `/sdcard/Tasker/profiles/` and
`/sdcard/Tasker/tasks/`, then import `Digest_alarm.tsk.xml` first (Tasks tab,
long-press → Import Task), then both `.prf.xml` files (Profiles tab,
long-press → Import Profile), and save with ✓.

**The logic is JavaScript, not Tasker conditions, on purpose.** Every date
comparison and regex runs inside the JavaScriptlet:

- `Digest record` builds today's `YYYY-MM-DD` from the phone's clock, reads
  only `%evtprm2` -- the title of the notification that fired the profile --
  and sets the global `%DigestDate` only for `^homelab digest <today>: `.
  Not `%NTITLE`, which is the last notification shown rather than the one that
  fired, and not the body: a failure alert quoting a digest title in its text
  would otherwise count. Tasker's variables guide recommends `%evtprm2` for
  Notification events for the same reason.
- `Digest check` starts `Digest alarm` with `performTask` when `%DigestDate`
  is not today.

That keeps it off the parts of Tasker's import format that could not be
checked against a real export: condition operator codes and the
Parse/Format DateTime layout. The notification filter is only the owner app;
the title filter is left to the script, which is stricter than a wildcard.
`scripts/test_tasker_digest.py` runs both JavaScriptlets from the committed
XML under node: today's digest, yesterday's, an unrelated alert, a digest
title in the body, a stale `%NTITLE`, and the alarm logic. It is skipped where
node is not installed.

**Tasker's import is silent about most mistakes.** The first import of
`Digest_received.prf.xml` failed with only a toast; the reason was in
`adb logcat`: `unpack object: expected object type App, seen Str`. The
notification event's `arg0` is an `App` element, not a string. When an import
fails, read the log before guessing.

**After replacing a profile, leave Tasker before testing.** Deleting
`Digest received` and importing the fixed one, then saving with ✓, was not
enough: the next digest logged
`handleSystemEvent: unknown profile ID 19 from event pid 19, code461` and no
script ran, because the event monitor still held the deleted profile. Backing
out to the home screen reloaded it, and the next digest was recorded.

Requirements on the phone:

- Tasker has notification access. Granted over adb on 2026-09-10 with
  `cmd notification allow_listener net.dinglisch.android.taskerm/net.dinglisch.android.taskerm.NotificationListenerService`;
  confirm with `dumpsys notification`, not the `enabled_notification_listeners`
  setting, which did not show it.
- Tasker is exempt from battery optimisation, so the 09:00 profile fires in
  Doze. It already was: `dumpsys deviceidle whitelist` lists it.

What the digest does **not** test: Kuma's own ntfy notification. And if these
profiles are disabled or lose permission, nothing tells you. Seeing the digest
every morning is the only mitigation.

The `OnFailure=` wiring used to be on that list. It came off on 2026-09-12,
when `homelab-monitor-check.service` failed for real twice over a stale-red
monitor and both alerts reached the phone at high priority unattended — see
`stacks/ntfy/README.md`.

**First delivery, 2026-09-10.** Sent by hand from the server:
`sent (200): homelab digest 2026-09-10: OK`, body
`last backup 8h ago · 10 monitors up · 0 failed units`. It arrived on the
phone on the default channel. `homelab-digest.timer` installed by
`install-systemd.sh --system`, next elapse 2026-09-11 07:15 CDT, service as
`User=r` from `/opt/homelab`, `TimeoutStartSec=300`; no user-mode copy.

**Acceptance run, 2026-09-10.** For each alarm test, `Digest missing` was
moved to a minute or two ahead. It must be back at 09:00 once testing ends.
Times are the phone's clock, CDT.

| Test | What was sent | Result |
|---|---|---|
| today's digest is recorded | `homelab digest 2026-09-10: OK` from the server | pass: Tasker ran the JavaScriptlet, `%DigestDate` = `2026-09-10` |
| a failure alert does not count | `%DigestDate` cleared, then `homelab-backup.service failed` | pass: alert arrived, alarm fired 13:44:01 |
| yesterday's digest does not count | `homelab digest 2026-09-09: OK` | pass: digest arrived 13:46:00, alarm fired 13:48:01 |
| unreadable backup config is reported | `chmod 000 stacks/backup/compose.yaml`, `--dry-run`, mode 664 restored | pass: `backup: compose config failed to parse` and `backup: config unreadable, archive age unknown`, exit 0, `git status` clean |
| unreadable Kuma is reported | `docker stop uptime-kuma`, `--dry-run`, started again | pass: `kuma: could not read monitors`, plus `uptime-kuma: down` from `problems_for`, exit 0; Kuma healthy 19 s after start |
| phone offline and locked | `Digest missing` at 14:00, airplane mode on, screen locked, `dumpsys battery unplug` | pass: alarm fired 14:00:01 with `airplane_mode_on=1`, screen dozing, lock screen up |

**What the offline test did not prove: full deep Doze.** A phone on USB does
not enter Doze because it is charging, so charging was masked with
`dumpsys battery unplug` and `dumpsys deviceidle force-idle` was tried. It
stopped at `INACTIVE`, and stepping reached only `QUICK_DOZE_DELAY`, which is
where the phone was when the alarm fired. The Doze case rests on Tasker's
battery-optimisation exemption rather than on a measurement. Both changes
were reverted afterwards (`deviceidle unforce`, `battery reset`); deep state
read `ACTIVE` and USB power was reported again.

The alarm tests watched for the notification over adb
(`dumpsys notification --noredact`) and refused to start if an earlier alarm
was still showing, so a stale notification could not pass for a new one.

**Re-test after the `%evtprm2` fix, 2026-09-10.** Review found that the first
version of `Digest record` also matched `%NTITLE` and the notification body.
The acceptance run above had not caught it, because its unrelated alert had an
unrelated body too. After the fix and a re-import:

| Test | What was sent | Result |
|---|---|---|
| today's digest is still recorded | `homelab digest 2026-09-10: OK` | pass: script ran 16:30:17, `%DigestDate` = `2026-09-10` -- so `%evtprm2` is the Home Assistant title on this phone |
| a digest title in the body does not count | `%DigestDate` cleared; title `homelab-backup.service failed`, body `homelab digest 2026-09-10: OK` | pass: script ran 16:34:54, `%DigestDate` stayed empty, alarm fired 16:36:01 |

The first row reads "the Home Assistant title" because that was the sending
app at the time. `%evtprm2` is the firing notification's own title whatever
posts it: on 2026-09-12, with the profile switched to ntfy, the same mechanism
matched ntfy titles and correctly ignored three failure alerts in a row. The
measurement stands; the app in it does not generalise.

**The probe URL and the label are two separate strings.** Because the four
hosts are unrolled rather than looped, each host is an `HTTP Request` holding
the URL plus a `Variable Set` holding the name that reaches the alert. Editing
one without the other makes the alert name the wrong host -- exactly what the
run above shows, where the URL was `nosuch.` and the message still read
`authentik.`. Harmless in a test, misleading in a real outage. Change both
together.

**On home wifi this tests less than it appears.** The names resolve to
`homelab`'s LAN address and the phone reaches it directly over wifi, exercising
neither the tunnel nor the subnet route. Only when the phone is away is this a
genuine remote-path test.

To prove Tasker actually *detects*, rather than only that Kuma delivers:
point one probe at `nosuch.{$PUBLIC_DOMAIN}` and run the task by hand. An
unmatched name reaches the wildcard's `handle { abort }` -- the same mechanism
a `remote_ip` rejection uses -- so the phone sees an identical transport
abort. A manual `down`/`up` push proves the notification path and nothing
about whether the phone would ever send one.

**There is deliberately no monitor for `authentik.example.com`, and adding
one is a trap.** This container sits on the `edge` bridge, so its requests
reach Caddy from `10.201.7.x`. That vhost matches on host AND `remote_ip`
together, the bridge is not in the allowlist, and `handle { abort }` closes
the connection -- which Kuma reports as down while authentik is perfectly
healthy. Measured 2026-09-06: `remote_ip: 10.201.7.3`, `status: 0`,
`bytes_read: 0`, alongside a 200 from `authentik-server:9000` in the same
container.

The obvious repair -- adding `10.201.7.0/24` to the allowlist -- was considered
and rejected. It rests a privacy gate on a NAT
side-effect and admits anything that ever arrives over that bridge.

Nothing is lost by leaving it out. `jellyfin.example.com` is served by the
same wildcard block and the same wildcard certificate, and its handler has no
`remote_ip` gate, so the Jellyfin (public) monitor already covers Caddy, DNS
and certificate expiry for every name in that block. The internal monitor
above covers whether authentik itself is alive. Between them there is no gap.

The same reasoning rules out a monitor for `kuma.example.com`, for a second
reason on top: one that goes red when Kuma cannot reach itself reports nothing
Kuma could have sent. `homepage.` and `vikunja.` are allowlisted vhosts too,
and would fail identically.

**Homepage is monitored by container name with an explicit `Host` header**,
`{"Host": "homepage.<PUBLIC_DOMAIN>"}`, because `HOMEPAGE_ALLOWED_HOSTS` lists
only the published host:port forms and the public vhost -- `http://homepage:3000`
answers 400 without it and 200 with it. That keeps the probe container-to-
container on `edge`, so it tests Homepage rather than Caddy, and needs no
allowlist widened anywhere.

**Three production stacks deliberately have no monitor here**, and
`scripts/check_monitors.py` holds the list with a reason each: `cup` (its API
is what `check_updates.py` reads, on its own timer), `duplicati`
(`run_backups.sh` fails the backup unit when it cannot push offsite),
`backup`, `caddy`, `dozzle` and `uptime-kuma` itself. That check now also
fails when a production stack has *no* monitor at all -- the gap neither the
reachability nor the stale-red check could see, since both start from the
monitor list.

**Every internal monitor uses a container name, never the host's tailnet IP.**
A container on `edge` cannot reach the host's tailnet address at all: UFW is default-deny
inbound with only 22/tcp open, so a probe from the bridge to a host port dies
in `INPUT` and the monitor just times out. Vikunja was addressed that way
until 2026-08-27, left over from when `life-queue` did not join `edge`. It
joins now, so Caddy can proxy it, and the monitor goes by name like the rest.
That workaround outlived its reason and spent eleven hours reporting a
healthy Vikunja as down -- `docs/lessons-learned.md` §25.

**Jellyfin is the one exception.** Since the move to `pve` it is not a container at all but the LXC at `JELLYFIN_HOST`, so the monitor uses that address. The history: on 2026-09-15 it left `edge` for its
own network (`docs/host-setup.md` 13), so `jellyfin` no longer resolves from
here, and joining that network would let Jellyfin reach Kuma. The monitor uses
the LAN address and the published port instead. That is not the path the
paragraph above warns about: a published port on the LAN address is DNAT'd to
the container in `PREROUTING` and never reaches UFW's `INPUT` -- measured 200
from this container before the change. The tailnet address remains unusable.

Verify the name from inside this container before trusting it, because a name
that does not resolve produces a monitor that is red forever and gets muted
rather than fixed:

    docker exec uptime-kuma curl -sf -o /dev/null -w '%{http_code}\n' \
      http://life-queue-app:3456/api/v1/info

## Adding a monitor by SQL

The UI is fine for one monitor. For several, or from a shell, the database can
be edited directly — but **only with the server stopped**. Kuma holds monitor
state in memory and rewrites rows on shutdown, so an insert into a live
database is liable to be overwritten with no error.

Both public monitors above were added this way on 2026-08-31:

    docker stop uptime-kuma
    # back up first -- there is no undo
    docker run --rm -v uptime-kuma_data:/app/data -v "$PWD":/bk \
      --entrypoint sh louislam/uptime-kuma:2.5.3 \
      -c 'cp /app/data/kuma.db /bk/kuma.db.bak'
    docker run --rm -i -v uptime-kuma_data:/app/data -v /tmp/m.sql:/sql.sql:ro \
      --entrypoint sqlite3 louislam/uptime-kuma:2.5.3 /app/data/kuma.db '.read /sql.sql'
    docker start uptime-kuma

**Clone an existing monitor rather than writing an INSERT.** The `monitor`
table has over a hundred columns, most of them protocol-specific defaults that
have nothing to do with HTTP; hand-authoring a row means getting all of them
right. Copying a row that already works means getting two fields right:

```sql
CREATE TEMP TABLE t AS SELECT * FROM monitor WHERE id=2;  -- a working HTTPS monitor
UPDATE t SET id=NULL, name='...', url='...', created_date=DATETIME('now');
INSERT INTO monitor SELECT * FROM t;
INSERT INTO monitor_notification (monitor_id, notification_id)
  VALUES (last_insert_rowid(),
          (SELECT id FROM notification WHERE name='ntfy homelab-alerts'));
DROP TABLE t;
```

**The `monitor_notification` row is not optional.** Without it the monitor
probes correctly, goes red correctly, and tells nobody — which looks exactly
like a monitor that is working.

Two traps worth knowing: `docker run` **needs `-i`** or it never attaches
stdin, and a heredoc of SQL vanishes silently with a `0` exit and no output;
and SQLite reads `"double quotes"` as an *identifier* first, falling back to a
string literal only if no column matches, so string values must be in single
quotes.

Finish by confirming Kuma actually loaded them, rather than trusting the
insert — `check_monitors.py` probes every target from inside the container:

    uv run scripts/check_monitors.py --list

## Checking the monitors themselves

Kuma watches the services. `scripts/check_monitors.py` watches Kuma, because
a monitor is the only configuration on this host that is not a file in this
repo -- it lives in the SQLite database, made by hand in a web UI, and never
appears in a diff.

    uv run scripts/check_monitors.py            # probe + staleness, exit 1 on trouble
    uv run scripts/check_monitors.py --list     # what is configured right now

The behaviour is covered by `scripts/test_check_monitors.py`, which runs in
`./scripts/check.sh`. There was a `--self-test` flag duplicating those cases
inside the script; it asserted nothing the pytest suite did not already assert
more thoroughly, so it was two copies of one suite waiting to disagree.

**Where:** on `homelab`. It shells into the `uptime-kuma` container, so it does
nothing useful from a laptop -- reachability is a property of where the probe
runs, which is the whole lesson of the incident it exists for.

**When:**

| Moment | Why |
|---|---|
| Hourly, `homelab-monitor-check.timer` | the unattended case; installed by `scripts/install-systemd.sh` |
| **After adding or editing any monitor** | the one that matters -- catches a bad URL in a second, before it can be mistaken for an outage |
| After changing a stack's networks or container name | what silently invalidated the Vikunja monitor |
| When a single monitor is red and you doubt it | `--list` plus the probe answers "service or check?" directly |

It makes two different arguments, which fail at different times:

- **reachable** -- probes every monitor's target *from inside the container*.
  A transport failure is the alert; a 4xx/5xx is not, because that proves
  something answered and judging the response is Kuma's job.
- **suspect** -- a monitor red over an hour while a sibling is green. That
  shape is evidence about the check, not the service. When *everything* is
  red it stays quiet: that is an outage, and calling it a config error would
  be the wrong alert at the worst moment.

Exit codes follow `check_derp.py`: 0 clean, 1 broken or suspect, 2 could not
check. The unit sets `SuccessExitStatus=2`, so a run during the nightly
backup -- when containers are deliberately down -- does not cry wolf.

**It does not diff the database against a written copy.** That check would
have passed for all eleven hours of the incident: the README and the database
both named the tailnet address and port, agreeing perfectly, and both were wrong.
Comparing two copies tests transcription, not truth. Probing tests truth.

An internal-only check goes green while the public path is broken, which is
the outage family actually experiences. A public-only check cannot tell you
whether the application or the proxy failed. Keeping both is the point.

**The "public" monitor never leaves the house.** It runs on `homelab`, resolves
the public IP, and comes back through the Dream Machine's hairpin NAT. If the
ISP drops or the WAN IP changes before DuckDNS catches up, that monitor stays
green while nobody outside can reach Jellyfin. It still catches Caddy dying, a
failed renewal and broken DNS -- it is just not the proof of external
reachability it looks like. A real one needs a vantage point outside the house.

**Do not monitor the DERP relay with a plain HTTP check.** A degraded relay
answers normally — that is what `scripts/check_derp.py` and its timer exist
for, and that timer now notifies on failure by itself.

## Notes

**SQLite, so `stop-during-backup` is set.** A mid-write copy of the history
database restores as corrupt. The volume is registered in `stacks/backup`.

**Port 3004, not the upstream default 3001** — Homepage already holds 3001.
See `docs/ports.md`.

**Not exposed through Caddy, deliberately.** It knows the shape of the whole
house and every service worth attacking. It is reached over Tailscale.

**Check intervals cost more than they look on this host.** Every monitor is a
timer plus a database write on a 5400rpm disk. 60s is plenty; 20s across a
dozen monitors is not free here.
