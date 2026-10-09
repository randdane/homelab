# Mealie

**What:** Recipe manager — imports a recipe from a URL, strips the life story,
keeps the ingredients. Meal planning and a shopping list on top.
**Why I care:** Recipe sites vanish, get paywalled, or rewrite their pages.
An imported recipe is a local copy.
**URL:** https://mealie.${PUBLIC_DOMAIN} (gated: LAN and tailnet only). Port
`:9925` is bound to the host's loopback only; the break-glass path is
`ssh -L 9925:127.0.0.1:9925 <host>`.

**First login is a known default:** `changeme@example.com` / `MyPassword`.
Anyone on the LAN can use it until you change it, so change the email and
password on first login. `ALLOW_SIGNUP` stays `false`.

Verified 2026-09-29: login, then an import by URL (allrecipes)
came back with 11 ingredients, 9 steps and the image, at about 440 MiB of
the 1000 MiB limit.

Deployed on `homelab` and promoted to `production` the same day,
2026-09-29: the vhost answered the phone's probe with 200, backups include
`mealie_data`, and the Uptime Kuma monitor is recording heartbeats.

## Notes

**Two settings corrected from the legacy definition:**

- `ALLOW_SIGNUP` was `true` — anyone who reached the port could create an
  account. Now defaults to `false`; turn it on just long enough to create
  yours, then set it back and recreate.
- `BASE_URL` was `https://localhost:9925`. The scheme was wrong, which breaks
  generated links and image URLs in exported recipes.

**SSO through Authentik since 2026-09-29.** Application `mealie`, OAuth2
provider `mealie` (confidential, implicit consent), bound to group
`mealie-users`; `mealie-admins` makes you admin in Mealie on every login.
Client ID and secret live only in `homelab`'s `.env`. The old legacy
secret was never reused. Password login is kept deliberately (no
auto-redirect): it is the way in when Authentik is down. First SSO login
matched the existing `r` account by email rather than creating a second.

**The Android app Ghee** signs in through Authentik too (since 2026-10-09).
It needs Mealie v3.23.0 or newer for `/api/auth/oauth/native/config`;
older versions leave it stuck on "Completing login". The provider's
redirect URIs also include `ghee://oauth/callback` (strict match), set
through the API, so it lives in Authentik's database, not in this repo.
Set Ghee's server URL with `https://`: Caddy answers `http` with a 308,
and Ghee doesn't follow it for the token POST ("Authentication failed
(308)"). If the browser stops on a blank or loading Authentik page after
the password, close it and tap the login button again: with the session
already open, the redirect back to the app goes through.

Three things broke on the way, each with a misleading symptom:

- **Mealie's backend could not reach Authentik at all.** It fetches
  discovery and exchanges the code from its `edge` address, which the
  authentik vhost's allowlist closes (status 0). Fixed by
  `@authentik_oidc_internal` in the Caddyfile: `edge` minus its gateway,
  `/application/o/*` only. Any future OIDC client on `edge` gets this free.
- **"The request is otherwise malformed"** from Authentik: a provider
  created through the API has `grant_types: []`, which allows no flow. The
  web UI fills it in; the API does not. Set `authorization_code` and
  `refresh_token`.
- **"Invalid Credentials"** in Mealie, with the token exchange succeeding:
  Authentik's managed email mapping returns `email_verified: False`
  unconditionally, and Mealie refuses that by default. Hence
  `OIDC_REQUIRES_EMAIL_VERIFICATION=false`, safe only because every
  Authentik account is admin-made and gated by the group binding.

**The memory limit is intentional.** Mealie's importer will happily consume
more than a gigabyte parsing a badly-built recipe page. The limit plus
`MAX_WORKERS=1` keeps it from taking the host down with it.

**SQLite, so the backup label matters.** An archive taken mid-write is a
corrupt archive. The `data` volume holds recipes, uploaded images, and the
database together.
