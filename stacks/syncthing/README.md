# Syncthing

**What:** Device-to-device file sync. This instance is the always-on peer:
the laptop and the phone each sync with it, so they never need to be on at
the same time.
**Why I care:** No cloud in the middle, and the devices keep syncing with
each other through a `homelab` outage.
**URL:** `https://syncthing.<PUBLIC_DOMAIN>` (gated vhost, Authentik
forward-auth, then Syncthing's own password). `127.0.0.1:8384` on `homelab`
is the break-glass path over an ssh tunnel.

**Authentik:** forward-auth, group `admin-uis`. Syncthing has no OIDC. Its
own GUI password stays on, so it remains the way in when Authentik is down.

## How devices reach it

- **LAN:** `tcp://<HOMELAB_HOST>:22000`. `101.fw` admits 22000 tcp+udp from
  the LAN subnet only.
- **Off-LAN:** `tcp://homelab:22000` over the tailnet. The VM firewall cannot
  see tailnet traffic, and `policy.hujson` already lets your devices reach it.

Global discovery, relays and local discovery are **off** on this peer, so
each client lists both addresses on the device by hand. Nothing goes through
a third party, and no NAT punching is needed.

## What will bite you

- **The volume holds every synced folder,** and it is registered with the
  backup stack. A large folder grows the nightly archive by its full size.
  Check `df -h /` before sharing something big.
- **Losing the volume changes the device ID.** The keys live in it, and every
  client would have to be paired again. Restore from backup instead.
- **Its own password must be set at first start.** Until then, the GUI on
  127.0.0.1 has none.
- **No Kuma monitor:** it is on `admin`, which Kuma cannot reach. `status.py`
  pages on the image healthcheck once it is `production`.
- Android's background limits: the phone app syncs only while it is allowed
  to run. Exempt it from battery optimisation.

## Unverified

Throughput over the tailnet, and the phone app under Android 17.
