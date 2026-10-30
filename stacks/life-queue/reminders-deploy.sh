#!/usr/bin/env bash
# Run on the laptop: builds life-queue-reminders at its current commit, copies
# the image to homelab, creates the shadow ntfy user and token once, fills .env,
# pulls, and starts the `reminders` service only.
#   stacks/life-queue/reminders-deploy.sh <path-to-life-queue-reminders-repo>
# .env is filled before the pull: compose.yaml requires these values, and without
# them the whole life-queue stack stops rendering.
set -euo pipefail
repo=${1:?usage: reminders-deploy.sh <path-to-life-queue-reminders-repo>}
host=${REMINDERS_HOST:-homelab}

if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no)" ]; then
    echo "life-queue-reminders has uncommitted changes; deploy only from a commit" >&2
    exit 1
fi
tag=$(git -C "$repo" rev-parse --short HEAD)
docker build -t "life-queue-reminders:$tag" "$repo"
docker save "life-queue-reminders:$tag" | pv | ssh "$host" docker load

ssh "$host" 'bash -s' -- "$tag" <<'REMOTE'
set -euo pipefail
tag=$1
topic=life-queue-shadow
user=reminders-shadow
cd /opt/homelab/stacks/life-queue

if ! docker exec ntfy ntfy user list 2>&1 | grep -q "^user $user "; then
    # Tokens are how it logs in; nobody ever types this password.
    docker exec -e NTFY_PASSWORD="$(openssl rand -hex 32)" ntfy ntfy user add "$user"
    docker exec ntfy ntfy access "$user" "$topic" write-only
fi
if ! grep -q '^REMINDERS_NTFY_TOKEN=.' .env; then
    token=$(docker exec ntfy ntfy token add "$user" 2>&1 | grep -oE 'tk_[a-z0-9]+')
    if [ -z "$token" ]; then
        echo "no token from ntfy" >&2
        exit 1
    fi
    printf '\nREMINDERS_NTFY_TOKEN=%s\n' "$token" >> .env
fi
if grep -q '^REMINDERS_TAG=' .env; then
    sed -i "s/^REMINDERS_TAG=.*/REMINDERS_TAG=$tag/" .env
else
    printf 'REMINDERS_TAG=%s\n' "$tag" >> .env
fi

git -C /opt/homelab pull --ff-only
docker compose up -d --no-deps reminders
sleep 8
docker compose ps reminders
docker logs --tail 5 life-queue-reminders
REMOTE
