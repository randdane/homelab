# Ghostfolio

**What:** Investment portfolio tracker — holdings, allocation, performance
over time, across accounts and currencies.
**Why I care:** Seeing the whole position in one place, without handing a
broker aggregator read access to every account.
**URL:** http://localhost:3333

## Notes

**`env_file: ../.env` in the legacy definition pointed outside its own
directory** — at a file in the parent folder, which does not exist in this
repo's layout. Every variable is explicit here, sourced from this stack's own
`.env`.

**`ACCESS_TOKEN_SALT` is the one to be careful with.** Ghostfolio has no
passwords: the security token *is* the credential, and this salt hashes it.
Change it and every existing account is locked out permanently, with no reset
path. Set it once, before creating an account, and treat it like the data.

**Redis requires a password here.** The legacy definition left it open. It is
only reachable from this stack's network either way, but an unauthenticated
cache holding session state is a bad default to copy forward.

**Upstream's hardening is kept verbatim** — `cap_drop: ALL` on the app,
`no-new-privileges`, and a minimal capability set on Postgres. Those came from
upstream and are not decoration.

**Transactions are entered by hand.** That is the real cost of this stack and
the reason `data: precious`: the database is not reconstructible from anywhere
else. It also makes Ghostfolio a strong candidate for abandonment — if the
tracker shows it `planned` and untouched in six months, that is your answer.

**Market data comes from an external provider** and may need an API key
configured in the UI for anything beyond basic quotes.
