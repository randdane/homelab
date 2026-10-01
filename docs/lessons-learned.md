# Lessons learned

Failure patterns found while migrating 64 legacy stack directories into this
repo (2026-08-08 to 2026-08-11). Every item here is something that actually
happened, not a hypothetical — each one is written as a guard so it does not
happen again.

The single most common failure was not a crash. It was a container that
started, reported healthy, and silently did nothing.

---

## 1. "Up" is not "working"

Nine legacy stacks could not have functioned. Only some of them crashed; the
rest ran happily while doing nothing at all.

| Stack | Looked fine, but |
|---|---|
| Dozzle | No Docker socket mounted — would have shown zero containers, forever |
| Karakeep | Chrome image no longer exists; app runs, every page archive fails |
| Calibre / Calibre-Web | Each declared its *own* `books` volume — the reader shows an empty library |
| Gitea | Postgres never wired up; would have installed on SQLite at first run, unrecoverably |
| DuckDNS | Literal `SUBDOMAINS=yourdomain` — updates nothing, certificates later fail to renew |
| OliveTin | No config file — starts, serves an empty page |
| Invidious | Signature helper crash-looping; web UI still returns 200 |
| Immich | No database, Redis, ML service, or volumes |
| ownCloud | No MariaDB, no Redis |

**Guard:** verify the *function*, not the port. A 200 from `/` proves a web
server is running, nothing more. For each stack ask "what is the one thing
this exists to do?" and test that:

```bash
# Not this:
curl -fsS -o /dev/null -w '%{http_code}' http://localhost:PORT/

# This:
dig +short @127.0.0.1 -p 5335 example.com        # pihole actually resolves
curl -s http://IP:9222/json/version               # karakeep's chrome answers
curl -s http://localhost:8095/status.php          # owncloud installed:true
docker exec karakeep ... /health                  # meilisearch reachable from the app
docker run --rm --network X curl ... -X POST      # socket proxy returns 403
```

**Corollary:** a healthcheck that passes while the stack is broken is worse
than none, because it launders a failure into a green tick. Invidious's
healthcheck hits `/api/v1/trending`, which answers 200 with playback entirely
dead. When that is unavoidable, say so in the stack README so `up` is read
correctly.

---

## 2. Image references lie in four different ways

- **The image does not exist.** `ghcr.io/louislam/cup` (wrong author — Cup is
  `sergi0g`), `gcr.io/zenika-hub/alpine-chrome:123` (withdrawn).
- **`:latest` is not latest.** Authentik's `:latest` resolved to `2025.2.4`,
  *older* than the legacy file's own `2025.4.0` pin. Upstream's current
  release was `2026.5.6`.
- **`:latest` is a pre-release.** `wger/server:latest` is `2.7.0a2`, an alpha.
- **The version label is wrong or absent.** `owncloud/server` labels its
  Ubuntu base (`22.04`), not the app (`10.16.4`). Jellyseerr's label is empty.
  `tecnativa/docker-socket-proxy` labels `v0.5.0` while the registry tag is
  also `v0.5.0` — but the label carried a `v` the first guess did not.

**Guard:** before writing an image into a compose file:

```bash
docker manifest inspect IMAGE:TAG >/dev/null && echo EXISTS   # it resolves
docker inspect IMAGE --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
# and when that is empty or suspicious, ask the app itself:
docker run --rm --entrypoint sh IMAGE -c 'grep -m1 version /app/package.json'
```

For anything with several coupled services (Immich, Authentik, Karakeep),
**fetch upstream's own compose file** and adapt it rather than inventing the
details. Immich's database image tag encodes the vector-extension versions:
substituting a stock `postgres` gives a server that starts and then fails
every search.

---

## 3. Secrets were in the files, not the environment

Found committed in the legacy repo: a **live Gmail app password**
(Watchtower), an **OIDC client secret** (Mealie), a real **`hmac_key`**
(Invidious), and real Django **`SECRET_KEY`/`SIGNING_KEY`** (wger). Plus
hardcoded database passwords (Gitea `giteapass`, Paperless `paperless`) and a
Vault root token of `myroot`.

**Guard:**

- Every secret comes from `.env` with `${VAR:?message}` — fail to start, never
  start insecure. A default is only acceptable when the value is not a secret.
- `.env.example` carries the key, **with an empty value when `:?` guards it**,
  plus instructions to generate, obtain, or choose the value — `openssl rand
  -base64 32` for a password or signing key, but a provider name is picked
  from a list and an API key is issued by somebody else. See below —
  `CHANGEME` in that position defeats the guard.
- Never carry a credential value across from a legacy file, even to "test it
  once". Generate a fresh one; treat the old one as burnt and revoke it.
- Check for insecure defaults while you are there: `SIGNUPS_ALLOWED`,
  `ALLOW_SIGNUP`, `ENABLE_USER_SIGNUP`, `registration_enabled` all shipped
  open, and Flowise had no authentication at all while holding API keys.

### `CHANGEME` cancels the `:?` guard it is supposed to accompany

The two halves of that guard were written to work together and do not.
`${VAR:?message}` fails on unset **or empty**, which is the whole point; a
literal `CHANGEME` is neither. Compose interpolates it happily and the stack
starts with a credential of `CHANGEME`.

Found 2026-09-04. `stacks/ghostfolio/compose.yaml` guards `ACCESS_TOKEN_SALT`
with `:?` and its `.env.example` ships `ACCESS_TOKEN_SALT=CHANGEME`; so does
`stacks/duplicati` with `SETTINGS_ENCRYPTION_KEY`. Both are on the
unrecoverable list below — a Ghostfolio started on the placeholder salt has
issued real tokens against it, and fixing it later locks every account out.
`cp .env.example .env && docker compose up -d` was the documented path
straight into that.

**The rule, and why it is not "empty everywhere":**

| In `.env.example` | When | Why |
|---|---|---|
| `VAR=` (empty) | the compose file guards `VAR` with `:?` | the guard *is* the enforcement, and only an empty value trips it |
| `VAR=CHANGEME` | no `:?`, but the value must still be replaced | nothing else will stop you, so the string has to be visible in the file |
| `VAR=` (empty) | genuinely optional, e.g. `myfin`'s `SMTP_*` block | absent means "off"; this is the one collision, and a comment resolves it |

Empty therefore means two things, and the comment above each block is what
separates them. That ambiguity is the price of the guard actually firing,
which is the right trade: a variable whose absence is *fatal* announces itself
at `up`, in the message the `:?` carries.

`scripts/test_env_example.py` enforces both halves: every `:?` variable
appears in that stack's `.env.example` (an empty value and a commented-out one
both count as documented), and every `:?` variable is **empty unless it is in
that file's `ALLOWED_DEFAULTS`**, which holds three entries — `life-queue`'s
two URLs and `obsidian-livesync`'s `COUCHDB_USER`.

Each entry pins the **exact value**, not just the variable name, and the test
runs in both directions: an allowlisted variable set to anything else fails,
and an entry whose variable has gone back to empty fails as stale. Naming only
the variable was the first version and it was a hole with a comment on it —
editing the allowlisted `HA_URL` to `CHANGEME` passed the entire suite.

The invariant is inverted rather than a hunt for placeholder strings, because
the hunt was weak in two directions: `CHANGEME` in quotes or trailed by a
comment slipped past the regex, and `TODO`, `xxx` or `your-key-here` were
never looked for at all. "Empty unless allowlisted" cannot be evaded by
spelling. The cost is that a new stack shipping a real default must add a
line, which is the point — the line records why a value there is safe.

Note the check keys on `${VAR:?}` specifically. `${VAR?}` without the colon
fails only on *unset*, so an empty value satisfies it and the advice above
would be wrong; no stack uses that form, and the test does not hold it to the
empty rule.

**Instructions that say "fill in every `CHANGEME`" are now wrong** wherever a
guarded variable is left empty instead. The instruction is: copy
`.env.example`, fill every *required* key that has no value, leave the ones
documented as optional empty unless you want that feature, and replace every
`CHANGEME`. The stack tells you if you missed a required one; nothing tells
you if you filled in an optional one you did not want, which is why the
distinction is in the comment above each block rather than in the value.

**Keys that must be set before first run**, because they are unrecoverable
afterwards: `N8N_ENCRYPTION_KEY` (n8n hides a generated one inside the volume,
so a restore elsewhere cannot decrypt), `ACCESS_TOKEN_SALT` (Ghostfolio — the
token *is* the credential; changing it locks every account out permanently),
`PAPERLESS_SECRET_KEY`, `SETTINGS_ENCRYPTION_KEY` (Duplicati — holds every
destination's credentials).

---

## 4. `docker.sock:ro` is security theatre

The `:ro` flag governs permissions on the *socket file*, not the API calls
made over it. A container with a "read-only" socket can still start, stop,
delete, and create containers — including privileged ones that mount the host
filesystem.

**Guard:** `:ro` on the socket is only meaningful when the application itself
never issues writes (Cup lists images and queries registries — genuine). When
the application *does* write and you want it not to, use a socket proxy:

```yaml
  socket-proxy:
    image: tecnativa/docker-socket-proxy:v0.5.0
    environment:
      - POST=0          # the setting that actually makes it read-only
      - EXEC=0          # exec is host takeover on its own, GET or not
```

and prove it:

```bash
docker run --rm --network X curlimages/curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://socket-proxy:2375/containers/NAME/stop     # must be 403
```

Anything holding a read-write socket needs a containment story. Watchtower's
is `WATCHTOWER_LABEL_ENABLE=true` with an empty opt-in list, verified with
`--label-enable --monitor-only --run-once` reporting `scanned=0 updated=0`.

---

## 5. Config mounts: `:ro` is the default, not the rule

The convention is `config/` bind-mounted `:ro`. **CouchDB breaks under it** —
its entrypoint `chown`s `/opt/couchdb/etc/local.d`, and against a read-only
mount the container exits 1 **with a completely empty log**.

**Guard:** when a container exits non-zero with no output, suspect the
read-only mount before anything else. Test by removing `:ro` alone, and if
that is the cause, leave a comment saying the mount is deliberately writable
so nobody "fixes" it back:

```yaml
      # NOT :ro. CouchDB's entrypoint chowns this directory; against a
      # read-only mount it exits 1 with an empty log. Verified, not assumed.
      - ./config/10-homelab.ini:/opt/couchdb/etc/local.d/10-homelab.ini
```

Related: OliveTin, Invidious, and wger all mount a config file that *is* the
application. wger's legacy compose mounted an `nginx.conf` that was not in the
repo. Check the file exists before trusting the mount.

---

## 6. Healthchecks fail in more ways than they succeed

- **Missing binary.** Navidrome and Home Assistant ship neither `curl` nor
  `wget`; a healthcheck calling one reports unhealthy forever — strictly worse
  than having none.
- **Your own config rejects the check.** CouchDB with
  `require_valid_user = true` returns 401 on `/_up`. Accept the auth challenge
  as proof of life rather than embedding the admin password:
  `curl -s -o /dev/null -w '%{http_code}' ... | grep -qE '200|401'`.
- **The image already has one.** Vaultwarden, Paperless, Mealie, Pi-hole, and
  File Browser all ship healthchecks. Adding a second only overrides a better
  one.
- **Slow starts look like failures.** Cup refuses connections for ~2 minutes
  while it checks every image against its registry (84 images here); wger runs
  Django migrations for minutes on a cold database. Too short a `start_period`
  restarts the container *mid-migration*.

**Guard:** check what the image ships and what binaries exist before writing
one:

```bash
docker inspect IMAGE --format '{{if .Config.Healthcheck}}{{.Config.Healthcheck.Test}}{{else}}NONE{{end}}'
docker run --rm --entrypoint sh IMAGE -c 'command -v curl wget'
```

---

## 7. Volumes encode intent, and getting it wrong is silent

- **Shared vs separate.** Calibre and Calibre-Web must share one `books`
  volume; separate ones give an empty library with no error. Services that are
  useless apart belong in one stack with one lifecycle.
- **A volume that is never written implies persistence that does not exist.**
  Vault's legacy definition mounted `vault-data:/vault/file` while running
  `server -dev`, which keeps everything in memory. Removed rather than left as
  a false comfort.
- **Compose omits the entire top-level `volumes:` block from
  `docker compose config` output when no service mounts them** — so a
  declared-but-unused volume is invisible to `check_backups.py` and
  `status.py`.

**Guard:** every named volume is expected to be backed up. One that should
never be — a regenerable cache, a media library too large for a nightly
tarball — opts out explicitly, with a comment saying why:

```yaml
  cache:
    labels:
      homelab.backup: exclude
```

Only the exact value `exclude` opts out; anything else fails closed into the
backup set. Without this, correctly-configured stacks report a backup gap
forever, and **a warning that is always wrong stops being read**.

Run `uv run scripts/check_backups.py` before committing — it caught a volume
that was genuinely forgotten (`file-browser_srv`) as well as the deliberate
exclusions.

---

## 8. Ports collide, and the collision is discovered at the worst time

Six collisions across the migration: MyFin's API on 8081 (Vaultwarden) and
frontend on 8080 (IT-Tools), qBittorrent on 8080, Vault on 8200 (Duplicati),
wger's nginx on 80 (Caddy), Pi-hole's DNS on 53 (systemd-resolved) and then
5353 (mDNS).

**Guard:** check `docs/ports.md` before assigning, and regenerate it after
every stack:

```bash
uv run scripts/ports.py && cat docs/ports.md
ss -lntup | grep ':PORT\b'     # and check the host itself, not just the repo
```

Host-level conflicts are the ones the repo cannot see: `systemd-resolved`
holds 53, mDNS holds 5353, and `tailscaled` was already holding 443.

---

## 9. Environment variables that are not what they look like

- **`${USER}` is set by your shell.** n8n's legacy basic-auth user silently
  became the OS username. Compose interpolates from the environment, not just
  `.env`.
- **`env_file: ../.env` reaches outside the stack directory.** Ghostfolio's
  did, at a file this layout does not have.
- **An empty string is not "unset".** Vaultwarden rejects `DOMAIN=""` as
  invalid and crash-loops; `${DOMAIN:-}` looks harmless and is not.
- **Some variables are resolved by the browser, not the container.** MyFin's
  `VITE_MYFIN_BASE_API_URL` is baked into requests *your browser* makes —
  `localhost` works only from the same machine, and from a phone the page
  loads while every request fails.

---

## 10. Verify against the running thing, not the documentation

Things confirmed by executing rather than reasoning, several of which
contradicted a reasonable assumption:

- `docker compose config` preserves `x-*` keys verbatim, but does **not** do
  YAML 1.1 boolean coercion (`prerequisites: no` stays the string `"no"`),
  while `purpose: 2026` does arrive as an `int`.
- Compose normalizes both dict and `k=v` list label syntax to a dict.
- `git check-ignore -v` exits 0 on a re-include pattern, so `-v` broke the
  `|| echo` idiom entirely.
- `.Config.Image` is the readable tag; `.Image` is a digest.
- `RestartCount` and `.Created` reset when Compose or Watchtower recreates a
  container — which is why `status.py` keeps `first_observed_running` in its
  own append-only state file.

---

## 11. The host runs out before the stacks do

Starting all 37 stacks at once failed at the 22nd, and kept failing for every
stack after it, with an error that names nothing in this repo:

```
failed to create network karakeep_default: Error response from daemon:
  all predefined address pools have been fully subnetted
```

Nothing was wrong with `karakeep`. Docker's **default address pools** allow
roughly 31 bridge networks in total — `172.16.0.0/12` carved into /16s gives
16, `192.168.0.0/16` into /20s gives another 16 — and one-network-per-stack
is a convention that quietly makes 31 the maximum number of stacks that can
run simultaneously. It is invisible until the day you start everything, and
the failure blames whichever stack happened to be next in alphabetical order.

**Guard:** do this as step 1 on any new host — `docs/host-setup.md` carries
it as a runnable procedure. The pools are sized in `/etc/docker/daemon.json`,
which does not exist by default. Same address space, sliced smaller:

```json
{
  "default-address-pools": [
    { "base": "172.17.0.0/12", "size": 24 },
    { "base": "10.201.0.0/16", "size": 24 }
  ]
}
```

`size: 24` gives 4096 networks of 254 addresses instead of 16 of 65k. No
stack here needs more than a handful of addresses. Requires a Docker restart,
which stops every running container. Existing networks keep the subnets they
already have — the new sizing applies only to networks created afterwards, so
nothing breaks retroactively.

Two things worth knowing before doing this on another host:

- **The daemon rewrites the base to its natural boundary.** `172.17.0.0/12`
  is reported back by `docker info` as `172.16.0.0/12`. Not an error; just do
  not expect the value you wrote.
- **Pick the second base against the real network.** Any LAN, VPN, or
  Tailscale range that overlaps it will be blackholed by Docker's routes,
  and that failure looks like "the internet is broken", not like Docker.

Before blaming a stack, check whether the ceiling is the host's:

```bash
docker network ls -q | wc -l
docker info -f '{{json .DefaultAddressPools}}'
docker network prune -f        # orphaned networks from deleted projects
```

The prune is worth doing first and is never sufficient on its own: it
recovered 7 networks here, from projects deleted months ago, which is not
close to the 15 that were needed.

---

## Checklist for a new stack

Copy `_template/`, then before committing:

- [ ] Every image tag resolves (`docker manifest inspect`) and is **pinned**
      if the service migrates on-disk state
- [ ] Upstream's own compose consulted for any multi-service application
- [ ] No secret has a default; `.env.example` lists every key with `CHANGEME`
      and a generation command
- [ ] Insecure signup/registration defaults turned off
- [ ] Healthcheck: image's own if it has one, otherwise verified binaries and
      a `start_period` that survives first-run migrations
- [ ] Every named volume either registered in `stacks/backup/compose.yaml` or
      labelled `homelab.backup: exclude` **with a comment**
- [ ] `uv run scripts/check_backups.py` clean
- [ ] `uv run scripts/ports.py` regenerated, no collision with the host
- [ ] Host has network headroom (`docker network ls -q | wc -l` well under the
      address-pool ceiling — see §11)
- [ ] Started once, **the stack's actual function tested**, then
      `uv run scripts/status.py --capture`, then `docker compose down`
- [ ] README says what it is, why it exists, and what will bite you
- [ ] Anything unverified is stated plainly — in the README and in the commit

---

## 12. A registry of everything cannot run anywhere partial

`stacks/backup/compose.yaml` lists every volume in this repo and declares
each one `external: true`. On the laptop, where all 37 stacks had been
deployed at least once, that is invisible. On a fresh server it is fatal:

```
external volume "lgtm_mimir-data" not found
```

Compose refuses to start the *whole stack* because one declared volume is
missing. So the backup service — the thing you most want running on day one
of a new host — is the last thing that can start, and only after every other
stack has run at least once.

The shape of the bug is general: a file that enumerates the complete system
is correct as documentation and unusable as configuration on any host that
holds a subset. Hosts hold subsets most of the time.

**Guard:** `scripts/backup_here.py` renders `compose.host.yaml` from the
canonical list, keeping only volumes `docker volume ls` actually reports.
`compose.yaml` stays the registry; the rendered file is what runs. Re-run it
after deploying a stateful stack.

It prints every skipped volume on every run, and that noise is the point. A
backup covering 2 of 49 volumes produces the same cheerful log line as one
covering all 49, and the difference only surfaces during a restore.

**The tempting wrong fix:** `docker volume create` the 46 missing volumes so
the canonical file starts. It works, immediately, and it manufactures nightly
archives of empty directories — a backup that passes every check except the
one that counts.

---

## 13. Docker creates missing bind sources as root, in your home directory

The backup stack bind-mounts its archive directory:

```yaml
- ${BACKUP_ARCHIVE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/homelab/backups}:/archive
```

If that path does not exist, Docker creates it — **as root**, including every
missing parent. So starting the backup stack on a fresh host silently created
`~/.local/state/homelab/` owned by `root:root`.

That directory is also where `status.py` writes its per-host state file. The
hourly status timer, running as the user, then failed every hour with:

```
PermissionError: [Errno 13] Permission denied:
  '/home/<user>/.local/state/homelab/status-server1.42585.tmp'
```

Nothing about that message points at the backup stack, and the timer's
failure is invisible unless you go looking — `systemctl list-timers` happily
shows the next run either way, and the *service* result is where the
`exit-code` hides.

**Guard:** `scripts/backup_here.py` now creates the archive directory as the
invoking user before Compose runs, so Docker never has a missing path to
invent. The archives inside stay root-owned, which is correct — the backup
container writes them as root.

The general form: any `:/container` path whose host side might not exist yet
is a root-owned directory waiting to happen, and bind sources under `$HOME`
are the ones that bite, because everything else there assumes it is yours.

## 14. A node on the wrong control plane looks exactly like a healthy one

`tailscale up` without `--login-server` registers against
`controlplane.tailscale.com`. It does not warn, fail, or ask. On the first server — which
*hosts* the headscale control plane — a bare `tailscale up` on 2026-08-21 moved
the host onto Tailscale SaaS, and every surface stayed green:

- `systemctl status tailscaled` — active (running)
- `tailscale status` — a full peer list, just a *different* tailnet's peers
- headscale's own `/health` — 200, cert valid, container up
- `headscale nodes list` — still showed the server at `<tailnet-ip>`, "online"

That last one is the trap. Headscale reports the node record it holds, not
whether the machine is currently speaking to it. The database still had the
node, so the control plane cheerfully described a host that had left.

What actually broke: the first server no longer held `<tailnet-ip>`, so
`VIKUNJA_PUBLIC_URL=http://<tailnet-ip>:3456/` — the base for every link Vikunja
hands out — pointed at an address nothing answered on. The phone, which *had*
joined headscale, could not reach the one service it was enrolled to reach.

The diagnostic that works is the only one that names the control server:

```
tailscale debug prefs | grep -i controlurl
```

`ControlURL: None` means the SaaS default. `tailscale status --json` reports
`ControlURL: None` for it too, which reads like "unset" and is easy to skim
past — the peer usernames give it away faster, SaaS shows email-style
identities where headscale shows plain usernames.

Recovery is a profile switch, not a re-registration:

```
sudo tailscale switch --list
sudo tailscale switch <profile>
```

The saved profile keeps the original node key, so the node reclaims its old
address. Re-running `tailscale up --login-server ...` with a fresh preauth key
also works, but registers a *new* node and hands out a *new* IP — which then
silently invalidates every config that hardcoded the old one.

The pattern, again: the check that would have caught this asks "is it
connected to the thing I think it is?", not "is it up?". Same shape as the
duplicati source that backed up nothing and the duckdns token that returned
KO — see §10.

## 15. Copying a live WAL-mode SQLite file returns stale data, silently

Every SQLite-backed service here — Vikunja, n8n, headscale, Duplicati — runs
in WAL mode. Recent writes live in a sidecar `database.sqlite-wal` file and
are folded into the main file only at a checkpoint. So this, the obvious way
to inspect a database inside a Docker volume, is wrong:

```
docker run --rm -v some_data:/d alpine sh -c \
  'cp /d/database.sqlite /tmp/x; sqlite3 /tmp/x "select ..."'   # WRONG
```

It copies the main file and leaves the `-wal` behind. The result is a valid,
`integrity_check`-clean database showing a consistent view of the past. No
error, no warning, no hint that anything is missing.

Query the file **in place** instead, so SQLite reads the `-wal` alongside it:

```
docker run --rm -v some_data:/d alpine sh -c \
  'sqlite3 /d/database.sqlite "select ..."'                     # RIGHT
```

For a copy that must be a copy — a backup, or an inspection while the service
must not be disturbed — use the API that checkpoints:

```
sqlite3 /d/database.sqlite ".backup /out/snapshot.sqlite"
```

or stop the container first, which checkpoints on clean shutdown.

**What it cost.** On 2026-08-21, inspecting n8n's executions through a `cp`
showed the newest execution stuck in `running` for hours and no scheduled runs
since. Reading in place showed the truth: that execution had finished in
759 ms and every 30-minute poll had run. The stale copy did not merely omit
data — it manufactured a symptom convincing enough to justify editing the
database to "fix" it.

The `stacks/headscale/README.md` gotcha about stopping the container before
copying `db.sqlite` is this same rule. It applies to every SQLite service
here, and it applies to *reads*, not just backups — which is the half that is
easy to miss, because a read feels harmless.

**The same rule, inverted, applies to restores.** `stop-during-backup` stops
the container; it does not guarantee the app checkpointed on the way down.
Measured on 2026-08-24: Jellyfin and Postgres archive clean, with no sidecar
files at all — but Vikunja's archive carried a **4 MB `vikunja.db-wal`**
holding transactions absent from `vikunja.db`. Restoring only the `.db` would
have rolled the database back to whenever it last checkpointed, and
`PRAGMA integrity_check` would still have answered `ok`, because the result is
a perfectly valid older database.

So: **restore `-wal` and `-shm` alongside the `.db`, always.** Copy the whole
directory rather than picking files out of it. The presence of a sidecar in an
archive is also the useful signal in the other direction — it tells you that
service does not checkpoint on stop, so never inspect its live files with `cp`.

## 16. A GPU can be passed through correctly and still be entirely unused

Jellyfin had `/dev/dri/renderD128` visible in the container, the right group
membership, `EnableHardwareEncoding` set to `true`, and `VaapiDevice` pointing
at the correct node. Every individual setting looked right. It was transcoding
in software the whole time, because one other field said:

    <HardwareAccelerationType>none</HardwareAccelerationType>

Nothing warns about this. There is no error, no degraded-mode log line — the
transcode simply runs on the CPU and the only visible symptom is that it is
slow under load you may not have generated yet.

Two general shapes here:

**Device present is not device used.** Passing hardware into a container is
necessary and not sufficient; the application has its own switch, and the two
are configured in different places by different means.

**Render node numbers do not follow card numbers.** This host has two GPUs,
and the mapping is crossed: `card0` is amdgpu but `renderD128` is i915, while
`card1` is i915 but `renderD129` is amdgpu. Guessing `renderD128 = card0`
would have handed Jellyfin the weak discrete Radeon. Always resolve it:

    readlink -f /sys/class/drm/renderD128/device/driver

Verify with work, not with configuration: run an actual encode
(`ffmpeg -c:v h264_vaapi`) and read the fps, and confirm the application logs
that it found the device — Jellyfin prints `VAAPI device ... is Intel GPU
(iHD)` only when it is really going to use it.

## 17. A command that failed is not a command that did nothing

Restoring a volume, the writer was stopped first — or so the script said:

```
docker compose stop life-queue-app      # -> "no such service: life-queue-app"
```

The compose *services* are `vikunja` and `n8n`; `life-queue-app` is the
**container name**. The stop never happened. The next step then wiped and
rewrote the volume underneath a running SQLite writer. The data survived, but
only by luck: nothing in the procedure would have caught it.

Three general shapes, all of which cost time this month:

**A failed step in a sequence keeps going** unless something stops it. The
error was printed, scrolled past, and the pipeline continued into a step whose
safety depended entirely on the failed one. `set -e` does not save you when
the failure is swallowed by `||`, a pipe, or a shell that only reports the
last command's status.

**Names that look interchangeable are not.** Compose service, container name,
project name, and volume name are four different namespaces that often share
a word. Resolve them (`docker compose config --services`) rather than assuming.

**Assert the post-condition, not the command.** The fix is not "check the exit
code" — it is to verify the state you actually need:

```
docker ps -a --filter name=<container> --format '{{.Names}}\t{{.Status}}'
# require: Exited
```

Any destructive step that depends on a precondition should prove that
precondition immediately before acting, because the check is cheap and the
failure is silent. Same family as §10: verify against the running thing.

## 18. A bind-mounted *file* is pinned to an inode, and `git pull` replaces it

Adding a hostname to Caddy's vhost: edit the Caddyfile in the repo, push, pull
on the host, reload. Caddy validated the config and reloaded cleanly:

```
Valid configuration
{"level":"info","msg":"config is unchanged"}
{"level":"info","logger":"admin.api","msg":"load complete"}
```

Every signal was green, and Caddy went on serving the previous config. TLS to
the new name failed the handshake, because as far as Caddy was concerned that
site did not exist.

Compose mounts the config as a single file:

```yaml
volumes:
  - ./config/Caddyfile:/etc/caddy/Caddyfile:ro
```

A single-file bind mount resolves to an **inode** at container start, not to a
path. Git does not edit files in place — it writes a temporary file and renames
it over the target, which is a *new* inode. The host path now points somewhere
the container cannot see; the container still holds the old file, which is
unlinked from the directory but very much alive.

Directory mounts do not have this problem: the kernel resolves names inside
them per lookup. It is specific to mounting one file.

The tell, and the check that costs a second:

```
docker exec <container> grep -c <new-thing> /etc/<config>   # -> 0
grep -c <new-thing> /opt/homelab/stacks/<stack>/config/<config>   # -> 3
```

The fix is `docker compose up -d --force-recreate <service>`; a reload cannot
help, because the file the reload re-reads is the stale one. Anything that
replaces rather than rewrites triggers it — `git pull`, `git checkout`, `mv`,
`sed -i`, editors that write-and-rename (many do -- check yours before relying on it).
`sed` without `-i`, `tee`, and a `>` redirect all rewrite in place and are fine.

Every stack in this repo mounts its config this way, so the rule is general:

> After pulling config onto the host, recreate the container. Do not trust a
> reload, and do not trust "valid configuration" — validation parses the file
> the container can see, which is exactly the file that is wrong.

Same family as §14 and §17: the operation reported success, and the success was
about something other than what you needed to be true.

### It happened again on 2026-09-03, to the same stack

Caddyfile edited, pushed, pulled on the first server, reloaded. `caddy reload` returned
exit 0 and logged `adapted config to JSON`. The change — moving
`jellyfin.example.com` under the wildcard — was not live, and would not have
been noticed by the reload's output at all:

```
host inode=7078604    container inode=7078597
grep -c @jellyfin:  host 2,  container 0
```

Caught only because the verification read Caddy's **live config** from the
admin API rather than trusting the reload. The route list still showed
`jellyfin.myhome.duckdns.org, jellyfin.example.com` on one site — the
old file, faithfully reloaded.

Worse, the same session had deployed the wildcard change an hour earlier and
believed the deploy path worked. It only appeared to because that one used
`docker compose up -d`, which recreates. The reload path had been broken the
whole time.

**So the fix this time was structural, not procedural.** The warning above is
now the second prose warning about this exact failure on this exact stack, and
prose has a losing record here. `stacks/caddy/compose.yaml` mounts the
directory:

```yaml
- ./config:/etc/caddy:ro          # not ./config/Caddyfile:/etc/caddy/Caddyfile
```

A directory mount resolves by path on every open, so a replaced file is seen.
The failure mode is now impossible for this stack rather than merely
documented.

**The remaining stacks still mount single files, and the rule above still
applies to them.** Converting them is the obvious follow-up; each needs its
target directory checked for anything the mount would shadow, which is why it
was not done blindly here.

The meta-lesson is the one worth keeping: this section existed, was accurate,
was specific, and named Caddy — and the bug still recurred, in a session that
had the file open. When a documented trap recurs, the next fix should remove
the trap, not describe it better. (The same session did this twice: the
Vikunja whole-object `POST` trap at `stacks/life-queue/README.md:45` was read,
documented, and walked into anyway.)

## 19. Issuing a certificate is how you announce a hostname

`jellyfin.example.com` was added to Caddy and told to nobody. The DNS record
was minutes old, the name appeared in no link, no email, no config anyone else
could read. Caddy obtained a certificate at 17:22 GMT:

```
2026-08-24T18:20:53   ...  (first non-self request)
2026-08-24T18:22:08    34  134.209.25.199   Mozilla/5.0 (l9scan/2.0.23...)
2026-08-24T18:25:22   110  34.72.176.129    Mozilla/5.0 (Windows NT 10.0...)
2026-08-24T18:34:16   112  103.196.9.140    Mozilla/5.0 (iPhone; CPU iPhone OS 26_3_...)
distinct non-self IPs: 36    total requests: 932
```

First scan **58 minutes** after issuance. `l9scan` is LeakIX; the `34.x` are
Google Cloud. They were probing `/cpanel/phpinfo.php`, `/web/config.json`, and
`/System/Info/Public`.

The first cut of this table had a fourth row at the top — 167 requests from
`curl/8.5.0` — which was **our own verification loop** hitting `/health` from
the laptop's public IP. Exclude your own traffic before counting, by finding
what `curl ifconfig.me` returns rather than by eye: an ordinary residential ASN
and a plain `curl` user-agent look exactly like a scanner in an access log.

Every certificate a public CA issues is published to the **Certificate
Transparency** logs, by design — that is what makes mis-issuance detectable.
The logs are a public, real-time stream, and scanning them is how you find
hosts that were never linked anywhere. There is no opt-out for a publicly
trusted cert.

What follows from that:

**Obscurity of a hostname buys nothing.** "Nobody knows this name" is false the
moment TLS works. Anything reachable at a name Caddy holds a cert for is
reachable by strangers within the hour, so it must be able to defend itself.

**The surface is every name in the Caddyfile**, not the ones you handed out.
Adding a vhost "temporarily to test" publishes it permanently.

**Version banners are target lists.** Jellyfin serves its exact version at
`/System/Info/Public` without authentication, and cannot be made to stop —
clients need it. Scanners record it, and when a CVE lands the list already
exists. This is why `stacks/cup` runs on the always-on host: patch latency is
the whole defence.

The genuine alternatives, if a name must stay private: put it behind Tailscale
and give it no public cert at all, or use a wildcard cert (`*.example.com` via
DNS-01), which publishes only the parent domain to the logs.

## 20. A config value of the wrong *shape* is not a config value

Jellyfin sits behind Caddy, and `KnownProxies` is what tells it to believe the
`X-Forwarded-For` header. It was set, it looked right, and it did nothing:

```xml
<KnownProxies>10.201.7.0/24</KnownProxies>
```

`KnownProxies` is a **string array**. Written as raw text it deserialises to an
*empty list* — the value is discarded, and the correct shape is what
`VirtualInterfaceNames` two lines below already demonstrates:

```xml
<KnownProxies>
  <string>10.201.7.0/24</string>
</KnownProxies>
```

There is no warning, no log line, no failed start. Jellyfin ran normally and
simply trusted no proxy, attributing every remote request to Caddy's container
address:

```
Authentication request for nosuchuser has been denied (IP: 10.201.7.3)
```

Two things were broken by that, both invisible:

**Jellyfin's own per-IP lockout** counted every failure from the entire
internet against one address. It never protected anyone, and had it ever
tripped it would have locked out all remote access at once.

**Anything downstream that reads those logs inherits the error.** A CrowdSec
brute-force scenario would have produced a technically-correct ban on
`10.201.7.3` and taken Jellyfin off the internet on the first attack. This is
how a monitoring system amplifies a data bug into an outage.

The general shape:

**A parser that accepts a value is not a parser that used it.** Strict schemas
reject bad input loudly; XML and YAML deserialisers routinely accept it and
hand back a default. `""`, `[]`, `0`, and `false` all look like "configured" in
a config file and like "unset" in the code.

**Copy the shape from a working sibling in the same file.** Anything already
serialised by the application itself is ground truth for what it expects.

**Assert the effect, never the setting.** `grep KnownProxies network.xml`
returned the value we wanted the whole time it was broken. The only check worth
running was to fail a login from outside and read which IP got logged. Same
family as §14 and §17: confirm the thing you actually need to be true.

## 21. Bumping a database image tag is not a database upgrade

Moving authentik's Postgres from 16 to 18 looked like a one-character edit.
Two separate things made it not one.

**Major versions cannot read each other's data directory.** Postgres has never
been able to; the on-disk format is version-specific. The only paths are
`pg_upgrade` (needs both binaries present) or dump-and-restore. For a 34 MB
database, dump-and-restore is not the fallback — it is the easy option.

**And in 18+ the Docker image moved the mount point.** `PGDATA` became
`/var/lib/postgresql/18/docker`, with the declared volume one level up at
`/var/lib/postgresql`, so `pg_upgrade --link` does not have to cross a mount
boundary. The old convention mounted `/var/lib/postgresql/data` directly. An 18
image handed a 16-shaped mount **refuses to start and explains why** — it does
not attempt an in-place read. That loud failure is worth more than a dozen
green checks, and is the pattern to imitate: when a config can be wrong, fail
on it, do not default around it.

**The rehearsal is free, so there is no reason to skip it.** `pg_dump` takes an
MVCC snapshot of a *live* server. The entire migration was proven — restore
into a throwaway 18 container, compare table, index, constraint and row counts
against the still-running original — before anything was stopped or deleted.
Both the mount-point error and the readiness race below were found there, at a
cost of nothing, instead of during a cutover with the service down.

**`pg_isready` on a first start is a false green.** The entrypoint starts a
temporary local-only server to run `initdb` and the init scripts, then shuts it
down before the real start. A readiness loop polling `pg_isready` breaks out on
that temporary server, and the next command hits a database that is mid-restart.
Gate on `PostgreSQL init process complete` in the logs, then poll. Same family
as §14, §17 and §20: the signal answered, but it was not answering the question
being asked.

**A volume is not free just because its own stack is down.** The cutover
stopped on `volume is in use` after `docker compose down` in the authentik
stack. The holder was `backup` — the `offen/docker-volume-backup` sidecar,
which mounts every volume it archives read-only from a *different* compose
stack. `compose down` only releases what that project owns. Worse, `docker
stop` did not fix it either: **Docker counts volume references from exited
containers too**, so the reference survived until the container was removed.
For anything in the backup set, releasing a volume means
`docker compose down` on the backup stack, not stopping a container. The
check that actually answers the question is to enumerate `docker ps -aq` —
all states — and inspect each one's mounts, before attempting the remove.

**`pg_restore` leaves no planner statistics.** The tables are correct and the
indexes exist, so every count matches and everything looks finished, but the
planner is working from empty stats until autovacuum gets around to it. Run
`ANALYZE` as the last step of any restore. This is the same false-green shape
as the rest of this entry: the verification query returns the right number
while the thing you actually care about — query plans — is still wrong.

## 22. An allowlist `.gitignore` drops new file types in total silence

`.gitignore` here ignores `*` and re-includes config by extension. Adding
`stacks/headscale/config/dns/extra-records.json` produced this:

- `git add -A` — no output, no warning, file not staged
- `git commit` — succeeded
- `git push` — succeeded
- `git pull` on the server — succeeded
- the file — absent, and the service configured to read it

Five green signals and one missing file. `git status` was clean the whole
time, because from git's point of view there was nothing to report: the file
was ignored, and ignored is not a problem state. The only rule that would have
caught it was `!*.json`, which did not exist because no `.json` had ever been
committed.

The failure is structural, not careless. A denylist fails open — forget a rule
and a secret gets committed, which is loud and awful. An allowlist fails
closed — forget a rule and a config file quietly does not exist, which is
quiet and looks like success. The original comment in that file read "Nothing
to remember, nothing to forget." That is true only for extensions already
listed, and it is exactly the sentence that stops you checking.

**When committing a file in a format this repo has not used before, assert it
is tracked:** `git ls-files <path>` must print the path. Not `git status`,
which says nothing about ignored files. Same family as §14, §17, §20 and §21:
the check has to ask the question you actually need answered, and "did the
commit succeed" is not that question.

---

## 23. A container's mount table is fixed at creation

Sibling of §18. There, the mount existed and the *file behind it* was stale.
Here the mount was never in the container at all.

`stacks/headscale/compose.yaml` declares:

```yaml
volumes:
  - ./config/dns:/etc/headscale/dns:ro
```

The directory existed on the host, the file in it was correct, and headscale
crash-looped for 45 hours on:

```
Error: ... setting up extrarecord manager: getting file info: stat
/etc/headscale/dns/extra-records.json: no such file or directory
```

Because the **running container had been created before that mount was added**.
Compose reconciles mounts when a container is created, never on a restart, and
`restart: unless-stopped` faithfully restarted it into the same stale mount
table forever. This class of failure cannot self-heal.

The diagnostic is to compare what Compose declares against what the container
actually got:

```bash
docker inspect <container> --format '{{json .Mounts}}' | python3 -m json.tool
docker compose config | grep -A3 volumes
```

Two mounts where compose.yaml declares three is the whole answer.

The fix is the same command as §18, and for the same underlying reason:

    docker compose up -d --force-recreate <service>

> Adding a volume, port, env var or bind mount to a compose file changes
> nothing about a container that already exists. `restart` re-runs the old
> definition. Only recreation applies the new one.

This was already documented for `stacks/backup` (`docs/host-setup.md`,
`stacks/backup/README.md`) and for Caddy (`stacks/caddy/README.md`,
`docs/conventions.md`) before it bit headscale. Four prose warnings did not
prevent the fifth occurrence, which is why the durable guard is detection:
`scripts/status.py` now treats a `restarting` container as not up and exits
non-zero for a production stack that is down, so the next one surfaces in an
hour instead of whenever someone tries to SSH in.


---

## 24. `restart: unless-stopped` remembers that you stopped it

A shutdown script stopped every stack before powering off, to be careful:

```bash
docker compose stop --timeout 30    # for each stack
shutdown now
```

The host came back at 00:00 and **nothing came with it**. Sixteen containers
sat exited for fourteen hours: headscale, caddy, jellyfin, authentik, the
backup container. Nothing had crashed. Every one was in exactly the state it
had been told to be in.

`docker stop` and `docker compose stop` flag a container as *manually
stopped*, and that flag survives reboots. `unless-stopped` means "restart
unless a human stopped it" — a human stopped it, so it stayed down. That is
the whole difference from `always`.

Containers stopped by **daemon shutdown** are never flagged. So the careful
thing and the working thing are opposites here:

```bash
# breaks: containers stay down after the next boot
docker compose stop && shutdown now

# works: dockerd SIGTERMs them on the way down, they restart at boot
poweroff
```

systemd stops `docker.service` during shutdown; dockerd SIGTERMs every
container, waits `shutdown-timeout` from `/etc/docker/daemon.json` (default
**15s** — raised to 30 here, because a Postgres checkpoint on a 5400rpm disk
can outrun 15s), then SIGKILLs the rest.

> Do not stop containers before rebooting. If some tool must, it owns
> starting them again afterwards. `restart: unless-stopped` will not.

The tell is an exited container with `ExitCode=0` and `RestartCount=0` under a
restart policy that should have restarted it. That combination means the
daemon was *told* to leave it alone.

Same family as §1 and §23: the operation reported success, and the thing you
needed to be true was not. Here it went further — the only alert that fired
was a nightly backup reporting a failed backup, during a total outage, because
its container was missing like everything else. Something noticed; it noticed
at the wrong altitude to be understood.


## 25. A container cannot reach the host by its tailnet IP

Uptime Kuma monitored Vikunja at the host's own Tailscale address:

```
http://<tailnet-ip>:3456/api/v1/info      -> TIMEOUT
http://life-queue-app:3456/api/v1/info  -> HTTP 200
```

Both name the same process. The first leaves the docker bridge, arrives at
the host as ordinary inbound traffic, and meets UFW's `INPUT` chain — which
is default-deny with `22/tcp` as its only rule. It is dropped and logged,
once per probe, forever:

```
[UFW BLOCK] IN=br-... SRC=10.201.7.2 DST=<tailnet-ip> DPT=3456 SYN
```

Published ports do not help. `0.0.0.0:3456->3456` makes the port reachable
from the LAN and the tailnet; it does not exempt the bridge from the host
firewall. A container talking to `<tailnet-ip>` is not "staying local" — it is
knocking on the front door from the inside.

> From inside a container, address a sibling by **container name on a shared
> network**. Use a host IP only for the thing a host IP is actually for: the
> URL a *browser* will open.

Both forms appear in one file here, correctly:

```yaml
VIKUNJA_SERVICE_PUBLICURL: http://<tailnet-ip>:3456/   # a browser opens this
VIKUNJA_INTERNAL_URL:      http://vikunja:3456       # a container opens this
```

### The part that actually bit

The monitor was written with a host IP for a real reason, and the README said
so: `life-queue` did not join `edge`, and widening a network just to monitor
it is a bad trade. Two days later a commit put `life-queue-app` on `edge` so
Caddy could proxy it. The reason evaporated; the workaround did not, and
neither did the paragraph explaining it, which was now simply false.

**A rationale in prose is a claim with an expiry date and no expiry check.**
The commit that invalidated it was correct, small, and touched no file the
monitor appeared in — because the monitor did not appear in any file. It
lived in Kuma's SQLite database, hand-created through a web UI, with the
README table as a transcription that nothing reconciles.

That is the real defect, and it generalises past monitoring: **configuration
that is not in the repository is configuration nobody reviews.** Every stack
on this host is a file, read in a diff, deployed by pulling. The monitors
were the one exception, and the exception is what drifted.

### The tell

One monitor red for eleven hours while every sibling stays green, with the
service demonstrably serving, is a broken **check**, not a broken service.
Sibling monitors agreeing against the odd one out is evidence about the
monitor.

Same family as §1, §23 and §24 — the check reported confidently and measured
something other than what it named. §1 and §24 were outages reported as
healthy. This one is the mirror: healthy reported as an outage, which spends
the same trust and spends it faster, because the cure is to mute the alert.

The verification command was already in the README and returns the answer in
one second:

```bash
docker exec uptime-kuma curl -sf -o /dev/null -w '%{http_code}\n' \
  http://life-queue-app:3456/api/v1/info
```

Nothing ran it. Write checks that execute, not checks that are documented --
so it now runs hourly as `scripts/check_monitors.py`, which probes every
monitor's target from inside the container and flags any monitor red while
its siblings are green.

Note what that script deliberately does *not* do: export the monitors and
diff them against the README. That was the obvious fix and it would have
caught nothing here, because both copies already agreed on the wrong value.
**Reconciling two records tests transcription. Only touching the real thing
tests reality** -- the same distinction as section 10.


## 26. One process, two log sinks, two formats — and you are reading the wrong one

CrowdSec's Jellyfin datasource reported `908 lines read, 12 parsed, 896
unparsed`. That was read as a broken parser and written up as "brute-force
detection is close to non-functional." Both halves were wrong, and the way
they were wrong is the lesson.

### Unparsed is not an error rate

`LePresidente/jellyfin-logs` contains exactly one grok pattern, matching one
sentence:

```
Authentication request for X has been denied (IP: Y)
```

Library scans, database vacuums, startup banners and EF Core warnings are
*supposed* to miss. For a single-purpose parser, 98% unparsed is the design.
Compare the Caddy datasource in the same table — `13.95k read, 13.87k parsed`
— where every line is an access-log entry and a high unparsed count really
would mean something. **The same metric means opposite things for two
datasources**, so "unparsed is high" is not a finding until you know which
kind you are looking at.

The metric that answers the question directly is `cscli explain`:

```bash
docker exec crowdsec cscli explain --type jellyfin --log '<a real denied line>'
```

It prints the whole pipeline, stage by stage, with a 🟢 or 🔴 per parser. The
parser was green the entire time.

### The real defect was one stage further on

That same output carried a warning that the metrics table could not show:

```
dateparse-enrich 🔴
warning: Line 0/1 is missing evt.StrTime
```

Jellyfin writes its logs **twice**, through two Serilog sinks configured in
`/config/config/logging.default.json`, with two different templates:

| Sink | Timestamp | Message | Who reads it |
|---|---|---|---|
| Console | `{Timestamp:HH:mm:ss}` | `{Message:lj}` — unquoted | `docker logs`, so CrowdSec |
| File | `{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz}` | `{Message}` — quoted | `/config/log/log_*.log` |

The same event, both ways:

```
[21:11:03] ... for alice has been denied (IP: 10.0.0.109).
[2026-08-29 18:17:22.813 -05:00] ... for "alice" has been denied (IP: "10.0.0.109").
```

The parser's `JELLYFIN_CUSTOMDATE` is `%{YEAR}-%{MONTHNUM}-%{MONTHDAY}
%{HOUR}:%{MINUTE}:%{SECOND}` — a full date. **It was written against the file
sink; this host feeds it the console sink.** Upstream clearly knew the two
sinks differ, because the pattern wraps username and IP in `"?` to absorb the
quoting difference. They just did not do the same for the timestamp.

### Why nothing looked broken

The date group is optional, `(...)?`, and the rest of the pattern anchors on
`.*`. So the message matched, the username and IP were captured, the scenario
fired, and only the timestamp came back empty. CrowdSec then falls back to the
arrival time, and leaky buckets over live traffic behave identically.

The damage is confined to **forensic mode** — replaying an old log to ask what
happened last Tuesday. Every event collapses to the time of the replay, so the
buckets see a burst that never happened and miss one that did. That is a
failure you only meet on the day you are investigating something, which is the
worst day to discover your timestamps are fiction.

### The fix is upstream of the parser, not in it

A line that never contained a date cannot have one recovered from it. The most
a pattern change could do is match the short form without *inventing* a
timestamp, and a fabricated timestamp is worse than an absent one because it
is indistinguishable from a real one. So this was not filed upstream.

Give the parser a date instead. Two ways:

1. Point acquisition at the file sink, which already has one. Costs a bind
   mount of Jellyfin's log directory and a second log source to rotate.
2. Override the console template. `stacks/jellyfin/logging.json` is a verbatim
   copy of the shipped defaults with one timestamp format changed, installed
   by `set_logging.sh`.

Chose 2: no new mount, no second source, and `docker logs jellyfin` stops
printing bare `[03:08:36]` with no day, which was ambiguous for humans too.

`logging.json` rather than editing `logging.default.json`, because Jellyfin
prefers the former and does not replace it on upgrade.

### A trap inside the fix

Commentary in that JSON lives at the **top level, outside the `Serilog`
section**. Serilog binds the keys inside a sink's `Args` to that sink method's
*parameters*, so a `"_comment"` dropped in beside `outputTemplate` can fail
the sink and take console logging with it — while the file sink keeps working
and the container stays healthy. The first draft had exactly that and it was
removed before deploying, not after.

### Verified

```
before:  dateparse-enrich 🔴   + "missing evt.StrTime"
after:   dateparse-enrich 🟢 (+2 ~2)
         geoip-enrich     🟢 (+9)
         LePresidente/jellyfin-bf            🟢
         LePresidente/jellyfin-bf_user-enum  🟢
```

Tested against a genuine failed login, generated on purpose, not a
hand-written line:

```bash
curl -X POST http://localhost:8096/Users/AuthenticateByName \
  -H 'Content-Type: application/json' \
  -H 'Authorization: MediaBrowser Client="x", Device="cli", DeviceId="x", Version="1.0"' \
  --data '{"Username":"crowdsec_verify_user","Pw":"deliberately-wrong"}'
```

A hand-written test line proves the grok compiles. Only a real one proves the
producer emits what the consumer expects — which was the entire bug.

### But that verification stopped one step short

`cscli explain` proves the *parser* is green. It does not prove an event ever
reaches a scenario, because `explain` replays a line through the pipeline by
hand — it never touches the live acquisition path. The metric that answers
that is on the acquisition table, and it was still empty:

```
| Source          | Lines read | Lines parsed | Lines unparsed | Lines poured to bucket |
| docker:jellyfin | 329        | 2            | 327            | -                      |
```

A `-` in that last column means **no Jellyfin login failure had ever reached
`jellyfin-bf`**, so the scenario could not have banned anyone no matter how
hard someone tried. The parser being correct and the pipeline being live are
two different claims, and only the second one is protection.

Closed on 2026-09-01 with a real failed login sent through the **public
hostname**, from a machine that was off-LAN at the time:

```bash
curl -X POST https://jellyfin.myhome.duckdns.org/Users/AuthenticateByName \
  -H 'Content-Type: application/json' \
  -H 'X-Emby-Authorization: MediaBrowser Client="xff-test", Device="probe", DeviceId="xfftest", Version="1"' \
  -d '{"Username":"xff_probe_user","Pw":"nope"}'
```

```
[2026-09-01 10:51:14.591 -05:00] [INF] ... Authentication request for
xff_probe_user has been denied (IP: <remote-ip>).
```

```
| docker:jellyfin | 332 | 3 | 329 | 2 |
```

Three things at once: the timestamp is the new format, `parsed` incremented,
and `poured to bucket` went from `-` to 2. That is the end-to-end proof — the
whitelist did not eat it, because the source was a genuine public address.

### A near-miss on the way there

Before that probe, the most recent parsed lines logged `IP: 10.201.7.1`, which
reads exactly like Jellyfin's `KnownProxies` having drifted — the silent
failure the Caddyfile explicitly warns about, where every remote client looks
like the proxy and per-IP lockout quietly stops working. It was almost
reported as one.

It was not that. Caddy is `10.201.7.3` on the `edge` network; `10.201.7.1` is
the **gateway**, and those earlier attempts had been made from the host itself
against `localhost:8096`, bypassing the proxy entirely. The proxied probe
logged a real public IP, proving `KnownProxies` correct.

The general form: **a test that originates inside the network cannot
distinguish "the proxy is not forwarding the client IP" from "there is no
proxy in this path."** Both produce a private address in the log. Only a
request from genuinely outside separates them — which is the same reason the
`curl` above uses the public hostname rather than `localhost`.

### The general shape

**When a program writes the same event to two places, they are two different
formats until proven otherwise**, and a parser is written against one of them.
Before pointing a log pipeline at a source, check which sink the parser's
author had in front of them. The cost of getting it wrong is not a loud
failure; it is a field quietly arriving empty, behind a pipeline that reports
success at every stage.

---

## 27. `:ro` on the Docker socket restricts the file, not the API

Six stacks mounted `/var/run/docker.sock:/var/run/docker.sock:ro`, and one of
them said this in a comment:

> Read-only is genuine here: Cup only ever lists images and queries
> registries. It never needs a write method, so a compromise of it cannot
> restart anything.

The first sentence is true and the last is false. `:ro` governs the socket
**file's** permissions — whether the container may write to that inode. It
says nothing about the HTTP methods sent over it. Docker's API has no
read-only mode; every container with the socket has the full API.

So a compromised Cup could restart anything. It could also do considerably
worse than restart, in two calls with no `exec` involved:

```
POST /containers/create   {"Image":"alpine","HostConfig":{
                             "Binds":["/:/host"],"Privileged":true}}
POST /containers/<id>/start
```

That is host root, from a container whose only stated job is listing image
tags. Access to the Docker socket is equivalent to root on the host, and no
mount flag changes that.

### What actually restricts it

A socket proxy in front, refusing methods rather than trusting the client not
to send them. `tecnativa/docker-socket-proxy` is HAProxy with per-endpoint
ACLs; `POST=0` is the setting that makes it read-only, and the endpoint flags
narrow the surface further. Verified 2026-09-03 against a live proxy:

```
GET  /containers/json     -> 200
GET  /images/json         -> 200
POST /containers/create   -> 403
```

Each stack gets its own proxy rather than one shared instance. A shared proxy
would have to grant the **union** of what every client needs, so CrowdSec's
log access and Cup's image access would both be available to whichever of them
was compromised. Per-stack costs four small HAProxy containers and gives each
client exactly its own set.

The endpoint sets are measured, not guessed — each client was run against a
proxy and its calls read out of the access log:

| Stack | Endpoints it actually called | Flags |
|---|---|---|
| `cup` | `/containers/json`, `/images/json` | `CONTAINERS`, `IMAGES` |
| `dozzle` | `/containers/json`, `/containers/<id>/json`, `/containers/<id>/logs`, `/events`, `/info`, `/version` | `CONTAINERS`, `EVENTS`, `INFO`, `VERSION` |
| `homepage` | `/containers/json`, `/containers/<id>/json`, `/containers/<id>/stats`, `/events` | `CONTAINERS`, `EVENTS` |
| `crowdsec` | `/containers/json`, `/containers/<id>/json`, `/containers/<id>/logs`, `/info` | `CONTAINERS`, `INFO` |

### What the proxy does not buy: `CONTAINERS=1` discloses container-environment secrets

`POST=0` stops takeover. It does not make the remaining surface confidential,
and the flag every one of these four clients needs is the reason: `CONTAINERS`
permits `GET /containers/{id}/json`, whose response includes `Config.Env` —
the fully resolved environment of that container, and so every secret injected
into it. (Not every `.env` value: one consumed by Compose itself, for a port
or an image tag or a volume path, never reaches the container's environment
and does not appear there. Nor anything held outside that environment — a
credential read from a mounted file, a Docker secret, or one the application
stores itself. No stack here does that today, which is exactly why the
environment is the whole of the exposure.) `GET /containers/json` alone leaks
the label set and image of everything on the host.

So a compromised Dozzle cannot start a privileged container, but it can read
Vaultwarden's `ADMIN_TOKEN`, Duplicati's `SETTINGS_ENCRYPTION_KEY` and the
Porkbun API key out of the Docker API, through a proxy configured exactly as
intended. There is no narrower proxy flag: `CONTAINERS=1` permits both
container listing and container inspection, and inspection is what returns
`Config.Env`. They are separate endpoints behind one coarse switch.

Accepted, because none of the four is `exposure: internet` — `cup` and
`dozzle` are `lan`, `homepage` and `crowdsec` are `internal` — and the
alternative is not running them. But it is a bounded blast radius, not a
boundary — the useful consequence is that **none of these four may ever
become `exposure: internet`**, and that adding a fifth client to a proxy is a
decision about secrets, not just about writes. Storing credentials somewhere other than the
container environment is the only thing that would actually close it, and is
not worth it here.

Each client also needed telling where to look, and every one of them uses a
different mechanism — there is no common convention to rely on:

| Stack | How it is pointed at the proxy |
|---|---|
| `cup` | `-s tcp://socket-proxy:2375` **as an argv flag** — it ignores `DOCKER_HOST` entirely and fails with `Socket not found: /var/run/docker.sock` |
| `dozzle` | `DOCKER_HOST=tcp://socket-proxy:2375` |
| `homepage` | `host:` / `port:` in `config/docker.yaml`, replacing `socket:` |
| `crowdsec` | `docker_host:` in each `acquis.d` source |

### The two that keep the raw socket, and why

**`backup` cannot use this.** It honours
`docker-volume-backup.stop-during-backup` labels, so it must stop and start
containers — and the proxy's `POST` flag is global, not per-endpoint. The
minimum that works, `POST=1` with `CONTAINERS=1`, also permits
`POST /containers/create`, which is the host-root path above. A proxy there
would add a container and change nothing. It keeps the raw socket, and its
mitigation is that it publishes no ports and runs on a timer.

**`lgtm`'s collector would gain almost nothing.** It also mounts
`/var/lib/docker/containers:ro` and `/:/hostfs:ro` and runs as `user: "0:0"`
for hostmetrics — so it can already read every file on the host, including
every `.env`. Proxying its socket while `/hostfs` stays would be security
theatre. The real question there is whether host-level metrics are worth a
root container with the filesystem mounted, and that is a design decision, not
a mount flag. It is also the one stack of the six that is not running.

### Verifying it, and the command that lies

`docker exec crowdsec cscli metrics show acquisition` is the natural check
that CrowdSec is still reading logs. For about a minute after a recreate it
reports this:

```
level=warning msg="fetching metrics: ... :6060: connect: connection refused"
+--------+------------+--------------+---...
+--------+------------+--------------+---...      <- empty
```

An empty acquisition table is exactly what a broken datasource looks like, so
this reads as "the proxy change killed log ingestion". It is not. `cscli
metrics` reads from CrowdSec's own Prometheus endpoint on :6060, which starts
after the agent does; the warning is about *fetching metrics*, and the empty
table is the absence of a metrics source rather than the absence of data. The
same command minutes later showed `docker:caddy` reading and parsing normally.

The log is the honest check, and it is available immediately:

```bash
docker logs crowdsec | grep -E "connected to container logs|start monitoring"
```

Which is the same failure this whole file keeps circling: **a check whose
failure mode is indistinguishable from the fault it is checking for.** The
recovery drill blamed a B2 credential for a blocked TLS handshake; `caddy
reload` reported success over a stale file; here a metrics endpoint that has
not started yet reports as a datasource that is not reading.

### The general shape

**A flag that looks like a permission may only be a file permission.** `:ro`,
`readOnlyRootFilesystem`, `--read-only` all constrain the filesystem; none of
them constrains what a program does over a socket it can still open. When the
thing behind the socket is an API, the only control is something that
understands the API and refuses calls.

Worth noting where this was already known: `stacks/dockge/compose.yaml` opened
with an accurate four-line explanation of exactly this, and used a proxy. The
knowledge was in the repo, in a stack that had never been deployed, while five
running stacks did the wrong thing — and `stacks/crowdsec` cited Cup's
incorrect comment as precedent for its own.

---

## 28. A config that is correct on disk is not a config that is running

Three separate things get called "restart it", and they fix three different
problems. Picking the wrong one leaves the change inert with nothing reporting
an error — and `docker compose up -d` will tell you the container is `Running`,
which is true and not the question.

| Operation | Makes a new | Needed when | Command |
|---|---|---|---|
| **rebuild** | image | the Dockerfile or its build context changed | `docker compose build`, or `up -d --build` |
| **recreate** | container | the *service definition* changed — image tag, `environment`, `env_file`, `volumes`, `labels` | `docker compose up -d` |
| **restart** | neither | the *content of a mounted file* changed, because the process read it at startup | `docker compose restart <svc>` |

`up -d` compares the desired **spec** against the running container. Content
inside an already-mounted directory is not part of that spec, so it correctly
does nothing. That is not a bug to work around; it is the reason a restart
exists as a separate verb.

### Measured, 2026-09-04, all three in one afternoon

Moving two public hostnames into a repo-wide `.env` hit every row of that
table at once:

- **`caddy` and `headscale` gained an `env_file:`** — a spec change, so
  `up -d` recreated them and the variables arrived. Until that happened the
  running containers had no `PUBLIC_DOMAIN` at all, because **`env_file` is
  applied at container creation and never afterwards.**
- **`crowdsec` got an edited scenario** inside a directory it already mounts —
  no spec change, so `up -d` printed `Container crowdsec Running` and did
  nothing. The running process kept the scenario it had loaded 18 hours
  earlier while the file on disk said something else.

**Neither `cscli` command could tell you that**, and the first attempt to
write this section got it wrong. `cscli scenarios list` reads the index on
disk, so it showed the new name before the restart *and* after — it never
described the process. `cscli metrics` looked like the counter-example because
it showed the old name, but it aggregates **historical alerts**, which carry
whatever the scenario was called when they fired. It still shows the old name
now, with the new scenario correctly loaded, and it always will:

```
| myhome/jellyfin-geoblock-non-us | 12 |     <- past alerts, not the live scenario
```

The reliable test needs no product-specific command at all — compare when the
process started against when the file changed:

```bash
docker inspect crowdsec --format 'started: {{.State.StartedAt}}'   # 15:15:30Z
stat -c 'mtime:   %y' path/to/the/file                             # 14:59:01Z
```

Started *after* the file changed means the running process has read it.
Started *before* means it has not, whatever any status command says.

### The trap that nearly bit

For about ten minutes both `caddy` and `headscale` were in a state where they
served correctly and **could not restart**: the new config was on disk, their
running processes held the old one in memory, and the values the new config
needed were absent from their environments. The first server rebooted nightly at 04:00.
That reboot would have taken down the edge, Jellyfin, and the headscale
control plane — including the remote access needed to fix it.

The tell was there and was nearly dismissed: `docker ps` showed
`caddy Up 23 hours (unhealthy)`, with `FailingStreak: 10` and the exact error
a restart would have died on. It was misread as pre-existing *because the
uptime was long* — but uptime measures the process, and the healthcheck was
reporting on the file. Those had just stopped agreeing.

**A long uptime is evidence that nothing has restarted, not evidence that a
restart would succeed.** When a healthcheck starts failing on a container
nobody touched, the config underneath it is the first thing to check.

### Verifying the running thing, not the file

Validating a config file proves the file. It says nothing about the process,
and the two claims are easy to conflate — `caddy adapt --env-file ...` passed
here while the container it was standing in for had none of those variables.

Ask the container instead:

```bash
docker exec caddy sh -c 'echo $PUBLIC_DOMAIN'   # is the value even in there
docker exec headscale headscale configtest      # would it survive a restart
docker inspect <svc> --format '{{.State.StartedAt}}'   # vs the file's mtime
```

The middle one is the general form: run the service's own config check *inside
the running container*. It answers "would this come back if it went down",
which is the question a healthcheck on a long-running process does not.

Be wary of a status command that merely *looks* like it answers this. Most
report on the file, or on history, rather than on what the process is holding
— and they are more misleading than having no check at all, because they
produce a confident answer to a question they were not asked.

This is §18 with a different mechanism — there a bind-mounted file was pinned
to an inode so `git pull` never reached the container; here the file reaches
the container fine and the process never re-reads it. Both end the same way: a
repo that is right, a service that is wrong, and nothing that disagrees out
loud until a restart.

---

## 29. A check that cannot answer for one thing will report on everything else

`scripts/check_updates.py` reads Cup's JSON and alerts when an image at
internet patch priority is behind. (It said "internet-facing" until
2026-09-07, and that stopped being accurate when authentik became
`exposure: lan` with `patch_priority: internet` — the LAN-only stack whose
updates still matter, which is the case this section is about.) It had been green for weeks, and it was checking the wrong
image for the one stack that matters most.

Caddy is built, not pulled — the CrowdSec bouncer is a compiled-in plugin, so
`stacks/caddy` produces `homelab/caddy-crowdsec:2.11.4` locally. That name is
in no registry, so Cup's lookup can only ever fail:

```
GET https://registry-1.docker.io/v2/homelab/caddy-crowdsec/tags/list:
Unauthorized! Please configure authentication for this registry ...
```

Caddy is the only thing on this host listening to the public internet. It
terminates TLS for every vhost, and it is the process CrowdSec's decisions are
enforced in. It was the single component the update check could never answer
for, and the check said so — `unknown, registry lookup failed` — every run, to
nobody, forever.

### The permanent unknown is what hid it

The script already distinguished "no update" from "could not check", which was
the right design and is why the information was on screen the whole time. What
it did not distinguish was **transient** from **permanent**. A rate-limited
registry lookup and an image that does not exist upstream printed the identical
line, so the unknown list always had exactly one entry in it. A list that is
never empty is a list nobody reads, and the day a real lookup failure joined it
would have looked like an ordinary Tuesday.

The lesson is not "handle errors". It is that an error condition which can
never clear stops being a signal and starts being furniture.

### The answer was already in the data

`caddy:2.11.4` — the Dockerfile's base image — was in Cup's report the whole
time, with a real verdict:

```json
{"reference": "caddy:2.11.4", "in_use": false,
 "result": {"error": null, "has_update": false}}
```

The script threw it away, because it filtered on `in_use` and nothing runs the
base image directly. So no new registry polling was needed and none was added:
a service with `build:` is now filed under its Dockerfile's **final** `FROM`
instead of the tag it builds to, and the unanswerable built tag is dropped
rather than reported as unknown.

The final stage is the correct one to read. An earlier `FROM ... AS builder`
contributes a compiled binary and nothing else, so its version is a build-time
detail; the last stage supplies the runtime the container actually runs on.
Both are pinned together in `stacks/caddy/Dockerfile` on purpose, and reading
the last one is what stays honest if they ever drift apart.

### The fix introduced its own silent failure, and that needed a guard too

A base image is on the host only as a **leftover of the build that consumed
it**. `docker image prune -a` removes it, Cup stops reporting it, and the new
check would have gone quiet — printing `clean` while watching nothing, which is
precisely the defect it was written to remove.

So absence is an alert, not a silence:

```
Base images nothing checked -- the build leftover is gone:
  caddy (internet): homelab/caddy-crowdsec:2.11.4 builds FROM caddy:2.11.4,
  which Cup did not report
```

This was not hypothetical. The same afternoon, the recommendation to run
`docker image prune` to stop Cup overstating its backlog would have disabled
the check that had just been built.

### Right answer, wrong instructions

The alert then told you to bump the tag in `compose.yaml` and run
`docker compose up -d`. For every other stack that is correct. For a built one
it does nothing at all — and it fails in the worst direction, because the alert
clears on the next run while the old binary keeps serving. Built stacks now get
their own remediation: bump every `FROM` **and** the `image:` tag, then
`docker compose build --pull`.

An alert with the wrong fix is more expensive than no alert, because someone
follows it and then believes the problem is gone.

### The general shape

This is the third instance of the same failure here in a week — the CI identity
scan that passed having scanned nothing, `check_derp.py` reporting green on a
missing `HEADSCALE_SERVER_URL`, and now this. In all three the check ran, the
exit code was 0, and the thing being checked was absent rather than healthy.

The question to ask of any monitor is not "is it passing" but **"what would it
have to see to fail, and can it still see that?"** For anything that reports on
a *set*, the coverage of the set is part of the check: a report over the wrong
five items is indistinguishable from a report over the right six, and both look
like a pass.

Two habits fall out of it. Assertions that live inside a script rather than in
a `test_*.py` need a line in `scripts/check.sh`, or nothing ever runs them —
`check_derp.py` and `check_updates.py` were both in that state. And the checks
themselves want mutation testing: all three of the new assertions here were
confirmed to fail when the logic they cover was broken on purpose, which is the
only evidence that a passing test is doing work.

---

## 30. A mechanism you argued for is a hypothesis until you remove it and watch

On 2026-09-09 four `remote_ip`-gated vhosts went unreachable from off-LAN.
Adding one nat rule fixed it immediately:

```bash
sudo iptables -t nat -I POSTROUTING 1 -s 100.64.0.0/10 -d 10.201.0.0/16 -j ACCEPT
```

The explanation written into the site notes that afternoon was that the
`DOCKER` DNAT carries no `-d`, so a packet addressed to this host is rewritten
toward a container, which converts "deliver locally" into "forward", which is
what Tailscale marks `0x40000` and masquerades in `ts-postrouting`. It is a
coherent story. It names real rules. It explains the symptom. It was wrong.

The experiment was two commands: move the exemption below the jump to
`ts-postrouting`, then probe from both sides.

| Exemption position | Remote client (the laptop) | Local self-probe |
|---|---|---|
| above `ts-postrouting` | preserved | preserved |
| **below** `ts-postrouting` | **preserved** | preserved |
| removed entirely | **preserved** | rewritten to `10.201.7.1` |

The rule it actually defeats is Docker's
`-o br-<edge> -m addrtype --src-type LOCAL -j MASQUERADE`, which fires only
when the source is one of the host's *own* addresses. A remote client is never
`LOCAL` and was never affected by it. Tailscale had nothing to do with it.

### The wrong theory got encoded into an assertion

This is the part that made it more than an embarrassing comment.
`scripts/tailnet-source-rule.sh` was written to *enforce* the theory: it
asserted that the exemption sat above `ts-postrouting`, and failed loudly if
not. The measurement above shows the exemption working perfectly in exactly
the configuration that assertion calls broken. The check would have reported a
failure on a healthy system, which is how a check gets muted.

A wrong explanation is inert while it stays prose. It becomes expensive the
moment something automated starts believing it.

### Correction, 2026-09-22: the retraction above was the error

Everything from "It was wrong" onward is wrong. The original `ts-postrouting`
explanation was right, and retracting it removed the only check that could
have caught the same failure when it came back.

It came back. On 2026-09-22 the exemption sat at POSTROUTING position 2, one
below the jump to `ts-postrouting`, with a packet counter of **zero**. All
four gated vhosts returned `000` to a tailnet client, Caddy logged
`remote_ip 10.201.7.1` for every request to them over seven days, and
`tailnet-source-rule.sh` reported `ok` the whole time — because the assertion
had been narrowed to the one rule that was not doing the rewriting. Moving the
exemption to position 1 restored all four in a single command.

**Why the disproving experiment disproved nothing.** Its "remote client"
row was the laptop, and the laptop has an `ip rule` that sends the LAN `/24`
straight out of the wifi whenever it is on the home LAN. A probe run at home
never entered the tunnel, never carried the `0x40000` mark, and so was never a
candidate for `ts-postrouting` — it would read "preserved" in every row of
that table whatever the exemption did. The 2026-09-22 reproduction was run
from a foreign network (an unrelated `/17`, no LAN route in `main`),
where `ip route get <HOMELAB_HOST>` genuinely answers `dev tailscale0`.

So the lesson below still holds, and now cuts the other way too:

- **A negative result needs the same scrutiny as a positive one.** "I moved
  the rule and nothing broke" was taken as proof, and it was a measurement of
  a path that could not break. Before believing an experiment that fails to
  reproduce, check the probe reached the code you meant to test — here, one
  `ip route get` would have shown the traffic was not on the tunnel at all.
- **Retracting a check is a change, not a cleanup.** Deleting the
  `ts-postrouting` assertion looked like removing a false alarm. It was
  removing the detector, and the thing it detected recurred within a
  fortnight with nothing left to notice.
- **What actually caught it was the probe outside the failure domain** — the
  phone's push monitor — not anything running on the affected host. Every
  on-host check agreed the system was fine, for the same reason all four
  Jellyfin checks agreed in §33: they were all reading the wrong thing.


### The same error, in a different tool, an hour later

Building a Tasker task on the phone, a `For` action's opcode and argument
positions were copied from a working example. The example was **blank** — its
fields had never been filled in. It taught structure and nothing about
behaviour, and the assumption that `For` splits a comma-separated list on
commas was never questioned. It does not. One iteration ran with the loop
variable set to the entire string, producing a request to
`https://a.example,b.example,c.example/`.

That took four wrong hypotheses to find — TLS, URL substitution, variable
naming, and an in-loop `Variable Clear` — each eliminated by measurement, none
by reasoning. What finally located it was arithmetic: a single request to one
of those hosts returns 200 every time, so had the loop iterated, that host
could not have appeared in the failed list.

**Guard:** for anything load-bearing, the experiment is to remove or displace
it and observe. Both cases here cost one command and under a minute. An
explanation that has not survived that is a hypothesis, and it does not belong
in a comment, a doc, or an `if` statement. Note also what an example does and
does not carry: a blank one gives you syntax and argument order, never
semantics.

---

## 31. A probe that answers correctly can still be answering the wrong question

The phone-side check for those same gated vhosts needed a detection test —
proving it reports `up` correctly says nothing about whether it can notice a
failure. The test was to point one probe at `nosuch.example.com`, expecting
Caddy's `handle { abort }` to close the connection.

It reported `up`. Twice.

Nothing was broken. `example.com` carried a wildcard CNAME to Porkbun's
parking host, so a name with no explicit record resolved there and answered
`302`. The probe asked "did anything answer" and the honest answer was yes. The
question it was *meant* to ask — "does an unmatched name get refused by
Caddy" — was never asked, because the request never reached Caddy at all.

The site notes had recorded that wildcard months earlier. The information
was written down and simply not applied.

### The invalid test was worth more than a valid one

Asking why the test could not fail exposed something real. With that wildcard
in place, **deleting a gated vhost's A record would have left the name
resolving to a parking page that answers 302** — so a reachability check would
have reported a service healthy that nobody could reach. Precisely the failure
shape this whole area exists to catch, sitting in DNS rather than in any
config this repo manages.

Removing the wildcard fixed it, and turned the test valid in the same motion:

```
before:  nosuch.example.com -> uixie.porkbun.com -> 302
after:   nosuch.example.com -> NXDOMAIN
```

Re-run afterwards, the probe went `down` at 60 seconds — the retry had fired
and the second pass failed too — and back `up` when the name was restored.

### What a real detection test costs

Every earlier confirmation in that session had been a green result: the check
ran, exit 0, all hosts answered. Each was correct and none of them proved the
thing that matters. The failures had to be *induced* — a gate widened on the
live host, a container stopped, a gate deleted from the Caddyfile, a name
pointed nowhere — and in each case the check was watched failing before it was
believed.

**Guard:** induce the failure you claim to detect, then verify it failed for
the reason you think. A test that has only ever passed is a test whose failure
path has never executed. And when a test cannot fail, do not just fix the
test — ask what makes failure impossible, because the answer is sometimes a
hole in the system rather than a mistake in the test.

## 32. Containment moved out of the host that is being contained

From 2026-09-15 Jellyfin — the only service open to the whole internet with no
allowlist — was contained by an iptables chain in `DOCKER-USER` plus an
`INPUT` rule, installed by a oneshot unit and rebuilt whenever Docker or
Tailscale flushed the tables. It worked, and was measured to work. It also
lived *inside* the machine it protected, depended on a Docker network's subnet
staying fixed, and needed a unit whose only job was to put the rule back.

At the move to `pve` (2026-09) Jellyfin became an LXC and the rule became
`pve/firewall/102.fw`: declarative, applied by the hypervisor on the guest's
NIC, surviving any restart inside the guest, and checked against by the same
tests. The script, its unit and `host-setup.md` 13 were
deleted; this section is why they existed.

Two things carried over, learned again on the new side:

- **A firewall that says "enabled" can be missing rules.** The first `102.fw`
  was pasted through a terminal and lost chunks of three lines; `pve-firewall`
  skipped them and loaded the rest. Rules are now a tracked file, copied not
  pasted, and `pve-firewall compile` must report no parse errors.
- **Moving a service moves its log source.** CrowdSec read Jellyfin from the
  Docker log stream; outside Docker that input simply vanishes, with every
  scenario still loaded and green. It now arrives over syslog.


## 33. Ask how the service is *run* before reading what you think is its config

On 2026-09-21 a session concluded that Jellyfin on CT 102 had never been
migrated: zero users, no LDAP plugin, and `IsStartupWizardCompleted` false. It
proposed re-migrating from the first server, and the 1.3 G transfer it ran to do so
triggered the NIC hang that took the host off the network for ten hours.

The conclusion was wrong. Jellyfin had been fully migrated since the cutover
and was working the whole time. `r` existed, with its password hash, along with
4649 library items and correct `KnownProxies` and `LdapServer` values.

The evidence had been read out of the wrong directory. The apt package creates
`/var/lib/jellyfin` and `/etc/jellyfin`, and this unit does not use either:

```
# /etc/jellyfin-homelab.env, via EnvironmentFile=
JELLYFIN_DATA_DIR=/config
JELLYFIN_CONFIG_DIR=/config/config
```

The live state is in `/config`. The default paths exist, are populated with a
package-default skeleton, and are inert.

### A wrong premise reads as consistent evidence

This was not one unlucky file. Four checks were run — the user table, the
plugins directory, the config XML, the wizard flag — and all four agreed,
because all four were read from the same unused tree. Agreement between checks
feels like confirmation and is worth nothing when they share an assumption.

The tell was visible and went unexamined. Jellyfin's own startup log printed

```
Main: Environment Variables: ["[JELLYFIN_DATA_DIR, /config]", ...]
Loaded assembly LDAP-Auth ... from /config/plugins/LDAP-Auth_23.0.0.0/
```

while the session was asserting there was no LDAP plugin. It was read as a
Docker-era leftover rather than as the answer.

### The check that costs nothing

Before reading a service's files, ask the service manager where they are:

```bash
systemctl cat <unit>          # EnvironmentFile, ExecStart, overrides, drop-ins
systemctl show <unit> -p Environment -p ExecStart -p FragmentPath
```

`systemctl cat` shows drop-ins and `EnvironmentFile=` lines, which is exactly
where a path override hides. For a container, the same question is
`docker inspect` — the mount table, not the compose file, since a running
container keeps the mounts it was created with (§18 and the 2026-08-25
headscale incident are the same lesson from the other direction).

### The generalizable shape

**A default path that exists is not evidence that it is used.** Package
installs leave a populated skeleton whether or not anything reads it, so
"the directory is there and looks empty/fresh" is consistent with both "this
is the live state" and "nothing has ever touched this." Only the process's own
configuration distinguishes them.

Stated as a rule: *find out how a thing is invoked before interpreting what is
on disk near it.* One `systemctl cat` would have cost a second and saved a
ten-hour outage — not because the outage was caused by the misreading, but
because the work that triggered it should never have been started.

## 34. A control more permissive than the system under test validates nothing

Chasing why the tailnet never makes a direct connection, a hand-rolled STUN
probe got no reply from the self-hosted DERP — from the WAN, from the LAN,
from the Docker bridge, and from inside the container's own network
namespace. `ss` showed the socket open and `Recv-Q` at 0, `tcpdump` showed the
request arriving, and the log had no errors. The conclusion written down was
"the STUN server answers nothing", and it went into a task as a fault to fix.

It was false. The server was working the whole time. A packet capture caught
a **real** client being answered — and the client was this same laptop's
`tailscaled`, doing successful STUN against that server from the same foreign
network, seconds after the hand-rolled probe had "proved" it dead.

Tailscale's STUN server requires a `SOFTWARE` attribute and a `FINGERPRINT`
attribute **together**, and silently drops anything else. Measured:

| Request | Bytes | Home DERP | `stun.l.google.com` |
|---|---|---|---|
| bare | 20 | no response | OK |
| FINGERPRINT only | 28 | no response | OK |
| SOFTWARE only | 32 | no response | OK |
| SOFTWARE + FINGERPRINT | 40 | **OK** | OK |

**There was a control, and the control was the problem.** Every probe was
checked against a public STUN server first, which answered — so the tool
looked sound. But Google's server accepts a bare 20-byte request. It is
*more permissive* than the system under test, so it could not fail on the one
defect the probe had. A control only validates an instrument if it exercises
the same strictness; a lenient control certifies a broken tool.

The same reply also carries an IPv6-family (v4-mapped) `XOR-MAPPED-ADDRESS`
where Google returns IPv4-family, so a parser written against the public
server misreads the private one even once it does answer.

Three things to carry forward:

- **"No response" is the weakest possible evidence.** It is equally consistent
  with a dead service, a blocked path, and a malformed request, and it looks
  identical in all three cases. Prefer an observation that distinguishes them
  — here, one `tcpdump` of a *working* client settled in seconds what four
  layers of negative probing had got backwards.
- **Before believing your own tool, find something that already works and
  watch it succeed.** The real client was on the same machine the whole time.
- This is §33's shape again — several checks agreeing because they all shared
  one wrong assumption — and the 2026-09-09 correction's shape too: a negative
  result accepted without asking whether the probe could have produced a
  positive.

The real cause of the relaying was elsewhere, and nothing to do with STUN
being broken: the DERP/STUN server sits on the same LAN as the nodes it
measures, so `netcheck` on VM 101 reports its "public" address as
the gateway's address — the router's hairpin SNAT — and every peer then tries to hole-punch
to a private address.
