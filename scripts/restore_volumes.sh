#!/usr/bin/env bash
# Restore named Docker volumes from a homelab backup archive onto a fresh host.
#
#   scripts/restore_volumes.sh ARCHIVE VOLUME...           # dry run: what would happen
#   scripts/restore_volumes.sh --apply ARCHIVE VOLUME...   # do it
#
# Volume -> archive directory comes from stacks/backup/compose.yaml, the
# canonical registry. Owners, modes and the volume root's own owner/mode are
# kept (Postgres refuses a data dir that is not 700). Refuses to touch a volume
# that already holds data: this is for empty hosts, not docs/recovery.md §5.
set -euo pipefail

apply=0
if [ "${1:-}" = "--apply" ]; then apply=1; shift; fi
if [ $# -lt 2 ]; then
  sed -n '2,6p' "$0" >&2
  exit 2
fi
archive=$(realpath "$1"); shift
registry="$(dirname "$0")/../stacks/backup/compose.yaml"

[ -f "$archive" ] || { echo "no such archive: $archive" >&2; exit 2; }

dir_for() {  # volume name -> directory under /backup in the archive
  sed -nE "s#^\s+- $1:/backup/([a-z0-9-]+):ro\s*\$#\1#p" "$registry"
}

members=$(tar -Ptzf "$archive" | sed 's#^/##')
plan=()
for vol in "$@"; do
  dir=$(dir_for "$vol")
  if [ -z "$dir" ]; then echo "FAIL $vol: not in $registry" >&2; exit 1; fi
  if ! grep -qx "backup/$dir" <<<"$members"; then
    echo "FAIL $vol: backup/$dir is not in the archive" >&2; exit 1
  fi
  # Empty is legitimate (authentik_certs is empty on the first server): restored as an
  # empty volume with the archived root owner/mode.
  n=$(grep -c "^backup/$dir/." <<<"$members" || true)
  if docker volume inspect "$vol" >/dev/null 2>&1 &&
     [ -n "$(docker run --rm -v "$vol":/v:ro alpine ls -A /v)" ]; then
    echo "FAIL $vol: volume exists and is not empty -- use docs/recovery.md §5" >&2; exit 1
  fi
  printf '%-32s <- backup/%-22s %6d entries\n' "$vol" "$dir" "$n"
  plan+=("$vol:$dir")
done

if [ "$apply" -eq 0 ]; then echo "dry run -- rerun with --apply"; exit 0; fi

# One pass over the archive whatever the volume count: it is ~360 MB compressed.
mounts=()
for p in "${plan[@]}"; do
  docker volume create "${p%%:*}" >/dev/null
  mounts+=(-v "${p%%:*}:/dst/${p#*:}")
done
docker run --rm -v "$archive":/a.tgz:ro "${mounts[@]}" alpine sh -euc '
  mkdir /w && tar xzf /a.tgz -C /w
  for d in /dst/*; do
    src=/w/backup/${d#/dst/}
    cp -a "$src/." "$d/"
    chown "$(stat -c %u:%g "$src")" "$d"
    chmod "$(stat -c %a "$src")" "$d"
    echo "ok   ${d#/dst/}  $(find "$d" | wc -l) entries, root $(stat -c "%u:%g %a" "$d")"
  done'
