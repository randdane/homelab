#!/usr/bin/env bash
# Make Jellyfin trust Caddy's X-Forwarded-For. Run on pve, as root.
#
# network.xml lives in the LXC's /config, not in git, so a rebuilt container
# loses this and the loss is silent -- Jellyfin keeps working, it just
# attributes every remote request to the proxy. Re-run this after any rebuild.
#
# The trap: KnownProxies is a string ARRAY. Written as raw text --
#   <KnownProxies>10.0.0.78</KnownProxies>
# -- it deserialises to an EMPTY list, no warning, no log line, and Jellyfin
# silently trusts no proxy at all. It must be <string> children, the same
# shape VirtualInterfaceNames already uses.
#
# Verify by failing a login from outside and reading the logged IP:
#   pct exec 102 -- journalctl -u jellyfin | grep "has been denied"
# It must show the client's address, not Caddy's host.
set -euo pipefail

CT=${CT:-102}
# Caddy's host. Since the move to pve it is an address, not a Docker subnet.
PROXY=${PROXY:?set PROXY to Caddy's host, the HOMELAB_HOST address}
FILE=/config/config/network.xml

WANT="  <KnownProxies>
    <string>${PROXY}</string>
  </KnownProxies>"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
pct pull "$CT" "$FILE" "$WORK/network.xml"

if grep -qF "<string>${PROXY}</string>" "$WORK/network.xml"; then
  echo "already correct: KnownProxies = ${PROXY}"
  exit 0
fi

WANT="$WANT" python3 - "$WORK/network.xml" <<'PYEOF'
import os, re, sys
path = sys.argv[1]
s = open(path).read()
pattern = re.compile(r"[ \t]*<KnownProxies\s*/>|[ \t]*<KnownProxies>.*?</KnownProxies>", re.S)
if not pattern.search(s):
    sys.exit("no KnownProxies element found -- refusing to guess where it goes")
open(path, "w").write(pattern.sub(os.environ["WANT"], s, count=1))
PYEOF

# Jellyfin rewrites its config files while running; write only while stopped.
pct exec "$CT" -- systemctl stop jellyfin
if pct exec "$CT" -- systemctl is-active --quiet jellyfin; then
  echo "refusing to write: jellyfin is still running in CT $CT" >&2
  exit 1
fi
pct push "$CT" "$WORK/network.xml" "$FILE" --user jellyfin --group jellyfin
pct exec "$CT" -- systemctl start jellyfin
echo "set KnownProxies = ${PROXY}, restarted jellyfin in CT ${CT}"
