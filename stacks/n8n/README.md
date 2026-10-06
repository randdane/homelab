# n8n

**What:** Visual automation builder — scheduled jobs, webhooks, and glue
between APIs, without writing the plumbing.
**Why I care:** It is where the small recurring chores end up living, and each
one quietly becomes something you depend on.
**URL:** http://localhost:5678

## Notes

**`N8N_ENCRYPTION_KEY` must be set before first run.** n8n encrypts stored
credentials with it. Left unset, n8n generates a key and hides it inside the
volume — which means restoring the volume elsewhere without that file leaves
every saved credential undecryptable. Setting it explicitly, from `.env`,
makes a restore actually work. Changing it afterwards orphans every credential
you have saved.

**The legacy definition used `${USER}` for basic-auth.** `USER` is set by the
shell to your OS username, so the auth user silently became your login name rather than
anything intended — and `N8N_BASIC_AUTH_*` no longer exists in n8n 2.x
regardless. Modern n8n has built-in user management: the first account you
create at the setup screen is the owner. Create it promptly; until you do, the
instance is unclaimed.

**`WEBHOOK_URL` is the URL third parties call back on**, not the one n8n sees
internally. Wrong value, and webhooks register an address nobody can reach —
the workflow looks fine and simply never fires. Update it when this moves
behind Caddy.

**The `data` volume is genuinely precious.** Workflows, execution history, and
those encrypted credentials. Rebuilding a mature set of workflows by hand is
real work, and the credentials cannot be rebuilt at all — only re-issued.

**Upgrade by hand, never unattended.** n8n migrates its database on
startup, and workflows can break between major versions.
