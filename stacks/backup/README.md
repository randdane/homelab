# backup

**What:** Nightly tar.gz of every named volume in this repo, via
`offen/docker-volume-backup`.
**Why I care:** Named volumes are chosen over bind-mounted data specifically
so one mechanism covers all of it. This is that mechanism.
**URL:** none. Triggered by the `homelab-backup` systemd timer, not by its own cron.

> [!NOTE]
> **Restoring from these archives:** `docs/recovery.md` — verified end to end
> on 2026-08-24, including pulling from B2 and standing both databases up in
> throwaway containers.

## What `stop-during-backup` does and does not guarantee

It stops the container before the archive and starts it after, so nothing is
written mid-read. It does **not** guarantee the application checkpointed its
database on the way down.

Measured 2026-08-24 across this repo's archives: Jellyfin and Postgres come
out clean, with no sidecar files. Vikunja does not — its archive contains a
live `vikunja.db-wal` holding transactions that are not in `vikunja.db`.

Two consequences:

- **Restore whole directories, never individual files.** Taking `vikunja.db`
  alone silently rolls the database back to its last checkpoint, and
  `integrity_check` still says `ok`. See `docs/recovery.md` §5.
- **A sidecar in the archive tells you that service never checkpoints on
  stop**, so its live files must never be inspected with `cp` either. See
  `docs/lessons-learned.md` §15.

## Recreate this container after adding a stateful stack

Its mount list is fixed when the container is created. A `backup` container
started before a stack existed keeps archiving the old set forever **and
reports success while doing it**. This silently excluded `authentik_database`
and `caddy_data` until 2026-08-23 — the account database and the ACME keys,
the two least replaceable things here.

```bash
cd /opt/homelab/stacks/backup && docker compose up -d --force-recreate
docker exec backup sh -c 'cd /backup && for d in *; do \
  printf "%-24s %s\n" "$d" "$(ls -A "$d" 2>/dev/null | wc -l)"; done'
```

Every stack you actually run should show a non-zero count.

## Running this on a host that has not deployed everything

`compose.yaml` is the canonical registry: every volume in the repo that must
be archived, on any host. Every entry is `external: true`, so on a host
missing any of them Compose refuses to start the whole stack:

    external volume "lgtm_mimir-data" not found

That is most hosts — including a new server in the middle of a staged
migration, where nothing is backed up yet and the need is greatest. Render a
host-specific file first:

    uv run scripts/backup_here.py
    docker compose -f compose.host.yaml up -d

It prints the volumes it is skipping every single run. That is deliberate: a
backup covering 3 of 49 volumes looks identical to one covering everything
until the day you try to restore. **Re-run it after deploying any new
stateful stack**, or that stack's volume is silently outside the backup set.

`compose.host.yaml` is generated and gitignored. Edit `compose.yaml`.

The tempting alternative — `docker volume create` for the 46 missing volumes
so the canonical file starts — is a trap: it produces successful nightly
archives of empty directories.

## What is deliberately not in the archive

Whole volumes opt out with a `homelab.backup: exclude` label on the volume.
*Parts* of a volume opt out with `BACKUP_EXCLUDE_REGEXP` here, which is a
blunter tool — it matches paths across every source at once, so keep the
patterns specific enough to name one stack's directory.

Currently excluded: `jellyfin-config/metadata/` — now moot: since the move to `pve`, Jellyfin's config is backed up by `vzdump` of its LXC, not by this stack. The default is left in place because it matches nothing and costs nothing.

Measured 2026-08-21, before excluding it:

| | |
|---|---|
| Whole archive | 1.1 GB |
| `jellyfin_config` | 1.1 GB (3,714 image files) |
| ...of which `metadata/People` | 524 MB — 2,432 actor headshots |
| ...of which `metadata/library` | 550 MB — posters, backdrops, logos |
| `life-queue_n8n` | 6.2 MB |
| `life-queue_vikunja` | 4.6 MB |
| `headscale_data` | 204 KB |
| **Irreplaceable data in total** | **~32 MB** |

Two things made this worth fixing:

**gzip was doing nothing.** JPEG and PNG are already compressed; 200 sample
files went from 40,806 KB to 40,321 KB — 98%. A `.tar.gz` of an image cache
is a `.tar` with extra steps.

**The data is regenerable.** Jellyfin re-fetches artwork from TMDB on a
library scan. Fourteen days of retention meant carrying ~15 GB to avoid one
click after a restore.

The test to apply before excluding anything: *can this be rebuilt from a
source that is not this backup?* Size is what makes it worth doing; being
regenerable is what makes it safe. Do not exclude something merely because it
is big — the media library is excluded because it can be downloaded again,
not because of its size.

Apply that test against a **dead disk**. "It is still on disk" is not a
source; that is the disk the backup exists to survive. The media library
passes anyway — it can be re-acquired from where it came from — but the
accepted loss is real and written down in `docs/recovery.md`: 588 GB, weeks
of re-downloading, and nothing personal in it.

**Known loss:** artwork replaced by hand inside Jellyfin also lives in
`metadata/` and will not come back from TMDB. Anything auto-fetched will.

## Adding a volume to the backup

It does not auto-discover volumes. **Two Compose entries per volume**, not per
stack — a four-volume stack costs eight lines:

1. `- <project>_<volume>:/backup/<name>:ro` under the backup service.
2. `<project>_<volume>: {external: true}` under the top-level `volumes`.

Volume names are compose-prefixed with the project `name:`. Check with
`docker volume ls`.

Also add `docker-volume-backup.stop-during-backup: "true"` to the source
container. That label is the *only* thing the label mechanism does — it pauses
the container so the archive is not caught mid-write. It does not cause the
volume to be backed up.

## Restore

    ARCHIVE="${XDG_STATE_HOME:-$HOME/.local/state}/homelab/backups"
    docker run --rm -v lgtm_grafana-data:/target -v "$ARCHIVE":/archive \
      alpine sh -c "tar -xzf /archive/<file>.tar.gz -C /target --strip-components=2 backup/lgtm-grafana"

Inspect the archive first: `tar -tzf <file>.tar.gz | head`.

The selector above (`backup/lgtm-grafana`, no leading slash) is correct for
the command as written, because it always runs inside the `alpine` image,
whose busybox tar strips the leading `/` from stored member names *before*
matching. Verified: extracting into a throwaway volume and inspecting its
contents.

If you instead extract with the host's own tar (GNU tar, e.g. running this
directly on a server without docker) the selector needs a leading slash --
GNU tar matches against the raw stored name, slash included, and only strips
it afterward when writing files to disk:

    tar -xzf <file>.tar.gz -C /some/target --strip-components=2 /backup/lgtm-grafana

Also verified by extraction. The two tools disagree on this, so copy the
selector that matches whichever tar you are actually running, not the other
one.

## Notes

- Archives default to `${XDG_STATE_HOME:-$HOME/.local/state}/homelab/backups`,
  outside the repo. Override with `BACKUP_ARCHIVE_DIR`.
- `TZ` is passed to the container on purpose. It still matters for timestamps
  even though the cron expression no longer schedules anything.
- **The container's cron is deliberately disabled** —
  `BACKUP_CRON_EXPRESSION=0 0 31 2 *`, a date that never occurs. Container
  cron has no catch-up: a host that was powered off at 03:00 on
  two nights running simply lost both with nothing
  reporting a problem. `homelab-backup.timer` uses `Persistent=true`, so a
  missed run fires after the next boot instead of vanishing.
- Verify the first run rather than trusting the schedule. See below.
