# Forgejo

**What:** Self-hosted git, backed by Postgres. Replaces the `gitea` stack.
**Why I care:** A remote I own, on hardware I own, that the existing nightly
backup already covers. The Obsidian vault lives here.
**URL:** http://localhost:3002 (SSH on 3003)

## Why this replaced Gitea

Gitea was never deployed — it sat at `lifecycle: planned` from the day it was
written. Its stated reason ("this repo exists on exactly one laptop with no
remote") had also gone stale: the repo has had a GitHub remote for a while.

So the purpose changed. This is not about *having* a remote; it is about having
one that is self-hosted and inside the backup system, which is what makes it
the right home for the vault.

## `lifecycle: production` since 2026-09-03

It was `developing`, which in this repo means "expected to be down" —
`status.py` alerts only on `production`, so a stack marked `developing` is
silently exempt. Forgejo has run continuously since 2026-08-28 , holds
`data: precious`, and was not in Uptime Kuma either: 24/7 operation, precious
data, and zero monitoring is the worst available combination, and the
lifecycle field was the reason.

What it is *not* is page-worthy in the way Caddy is. If Forgejo is down,
Obsidian Sync still replicates the vault and GitHub still has this repo — this
is the copy you want when those fail, not the one anything depends on
minute-to-minute. `production` here means "tell me", not "wake me".

It still has no Uptime Kuma monitor. Adding one is a live change to that
stack's database, so it is a separate decision.

## The two things that are easy to get wrong

**The installer will silently choose SQLite.** Without the
`FORGEJO__database__*` variables, first run ignores the Postgres container
beside it, sets itself up on SQLite, and there is no way back once install
completes. This bit the Gitea stack's original compose file and the fix is
inherited here.

**The web installer is not reproducible.** `INSTALL_LOCK=true` skips it
entirely: the schema is created by the startup migration (130 tables), and the
admin account is made from the CLI. Leaving the installer unlocked also means
whoever reaches the port first defines the admin account.

```bash
docker exec -u git forgejo forgejo admin user create \
  --admin --username <you> --email <you@example> --random-password
```

Store that password in Vaultwarden and change it on first login.

## `FORGEJO_ROOT_URL` must be set on the server

Clone URLs are built from it. Left unset it defaults to `localhost:3002`, and
every clone command the web UI offers points at the wrong host — which looks
like it works, from the machine running the container, and nowhere else.

On `homelab` it is the **gated vhost** (since 2026-09-29):

```
FORGEJO_ROOT_URL=https://forgejo.example.com/
```

HTTPS, the same `remote_ip` gate as every internal vhost, and the address
the Authentik SSO redirect is registered against. It replaced
`http://<HOMELAB_HOST>:3002/` when SSO arrived: browsers upgrade a bare
address to HTTPS, which that port does not speak, so the login page was
unreachable from a browser while `curl` got it fine.

The published port stays. Remotes already pointing at
`http://<HOMELAB_HOST>:3002/` keep working, and it is the way in when Caddy is
down. SSH clone URLs are built from `HOMELAB_HOST` (`<HOMELAB_HOST>:3003`) and
never touch Caddy.

**SSO:** auth source `authentik` (OpenID Connect), created with
`forgejo admin auth add-oauth`, so it lives in the database, not in this
repo. Authentik application `forgejo`, group `forgejo-users`. The first SSO
login asks to link to the existing account once. Password login stays.

## Pushing creates the repo

`ENABLE_PUSH_CREATE_USER=true`, so a `git push` to a repo that does not exist
yet creates it under your own namespace. No web-UI step, no API call first.

```bash
git remote add origin https://forgejo.example.com/<you>/<newrepo>.git
git push -u origin main
```

> [!warning] Without this, a push to a missing repo returns **403, not 404**
> Forgejo will not confirm whether a private repo exists to someone who may not
> be allowed to see it, so "no such repo" and "not yours" are deliberately
> indistinguishable. The result reads like a credentials problem and is not
> one. This cost a debugging session on 2026-08-28.
>
> The tell is the status code: with basic auth, a **wrong password gives 401**
> and a **403 means you authenticated successfully**. If a push 403s, suspect
> the repo, not the password. Confirm with:
>
> ```bash
> docker exec forgejo-postgres psql -U forgejo -d forgejo \
>   -c "select owner_name, name from repository;"
> ```

`DEFAULT_PUSH_CREATE_PRIVATE=true` is pinned in the compose rather than left to
the upstream default. It currently *is* true upstream, but whether a repo
created by an accidental push is private should not depend on somebody else's
default staying put.

Only `_USER` is enabled, not `_ORG` — there are no orgs on this instance.

> [!note] `empty: true` right after a push is a race, not a failure
> Forgejo finishes processing a push in the background, so the API can report
> the repo as empty for a moment after `git push` returns. Re-read it, or check
> `/branches`, before concluding the push failed.

## Verifying it is actually on Postgres

Do not trust a healthy container; healthz passes on SQLite too.

```bash
docker exec forgejo-postgres psql -U forgejo -d forgejo \
  -tAc "select count(*) from information_schema.tables where table_schema='public';"
```

Expect ~130. A `0` means the migration has not run; a `/data/gitea/gitea.db`
file existing means it went to SQLite after all.

## Backups

Both volumes are registered in `stacks/backup/compose.yaml`:
`forgejo_forgejo-data` (repositories) and `forgejo_pg-data` (issues, users,
PRs). Restoring one without the other is half an instance.

## SSO linking

The first attempt showed Forgejo's link page, which wants the **local**
password, rarely used here since git goes over SSH. Now
`ENABLE_AUTO_REGISTRATION` + `ACCOUNT_LINKING=auto`: Forgejo tries to create
the user, finds the email taken, and links instead. Verified 2026-09-29:
`external_login_user` holds `r` ↔ `authentik`.

One symptom that is not a fault: after a failed link attempt, the next
successful login may 404 on `/user/link_account_signin`, a stale
`redirect_to` from the old flow. The login itself succeeded.
