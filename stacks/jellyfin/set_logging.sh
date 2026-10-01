#!/usr/bin/env bash
# Give Jellyfin's console log a full date, so CrowdSec can read a timestamp.
#
# logging.json lives in the LXC's /config, not in git, so a rebuilt
# container loses this. The loss is silent in the worst way: detection keeps
# working (CrowdSec falls back to the arrival time), and only forensic replay
# of an old log is wrong -- every event collapses to the time of the replay.
# Re-run this after any rebuild. Same reasoning as set_known_proxies.sh.
#
# Jellyfin prefers logging.json over logging.default.json and does not replace
# it on upgrade, which is why the override goes in a new file rather than an
# edit to the shipped one.
set -euo pipefail

CT=${CT:-102}
SRC=${SRC:-"$(dirname "$0")/logging.json"}
DEST=/config/config/logging.json

if [ ! -f "$SRC" ]; then
  echo "missing source file: $SRC" >&2
  exit 1
fi

# Fail before touching the container, not after stopping it.
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SRC" \
  || { echo "$SRC is not valid JSON -- refusing to install it" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if pct pull "$CT" "$DEST" "$WORK/current.json" 2>/dev/null; then
  if cmp -s "$SRC" "$WORK/current.json"; then
    echo "already correct: $DEST matches $SRC"
    exit 0
  fi
fi

# Serilog reads this once at startup. A running Jellyfin would keep the old
# template and, worse, is free to rewrite config files under us.
pct exec "$CT" -- systemctl stop jellyfin
if pct exec "$CT" -- systemctl is-active --quiet jellyfin; then
  echo "refusing to write: jellyfin is still running in CT $CT" >&2
  exit 1
fi

pct push "$CT" "$SRC" "$DEST" --user jellyfin --group jellyfin
pct exec "$CT" -- systemctl start jellyfin
echo "installed $DEST, restarted jellyfin in CT $CT"
echo "verify:  pct exec $CT -- journalctl -u jellyfin -n 3 -o cat    # lines must start [YYYY-MM-DD HH:MM:SS"
