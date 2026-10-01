# Caddy

**What:** Reverse proxy with automatic HTTPS. Obtains and renews certificates
without being asked.
**Why I care:** It is the only process in the house listening on the public
internet, and the only reason Jellyfin can be shared with someone who will not
install Tailscale.
**URL:** https://jellyfin.example.com, https://vikunja.example.com (override
the ports on the laptop — see below)

## Notes

**It answers to eight names, and only three of them are public.** The
Caddyfile is not a list of public sites; it is a list of names this host
answers to, some gated by source address rather than by exposure:

| Name | Reachable from | Gate |
|---|---|---|
| `myhome.duckdns.org` | the internet | none — headscale control plane and DERP, deliberately not bounced |
| `jellyfin.myhome.duckdns.org` | the internet | CrowdSec |
| `jellyfin.example.com` | the internet | CrowdSec |
| `authentik.example.com` | tailnet + LAN | `remote_ip` allowlist, ANDed with the host match |
| `homepage.example.com` | tailnet + LAN | `remote_ip` allowlist, ANDed with the host match |
| `kuma.example.com` | tailnet + LAN | `remote_ip` allowlist, ANDed with the host match |
| `vikunja.example.com` | tailnet + LAN | `remote_ip` allowlist, ANDed with the host match |

**`tasks.<base_domain>` is gone, as of 2026-09-09.** `vikunja.example.com`
replaced it and is the name actually used: it resolves whether or not
Tailscale is up, which a MagicDNS-only name does not. Retiring it removed a
live route, its A record, and the only vhost that expressed its gate as a
whole site block rather than a host matcher.

**`auth.myhome.duckdns.org` is gone, as of 2026-09-05**, replaced by
`authentik.example.com` — a clearer name, and no longer public. Nothing
needed it public: family reach authentik over LDAP, container-to-container on
`edge`, so no browser is ever redirected to it from outside the tailnet.
Forward-auth and OIDC *do* redirect the browser, which is why this name must
be reachable from wherever the user sits — and why an internal-only name
suffices exactly as long as every protected app is internal too. Publishing it
again is one DNS record and one provider's issuer URL.

The internal ones need saying out loud: Caddy's :80 and :443 are already
internet-facing for the others, so **any** vhost here is reachable from
outside by anyone sending the matching `Host` header. An unguessable hostname
is not a control. The `remote_ip` block is.

That allowlist is only trustworthy since 2026-09-03, when
`"userland-proxy": false` stopped `docker-proxy` rewriting every request
arriving over `tailscale0` to the bridge gateway. Before that it rejected the
entire tailnet while looking like a network fault.

**Ports 80/443 are the defaults and are wrong for the laptop.** `tailscaled`
already holds 443 here. Override `HTTP_PORT`/`HTTPS_PORT` in `.env` locally;
the committed defaults are the server's.

**Caddy has a pinned address on `edge`: `10.201.7.240`.** It is the only
container on that network with one, because it is the only one whose IP is an
identity rather than an implementation detail — CrowdSec records a bouncer row
per address it sees a key from, so a Caddy that moves leaves a stale row every
recreate, and the address it vacates gets reused by something else. See
`stacks/crowdsec/README.md`.

`.240` rather than a low address: Docker allocates dynamically from the bottom
of the subnet, so pinning to something low means another stack can take it
while Caddy is down, and **Caddy then fails to start** — trading a cosmetic
annoyance for an edge outage. Do not "tidy" this down to the next free number.

**It is above expected occupancy, not reserved.** An earlier version of this
paragraph said "above the allocator's range", which is wrong: `edge` today has
a `Subnet` and no `IPRange`, so Docker may allocate `.240` dynamically like any
other address. With eight containers on a `/24` that will not happen in
practice, but it is luck rather than configuration. `docs/host-setup.md` §11
gives the `--ip-range` that would make it true; applying it to the existing
network needs every attached container disconnected, so it is documented for
the next rebuild rather than done now.

**Every vhost name comes from the repo-root `.env`.** `PUBLIC_DOMAIN` and
`DUCKDNS_HOST` are read into this container by `env_file: ../../.env` and
substituted as `{$VAR}` in the Caddyfile — see `docs/conventions.md`. No
public hostname is written literally in this stack any more. An unset value
is an invalid site address and Caddy refuses to start, which is deliberate.

**`SITE_ADDRESS` is the headscale vhost, and is separate from those.** It
lives in this stack's own `.env` because it is the one site address that
differs between the server and a laptop — an `http://` or `localhost` value
disables automatic TLS for it, which is what makes this stack runnable with
no domain. Every other vhost is built from the two variables above and
ignores it.

**The `data` volume holds certificates and ACME account keys.** Losing it
means re-issuing everything, and there is a limit on how often you may. It is
marked `replaceable` because certificates regenerate — but not freely.

**Proxying other stacks needs a shared network.** Each stack in this repo gets
its own Compose network, and Caddy cannot reach across them by service name.
That shared network is `edge`, and it is external — Compose will not create
it, so a fresh host must create it before either stack starts, **with an
explicit subnet**: see `docs/host-setup.md` §11. A bare
`docker network create edge` with no flags takes whatever `/24` Docker's address pool hands
out next, which is not necessarily `10.201.7.0/24` — and Caddy's pinned
address below is then outside the network and the container refuses to start.
`stacks/headscale` already joins it.

**`config/Caddyfile` is tracked, but only because `.gitignore` names it.**
The allowlist there matches on file extension and `Caddyfile` has none, so it
was silently untracked for a while and edits never reached the server — Caddy
kept serving a stale config that looked fine. Any other extensionless config
file needs the same explicit `!` entry.

**The healthcheck runs `caddy validate`**, not an HTTP request. A broken
Caddyfile is the failure that actually happens here, and it is invisible to a
request against a site that is still serving the old config.

## TLS: one wildcard, issued over DNS-01

`*.example.com` is issued through the Porkbun DNS provider, and the
certificate names no subdomains at all:

    subject=CN = *.example.com
    X509v3 Subject Alternative Name: DNS:*.example.com

That is the point, not a side effect. A per-name certificate publishes the
hostname to Certificate Transparency within seconds, and `vikunja.` tells a
scanner exactly which CVE list to work through — `docs/lessons-learned.md` §19
records a hostname being scanned 58 minutes after its certificate issued.

DNS-01 is also the only challenge that *can* work for these names. They
resolve to the server's LAN address, a private address, so Let's Encrypt cannot reach
them for HTTP-01 at all. Proving control of the zone sidesteps reachability.

`jellyfin.myhome.duckdns.org` keeps its own certificate because the
wildcard does not cover a duckdns subdomain. `jellyfin.example.com` moved
under the wildcard on 2026-09-03; it was already in CT from its 08-24 per-name
certificate, so this stops issuing a second one rather than recovering any
privacy.

**A first issuance can look broken for a minute.** 2026-09-03: the first
attempt failed with `HTTP 404 ... Certificate not found` while downloading the
chain, and Caddy opened a staging account as a hedge; the retry succeeded ~80
seconds later. That was a Let's Encrypt-side race, not a misconfiguration.

## This image is built, not pulled

Caddy plugins are compiled into the binary — there is no runtime loading. So
`compose.yaml` has a `build:` stanza and `stacks/caddy/Dockerfile` builds
`homelab/caddy-crowdsec:2.11.4` with xcaddy, carrying **two** plugins: the
CrowdSec bouncer and the Porkbun DNS provider. About 4.5 minutes on a 2-core laptop CPU.

    docker pull caddy:2.11.4   # refreshes the tag Cup reads; build --pull does not
    docker compose build --pull caddy && docker compose up -d caddy
    curl -fsS -o /dev/null http://localhost:8010/api/v3/refresh

Bumping the Caddy version means editing **both** stages of the Dockerfile.

**Both plugins are pinned to an exact version**, and the tag is the reason:
`homelab/caddy-crowdsec:2.11.4` names the Caddy version and reads as though it
pinned the whole image. It does not — the plugins move independently, and an
unpinned `--with` resolves to whatever is latest at build time, so the same
tag could carry different code tomorrow. The two things that would change
silently are the only active defence on the public vhost and the module
holding the Porkbun credentials. The image also carries `homelab.plugin.*`
labels so `docker inspect` answers the question without starting anything.

After any rebuild, confirm both plugins registered at the versions you meant.
One that failed to compile in still yields a working caddy binary, and the
failure surfaces later as a Caddyfile parse error on the `crowdsec` or
`dns porkbun` directive:

    docker run --rm homelab/caddy-crowdsec:2.11.4 caddy build-info \
      | grep -E 'hslatman|caddy-dns/porkbun'

`caddy list-modules | grep -E 'crowdsec|porkbun'` answers the weaker question —
whether the modules are present at all, without saying which version.

Three credentials must be in `.env` or the container will not start — all
three are declared `${…:?}`. `CROWDSEC_API_KEY` comes from
`docker exec crowdsec cscli bouncers add caddy-bouncer` and is shown once;
`PORKBUN_API_KEY` and `PORKBUN_API_SECRET_KEY` come from
porkbun.com/account/api, and the key alone is not enough — API ACCESS has to
be switched on for the domain itself under Domain Management, or issuance
fails with "domain not opted in". See `.env.example`. Which vhosts are
bounced, and why headscale is not, is in `stacks/crowdsec/README.md`.

**A pulled Caddyfile change needs only a reload.** `./config` is mounted as a
DIRECTORY, which resolves by path on every open, so a file replaced by `git
pull`'s write-and-rename is picked up:

    docker exec caddy caddy reload --config /etc/caddy/Caddyfile
    docker exec caddy grep -c <new-hostname> /etc/caddy/Caddyfile

It was a single-file mount until 2026-09-03, and that is why this used to
demand `--force-recreate`: a file mount pins the inode, so the container kept
reading the replaced-away original, `caddy reload` answered `"config is
unchanged"`, and the new site silently did not exist. Verify the `grep` above
rather than trusting reload's exit status — that is the failure it cannot
report. See `docs/lessons-learned.md` §18.

## Authentik: forward-auth and the OIDC exception

Two additions from 2026-09-29, both in `config/Caddyfile`:

- **`(authentik_forward_auth)`**, imported by homepage, kuma, dozzle and cup
  inside their `handle`, after the `remote_ip` gate. Authentik proxy provider
  `admin-uis`, forward_domain on the parent domain, group `admin-uis`, run by
  Authentik's embedded outpost. One sign-in covers all four. If Authentik is
  down they are down too: use the published ports or ssh tunnels.
  **The embedded outpost's `authentik_host` must be the public URL.** It
  shipped as `http://localhost:9000`, so every redirect sent the browser to
  localhost. Set in Authentik → Outposts → embedded outpost → config.
- **`@authentik_oidc_internal`**: OIDC apps' backends (mealie, karakeep,
  vikunja, forgejo) fetch discovery and exchange codes from their `edge`
  address, which the authentik vhost's allowlist refuses. Admitted for
  `/application/o/*` only, `edge` minus its gateway. See the comment in the
  Caddyfile for why the gateway stays out.

**Creating an OAuth2 provider through Authentik's API**: set `grant_types`
explicitly (`authorization_code`, `refresh_token`). It defaults to `[]`, and
the failure is a bare "The request is otherwise malformed".
