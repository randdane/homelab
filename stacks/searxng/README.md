# SearXNG

**What:** Metasearch: one query fanned out to Google, Bing, DuckDuckGo and
others, results merged, no ads or tracking.
**Why I care:** The search engines see `homelab`'s address and no cookies,
not the browser's history. Chosen over Whoogle, which is mostly
unmaintained.
**URL:** `https://search.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet).
`127.0.0.1:8888` on `homelab` is the break-glass path over ssh.

**No Authentik, deliberately** (`adding-a-stack.md` §0 "neither"): no login
to replace and nothing stored. Forward-auth would also break it as the
browser's default search engine. The `remote_ip` gate is the control.

## Notes

Stateless. The entrypoint writes `settings.yml` into the container with a
fresh random `secret_key` on every recreate. That is fine: nothing persists
that the key signs. To customise engines, mount a `settings.yml` and add it
to the `.gitignore` allowlist.

The limiter (bot protection) is off, which is the default, and it would need
Valkey. The gate already keeps out everyone it would stop.

**What will bite you:** every query leaves from the house's one public IP,
so a heavy session can get it rate-limited or CAPTCHA'd by Google. The other
engines still answer when that happens.

**Unverified:** results quality, and use as the phone's search engine.
