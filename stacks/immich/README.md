# Immich

**What:** Self-hosted photo and video library with search, albums, face
recognition, and phone apps that back up automatically.
**Why I care:** It replaces a phone camera roll. Photos are the least
replaceable data in the house.
**URL:** `https://immich.<PUBLIC_DOMAIN>` (gated vhost: LAN and tailnet). `127.0.0.1:2283` on `homelab` is the break-glass path over ssh.

## Notes

**The legacy definition could never have worked.** It was one
`immich-server` container with no database, no Redis, no machine-learning
service, and no volumes. This stack is adapted from the
[upstream release compose](https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml),
which is the only supported source — Immich's services are versioned together.

**The database is not an ordinary Postgres.** The image tag
`14-vectorchord0.4.3-pgvectors0.2.0` encodes the vector extension versions
Immich expects. Substituting stock `postgres:17` gives you a server that
starts normally and then fails every search and every face-recognition job.
When bumping `IMMICH_VERSION`, check the upstream compose for a matching
database image rather than bumping the app alone.

## Storage: virtiofs from `pve`

The library lives on `tank/photos`, a ZFS dataset on `pve`'s mirrored pool,
shared into the VM over virtiofs. It is a sibling of `tank/media`, not a
child: nothing else needs to see it. Immich stores originals there -- it is
not a cache.

```bash
# on pve
zfs create -o compression=lz4 -o atime=off -o xattr=sa -o acltype=posix tank/photos
install -d -m 755 /tank/photos/immich        # the guard directory, see below
pvesh create /cluster/mapping/dir --id photos --map node=<PVE_NODE>,path=/tank/photos
qm set <VMID> --virtiofs1 photos
qm shutdown <VMID> && qm start <VMID>        # a full stop/start: every stack on the VM is down ~2 min

# on the VM
sudo install -d /mnt/photos
echo 'photos /mnt/photos virtiofs defaults,nofail 0 0' | sudo tee -a /etc/fstab
sudo mount /mnt/photos && findmnt /mnt/photos
```

**The guard is a directory that exists only on the dataset.** The server's
`/data` is a bind of `/mnt/photos/immich`, with `create_host_path: false`. If
the share is not mounted, `/mnt/photos` is an empty directory on the VM disk,
`/mnt/photos/immich` does not exist, and compose refuses to create the
container. Binding `/mnt/photos` itself would not work, because the empty
mount point exists either way. Immich's `.immich` marker files are a second
guard only after the first start: Immich creates them on initialization, so a
first start on the wrong disk would have initialized happily. Duplicati's
`/source-photos` bind uses the same guard.

Test it before the first real start: with the share unmounted,
`docker compose up -d` must fail on the missing bind source. Remount it, and
it must start.

Postgres (`db-data`) and the model cache stay as named volumes on the VM
disk. Postgres is small, and it needs local-disk fsync semantics, which
virtiofs is not.

## Login

Authentik OIDC (`docs/adding-a-stack.md` section 3b), access restricted to the
group `immich-users`; Immich's OAuth settings are entered in its admin UI.
**Password login stays enabled**: it is the way in when Authentik is down. If
it is ever turned off, `immich-admin enable-password-login` inside the server
container turns it back on.

## Backup

| What | How | Where |
|---|---|---|
| `immich_db-data` | `backup` stack, nightly archive, `stop-during-backup` | the archive directory, then Duplicati -> B2 |
| `/mnt/photos/immich` on the VM (originals, uploads, Immich's own daily DB dumps in `backups/`) | Duplicati, incremental, direct (`/source-photos`) | B2 |
| `model-cache` | excluded (`homelab.backup: exclude`); re-downloaded | -- |

Adding `/source-photos/` to the Duplicati job is a UI step, because the job
lives in Duplicati's own database, not the repo (`stacks/duplicati/README.md`).
Exclude `/source-photos/thumbs/` and `/source-photos/encoded-video/`, which
Immich regenerates, so B2 holds originals, uploads and DB dumps. A restore
then regenerates thumbnails and transcodes, and must keep the `.immich`
marker files, which the backup includes.

**Pairing a database with its files.** A restore needs files at least as new
as the database it restores, never older. Extra files are harmless; missing
ones are broken assets. Photos are written once and never changed, so this is
a matter of ordering, not snapshots. Immich's automatic DB dump runs at
02:00. The backup timer (~03:00) archives the database volume, then runs
Duplicati, which reads the photos afterwards. So every DB dump in B2 sits in
the same Duplicati version as a file set taken after it.

**Recovery window.** A deleted photo stays on disk in Immich's trash for 30
days, and Duplicati keeps backing it up throughout. After the trash is
emptied, Duplicati's `keep-time` decides how long a version with it
survives: roughly 30 days in-app, then the keep-time window in B2. B2's
Object Lock is a floor that stops early deletion, not extra retention
(`stacks/duplicati/README.md`).

**Upgrade by hand, never unattended.** Immich runs schema migrations on
startup and the release notes regularly carry breaking changes.
