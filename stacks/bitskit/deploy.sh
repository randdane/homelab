#!/usr/bin/env bash
# Run on the laptop: builds bitskit at its current commit, copies the image to
# homelab, sets BITSKIT_TAG and restarts this stack only.
#   stacks/bitskit/deploy.sh <path-to-bitskit-repo>
# Reaches the server as the ssh alias `homelab` (override with BITSKIT_HOST).
set -euo pipefail
repo=${1:?usage: deploy.sh <path-to-bitskit-repo>}
host=${BITSKIT_HOST:-homelab}

if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
    echo "bitskit has uncommitted changes; deploy only from a commit" >&2
    exit 1
fi
tag=$(git -C "$repo" rev-parse --short HEAD)
docker build -t "bitskit:$tag" "$repo"
docker save "bitskit:$tag" | pv | ssh "$host" docker load
ssh "$host" "cd /opt/homelab/stacks/bitskit \
  && sed -i 's/^BITSKIT_TAG=.*/BITSKIT_TAG=$tag/' .env \
  && grep -q '^BITSKIT_TAG=$tag\$' .env \
  && docker compose up -d && docker compose ps"
