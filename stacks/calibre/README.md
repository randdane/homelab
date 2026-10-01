# Calibre + Calibre-Web

**What:** Calibre manages the library — imports, metadata editing, format
conversion — over a browser-delivered desktop session. Calibre-Web is the
pleasant reading and browsing front end over the same library.
**Why I care:** Ebooks bought or downloaded once, kept in a format that
outlives any particular store or device.
**URLs:** Calibre http://localhost:8088 · Calibre-Web http://localhost:8089

## Notes

**One stack, because the legacy definitions had a silent bug.** `Calibre/`
and `Calibre-Web/` each declared their *own* `books` volume. Calibre-Web reads
the `metadata.db` that Calibre writes — with separate volumes it would have
started cleanly, reported healthy, and shown an empty library forever. They
share one `books` volume here and one lifecycle, because they are not
independently useful.

**Calibre must run first.** Calibre-Web needs an existing `metadata.db` to
point at, and there isn't one until Calibre has created the library. On first
setup: start the stack, open Calibre, create the library in `/books`, then
configure Calibre-Web to use `/books`.

**Three volumes, and the split matters.** `books` is the library. `config` is
Calibre's settings. `web-config` is Calibre-Web's users, shelves, and read
state — deliberately separate, so resetting one tool does not lose the other's
data. All three are backed up.

**`DOCKER_MODS=linuxserver/mods:universal-calibre`** on Calibre-Web is what
gives it Calibre's conversion binaries. Without it, "send to Kindle" and
format conversion fail with an unhelpful error.

**The full Calibre container is heavy** — it ships an entire desktop session.
If you only ever read and never import, Calibre-Web alone would do; the pair
exists because bulk metadata editing is genuinely painful without the real
application.
