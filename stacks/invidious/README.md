# Invidious

**What:** A privacy-respecting front end for YouTube — no ads, no tracking,
no account, and an RSS feed for any channel.
**Why I care:** Watching a linked video without being logged in or profiled.
**URL:** http://localhost:3005

## Notes

**`lifecycle: developing` is a warning, not a placeholder.** YouTube actively
breaks third-party clients, and Invidious has periods where video playback
simply does not work. Expect to update it or wait for upstream. If that
annoys you more than ads do, drop the stack — that is a legitimate outcome
and the tracker is there to make the decision visible.

**The legacy config file carried a real `hmac_key` in git.** It was not
copied. Secrets come from `.env` via `INVIDIOUS_HMAC_KEY` and
`INVIDIOUS_DATABASE_URL`; the committed `config/invidious.yml` holds only
non-sensitive settings.

**`check_tables: true` replaces the entire legacy database bootstrap** — the
`init-invidious-db.sh` script and its nine `.sql` files are gone. Invidious
creates and migrates its own schema, so there is no hand-maintained SQL to
drift out of sync with the application.

**It was already broken when migrated (2026-08-11).** `inv-sig-helper` panics
on startup — `called 'Option::unwrap()' on a 'None' value` in `player.rs`
after its nsig regexes fail to match YouTube's current player JS. This is
upstream breakage, not configuration: YouTube changed the player, the
extraction patterns no longer match. The fix is an upstream release; check for
a newer `inv-sig-helper` image before assuming the stack is misconfigured.

**And Invidious reports healthy anyway.** Its healthcheck hits
`/api/v1/trending`, which returns 200 with the signature helper dead — so the
tracker shows this stack `up` while no video will play. A more honest
healthcheck is not obviously available: playback failure only shows up when
resolving an actual video. Treat `up` here as "the web app is serving", not
"YouTube works".

**`inv-sig-helper` is not optional.** It solves YouTube's signature challenge;
without it many videos fail to play and the error does not say why. It runs
with all capabilities dropped, a read-only filesystem, and
`no-new-privileges` — because its whole job is executing untrusted JavaScript
fetched from YouTube. That hardening is from upstream and should not be
relaxed.

**If videos stop loading with "This helps protect our community"**, YouTube
has flagged the instance as a bot. The fix is `po_token` and `visitor_data`
in the config — but those come from a real Google session and make your
requests *more* identifiable to Google, which partly defeats the point. That
trade-off is why they are commented out rather than set.

**`data: replaceable`.** The database holds local accounts, subscriptions, and
cached metadata — all either re-fetchable or a preference. It is still backed
up, since re-subscribing to everything by hand is tedious.

**Keep it `exposure: lan`.** A public Invidious instance attracts traffic and
gets your IP rate-limited by YouTube quickly.
