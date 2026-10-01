# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_backup_here.py -v

The render has to drop BOTH lines a volume occupies -- the mount and the
external declaration. Dropping only the mount leaves `external: true` behind,
and the stack still refuses to start for a volume it no longer backs up:
the failure this script exists to prevent, reintroduced silently.
"""

import subprocess

import backup_here
from backup_here import parse_canonical, render


def test_existing_volumes_is_bounded(monkeypatch):
    """`docker volume ls` renders the file the nightly backup runs from, so
    an unbounded call would hang the render rather than fail it."""
    seen = {}

    class Result:
        stdout = "kept_data\ngone_data\n"

    def record(*a, **k):
        seen.update(k)
        return Result()

    monkeypatch.setattr(subprocess, "run", record)
    assert backup_here.existing_volumes() == {"kept_data", "gone_data"}
    assert seen.get("timeout") == backup_here.DOCKER_TIMEOUT


def test_existing_volumes_lets_a_timeout_propagate(monkeypatch):
    """Deliberately loud: silently rendering fewer volumes is the failure the
    module docstring refuses to allow."""
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker volume ls", timeout=30)

    monkeypatch.setattr(subprocess, "run", hang)
    try:
        backup_here.existing_volumes()
    except subprocess.TimeoutExpired:
        return
    raise AssertionError("a wedged dockerd must not render silently")

SAMPLE = """name: backup

services:
  backup:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ${BACKUP_ARCHIVE_DIR:-/tmp}:/archive
      - kept_data:/backup/kept:ro
      # A comment about the dropped one.
      - gone_data:/backup/gone:ro

volumes:
  kept_data:
    external: true
  gone_data:
    external: true
"""


def test_parse_finds_only_backup_sources():
    # The socket and the archive mount are not backup sources.
    assert parse_canonical(SAMPLE) == ["kept_data", "gone_data"]


def test_render_drops_mount_and_external_declaration():
    out = render(SAMPLE, {"kept_data"})
    assert "gone_data" not in out
    assert "kept_data:/backup/kept:ro" in out
    assert "kept_data:\n    external: true" in out


def test_render_keeps_non_source_mounts():
    out = render(SAMPLE, {"kept_data"})
    assert "/var/run/docker.sock" in out
    assert ":/archive" in out


def test_render_is_identity_when_everything_is_present():
    assert render(SAMPLE, {"kept_data", "gone_data"}) == SAMPLE
