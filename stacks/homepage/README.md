# homepage

**What:** Dashboard for everything running on this machine.
**Why I care:** I forget which containers exist. This is the answer to
"what is running and why do I care" without reading compose files.
**URL:** http://localhost:3001

## How services get here

They are not listed in `config/services.yaml`. Homepage reads `homepage.*`
labels off the Docker socket, so a stack documents itself in its own compose
file and a stopped stack disappears from the dashboard on its own.

See `../../docs/conventions.md` for the label block.

## Notes

- Homepage has no `homepage.*` labels on itself and does not appear on its own
  dashboard. Deliberate: you are already looking at it when you'd click the
  tile, so a self-referencing link adds nothing. Revisit if a second
  dashboard/entry point is ever added.

- The five config files are mounted **individually** as `:ro`, not as a
  directory. Homepage creates missing files in `/app/config` at boot; leaving
  the directory writable from the image layer lets it do that without any
  write access to what is in git. Do not "simplify" this to `./config:/app/config`.
- Set `HOMELAB_HOST` and `PUBLIC_DOMAIN` in this stack's `.env`; both are
  `${VAR:?}`, so the stack will not render without them. Compose derives
  `HOMEPAGE_ALLOWED_HOSTS` from them -- do not set that variable directly, and
  it is no longer read from `.env`. Homepage matches the Host header
  literally, so the list has to name every address AND port the dashboard is
  reached by, and a name missing from it returns a host-validation 400 that
  reads as a broken proxy rather than a missing setting.
- Homepage reads Docker through this stack's socket proxy, not the socket
  itself — `:ro` on the socket restricts the file, never the API. The proxy
  denies every write and every host-root endpoint, but the `CONTAINERS=1` it
  needs still exposes every container's environment. Bounded takeover, not
  bounded disclosure: do not publish this beyond the LAN. (`exposure` reads
  `lan` because the Caddy vhost is reachable from the LAN and tailnet; port
  3001 itself is bound to `127.0.0.1`. It records what can reach the stack,
  not how sensitive it is -- this paragraph used `internal` to mean
  "sensitive", which is the drift that made three stacks understate an open
  port.) Since 2026-09-05 it has
  a Caddy vhost, `homepage.<PUBLIC_DOMAIN>`, and that is not a contradiction:
  the name is public but the vhost is `remote_ip`-gated to the tailnet and
  LAN, so nothing outside can reach it. Do not drop that allowlist. See
  `docs/lessons-learned.md` §27.
