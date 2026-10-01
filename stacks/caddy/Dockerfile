# Caddy with the CrowdSec bouncer compiled in.
#
# Caddy plugins are not loadable at runtime -- Go has no dynamic module
# loading here, so "installing" a plugin means rebuilding the binary. That is
# why this stack builds an image instead of pulling `caddy:2.11.4`.
#
# Pin both stages to the SAME Caddy version. The builder produces the binary
# and the runtime stage supplies everything else (entrypoint, CA bundle,
# directory layout); a mismatch is not caught at build time and shows up as
# strange behaviour later.
#
# Rebuild after changing the version or the plugin list:
#   docker compose build --pull caddy && docker compose up -d caddy
#
# Takes about 4.5 minutes on the first server. `caddy list-modules | grep -E 'crowdsec|porkbun'`
# in the built image is the check that it worked -- a plugin that failed to
# register still produces a working caddy binary, just without the module,
# and the failure then surfaces as a Caddyfile parse error.
#
# The porkbun DNS provider exists to issue the *.${PUBLIC_DOMAIN} wildcard
# over DNS-01 rather than a certificate per hostname over HTTP-01. That is a
# disclosure decision, not a convenience one: a wildcard names no subdomains,
# so nothing internal appears in Certificate Transparency. See
# docs/lessons-learned.md 19 -- the last hostname issued a per-name
# certificate here was being scanned 58 minutes later. DNS-01 also works for
# names that resolve to private addresses, which HTTP-01 cannot do at all.
# Both plugins are PINNED, and that is not tidiness. An unpinned `--with`
# resolves to whatever is latest at build time, so `docker compose build`
# tomorrow can produce different code under the same `homelab/caddy-crowdsec:
# 2.11.4` tag -- a tag that names the Caddy version and reads as if it pinned
# the whole image. The two things that would change silently are the only
# active defence on the public vhost and the module that holds the Porkbun
# API credentials.
#
# Versions read out of the running binary on the first server 2026-09-03, so this pins
# what is deployed rather than adopting a newer one by accident:
#
#   docker exec caddy caddy build-info | grep -E 'hslatman|caddy-dns/porkbun'
#
# Bumping either one is a deliberate edit here, followed by that same command
# against the rebuilt image.
FROM caddy:2.11.4-builder AS builder

RUN xcaddy build \
    --with github.com/hslatman/caddy-crowdsec-bouncer/http@v0.14.1 \
    --with github.com/caddy-dns/porkbun@v0.3.1

FROM caddy:2.11.4
COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# So the image says what is in it without running it. The tag cannot: it names
# the Caddy version only, and both plugins move independently of Caddy.
LABEL org.opencontainers.image.title="caddy-crowdsec-porkbun" \
      org.opencontainers.image.description="Caddy 2.11.4 with the CrowdSec bouncer and the Porkbun DNS provider compiled in" \
      homelab.caddy.version="2.11.4" \
      homelab.plugin.crowdsec-bouncer="v0.14.1" \
      homelab.plugin.porkbun="v0.3.1"
