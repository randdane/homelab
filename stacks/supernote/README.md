# Supernote Private Cloud

**What:** Ratta's self-hosted sync server for Supernote e-ink tablets. Notes
written on the tablet sync here; PDFs and documents added here appear on the
tablet. A web portal shows previews of notes.
**Why I care:** Handwritten notes otherwise live only on the tablet, or on
Ratta's cloud. Here they land in the nightly volume archive and in the
weekly backup of the whole VM.
**URL:** `https://supernote.<PUBLIC_DOMAIN>` (gated vhost).
`127.0.0.1:19072` on `homelab` is break-glass over ssh.

**Authentik:** neither OIDC nor forward-auth. The tablet's sync client
cannot follow a forward-auth redirect, and the app has no OIDC. The web
portal has its own TOTP (since 26.05.26); turn it on.

## Sources

Adapted from Ratta's compose file,
`https://supernote-private-cloud.supernote.com/docker-deploy/docker-compose.yml`,
and the manual-deployment PDF. `supernotedb.sql` is
`https://supernote-private-cloud.supernote.com/cloud/supernotedb.sql`,
downloaded 2026-10-23 (sha256 `918ea59b…7fe66e4c`). It is schema only: no
rows and no credentials. Changelog:
`ratta.helpjuice.com/supernote-private-cloud-changelog`.

## First run

1. `.env` from `.env.example`, secrets generated as it says, `chmod 600`.
2. `docker compose up -d`. MariaDB runs `supernotedb.sql` on its first
   start against an empty `db` volume.
3. On the vhost: turn registration **on**, create your account, turn it
   **off** (the setting persists across restarts), then enable TOTP and keep
   the recovery codes in the vault.
4. On the tablet: Settings → Sync → Private Cloud, server
   `https://supernote.<PUBLIC_DOMAIN>`.

## Auto-sync

Manual sync uses the HTTP port. Auto-sync is a WebSocket that Ratta serves
on 18072, which they say cannot be changed. 25.12.17 added "auto sync over
HTTPS in reverse proxy environments", so it may work through Caddy on 443.
**Unverified.** If it does not, publish `18072:18072` and allow it in
`pve/firewall/101.fw` from the tablet's address only.

## What will bite you

- **The images pull ~800 MB** between them.
- **Container names don't matter here:** the service reaches its
  neighbours by service name (`mariadb`, `redis`, `notelib`). Its jar falls
  back to those names **and to built-in passwords** (`enote123`,
  `ratta@2024..`) when a variable is missing, which is why compose sets every
  one explicitly.
- **MariaDB stays on 10.6.24.** Ratta warns against upgrading it. A
  schema update comes as a new `supernotedb.sql`, applied by hand:
  `docker exec -i supernote-mariadb mysql -u supernote -p supernotedb < supernotedb.sql`.
  The 2026-10-23 copy is all `CREATE TABLE IF NOT EXISTS` with no `DROP`,
  so re-running it is safe. Check a new copy for `DROP` before applying it.
- **Upgrading the app:** bump `service` (date tags, e.g. `26.10.09`) and
  `notelib` (its own `6.9.x` series), `docker compose up -d`.
- **`notelib` has no healthcheck.** Its command ends in `tail -f /dev/null`,
  so the container stays up even if the converter dies. Symptom: note
  previews fail in the portal.
- **Restore `db` and `data` together.** The database indexes the files; one
  without the other is an inconsistent cloud.

## Unverified

- Auto-sync through Caddy (above).
- RAM use; measure with `docker stats --no-stream` after first sync.
- That a server address with no port is accepted by the tablet.
