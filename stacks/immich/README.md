# Immich

**What:** Self-hosted photo and video library with search, albums, face
recognition, and phone apps that back up automatically.
**Why I care:** It replaces a phone camera roll. Photos are the least
replaceable data in the house.
**URL:** http://localhost:2283

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

**`upload` is a named volume here, which is a laptop convenience.** On the
server it should be a bind mount to real storage, because the photo library
will outgrow whatever Docker's volume directory sits on:

```yaml
    volumes:
      - /srv/photos:/data
```

Immich stores originals there — it is not a cache.

**Backup covers `upload` and `db-data`, and both are required.** The database
holds albums, people, and the search index; the volume holds the files. A
restore of one without the other is a broken library. `model-cache` is
excluded — several GB of downloadable CLIP and face-detection models.

**Upgrade by hand, never unattended.** Immich runs schema migrations on
startup and the release notes regularly carry breaking changes.
