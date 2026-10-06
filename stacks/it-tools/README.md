# IT-Tools

**What:** A large collection of small dev utilities — base64, JWT decoding,
hashes, cron expressions, UUIDs, colour conversion, JSON formatting.
**Why I care:** These are the things otherwise pasted into a random website.
Anything sensitive — a JWT, a hash, a token — should not go to someone else's
server.
**URL:** `https://it-tools.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet).
`127.0.0.1:8080` on `homelab` is the break-glass path over ssh.

**No Authentik, deliberately** (`adding-a-stack.md` §0 "neither"): there is
no login to replace, no stored data, and every tool runs in the browser — the
server only hands out static files. The `remote_ip` gate is the control.

## Notes

Stateless. No volumes, nothing to back up, nothing to lose. Delete and
recreate freely.

`:latest` is deliberate — there is no on-disk state to migrate, so an
unattended update cannot break anything that a restart will not fix.

Chosen over OmniTools, which does the same job and was the less maintained of
the two.
