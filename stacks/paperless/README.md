# Paperless-ngx

**What:** Scan paper in, get an OCR'd, tagged, full-text-searchable archive.
Tika and Gotenberg add Office and email documents.
**Why I care:** The paper it replaces is often the only copy — and the reason
to keep paper at all is being able to find it.
**URL:** `https://paperless.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet).
`127.0.0.1:8000` on `homelab` is the break-glass path over ssh.

## Notes

**Two bugs in the legacy definition, either of which stopped it starting:**

- `env_file: docker-compose.env` pointed at a file that does not exist; the
  directory contained `compose.env` (which was empty anyway). All settings are
  now explicit `environment:` entries.
- The `broker` service mounted `redis_data` while the volumes block declared
  `redisdata`. Compose rejects a mount of an undeclared volume.

The Postgres password was also hardcoded to `paperless`.

**`PAPERLESS_SECRET_KEY` is required and has no default here.** Django signs
session cookies with it. A well-known value means anyone can forge a session;
changing it later logs everyone out.

**The consume folder is a named volume**, filled by web or phone upload and
by the scanner over SMB (below).

Files are deleted from it once ingested, which is why it is excluded from
backup along with `export` (a manual dump target) and `redis-data` (the task
queue).

**`data` and `media` are both backed up, plus Postgres.** `media` holds the
document originals; `data` holds the search index and thumbnails; Postgres
holds tags, correspondents, and metadata. All three are needed together.

**Tika is unpinned (`:latest`)** because it is a stateless conversion sidecar
with no on-disk format to migrate. Everything with state here is pinned.

## SSO (Authentik, native OIDC)

django-allauth's OpenID Connect app, enabled by
`PAPERLESS_APPS=allauth.socialaccount.providers.openid_connect` in `.env`
with `OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` from the Authentik provider
`paperless` (group `paperless-users`). Callback:
`https://paperless.<PUBLIC_DOMAIN>/accounts/oidc/authentik/login/callback/`.
Read from the v3.0.5 source (`src/paperless/settings.py`), not memory.

**Accounts are linked by hand, not by email.** Upstream defaults
`PAPERLESS_SOCIALACCOUNT_ALLOW_SIGNUPS` to yes, which would create a fresh
account for anyone Authentik lets through. It is `false` here, so an SSO
login only works for an account that already has Authentik connected:

1. `docker compose exec webserver createsuperuser` (or the first-run prompt)
2. Log in with that password, Profile → Connect new social account → Authentik

That also sidesteps Authentik's stock email mapping sending
`email_verified: false`. Password login stays enabled as break-glass.

Clicking **Authentik** before step 2 gives "We are sorry, but the sign up is
currently closed." That is the guard working, not a fault: no account is
linked yet, and none is created.

Verified 2026-10-05 from an incognito window: Authentik logged a
`/application/o/token/` 200 from `paperless-webserver`'s `edge` address, and
Paperless holds one user (`paperless`) with one `authentik` social account.
Authentik group: `paperless-users`.

## The scanner

The Raven scanner (`devices.md`, `raven`) scans to an SMB share, so scans never
leave the house. The `samba` service (`crazymax/samba`, config in `samba.yml`)
serves one share, `scans`, which is the `consume` volume itself: Paperless
ingests a scan seconds after it lands and deletes it.

Locked to the scanner at three layers, each proved alone:

- **Samba:** `hosts allow` is `SCANNER_IP` only; one user, `raven`; guest off,
  unknown users not mapped to guest; SMB1 refused (`server min protocol =
  SMB2`); the share is not listed. The image defaults to the opposite on
  several of these (guest ok, every RFC1918 range); `samba.yml` and the
  compose environment close each.
- **`101.fw`:** tcp 445 from the scanner's address only.
- **`dream`:** the `iot` VLAN reaches `homelab` on 445 from the scanner only.

445 is published on `SAMBA_BIND_IP` (`homelab`'s LAN address), not 0.0.0.0,
so the tailnet address is not a way in. Unset, it binds 127.0.0.1, which is
what the laptop wants.

**Password:** `~/.local/share/_r_/paperless-samba/raven_password` on the host
(dir 700, file 600, no trailing newline), mounted at `/run/secrets`. The same
password goes into the scanner's Network destination. Changing it: edit the
file, `docker compose up -d --force-recreate samba`, update the scanner.

**Files are owned by `PUID:PGID`** (`force user = raven`, with `raven` created
at Paperless's uid), so Paperless can delete them after ingesting.

**Check the generated config:** `docker exec paperless-samba testparm -s`.
