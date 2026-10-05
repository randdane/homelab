# ytdl-sub

Downloads the last two months of a few YouTube channels, writes them as TV
shows Jellyfin reads natively (`.nfo`, thumbnails, one season per year), and
deletes anything older. Watch in Jellyfin; there is no separate UI.

Chosen over Tube Archivist (2026-09-29): that one runs Elasticsearch and its
own player, 2–4 GB, to duplicate what Jellyfin already does. This is a cron
job that uses memory only while it runs.

## Channels

Site data: `$SITE_DIR/stacks/ytdl-sub/site/subscriptions.yaml`, mounted at
`/ytdl-site` (template in `site.example/`). Add a line, commit and push the
site repo, `git pull` it on `homelab`; the next 04:00 run picks it up. The key is the show name Jellyfin
displays.

## Storage: virtiofs from `pve`

`homelab` cannot see `tank`, and its own disk is 64 GB. The videos go to
`/tank/media/youtube` on `pve`, a plain directory inside `tank/media` — not a
child dataset, because Jellyfin's `mp0` bind of `/tank/media` would not show
a nested mount. So Jellyfin already sees it, read-only, at `/media/youtube`.

One-time setup (🛑 the VM needs a full stop/start, not a reboot, to gain the
device):

```bash
# on pve
install -d -o 1000 -g 1000 -m 755 /tank/media/youtube
pvesh create /cluster/mapping/dir --id youtube --map node=pve,path=/tank/media/youtube
qm set 101 --virtiofs0 youtube
qm shutdown 101 && qm start 101

# on homelab
sudo install -d /mnt/youtube
echo 'youtube /mnt/youtube virtiofs defaults,nofail 0 0' | sudo tee -a /etc/fstab
sudo mount /mnt/youtube && findmnt /mnt/youtube
```

Files must stay world-readable: the Jellyfin CT reads them as `nobody`. The
image's default umask (022) does that.

Then in Jellyfin: a **Shows** library on `/media/youtube`.

And `cp .env.example .env` with `SITE_DIR` set; without it compose refuses
to start.

## Running by hand

```bash
docker compose run --rm -w /tmp --user 1000:1000 --entrypoint ytdl-sub ytdl-sub \
  --dry-run --config /ytdl/config.yaml sub /ytdl-site/subscriptions.yaml
```

A dry run is slow, not stuck: throttle protection sleeps ~20 s per video
even with `--dry-run`. Don't wrap it in `timeout` — that kills the client and
leaves the `run` container going.

`-w /tmp` matters: the default workdir `/config` is root-owned in a fresh
volume, and ytdl-sub puts its lock file in the working directory
(`Permission denied: '/config/.ytdl-sub-lock'`). Drop `--dry-run` to download.
Scheduled runs log to `docker logs ytdl-sub`.

## What will bite you

- **YouTube breaks yt-dlp** every few weeks. `UPDATE_YT_DLP_ON_START=stable`
  means `docker compose restart` is the fix; a newer image is the slower one.
- **Nothing is backed up.** `tank` is one disk, and B2 does not take media.
  Only Recent keeps it small enough not to matter.
- **Not monitored.** No healthcheck (no HTTP to probe) and no Kuma monitor.
  A run that fails every night is visible only in the container log.
- **No Authentik**: there is no UI to put it in front of (§0 "neither").
