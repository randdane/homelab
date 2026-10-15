# Homebox

**What:** Inventory of physical belongings: purchase date, price, vendor,
serial, warranty end, receipt and manual attachments, and where it is kept.
Prints QR labels that open the item.
**Why I care:** Warranty claims, insurance, and "which box is the spare
cable in". Nothing else here holds that.
**URL:** `https://homebox.<PUBLIC_DOMAIN>` (gated vhost). `127.0.0.1:7745`
on `homelab` is break-glass over ssh.

**Authentik:** native OIDC, provider and application `homebox`, group
`homebox-users`. Password login stays enabled for when Authentik is down.

## What goes here, and what does not

Ownership and physical facts only. Network facts (addresses, MACs, VLANs,
why a thing is configured the way it is) stay in `devices.md` in
`homelab-private`: it is in git, readable when `homelab` is down, and the
outage runbooks live there. An item for a networked device takes its
`devices.md` name (e.g. `pve`) and carries no IP or MAC.

## First run

1. `.env`: `HBOX_API_KEY_PEPPER` (`openssl rand -base64 48`) and
   `ALLOW_REGISTRATION=true`. `docker compose up -d`.
2. Register your account on the vhost, then `ALLOW_REGISTRATION=false` and
   `docker compose up -d` again. Confirm the register page refuses.

**What will bite you:**
- The pepper is part of the data. Losing it voids every API key; it is in
  `.env`, not the volume, so keep it with the other secrets.
- Pinned on purpose: releases migrate the database. Read release notes
  before bumping the tag.
- An OIDC login whose subject is unknown is linked to a local account with
  the same email, verified or not. Safe only because Authentik is the sole
  issuer and accounts exist only because you made them.

**Unverified:** OIDC end to end, attachment upload over 10 MB, QR labels
from the phone.
