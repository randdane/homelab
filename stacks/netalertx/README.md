# NetAlertX

**What:** Pushes to the phone when a device the house has not seen before
joins any VLAN (`myfi`, `iot`, `WuTangLAN`, `agents`). It reads `dream`'s
UniFi client list every 5 minutes and scans nothing itself.
**Why I care:** the device inventory is kept by hand, and nothing else
notices a new MAC.
**URL:** https://netalertx.${PUBLIC_DOMAIN} (gated: LAN and tailnet, then
Authentik, then NetAlertX's own password). Port `:20211` is bound to the
host's loopback only; break-glass is `ssh -N -L 20211:127.0.0.1:20211 homelab`.

## First start

1. `docker compose up -d`, then open the UI through the ssh tunnel.
2. **Change the UI password first.** Upstream ships with the password off
   and `123456` as the default; compose turns it on, so the default is live
   until you change it.
3. Confirm `LOG_LEVEL='minimal'` in `/data/config/app.conf` **before**
   entering the UniFi key.
4. Enter the UniFi site and the ntfy token (below). Leave `NTFY_RUN`
   disabled for a 24 h baseline, then clear the new-device flags and enable
   it.

## Settings that live only in the UI

Secrets cannot go through the environment: `APP_CONF_OVERRIDE` is written
to `/data` and logged at verbose.

- **UniFi import (API) → sites:** base URL
  `https://192.168.1.1/proxy/network/integration/`, version `v1`, verify
  SSL off, site `default`, API key `homelab-netalertx`.
- **NTFY → token:** the `homelab` token from `stacks/ntfy/.env`.
- **NTFY → `NTFY_RUN`:** `on_notification` once the baseline is done.
- **The UI password.**

Everything else that matters is forced from `compose.yaml` on every start;
a UI edit to those settings is undone by the next restart, on purpose.

## What will bite you

- **Verbose logging leaks the UniFi key.** Upstream's default level is
  `verbose`, at which the UniFi plugin logs its whole site config. Compose
  forces `minimal`. If you ever raise it to debug something, redact the key
  before pasting any log.
- **`LOADED_PLUGINS` is not enough to keep a scanner off.** A plugin whose
  `_RUN` setting is anything but `disabled` loads even when unlisted, so
  compose forces each scanner's `_RUN` to `disabled` as well.
- **Online/offline is meaningless here.** The UniFi API lists active clients
  only, and quiet ones drop out of it and come back many times a day. Only
  new-device alerts are on.
- **A visitor who connects and leaves between two polls is missed.**
- **`status.py` pages if no device has been seen for 30 minutes**: the sign
  that the UniFi import stopped (revoked key, `dream` down, plugin crash).

## Unverified

- Where the UniFi key ends up at rest under `/data`.
- What NetAlertX itself shows when the key is revoked.
- The 24 h baseline and a real new-device push.
