# MyFin

**What:** Personal budgeting — accounts, categories, budgets, and where the
money actually went.
**Why I care:** Ghostfolio answers "what do I own"; this answers "what did I
spend". Different questions.
**URL:** http://localhost:8086 (API on 8085)

## Notes

**Two port collisions fixed.** The legacy definition put the API on 8081
(Vaultwarden) and the frontend on 8080 (IT-Tools). They are 8085 and 8086
here. Both are also the reason `VITE_MYFIN_BASE_API_URL` had to change.

**`API_URL` is resolved by your browser, not by the frontend container.**
This is the setting most likely to confuse: the frontend is a static bundle,
and the API URL is baked into requests your browser makes. `localhost:8085`
works only when you browse from this same machine. Open MyFin from a phone
with that value and the page loads while every request fails. Set it to the
server's hostname before using it from anywhere else.

**`ENABLE_USER_SIGNUP` defaults to false.** Turn it on only long enough to
create your account.

**SMTP is optional and deliberately empty.** With no mail server configured,
password reset is simply unavailable — which is fine for a single user, and
better than carrying credentials in a compose file.

**`BYPASS_SESSION_CHECK` is forced to false.** It disables session validation
entirely; it exists for local development and has no business being on.

**MySQL, not Postgres** — upstream's choice, kept. `db-data` holds every
transaction you enter, so it is backed up and the container carries the
stop-during-backup label; an archive of a MySQL data directory taken mid-write
is not reliably restorable.

**This is the highest-abandonment-risk stack in the repo.** Budgeting only
works if transactions get entered, and that habit is the thing that lapses.
If it is still `planned` and untouched in a few months, drop it rather than
letting it sit.
