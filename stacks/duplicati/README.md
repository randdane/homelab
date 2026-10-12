# Duplicati

**What:** Scheduled, encrypted, incremental backups to somewhere else —
cloud storage, another machine, an external disk.
**Why I care:** The `backup` stack writes nightly archives to this machine.
A dead disk, a theft, or a fire takes the originals and the archives together.
This is the copy that leaves the building.
**URL:** https://duplicati.${PUBLIC_DOMAIN} (since 2026-09-29). Three layers:
Caddy's `remote_ip` gate, Authentik forward-auth (group `admin-uis`), then
Duplicati's own password. It was loopback-only before, because this UI can
read the B2 credentials and delete backup sets; the loopback port stays for
`scripts/run_backups.sh` and as break-glass
(`ssh -N -L 8200:127.0.0.1:8200 homelab`). Duplicati 2.1+ rejects an
unlisted `Host`, hence `DUPLICATI__WEBSERVICE_ALLOWED_HOSTNAMES`.

## Status

Running on `homelab`. Two jobs since 2026-10-12, each in its own B2 bucket
with its own application key and passphrase:

| Job (ID) | Sources | Bucket | `keep-time` |
|---|---|---|---|
| `offsite-data` (5) | `/source/`, `/source-state/` (minus `backups/`) | `homelab-data-<suffix>/duplicati` | `10D` |
| `offsite-photos` (6) | `/source-photos/` (minus `thumbs/`, `encoded-video/`) | `homelab-photos-<suffix>/duplicati` | `90D` |

Both: AES, zip level 1, 50 MB dblocks, **auto-compact on**.

**Their own schedules are disabled.** The `homelab-backup` systemd timer runs
the local archive and then triggers every job in `DUPLICATI_JOBS` (in `.env`),
in that order, via `scripts/run_backups.sh`. It attempts every job even if one
fails, and the run fails if any did. Two independent schedules an hour apart
only worked while nothing ever caught up after a boot; see that script's
header.

**Why two buckets.** A leaked key or a wrong setting reaches one data set,
and each bucket's size and restore test read on their own. The photos keep
90 days because a mass delete in Immich can go unnoticed for weeks and photos
rarely change, so long history costs little; the archives change every night
and anything older than a few days is stale.

The single job `homelab-offsite` (ID 1, bucket `homelab-offsite-<suffix>`)
it replaced filled the 10 GB free tier: it was fed gzipped archives, which
defeat dedup, compaction was off because of the lock, and the photos were
then added to it. It failed every night from 2026-10-09 (`storage_cap_exceeded`
on the last). It is kept, unscheduled, until the new jobs have been through a
full retention cycle (Vikunja #67).

### Object Lock

Both buckets: **governance, 7 days**. The Duplicati keys hold only
`listBuckets listFiles readFiles writeFiles deleteFiles` — **no
`bypassGovernance`**, so a stolen key cannot erase the last week. Tested
2026-10-12: a fresh upload refused deletion, with and without
`bypassGovernance: true` in the request.

Three traps found setting this up:

- **The web UI's "Read and Write" key includes `bypassGovernance`.** Create
  the keys over the API (`b2_create_key` with an explicit capability list;
  on API v3 it takes `bucketId`, singular) and read the capabilities back.
- **`isFileLockEnabled: true` is not a lock.** A new bucket reported it with
  `defaultRetention.mode: null`, so uploads would have carried no retention
  at all. Set the default retention, and check an object, not the bucket.
- **Duplicati's destination test fails under the lock.** It uploads
  `duplicati-access-privileges-test.tmp` and then cannot delete it:
  `401 access_denied`. Expected; the file is harmless and ages out.

**Open question: does compaction work under the lock?** Retention and
compaction delete remote volumes; any younger than 7 days are refused. The
first real test is after day 10 of the data job. If deletes there are fatal,
the choice is between a longer `keep-time` and dropping the lock — do not
switch compaction off again; that is how the old bucket filled.

Lifecycle: "keep only the last version" (`daysFromHidingToDeleting: 1`).
`daysFromUploadingToHiding` must stay null — it hides *current* files.

**Why not `keep-versions`.** The lock sets a floor under retention: three
daily versions is three days, inside the lock window, so every prune would
hit locked objects. The same reasoning retired `--retention-policy`, which
the UI renders as the "Backup retention" dropdown and therefore cannot
remove; it came out over the API.

**Creating a job needs its passphrase.** `POST /api/v1/backups` without one
answers `400 Missing passphrase`, and the UI's import form failed validation
without saying where. The jobs were created by a script on `homelab` that
read the export file and prompted for the passphrase with `getpass`, so the
passphrase never touched a file. Editing a job over the API is safe: a PUT
preserves the passphrase rather than storing the mask it returns on read.

Job definitions live in the `config` volume, not in this repo, and that
volume is in the local nightly archive — so the offsite copy contains the
configuration needed to rebuild the offsite backup, one generation behind.
What it does NOT contain is `SETTINGS_ENCRYPTION_KEY` or the restore
passphrases. Those must live somewhere else.

### Verified end to end, 2026-10-12

First runs: data 1.21 GB in ~30 s, photos 20.6 GB (11,373 files) in 8.5 min,
0 errors, 0 warnings. Then the disaster path — `duplicati-cli restore
--no-local-db` inside the container as `abc`, every block from B2:

| Job | Restored | Check |
|---|---|---|
| data | newest archive, 421 MiB | sha256 matches the source; `tar -tzf` lists 13,514 entries |
| photos | newest Immich DB dump + 5 random originals | all 6 sha256 match |
| data, wrong passphrase | 0 files | decrypt refused |

Re-run after any change to a destination, key or passphrase. Two ways this
test lied before it passed — a `\$` left in the passphrase, and a root run
that broke the web login — are in `docs/recovery.md` §2.

## Notes

**The `source` mount is a bind, not a named volume.** An earlier version of
this compose file declared `source:` as a named volume while this README
described a bind mount. That combination mounts an empty directory: Duplicati
would have run on schedule, uploaded nothing, and reported success — the same
failure shape as the Calibre stack in `lessons-learned.md` §1, but on the one
stack whose entire job is being there when everything else is gone.

**This does not replace the `backup` stack; it stands behind it.** That one
makes consistent local archives of every registered volume. This one takes
archives offsite, encrypted, on a schedule. Point Duplicati's source at the
archive directory rather than at the live volumes — backing up already
consistent tarballs avoids reading databases mid-write:

```yaml
      - ${XDG_STATE_HOME:-${HOME}/.local/state}/homelab/backups:/source:ro
```

`:ro` is deliberate. Duplicati never needs to write to what it backs up.

**There is a second source, and it exists because the first one was not
enough.** `/source` is the archive directory only, so anything sitting
*beside* it in the state directory was backed up nowhere at all. Two things
already were: the Uptime Kuma push token, which did not survive the
2026-09-20 migration, and `porkbun-dns-backup-*.json` — the documented
rollback for the DNS wildcard removal, which existed only on the old
server, a machine backed up by nothing and scheduled for wipe. Both were
found by accident rather than by any check.

```yaml
      - ${XDG_STATE_HOME:-${HOME}/.local/state}/homelab:/source-state:ro
```

`keep/` inside that directory is for files that must not age out. The backup
stack prunes only `backups/`, and by age, so anything that must outlive
`BACKUP_RETENTION_DAYS` (3) cannot live there. Anything in the source is
referenced by every new version, so a file in `keep/` is protected by being
*live* rather than by retention.

It is empty unless something is deliberately parked there; `keep/README.txt`
on the host records what and why.

A second mount rather than widening `/source` to the parent, because the two
directories are only nested *by default*: `BACKUP_ARCHIVE_DIR` may put the
archives on another disk entirely, and mounting that parent would sweep in
whatever else lives there.

**Where they do nest, the job must exclude `/source-state/backups/`**, or
every archive is backed up twice. The job's sources and filters are:

| Setting | Value |
|---|---|
| Sources | `/source/`, `/source-state/` |
| Filter | exclude `/source-state/backups/` |

`/source-state/env-files/` holds a copy of every stack's `.env`, rebuilt by
`scripts/run_backups.sh` before each run (since 2026-10-12). Those files
hold the database passwords the restored volumes expect, and were otherwise
on the VM disk only, which vzdump keeps onsite.

**That filter lives in Duplicati's own database, not in this repo**, which is
the one genuinely fragile part of this arrangement — restoring Duplicati from
a bare `config` volume does not bring it back. Check it after any rotation or
rebuild. `SourceFilesCount` is the cheap way to tell: it should be the number
of archives plus the loose state files. If it is roughly double, the filter is
gone and the archives are being stored twice.

The exclusion is deliberately here rather than in the backup stack's
`BACKUP_EXCLUDE_REGEXP`. Losing it here costs duplicate content, which dedups
to nearly nothing; losing it there would put every previous tarball inside the
next one.

**Rotating any of these secrets:** follow `ROTATION.md` in this directory.
Duplicati has no in-place rotation for the settings key, so it means wiping
the settings database and recreating the job — worth doing from a written
procedure rather than improvising, because the old credential must not be
destroyed until the new one has passed a restore test.

**`SETTINGS_ENCRYPTION_KEY` protects the credentials for every destination**,
and 2.1+ refuses to start without it. Store it somewhere that is *not* only
this machine — Vaultwarden, or on paper. Losing it means reconfiguring every
backup job by hand.

**The restore passphrase matters more than any of this.** Duplicati encrypts
the backup itself with a passphrase you set per job. Without it the offsite
copy is noise. It is not stored in this repo and cannot be recovered from it.

**Test a restore.** An untested backup is a belief, not a backup. Restoring a
single small file to a scratch directory takes two minutes and is the only
evidence the chain works end to end.

**`config` is backed up locally too**, so a rebuild of this machine does not
also lose the job definitions and the block database that makes incrementals
incremental. `source` and `backups` are excluded — the first is other stacks'
data, the second is output.
