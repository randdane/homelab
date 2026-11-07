# mealier

A Rust rewrite of Mealie with a batch log (each time a recipe is made, with the
amounts actually used). Source and image: `github.com/<owner>/mealier`,
`ghcr.io/<owner>/mealier`. On trial beside `stacks/mealie`; Mealie keeps its
vhost until cutover.

- URL: `https://mealier.<PUBLIC_DOMAIN>` (gated vhost, LAN and tailnet only)
- Port: `127.0.0.1:9926` on the host, for the CLI and checks
- Auth: native OIDC with Authentik (groups `mealier-users`, `mealier-admins`),
  plus a break-glass local admin
- Data: one volume, `mealier_data` (SQLite, images, assets), in the nightly
  volume backup

## Setup

1. `.env` from `.env.example`, `chmod 600 .env`. `MEALIER_TAG` is a full commit
   sha that CI pushed to GHCR.
2. Local admin hash:
   `docker run --rm -i $MEALIER_IMAGE:$MEALIER_TAG hash-password`,
   then set `MEALIER_ADMIN_PASSWORD_HASH` and
   `MEALIER_ADMIN_PASSWORD_FILE=/run/secrets/admin_password_hash`.
3. Import Mealie **before anyone signs in** (the importer refuses a database
   with users other than the local admin):
   ```bash
   docker compose run --rm -v /path/to/backup.zip:/import.zip:ro mealier import-mealie /import.zip
   ```
4. `docker compose up -d`.

## What will bite you

- **An in-app restore exits the process on purpose** so the container restarts
  on the new data; `restart: unless-stopped` is what brings it back.
- **Sablier stops it when idle**, so nothing inside runs on a timer. Scheduled
  webhooks only fire if something runs `mealier run-scheduled`; nothing does.
- **`edge` only, no project network.** `homelab` is close to Docker's
  address-pool ceiling (`docs/lessons-learned.md` §11).
- **Mealie keeps three backups on its server** and deletes the oldest on each
  new one. Copy a backup off the server before making more.

## Authentik

Native OIDC. Provider and application `mealier` (confidential,
`authorization_code` + `refresh_token`, strict redirect `/auth/callback`, the
same flows, signing key and scope mappings as `mealie`'s provider); the
application is bound to `mealier-users`. `groups` reaches the ID token through
the stock `profile` mapping. Email is trusted from Authentik even though it sends
`email_verified: false`.

## Measured (2026-11-06, first deploy)

- Idle memory 2.2 MiB (`docker stats`), against Mealie's 220–450 MB. Image
  43.8 MB uncompressed.
- Trial import of a Mealie v3.28 backup: 2 groups, 2 households, 4 recipes,
  2691 foods, every other mapped table present; skipped items listed in the
  report.
- SSO tested in an incognito window: Authentik logged a `/application/o/token/`
  200 from mealier's own container address, and the imported user was linked by
  email (one account, not two). Local admin login also works.
- No Uptime Kuma monitor: Sablier stops it when idle, and `check_monitors.py`
  skips Sablier stacks.

## What broke on the way

- A new named volume was root's and the nonroot process could not create its
  database. Fixed in the image (it now ships `/data` owned by 65532); the first
  volume was chowned by hand once.

## Unverified

- The vhost from the phone (not phone-probed during the trial).
- The first nightly archive containing `mealier_data`. The backup container
  mounts it since 2026-11-07 (`check_backups.py` clean on `homelab`).
