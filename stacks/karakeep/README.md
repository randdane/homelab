# Karakeep

**What:** Bookmark manager that saves a full snapshot of every page, indexes
the text, and organises it with lists and tags.
**Why I care:** A bookmark is a promise someone else has to keep. A snapshot
still works when the site is paywalled, rewritten, or gone.
**URL:** https://karakeep.${PUBLIC_DOMAIN} (gated to LAN and tailnet);
`http://<HOMELAB_HOST>:3006` is the break-glass path if Caddy is down.

## Notes

**The legacy definition pointed at an image that no longer exists.**
`gcr.io/zenika-hub/alpine-chrome:123` fails to pull. Karakeep would have run
and looked healthy while every archive attempt failed — the app works without
Chrome, it just silently stops preserving anything, which is the entire point
of using it. Upstream now ships `ghcr.io/karakeep-app/karakeep-chrome`.

Its Meilisearch was also `v1.13.3` against upstream's current `v1.41.0`.

**Three services, and each does something distinct:** `karakeep` is the app,
`chrome` renders and snapshots pages, `meilisearch` makes the archived text
searchable. Dropping Chrome or Meilisearch leaves an app that starts fine and
quietly does less than you think.

**`MEILI_MASTER_KEY` must match in both services.** It is passed to Meilisearch
as its key and to Karakeep as the client credential; a mismatch shows up as
search silently returning nothing rather than as an error.

**`NEXTAUTH_SECRET` signs session cookies.** Changing it logs everyone out.

**Set `DISABLE_SIGNUPS=true` once your account exists**, otherwise anyone
reaching the port can register.

**`data` is backed up; the search index is not.** Meilisearch's index is
derived from `data` and rebuilds on demand — archiving it would roughly double
the nightly tarball for something regenerable.

**The version is a rolling `release` tag**, not semver — that is upstream's
stable channel, so this one cannot be pinned to a number the way the other
stateful stacks are. Read the release notes before pulling.

**Verified 2026-09-18.** The first bookmark crawled in ~6 s:
`crawlStatus` success, screenshot, HTML and banner image stored as assets, and
one document in Meilisearch's `bookmarks` index, so Chrome, Meilisearch and the
shared key all work. The PDF is skipped as empty, which is upstream's default.

**Log in at the vhost, not `:3006`.** `NEXTAUTH_URL` is `https://`, so the
session cookie is marked secure and a browser drops it over plain HTTP: the
password is accepted and the login silently fails. Sign-up still works there,
which makes it look like a wrong password.

**Promoted to `production` 2026-09-29**, after eleven days in use. The same
day the phone's vhost probe was found never to have covered it, so it was
added then; its first probe answered 200.

**SSO through Authentik since 2026-09-29.** Application `karakeep`, group
`karakeep-users`. `OAUTH_ALLOW_DANGEROUS_EMAIL_ACCOUNT_LINKING=true` links
the first SSO login to the existing account by email; it worked first time.
Password login stays. The "custom" provider in Karakeep's UI is Authentik.
