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

**The consume folder is a named volume** for now; documents come in by web
or phone upload. A scanner needs it as a share it can write to — not wired
up until the scanner model is known. The likely shape is a bind mount:

```yaml
      - /srv/scans:/usr/src/paperless/consume
```

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
