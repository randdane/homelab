# life-queue

**What:** Vikunja for tasks plus an n8n flow that turns Vikunja reminders into
phone notifications through ntfy.
**Why I care:** One queue for everything — errands, health, money, homelab,
projects. The measure of success is that the weekly review keeps happening,
not the task count.
**URL:** `https://vikunja.${PUBLIC_DOMAIN}`. Break-glass when Caddy is down: port 3456 is
bound to the host's loopback only, so tunnel to it with
`ssh -L 3456:127.0.0.1:3456 <host>` and browse `http://localhost:3456`.

**Reminders go through ntfy**, since 2026-09-13: an HTTP Request node named
`Notify ntfy` posts JSON to `$env.NTFY_URL` with the Vikunja link as `click`.
The credentials come from `stacks/ntfy/.env` via `env_file`, so no secret is
stored inside n8n. It replaced a node that called Home Assistant's
`notify` service.

Verified with two real reminders on 2026-09-13 — ntfy accepted both and
returned their message ids, and both arrived on the phone from the ntfy app:

| Reminder | ntfy id | `click` |
|---|---|---|
| a genuine task reminder at 12:00 | `nqMPi9szJb2S` | `…/tasks/39` |
| a test task at 12:17 | `HQtgd2bNGnEK` | `…/tasks/42` |

The tap opened the right task. The page itself did not load, for an unrelated
reason worth knowing: with "Use Tailscale DNS" on, the phone resolved **no**
names at all — public or tailnet — so a tap on a correct link hung on the
name, never reaching an address. The LAN address itself is reachable and
the vhost's name resolves to it correctly; the failure was resolution,
not routing. The same fault made the phone's vhost probe fail — its guard is
a literal IP and passed, its four probes are names and timed out — and kept
the phone's push monitor red in Uptime Kuma. The fix was turning "Use
Tailscale DNS" off on the phone: Android cannot split DNS by domain for a
VPN, so a silent MagicDNS responder takes every name down with it. So a tap that lands on the correct URL and then hangs
is that bug, not this one.

An earlier version of this paragraph said the phone "cannot reach
the server's LAN address". That was wrong, and wrong in a costly direction: it points a
reader at routing and the subnet route when the setting that matters is the
phone's DNS.

**Delivery depends on the phone's tailnet, entirely.** The ntfy app's server
is the server's tailnet address on :2586, a tailnet-only address, so with Tailscale off the
phone receives nothing even on the home wifi. Messages are not lost —
`NTFY_CACHE_DURATION` is 72h and the backlog flushes on reconnect, measured
2026-09-13: the phone was off the tailnet overnight, the 07:15 digest could
not be delivered, the 09:00 `no homelab digest today` watchdog fired
correctly, and everything arrived at 10:53:58 when the phone was picked up.

**Do not test this with n8n's "Execute step" or a manual run.** It publishes a
real message to the live topic with `$json` undefined — one such run on
2026-09-13 sent the body `triggered` with a `click` of `…/tasks/undefined`.
Fire an actual reminder instead.

Design doc lives in the `life-queue` repo, which also keeps the import and
restore-verification scripts. This directory is the only deployment
definition — there is deliberately no second copy to drift from.

## Before the first start

`N8N_ENCRYPTION_KEY` must be in `.env` **before n8n ever starts**. n8n
encrypts stored credentials with it, and changing it later orphans every one
with no recovery. Migrating an existing volume means carrying the *old* key
across, not generating a new one.

`VIKUNJA_SERVICE_SECRET` likewise: unset means a fresh random JWT secret on
every boot, which logs out every session on every restart.

## The n8n login, and how to get back in

n8n's owner account is **not** in `.env` or anywhere in this repo — it lives
in the n8n database as a bcrypt hash, so it cannot be read back out. The
credential is kept in **Bitwarden** (the hosted one; `stacks/vaultwarden` is
`lifecycle: planned` and has never been deployed, so nothing is there). The
account is the owner email, MFA off as of 2026-09-13.

If it is ever lost, reset it rather than hunting:

```bash
ssh homelab 'docker exec life-queue-n8n n8n user-management:reset'
```

Then reload n8n (loopback-only, no vhost: `ssh -L 5679:127.0.0.1:5679 homelab`, then `http://localhost:5679`) and create the owner account again.

**Measured 2026-09-13, because "resets the database to the default user
state" reads alarmingly:** the reset cost nothing but the login. The
`life-queue reminders` workflow came through intact and still active, all
five nodes present, and **711 execution records survived**. That holds
because this stack deliberately passes every secret as `$env` rather than
storing n8n credential objects — `credentials_entity` was empty, so there was
nothing for the reset to orphan. A stack that *did* use n8n credentials would
lose them, and `N8N_ENCRYPTION_KEY` would not save it.

**That empty table is a property to keep, not a fact to lean on.** An HTTP
Request node's **Authentication** dropdown offers *Generic Credential Type →
Bearer Auth*, and picking it writes a row into the encrypted credential store
that survives switching the node back to *None* — orphaned, referenced by
nothing. One appeared exactly that way on 2026-09-13 (`Bearer Auth account`,
type `httpBearerAuth`) while wiring the ntfy node, and was deleted. Leave
Authentication on **None** and carry the token in a header as
`Bearer {{$env.NTFY_TOKEN}}`.

Back up the volume first anyway if there is time; it is one `docker run`
against `life-queue_n8n-data`.

## Reading n8n's database from outside

n8n runs SQLite in **WAL mode**, so `database.sqlite` on its own is stale:
recent writes sit in `database.sqlite-wal` until a checkpoint. Copying just
that one file returns old data with no error and no warning — on 2026-09-13 it
made a correctly saved, correctly wired workflow look unsaved, unwired and
missing its headers. Copy all three files so SQLite replays the WAL on open:

```bash
ssh homelab 'D=$(mktemp -d); for f in database.sqlite database.sqlite-wal database.sqlite-shm; do
  docker cp "life-queue-n8n:/home/node/.n8n/$f" "$D/$f"; done; echo "$D"'
```

Read it with Python's `sqlite3` through `uv run` — neither the n8n image nor
`homelab` has the `sqlite3` CLI. Workflows live in `workflow_entity` with
`nodes` and `connections` as JSON text; a node's wiring is only in
`connections`, so a node can look perfectly configured and still never run.

## Reminders

Two paths into the same n8n workflow, and both are needed:

1. **Vikunja webhook** — fires once when a reminder comes due. Vikunja sends
   it exactly once and **never retries**, so a reminder that fires while n8n
   is restarting is gone.
2. **A 30-minute overdue poll** — the safety net for exactly that. Not
   hardening; without it, missed reminders are silent.

Webhooks are registered **per project**, so a new project has no reminders
until they are registered for it. Re-run `scripts/import.py webhooks` in the
life-queue repo after adding one.

## Gotchas

- **`outgoingrequests.allownonroutableips: true`** in `config/config.yaml` is
  load-bearing. Without it Vikunja refuses every webhook to `http://n8n:5678`
  as an SSRF attempt, before sending, with no error the UI shows you.
- **`POST /api/v1/tasks/{id}` replaces the whole object**, it does not patch.
  Sending `{"id":1,"done":true}` zeroes `repeat_after` and a recurring task
  silently stops recurring. GET, modify, POST the whole thing back.

  **Use `vikunja.py` and this cannot happen to you:**

  ```python
  import sys; sys.path.insert(0, "/opt/homelab/stacks/life-queue")
  import vikunja
  vikunja.patch(22, done=True)      # reads first, preserves everything else
  ```

  `vikunja.call()` refuses a partial `POST` to `/tasks/{id}` before it reaches
  the network, so the destructive request cannot be sent by accident. Run
  `python3 vikunja.py` for its self-check.

  This bullet existed, in these words, on 2026-09-03 when a session that had
  read it closed task #14 with a bare `POST` and destroyed an 881-character
  description. That is why the guard is code now and not a fourth sentence
  here.
- **The list-all endpoint is `GET /api/v1/tasks`**, not `/tasks/all` — that
  path does not exist in 2.5.0 and returns a JSON *object* with HTTP 400, so
  a naive `len()` over the response reports "2" rather than failing.
- **First start on a fresh volume fails** with `mkdir /db/files: permission
  denied`: a new named volume is root-owned and Vikunja runs as uid 1000.
  Fix once, before starting:
  `docker run --rm -v life-queue_vikunja-data:/db alpine chown -R 1000:0 /db`
- **Phone access depends on the tailnet.** If headscale is down, reminders
  still fire but links resolve to an address the phone cannot reach; the LAN
  address is the break-glass path.

## Restore

Both volumes or neither — Vikunja without n8n is a task list that never
notifies you. `scripts/verify-restore.sh` in the life-queue repo restores a
backup into throwaway containers and asserts task count and workflow count.

## The hostname

`vikunja.${PUBLIC_DOMAIN}` is a public *name* with a private *answer* — it
resolves to the server's LAN address for anyone, and nobody outside can
reach that. What keeps it private is in two files:

1. `stacks/caddy/config/Caddyfile` — a `remote_ip` gate on the vhost. This is
   the part that actually matters: Caddy's :80 is internet-facing for the
   public vhosts, so without the gate anyone could reach this by sending the
   right `Host` header. Unguessability is not a control.
2. `compose.yaml` — this stack joins the `edge` network so Caddy can resolve
   `life-queue-app` by name.

`tasks.<base_domain>` (MagicDNS) was the earlier name and was retired 2026-09-09, along
with its MagicDNS A record. It resolved only while Tailscale was up, which is
exactly when a phone most often could not use it.

Remove any one of the three and the service either breaks or quietly becomes
reachable from the internet. The vhost is plain `http://` on purpose: issuing
a certificate would publish the hostname to Certificate Transparency logs.

**`VIKUNJA_PUBLIC_URL` must match the hostname people actually use.** It is
the base for every link Vikunja generates, including the reminder deep links
n8n sends through ntfy. Point it somewhere unreachable and the
notifications still arrive — they just lead nowhere, which is exactly how this
failed before.

## SSO through Authentik

Since 2026-09-29: application `vikunja`, group `vikunja-users`, provider
block `authentik` in `config/config.yaml`, client ID and secret from
`.env`. Password login stays.

**Linking needs `email_verified: true`, and Authentik's stock email mapping
sends `false`.** Vikunja 2.5.0 applies `emailfallback` only when that claim
is true (`fallbackSearchUsers` in `pkg/modules/auth/openid/openid.go`); with
it false, the first SSO login silently created a second, empty user with
the same address instead of linking. The provider now uses the custom
Authentik mapping **`homelab: email (admin-verified)`** in place of the
stock one. That assertion is honest only because every Authentik account is
made by an admin.

A wrongly created SSO user must be deleted, not just the mapping fixed:
Vikunja matches issuer+subject before trying the email fallback, so it keeps
logging into the stray account. `vikunja user delete <id> --now --confirm`.
