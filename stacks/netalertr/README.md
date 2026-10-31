# netalertr

Alerts on each new device MAC the router reports, and serves a read-only API to
agents. It replaces NetAlertX at much lower memory. Design: `docs/spec.md` in the
`netalertr` repo. The image is built there and copied over by `netalertr-deploy.sh`;
there is no registry.

**Draft.** Copy this directory to `stacks/netalertr/`. `lifecycle: developing`
until the side-by-side run passes.

## URL

`https://netalertr.<domain>`, gated to the LAN and tailnet like the other vhosts.
Paths: `/` (no auth), `/devices`, `/devices/{mac}`, `/events?since=`, `/health`;
the rest need `Authorization: Bearer <API_TOKEN>`.

## No Authentik

This vhost is deliberately outside Authentik: it is an API for agents, with no
interactive login, secured by its bearer token, a `remote_ip` allowlist in Caddy
and the container being unreachable except over `edge`.

## Deploy

```bash
DREAM_URL=https://<dream>/proxy/network UNIFI_API_KEY="$(cat <key file>)" \
  stacks/netalertr/netalertr-deploy.sh <path to netalertr repo>
```

It refuses a dirty tree, builds, copies the image, pulls the stack, fills `.env`
(each variable only if missing), creates the write-only ntfy user and token once, and
starts this stack only. The key travels on ssh's stdin. `ROTATE_KEY=1` replaces a key
already in `.env`.

## What will bite you

- **The volume is the baseline.** Losing it silently re-seeds on the next good poll
  and drops the event log.
- A short but valid list from the controller would seed a partial baseline. Run
  `netalertr_compare.py` right after seeding.
- Both this and NetAlertX alert to the same topic during the run; tell them apart by
  title (`netalertr: new device`).
- The key sits in `.env` (mode 600). It also reads firewall rules, so treat it as
  sensitive.
- One repeat alert is possible after a crash between ntfy accepting and the save.

## Unverified

- The compare fails until `NETALERTR_EXPECTED_NETWORKS` in `.env` holds the four
  network names the controller reports.
- The memory figure on the host.

## Compare

```bash
uv run netalertr_compare.py          # read-only; exit 1 on any FAIL
python3 netalertr_compare.py --self-test
```
