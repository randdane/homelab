# Vaultwarden

**What:** Bitwarden-compatible password server. The official Bitwarden clients
and browser extensions all talk to it.
**Why I care:** Of everything in this repo, this is the one where data loss is
not recoverable by re-downloading or re-scanning. Passwords, TOTP seeds, and
secure notes exist nowhere else.
**URL:** http://localhost:8081

## Notes

**Three settings the legacy compose got wrong**, all now in `.env`:

- `SIGNUPS_ALLOWED` was `true`. Anyone who reached the port could register.
  It now defaults to `false` — turn it on just long enough to create your
  own account, then set it back and recreate the container.
- `ADMIN_TOKEN` was the literal string
  `some-string-to-enable-the-/admin-page`. Empty here, which keeps `/admin`
  disabled entirely. Generate a real one with `openssl rand -base64 48` only
  if you need that page.
- `DOMAIN` was commented out. Vaultwarden needs to know its own external URL
  or attachments, WebAuthn, and emailed links break in confusing ways. Set it
  once Caddy is in front.

**HTTP is a laptop-only affordance.** Browser extensions and mobile clients
refuse to talk to a non-HTTPS server on anything but localhost. This is not
genuinely usable until Caddy terminates TLS for it.

**No healthcheck block here on purpose** — the image already ships
`CMD /healthcheck.sh`, and adding a second one just overrides a better one.

**The backup label matters more here than anywhere else.** The data is SQLite;
an archive taken mid-write is a corrupt archive, and you would not find out
until you needed to restore it.
