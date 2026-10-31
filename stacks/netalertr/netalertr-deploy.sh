#!/usr/bin/env bash
# Run on the laptop: builds netalertr at its current commit, copies the image to
# the host, fills .env, pulls, and starts this stack only. Works for a first
# install and for a redeploy. It never prints a secret.
#   DREAM_URL=https://<dream>/proxy/network UNIFI_API_KEY=... \
#     netalertr-deploy.sh <path-to-netalertr-repo>
# ROTATE_KEY=1 replaces a key already in .env. Otherwise the key is written only
# if .env has none.
set -euo pipefail
repo=${1:?usage: netalertr-deploy.sh <path-to-netalertr-repo>}
host=${NETALERTR_HOST:-homelab}
dream_url=${DREAM_URL:?set DREAM_URL, the base through /proxy/network}
: "${UNIFI_API_KEY:?set UNIFI_API_KEY}"
rotate=${ROTATE_KEY:-0}

if [ -n "$(git -C "$repo" status --porcelain)" ]; then
    echo "netalertr has uncommitted changes; deploy only from a commit" >&2
    exit 1
fi
tag=$(git -C "$repo" rev-parse --short HEAD)
docker build -t "netalertr:$tag" "$repo"
docker save "netalertr:$tag" | pv | ssh "$host" docker load

# The script is the ssh command string, because stdin carries the key.
read -r -d '' remote <<'REMOTE' || true
set -euo pipefail
tag=$1
dream_url=$2
rotate=$3
topic=homelab-alerts
user=netalertr
IFS= read -r key

if [ -n "$(git -C /opt/homelab status --porcelain)" ]; then
    echo "/opt/homelab has uncommitted changes" >&2
    exit 1
fi
git -C /opt/homelab pull --ff-only
cd /opt/homelab/stacks/netalertr

umask 077
touch .env
chmod 600 .env
if [ -n "$(tail -c1 .env)" ]; then
    printf '\n' >> .env
fi

# Add a variable only if it has no value yet.
setvar() {
    if ! grep -q "^$1=." .env; then
        sed -i "/^$1=\$/d" .env
        printf '%s=%s\n' "$1" "$2" >> .env
    fi
}
setvar UNIFI_URL "$dream_url"
setvar NTFY_URL http://ntfy:80
setvar NTFY_TOPIC "$topic"
setvar API_TOKEN "$(openssl rand -hex 32)"

if ! docker exec ntfy ntfy user list 2>&1 | grep -q "^user $user "; then
    # Tokens are how it logs in; nobody ever types this password.
    # Exported and passed by name, so the value never appears in an argument list.
    NTFY_PASSWORD=$(openssl rand -hex 32)
    export NTFY_PASSWORD
    docker exec -e NTFY_PASSWORD ntfy ntfy user add "$user"
    unset NTFY_PASSWORD
fi
# Every deploy, so the ACL is right even if an earlier run stopped after the user was made.
docker exec ntfy ntfy access "$user" "$topic" write-only
if ! grep -q '^NTFY_TOKEN=.' .env; then
    token=$(docker exec ntfy ntfy token add "$user" 2>&1 | grep -oE 'tk_[a-z0-9]+' || true)
    if [ -z "$token" ]; then
        echo "no token from ntfy" >&2
        exit 1
    fi
    sed -i '/^NTFY_TOKEN=$/d' .env
    printf 'NTFY_TOKEN=%s\n' "$token" >> .env
fi

if [ "$rotate" = 1 ] || ! grep -q '^UNIFI_API_KEY=.' .env; then
    if [ -z "$key" ]; then
        echo "no key on stdin" >&2
        exit 1
    fi
    sed -i '/^UNIFI_API_KEY=/d' .env
    printf 'UNIFI_API_KEY=%s\n' "$key" >> .env
fi

sed -i '/^NETALERTR_TAG=/d' .env
printf 'NETALERTR_TAG=%s\n' "$tag" >> .env

docker compose up -d
sleep 8
docker compose ps
docker exec netalertr /netalertr --healthcheck || true
REMOTE

# Single-quote a value for the remote shell.
q() { printf "'%s'" "${1//\'/\'\\\'\'}"; }
# shellcheck disable=SC2029  # expanding on the client side is the point
printf '%s\n' "$UNIFI_API_KEY" | ssh "$host" "bash -c $(q "$remote") _ $(q "$tag") $(q "$dream_url") $(q "$rotate")"
