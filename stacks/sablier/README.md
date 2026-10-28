# Sablier

**What:** Stops containers that nobody is using and starts them again when a
request for them reaches Caddy.
**Why I care:** RAM on `homelab`. Some tools sit idle for days and still
hold a gigabyte. Stopped, they hold nothing. What it has saved so far:
`$SITE_DIR/docs/resource-savings.md`.
**URL:** none. It has no UI; Caddy talks to it on the `admin` network at
`sablier:10000`.

## How a container is managed

1. Labels on the container: `sablier.enable: "true"` and
   `sablier.group: <group>`.
2. A `sablier` block in its vhost in `stacks/caddy/config/Caddyfile`,
   **after** `import authentik_forward_auth`, so an unauthenticated request
   cannot wake it.
3. Its container name in the socket proxy's `-allowPOST` pattern in this
   stack's `compose.yaml`. Without that, Sablier gets a 403 and the waiting
   page never ends.
4. Pause or delete its Uptime Kuma monitor: a stopped container reads as an
   outage there.

## Notes

**The socket proxy is wollomatic, not the tecnativa proxy the other stacks
use.** Sablier has to POST start and stop. Tecnativa can only allow POST in
general, and with `CONTAINERS=1` that includes `POST /containers/create`,
which is host root. Wollomatic matches each method against a regex, so POST
here is limited to start, stop and wait on the named containers. GET is still
open to every container's details, the same as the other proxies.

**What it can still do if compromised:** start or stop the containers named
in `-allowPOST`, and read every container's config through GET (environment
variables included). It cannot create, exec or touch other containers.

**A stopped container stays stopped across a reboot.** Docker counts an
API stop as a manual stop, so `unless-stopped` leaves it down, and
`homelab-reconcile` skips it because `docker compose ls` lists only running
projects. That is what we want.

**A deploy starts it, and Sablier only stops it again when Sablier itself
restarts** (`auto-stop-on-startup`, default on). A later release adds
`auto-warm-externally-started`, which would give a deployed container a
normal session; it is not in 1.18.0.

**What will bite you:** the first page load after an idle period waits for
the container's own startup. Stirling takes about 75 s.
