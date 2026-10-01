#!/usr/bin/env bash
# Prove the offsite backups can be reached and decrypted using ONLY what is in
# the password manager -- no repo, no .env, no Duplicati database, no server.
#
#   ./scripts/recovery-drill.sh
#   DRILL_KEEP=1 ./scripts/recovery-drill.sh   # keep the raw duplicati output
#
# Exit codes, because this is worth scripting against:
#   0  passed        1  the backup or this script is wrong
#   2  empty input   3  the network never reached B2, so nothing was tested
#   4  the drill's own verdict stands, but it could not delete the decrypted
#      data it restored -- see cleanup() below
#
# That is the path you are actually on after a disk failure, and it is the one
# path never exercised by ordinary operation: every normal restore starts from
# a working Duplicati that already knows the bucket and holds the keys. This
# script refuses to read any of that, on purpose. If it passes on a machine
# that has never touched B2, the recovery is real.
#
# Run it somewhere that is NOT the server. Nothing is written outside a temp dir,
# and nothing is restored into the live homelab -- one small file is pulled to
# a scratch directory and thrown away.
#
# Secrets are prompted for, never taken as arguments: an argument lands in the
# shell history of the machine you are proving you can recover from.
set -euo pipefail

IMAGE="${DRILL_IMAGE:-duplicati/duplicati:latest}"
WORK="$(mktemp -d)"
# DRILL_KEEP=1 leaves the raw duplicati output behind. Every failure below
# already prints it, but a *successful* run throws away the one thing you need
# to see when its format shifts -- which is what turned diagnosing the list
# format into two round trips. No secrets land here: they go in by environment.
#
# The restore step runs as root inside the container, so the archive lands in
# $WORK owned by root and mode 444. Measured 2026-09-03: `rm -rf "$WORK"` then
# fails, and a drill that printed PASS left a decrypted 9.5 MB archive --
# private keys included -- on disk, with the invoking user unable to delete it.
# It is 0700 so nobody else can read it, but every run leaked another copy.
# Hand ownership back first; the image is already pulled, so it costs a second.
#
# The trap must also be able to FAIL the drill, which is the part the first fix
# missed. An EXIT trap inherits the status it was entered with, so
# `rm -rf ... || echo WARNING` printed its warning and still exited 0: PASS on
# stdout, plaintext on disk, and a caller with no way to tell. The status is
# now decided by whether $WORK is gone rather than by rm's return code -- the
# postcondition is what is worth checking, and it is why the chown above is
# allowed to fail silently. Exit 4 is distinct from the drill's own 1/2/3 so
# "the backup is fine, the scratch directory is not" stays distinguishable
# from "the backup is broken".
cleanup() {
    local rc=$?
    docker run --rm -v "$WORK:/work" --entrypoint chown "$IMAGE" \
        -R "$(id -u):$(id -g)" /work >/dev/null 2>&1 || true
    if [ -n "${DRILL_KEEP:-}" ]; then
        # Say what is being left behind. A passing drill leaves a real
        # decrypted archive here -- private keys included. mktemp -d gives
        # 0700, so other users cannot read it; the point is that it is
        # plaintext on disk and nothing will clean it up.
        echo "kept: $WORK (holds DECRYPTED backup data -- rm -rf when done)"
        exit "$rc"
    fi
    rm -rf "$WORK" 2>/dev/null || true
    if [ -e "$WORK" ]; then
        echo "FAILED -- $WORK could not be removed and holds DECRYPTED backup" >&2
        echo "data, private keys included. Remove it by hand. Whatever the drill" >&2
        echo "reported above about the backup itself still stands; this exit" >&2
        echo "code is about the plaintext left behind." >&2
        exit 4
    fi
    exit "$rc"
}
trap cleanup EXIT

ask() {  # ask VAR "prompt" [--secret]
    local var="$1" prompt="$2" secret="${3:-}" value=""
    if [ -n "$secret" ]; then
        read -rsp "$prompt: " value; echo
    else
        read -rp "$prompt: " value
    fi
    [ -n "$value" ] || { echo "  empty -- nothing to test" >&2; exit 2; }
    printf -v "$var" '%s' "$value"
}

cat <<'INTRO'
Recovery drill -- password-manager-only path.

Everything below must come from the password manager. If you find yourself
reaching for the repo, the server, or a .env file to answer one of these, that is
the finding: stop and write it down.
INTRO

# Both halves, not just the bucket. Duplicati writes under a prefix inside the
# bucket, and listing the wrong prefix succeeds and finds nothing -- which is
# why an empty listing is a failure below, not a pass.
echo
echo "The bucket AND the path inside it, as one string. Duplicati's TargetURL"
echo "is b2://<bucket>/<path> -- you need everything after the b2:// ."
ask BUCKET   "B2 bucket/path (both parts, e.g. my-bucket-a1b2c3/homelab)"
ask KEYID    "B2 application key ID" --secret
ask APPKEY   "B2 application key" --secret
ask PASSWORD "Duplicati backup passphrase" --secret

# Credentials go in via the environment, not argv: argv is visible in `ps` to
# every user on the box, and this drill exists to be run on a machine you do
# not yet trust.
run() {
    docker run --rm -i \
        -e AUTH_USERNAME="$KEYID" -e AUTH_PASSWORD="$APPKEY" \
        -e PASSPHRASE="$PASSWORD" \
        -v "$WORK:/work" \
        "$IMAGE" duplicati-cli "$@" --number-of-retries=1
}

echo
echo "== 1. reach the bucket and list versions"
if ! run list "b2://$BUCKET" > "$WORK/list.txt" 2>&1; then
    sed 's/^/   /' "$WORK/list.txt"
    # Separate "the network ate it" from "the credential is wrong" before
    # naming a secret. Measured 2026-09-02 on a corporate wifi that
    # intercepts TLS to api.backblazeb2.com: this step failed with
    # AuthenticationException, and the message below sent the drill's
    # operator toward rotating a B2 key that had never been transmitted.
    # A blocked handshake and a rejected key look nothing alike in the
    # stack trace and identical in a one-line verdict.
    if grep -qiE 'SSL connection could not be established|AuthenticationException|corrupted frame|NameResolution|No such host|Connection refused|timed out' "$WORK/list.txt"; then
        echo "   FAILED -- the connection to B2 never completed, so NO credential" >&2
        echo "   was tested. This is the network you are on, not your backup." >&2
        echo "   Confirm with:  curl -sS -o /dev/null https://api.backblazeb2.com/" >&2
        echo "   rc=35 means TLS is being intercepted or blocked. Re-run the" >&2
        echo "   drill from a network that does not do that." >&2
        exit 3
    fi
    # Not the passphrase: this step never uses it. Saying so keeps the
    # drill from sending you to look up the wrong secret.
    echo "   FAILED -- could not reach the bucket. That is the bucket path or" >&2
    echo "   the B2 key, NOT the passphrase (unused until step 2)." >&2
    exit 1
fi
# [[:space:]], not ' ': duplicati-cli indents fileset lines with a TAB, so
# '^ *[0-9]' matched nothing and the drill reported "no backup versions" over
# a listing of seven of them -- then blamed the bucket path, which was right.
VERSIONS="$(grep -cE '^[[:space:]]*[0-9]+[[:space:]]*:' "$WORK/list.txt" || true)"
# An empty listing is a FAILURE, not a pass. Listing a bucket without the path
# inside it succeeds and returns nothing, and reporting that as ok would make
# this drill certify a recovery that cannot happen -- the precise failure it
# was written to catch.
if [ "$VERSIONS" -eq 0 ]; then
    sed 's/^/   /' "$WORK/list.txt"
    # Distinguish the two ways to get here. Blaming the path when duplicati
    # clearly printed a fileset header sends you to re-enter a correct bucket
    # over and over, which is what this drill did on its first real run.
    if grep -qi 'Listing filesets' "$WORK/list.txt"; then
        echo "   FAILED -- duplicati listed filesets but none could be parsed" >&2
        echo "   out of its output. The credentials are fine; this script's" >&2
        echo "   parser is wrong. Fix the regex above, do not re-enter secrets." >&2
    else
        echo "   FAILED -- reached B2 but found no backup versions." >&2
        echo "   Almost always the path: you gave the bucket without the prefix" >&2
        echo "   Duplicati writes under. Try <bucket>/<path>, not <bucket>." >&2
    fi
    exit 1
fi
echo "   ok -- $VERSIONS version(s) visible"

echo
echo "== 2. decrypt: list the files inside the newest version"
# The "*" filter is load-bearing. `duplicati-cli list <url>` with no filter
# lists FILESETS, not their contents -- so this step used to re-print step 1's
# version list, count its lines, and announce that the passphrase decrypts.
# It happened to be true (reading a dlist requires the passphrase), but it was
# not what the line claimed, and step 3 then found no path to restore.
if ! run list "b2://$BUCKET" "*" --version=0 --all-versions=false \
        > "$WORK/files.txt" 2>&1; then
    sed 's/^/   /' "$WORK/files.txt"
    echo "   FAILED -- listing versions worked but reading one did not." >&2
    echo "   That is the passphrase, not the B2 key." >&2
    exit 1
fi
# Reported, not asserted. Zero paths here means step 3 finds no TARGET and
# fails there with a better message, so gating on this count only adds a
# second parser that can stop you reaching the proof it is standing in front
# of. `grep -c .` counted duplicati's progress chatter and could not return
# zero; this counts paths, which is worth printing and not worth failing on.
FILES="$(grep -cE '^[[:space:]]*/' "$WORK/files.txt" || true)"
echo "   ok -- version decrypted, $FILES file path(s) listed"

echo
echo "== 3. actually restore one file"
# The smallest real proof: pull something out and confirm bytes landed. A
# listing can succeed against an archive that will not extract.
# [[:space:]] here too, for the same reason: '[^ ]' happily swallows a tab
# and hands duplicati a path with whitespace glued to it.
# Prefer an archive; fall back to any listed path. Trailing "/" is excluded
# because a directory restores as an empty tree and the byte check below would
# then fail for a reason that has nothing to do with the backup.
TARGET="$(grep -oE '/[^[:space:]]*\.tar\.gz' "$WORK/files.txt" | head -1 || true)"
if [ -z "$TARGET" ]; then
    TARGET="$(grep -oE '^[[:space:]]*/[^[:space:]]*[^/[:space:]]$' "$WORK/files.txt" \
              | sed 's/^[[:space:]]*//' | head -1 || true)"
fi
if [ -z "$TARGET" ]; then
    sed 's/^/   /' "$WORK/files.txt"
    echo "   FAILED -- nothing in the listing looked like a file path, so" >&2
    echo "   nothing could be restored. Steps 1 and 2 only prove the keys" >&2
    echo "   reach the bucket; this step is the one that proves a restore" >&2
    echo "   works, and a drill that skips it has proved nothing. Either the" >&2
    echo "   archive is empty or Duplicati changed its list format -- fix the" >&2
    echo "   TARGET pattern above." >&2
    exit 1
fi
echo "   restoring: $TARGET"
# --no-local-blocks is gone: 2.3.0 deprecated it, and not using local blocks is
# now the default anyway. It was not merely noise -- duplicati-cli exits
# non-zero on any warning, so the deprecation notice made a restore that wrote
# 9.13 MiB report FAILED.
RC=0
run restore "b2://$BUCKET" "$TARGET" \
    --restore-path=/work/restored > "$WORK/restore.txt" 2>&1 || RC=$?

# Bytes on disk decide, not the exit code. The exit code conflates "could not
# restore" with "restored, and also had an opinion"; the file either exists or
# it does not.
BYTES="$(find "$WORK/restored" -type f -printf '%s\n' 2>/dev/null \
         | awk '{t+=$1} END {print t+0}')"
if [ "$BYTES" -eq 0 ]; then
    sed 's/^/   /' "$WORK/restore.txt"
    echo "   FAILED -- nothing landed in the restore directory (exit $RC)." >&2
    echo "   recovery.md warns about a restore that reports success and" >&2
    echo "   writes nothing; this is that, or an outright failure above." >&2
    exit 1
fi
echo "   ok -- $BYTES bytes restored"

# Decrypting to the right length is not the same as decrypting to the right
# bytes. For an archive, gzip's own checksum settles it -- this is the
# "listing can succeed against an archive that will not extract" case the top
# of this step is about.
VALIDATED=""
case "$TARGET" in
    *.tar.gz)
        if gzip -t "$WORK/restored/$(basename "$TARGET")" 2>/dev/null; then
            echo "   ok -- gzip checksum valid, so the bytes are truly intact"
            VALIDATED=1
        else
            echo "   FAILED -- restored $BYTES bytes that are not a valid" >&2
            echo "   gzip archive. Decryption produced garbage of the right" >&2
            echo "   size, which is the worst way for a backup to be wrong." >&2
            exit 1
        fi
        ;;
esac

# Ignoring a non-zero exit is only safe because gzip re-checked the bytes
# independently. With no such check -- any target that is not an archive -- a
# partial file and a failed "Verifying restored files" step look exactly like
# this, so the exit code is the only evidence left and it has to count.
if [ "$RC" -ne 0 ]; then
    if [ -z "$VALIDATED" ]; then
        sed 's/^/   /' "$WORK/restore.txt"
        echo "   FAILED -- duplicati exited $RC and nothing here can check the" >&2
        echo "   $BYTES restored bytes independently ($TARGET is not an" >&2
        echo "   archive). Bytes on disk are not proof when the tool that" >&2
        echo "   wrote them reported a problem." >&2
        exit 1
    fi
    echo "   note -- duplicati exited $RC (warnings); gzip verified the bytes:"
    grep -iE 'warn|deprecat' "$WORK/restore.txt" | sed 's/^/     /' | head -3
fi

echo
echo "PASSED -- the password manager alone is enough to reach and decrypt the"
echo "offsite backups. Record today's date in docs/recovery.md."
