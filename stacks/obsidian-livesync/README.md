# Obsidian LiveSync

**What:** A CouchDB tuned to be the sync backend for the Obsidian
Self-hosted LiveSync plugin.
**Why I care:** Notes sync between laptop and phone without a third party
holding them — and the notes stay plain Markdown files on every device, so
this container failing costs sync, not data.
**URL:** http://localhost:5984/_utils (CouchDB's own admin UI)

## Notes

**`config/10-homelab.ini` is required, not tuning.** CouchDB out of the box
rejects the plugin. Three settings matter:

- **CORS with `app://obsidian.md` and `capacitor://localhost`.** Without these
  the plugin cannot talk to the server from inside Obsidian at all, and the
  error it reports says nothing about CORS. `capacitor://` is mobile — omit it
  and desktop works while the phone mysteriously does not.
- **`single_node = true`**, or CouchDB waits to be told about a cluster and
  never finishes starting up.
- **`max_document_size = 50000000`**. The 8MB default rejects large notes and
  attachments with an opaque error.

**`local.d` is not persisted, deliberately.** CouchDB writes the hashed admin
password there at startup, and it re-derives it from `COUCHDB_USER` /
`COUCHDB_PASSWORD` every boot. Keeping it ephemeral means the `.env` file is
the single source of truth for those credentials, rather than a stale hash in
a volume silently winning.

**Per-device setup is manual.** Each device needs the plugin configured with
the URL, credentials, and database name, then one device seeds the vault and
the rest fetch it. Setting up a second device as though it were the first is
the standard way to get a conflict storm.

**This is a sync backend, not a backup.** It holds every revision the plugin
has sent, so it is worth archiving — but the authoritative copy of your notes
is the Markdown in your vault, on disk, on each device.
