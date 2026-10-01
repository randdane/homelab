# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_check_backups.py -v

Tests operate on the JSON `docker compose config` produces. The parsing
itself is Compose's job.
"""

from compose import volumes_needing_backup, registered_volumes

STACK_CONFIG = {
    "volumes": {
        "grafana-data": {"name": "lgtm_grafana-data"},
        "loki-data": {"name": "lgtm_loki-data"},
    }
}

BACKUP_CONFIG_FULLY_REGISTERED = {
    "volumes": {
        "lgtm_grafana-data": {"name": "lgtm_grafana-data", "external": True},
        "lgtm_loki-data": {"name": "lgtm_loki-data", "external": True},
    }
}

BACKUP_CONFIG_MISSING_LOKI = {
    "volumes": {
        "lgtm_grafana-data": {"name": "lgtm_grafana-data", "external": True},
    }
}


def test_fully_registered_set_reports_clean():
    declared = volumes_needing_backup(STACK_CONFIG)
    registered = registered_volumes(BACKUP_CONFIG_FULLY_REGISTERED)
    assert declared - registered == set()


def test_unregistered_volume_is_detected():
    declared = volumes_needing_backup(STACK_CONFIG)
    registered = registered_volumes(BACKUP_CONFIG_MISSING_LOKI)
    missing = declared - registered
    assert missing == {"lgtm_loki-data"}


def test_external_volumes_are_not_treated_as_declared_by_their_own_stack():
    """A stack referencing an external volume (not its own) should not count
    it as something it declares -- only the owning stack's plain named
    volume does."""
    config = {"volumes": {"other_data": {"name": "other_data", "external": True}}}
    assert volumes_needing_backup(config) == set()


def test_volume_labelled_exclude_is_not_expected_to_be_backed_up():
    """Jellyfin's cache regenerates and its media library is too large for a
    nightly tarball. Without the label both would report a backup gap
    forever, and a warning that is always wrong stops being read."""
    config = {
        "volumes": {
            "config": {"name": "jellyfin_config"},
            "cache": {
                "name": "jellyfin_cache",
                "labels": {"homelab.backup": "exclude"},
            },
        }
    }
    assert volumes_needing_backup(config) == {"jellyfin_config"}


def test_other_backup_label_values_do_not_exclude():
    """Only the exact value `exclude` opts out -- a typo or a note left in
    the label must fail closed, into the backup set."""
    config = {
        "volumes": {
            "data": {"name": "s_data", "labels": {"homelab.backup": "excluded"}},
        }
    }
    assert volumes_needing_backup(config) == {"s_data"}


def test_backup_stack_registers_via_external_flag():
    assert registered_volumes(BACKUP_CONFIG_FULLY_REGISTERED) == {
        "lgtm_grafana-data", "lgtm_loki-data",
    }


def _run_without_docker(tmp_path, *args):
    """check_backups.py with a `docker` that fails every call, so the
    container cannot be inspected."""
    import os, subprocess
    from pathlib import Path
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    script = Path(__file__).with_name("check_backups.py")
    return subprocess.run(["python3", script, *args], capture_output=True,
                          text=True, env=env, timeout=60)


def test_uninspectable_container_skips_without_the_flag(tmp_path):
    r = _run_without_docker(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "skipped" in r.stderr


def test_uninspectable_container_fails_with_require_mounts(tmp_path):
    r = _run_without_docker(tmp_path, "--require-mounts")
    assert r.returncode == 1, r.stderr
    assert "could not inspect" in r.stderr


def test_only_production_stacks_must_be_registered():
    """A `planned` or `developing` stack is not deployed, so its volumes do
    not exist. Registering them names volumes the backup container cannot
    mount, and one absent external volume stops it starting at all -- the
    2026-09-20 cutover failure."""
    from check_backups import requires_backup_registration
    assert requires_backup_registration({"x-homelab": {"lifecycle": "production"}})
    assert not requires_backup_registration({"x-homelab": {"lifecycle": "planned"}})
    assert not requires_backup_registration({"x-homelab": {"lifecycle": "developing"}})


def test_a_stack_with_no_lifecycle_is_not_required():
    """Fails open deliberately: an unparseable or unlabelled stack is caught
    by status.py's schema check, not here. Failing closed would put an
    unknown volume in the registry and stop the backup container starting."""
    from check_backups import requires_backup_registration
    assert not requires_backup_registration({})
    assert not requires_backup_registration({"x-homelab": None})
