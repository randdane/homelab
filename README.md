# homelab

Docker stacks for a small homelab — chiefly a dev laptop and `homelab`, the
24/7 host (a VM on Proxmox). Every stack declares where it
is *meant* to run in `x-homelab.host`, which distinguishes `always-on`
(`homelab`) from `server` (a bigger box, powered up only when wanted). See the
schema below.

    cd stacks/<name> && docker compose up -d

**New machine?** `docs/host-setup.md` first — the host-level configuration
that cloning this repo does not provide, address pools before all else.

See `docs/conventions.md` before adding a stack, and
`docs/lessons-learned.md` for the failure patterns worth guarding against —
it ends with a pre-commit checklist.

Site data -- the Headscale policy and records, the Ansible inventory,
firewall rules -- lives outside this repo, in a checkout `SITE_DIR` points
at. `site.example/` shows the layout.
Port registry is `docs/ports.md`, regenerated with `uv run scripts/ports.py`.

Live dashboard: http://localhost:3001

## Stacks

One directory per stack under `stacks/`. There is no list here on purpose: a
hand-written table is wrong the first time a stack is added and nothing
notices. Every stack carries its own intent in `x-homelab` (below), so ask the
repo instead:

```bash
uv run scripts/status.py     # every stack, its purpose, and whether it is up
```

## Metadata conventions

Two sets of metadata in this repo are read by tooling rather than by Docker.
Both are defined here so the next stack does not reinvent them.

### `x-homelab` — stack intent

A top-level Compose extension field in every `stacks/*/compose.yaml`, read by
`scripts/status.py`. It describes the *stack*, which is why it is not a service
label: choosing a representative service is ambiguous (`paperless` has no `paperless`
service), and renaming that service would silently delete the stack's intent.

```yaml
name: paperless

x-homelab:
  lifecycle: production      # planned | developing | production | retired | not-needed
  purpose: Why this exists, for you. Nothing can derive this field.
  description: What it does. Optional -- falls back to homepage.description.
  host: both                 # laptop | server | always-on | both -- where it is MEANT to run
  exposure: internal         # internal | lan | internet -- what can REACH it
  # patch_priority: internet  # optional; defaults to exposure. What being
  #                           # out of date COSTS, which is not always the
  #                           # same question. check_updates.py alerts on it.
  data: replaceable          # none | replaceable | precious -- restore priority
  prerequisites: none        # external needs not in this compose file, or "none"
```

Every key except `description` and `patch_priority` is required and must be
non-blank. Use the literal `none` for `prerequisites` when there is nothing to
record — a missing key is an omission, and the checker treats it as one.

`patch_priority` is optional and defaults to `exposure`; set it only where
what an update *costs* differs from who can *reach* the service. A value that
is neither absent nor valid is an error, not a low priority — `check_updates.py`
exits 1 rather than filing the stack as not worth alerting.

`lifecycle: retired` means it ran in production and was consciously killed.
`not-needed` means it was migrated here, evaluated, and never promoted. Neither
applies to something that was never migrated at all; those live in the old
archive and do not appear in the tracker.

`data` is restore *priority*, not a backup switch — backup registration is a
separate explicit step in `stacks/backup/compose.yaml`.

### `homepage.*` — dashboard tiles

Per-service labels, discovered by Homepage over the Docker socket. These stay
service-level because a tile genuinely is a property of one service. Only
services with a web UI carry them. See `docs/conventions.md`.

## Status tracking

```bash
uv run scripts/status.py             # what exists, what it's for, what's happened
uv run scripts/status.py --long      # per-service health, image, restarts
uv run scripts/status.py --markdown  # same, pasteable
```

State lives at `${XDG_STATE_HOME:-~/.local/state}/homelab/status-<host>.json`,
one file per host, never committed. It is append-only: `docker compose down`
destroys a container and every timestamp on it, so the record has to outlive
the container it describes.

The one exception is `repair_record()`, which fixes states no capture can
produce — a missing key, or a last-up time on a stack that has never run.
Those mean the file was hand-edited, so a repair cannot destroy an
observation. It prints what it changed rather than doing it quietly.

Hourly capture, so a stack you started and stopped between manual runs still
leaves a trace:

```bash
sudo ./scripts/install-systemd.sh --system   # server only; the laptop mode is retired
```

The units are templates (`scripts/systemd/*.in`) rather than files you
symlink: `WorkingDirectory=` expands specifiers like `%h` but **not**
environment variables. The installer resolves the repo root, `uv`, and `$HOME` and writes real
units into place. Re-run it after moving the repo.

If you previously symlinked the units, the installer detects and replaces the
symlinks — rendering through one would write into the repo.

If you set `HOMELAB_HOST` (see `docs/conventions.md`), set it somewhere both
your login shell and the systemd user manager read — `~/.config/environment.d/*.conf`,
not `~/.bashrc`. The timer never sources `.bashrc`, so a `HOMELAB_HOST` set
only there is invisible to it: manual runs write `status-<HOMELAB_HOST>.json`
while the hourly timer falls back to the hostname and writes
`status-<hostname>.json`, silently splitting one host's history in two.
