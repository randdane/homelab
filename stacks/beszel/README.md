# Beszel

**What:** Resource history for the hosts and every container — CPU, memory,
disk, network, temperatures — with threshold alerts to ntfy.
**Why I care:** Uptime Kuma and `status.py` say whether something is up.
Nothing else here says what it is *using*, or whether that is creeping up:
RAM headroom before adding a stack, `tank` and the VM disk filling, a
container chewing CPU at 04:00, `pve` running hot.
**URL:** `https://beszel.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet).
`127.0.0.1:8094` on `homelab` is the break-glass path over ssh.

Replaced the `lgtm` stack for this job (moved to `~/Projects/_personal/lgtm-lab`,
learning only): one small hub instead of five containers and gigabytes.

## Agents

| Host | How | Talks to the hub over |
|---|---|---|
| `homelab` | `agent` service here, host network | a unix socket in the shared `socket` volume — no port |
| `pve` | the agent binary as a systemd service | outbound WebSocket to the vhost (`HUB_URL` + `TOKEN`) — no listening port |

The `homelab` agent reads Docker through `beszel-socket-proxy`
(GET and HEAD on containers, events, info, version and `_ping`), which shares
the host network and listens on `127.0.0.1:2377` with `-allowfrom=127.0.0.1/32`,
because a host-network container cannot resolve container names.

## `pve` agent

`scripts/systemd/pve-beszel-agent.service`, installed by hand: the v0.21.0
static binary (checksum-verified against the release's
`beszel_0.21.0_checksums.txt`) at `/usr/local/bin/beszel-agent`, secrets in
root-only `/etc/homelab/beszel-agent.env`. It runs as a systemd
`DynamicUser`, so it has no root and no disk access. SMART on `pve` stays with `smartd`;
`SMART_DEVICES=` (empty) in the env file turns the agent's SMART scan off,
which otherwise logs `smartctl failed` per disk at every start.

## First deploy

`BESZEL_KEY` cannot exist until the hub has run, so:

1. `docker compose up -d hub`, then create your account at the vhost.
2. **Add system**: name `homelab`, host `/beszel_socket/beszel.sock`. Copy the
   public key it shows into `.env` as `BESZEL_KEY`.
3. `docker compose up -d` — starts the agent and the proxy.

All env names were read from the v0.21.0 source (`agent/utils/utils.go`,
`internal/migrations/initial-settings.go`); both accept a `BESZEL_AGENT_` /
`BESZEL_HUB_` prefix.

## SSO (Authentik, native OIDC)

PocketBase's OAuth2, configured in the admin panel at `/_/` (Collections →
`users` → Options → OAuth2 → OpenID Connect), not in env. Authentik provider
`beszel`, group `beszel-users`, redirect
`https://beszel.<PUBLIC_DOMAIN>/api/oauth2-redirect`. `USER_CREATION` is unset,
so an SSO login only links to an existing account with the same email.
Password login stays as break-glass.

Two failures on the way, both silent in the UI (a blank page):

- **400, and no token request in Authentik's log.** The hub sat on
  `beszel_default` and `edge`, Docker picks the default route by network
  name, and `beszel_default` sorts before `edge` — so the token request
  arrived SNAT'd as `10.201.7.1`, which `@authentik_oidc_internal` refuses.
  Fixed with `gw_priority: 1` on `edge`. Only a stack named before `edge`
  hits this.
- **403 after a 200 token exchange.** PocketBase v0.40.4 drops the email
  unless `email_verified` is true (`tools/auth/oidc.go`), so it could not
  match the account and would not create one. The provider uses
  `homelab: email (admin-verified)`.

Verified 2026-10-05: one user, one `oidc` external auth, `auth-with-oauth2` 200.

## Alerts

To ntfy topic `homelab-alerts`, set 2026-10-12 from a day of history.
Notification URL (Settings → Notifications), the token being `NTFY_TOKEN`
from `stacks/ntfy/.env` as the basic-auth password:
`ntfy://:<token>@ntfy:80/homelab-alerts?scheme=http&title=Beszel`.

| Alert | Hosts | Threshold | Normal |
|---|---|---|---|
| Status down | both | 2 min | — |
| Disk | both | 85 %, 10 min | homelab 69 %, pve root 10 % |
| Memory | both | 90 %, 10 min | homelab peaks 74 %, pve 51 % |
| Temperature | pve | 90 °C, 5 min | package ~44 °C, peaks 82 °C |

No CPU alert: homelab hits 100 % in routine jobs. The pve disk alert
covers `tank` with no setting: the agent finds ZFS pools itself (the
"storage pool" charts) and the Disk alert checks them. Don't add `/tank` to
`EXTRA_FILESYSTEMS`; that draws a duplicate "tank Usage" chart whose I/O
stays at zero. `tank` was at 14 % on 2026-10-12.
Test notification confirmed on the phone 2026-10-12.

## What will bite you

- **`data` is backed up; `agent-data` and `socket` are not.** Losing the
  agent's data only means re-adding the system.
