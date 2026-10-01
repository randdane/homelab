# Navidrome

**What:** Music server, Subsonic-compatible — works with any Subsonic client
app on a phone.
**Why I care:** Jellyfin technically plays music, but handles a real library
badly: no proper artist grouping, weak playlist support, and clients that are
built for video.
**URL:** http://localhost:4533

## Notes

**The `music` volume is a placeholder.** It is a named volume so the stack runs
on the laptop with nothing mounted. On the server, replace it with a bind mount
to the real library:

```yaml
    volumes:
      - /srv/music:/music:ro
```

It is already `:ro` here — Navidrome never needs to write to the library.

**`data` is backed up, `music` is not.** That split is deliberate and the
`homelab.backup: exclude` label on `music` records it. `data` is small and
holds play counts, ratings, playlists, and users — none of which come back
from a rescan. The library itself is far too large for a nightly tarball.

**No healthcheck.** The image ships neither `curl` nor `wget`, and a
healthcheck that calls a missing binary reports unhealthy forever, which is
worse than having none. The tracker will show this stack as `up` on presence
alone.

**First scan is slow** on a large library, and the default schedule here only
rescans daily. Trigger one manually from the UI after adding music.
