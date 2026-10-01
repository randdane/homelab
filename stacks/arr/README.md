# arr

**What:** The media acquisition pipeline. Prowlarr manages indexers and syncs
them to Radarr (films), Sonarr (TV), and Lidarr (music); Bazarr fetches
subtitles; Jellyseerr takes requests; qBittorrent does the downloading, behind
a VPN.
**Why I care:** It is what keeps Jellyfin and Navidrome supplied without
anyone doing it by hand.
**URLs:** Radarr 7878 · Sonarr 8989 · Lidarr 8686 · Bazarr 6767 ·
Jellyseerr 5055 · Prowlarr 9696 · qBittorrent 8082

## Notes

**This replaces eight legacy directories.** `stack_arr/` was the good one;
the seven single-service directories picked competing tools for the same jobs
and are dropped — Prowlarr supersedes Jackett, qBittorrent-behind-Gluetun
supersedes a bare Transmission, and Jellyseerr is the Jellyfin-native fork of
Overseerr (Overseerr targets Plex first).

**qBittorrent and Prowlarr have no network stack of their own.**
`network_mode: "service:gluetun"` puts them inside the VPN container's
network. If the tunnel drops, their traffic stops rather than falling back to
your own address. That is the entire point — do not "simplify" it to the
default network.

Two consequences that look like bugs but are not:

- Their ports are published on the **gluetun** service, not on theirs. That is
  the only place they can be.
- If Gluetun fails to authenticate, **both containers stay down**. Look at
  `docker logs arr-gluetun` first, not at qBittorrent.

**VPN credentials are required and are not in this repo.** Set
`VPN_SERVICE_PROVIDER` plus either the WireGuard or OpenVPN variables in
`.env`; the exact names vary per provider, listed in the
[gluetun wiki](https://github.com/qdm12/gluetun-wiki). Both forms are wired
through so either works.

**qBittorrent's Web UI is on 8082, not the usual 8080** — IT-Tools already has
8080 in this repo.

**Media volumes are placeholders.** `downloads`, `movies`, `tv`, and `music`
are named volumes so the stack runs on the laptop. On the server they become
bind mounts to real storage, and `movies`/`tv` must be the same paths Jellyfin
reads, or Radarr will import into a directory nothing plays from:

```yaml
      - /srv/media/movies:/movies
      - /srv/downloads:/downloads
```

Keep downloads and media on **one filesystem** — the *arrs hardlink imports
rather than copying, and across filesystems that silently becomes a full copy.

**Configs are backed up, media is not.** The configs hold indexer definitions,
API keys, quality profiles, and request history — small and tedious to
rebuild. The media is large and re-acquirable by definition.

**First-run wiring** (none of it is automatic): set qBittorrent's password
from its startup log, add indexers in Prowlarr and let it sync them out, then
point Radarr/Sonarr/Lidarr at qBittorrent as a download client, and Jellyseerr
at Jellyfin plus Radarr/Sonarr.
