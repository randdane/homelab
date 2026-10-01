# CrowdSec

**What:** Reads the logs of the two internet-facing containers, decides which
IPs are hostile, and -- since 2026-08-31 -- has Caddy turn them away.
**Why I care:** the Jellyfin vhost was scanned by 36 distinct IPs within
an hour of its certificate being issued (`docs/lessons-learned.md` §19).
Something has to watch that.
**URL:** none. Everything is `docker exec crowdsec cscli ...`.

## Enforcement is on, at Caddy

CrowdSec is two halves. The **agent** here decides an IP is hostile; a
**bouncer** enforces it. The bouncer is compiled into Caddy
(`stacks/caddy/Dockerfile`) and applied per-vhost in the Caddyfile, so
enforcement covers HTTP through the proxy -- which is the entire surface this
agent observes. Decisions still accumulate in the local database either way:

    docker exec crowdsec cscli decisions list     # who is blocked right now
    docker exec crowdsec cscli alerts list        # and why
    docker exec crowdsec cscli bouncers list      # is the bouncer pulling?

**Not every vhost is bounced.** Both `jellyfin.` names are; the headscale
control plane is deliberately not, because a false positive there costs the
ability to log in and fix the false positive. The same reasoning now covers
`authentik.` — banning yourself out of the identity provider is how you lose
the ability to undo the ban. `authentik.`, `homepage.`, `vikunja.` and `kuma.`
are all `remote_ip`-gated to the tailnet and LAN instead.

A firewall bouncer was the alternative and was rejected: it would enforce the
same decisions on every port, by writing nftables rules on a host where Docker
already manages iptables chains and Tailscale manages its own nft tables --
more blast radius for no more coverage, on the box whose tailnet is the only
way in.

### Detect-only came first, and produced the evidence

Ran that way 2026-08-24 to 2026-08-31 before the bouncer was added. The
question detect-only answers is whether a scenario fires on your own
household's traffic, and the answer was no:

    76 alerts, 34 distinct IPs, zero residential addresses.
    Censys x5, GCP x7, DigitalOcean x3, Oracle, Azure, Akamai,
    dataforest GmbH, China Telecom.

That is the check worth repeating before widening enforcement to another
vhost. `whitelist-good-actors` also did real work: 8.65k of 13.95k Caddy
lines whitelisted.

### Enforcement lags a decision by up to 60 seconds

The bouncer runs in **stream mode**: it polls the LAPI on an interval and
caches decisions locally, rather than querying per request. So a new decision
is not enforced instantly, and testing it looks like a failure if you check
too early. Measured on this host:

| t | jellyfin.example.com |
|---|---|
| +20s | 200 |
| +40s | 200 |
| **+60s** | **403** |
| +120s | 403 |

Removal is faster -- under 20 s. The first two attempts at this test were
read as "the bouncer does not work"; they were read too soon.

### If the LAPI is unreachable, the bouncer FAILS OPEN

The Caddyfile sets only `api_url` and `api_key`, so every other option is a
default -- and the one that matters is `enable_hard_fails`, which defaults to
**false**. That means traffic is **allowed** when this agent is unreachable,
not blocked. Existing bans stop being enforced; nothing else breaks.

That is availability chosen over security, and it is the opposite of what the
rest of this stack's reasoning might lead you to assume. Two consequences
worth having straight before you need them:

- **Restarting CrowdSec is cheap.** It costs a window with no enforcement, not
  an outage. Combined with stream mode above, expect roughly a minute of
  unenforced traffic after a restart -- the bouncer's cache goes stale and the
  next successful poll refills it.
- **A dead agent is silent.** Nothing about the site looks wrong; requests
  that should be blocked simply are not. `cscli bouncers list` and its
  `Last API pull` column are the only place this shows up, which is why that
  command is in the triage list above rather than an afterthought.

Setting `enable_hard_fails` would invert this: Caddy then refuses to start
when the LAPI is unreachable. Do not, without thinking hard about the
dependency it creates -- CrowdSec would become able to take the edge down, and
the edge is what serves the headscale control plane that is the way back in.

### Reading `cscli bouncers list`: one bouncer can be several rows

Expect to see something like this, and do not read it as two bouncers:

```
caddy-bouncer             10.201.7.3  ✔️  last pull 2026-09-03T12:41Z
caddy-bouncer@10.201.7.6  10.201.7.6  ✔️  last pull 2026-09-04T15:29Z
```

Both rows carry the **same API key** -- verified 2026-09-04 by reading the
`bouncers` table out of `crowdsec.db`; the key hashes are identical. CrowdSec
appends `@<ip>` when a key it knows turns up from a new address, so one
bouncer that has moved shows up as one row per address it has ever used.

**Caddy is pinned to `10.201.7.240` as of 2026-09-04**, which stops new rows
appearing — that pin exists for this reason and nothing else. The rows already
present are from before it and will not clear on their own. Two traps follow:

- **The stale row's address gets reused.** `10.201.7.3` above belongs to
  `life-queue-app` now. The list reads as though that container runs a
  bouncer. It does not.
- **Do not prune these casually.** They share one key, and this is a live
  control on the internet-facing edge. If you do decide to, the rollback is
  that the key is in `stacks/caddy/.env`:
  `cscli bouncers add caddy-bouncer -k "$CROWDSEC_API_KEY"`.

The row to trust is the one whose `Last API pull` is recent. That is the
running bouncer, whatever it is called.

### Internal traffic all looks like the gateway

The router source-NATs hairpinned traffic, so anything reaching the public
hostnames **from inside the house** arrives at Caddy as the gateway's LAN address, not as
its own address:

    docker logs caddy | grep -o '"remote_ip":"[^"]*"'    # -> the gateway, e.g. 10.0.0.1

Genuine internet traffic is unaffected and carries real source IPs -- the
scanner alerts above are the proof. This only bites when testing: a ban on
your own public IP does nothing from the couch, because that is not the
address Caddy sees. Ban the gateway address to test from inside, and delete it
straight after.

It is not a false-positive risk, because `crowdsecurity/whitelists` already
covers `192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12` and `127.0.0.0/8`, so
no scenario can ban the gateway and lock the household out of Jellyfin in one
move. Manual `cscli decisions add` bypasses that whitelist, which is why the
test above works at all. **Do not remove that whitelist**, and note that
nothing inside the house can be detected either -- an acceptable trade here,
but it is a trade.

### The Jellyfin unparsed count is normal; the timestamp is not

    | docker:jellyfin | 908 lines read | 12 parsed | 896 unparsed |

That looks alarming and is not. `LePresidente/jellyfin-logs` contains exactly
one grok pattern, for `Authentication request for X has been denied (IP: Y)`.
Everything else Jellyfin logs -- library scans, database vacuums, startup --
is supposed to miss. **Unparsed is not a health signal for a single-purpose
parser**, unlike the Caddy datasource where every line is meant to parse.

The check that does mean something is `cscli explain`, and it passes:

    docker exec crowdsec cscli explain --type jellyfin --log '<a real denied line>'
    # -> LePresidente/jellyfin-logs 🟢, jellyfin-bf 🟢, jellyfin-bf_user-enum 🟢

**The real defect is that the timestamp is lost**, and `cscli explain` is
where it shows:

    dateparse-enrich 🔴
    warning: Line 0/1 is missing evt.StrTime

Jellyfin writes its logs twice, with two different Serilog templates
(`/config/config/logging.default.json`):

| Sink | Template | Example |
|---|---|---|
| Console (what Docker captures) | `[{Timestamp:HH:mm:ss}]` + `{Message:lj}` | `[18:17:22] ... for alice ... (IP: 10.0.0.109).` |
| File (`/config/log/log_*.log`) | `[{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz}]` + `{Message}` | `[2026-08-29 18:17:22.813 -05:00] ... for "alice" ... (IP: "10.0.0.109").` |

The parser's `JELLYFIN_CUSTOMDATE` is `%{YEAR}-%{MONTHNUM}-%{MONTHDAY}
%{HOUR}:%{MINUTE}:%{SECOND}` -- a full date. **It was written for the file
sink, and this stack feeds it the console sink.** The date group is optional
and the rest of the pattern is anchored on `.*`, so the message still matches
and detection still works; only the captured timestamp comes back empty.
(The parser handles the quoting difference deliberately: `"?` around both
username and IP.)

Consequences, in order of how much they matter:

- **Live detection: unaffected.** With `evt.StrTime` empty CrowdSec uses the
  arrival time, and the leaky buckets behave normally. This is why the
  scenarios still fire.
- **Forensic / time-machine mode: broken.** Replaying an old log file to ask
  "what happened last Tuesday" needs the line's own timestamp, and there
  isn't one. Every event collapses to the time of the replay.

The fix belongs on our side, not upstream: a line that never contained a date
cannot have one recovered from it, so the most an upstream pattern change
could do is match without inventing a timestamp. Give the parser a date
instead -- either point acquisition at the file sink, or add a
`/config/config/logging.json` overriding the console template to
`[{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz}]`. The second is smaller: no bind
mount, no second log source, and `docker logs jellyfin` stops printing bare
times that are ambiguous past midnight.

**Fixed 2026-08-31** by `stacks/jellyfin/logging.json`, installed with
`stacks/jellyfin/set_logging.sh`. `dateparse-enrich` is green against a real
failed login. Full write-up in `docs/lessons-learned.md` section 26.

## Verified working, 2026-08-24

Not assumed — six scanner-shaped requests were sent at the live host and the
pipeline was checked end to end:

    | Source          | Lines read | Lines parsed | Lines unparsed |
    | docker:caddy    | 9          | 9            | -              |
    | docker:jellyfin | 9          | 3            | 6              |

    crowdsecurity/CVE-2017-9841                ban:1
    crowdsecurity/http-admin-interface-probing ban:1

Both decisions were against **our own laptop's public IP**, and were deleted
afterwards. Test from an address you are willing to see banned, and check
`curl ifconfig.me` before reading any access log — see §19.

`cscli metrics show acquisition` is the check that matters. A datasource whose
`type` label does not match its parser reads lines happily and parses none, and
every other signal — container healthy, LAPI reachable, collections enabled —
stays green while it does.

**But do not trust it for the first minute after a restart.** `cscli metrics`
reads CrowdSec's own Prometheus endpoint on :6060, and while that is still
coming up the command prints a `connection refused` warning followed by an
*empty table* — indistinguishable from acquisition being dead. For an
immediate answer use `docker logs crowdsec | grep "connected to container
logs"` instead. See `config/acquis.d/docker.yaml` and
`docs/lessons-learned.md` §27.

## Fixed: Jellyfin was logging Caddy's IP

Until 2026-08-24 Jellyfin logged `10.201.7.3` -- Caddy -- as the client for
every remote request, so its own per-IP lockout counted the whole internet as
one address and the brute-force scenario would have banned the proxy.

`KnownProxies` is a string **array**, and had been written as raw text, which
deserialises to an empty list without a warning. See
`stacks/jellyfin/set_known_proxies.sh` and `docs/lessons-learned.md` §20.
Confirmed fixed -- a failed login from outside now logs the real client IP.

That was the blocker on adding a bouncer, and is why the bouncer came later.

## Local scenarios

`config/scenarios/` is mounted at `/etc/crowdsec/scenarios/local` — a
**subdirectory**, because binding over `scenarios/` itself would hide all 61
hub scenarios living in the `config` volume. CrowdSec walks the tree, so a
subdirectory is picked up normally.

### `jellyfin-geoblock.yaml`

Bans non-US source IPs on the public Jellyfin vhosts. Everyone who uses this
Jellyfin is domestic, so country is a usable signal here in a way it would not
be for a general-purpose site.

It matches `hasPrefix(evt.Meta.target_fqdn, 'jellyfin.')` rather than listing
the FQDNs, so no public hostname is written here and a new `jellyfin.*` vhost
is covered without editing this file. `authentik.` and the headscale control
plane still do not match, which is the part that matters.

**`hasPrefix`, not `startsWith`.** The latter is not an expr builtin, and an
invalid filter is *fatal* -- CrowdSec refuses to start at all, taking Caddy's
LAPI with it. Verified against v1.7.8 by loading both forms in a throwaway
container: `hasPrefix` reached "Loaded 10 scenarios", `startsWith` died with
`unexpected token Operator("startsWith")`. Load a changed filter that way
before deploying it; the blast radius is the whole edge, not this scenario.

A scenario rather than a Caddy matcher on purpose: `crowdsecurity/geoip-enrich`
is already in the pipeline, so this needs no new Caddy plugin, no MaxMind
licence key and no second database to keep updated. The bouncer that enforces
it is the one already compiled into Caddy.

**It cannot break a TV client.** The decision is made on the IP, before the
request reaches Jellyfin — no redirect, no challenge, no header for a Roku or
Tizen app to fail to understand. That is the same constraint that rules out
`forward_auth` and is why authentication here is authentik over LDAP.

**The emptiness guard on `IsoCode` is load-bearing.** `geoip-enrich` leaves it
empty whenever it cannot resolve an address. Comparing an empty string to
`'US'` is true, so without the guard the first failed lookup bans the house.

**Family travelling abroad will be locked out** for the ban duration. The
escape hatch is the tailnet — an enrolled device reaches `jellyfin:8096`
directly and never passes through Caddy, so it is not subject to this at all.
The manual undo is:

```bash
ssh homelab 'docker exec crowdsec cscli decisions delete --ip <addr>'
```

`authentik.${PUBLIC_DOMAIN}` and the headscale control plane are deliberately
**not** in the filter. Locking yourself out of the login path from a hotel is
a much worse day than a media server that will not play.

Validate a scenario edit before recreating anything — a scenario that fails to
load is skipped silently:

```bash
ssh homelab 'docker exec crowdsec crowdsec -c /etc/crowdsec/config.yaml -t 2>&1 | grep -c "Adding .* bucket"'
```

The count must go up by one against the same command run without the file.

## Notes

**The collections were checked against the hub index before being named**, not
guessed. `crowdsecurity/caddy` parses the JSON access log Caddy already emits,
so no Caddyfile change was needed. There is no first-party Jellyfin support:
`LePresidente/jellyfin` is third-party, and is the reason for the caveat above.

**`whitelist-good-actors` is not optional.** Without it the first thing a
bouncer blocks is Googlebot.

**The community blocklist is the reason to prefer this over fail2ban.** The
agent registers with the Central API and pulls IPs that attacked other people's
hosts, so they are known here before they arrive. It also shares this host's
alert signals — IP, scenario, timestamp, not log contents. `DISABLE_ONLINE_API=true`
opts out of both.

**`acquis.d` is a directory mount, not a file**, so `git pull` cannot leave the
container reading a stale acquisition config. See `docs/lessons-learned.md` §18.

**The image ships a placeholder `/etc/crowdsec/acquis.yaml`** pointing at
`/does/not/exist`, which logs a warning on every start. Harmless: the real
config is everything in `acquis.d`.

**Nothing here is precious.** The hub content re-downloads and the decisions
database rebuilds from live traffic. Both volumes are registered for backup
anyway, because losing the alert history loses the evidence of what was
happening before an incident.
