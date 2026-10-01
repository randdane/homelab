# DuckDNS

> **Retired 2026-09-24.** Home Assistant's built-in **DuckDNS**
> integration updates `DUCKDNS_HOST` now (Settings → Devices &
> services → Duck DNS), from `hass`, which is not `pve`. That removes the
> case where the name goes stale during a `pve` outage, which is exactly
> when the fallback paths need it. The notes below still describe the
> service, and the token check still applies. `check_derp.py`
> compares the record with the public IP daily, whichever updater runs.
> Restoring this stack is `lifecycle: production` plus `docker compose up -d`.

**What:** Updates a free `*.duckdns.org` hostname to point at this house's
current public IP, re-checking every few minutes.
**Why I care:** Home IPs change without warning. Caddy's certificates and
every externally shared link depend on the hostname resolving correctly.
**URL:** none — no web interface.

## Notes

**The legacy definition had literal placeholders.** `SUBDOMAINS=yourdomain`
and `TOKEN=yourtoken`. It would have started, reported healthy, and updated
nothing — while Caddy's certificates quietly failed to renew. Both are now
required from `.env` and the stack refuses to start without them.

**`SUBDOMAINS` is the name only** — `myhouse`, not `myhouse.duckdns.org`.
Getting this wrong is silent: DuckDNS returns `KO` and the container keeps
running.

Observed cadence on 2026-08-21 was updates at 17:08:44 and 17:11:21 — a few
minutes apart, not the "every five minutes" an earlier version of this file
claimed. Two samples is not a schedule; treat the interval as "frequent" and
do not build anything that depends on an exact period.

**The token that was in `.env` was dead** (2026-08-21). It was a real-looking
UUID, and `curl`ing the update endpoint with it returned `KO` for every
subdomain — as did the placeholder subdomain `homelab-migration-test`, which
is not on the account and does not resolve. Had the stack ever been started,
it would have run, reported healthy, and updated nothing. DNS was correct only
because the ISP had not changed the address since it was last set by hand.

Verify a token before trusting it:

    curl "https://www.duckdns.org/update?domains=<sub>&token=<token>&ip=$(curl -s https://api.ipify.org)"

`OK` means the token owns that subdomain. `KO` means it does not, and the
container will not tell you the difference.

**This is the dependency nothing warns you about.** If DuckDNS stops updating,
nothing breaks immediately — it breaks whenever the ISP next changes the IP,
which might be weeks later, and the symptom is "the internet can't reach
Jellyfin" rather than anything pointing here. Worth a Homepage widget or an
uptime check against the hostname rather than the container.

**Nothing to back up and nothing to expose.** It makes one outbound HTTPS
request on a timer and holds no state.

**Chosen over DNS-Updater** because the hostname does not need to be a domain
you own. If that changes, swap this for a client that updates a real
registrar — the rest of the ingress setup is unaffected.
