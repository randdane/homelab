# Dozzle

**What:** Live log tail for every container, in a browser.
**Why I care:** When something is broken right now, Loki means writing a LogQL
query against data that may not have shipped yet. This is `docker logs -f`
with a UI, and it is one stateless container.
**URL:** https://dozzle.<PUBLIC_DOMAIN> (break-glass: `ssh -fN -L 8090:127.0.0.1:8090 homelab`, then http://localhost:8090)

## Notes

**The legacy compose had no Docker socket mount.** It would have started
cleanly, reported healthy, and shown zero containers forever — the exact
failure this repo exists to stop. Dozzle now reads Docker over this stack's
socket proxy, via `DOCKER_HOST=tcp://socket-proxy:2375`.

**The proxy bounds takeover, not disclosure.** It denies every write and every
host-root endpoint, but `CONTAINERS=1` is what Dozzle needs, and that alone
lets anything reaching it enumerate every container and read its logs,
environment and labels. Keep this `exposure: lan` and never put it behind a
public hostname. See `docs/lessons-learned.md` §27.

**Deliberately not duplicating Loki.** Loki answers "what happened last
Tuesday"; Dozzle answers "what is this container printing right now". The
legacy repo had both plus a Prometheus stack, which is how they stopped
being distinguishable.
