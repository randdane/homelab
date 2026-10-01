# Tasker on the phone

Tasker XML for the phone that isn't tied to a stack. Stack-specific Tasker files
live with their stack (`stacks/uptime-kuma/tasker/`).

Since 2026-09-30 the exports themselves are site data and live in
`homelab-private` under `phone/tasker/` (`$SITE_DIR/phone/tasker/`). The
file names below are the ones there.

## Shizuku autostart

Shizuku gives Tasker `adb shell` privileges without root. It stops at every
reboot, and its own start-on-boot option needs root. These two profiles restart
it by turning on Wireless debugging, opening Shizuku and having AutoInput tap
**Start**. Once Tasker gets `uid=2000` back, the task turns Wireless debugging
off again, so no adb port stays open on the LAN. The server keeps running
without it (verified 2026-09-11). If the start fails, Wireless debugging stays
on so Start can be tapped by hand.

| File | Profile | Trigger | Task |
|---|---|---|---|
| `Shizuku_Autostart.prf.xml` | Shizuku Autostart | Display Unlocked + Wifi Connected `myfi` | `Shizuku Start` |
| `Shizuku_Boot.prf.xml` | Shizuku Boot | Device Boot | `Shizuku Boot Wait`: wait 20 s, then run `Shizuku Start` |

Both are needed. Storage stays encrypted until the first unlock after a boot,
so Tasker starts *because of* that unlock and never sees it. Device Boot covers
that unlock, and Display Unlocked covers the rest.

**Verified 2026-09-11**, both by stopping the server over adb and by a real
reboot: `uid=2000` about 47 s after Tasker's monitor started, with no false
alarm.

### Prerequisites

The XML can't carry these, so a fresh phone needs them first:

- Shizuku installed and paired with Wireless debugging.
- AutoInput's accessibility service enabled. Android's restricted-settings
  block may have to be lifted first:
  `adb shell appops set com.joaomgcd.autoinput ACCESS_RESTRICTED_SETTINGS allow`
- Tasker granted Shizuku and secure-settings permissions:

  ```bash
  adb shell pm grant net.dinglisch.android.taskerm moe.shizuku.manager.permission.API_V23
  adb shell pm grant net.dinglisch.android.taskerm android.permission.WRITE_SECURE_SETTINGS
  ```

### Import

1. Copy both files to `/sdcard/Tasker/profiles/`.
2. Import `Shizuku_Autostart.prf.xml` first, from the **Profiles** tab
   (long-press → Import Profile). `Shizuku Boot` runs `Shizuku Start` by name.
   The Tasks tab's Import Task rejects a `.prf.xml` with "bad filename".
3. Import `Shizuku_Boot.prf.xml` the same way.
4. **Open Shizuku Autostart's Wifi Connected condition and go back.** An
   imported Wifi Connected condition ignores unlocks until it has been saved
   once in Tasker. This happened after every import.
5. Turn on the Run Log (⋮ → Monitoring → Run Log). It writes
   `/sdcard/Tasker/log/runlog.txt`, readable over adb, and is the only record
   of what a boot run did.

### Traps behind the design

- **Tasker only gets Shizuku's binder when Tasker's own screen opens.** After
  a Shizuku restart, a background Run Shell fails with
  `runShell: Shizuku not setup yet` however long it waits. So the task opens
  Tasker before its final check.
- **Condition `<op>2</op>` is "matches"; `<op>1</op>` is not "doesn't
  match".** Op 1 came out true even when `%output` held `uid=2000`. The task
  only ever uses a matches-Stop and has no negative condition.
- **Run Shell's "Use Shizuku" is `arg8`.**
- **Waiting on `%KEYG` or `%WIFII` inside a task never finishes.** Tasker
  doesn't keep them current unless a profile watches them.
- **Tasker's "Missing Permissions" notification after every boot is
  harmless.** It fires when the first Run Shell runs before Shizuku is up.
  Don't tap it mid-run; it opens Shizuku's app list over the Start button.

## Tailnet guard

`Tailnet_Guard.prf.xml`: profile **Tailnet Guard**, Event Display Unlocked on
any network, running task **Tailnet Check**.

Android runs one VPN at a time. NetBird and WG Tunnel are also installed, and
starting either one silently takes over from Tailscale. The phone then drops
off the tailnet, and the 07:00 vhost probe fails in a way that looks like
`homelab` is down.

On each unlock, a Shizuku Run Shell checks that some interface carries
`<phone-tailnet-ip>`:

```sh
ip -4 -o addr show 2>/dev/null | grep -q <phone-tailnet-ip> && echo TAILNET_OK || echo TAILNET_DOWN
```

It matches the exact address and **names no interface**, because Android does
not keep the VPN on one. The tailnet address moves between `tun0`, `tun1` and
higher as the VPN is torn down and re-established, and the number does not
reset until reboot. Matching the address alone is also what distinguishes
Tailscale from a takeover by another VPN: NetBird or WG Tunnel gets the
`tun` device, but not `<phone-tailnet-ip>`.

An earlier version named `tun0`, on the grounds that another VPN could also
call its interface `tun0` — true, and beside the point, since the address in
the same command already settles that. What the name actually did was break
the check: on 2026-09-12 at 06:00 the phone was on `tun1`, so both checks
reported `TAILNET_DOWN` and the task raised **"Phone is off the tailnet"**
while Tailscale had been connected all night. A false alarm from a guard is
worse than no guard, because the next real one gets ignored.

On `TAILNET_DOWN`, the task sends
Tailscale's own intent, waits 5 s and checks again. Only if it's still down
does it notify **"Phone is off the tailnet"**.

```sh
am broadcast -a com.tailscale.ipn.CONNECT_VPN -n com.tailscale.ipn/.IPNReceiver
```

The command prints a marker for both outcomes, so every condition is the proven
matches-Stop. If Shizuku isn't running, as just after a reboot, `%tun` is never
set, neither marker matches, and the task does nothing rather than raise a
false alarm.

**Verified 2026-09-11:** a normal unlock stopped at the first check (11:47).
With Tailscale disconnected over adb, the next unlock reconnected it and
stopped at the second check with no notification (11:49:57 to 11:50:02).
Import needs no save step, because the profile has no Wifi Connected condition.

That run passed only because the phone happened to be on `tun0` at the time,
which is why it did not catch the bug above. **Re-measured 2026-09-12** on a
phone sitting on `tun1`, both commands run over adb back to back:

| Command | Result |
|---|---|
| `ip -4 -o addr show \| grep -q <phone-tailnet-ip>` — shipped | `TAILNET_OK` |
| `ip -4 -o addr show tun0 \| grep -q <phone-tailnet-ip>` — old | `TAILNET_DOWN` |

The old form is the false alarm, reproduced on demand. Re-import the profile
after pulling; the running copy on the phone still holds the `tun0` version
until you do.
**Untested:** a real takeover by NetBird or WG Tunnel. The address check
should catch it, but whether `CONNECT_VPN` can reclaim the VPN from another
app, rather than from a plain disconnect, is unverified.

## Notification forwarding

Four profiles watch notifications from named apps and publish them to ntfy's
`phone-forward` topic, where Home Assistant re-fires each one as a
`phone_notification` event.

| File | Profile | Owner apps | Verified |
|---|---|---|---|
| `Forward_doorbell_cameras.prf.xml` | Forward doorbell cameras | Blink, Nest | **2026-09-13: Blink end to end**; Nest not yet |
| `Forward_printers.prf.xml` | Forward printers | Prusa Connect, Bambu Handy | not yet |
| `Forward_car_charging.prf.xml` | Forward car charging | Tesla | **2026-09-13: end to end** |
| `Forward_home_devices.prf.xml` | Forward home devices | Dreo, Pentair, SleepIQ, Shelly ×2 | not yet |

Ring, ChargePoint and B-hyve are **not installed**; Ring joins the doorbell
group if it ever is. All ten packages above were confirmed installed on
2026-09-13 by `cmd package resolve-activity`.

**The task is split in two, and that split is the point.** Each profile owns a
small task (`Forward <group> send`) holding only a JavaScriptlet, which builds
the JSON with `JSON.stringify` — so a quote or emoji in a title is escaped
rather than concatenated — and hands it to the shared task by name:

```js
performTask('Forward notification', 10, body);
```

`Forward_notification.tsk.xml` is the only file holding the URL and the token,
so a change to either is one edit rather than four. It is a single HTTP Request
posting `%par1` as the body. The token is the global `%NTFY_PHONE_TOKEN`, set
by hand on the phone; no export contains it (`grep -rn 'tk_' phone/tasker/`
is clean, and ntfy tokens start `tk_`).

**`%evtprm1` is the package name, not the label.** Measured 2026-09-13: a real
Tesla notification arrived in Home Assistant as
`tags: ["com.teslamotors.tesla"]`. So anything filtering on that event matches
a package string. This closes the question the plan left open; the label was
the other plausible answer and would have broken every filter written against
it.

**Verified end to end 2026-09-13, on a real notification rather than a staged
one.** A Tesla "Connected" notification at 17:55:14 CDT produced, in order:

| Stage | Evidence |
|---|---|
| Tasker | run log: `Forward car charging send` → `Forward notification` → `HTTP Request` **OK** |
| ntfy | `tag=publish topic=phone-forward user_name=phone` from `<phone-tailnet-ip>` |
| HA entity | `event.phone_forward_2` — title `Mr. Sgwlcs`, message `Connected` |
| HA automation | `last_triggered` 22:55:14.595910Z, context `parent_id` = the entity's context id |

The automation fired 0.65 ms after the entity updated, and the matching
`parent_id` is what makes that causal rather than coincidental. Mode is
`queued`, `max: 20`, so two notifications in the same second both forward.

Nothing triggered by `phone_notification` may unlock, disarm or open anything:
anyone holding the phone token can forge one.

### Import

1. Set the global first — Tasker → Vars → `%NTFY_PHONE_TOKEN` = the `phone`
   token from Bitwarden. The XML references it by name only.
2. Copy `Forward_notification.tsk.xml` to `/sdcard/Tasker/tasks/` and import it
   from the **Tasks** tab.
3. Copy the four `.prf.xml` to `/sdcard/Tasker/profiles/` and import each from
   the **Profiles** tab.
4. Two settings do not survive an import and must be set afterwards, on the
   shared task **and** on all four `Forward <group> send` stubs:
   **Collision Handling → Run Both Together** (Task Edit → gear → Task
   Properties), and **New Only** on each profile's Notification event. Both
   were set on 2026-09-13; re-exporting would capture New Only but not the
   collision setting, which no export on this phone contains.

### Traps behind the design

- **The phone has no HTTP client.** No `curl`, no `wget`, no `busybox`;
  toybox offers only `nc`, which blocks regardless of `-w`. A Run Shell
  approach is not available, so the HTTP Request action is the only path.
- **HTTP Request is action code `339`, and its arguments are positional:**
  `arg1` method (**`1` = POST**, `0` = GET), `arg2` URL, `arg3` headers
  (one `Key:Value` per line), `arg4` query parameters, `arg5` body,
  `arg8` timeout. Every HTTP Request already on this phone was a GET with
  `arg3`–`arg7` empty, so the POST mapping could not be read off them; it was
  established from public exports in the same dialect and checked against the
  local GETs as a control. Do not infer the order from the UI — the user
  guide lists Body *last*, after Trust Any Certificate, which is not its
  argument position.
- **Several owner apps go in one profile**, comma-separated with a newline,
  in all three of `appPkg`, `appClass` and `label`. **Verified 2026-09-13:**
  the doorbell profile shows both Blink and Nest in the picker, and a real
  Blink motion notification fired it. This closes the plan's question about
  whether a second profile per app would be needed — it is not.
- **Tasker's external-access broadcast runs a task but does not pass `par1`.**
  `am broadcast -a net.dinglisch.android.tasker.ACTION_TASK -e task_name ...`
  ran `Forward notification` with an empty body, which logged `Err`. That is
  a broken probe, not a broken task — the real notification 70 s earlier
  succeeded. Test with a real notification.
- **A forward never comes back to the phone, and that is not a fault.** The
  `phone` user is write-only on `phone-forward`, so the handset can publish
  to it but cannot subscribe; the ntfy app only ever shows `homelab-alerts`.
  Watch a forward in Home Assistant — Developer tools → Events on
  `phone_notification`, or the entity `event.phone_forward_2`. Expecting the
  forward to appear in the ntfy app reads as total failure when the whole
  chain is in fact working.
- **Collision handling belongs on the per-profile `Forward <group> send`
  tasks too**, not only on the shared task. Blink's paired notifications
  arrived in the same second and the second was dropped with `T RejCopy`,
  because a task defaults to **Abort New Task**. All four stubs were set to
  **Run Both Together** on 2026-09-13 and each was read back to confirm it.
  The setting is in Task Edit → the **gear** icon in the toolbar (not the ⋮
  menu) → Task Properties → Collision Handling.

**`Int arg7` is New Only, and it is now ticked on all four profiles.** The
Notification event's fields, read off the phone on 2026-09-13, run Owner
Application, Title, Text, Subtext, Messages, Other Text, Cat, then **New
Only** last — eight positions, matching `arg1`–`arg7` after the `App` block,
which is what confirms the argument's identity. Checked on Android 6+, a
notification duplicating one already in the status bar does not fire the
event. These exports omit the argument so it imports unchecked; it was
enabled by hand afterwards. Re-exporting the profiles would capture it.

**Read a checkbox's state from the `CheckBox` node, not the label.** In a
`uiautomator` dump every `TextView` carries `checked="false"` regardless, so
matching `checked=` against the node nearest the text *New Only* reports
false whatever the truth is — it read false on four profiles that were all
actually ticked. Select by `class` containing `CheckBox` instead.

The Blink test is why it matters: Blink posts a `GROUP_SUMMARY` alongside the
real notification, and the run log shows the second arrival as `T RejCopy`.

**Still unverified:** whether an emoji in a forwarded title survives intact —
no emoji title has arrived yet.
