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
(`CONTAINERS`, `VERSION`, `POST=0`), published on `127.0.0.1:2377` because a
host-network container cannot resolve container names.

## First deploy

`BESZEL_KEY` cannot exist until the hub has run, so:

1. `docker compose up -d hub`, then create your account at the vhost.
2. **Add system**: name `homelab`, host `/beszel_socket/beszel.sock`. Copy the
   public key it shows into `.env` as `BESZEL_KEY`.
3. `docker compose up -d` — starts the agent and the proxy.

All env names were read from the v0.21.0 source (`agent/utils/utils.go`,
`internal/migrations/initial-settings.go`); both accept a `BESZEL_AGENT_` /
`BESZEL_HUB_` prefix.

## What will bite you

- **`data` is backed up; `agent-data` and `socket` are not.** Losing the
  agent's data only means re-adding the system.
