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

Running on `homelab`, reading `~/.local/state/homelab/backups`.

**Its own schedule is disabled.** The `homelab-backup` systemd timer runs the
local archive and then triggers this job, in that order, via
`scripts/run_backups.sh`. Two independent schedules an hour apart only worked
while nothing ever caught up after a boot; see that script's header.

Job: **homelab-offsite** -> `b2://homelab-offsite-<suffix>/homelab`. The bucket
is private and the application key is scoped to it.

### Object Lock, and what it forces

The bucket has **governance retention**, so an uploaded object cannot be
deleted before its retention expires — not by this host's key, not by anything
that compromises it. That is the point: without it, the credentials able to
destroy the only offsite copy sit on the machine being protected.

**The bucket default was lowered from 14 days to 7 on 2026-09-23.** That
changes what *new* uploads carry. It does **not** touch objects already
uploaded: B2 stamps retention at upload time, so every object written before
that date keeps its 14-day lock until it expires. The last of them clears
around **2026-10-07**, and until then the 14-day figure is still the binding
constraint.

Two Duplicati settings exist solely to avoid fighting that lock:

| Setting | Value | Why |
|---|---|---|
| `keep-time` | `16D` | Must exceed the lock, or deletes hit locked objects and every run errors. 16D clears the legacy 14-day stamps with margin. Drop to `10D` once they have expired — not before. |
| `--no-auto-compact` | `True` | Compaction deletes and rewrites remote volumes whenever wasted space crosses a threshold, including files uploaded yesterday. Under the lock those deletes are refused. |

**`keep-time` was `30D` until 2026-09-23.** It was cut to `16D` to drop the
pre-cutover history, which the owner did not want offsite. Measured on the run
that followed: `Success`, **0 errors, 0 warnings**, 15 filesets deleted,
35 versions → 21, oldest now 2026-09-08. No delete was refused, which is the
evidence that 16D clears the legacy locks.

**It freed no space, and that is expected.** `TargetFilesSize` was 6.653 GiB
before and after; only the `.dlist` files went (354 remote files → 345). The
data volumes behind a deleted version stay until compaction runs, and
compaction is off. Retention controls how many versions exist; it does not
reclaim bytes. Space relief needs compaction, not yet done.

**Why not `keep-versions`, which is what "keep 3 backups" sounds like.**
Object Lock sets a floor under retention: three daily versions is three days,
far inside the lock window, so every run would error trying to prune. The same
reasoning retired `--retention-policy` below. Three restore points belong in
the *local* rotation (`BACKUP_RETENTION_DAYS`), which no lock governs; the
offsite floor is lock + margin, currently 10 days.

`--retention-policy` was removed. It coexisted with `keep-time` and took
precedence, pruning daily versions after a week — inside the lock window.
Note that the UI *cannot* remove it: it renders `retention-policy` as the
"Backup retention" dropdown, so the value can be replaced but never deleted,
and it stays invisible in the advanced-options list. It came out over the API.

The B2 lifecycle rule is `daysFromHidingToDeleting: 30`,
`daysFromUploadingToHiding: null`. The second field must stay null — it hides
*current* files, which would start hiding live backup data.

**`isFileLockEnabled: true` is not enough.** A bucket can report file lock
enabled while `defaultRetention.mode` is `null`, in which case nothing is
actually locked and uploads carry no retention at all. That was true here for
several hours. Verify at the object level, not the bucket level:

```
b2 file info "b2://homelab-offsite-<suffix>/<some object>"   # fileRetention.mode
```

Editing this job over the API is safe: a PUT preserves the passphrase rather
than storing the `***************` mask it returns on read. That was proved
with a throwaway job, not assumed — the mask is a fixed 15 characters
regardless of the real length, so it cannot be checked by inspection.

Job definitions live in the `config` volume, not in this repo, and that
volume is in the local nightly archive — so the offsite copy contains the
configuration needed to rebuild the offsite backup, one generation behind.
What it does NOT contain is `SETTINGS_ENCRYPTION_KEY` or the restore
passphrase. Those are the two things that must live somewhere else.

### Verified end to end, 2026-08-21

Not "the job ran green" — the whole chain, from B2 back to usable data:

- Upload: 3 objects, 9.14 MB, all `.aes`.
- Restore from B2 with `--no-local-db` (i.e. the disaster path, where the
  local block database is gone too): `Restored 1 (9.132 MiB) files`.
- The restored archive is **byte-identical** to the original:
  `sha256 63f04519...ef47a` on both sides.
- Extracted it and checked what came out, rather than trusting the tarball:
  headscale `ok nodes=2`, vikunja `ok tasks=10`, n8n `ok workflows=1`,
  jellyfin `ok users=1`, and both headscale private keys present.
- Restoring with a deliberately wrong passphrase recovers **0 files** — so
  the data really is encrypted with the passphrase, not merely stored behind
  an account login.

Re-run that last set after any change to the destination or the key.

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
