# /// script
# requires-python = ">=3.11"
# ///
"""Render the backup stack for the volumes that actually exist on THIS host.

    uv run scripts/backup_here.py            # write stacks/backup/compose.host.yaml
    uv run scripts/backup_here.py --check    # report only, write nothing

`stacks/backup/compose.yaml` is the canonical registry: every volume in the
repo that must be archived, on any host. It declares them all `external: true`,
which means it only starts on a host where all 37 stacks are deployed --
`external volume "lgtm_mimir-data" not found`, and nothing runs at all.

Hosts are deployed in waves, so that is most hosts, including a brand new one
where nothing is backed up yet and the need is greatest. This renders a
host-specific compose containing only the volumes Docker actually has.

The skipped volumes are PRINTED, every run, by design. A backup that quietly
covers three volumes out of forty-nine looks exactly like one that covers
everything -- and you would not find out until a restore. Do not silence this.

Deliberately NOT done: creating the missing volumes so the canonical file
starts. That produces successful nightly archives of empty directories, which
is worse than a visible failure.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from compose import ROOT, DOCKER_TIMEOUT

STACK = ROOT / "stacks" / "backup"
CANONICAL = STACK / "compose.yaml"
RENDERED = STACK / "compose.host.yaml"


def ensure_archive_dir():
    """Create the archive directory before Docker does.

    The backup service bind-mounts it, and Docker creates a missing bind
    source as ROOT. That silently takes ownership of
    ~/.local/state/homelab/, which is also where status.py writes -- so the
    status timer starts failing with PermissionError on a directory inside
    the user's own home. Two unrelated stacks colliding through one path.
    Creating it here, as the user, keeps Docker from inventing it.
    """
    base = os.environ.get("BACKUP_ARCHIVE_DIR")
    if not base:
        state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        base = str(Path(state) / "homelab" / "backups")
    Path(base).mkdir(parents=True, exist_ok=True)
    return base


def existing_volumes():
    """Volume names Docker actually has on this host.

    Bounded, and the timeout is deliberately allowed to propagate: this
    renders the file the nightly backup runs from, so a wedged dockerd must
    fail loudly rather than hang. Silently rendering fewer volumes is the
    exact failure the module docstring refuses to allow.
    """
    out = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"],
                         capture_output=True, text=True, check=True,
                         timeout=DOCKER_TIMEOUT).stdout
    return set(out.split())


def parse_canonical(text):
    """Volume names referenced as backup sources, in file order.

    Read with a regex rather than `docker compose config`, because that
    command resolves external volumes and fails on this very file -- the
    problem being solved here.
    """
    mounts = re.findall(r"^\s*-\s+([A-Za-z0-9_.-]+):/backup/[^:]+:ro\s*$", text, re.M)
    return mounts


def render(text, keep):
    """Drop mount lines and external declarations for volumes not in `keep`."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*-\s+([A-Za-z0-9_.-]+):/backup/[^:]+:ro\s*$", line)
        if m and m.group(1) not in keep:
            i += 1
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if (m and m.group(1) not in keep
                and i + 1 < len(lines)
                and lines[i + 1].strip() == "external: true"):
            i += 2
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    text = CANONICAL.read_text()
    registered = parse_canonical(text)
    have = existing_volumes()
    keep = [v for v in registered if v in have]
    skip = [v for v in registered if v not in have]

    print(f"registered in compose.yaml: {len(registered)}")
    print(f"present on this host:       {len(keep)}")
    for v in keep:
        print(f"  backing up  {v}")
    if skip:
        print(f"\nNOT on this host, so NOT backed up ({len(skip)}):")
        for v in skip:
            print(f"  skipped     {v}")
        print("\nThese are stacks this host has not deployed. If one of them IS")
        print("deployed here, its volume is misnamed -- check `docker volume ls`.")

    if not keep:
        sys.exit("\nNothing to back up: none of the registered volumes exist here.")

    if args.check:
        return

    archive = ensure_archive_dir()
    print(f"\narchive directory ready: {archive}")

    body = render(text, set(keep))
    header = (
        "# GENERATED by scripts/backup_here.py -- do not edit, do not commit.\n"
        "# Canonical registry is compose.yaml; this is that file filtered to the\n"
        f"# volumes present on this host ({len(keep)} of {len(registered)}).\n"
        "# Re-run after deploying any new stateful stack, or its volume is not\n"
        "# in the backup set and nothing will say so.\n"
    )
    RENDERED.write_text(header + body)
    print(f"\nwrote {RENDERED}")
    print(f"start with:  docker compose -f {RENDERED.name} up -d")


if __name__ == "__main__":
    main()
