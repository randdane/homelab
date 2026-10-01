# wger

**What:** Workout logger — routines, sets, weights, body measurements, and
the history that shows whether any of it is working.
**Why I care:** Training history is only useful if it is unbroken, and it
cannot be reconstructed after the fact.
**URL:** http://localhost:8093

## Notes

**The nginx service could not have started.** The legacy compose mounted
`./config/nginx.conf`, and the `config/` directory contained only `prod.env`.
That file is written here.

**nginx is required even though Caddy exists.** Django does not serve its own
static files in production — without this nginx in front, wger loads with no
CSS and every image broken. Caddy would proxy to it, not replace it.

**Pinned to 2.6, not `:latest`.** Upstream's latest tag is currently
`2.7.0a2` — an alpha. A training log is a poor place for pre-release code.

**Port 8093, not 80.** The legacy file bound port 80 directly, which collides
with Caddy on the server.

**Celery is deliberately off.** Its job here is bulk-syncing wger's public
exercise and ingredient databases; a single-user instance does not need two
more containers for that. Exercise sync can be triggered by hand from the
admin when needed.

**First start is slow.** Django runs migrations against an empty database,
which takes minutes — hence the 300 second `start_period` on the healthcheck.
A shorter one marks the container unhealthy and restarts it mid-migration.

**`SECRET_KEY` and `SIGNING_KEY` are required with no defaults.** They sign
sessions and API tokens; a default value means forgeable sessions. The legacy
`config/prod.env` contained real values, which were not copied.

**Backed up: Postgres and `media`.** The training history and uploaded
images. `static` is regenerated from the image on every boot, so it is
excluded.
