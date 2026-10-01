# Jellyfin

**What:** Media server — movies, TV, and music, streamed to the TV and to phones.
**Why I care:** It is the one service that was already running in production before
this repo existed, and the only one other people notice when it breaks.
**URL:** https://jellyfin.${PUBLIC_DOMAIN} (Caddy on the `homelab` VM), and
http://<JELLYFIN_HOST>:8096 on the LAN.

## Not a Docker stack any more

Since the move to Proxmox, Jellyfin runs **natively, from its apt repo, in
the unprivileged LXC 102** at `JELLYFIN_HOST`, not in Docker. This directory keeps
the helpers and the knowledge; there is no `compose.yaml`, which is also what
switches `scripts/check_updates.py` from the pinned tag to asking the LXC.

| Concern | Where it lives now |
|---|---|
| Paths | `/config`, `/cache`, `/media` — the Docker image's, kept so the database's absolute paths needed no rewrite. Set by `/etc/jellyfin-homelab.env` via a drop-in, `jellyfin.service.d/homelab.conf`, which also sets `TimeoutStopSec=60` (the package ships 15 s) |
| Media | `tank/media` on `pve`, bind-mounted read-only as `mp0`. Files must stay world-readable: the CT reads them as `nobody` |
| GPU | `dev0: /dev/dri/renderD128,gid=992` (`render` inside the CT); VAAPI in `encoding.xml` |
| Isolation | `$SITE_DIR/pve/firewall/102.fw` — replaces `jellyfin-egress-rule.sh` and the `jellyfin` Docker network |
| LDAP | Authentik's outpost, published on `<HOMELAB_HOST>:3389` for the LXC only (`$SITE_DIR/pve/firewall/101.fw`) |
| CrowdSec | rsyslog in the CT forwards the `jellyfin` program to udp `<HOMELAB_HOST>:4242`; `stacks/crowdsec/config/acquis.d` |
| Backup | `vzdump` of CT 102, not the `backup` stack |
| Updates | `apt-mark hold`; security-only unattended upgrades; upgrade by hand after `pct snapshot 102 pre-<ver>` |

**The version is held.** Jellyfin migrates its library database on startup and
does not migrate back. Upgrade by hand, snapshot first — `pct rollback` undoes
the migration too:

```bash
pct snapshot 102 pre-<ver>
pct exec 102 -- sh -c 'apt-get update && apt-mark unhold jellyfin-server jellyfin-web &&
  apt-get install -y jellyfin-server=<ver>+deb13 jellyfin-web=<ver>+deb13 &&
  apt-mark hold jellyfin-server jellyfin-web'
```

**Helpers, all run on `pve` as root:** `set_known_proxies.sh`, `set_logging.sh`,
and `install_ldap_plugin.sh` (rebuild only — it rewrites `LDAP-Auth.xml`
whole). To repoint an existing install at a new LDAP host, change
`<LdapServer>` alone, with Jellyfin stopped.

## Which libraries LDAP users get

`install_ldap_plugin.sh` writes `EnableAllFolders=false` with an empty
`EnabledFolders`, which means a freshly provisioned user sees **nothing** until
libraries are granted. That is deliberate: the alternative grants every library
including ones that do not exist yet.

Library GUIDs are generated per-install, so they are not committed. Read them
off the running instance:

```bash
rm -rf /tmp/jflib && mkdir -p /tmp/jflib
for f in jellyfin.db jellyfin.db-wal jellyfin.db-shm; do     # on pve
  pct pull 102 "/config/data/$f" "/tmp/jflib/$f" 2>/dev/null || true
done   # the -wal matters: without it recent libraries are missing
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("/tmp/jflib/jellyfin.db")
for i, n in c.execute(
        "SELECT Id, Name FROM BaseItems WHERE type LIKE '%CollectionFolder%'"):
    print(n, str(i).replace("-", "").lower())
EOF
```

Then put them in `/config/plugins/configurations/LDAP-Auth.xml`:

```xml
<EnabledFolders><string>GUID1</string><string>GUID2</string></EnabledFolders>
```

Restart Jellyfin, and verify with a throwaway account rather than assuming:
`/Users/AuthenticateByName` returns the new user's `Policy.EnabledFolders`, and
`/UserViews?userId=<id>` shows what it can actually see. Stored GUIDs that
match no library produce an empty view, not an error.

**This only affects users created afterwards.** Accounts that already exist
keep the policy they were given -- check them in Dashboard -> Users -> Access.

## Client IPs behind Caddy

Jellyfin only believes `X-Forwarded-For` from an address in `KnownProxies`,
which must be Caddy's host (`HOMELAB_HOST`). It lives in `network.xml` in the
CT's `/config` and therefore **not in git** — a rebuild loses it, and nothing
complains. Re-run after any rebuild (on `pve`):

    PROXY=<HOMELAB_HOST> bash set_known_proxies.sh

`KnownProxies` is a string **array**. Raw text parses as an empty list with no
warning, which silently disables proxy trust — see `docs/lessons-learned.md`
§20. Never verify this by reading the file; fail a login from outside the
house and check which address was logged:

    pct exec 102 -- journalctl -u jellyfin | grep "has been denied"

It must show the client's real address. Caddy's host address means it is broken, and
that per-IP lockout is counting the entire internet as one address.
