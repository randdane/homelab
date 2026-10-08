# ntfy

**What:** Self-hosted push notifications. Every homelab alert reaches the
phone through here, and the phone forwards app notifications back through
here to Home Assistant.
**Why I care:** The old path was Home Assistant → Google push, a hop nobody
here could observe.
**URL:** `http://<HOMELAB_HOST>:2586`. Use this one, from everywhere — it works
on the LAN, over Teleport, and over the tailnet subnet route.
The tailnet address on :2586 still answers but works only while the tailnet does,
which is the wrong dependency for an alert path (see below).
Deliberately no vhost: the alert path must not depend on Caddy, DNS or
certificates.

## First-run setup, which is not automated

Auth is deny-all, so nothing works until users exist. Each `user add` prompts
for a password; nothing logs in with it, but keep it in **Bitwarden** — the
hosted one. Vaultwarden was never deployed and was rejected 2026-10-08, so
an earlier instruction here to "keep it in Vaultwarden" sent
someone looking in a password manager that does not exist. The same applies
to the three tokens below: save each one as it is printed. A token that was
never recorded is not recoverable — mint a replacement with
`ntfy token add <user>` and leave the old one in place if something is still
using it.

```bash
docker exec -it ntfy ntfy user add homelab
docker exec -it ntfy ntfy user add hass
docker exec -it ntfy ntfy user add phone

docker exec ntfy ntfy access homelab homelab-alerts write-only
docker exec ntfy ntfy access hass    homelab-alerts write-only
docker exec ntfy ntfy access hass    phone-forward  read-only
docker exec ntfy ntfy access phone   homelab-alerts read-only
docker exec ntfy ntfy access phone   phone-forward  write-only

docker exec ntfy ntfy token add homelab   # -> stacks/ntfy/.env NTFY_TOKEN, and Kuma
docker exec ntfy ntfy token add hass      # -> HA's ntfy integration only
docker exec ntfy ntfy token add phone     # -> ntfy app and Tasker only
# Optional, one per extra machine that should alert: a labelled token for the
# same write-only user, so it can be revoked alone. It goes in that machine's
# own stacks/ntfy/.env, with NTFY_URL set to the server rather than 127.0.0.1.
docker exec ntfy ntfy token add --label laptop homelab
```

The access table is the audit. It must match this, exactly:

| User | `homelab-alerts` | `phone-forward` |
|---|---|---|
| `homelab` | write | — |
| `hass` | write | read |
| `phone` | read | write |

```bash
docker exec ntfy ntfy access
```

## Checking the gate is shut

From `homelab`. The first must be 403, or deny-all is not in force:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -d test http://127.0.0.1:2586/homelab-alerts
```

## Deployed

**2026-09-11, on the first server.** Users `homelab`, `hass`, `phone`, with the access
table above. Gate checked from the server: anonymous publish 403, `homelab`
publish 200, `homelab` read 403; `uptime-kuma` reaches `http://ntfy:80/v1/health`
with 200. Kuma monitor `ntfy` added, reporting through the HA webhook until
Kuma itself moves to ntfy. Extra checks: `homelab` to `phone-forward` 403,
invalid token 401.

**The first `.env` was world-readable.** It was created with `cp -n` and no
`chmod 600`, and that host's umask was 0002, so it held the `homelab` token at
0664 until `homelab-status.service` failed on it at 15:03 CDT the same day.
Fixed with `chmod 600`. Create it with `cp -n .env.example .env && chmod 600
.env`, as `docs/host-setup.md` says for every stack.

## Phone

**Subscribed 2026-09-11.** `io.heckel.ntfy/.ui.MainActivity` (app 1.25.2),
battery-exempt — `dumpsys deviceidle whitelist` holds
`user,io.heckel.ntfy,10509` — and standby bucket 5. Daytime test arrived in
0.8 s with the title exactly as published and nothing prepended.

**Overnight gate, 2026-09-12.** Published 02:30:00 CDT from the server
(`ntfy-gate.service`, message id `EQ6dvk6jbK5b`); posted on the locked, idle
phone at 02:30:01, 1 s later. Pass. Doze does not delay delivery.

**Re-subscribed 2026-09-22, and the old subscription had been dead for days.**
It pointed at the server's tailnet address and `/homelab` — a tailnet address, and a topic
that does not exist. The phone's Tailscale had been unreliable since its
2026-09-12 update and offline outright since 2026-09-21, so nothing had
arrived in that window. Now `http://<HOMELAB_HOST>:2586/homelab-alerts` as user
`phone`, with `NTFY_BASE_URL` moved to match. Verified end to end the same day:
published from `homelab` via `notify.py`, arrived on the phone.

**The publisher could not have detected this.** `notify.py` posts to
`http://127.0.0.1:2586` over loopback and got `sent (200)` for every alert the
whole time — the server accepted and cached each one correctly. Only the
receiving side was broken. A 200 from ntfy proves the message was stored, not
that anyone got it, and there is nothing on the sending host that can tell the
difference. This is the case for task #52, an external dead-man's switch: a
check that lives inside the system can confirm acceptance and never delivery.

Add a third trap to the two below: **a subscription against an address that
has stopped resolving looks exactly like a quiet system.** Re-check the topic
URLs, not just that the app is running, whenever the network layout changes.

Two traps cost most of the time spent getting there, and neither looks like a
delivery failure:

- **A muted subscription stores messages silently.** The app has an app-wide
  mute and a per-topic mute, both drawn as a crossed-out bell. While either
  is set the message arrives, is listed in the app, and never reaches the
  notification shade. Android is clean in this state — channel enabled, DND
  off, battery exempt — so every check passes while nothing is posted.
- **Polling for the result while the app is foreground reads a false
  negative.** ntfy cancels its own notification when the topic is on screen,
  so a check run seconds late finds `cancelled` and reports non-delivery.
  Test from the home screen (`input keyevent KEYCODE_HOME`) and poll at 1 s.

The measurement that counts for delivery is `dumpsys usagestats`:
`type=NOTIFICATION_INTERRUPTION package=io.heckel.ntfy channelId=ntfy`, whose
timestamp is when the shade actually got it. The notification's own `when=`
field is the server's message time, not the post time, so it cannot prove
delivery latency — it would read on time even if the post were late.

## Cutover

**2026-09-12.** `notify.py` publishes here; `homelab-failure-notify@` at
`TimeoutStartSec=300`; SMART scripts at `timeout 240`; `Digest received`
owner app ntfy. Hand-sent digest recorded at 06:29:22;
`homelab-failure-notify@` test delivered at 09:48:23 on `channel=ntfy-high`
with `importance=4`; retry test with ntfy stopped 40 s delivered after 50.5 s
(five `could not reach ntfy` lines, then `sent (200)`). First unattended
digest 2026-09-12 07:15:12, recorded by `Digest record`, no 09:00 alarm.

**The `OnFailure` path proved itself unasked, which is better evidence than
the test.** `homelab-monitor-check.service` failed for real at 08:09:26 and
09:19:34 — a Kuma monitor stale-red, nothing to do with ntfy — and both
alerts arrived on the phone at high priority with no one watching. The
staged test only shows the path works when invoked by hand.

**Installing the units needs the pull first, and a stale run looks
identical to a good one.** `install-systemd.sh --system` renders from the
checkout, so running it before `git pull` on `homelab` re-installs the old
templates and reports success. It happened here: the first run left
`TimeoutStartSec=90` and `Description=Report %i failing to Home Assistant`
in place. Verify after every run, which needs no sudo:

```bash
ssh homelab 'systemctl show homelab-failure-notify@x.service -p TimeoutStartUSec'
```

`TimeoutStartUSec=5min` is the cutover value; `1min 30s` means the units are
pre-ntfy whatever the script printed.

**`Digest received` fires on every ntfy notification, and that is
deliberate.** The profile's notification-title filter is empty; the filtering
lives in `Digest record`, which matches `%evtprm2` against
`^homelab digest (\d{4}-\d{2}-\d{2}): ` and requires the date to be today.
So the failure alerts above each ran the script and set nothing. Filtering in
the task rather than the profile is what makes `%evtprm2` — the firing
notification's own title — available at all; a profile-level filter would
still have to be re-checked in the task, because `%NTITLE` is the last
notification shown rather than the one that fired.

## Home Assistant

**2026-09-13.** ntfy integration on `hass` at `http://<HOMELAB_HOST>:2586` with
the `hass` token — the LAN address, because `hass` is not a tailnet member.
`homelab-alerts` event entity disabled (unreadable by design: `hass` has
write-only access to that topic). Its notify entity `notify.homelab_alerts_2`
is kept, and `ntfy.publish` is available with fields `title`, `message`,
`priority`, `tags`, `click`, `icon`, `actions`.

Event entity **`event.phone_forward_2`** exposes `title`, `message` and
`tags` — the plan's guessed attribute names were all correct. Automation
"Phone notification forwarded" re-fires each message as `phone_notification`
{app, title, text}. **Nothing on that event may unlock, disarm or open
anything.**

**The entity id carries a `_2` and that is load-bearing, not cosmetic.** The
integration was added twice, so HA suffixed both entities. Deleting the
orphaned registry entries would free the base id and rename this one, and the
automation would then stop firing **silently** — no error, no events. If you
ever tidy those up, update the automation's `entity_id` in the same change.

**The `from_state` condition is required, and `not_from` cannot replace it:**

```yaml
conditions:
  - condition: template
    value_template: "{{ trigger.from_state is not none }}"
```

When the integration is re-enabled or first loaded, HA creates the entity
fresh and the state trigger fires with `from_state` = **null**, re-delivering
the last message as a duplicate notification. `not_from` matches state
*strings*, so it has nothing to match on. Measured 2026-09-13: without the
condition, re-enabling the integration re-fired the previous message.

Verified after the fix — a genuine forward fires
(`condition/0 → true`, action emits `app: com.example.test`), while an
integration reload, a config-entry disable/enable, and a full Home Assistant
restart each produce **no** trigger at all.

Replay after the integration was disabled and re-enabled: **lost**. A forward
published while the integration was down never arrived. ntfy is not at fault
— the message was still in its cache three days out — but the integration
subscribes without requesting anything earlier, and it cannot be told to:
`config_entries/get` reports `supports_options: False`. So anything published
while Home Assistant is down or disabled is gone for good. The homelab-alerts
direction is unaffected; this applies to phone forwards only.

**The Android app does the opposite, and the debug log shows both side by
side** — same server, same cache, different client:

```
user_name=phone  <phone-tailnet-ip>  GET /homelab-alerts/ws?since=mJRgKO7ZU6UG
user_name=hass   <hass-lan-ip>       GET /phone-forward/ws
```

The app resumes from its last message id, which is why the phone catches up
after a spell offline. HA's integration connects bare, which is why forwards
sent during an HA outage are gone. If someone later reports "ntfy lost a
message", that asymmetry is the first thing to check — the server kept it.

Moved from `notify.mobile_app_pixel_10_pro` to ntfy: **none found**. Checked
by pulling every automation config through the API — 7 automations, 0 scripts,
no `scene` domain, and none referencing `mobile_app` or `notify.`. All seven
are lighting rules.
