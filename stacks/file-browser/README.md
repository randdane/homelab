# File Browser

**What:** A web UI for browsing, uploading, and editing files on the server.
**Why I care:** Occasionally you need to look at a media folder or fix a
config file from a device that has no shell.
**URL:** http://localhost:8087

## Notes

**Change the admin password immediately.** File Browser initialises with a
default administrator account on first run. Until you change it, anything
that can reach the port can read and write everything mounted.

**It has no data of its own** — `data: none` is accurate. `/srv` is a window
onto directories that belong to other stacks; the `database` volume holds only
users and settings, and is recreated by deleting it.

**Mount only what you need, read-only where you can.** On the server, replace
the placeholder `srv` volume with explicit bind mounts:

```yaml
    volumes:
      - /srv/media:/srv/media:ro
      - /srv/downloads:/srv/downloads
```

Whatever is mounted here is reachable by anyone who gets past the login page,
with the container's permissions. Mounting `/` "for convenience" turns a
file manager into a host compromise.

**Keep it `exposure: lan`.** This is the stack most obviously worth *not*
putting behind a public hostname, even with authentication in front — and
Tailscale already covers remote access.

**Overlaps ownCloud only superficially.** ownCloud syncs your documents and
owns them; this reads directories other things own. Different jobs.
