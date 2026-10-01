# /// script
# requires-python = ">=3.11"
# ///
"""Check every stack's named volumes are registered for backup, and that the
running backup container actually mounts them.

    uv run scripts/check_backups.py
    uv run scripts/check_backups.py --require-mounts   # on the backup host

Without the flag, a host with no `backup` container skips the mount check
and exits 0, which is right for the laptop. run_backups.sh passes it: on the
host about to archive, "could not inspect" must fail, not pass.

`docker-volume-backup.stop-during-backup` only pauses a container during
backup -- it does not register the volume itself. Registration is a manual,
two-entry step in `stacks/backup/compose.yaml` (see docs/conventions.md), and
skipping it fails silently: the volume is never archived, and nobody notices
until a restore is needed.

The second check exists because registration is not the last thing that can go
wrong. A container's mount list is fixed when it is created: a `backup`
container started before a stack existed keeps archiving the old set forever
and reports success while doing it. On 2026-08-23 that had silently excluded
`authentik_database` and `caddy_data` -- the account database and the ACME
keys. Nothing surfaced it; a restore would have.

Parsing is delegated to `docker compose config`, same as scripts/ports.py --
hand-parsing the YAML would get anchors, extends and interpolation wrong.

This script answers coverage only: is the right *set* of volumes archived. It
says nothing about whether the archiving still happens. Recency is
`stale_backup_problem` in scripts/status.py, which flags an archive older than
`MAX_ARCHIVE_AGE_HOURS` on every hourly run. Both have to pass; neither implies
the other, and looking only here is how you conclude nothing watches backup age.
"""

import json
import shutil
import subprocess
import sys

from compose import ROOT, volumes_needing_backup, registered_volumes, stack_config, stack_dirs

BACKUP_CONTAINER = "backup"


def requires_backup_registration(config):
    """Whether a stack's volumes must appear in the backup registry.

    Only production stacks. A `planned` or `developing` stack is not deployed,
    so its volumes do not exist and cannot be archived -- requiring
    registration anyway means the registry names volumes the backup container
    cannot mount, and an external volume that is absent stops that container
    starting at all. That is what broke the 2026-09-20 cutover: the first server had 37
    such volumes lying around as leftovers, the VM had none.

    Same rule, same reason as check_monitors.py -- a stack that is not running
    is not a gap in coverage, and a warning that is always wrong stops being
    read. Promoting a stack to `production` starts requiring it, which is the
    moment the data becomes real.

    The gap this leaves: a stack that is deployed but still labelled
    `developing` is no longer enforced. `karakeep` is exactly that today --
    running, holding real bookmarks, and registered, but its registration is
    now unchecked. The label is what is wrong there, not this rule; promoting
    it means also giving it a monitor, since check_monitors.py requires one of
    every production stack."""
    return (config.get("x-homelab") or {}).get("lifecycle") == "production"


def mounted_volumes(container=BACKUP_CONTAINER):
    """Volume names the RUNNING container has mounted, or None if we cannot
    tell -- no docker, or the container does not exist on this host.

    Deliberately distinguishes "no answer" from "empty set": on a laptop with
    no backup container, silently reporting zero mounts would look identical
    to a container that lost every mount."""
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.run(
            ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    try:
        mounts = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return {m["Name"] for m in mounts if m.get("Type") == "volume" and m.get("Name")}


if __name__ == "__main__":
    stacks_dir = ROOT / "stacks"
    backup_config = stack_config(stacks_dir / "backup")
    registered = registered_volumes(backup_config) if backup_config else set()

    declared = {}  # volume name -> stack name
    skipped = []
    for stack_dir in stack_dirs():
        if stack_dir.name == "backup":
            continue
        config = stack_config(stack_dir)
        if not config:
            continue
        if not requires_backup_registration(config):
            skipped.append(stack_dir.name)
            continue
        for vol in volumes_needing_backup(config):
            declared[vol] = stack_dir.name

    missing = sorted(v for v in declared if v not in registered)
    if missing:
        print("Volumes declared but not registered for backup:", file=sys.stderr)
        for vol in missing:
            print(f"  {vol} (stack: {declared[vol]})", file=sys.stderr)
        sys.exit(1)

    print(f"clean: {len(declared)} volume(s), all registered for backup", file=sys.stderr)
    if skipped:
        print(
            f"not checked: {len(skipped)} non-production stack(s) -- "
            + ", ".join(sorted(skipped)),
            file=sys.stderr,
        )

    # Registration is not enough: the container must actually mount them.
    mounted = mounted_volumes()
    if mounted is None and "--require-mounts" in sys.argv[1:]:
        print(
            f"FAILED: could not inspect the '{BACKUP_CONTAINER}' container's "
            "mounts, and --require-mounts says this host must be able to",
            file=sys.stderr,
        )
        sys.exit(1)
    if mounted is None:
        print(
            f"skipped: no '{BACKUP_CONTAINER}' container here -- run this on the "
            "backup host to check its mounts are current",
            file=sys.stderr,
        )
        sys.exit(0)

    stale = sorted(v for v in registered if v not in mounted)
    if stale:
        print(
            f"\nThe running '{BACKUP_CONTAINER}' container is NOT archiving "
            f"{len(stale)} registered volume(s):",
            file=sys.stderr,
        )
        for vol in stale:
            print(f"  {vol}", file=sys.stderr)
        print(
            "\nIts mount list was fixed when it was created. Recreate it:\n"
            "  cd stacks/backup && docker compose up -d --force-recreate",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"clean: container mounts all {len(mounted)} registered volume(s)", file=sys.stderr)
