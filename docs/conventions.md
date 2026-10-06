# Conventions

Copy `_template/` to start a new stack. Everything below is already in it.

The step-by-step order, through deploy and monitoring, is
`adding-a-stack.md`.

These are the rules. `lessons-learned.md` is the *why* — the failure patterns
that produced them, with a pre-commit checklist. Read it once before adding a
stack you have not run before.

    cp -r _template stacks/mynewthing

## Every stack directory contains

| File | Committed | Purpose |
|---|---|---|
| `compose.yaml` | yes | Always this name. `scripts/ports.py` matches it exactly. |
| `.env.example` | yes | Every key the stack needs. Blank for anything `compose.yaml` guards with `${VAR:?}` — a placeholder satisfies the guard. See `lessons-learned.md` §3. |
| `.env` | no | Real credentials. Gitignored, and `chmod 600` — `umask 002` writes 0664 otherwise, and `status.py` reports it hourly. |
| `README.md` | yes | What it is, why it exists, URL, gotchas. |
| `config/` | yes | Hand-edited config, bind-mounted `:ro`. Optional. |

Directory names are lowercase-kebab.

## The repo-root `.env` holds this deployment's identity

`PUBLIC_DOMAIN`, `DUCKDNS_HOST` and `HEADSCALE_SERVER_URL` live in `/.env`
(see `/.env.example`), which is the canonical copy — not merely the usual one.
A stack `.env` may hold the same value where Compose cannot read the root file
at render time (see the carve-out below), and where it does, `/.env` is still
what that copy has to agree with. Caddy builds every vhost
from the first two, headscale takes its `server_url` from the third, and
`scripts/check_derp.py` reads the last two.

**The bar is what the value *is*, not how many things read it.** An earlier
version of this section said "two consumers", and that rule was wrong on its
own example: `PUBLIC_DOMAIN` has exactly one runtime consumer, Caddy, and
several substitutions inside one Caddyfile are still one module. Counting
consumers would have sent it to `stacks/caddy/.env` and split the domain from
the duckdns name it is always changed alongside.

So the test is: **does this name the deployment, or configure a service?**
A hostname the whole installation is reached by is identity, and belongs here
even with one reader today. A port, a tag, a feature flag is configuration
and belongs in the stack's own `.env`, where Compose reads it with none of the
machinery below.

**Only non-secret identifiers.** `env_file` hands a container *every* key in
the file, so a shared credential put here would be injected into every stack
that reads it — including ones with no business holding it. Secrets stay in
the stack `.env` that needs them, behind `${VAR:?}`.

Two mechanisms, because there is no single one that covers both:

| Consumer | How it reads the file |
|---|---|
| a container | `env_file: [{path: ../../.env, required: false}]` — puts the values in the container's environment, where Caddy's `{$VAR}` finds them |
| a script in `scripts/` | `shared_env("NAME")` from `compose.py` — real environment first, then the file |

**`env_file` does not feed Compose's own `${VAR}` interpolation**, which reads
only the shell and the stack's own `.env`. So the root file cannot *supply*
`${VAR}` anywhere in a `compose.yaml`; only the container and the scripts can
read it.

That is a fact about Compose, not a rule, and it has one consequence worth
stating plainly because it looks like a violation of the section above: a
compose file that genuinely needs one of these values at render time — a
`homepage.href` label naming a vhost, say — must have its own copy in the
stack's `.env`. See "Dashboard links across hosts" below. The identity still
belongs in `/.env` for every consumer that can reach it; the copy exists
because interpolation cannot, and it is guarded with `${VAR:?}` so a missing
one fails instead of rendering blank.

**`required: false` is not optional politeness.** The default is `true`, and a
missing root `.env` then makes the whole stack unrenderable — `docker compose
config` fails, and every tool here reads stacks through that command. Measured:
caddy dropped out of `docs/ports.md` and took its two volumes out of the
backup-coverage count with one `SKIP` line to show for it. Let the stack render
everywhere and fail at the service instead, where an empty `{$PUBLIC_DOMAIN}`
is an invalid site address and Caddy refuses to start with a real error.

**The `shared_env()` override reaches scripts only.** The real environment
wins there, which makes `DUCKDNS_HOST=x uv run ...` work for a one-off. It
does *not* reach containers: Compose resolves `env_file` from the file itself
and the shell environment does not feed it
([precedence][compose-env-precedence]). A permanent systemd `Environment=`
would therefore have the monitor reasoning about one hostname while the
containers use another.

None of these names has a fallback default anywhere. A default would be this
deployment's public hostname written into the repo, which is the thing keeping
them in `.env` exists to avoid — and in `check_derp.py` a silent default would
mean a monitor that reports success while checking a hostname nobody uses. For
the same reason `check_derp.py` treats **any** of them being absent as a
failed run rather than a skipped one.

`shared_env()` refuses a key defined twice. Compose takes the last occurrence
(measured); the obvious loop takes the first. Rather than pick, it raises —
the one outcome worse than either is the script and the container quietly
disagreeing, which is what a shared file exists to prevent.

[compose-env-precedence]: https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/

## Compose rules

- `name:` set explicitly at the top, so the project name does not depend on
  the directory name.
- `restart: unless-stopped` on every service.
- **Named volume** for anything the container writes.
  **`:ro` bind mount from `config/`** for anything you edit by hand.
- `healthcheck:` wherever the image supports one — verify the binary first.
- `docker-volume-backup.stop-during-backup: "true"` on stateful containers.
- **Every named volume is expected to be backed up.** A volume that should
  never be archived — a cache that regenerates, a media library too large for
  a nightly tarball — opts out explicitly, with a comment saying why:

  ```yaml
  volumes:
    cache:
      labels:
        # Regenerated on demand; archiving it wastes space nightly.
        homelab.backup: exclude
  ```

  Only the exact value `exclude` opts out; anything else fails closed into the
  backup set. Without this, correctly-configured stacks report a backup gap
  forever — and a warning that is always wrong stops being read.

  That label is all-or-nothing for one volume. To drop a *path inside* a
  volume that is otherwise precious — a regenerable cache living next to a
  database — use `BACKUP_EXCLUDE_REGEXP` in `stacks/backup`, and record why
  in that stack's README.

  The test either way is **"can this be rebuilt from something that is not
  this backup?"**, not "is this big?". Jellyfin's artwork cache is excluded
  because TMDB will re-supply it; the media library is excluded because it can
  be downloaded again. Size decides whether the exclusion is worth making,
  never whether it is safe.

  Answer that test against **a dead disk**, which is the failure a backup is
  for. "It already exists on disk outside any volume" is not an answer -- that
  is the disk that just died. Both of these read as sound rationale right up
  until the only moment they are consulted.
- Homepage labels on anything with a web UI, and nothing else (see below).
- **An `x-homelab` block** at the top level — stack intent, read by
  `scripts/status.py`. All keys except `description` and `patch_priority` are
  required and non-blank; use the literal `none` for `prerequisites` when
  there is nothing to record. The schema is defined in `README.md`, and `_template/` has it
  filled in with valid enum values.
- **`exposure` is factual, and `patch_priority` is the judgement.** `exposure`
  answers only "what can reach this" — a stack behind a `remote_ip` allowlist
  is `lan` even if it feels important. `patch_priority` is optional, defaults
  to `exposure`, and is what `scripts/check_updates.py` alerts on. Set it only
  where the two genuinely differ: authentik is `exposure: lan` and
  `patch_priority: internet`, because it issues the tokens public Jellyfin
  accepts. Without the second key the only way to keep a stack in the alert
  set is to overstate its reachability, which makes `exposure` useless as a
  record of what is actually exposed.
- No `start.sh` / `stop.sh`. `docker compose up -d` is the interface.
- No top-level `networks:` block unless a service needs to be reached from
  outside its own compose file. Compose already creates a per-project default
  network; a declared network is only worth the extra lines when something
  external needs to join it. Existing stacks that declare one predate this
  convention — leave them, do not churn working stacks to match.
  Compose's per-project network is still a real network, and Docker's default
  address pools only fit ~31 of them: a host meant to run every stack at once
  needs the `default-address-pools` sizing in `lessons-learned.md` §11 set
  **before** it has 31 stacks, not after.
- `container_name:` prefixed with the project name (`<stack>-<service>`,
  e.g. `lgtm-grafana`) when a stack has more than one service, since it is
  the label column in `docs/ports.md`. A single-service stack may use the
  bare name. Existing containers are not renamed retroactively.

## The "not created by Docker Compose" volume warning is expected

    volume "headscale_data" already exists but was not created by Docker
    Compose. Use `external: true` to use an existing volume

Cosmetic. Ignore it. Investigated and decided 2026-08-25 so it does not get
re-investigated every time it scrolls past.

Compose stamps `com.docker.compose.*` labels on volumes **it** creates. A
volume that predates its stack being managed here has none, and Compose says
so once per `up`. On the first server that was 45 of the named volumes, 30 of
them `data: precious`.

Nothing behaves differently. The label is not a safety property -- verified:
`docker compose down -v` removes an unlabelled volume exactly as readily as a
labelled one. It changes the warning text and nothing else.

**The labels cannot be added in place.** There is no `docker volume update`,
and `docker volume create --label ... <existing>` is a silent no-op: it exits
0, prints the volume name, and changes nothing. Labels are fixed at creation,
the same way a container's mount table is (`docs/lessons-learned.md` §23).
Adding them means recreating each volume and copying the data, with the writer
stopped -- and since every one of these is also mounted into the `backup`
container, that container needs recreating afterwards too, or it silently
keeps archiving the old set (§6 of `docs/recovery.md`).

**It self-heals, so let it.** The volumes that *do* carry labels -- authentik,
caddy, crowdsec, duplicati, jellyfin_cache, uptime-kuma -- got them by being
recreated for real reasons: a Postgres major-version migration, a restore, a
rebuild. Any volume gains its labels the next time something legitimately
recreates it, at no extra risk. Forcing it early is 45 stop/copy/swap cycles
across precious data to buy a cosmetic property that arrives for free.

Do **not** "fix" it with `external: true`, which the warning itself suggests:
that stops Compose creating the volume on a fresh host, which is exactly the
rebuild path `docs/host-setup.md` §1c depends on. It trades a cosmetic warning
for real recovery friction.

## Image tags

Pin anything that migrates on-disk state: databases, Loki, Mimir, Immich.
`:latest` is fine for stateless services.

Nothing updates images unattended: tags are bumped in this repo after Cup or
`check_updates.py` reports them. An unattended updater turns a one-way schema
migration at startup into an upgrade nobody chose and nobody can undo by
pulling the old tag (`docs/declined-stacks.md`, Watchtower).

## Homepage labels

Required on any service with a UI. This is where "why do I care about this"
lives — next to the thing it describes, so it cannot drift separately.

```yaml
labels:
  homepage.group: Media
  homepage.name: Jellyfin
  homepage.description: Movies and TV for the living room TV
  homepage.href: http://localhost:8096
  homepage.icon: jellyfin
```

A stopped stack disappears from the dashboard, so the dashboard answers
"what is actually running" with nothing to keep in sync.

## Adding a stateful stack

Backup does not auto-discover volumes. Per **volume**, not per stack:

1. Add `docker-volume-backup.stop-during-backup: "true"` to the service that
   writes it. This only pauses the container so the archive is not caught
   mid-write — it does not cause the volume to be backed up.
2. Add **two entries** to `stacks/backup/compose.yaml`: a `:ro` mount under
   `/backup/`, and an `external: true` declaration under top-level `volumes`.

A four-volume stack therefore costs eight lines there. That is the price of
having what is backed up be readable in one file.

## Healthchecks

Check which binary the image ships before writing one. A healthcheck calling a
missing binary reports unhealthy forever, which is worse than no healthcheck:

    docker run --rm --entrypoint sh IMAGE -c 'command -v wget; command -v curl'

## Where the repo lives

Two hosts, two paths, on purpose:

| Host | Path | Why |
|---|---|---|
| Dev laptop | `~/Projects/_personal/homelab` | Scratch space; stacks come and go |
| `homelab` (server) | `/opt/homelab` | Production. `/opt` is FHS "add-on application software" |

`/opt` is root-owned by default, so the checkout is chowned to the human who
runs compose (`sudo chown r:r /opt/homelab`); otherwise every `git pull` needs
sudo and the tree ends up half root-owned.

Served data -- media libraries, anything a service hands to clients -- goes in
`/srv` (FHS: "site-specific data served by this system"), never in a named
volume. See `stacks/jellyfin` for the shape.

### Keeping the two checkouts in sync

Both checkouts track the same GitHub remote. **The laptop pushes, `homelab` pulls.**

```
# laptop
git push origin main

# homelab
cd /opt/homelab && git pull
```

`homelab` clones this repo over **HTTPS with no credentials**, which a public
repo allows. It cannot push, deliberately: production consumes the repo, it
does not author it. A `git push` from `homelab` asks for a username it does not
have, and that is the correct outcome, not a misconfiguration.

A private checkout, such as the site data in `SITE_DIR`, uses a **read-only
deploy key** instead. Deploy keys are per-repository, so the key grants
nothing else in the account. It has no passphrase, because an unattended
`git pull` cannot answer a prompt; the read-only scope is what limits the
blast radius if the disk is lost.

Pulling does not restart anything. Compose files change on disk only; the
running containers keep their old definition until an explicit
`docker compose up -d`.

If the pull changed a **config file bind-mounted into the container** (a
`Caddyfile`, `init.sql`, an `.xml`), `up -d` is not enough and neither is the
app's own reload. Single-file bind mounts are pinned to the inode they had at
container start, and `git pull` replaces files by rename, so the container goes
on reading the old one — reporting a valid config and a successful reload the
whole time. Recreate it, and confirm the container can see the change:

    docker compose up -d --force-recreate <service>
    docker exec <container> grep -c <new-thing> <path-in-container>

See `docs/lessons-learned.md` §18.

**Never `scp` a single file to `/opt/homelab`.** It works once and then rots:
the deployed tree silently stops matching any commit, which is the same
failure the duplicated Vikunja config had before v2 deleted it. If a file is
worth deploying it is worth committing. `git status` on `homelab` should always
be clean, and anything dirty there is either worth keeping or evidence someone
edited production directly.

#### Fallback: direct push, when GitHub is unreachable

This needs `receive.denyCurrentBranch = updateInstead` on the target.
It is not the default, so set it once before relying on this: `ssh homelab 'git -C /opt/homelab config
receive.denyCurrentBranch updateInstead'`. Then a direct push works without a
remote in the middle:

```
git push ssh://<user>@<HOMELAB_HOST>/opt/homelab main    # on the LAN
git push ssh://<user>@homelab/opt/homelab main           # over the tailnet
```

The target tree must be **clean** or the push aborts rather than discarding
local edits.

> [!IMPORTANT]
> Using this path leaves `homelab` ahead of `origin/main`, and the next
> `git pull` will report divergence. Push the same commits to GitHub as soon
> as it is reachable again.

Nothing in the repo should hardcode either path. `scripts/` resolve the repo
root from `__file__`; systemd units are templates rendered by
`scripts/install-systemd.sh`, because `WorkingDirectory=` expands specifiers
like `%h` but not environment variables.

## Paths

No `/home/<user>` path in any committed file, `.env.example` included. Host paths outside
the repo use a chained default in the compose file:

    ${BACKUP_ARCHIVE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/homelab/backups}

Compose resolves nested defaults, so this works with no `.env` entry and still
takes an override.

The storage rule (named volume, or `:ro` bind mount from `config/`) has one
real exception: `stacks/backup/compose.yaml`'s `/archive` mount. It is a
read-write bind outside the repo, on purpose — archives must survive
`docker volume prune`, so they cannot themselves live in a named volume that
prune would delete. Do not treat this as precedent for other stacks; it is
the backup target, not application state.

### "volume X already exists but was not created by Docker Compose"

Ignore it, and **do not** do what it suggests.

Compose stamps `com.docker.compose.project` on volumes it creates and warns
about any it finds without those labels. On the first server that was 51 of 79 volumes,
every one dating to the original 2026-08-21 deploy. They attach normally and
the data in them is fine; the warning is about provenance, not integrity.

The suggested remedy — `external: true` — means *Compose must never create
this volume, it must already exist*. That is right for a volume deliberately
managed outside Compose and wrong for these. Adding it silences the warning on
the host that already has the volume, and turns every affected stack into a
hard `volume ... not found` failure on a clean one. That host is the
disaster-recovery case, which is the entire point of `docs/recovery.md` and
`scripts/test_recovery_drill.py`. The warning would be traded for a restore
that cannot start.

Genuinely clearing it means `docker compose down -v`, letting Compose recreate
the volume, and restoring from the archive: real downtime and real risk, to
remove a log line. Leave them.

## Dashboard links across hosts

An `href` has exactly two allowed shapes, and
`scripts/test_homepage_links.py` enforces both:

    http://${HOMELAB_HOST:-localhost}:<port>    a published port
    https://<name>.${PUBLIC_DOMAIN}             a Caddy vhost

Prefer the vhost where one exists. It carries TLS, it reads as a name rather
than a port number, and it does not go stale when the published port moves.
"Where one exists" is checked, not trusted: the name must have a host matcher
in `stacks/caddy/config/Caddyfile`. The wildcard site block answers to every
name in the domain, so a typo does not 404 — it reaches `handle { abort }`
and the connection closes with no response, which reads as a network fault.

Both variables are interpolated from the **stack's own `.env`**, never from
the repo-wide one — Compose resolves `${VAR}` in a compose file from the
project directory, and `env_file` only ever hands values to a container. So
the same value is written into each stack that needs it. That is duplication
the repo-wide `.env` would normally exist to prevent, and it is unavoidable
here rather than an oversight — see the carve-out under "The repo-root `.env`
holds this deployment's identity", which this is the one exception to.

**`PUBLIC_DOMAIN` is written `${PUBLIC_DOMAIN:?...}` in these labels**, never
bare and never with a `:-default`. Unset, a bare one renders
`https://jellyfin.` — a valid-looking URL pointing nowhere, on a tile that
looks identical whether its link works or not. The guard moves that failure
to the host doing the deploy. `scripts/test_homepage_links.py` rejects the
`:-` form for the same reason.

**This is a deploy-time value that failed silently for months.** The
parameterization was added early with the note "set `HOMELAB_HOST` once this
moves off the laptop"; the move happened, and on 2026-09-05 thirty of
thirty-four stacks still had it commented out, rendering `http://localhost:<port>`
— a link to whatever machine was viewing the dashboard. Four stacks had set
it, to three different values. Nothing failed, because a tile with a dead link
looks exactly like a tile with a live one.

The value to use is the **LAN IP**, not `homelab` and not the tailnet address.
It is the only one that both resolves and routes in all four client states
(on or off the LAN, tailnet up or down); the two tailnet names go dark the moment Tailscale
drops, including on the same wifi as the server. Check what is deployed with:

    ssh homelab 'grep -h "^HOMELAB_HOST" /opt/homelab/stacks/*/.env | sort | uniq -c'

One line of output means the stacks agree. More than one means they do not.

## After changing ports

    uv run scripts/ports.py

## After adding a stateful stack

    uv run scripts/check_backups.py

Checks that every named volume declared across `stacks/*/compose.yaml` (other
than `stacks/backup` itself) is also registered as a backup source in
`stacks/backup/compose.yaml`. The `docker-volume-backup.stop-during-backup`
label only pauses a container during backup — it says nothing about whether
the volume is actually archived, and getting that wrong fails silently until
someone needs a restore. Exits non-zero if anything is declared but
unregistered.

Registering the volume is only half of it. **Recreate the backup container**
afterwards, or it keeps the mount list it was created with and archives the
old set while reporting success:

    cd stacks/backup && docker compose up -d --force-recreate

`check_backups.py` catches this too, but only when run **on the backup host** —
it compares the running container's actual mounts against the registry, and
skips that half (exit 0) anywhere the container does not exist.

If the stack stores SQLite, give it a `docker-volume-backup.stop-during-backup`
label — and note that the label does not imply a checkpointed WAL, so restores
must take the whole directory including `-wal` and `-shm`. See
`docs/recovery.md` §5 and `docs/lessons-learned.md` §15.

## Config files outside the allowlist

`.gitignore` allowlists tracked file types (`*.yaml`, `*.md`, `*.py`, `*.sh`,
`*.toml`, `.env.example`, `.gitignore`); everything else is ignored by
default. If a stack's config is a format not on that list (a `Caddyfile`,
`nginx.conf`, `Dockerfile`, `init.sql`, `app.ini`, ...), it will silently fail
to be tracked — the stack looks complete locally and arrives on a server
missing that file. Add an explicit `!path/to/file` line to `.gitignore` for
it, and confirm with `git status --ignored` when you create the stack, not
after something breaks.

- **`scripts/status.py` is the inventory**, and `docs/ports.md` the port
  registry. Neither is hand-maintained; both are regenerated.
- **`*.service` and `*.timer` are allowlisted** in `.gitignore` for the status
  timer. Any other new config extension needs its own `!` line — the allowlist
  fails closed and will otherwise drop the file silently.

## `lifecycle` describes reality, not readiness

`x-homelab.lifecycle` is read by `scripts/status.py`, which alerts on a
`production` stack that has run on this host and is now down. So the field is
an assertion about what is deployed, not a rating of how finished the compose
file looks. Promote a stack to `production` when it is actually running and
meant to stay running, and not before.

Getting this backwards is quiet in the worst way. `lgtm` was labelled
`production` for months having never once run on the server; the label was
believed, and the discrepancy only surfaced on 2026-09-06 when a
`docker compose up -d` across every stack pulled all five of its images --
which cannot happen if they were already there. A `production` stack that is
permanently down either alerts forever or teaches you to ignore the channel
that reports real outages.

`planned` and `developing` are both expected to be down and neither alerts.
Prefer them; the cost of under-claiming is nothing.

**The corresponding trap is starting stacks by accident.** `for d in stacks/*/;
do docker compose ... up -d; done` starts *every* stack in the repo, including
the two dozen marked `planned`. On 2026-09-06 that took the server from 19
containers to 70 and pulled ~55 GB of images onto a machine chosen for idling
at ~10 W. Nothing in the repo prevents it -- `lifecycle` is metadata, not a
guard -- so scope the loop to a list you have read, and check `docker ps -q |
wc -l` before and after.

It leaves a second mark worth knowing about: `status.py` records
`first_observed_running` the first time it sees a stack up, and that timestamp
is what scopes alerts to this host. A stack started by accident is
indistinguishable afterwards from one deployed on purpose, so it will alert
the day someone promotes it to `production`. The state file is
`${XDG_STATE_HOME:-~/.local/state}/homelab/status-<host>.json`; entries can be
cleared by hand, and the date they were written is usually enough to tell the
accidents apart.
