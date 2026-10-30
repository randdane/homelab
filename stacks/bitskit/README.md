# bitskit

**What:** About 140 small dev tools: encoders, converters, formatters,
generators, calculators, and canvas image and WebAudio tools. It's a Rust/WASM
rewrite of it-tools and omni-tools. The source lives in its own repo,
(a separate repo on the laptop), which has the spec and the list of upstream
tools that were left out, and why.
**Why I care:** It's the same reason as it-tools. Tokens, JWTs and hashes never
leave the browser. One 2.5 MB image serves both tool sets.
**URL:** `https://bitskit.<PUBLIC_DOMAIN>` (a gated vhost, open to the LAN and
the tailnet). `127.0.0.1:8098` on `homelab` is the break-glass path over ssh.

**No Authentik, deliberately** (`adding-a-stack.md` §0 "neither"). Like
it-tools, there is no login to replace, no stored data, and all the work
happens on the client. The `remote_ip` gate is the control.

## Deploying

There is no registry. `deploy.sh`, run on the laptop, does four things:
- builds the image at bitskit's current commit, tagged with the short sha
- pipes `docker save` into `docker load` over ssh
- writes `BITSKIT_TAG` into `.env` on `homelab`
- runs `docker compose up -d` for this stack only

It refuses to run with uncommitted changes in bitskit. To roll back, set
`BITSKIT_TAG` to the previous sha in `.env` (that image is still loaded) and
run `docker compose up -d`.

## What will bite you

- **HTTPS only.** Copy buttons (`navigator.clipboard`) and the camera
  recorder only work in a secure context. Plain `http://127.0.0.1:8098` works
  only through an ssh tunnel, which the browser counts as localhost.
- **The image is `FROM scratch`.** There is no shell to `docker exec` into.
  The healthcheck is the binary's own `--healthcheck` flag.
- **`docker image prune -a`** deletes old tags, and with them the rollback.

Stateless: no volumes, nothing to back up.

## Measured 2026-10-29 (image `a19c97f`)

- Image 18.5 MB, idle RAM 488 KiB (limit 32 MB), WASM 967 KB over the wire (brotli)
- `tests/smoke.mjs` (138 pages) and `tests/media.mjs` in the bitskit repo pass
  against the vhost, with no console or CSP errors
- Unknown names on the wildcard still abort. Uptime Kuma watches
  `http://bitskit:8080/healthz`. Not phone-probed (see `check_vhosts.py`)
