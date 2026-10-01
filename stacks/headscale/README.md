# headscale

Self-hosted Tailscale control plane. Every remote-access path into the house
depends on it, which makes it the one stack where "up but not working" is
expensive: nodes keep talking to each other on existing keys for a while, so a
broken control plane can stay invisible for hours.

- Control plane: `https://<DUCKDNS_HOST>` — served by `stacks/caddy`,
  which owns 80/443 and reverse-proxies to this container on `:8080`
- STUN: 3478/udp (published directly, not proxied)
- No web UI. Everything is `docker exec headscale headscale ...`.

## Why the config is committed but the data volume is precious

`config/config.yaml` holds no secrets — only *paths* to keys. The keys
themselves, the node database, and the Let's Encrypt cache live in the `data`
volume:

| File | Losing it means |
|---|---|
| `noise_private.key` | Control plane identity changes; every node must re-register |
| `derp_server_private.key` | Embedded relay identity changes |
| `db.sqlite` | Nodes, users, routes, pre-auth keys — all gone |
| `cache/` | Re-issuing the cert, against Let's Encrypt's 5-per-week duplicate limit |

Back it up. It is registered in `stacks/backup/compose.yaml`.

## Gotchas

- **`server_url` is not in `config/config.yaml`.** It comes from
  `HEADSCALE_SERVER_URL` in the repo-root `.env`, handed to the container by
  `env_file` — headscale's config loader maps any `HEADSCALE_<KEY>` variable
  onto the matching config key, and that override fully replaces the file's
  value. Verified against v0.29.3: `configtest` exits 0 with the variable and
  the key absent, 1 with neither, and still rejects a bad scheme. Do not add
  the key back; two sources would disagree silently. Setting it wrong
  re-registers every node against a URL that does not work, which is why
  `scripts/check_derp.py` fails when it is missing or disagrees with
  `DUCKDNS_HOST`.

  Commands below use `<DUCKDNS_HOST>`; substitute your own. That is a
  is not a second source of configuration.

- **Caddy owns 443; this listens on `:8080` behind it.** `server_url` is
  unchanged by that — nodes never saw a difference and did not re-register;
  only TLS termination moved. Both containers join the
  external `edge` network (created with an explicit subnet — see
  `docs/host-setup.md` §11), which is how Caddy resolves `headscale:8080`. Headscale's own `tls_letsencrypt_*` settings are
  therefore blank; leaving them set makes it fight Caddy for the certificate.
- **The embedded DERP relay is proxied too**, at `/derp`. It is a long-lived
  bidirectional stream, and a degraded relay looks exactly like a healthy one
  — an HTTP 200 on `/derp` proves nothing. Verify with `tailscale netcheck`
  and confirm a *latency figure* against "Headscale Embedded DERP".
- **STUN (3478/udp) is published directly by headscale**, not proxied. Caddy
  does not handle UDP, and this is a different protocol on a different port.
- **The DERP relay is pinned to the house's public IP**, `DERP_IPV4` in the
  repo-wide `.env`, rendered into `config/derp/custom.yaml` by
  `scripts/render_derp_map.py` (`derp.server.ipv4` in `config.yaml` is unused). A residential IP changes. When DERP starts failing while the
  control plane is fine, check that value first — or better, do not wait for
  the symptom: `uv run scripts/check_derp.py` compares it against the current
  public IP and the duckdns record, and exits non-zero on drift. It also
  probes `/health` and checks the body: address drift and a dead control
  plane are different failures, and checking only the former is how this
  reported `clean` hourly through a 45-hour outage. Kept rather
  than deleted (upstream marks it optional) because the server advertises an exit
  node, and a client routing DNS through a dropped tunnel cannot resolve the
  hostname to reconnect.
- **Restarting Docker restarts this**, which cuts every remote session
  routed through the tailnet. Do host-level Docker work from the LAN.
- **`tailscale up` on a node defaults to Tailscale SaaS, silently.** It must
  always be `tailscale up --login-server https://<DUCKDNS_HOST>`.
  Without the flag the node registers against `controlplane.tailscale.com`
  instead, and everything still looks healthy: the node is "up", `tailscale
  status` lists peers, and only the peers on *this* tailnet vanish. Verified
  2026-08-21 — a bare `tailscale up` moved the server to SaaS, its
  tailnet address went away, and every service address handed out over the
  tailnet pointed at nothing. Check with `tailscale debug prefs | grep -i
  controlurl`. Recovering is `sudo tailscale switch --list` then `sudo
  tailscale switch <profile>`; the old profile keeps the original node key, so
  the node reclaims its old IP instead of re-registering as a new one.
- **Adding a mount to compose.yaml does nothing until the container is
  recreated.** This crash-looped for 45 hours in August 2026: the `config/dns`
  bind mount was added to compose.yaml while a container created before it
  kept running, so `extra-records.json` was absent inside the container and
  headscale refused to start. `restart` cannot fix it -- only
  `docker compose up -d --force-recreate`. Compare
  `docker inspect headscale --format '{{json .Mounts}}'` against compose.yaml
  when a config file is "there" but the container disagrees. See
  `docs/lessons-learned.md` §23.
- **The image is distroless** — no shell. `docker exec headscale sh` fails;
  use `docker exec headscale headscale ...` directly.
- **Stop the container before copying `db.sqlite`.** It runs in WAL mode, so
  a copy taken while it is running can restore into a database that opens
  fine and is missing recent writes.

## Direct connections, and why they never happened until 2026-09-22

Every peer on this tailnet relayed every packet through DERP — for as long as
the tailnet has existed. `tailscale ping` reported
`direct connection not established` between every pair of nodes.

**The cause: the DERP server sits on the same LAN as the nodes it measures.**
A node asks STUN "what is my public address?" and the embedded DERP, being
inside the house, answers with a private one:

| STUN server asked | VM 101 told its address is |
|---|---|
| the embedded DERP, via the router's hairpin | `<gateway>:43721` |
| the embedded DERP, from the docker bridge | `10.201.7.1:58656` |
| `stun.l.google.com` | `<public-ip>:45688` |
| `stun.cloudflare.com` | `<public-ip>:44415` |

That private address is what VM 101 advertised to every peer. Remote peers
dutifully tried to hole-punch to the gateway's private address, could not route to it, and
fell back to DERP permanently. The router offers no way out either —
`tailscale debug portmap` reports `{PCP:false PMP:false UPnP:false}`.

**The fix is one external STUN node inside region 999**, with STUN disabled on
the relay node. See `config/derp/custom.yaml`; `automatically_add_embedded_derp_region`
is `false` so that file's region 999 replaces the generated one.

Result, measured immediately:

```
pong from homelab (<tailnet-ip>) via <public-ip>:41641 in 7ms
homelab: direct <remote-ip>:41641    (laptop)
homelab: direct <remote-ip>:41489    (phone)
```

### Two wrong shapes, both measured, both worth not repeating

**Adding an external region alongside the local one makes it worse.** netcheck
takes the reflexive address from the *nearest* region, which is still the
local one, so `IPv4:` stayed the gateway's private address — and now that two regions
disagreed, `MappingVariesByDestIP` flipped to **true**, a false symmetric-NAT
verdict that makes hole punching *more* conservative than before.

**Disabling STUN on region 999 and putting the external STUN in its own region
breaks home-region selection.** A region with no STUN-capable node gets no
latency measurement at all, and an unmeasurable region cannot be ranked — so
tailscale chose a `stunonly: true` region as its home DERP, pointing home at
something that cannot relay. `avoid: true` did **not** prevent this; `avoid`
only breaks ties between measurable regions.

Both nodes therefore have to live in region 999: the relay with
`stunport: -1`, and an external `stunonly` node to make the region measurable
and to supply the true reflexive address.

### What this does not add

Neither external node can be dialled as a relay (`stunonly: true`), so no
traffic goes near them — they answer "what is my public address" and nothing
else. `derp.urls` stays `[]`, so Tailscale's DERP map is still not pulled and
the work-wifi reasoning for that is untouched. If the external STUN is
blocked, region 999 loses its latency measurement and the tailnet degrades to
the old relay-everything behaviour rather than breaking.

**Changing this file needs `docker compose up -d --force-recreate`.** A plain
`docker compose up -d` reports `Container headscale Running` and changes
nothing, because the config is a bind mount and the compose definition has not
changed — the same trap as `docs/lessons-learned.md` 18.
