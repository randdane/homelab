# Authentik

**What:** Identity provider — single sign-on, with a forward-auth endpoint
Caddy can call before letting a request through.
**Why I care:** It is what stands between the public internet and anything
shared out of the house.
**URL:** http://localhost:9000 (initial setup at `/if/flow/initial-setup/`)

## Notes

**`lifecycle: production` since 2026-09-03.** It was `developing` on the
stated condition "promote it only once Caddy is calling it" — a
half-configured identity provider that other stacks *believe* is protecting
them is worse than none. Caddy has been calling it for a while:
`authentik.${PUBLIC_DOMAIN}` proxies here, and Jellyfin authenticates
against this LDAP outpost. The condition was met and the field was simply
never updated, which meant `status.py` did not alert on the identity provider
in front of the only publicly-reachable service in the house.

**Adapted from [upstream](https://goauthentik.io/docker-compose.yml), tag
2026.8.0.** Two differences from the legacy definition:

- It pinned `2025.4.0` and ran a **Redis** container. Upstream has dropped
  Redis; it is deliberately absent rather than forgotten.
- Its Postgres, media, certs, and Redis data were **bind mounts into the repo
  directory** (`./db`, `./media`, `./redis-data`, `./certs`), which puts live
  database files inside version control. All are named volumes now.

**The worker does not get the Docker socket.** Upstream mounts it so the
worker can manage outpost containers, which makes the worker host-root — and
this is the stack Caddy exposes to the internet, so it was the worst place in
the repo to keep that. The socket buys nothing here: the LDAP outpost is
declared in `compose.yaml` with `service_connection=None`, so the worker was
never managing anything. Verified against the live database before removing —
both outposts had `service_connection_id` NULL.

The worker still runs as root *inside* the container, which is upstream's
default and a much smaller thing once there is no socket to reach the host
through. Do not publish this stack's ports directly to the internet; put
Caddy in front.

If an outpost ever needs managing, declare it in `compose.yaml` like `ldap`,
or give the worker a scoped socket proxy (`stacks/dockge` has the pattern).
A `:ro` socket mount is not a mitigation — Docker API requests are operations,
not file writes.

**`AUTHENTIK_SECRET_KEY` must be stable.** It signs sessions and encrypts
stored provider secrets. Changing it invalidates both — you will be logged out
and some configured providers will stop working.

**Everything is in Postgres.** Users, groups, applications, flows, and
provider configuration. `database` is the volume that matters; `data`,
`certs`, and `custom-templates` hold uploaded media, issued certificates, and
branding.

**Bootstrap is manual and easy to forget:** the initial admin account is
created by visiting `/if/flow/initial-setup/` after first start. Until you do,
the instance is unconfigured and open.

**Never in Watchtower's scope.** Authentik migrates its schema on upgrade and
ships breaking changes between releases.

## Postgres

**Running Postgres 18** (`postgres:18-alpine`); authentik supports 14–18.

**The 18+ images changed where data lives.** `PGDATA` is
`/var/lib/postgresql/18/docker` and the declared volume is `/var/lib/postgresql`
— one level up from the 16-and-earlier `/var/lib/postgresql/data`. The compose
mount reflects that. Point an 18 image at a 16-shaped mount and it refuses to
start with an explicit error rather than touching the data, which is the only
forgiving thing about this.

**Major versions do not read each other's data directory.** There is no
"bump the tag" upgrade. 16 → 18 was done by dumping from the old server and
restoring into a freshly initialised new one:

    docker compose stop server worker ldap        # quiesce writers
    docker exec authentik-postgres pg_dump -U authentik -d authentik -Fc > authentik.dump
    docker compose down
    # copy the old volume aside, then remove it so 18 initdb's fresh
    docker compose up -d postgresql
    docker exec -i authentik-postgres pg_restore -U authentik -d authentik --no-owner --exit-on-error < authentik.dump
    docker compose up -d

`POSTGRES_PASSWORD` comes from the same `PG_PASS`, so the recreated role keeps
the password the app already has — nothing in `.env` changes.

**Rehearse the restore before removing anything.** `pg_dump` takes an MVCC
snapshot, so it can be taken from the live server and replayed into a throwaway
18 container with the real stack still running. That rehearsal is what turns
the cutover from a hope into a repeat.

**Waiting on `pg_isready` during first start is a false green.** The entrypoint
runs a temporary local-only server while it initialises, which answers
`pg_isready` and is then shut down. Wait for `PostgreSQL init process complete`
in the logs first.
