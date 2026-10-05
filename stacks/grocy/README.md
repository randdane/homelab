# Grocy

**What:** ERP for the house — food stock with expiry dates, shopping lists,
chores, and what things cost.
**Why I care:** It answers "do we already have this" from the shop, and
"what needs eating this week" at home.
**URL:** `https://grocy.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet).
`127.0.0.1:8092` on `homelab` is the break-glass path over ssh.

**Login is Authentik forward-auth** (`admin-uis` group). Grocy runs
`ReverseProxyAuthMiddleware`: it trusts `X-Authentik-Username` and creates a
Grocy user the first time it sees a name. There is no Grocy password.

**The Android app uses an API key**, since it cannot complete a browser login.
Make one in the web UI (user menu → Manage API keys) and give the app the
vhost URL and the key. Caddy sends `/api/*` around forward-auth and strips the
username header there, so on that path only a valid key gets in.

**The header is the credential.** Anything that reaches the container's port
80 with `X-Authentik-Username` set *is* that user. The loopback-only port and
the strip on `/api/*` are what keep that true; do not publish the port on the
LAN.

## Notes

**The default `admin` / `admin` account** is unused under forward-auth
(nobody can log in with a password), so it is harmless — delete it from
Manage users once your own user exists.

**Overlaps Mealie only at the edges.** Mealie holds recipes and meal plans;
Grocy holds what is physically in the house. They are usually run together,
and Grocy's shopping list is the piece Mealie does not have.

**SQLite in the `config` volume**, hence the stop-during-backup label. The
data is months of hand-entered stock, purchase history, and barcodes — tedious
rather than difficult to recreate, which is exactly the kind of loss that is
most annoying.

**Barcode scanning is the feature that makes it stick.** Without it, keeping
stock accurate by hand is the chore that kills this kind of app — worth
setting up a scanner or the phone app early rather than deciding later that
Grocy "did not work out".
