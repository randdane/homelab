# Cup

**What:** Checks every running container's image against its registry and
reports what has an update available.
**Why I care:** With images pinned in git, "should I bump this?" is the
question that actually needs answering. Cup answers it without touching
anything.
**URL:** https://cup.<PUBLIC_DOMAIN> (break-glass: `ssh -fN -L 8010:127.0.0.1:8010 homelab`, then http://localhost:8010)

## Notes

**The legacy definition pointed at an image that does not exist.**
`ghcr.io/louislam/cup` — louislam writes Dockge and Uptime Kuma, not Cup. The
correct image is `ghcr.io/sergi0g/cup`. It could never have started.

**Cup reaches Docker through this stack's socket proxy**, not the socket
itself — `-s tcp://socket-proxy:2375`. Cup only ever *lists* containers and
queries registries, so nothing is lost by denying it everything else, and the
proxy is what enforces that: `:ro` on the socket restricts the file, never the
API. See `docs/lessons-learned.md` §27.

**Cup reports; nothing acts on its own.** With a git-managed repo the update
path is: Cup reports, you bump a pinned tag, commit, `docker compose up -d`.

**It does not serve anything until its first pass finishes.** Cup checks every
image against its registry *before* opening the HTTP port. Connections are
refused until it finishes, which looks exactly like a broken container. How
long that takes scales with the image count — a host with 17 is quick;
a host with 84 took around two minutes. This is why there is no healthcheck:
any sensible `start_period` would be wrong on one host or the other.

**Why this runs on the always-on host.** `jellyfin` and `caddy` are exposed to
the internet, and scanners fingerprint Jellyfin's version through the
unauthenticated `/System/Info/Public` (see `docs/lessons-learned.md` §19).
Knowing a release has landed is the cheap half of not being on that list.
`unattended-upgrades` covers OS packages only; nothing else watches image tags.

## The answer has to arrive

Cup answers "what has an update" in a web UI, and a dashboard nobody opens is
not a control. `scripts/check_updates.py` reads this container's JSON and
pushes the answer, on `homelab-updates.timer` (Mondays, so an update found on
a weekday evening gets applied deliberately rather than in a hurry before a
weekend when family is using these stacks).

It alerts on the four stacks whose patch priority is `internet` only. Being
behind is an *exposure* there and a chore everywhere else, and a check that
fires on all forty stacks fires weekly, which is the same as not firing.

Priority is `x-homelab.patch_priority`, falling back to `x-homelab.exposure`.
Three of the four are reachable from outside and say so through `exposure`;
authentik is the fourth by `patch_priority`, because it is `exposure: lan` --
allowlisted to the LAN and tailnet -- while issuing the tokens public Jellyfin
accepts. A value that is present and unrecognised is an error, not a low
priority.

```bash
uv run scripts/check_updates.py --all        # everything, not just the four
uv run scripts/check_updates.py --url https://cup.<PUBLIC_DOMAIN>/api/v3/json   # from another machine
```

Exit 1 is "an image at internet patch priority is behind, or a stack's
priority could not be read"; exit 2 is "could not reach
Cup", which the unit treats as success — on a laptop that is asleep or off the
LAN that is the normal case, not a finding.

**A per-image registry error is carried as unknown, not as up to date.** Cup
reports `has_update: false` alongside an error, and reading that as clean is
how a renamed or rate-limited repository turns into a stack that looks current
for months.

**`caddy` is checked by its base image, because the built one is unanswerable.**
`stacks/caddy` compiles the CrowdSec bouncer in, so it builds
`homelab/caddy-crowdsec:2.11.4`, a name that exists in no registry — Cup can
only ever return `Unauthorized` for it. `check_updates.py` therefore reads the
final `FROM` out of `stacks/caddy/Dockerfile` and asks about `caddy:2.11.4`
instead, which Cup already had a real answer for; the built tag is dropped
rather than listed as unknown forever. Caddy is the only thing here listening
to the public internet, so it was exactly the wrong component to leave
unchecked. See `docs/lessons-learned.md` §29.

That base image is on this host only as a **leftover of the build**, so
`docker image prune -a` would remove it and Cup would stop reporting it. That
is an alert, not a silence — the script says which stack lost its base image
and tells you to `docker pull` it, rebuild, and refresh Cup.

`docker compose build --pull` alone is **not** enough, for this or for a base
image update. With BuildKit it fetches the new base into the build cache and
leaves the local tag on the old digest, and that local tag is what Cup reads.
Measured 2026-09-15: Caddy rebuilt on the new `caddy:2.11.4` layers, and Cup
kept reporting it behind until `docker pull caddy:2.11.4` and a
`curl http://localhost:8010/api/v3/refresh`.

**Nothing applies these updates.** Watchtower was removed on 2026-10-05
(`$SITE_DIR/docs/candidates.md`), so nothing here updates itself. That is deliberate for an
internet-facing media server whose users are family: an unattended 04:00
upgrade that breaks the TV app is discovered by someone else, at the worst
time, with no idea what changed. The tradeoff only holds while something says
an update is waiting, which is what this script is.

**Nothing to back up.** It stores no state — every answer is recomputed from
the running containers and the registries.
