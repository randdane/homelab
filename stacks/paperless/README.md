# Paperless-ngx

**What:** Scan paper in, get an OCR'd, tagged, full-text-searchable archive.
Tika and Gotenberg add Office and email documents.
**Why I care:** The paper it replaces is often the only copy — and the reason
to keep paper at all is being able to find it.
**URL:** http://localhost:8000

## Notes

**Two bugs in the legacy definition, either of which stopped it starting:**

- `env_file: docker-compose.env` pointed at a file that does not exist; the
  directory contained `compose.env` (which was empty anyway). All settings are
  now explicit `environment:` entries.
- The `broker` service mounted `redis_data` while the volumes block declared
  `redisdata`. Compose rejects a mount of an undeclared volume.

The Postgres password was also hardcoded to `paperless`.

**`PAPERLESS_SECRET_KEY` is required and has no default here.** Django signs
session cookies with it. A well-known value means anyone can forge a session;
changing it later logs everyone out.

**The consume folder is a named volume**, which is awkward on a server where
you want a scanner to drop files into it. Swap it for a bind mount there:

```yaml
      - /srv/scans:/usr/src/paperless/consume
```

Files are deleted from it once ingested, which is why it is excluded from
backup along with `export` (a manual dump target) and `redis-data` (the task
queue).

**`data` and `media` are both backed up, plus Postgres.** `media` holds the
document originals; `data` holds the search index and thumbnails; Postgres
holds tags, correspondents, and metadata. All three are needed together.

**Tika is unpinned (`:latest`)** because it is a stateless conversion sidecar
with no on-disk format to migrate. Everything with state here is pinned.
