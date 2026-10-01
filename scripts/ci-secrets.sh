#!/usr/bin/env bash
# The values CI scans for live in three places: the untracked files here, the
# repository's Actions secrets, and its Dependabot secrets (a separate store,
# see .github/workflows/check.yml). GitHub will not read a secret back, so
# nothing can compare them. This script is the one place that writes both
# stores, and the one place that notices when they are behind.
#
#   ./scripts/ci-secrets.sh          check: all present, SITE_WORDS not older
#                                    than .site-words
#   ./scripts/ci-secrets.sh --push   write all four to both stores
#
# Values are piped to `gh`, never passed as arguments or printed.
set -euo pipefail

cd "$(dirname "$0")/.."

IDENTITIES=(PUBLIC_DOMAIN DUCKDNS_HOST HEADSCALE_SERVER_URL)
STORES=(actions dependabot)

if [ ! -s .site-words ] || [ ! -s .env ]; then
    echo "needs .site-words and .env at the repo root" >&2
    exit 2
fi

env_value() {
    grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}

site_words() {
    grep -vE '^[[:space:]]*(#|$)' .site-words \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | paste -sd, - | tr -d '\n'
}

if [ "${1:-}" = "--push" ]; then
    for store in "${STORES[@]}"; do
        for name in "${IDENTITIES[@]}"; do
            value=$(env_value "$name")
            if [ -z "$value" ]; then
                echo "$name is empty in .env" >&2
                exit 1
            fi
            printf '%s' "$value" | gh secret set "$name" --app "$store" >/dev/null
        done
        site_words | gh secret set SITE_WORDS --app "$store" >/dev/null
        echo "$store: ${IDENTITIES[*]} SITE_WORDS written"
    done
    exit 0
fi

# ponytail: staleness is judged by timestamp, for SITE_WORDS only. An edit to
# .site-words that is reverted still reads as stale, and a changed identity in
# .env is not noticed at all -- .env changes for unrelated keys too often for
# its mtime to mean anything. Re-run --push after changing an identity.
words_mtime=$(date -u -r .site-words +%Y-%m-%dT%H:%M:%SZ)
status=0
for store in "${STORES[@]}"; do
    listing=$(gh secret list --app "$store" --json name,updatedAt \
        -q '.[] | "\(.name) \(.updatedAt)"')
    for name in "${IDENTITIES[@]}" SITE_WORDS; do
        updated=$(awk -v n="$name" '$1 == n { print $2 }' <<<"$listing")
        if [ -z "$updated" ]; then
            echo "   $store: $name is missing"
            status=1
        # ISO-8601 UTC timestamps order correctly as strings.
        elif [ "$name" = SITE_WORDS ] && [[ "$updated" < "$words_mtime" ]]; then
            echo "   $store: SITE_WORDS ($updated) is older than .site-words ($words_mtime)"
            status=1
        fi
    done
done
if [ "$status" -ne 0 ]; then
    echo "   fix: ./scripts/ci-secrets.sh --push"
fi
exit "$status"
