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

## Unverified

- Everything below the image: first deploy, import of the trial backup, OIDC
  against the real Authentik, the vhost from the phone.
