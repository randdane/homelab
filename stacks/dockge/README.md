# Dockge

**What:** A browser view of the Docker host — stacks, containers, logs.
**Why I care:** Sometimes you want to see what is running without a terminal.
**URL:** http://localhost:5001

## Notes

**`docker.sock:ro` does not make the Docker API read-only.** This is the thing
worth knowing here. The `:ro` flag governs permissions on the *socket file*,
not the API calls made over it — a container holding a "read-only" socket can
still start, stop, delete, and create containers, including privileged ones
that mount the host filesystem. Read-only sockets are security theatre.

So this stack does it properly: the socket goes to a
`tecnativa/docker-socket-proxy` with `POST=0`, and Dockge talks to the proxy
over TCP. Write methods are refused at the proxy, so Dockge genuinely cannot
change anything. `EXEC` is also denied — container exec is host takeover on
its own, GET or not. The proxy publishes no ports; only this stack's network
can reach it.

**Consequence: Dockge's buttons will not work.** Start, stop, and edit will
error. That is the intended behaviour, not a bug — this is a viewer.

**`DOCKGE_STACKS_DIR` deliberately points at an empty volume, not this repo.**
Dockge writes compose files into its stacks directory, and two sources of
truth for the same YAML is exactly how config drifts. Git is the source of
truth; this is a window.

**If you decide you want Dockge to actually manage stacks**, that is a real
choice with a real cost: give it the raw socket and point it at the repo, and
accept that the compose files can then change outside of git. Do not do it
halfway — a proxy with `POST=1` is the raw socket with extra steps.

**Nothing here is backed up.** Its settings are a few rows, and the stacks
directory is empty on purpose.
