# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_status.py -v

Tests operate on the JSON `docker compose config` produces, and on plain
dicts for the merge rules. Docker itself is never invoked -- there is no
value in asserting that `docker inspect` works.
"""

import json
import os
import subprocess
from pathlib import Path

import status


class _FakeRun:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

VALID = {
    "x-homelab": {
        "lifecycle": "production",
        "purpose": "Observability for everything else",
        "description": "Loki, Tempo, Mimir, Grafana",
        "host": "both",
        "exposure": "internal",
        "data": "replaceable",
        "prerequisites": "Docker socket, read-only",
    }
}


def test_valid_intent_has_no_errors():
    intent, errors = status.parse_intent(VALID)
    assert errors == []
    assert intent["lifecycle"] == "production"
    assert intent["description"] == "Loki, Tempo, Mimir, Grafana"


def test_missing_key_is_reported():
    config = {"x-homelab": {k: v for k, v in VALID["x-homelab"].items()
                            if k != "exposure"}}
    intent, errors = status.parse_intent(config)
    assert any("exposure" in e for e in errors)
    assert intent["exposure"] is None


def test_patch_priority_is_optional_and_validated():
    """Optional, so its absence is not an error -- but a typo must not pass
    silently, since a stack with a misspelled priority falls back to exposure
    and quietly leaves the alert set."""
    intent, errors = status.parse_intent(VALID)
    assert errors == []
    assert "patch_priority" not in intent

    intent, errors = status.parse_intent(
        {"x-homelab": {**VALID["x-homelab"], "patch_priority": "internet"}})
    assert errors == []
    assert intent["patch_priority"] == "internet"

    _, errors = status.parse_intent(
        {"x-homelab": {**VALID["x-homelab"], "patch_priority": "x"}})
    assert any("patch_priority" in e for e in errors)


def test_blank_value_is_reported():
    config = {"x-homelab": dict(VALID["x-homelab"], purpose="   ")}
    _, errors = status.parse_intent(config)
    assert any("purpose" in e for e in errors)


def test_bad_enum_value_is_reported():
    config = {"x-homelab": dict(VALID["x-homelab"], lifecycle="retried")}
    _, errors = status.parse_intent(config)
    assert any("lifecycle" in e and "retried" in e for e in errors)


def test_missing_block_entirely_reports_every_required_key():
    intent, errors = status.parse_intent({"services": {}})
    assert len(errors) == len(status.REQUIRED)
    assert all(intent[k] is None for k in status.REQUIRED)


def test_description_falls_back_to_homepage_label():
    config = {
        "x-homelab": {k: v for k, v in VALID["x-homelab"].items()
                      if k != "description"},
        "services": {
            "homepage": {"labels": {"homepage.description": "The dashboard"}}
        },
    }
    intent, errors = status.parse_intent(config)
    assert errors == []
    assert intent["description"] == "The dashboard"


def test_non_string_value_on_required_key_is_reported():
    config = {"x-homelab": dict(VALID["x-homelab"], purpose=2026)}
    intent, errors = status.parse_intent(config)
    assert any("purpose" in e for e in errors)
    assert intent["purpose"] != 2026


def test_description_is_optional_and_may_be_absent():
    config = {"x-homelab": {k: v for k, v in VALID["x-homelab"].items()
                            if k != "description"}}
    intent, errors = status.parse_intent(config)
    assert errors == []
    assert intent["description"] is None


def _svc(running=True, health=None, status=None, restarts=0):
    """status defaults to agree with `running`, which is what Docker reports
    for every container that is not mid-crash-loop."""
    if status is None:
        status = "running" if running else "exited"
    return {"status": status, "running": running, "health": health,
            "restarts": restarts, "image": "x:1"}


DEFINED = {"grafana", "loki"}


def test_all_running_and_healthy_is_up():
    observed = {"grafana": _svc(health="healthy"), "loki": _svc()}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "up"


def test_one_unhealthy_is_degraded():
    observed = {"grafana": _svc(health="unhealthy"), "loki": _svc()}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "degraded"


def test_missing_service_is_degraded():
    """A stack where Loki is dead and Grafana is fine must not render up."""
    observed = {"grafana": _svc(health="healthy")}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "degraded"


def test_stopped_service_alongside_running_is_degraded():
    observed = {"grafana": _svc(), "loki": _svc(running=False)}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "degraded"


def test_nothing_running_but_ran_before_is_down():
    observed = {"grafana": _svc(running=False), "loki": _svc(running=False)}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "down"


def test_no_containers_and_never_ran_is_never():
    assert status.aggregate_status(
        DEFINED, {}, daemon_ok=True, ever_ran=False) == "never"


def test_no_containers_but_ran_before_is_down():
    """docker compose down removes the containers; the history stays."""
    assert status.aggregate_status(
        DEFINED, {}, daemon_ok=True, ever_ran=True) == "down"


def test_daemon_unreachable_is_unknown():
    assert status.aggregate_status(
        DEFINED, {}, daemon_ok=False, ever_ran=True) == "unknown"


def test_health_none_means_no_healthcheck_not_unhealthy():
    """Most images define no healthcheck; absence must not read as failure."""
    observed = {"grafana": _svc(health=None), "loki": _svc(health=None)}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "up"


def test_starting_health_is_degraded():
    observed = {"grafana": _svc(health="starting"), "loki": _svc()}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "degraded"


T1 = "2026-08-01T10:00:00Z"
T2 = "2026-08-02T10:00:00Z"
T3 = "2026-08-03T10:00:00Z"

INTENT = {
    "lifecycle": "production", "purpose": "p", "host": "both",
    "exposure": "internal", "data": "replaceable", "prerequisites": "none",
    "description": None,
}


def _fresh():
    return {"host": "test", "updated": None, "last_archive": None, "stacks": {}}


def test_first_observed_running_set_on_first_running_capture():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    assert state["stacks"]["lgtm"]["first_observed_running"] == T1


def test_first_observed_running_unset_while_never_run():
    """On disk but never started: the field stays None."""
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={}, now=T1)
    assert state["stacks"]["lgtm"]["first_observed_running"] is None
    assert state["stacks"]["lgtm"]["last_seen_up"] is None
    assert state["stacks"]["lgtm"]["last_seen_down"] is None


def test_first_observed_running_never_changes():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T2)
    assert state["stacks"]["lgtm"]["first_observed_running"] == T1


def test_last_seen_up_moves_forward_only():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T2)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)  # clock went backwards
    assert state["stacks"]["lgtm"]["last_seen_up"] == T2


def test_last_seen_down_recorded_when_nothing_runs():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc(running=False)}, now=T2)
    assert state["stacks"]["lgtm"]["last_seen_up"] == T1
    assert state["stacks"]["lgtm"]["last_seen_down"] == T2


def test_absent_stack_keeps_record_and_frozen_intent():
    """Deleting the directory must not lose what the stack had been."""
    state = _fresh()
    status.merge(state, "old", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "old", present=False, intent=None,
                 observed=None, now=T2)
    record = state["stacks"]["old"]
    assert record["present"] is False
    assert record["intent"]["lifecycle"] == "production"
    assert record["first_observed_running"] == T1


def test_intent_snapshot_refreshes_while_present():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True,
                 intent=dict(INTENT, lifecycle="retired"),
                 observed={"a": _svc(running=False)}, now=T2)
    assert state["stacks"]["lgtm"]["intent"]["lifecycle"] == "retired"


def test_daemon_unreachable_does_not_touch_timestamps():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed=None, now=T3)
    record = state["stacks"]["lgtm"]
    assert record["last_seen_up"] == T1
    assert record["last_seen_down"] is None


def test_corrupt_state_file_is_quarantined(tmp_path):
    path = tmp_path / "status-test.json"
    path.write_text("{not json at all")
    state = status.load_state(path)
    assert state["stacks"] == {}
    quarantines = list(tmp_path.glob("status-test.json.*.corrupt"))
    assert len(quarantines) == 1
    assert quarantines[0].read_text() == "{not json at all"


def test_missing_state_file_returns_skeleton(tmp_path):
    state = status.load_state(tmp_path / "absent.json")
    assert state["stacks"] == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "status-test.json"
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.save_state(path, state)
    assert status.load_state(path)["stacks"]["lgtm"]["last_seen_up"] == T1


def test_second_corruption_does_not_destroy_first_quarantine(tmp_path):
    path = tmp_path / "status-test.json"
    path.write_text("{first corrupt")
    status.load_state(path)
    first_quarantine = next(tmp_path.glob("*.corrupt"))
    path.write_text("{second corrupt")
    status.load_state(path)
    quarantines = sorted(tmp_path.glob("*.corrupt"))
    assert len(quarantines) == 2
    assert first_quarantine in quarantines
    assert first_quarantine.read_text() == "{first corrupt"


def test_save_state_temp_filename_includes_pid(tmp_path, monkeypatch):
    path = tmp_path / "status-test.json"
    seen = {}
    real_replace = Path.replace

    def spy_replace(self, target):
        seen["tmp_name"] = self.name
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    status.save_state(path, _fresh())
    assert str(os.getpid()) in seen["tmp_name"]


ROW = status.Row(
    name="lgtm", lifecycle="production", presence="present", host="both",
    status="up", last_up="now", data="replaceable", backup="yes",
    purpose="Observability for everything else",
    description="Loki, Tempo, Mimir, Grafana", errors=[],
    services={"grafana": _svc(health="healthy")},
)


def test_render_includes_every_column_value():
    out = status.render([ROW], "2026-08-08T03:00:12Z")
    assert "lgtm" in out and "production" in out and "up" in out
    assert "Observability for everything else" in out


def test_render_shows_last_archive_in_header():
    out = status.render([ROW], "2026-08-08T03:00:12Z")
    assert "2026-08-08T03:00:12Z" in out.splitlines()[0]


def test_render_handles_no_archive_yet():
    out = status.render([ROW], None)
    assert "never" in out.splitlines()[0]


def test_long_adds_service_detail():
    out = status.render([ROW], None, long=True)
    assert "grafana" in out
    assert "healthy" in out
    assert "x:1" in out


def test_long_shows_description():
    """description is authored intent; if it renders nowhere, collecting it
    is dead weight."""
    out = status.render([ROW], None, long=True)
    assert "Loki, Tempo, Mimir, Grafana" in out


def test_short_output_omits_description():
    out = status.render([ROW], None)
    assert "Loki, Tempo, Mimir, Grafana" not in out


def test_short_output_omits_service_detail():
    out = status.render([ROW], None)
    assert "grafana" not in out


def test_markdown_emits_a_table():
    out = status.render([ROW], None, markdown=True)
    assert "| lgtm |" in out
    assert "| --- |" in out or "|---|" in out


def test_removed_stack_shows_last_known_lifecycle():
    row = ROW._replace(name="old", presence="removed", lifecycle="production")
    out = status.render([row], None)
    assert "removed" in out
    assert "production" in out


def test_invalid_intent_is_flagged_without_dropping_the_row():
    row = ROW._replace(name="broken", errors=["x-homelab.data is missing or blank"])
    out = status.render([row], None)
    assert "broken" in out
    assert "!" in out


def test_rows_sort_by_lifecycle_then_name():
    rows = [
        ROW._replace(name="zebra", lifecycle="production"),
        ROW._replace(name="alpha", lifecycle="retired"),
        ROW._replace(name="apple", lifecycle="production"),
    ]
    ordered = [r.name for r in status.sort_rows(rows)]
    assert ordered == ["apple", "zebra", "alpha"]


def test_unknown_lifecycle_sorts_last_without_crashing():
    rows = [ROW._replace(name="weird", lifecycle=None),
            ROW._replace(name="normal", lifecycle="production")]
    ordered = [r.name for r in status.sort_rows(rows)]
    assert ordered == ["normal", "weird"]


BACKUP_CONFIG = {
    "services": {
        "backup": {
            "volumes": [
                {"source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
                {"source": "/somewhere/homelab/backups", "target": "/archive"},
                {"source": "lgtm_loki-data", "target": "/backup/lgtm-loki"},
            ]
        }
    }
}


def test_archive_dir_read_from_the_backup_mount():
    """The path is already interpolated by `docker compose config`; deriving
    it from the mount avoids a second copy of the XDG chained default."""
    assert str(status.archive_dir(BACKUP_CONFIG)) == "/somewhere/homelab/backups"


def test_archive_dir_none_when_no_archive_mount():
    config = {"services": {"backup": {"volumes": [
        {"source": "x", "target": "/backup/y"}]}}}
    assert status.archive_dir(config) is None


def test_last_archive_none_when_directory_absent():
    assert status.last_archive(BACKUP_CONFIG) is None


def test_last_archive_returns_newest_archive(tmp_path):
    (tmp_path / "old.tar.gz").write_text("a")
    (tmp_path / "new.tar.gz").write_text("b")
    os.utime(tmp_path / "old.tar.gz", (1_600_000_000, 1_600_000_000))
    os.utime(tmp_path / "new.tar.gz", (1_700_000_000, 1_700_000_000))
    config = {"services": {"backup": {"volumes": [
        {"source": str(tmp_path), "target": "/archive"}]}}}
    assert status.last_archive(config).startswith("2023-11-14")


def test_last_seen_down_stamped_when_containers_are_destroyed():
    """`docker compose down` leaves observed == {}; the stack still went down.

    This is the whole reason the tool keeps a state file, so it is pinned
    separately from the stopped-container case, which satisfies the other
    half of the same condition."""
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={}, now=T2)
    assert state["stacks"]["lgtm"]["last_seen_down"] == T2


def test_last_seen_down_moves_forward_only():
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc()}, now=T1)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={}, now=T2)
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={}, now=T1)  # clock went backwards
    assert state["stacks"]["lgtm"]["last_seen_down"] == T2


def test_backup_unknown_when_backup_stack_will_not_parse():
    """A fresh clone has no .env, so stacks/backup fails to interpolate.
    That must not render as `no` -- an alarming false negative."""
    assert status.backup_state({"lgtm_loki-data"}, None) == "?"
    assert status.backup_state(set(), None) == "?"
    assert status.backup_state({"lgtm_loki-data"}, {"lgtm_loki-data"}) == "yes"
    assert status.backup_state({"lgtm_loki-data"}, set()) == "no"
    assert status.backup_state(set(), set()) == "n/a"


def test_render_says_unknown_not_never_when_archive_state_is_unreadable():
    out = status.render([ROW], "unknown")
    assert "unknown" in out.splitlines()[0]
    assert "never" not in out.splitlines()[0]


def test_unparseable_stack_keeps_its_stored_history_and_intent():
    record = {"intent": dict(INTENT), "last_seen_up": T1,
              "first_observed_running": T1, "services": {}}
    row = status._stored_row("lgtm", record, presence="present",
                             status="unknown", now=T3, backup="?",
                             errors=["compose config failed to parse"])
    assert row.lifecycle == "production"
    assert row.last_up == T1[:10]
    assert row.errors == ["compose config failed to parse"]


def test_unparseable_stack_with_no_stored_record_still_renders():
    row = status._stored_row("brandnew", None, presence="present",
                             status="unknown", now=T3, backup="?", errors=["x"])
    assert row.lifecycle is None and row.last_up == "-"


# --- crash-loop detection (INCIDENT-2026-08-25) -----------------------------
#
# headscale crash-looped for 45 hours while this tool reported it `up`. Docker
# reports Running=true for a container in the `restarting` state, so liveness
# alone cannot see a crash-loop. Verified against a real container looping on
# `exit 1`:  Status=restarting  Running=true  RestartCount=7


def _crashloop(restarts=7):
    """Exactly what `docker inspect` reports mid-crash-loop."""
    return _svc(running=True, status="restarting", restarts=restarts)


def test_restarting_container_is_not_up():
    assert not status.is_up(_crashloop())


def test_crash_looping_service_is_not_up_despite_running_true():
    """The regression. Both services "running", one crash-looping, no
    healthcheck defined (headscale is distroless) -- must not read as up."""
    observed = {"grafana": _svc(), "loki": _crashloop()}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "degraded"


def test_whole_stack_crash_looping_is_down_not_up():
    observed = {"grafana": _crashloop(), "loki": _crashloop()}
    assert status.aggregate_status(
        DEFINED, observed, daemon_ok=True, ever_ran=True) == "down"


def test_single_service_stack_crash_looping_is_down():
    """headscale's actual shape: one service, no healthcheck, restarting."""
    observed = {"headscale": _crashloop()}
    assert status.aggregate_status(
        {"headscale"}, observed, daemon_ok=True, ever_ran=True) == "down"


def test_last_seen_up_does_not_advance_during_a_crash_loop():
    """merge must agree with aggregate_status, or the table reports
    `last up: now` through a 45-hour outage."""
    state = _fresh()
    status.merge(state, "headscale", present=True, intent=INTENT,
                 observed={"headscale": _svc()}, now=T1)
    status.merge(state, "headscale", present=True, intent=INTENT,
                 observed={"headscale": _crashloop()}, now=T2)
    record = state["stacks"]["headscale"]
    assert record["last_seen_up"] == T1
    assert record["last_seen_down"] == T2


def test_missing_status_key_falls_back_to_running():
    """State files written before this change have no `status` key; reading
    one back must not crash or silently mark everything down."""
    assert status.is_up({"running": True, "health": None, "restarts": 0})


# --- alerting ---------------------------------------------------------------


def _state_with(name, *, ran=True):
    state = _fresh()
    state["stacks"][name] = {"first_observed_running": T1 if ran else None}
    return state


def test_failing_stacks_reports_a_down_production_stack():
    row = ROW._replace(name="headscale", lifecycle="production", status="down")
    assert [r.name for r in
            status.failing_stacks([row], _state_with("headscale"))] == ["headscale"]


def test_failing_stacks_ignores_a_stack_that_never_ran_here():
    """The laptop must not alert on server-only stacks."""
    row = ROW._replace(name="immich", lifecycle="production", status="down")
    assert status.failing_stacks([row], _state_with("immich", ran=False)) == []


def test_failing_stacks_ignores_non_production_lifecycles():
    for lifecycle in ("planned", "developing", "retired", "not-needed"):
        row = ROW._replace(name="olivetin", lifecycle=lifecycle, status="down")
        assert status.failing_stacks([row], _state_with("olivetin")) == []


def test_failing_stacks_ignores_up_never_and_unknown():
    for value in ("up", "never", "unknown"):
        row = ROW._replace(name="lgtm", lifecycle="production", status=value)
        assert status.failing_stacks([row], _state_with("lgtm")) == []


def test_failing_stacks_reports_degraded_too():
    row = ROW._replace(name="lgtm", lifecycle="production", status="degraded")
    assert len(status.failing_stacks([row], _state_with("lgtm"))) == 1


def test_describe_failure_names_the_broken_service_and_restarts():
    row = ROW._replace(name="headscale", status="down",
                       services={"headscale": _crashloop(restarts=42)})
    line = status.describe_failure(row)
    assert "headscale: down" in line
    assert "restarting" in line and "42" in line


def test_describe_failure_omits_healthy_services():
    row = ROW._replace(name="lgtm", status="degraded",
                       services={"grafana": _svc(health="healthy"),
                                 "loki": _crashloop()})
    line = status.describe_failure(row)
    assert "loki" in line
    assert "grafana" not in line


# --- host-level config checks ----------------------------------------------
#
# Replaces the guard that lived in scripts/safe-shutdown.sh. That one only
# fired if you remembered to run the script instead of `poweroff`, which is
# not a guard; this runs on the hourly timer regardless.


def _daemon_json(tmp_path, text):
    path = tmp_path / "daemon.json"
    path.write_text(text)
    return path


def test_shutdown_timeout_ok(tmp_path):
    path = _daemon_json(tmp_path, '{"shutdown-timeout": 30}')
    assert status.shutdown_timeout_problem(path) is None


def test_shutdown_timeout_above_minimum_is_fine(tmp_path):
    path = _daemon_json(tmp_path, '{"shutdown-timeout": 60}')
    assert status.shutdown_timeout_problem(path) is None


def test_shutdown_timeout_missing_key_is_reported(tmp_path):
    """The first server's real daemon.json before 2026-08-26: valid, other keys, no
    shutdown-timeout. Containers would get 15s then SIGKILL."""
    path = _daemon_json(tmp_path, '{"log-driver": "json-file"}')
    assert "want an integer" in status.shutdown_timeout_problem(path)


def test_shutdown_timeout_too_low_is_reported(tmp_path):
    path = _daemon_json(tmp_path, '{"shutdown-timeout": 10}')
    problem = status.shutdown_timeout_problem(path)
    assert "10s" in problem and "30s" in problem


def test_shutdown_timeout_string_is_not_configured(tmp_path):
    """Read as JSON, not grepped: "30" must not pass as 30."""
    path = _daemon_json(tmp_path, '{"shutdown-timeout": "30"}')
    assert "want an integer" in status.shutdown_timeout_problem(path)


def test_shutdown_timeout_bool_is_not_an_int(tmp_path):
    """bool is a subclass of int in Python; True must not read as configured."""
    path = _daemon_json(tmp_path, '{"shutdown-timeout": true}')
    assert "want an integer" in status.shutdown_timeout_problem(path)


def test_shutdown_timeout_unparseable_file_is_reported(tmp_path):
    path = _daemon_json(tmp_path, "{not json")
    assert "will not parse" in status.shutdown_timeout_problem(path)


def test_shutdown_timeout_absent_file_is_reported(tmp_path):
    assert "does not exist" in status.shutdown_timeout_problem(tmp_path / "nope.json")


def test_runs_production_here_true_when_a_production_stack_has_run():
    row = ROW._replace(name="headscale", lifecycle="production")
    assert status.runs_production_here([row], _state_with("headscale"))


def test_runs_production_here_false_on_a_host_that_never_ran_them():
    """The laptop runs the same timer; it must not report on the first server's daemon."""
    row = ROW._replace(name="headscale", lifecycle="production")
    assert not status.runs_production_here([row], _state_with("headscale", ran=False))


def test_runs_production_here_false_with_only_non_production_stacks():
    row = ROW._replace(name="olivetin", lifecycle="planned")
    assert not status.runs_production_here([row], _state_with("olivetin"))


# --- unmounted_backup_problem -----------------------------------------------

def test_every_registered_volume_mounted_is_clean():
    assert status.unmounted_backup_problem({"a", "b"}, {"a", "b", "c"}) is None


def test_registered_but_unmounted_volume_is_named():
    problem = status.unmounted_backup_problem({"a", "ntfy_data"}, {"a"})
    assert problem and "ntfy_data" in problem


def test_uninspectable_backup_container_is_a_problem():
    """"Cannot tell" on the backup host used to read as clean."""
    assert status.unmounted_backup_problem({"a"}, None) is not None


def test_uninspectable_mounts_defer_to_the_daemon_problem():
    assert status.unmounted_backup_problem({"a"}, None, daemon_down=True) is None


def test_unreadable_registration_is_not_this_check():
    assert status.unmounted_backup_problem(None, {"a"}) is None


# --- stale_backup_problem ---------------------------------------------------

NOW = "2026-08-26T20:00:00Z"


def _ago(hours):
    from datetime import datetime, timedelta
    return (datetime.strptime(NOW, status.TIMESTAMP_FORMAT)
            - timedelta(hours=hours)).strftime(status.TIMESTAMP_FORMAT)


def test_fresh_nightly_archive_is_not_a_problem():
    assert status.stale_backup_problem(_ago(3), NOW) is None


def test_archive_just_under_a_day_is_not_a_problem():
    """Normal operation: the newest archive is 0-24h old all day long."""
    assert status.stale_backup_problem(_ago(23), NOW) is None


def test_boundary_is_inclusive():
    assert status.stale_backup_problem(_ago(36), NOW) is None
    assert status.stale_backup_problem(_ago(37), NOW) is not None


def test_one_missed_night_is_a_problem():
    """The real 2026-08-26 shape: 03:09 run failed, no retry until 03:06 the
    next day, so the gap reaches 48h with nothing else complaining."""
    problem = status.stale_backup_problem(_ago(41), NOW)
    assert "41h" in problem and "36h" in problem


def test_never_backed_up_is_a_problem():
    problem = status.stale_backup_problem(None, NOW)
    assert "no backup archive exists" in problem


def test_a_future_timestamp_is_not_reported_as_stale():
    """Clock skew must not invent a negative age that reads as fresh, nor a
    huge one that pages at 3am."""
    assert status.stale_backup_problem(_ago(-2), NOW) is None


def test_stale_backup_is_scoped_to_hosts_that_run_backups():
    """The laptop has never run the backup stack and must stay quiet -- the
    same false-positive class as the HOMELAB_ALERT regression."""
    state = {"stacks": {}, "last_archive": None}
    assert not status.has_run_here(state, "backup")
    state = {"stacks": {"backup": {"first_observed_running": "2026-01-01T00:00:00Z"}},
             "last_archive": None}
    assert status.has_run_here(state, "backup")


def test_problems_for_collects_stack_and_host_failures(monkeypatch):
    """The list __main__ retries against: stack failures plus host checks."""
    monkeypatch.setattr(status, "daemon_problem", lambda: "daemon is dead")
    monkeypatch.setattr(status, "shutdown_timeout_problem", lambda: None)
    # See the note in test_problems_for_is_empty_when_nothing_is_failing.
    monkeypatch.setattr(status, "userland_proxy_problem", lambda: None)
    monkeypatch.setattr(status, "docker_proxy_running_problem", lambda: None)
    monkeypatch.setattr(status, "env_permission_problem", lambda: None)
    row = ROW._replace(name="headscale", lifecycle="production", status="down")
    problems = status.problems_for([row], _state_with("headscale"))
    assert len(problems) == 2
    assert any("headscale: down" in p for p in problems)
    assert "daemon is dead" in problems


def test_problems_for_is_empty_when_nothing_is_failing(monkeypatch):
    """A clean second capture is what lets the retry cancel a boot-race page."""
    monkeypatch.setattr(status, "daemon_problem", lambda: None)
    monkeypatch.setattr(status, "shutdown_timeout_problem", lambda: None)
    # Stubbed for the same reason as the two above: unstubbed, these read the
    # real /etc/docker/daemon.json and run a real pgrep, so the expected
    # problem count became host-dependent and these tests failed on a laptop
    # whose daemon.json has no userland-proxy key.
    monkeypatch.setattr(status, "userland_proxy_problem", lambda: None)
    monkeypatch.setattr(status, "docker_proxy_running_problem", lambda: None)
    monkeypatch.setattr(status, "env_permission_problem", lambda: None)
    row = ROW._replace(name="headscale", lifecycle="production", status="up")
    assert status.problems_for([row], _state_with("headscale")) == []


def test_observe_services_returns_none_when_docker_hangs(monkeypatch):
    """A hung `docker ps` reads as daemon-unreachable, not as an exception.

    None is the established contract for "the daemon did not answer", and
    aggregate_status already renders that stack `unknown`.
    """
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker ps", timeout=30)

    monkeypatch.setattr(subprocess, "run", hang)
    assert status.observe_services("anything") is None


def test_observe_services_bounds_its_docker_calls(monkeypatch):
    seen = {}

    def record(*a, **k):
        seen.update(k)
        return _FakeRun(0, stdout="")

    monkeypatch.setattr(subprocess, "run", record)
    status.observe_services("demo")
    assert seen.get("timeout") == status.DOCKER_TIMEOUT


def test_capture_does_not_observe_stacks_when_the_daemon_is_down(monkeypatch, tmp_path):
    """The load-bearing one: 42 stacks x DOCKER_TIMEOUT is 21 minutes, well
    past homelab-status.service's TimeoutStartSec=300. Probing once and
    skipping is what keeps a wedged dockerd a fast, named failure instead of
    a unit that blows its ceiling and says only that it failed."""
    calls = []
    # A VALID config, so the loop actually reaches observe_services. An
    # earlier version of this test stubbed stack_config to None, which makes
    # capture() `continue` before that line -- so it passed with the guard
    # removed and asserted nothing. Verified by mutation: delete the guard in
    # capture() and this test must fail.
    config = {"name": "demo", "services": {"app": {}}, "x-homelab": VALID["x-homelab"]}
    monkeypatch.setattr(status, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(status, "stack_config", lambda _d: config)
    monkeypatch.setattr(status, "registered_volumes", lambda _c: set())
    monkeypatch.setattr(status, "volumes_needing_backup", lambda _c: set())
    monkeypatch.setattr(status, "stack_dirs", lambda: [tmp_path / "demo"])
    monkeypatch.setattr(status, "daemon_problem", lambda: "docker daemon is unreachable")

    def observe(project):
        calls.append(project)
        return {}

    monkeypatch.setattr(status, "observe_services", observe)

    status.capture()
    assert calls == [], "observe_services ran despite the daemon being down"


def test_capture_observes_normally_when_the_daemon_answers(monkeypatch, tmp_path):
    """The control. Without it the test above passes if capture() stops
    observing entirely."""
    calls = []
    config = {"name": "demo", "services": {"app": {}}, "x-homelab": VALID["x-homelab"]}
    monkeypatch.setattr(status, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(status, "stack_config", lambda _d: config)
    monkeypatch.setattr(status, "registered_volumes", lambda _c: set())
    monkeypatch.setattr(status, "volumes_needing_backup", lambda _c: set())
    monkeypatch.setattr(status, "stack_dirs", lambda: [tmp_path / "demo"])
    monkeypatch.setattr(status, "daemon_problem", lambda: None)

    def observe(project):
        calls.append(project)
        return {}

    monkeypatch.setattr(status, "observe_services", observe)
    status.capture()
    assert calls == ["demo"]


def test_capture_records_the_backup_archive_mount(monkeypatch, tmp_path):
    """The producer half. Both tests below put archive_dir into state by hand,
    so deleting the assignment in capture() left them green -- and the whole
    point is that a rebuilt server's BACKUP_ARCHIVE_DIR reaches the disk check
    without anyone writing it down."""
    archives = tmp_path / "mnt" / "backups" / "homelab"
    backup_config = {"services": {"backup": {"volumes": [
        {"target": "/archive", "source": str(archives)}]}}}

    monkeypatch.setattr(status, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(status, "stack_config", lambda _d: backup_config)
    monkeypatch.setattr(status, "registered_volumes", lambda _c: set())
    monkeypatch.setattr(status, "stack_dirs", lambda: [])

    state, _rows, _display = status.capture()
    assert state["archive_dir"] == str(archives)


def test_capture_records_no_archive_dir_on_a_first_run_it_cannot_read(monkeypatch, tmp_path):
    """No state yet and stacks/backup unreadable: the skeleton's None stands.
    There is nothing to preserve, so this says only that nothing is invented."""
    monkeypatch.setattr(status, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(status, "stack_config", lambda _d: None)
    monkeypatch.setattr(status, "stack_dirs", lambda: [])

    state, _rows, display = status.capture()
    assert display == "unknown"
    assert state["archive_dir"] is None


def test_capture_keeps_the_last_known_archive_dir_when_backup_is_unreadable(monkeypatch, tmp_path):
    """The behaviour that actually matters, and the one the previous test was
    mis-named for. Same reasoning as last_archive: a compose file that will not
    parse is not evidence the archives moved, so the recorded path stands and
    the disk check keeps watching it. Forgetting it would quietly stop
    monitoring the filesystem at the first unrelated syntax error."""
    path = tmp_path / "state.json"
    state = status._skeleton()
    state["archive_dir"] = "/mnt/backups/homelab"
    status.save_state(path, state)

    monkeypatch.setattr(status, "state_path", lambda: path)
    monkeypatch.setattr(status, "stack_config", lambda _d: None)
    monkeypatch.setattr(status, "stack_dirs", lambda: [])

    fresh, _rows, display = status.capture()
    assert display == "unknown"
    assert fresh["archive_dir"] == "/mnt/backups/homelab"


def test_problems_for_watches_the_recorded_archive_directory(monkeypatch):
    """host-setup.md tells a server to put BACKUP_ARCHIVE_DIR on other storage.
    WATCHED_PATHS cannot know about that mount, so capture() records the
    resolved path and it has to reach the disk check."""
    monkeypatch.setattr(status, "daemon_problem", lambda: None)
    monkeypatch.setattr(status, "shutdown_timeout_problem", lambda: None)
    # Stubbed for the same reason as the two above: unstubbed, these read the
    # real /etc/docker/daemon.json and run a real pgrep, so the expected
    # problem count became host-dependent and these tests failed on a laptop
    # whose daemon.json has no userland-proxy key.
    monkeypatch.setattr(status, "userland_proxy_problem", lambda: None)
    monkeypatch.setattr(status, "docker_proxy_running_problem", lambda: None)
    monkeypatch.setattr(status, "env_permission_problem", lambda: None)
    seen = []
    monkeypatch.setattr(status, "disk_problem", lambda paths: seen.extend(paths))

    row = ROW._replace(name="headscale", lifecycle="production", status="up")
    state = _state_with("headscale")
    state["archive_dir"] = "/mnt/backups/homelab"
    status.problems_for([row], state)

    assert Path("/mnt/backups/homelab") in seen
    assert set(status.WATCHED_PATHS) <= set(seen), "the constants still count"


def test_problems_for_survives_a_state_file_with_no_archive_dir(monkeypatch):
    """State written before this field existed, and every host that has never
    read stacks/backup. Must not crash, must still watch the constants."""
    monkeypatch.setattr(status, "daemon_problem", lambda: None)
    monkeypatch.setattr(status, "shutdown_timeout_problem", lambda: None)
    # Stubbed for the same reason as the two above: unstubbed, these read the
    # real /etc/docker/daemon.json and run a real pgrep, so the expected
    # problem count became host-dependent and these tests failed on a laptop
    # whose daemon.json has no userland-proxy key.
    monkeypatch.setattr(status, "userland_proxy_problem", lambda: None)
    monkeypatch.setattr(status, "docker_proxy_running_problem", lambda: None)
    monkeypatch.setattr(status, "env_permission_problem", lambda: None)
    seen = []
    monkeypatch.setattr(status, "disk_problem", lambda paths: seen.extend(paths))

    row = ROW._replace(name="headscale", lifecycle="production", status="up")
    status.problems_for([row], _state_with("headscale"))
    assert set(seen) == set(status.WATCHED_PATHS)


def test_env_permissions_reported_even_with_no_production_stack(monkeypatch):
    """Not gated on runs_production_here -- a leaked credential is one on the
    laptop too, where every stack is `planned` and nothing has ever run."""
    monkeypatch.setattr(status, "daemon_problem", lambda: "unreachable")
    monkeypatch.setattr(status, "shutdown_timeout_problem", lambda: "too short")
    monkeypatch.setattr(status, "env_permission_problem", lambda: "caddy/.env is 0664")
    row = ROW._replace(name="mealie", lifecycle="planned", status="down")
    problems = status.problems_for([row], {"stacks": {}, "last_archive": None})
    # The two gated host checks stay silent; the ungated one does not.
    assert problems == ["caddy/.env is 0664"]


# --- .env permissions ------------------------------------------------------
def _stack_with_env(tmp_path, name, mode):
    d = tmp_path / name
    d.mkdir()
    env = d / ".env"
    env.write_text("SECRET=hunter2\n")
    env.chmod(mode)
    return d


def test_env_permission_problem_accepts_owner_only(tmp_path):
    dirs = [_stack_with_env(tmp_path, "caddy", 0o600)]
    assert status.env_permission_problem(dirs) is None


def test_env_permission_problem_flags_group_readable(tmp_path):
    """0664 is what `umask 002` gives you on Ubuntu without thinking."""
    dirs = [_stack_with_env(tmp_path, "caddy", 0o664)]
    problem = status.env_permission_problem(dirs)
    assert problem is not None
    assert "caddy/.env is 0664" in problem
    assert "chmod 600" in problem


def test_env_permission_problem_flags_other_readable_only(tmp_path):
    """0604: no group bits, but world-readable is still a leak."""
    dirs = [_stack_with_env(tmp_path, "duplicati", 0o604)]
    assert "duplicati/.env is 0604" in status.env_permission_problem(dirs)


def test_env_permission_problem_ignores_missing_env(tmp_path):
    """Most stacks are `planned` and have no .env at all."""
    d = tmp_path / "mealie"
    d.mkdir()
    assert status.env_permission_problem([d]) is None


def test_env_permission_problem_summarises_many(tmp_path):
    dirs = [_stack_with_env(tmp_path, f"s{i}", 0o664) for i in range(8)]
    problem = status.env_permission_problem(dirs)
    assert "8 .env file(s)" in problem
    assert "and 3 more" in problem, "long lists must not dump every path"


# --- disk space ------------------------------------------------------------
class _Statvfs:
    """Enough of os.statvfs_result to drive disk_problem."""
    def __init__(self, blocks=1000, bavail=500, files=1000, favail=500, frsize=4096):
        self.f_blocks, self.f_bavail = blocks, bavail
        self.f_files, self.f_favail = files, favail
        self.f_frsize = frsize


def _fake_fs(monkeypatch, stats, dev=1):
    monkeypatch.setattr(status.os, "statvfs", lambda _p: stats)
    monkeypatch.setattr(status.Path, "stat",
                        lambda self, **k: type("S", (), {"st_dev": dev})())


def test_disk_problem_quiet_with_room(monkeypatch, tmp_path):
    _fake_fs(monkeypatch, _Statvfs(bavail=500, favail=500))  # 50% free
    assert status.disk_problem([tmp_path]) is None


def test_disk_problem_flags_low_bytes(monkeypatch, tmp_path):
    _fake_fs(monkeypatch, _Statvfs(bavail=50, favail=500))  # 5% free
    problem = status.disk_problem([tmp_path])
    assert "95% full" in problem
    assert "inode" not in problem, "bytes and inodes are separate findings"


def test_disk_problem_flags_exhausted_inodes_on_an_empty_disk(monkeypatch, tmp_path):
    """The one that reads as a hardware fault: terabytes free, writes failing."""
    _fake_fs(monkeypatch, _Statvfs(bavail=990, favail=5))  # 99% bytes free
    problem = status.disk_problem([tmp_path])
    assert "inodes used" in problem
    assert "No space left on device" in problem
    assert "% full" not in problem


def test_disk_problem_reports_one_filesystem_once(monkeypatch, tmp_path):
    """The first server had images and archives on the same device; one alert, not two."""
    _fake_fs(monkeypatch, _Statvfs(bavail=10, favail=500), dev=7)
    problem = status.disk_problem([tmp_path, tmp_path / "other"])
    assert problem.count("% full") == 1


def test_disk_problem_ignores_paths_that_do_not_exist(monkeypatch):
    """A laptop has no /var/lib/docker if docker is not installed."""
    def boom(_p, **k):
        raise FileNotFoundError
    monkeypatch.setattr(status.Path, "stat", boom)
    assert status.disk_problem([Path("/nope")]) is None


def test_merge_repairs_a_record_missing_keys():
    """A state file with a key deleted must not crash the hourly capture.

    The gap is filled with None, not with `now`: absent and None both mean
    "never observed running here", and inventing a timestamp would claim a
    deployment that never happened.
    """
    state = _fresh()
    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc(running=False)}, now=T1)
    del state["stacks"]["lgtm"]["first_observed_running"]
    del state["stacks"]["lgtm"]["last_seen_down"]

    status.merge(state, "lgtm", present=True, intent=INTENT,
                 observed={"a": _svc(running=False)}, now=T2)

    assert state["stacks"]["lgtm"]["first_observed_running"] is None
    assert state["stacks"]["lgtm"]["last_seen_down"] == T2


def test_repair_record_reports_every_change_it_makes():
    """The one exception to append-only, so it must say what it did."""
    # A last-up on a stack that never ran: written together with
    # first_observed_running and only together, so this is edited state.
    record = {**status.SHAPE, "last_seen_up": "2026-09-06T00:00:00Z"}
    repairs = status.repair_record(record)
    assert len(repairs) == 1 and "never run" in repairs[0]
    assert record["last_seen_up"] is None

    # A missing key, which used to be a KeyError on every hourly capture.
    record = {k: v for k, v in status.SHAPE.items()
              if k != "first_observed_running"}
    repairs = status.repair_record(record)
    assert any("first_observed_running was missing" in r for r in repairs)
    assert record["first_observed_running"] is None

    # A healthy record is untouched and reports nothing.
    record = {**status.SHAPE, "first_observed_running": T1,
              "last_seen_up": T2}
    assert status.repair_record(record) == []
    assert record["last_seen_up"] == T2


def test_merge_returns_repairs_and_does_not_fight_a_running_stack():
    state = {"stacks": {"a": {**status.SHAPE, "present": True,
                              "last_seen_up": "2026-09-06T00:00:00Z"}}}
    repairs = status.merge(state, "a", present=True, intent=None,
                           observed={"a": _svc(running=False)}, now=T2)
    assert len(repairs) == 1
    assert state["stacks"]["a"]["last_seen_up"] is None

    # A stack that is genuinely up right now sets both, and reports nothing.
    repairs = status.merge(state, "a", present=True, intent=None,
                           observed={"a": _svc(running=True)}, now=T2)
    assert repairs == []
    assert state["stacks"]["a"]["last_seen_up"] == T2
    assert state["stacks"]["a"]["first_observed_running"] == T2


# Restored. This test was added on 2026-09-06 and deleted the same day by the
# next commit, which rewrote the tail of this file anchored on the function
# above it and swallowed this one. Two tests went out and two came in, so the
# suite total never moved -- 312 before and after -- and nothing noticed.
# Diffing test NAMES rather than counts is what found it.
def test_internal_exposure_must_not_publish_reachable_ports():
    """`internal` is a claim about reachability, not about importance."""
    internal = {**VALID["x-homelab"], "exposure": "internal"}

    def errors_for(*ports):
        return status.parse_intent(
            {"x-homelab": internal,
             "services": {"s": {"ports": list(ports)}}})[1]

    # Every interface, the case this started with.
    assert any("reachable" in e for e in
               errors_for({"published": "3001", "target": 3000}))
    # An explicit LAN address. The first version of this check asked whether
    # host_ip was 0.0.0.0 or ::, so this one -- published on the LAN, on the
    # nose -- passed as internal.
    assert any("reachable" in e for e in
               errors_for({"host_ip": "10.0.0.78", "published": "3001",
                           "target": 3000}))
    # IPv6, both the wildcard and a routable address.
    assert errors_for({"host_ip": "::", "published": "3001", "target": 3000})
    assert errors_for({"host_ip": "fd7a:115c:a1e0::1", "published": "3001",
                       "target": 3000})
    # A target-only port. Compose gives it a RANDOM host port on every
    # interface, which is as reachable as any other -- the old check exempted
    # these for having no `published` value.
    assert any("random->3000" in e for e in errors_for({"target": 3000}))
    # An address that will not parse is reachable: an unknown answer is not a
    # safe one.
    assert errors_for({"host_ip": "nonsense", "published": "1", "target": 1})

    # Loopback in both families is genuinely exempt.
    assert errors_for({"host_ip": "127.0.0.1", "published": "9000",
                       "target": 9000}) == []
    assert errors_for({"host_ip": "127.0.1.5", "published": "9000",
                       "target": 9000}) == []
    assert errors_for({"host_ip": "::1", "published": "9000",
                       "target": 9000}) == []

    # And `lan` says the open port out loud, so it is not an error.
    assert status.parse_intent(
        {"x-homelab": {**VALID["x-homelab"], "exposure": "lan"},
         "services": {"s": {"ports": [{"published": "3001",
                                       "target": 3000}]}}})[1] == []


def test_every_stack_in_the_repo_has_valid_metadata():
    """The unit tests above prove the rules work on synthetic input. This one
    runs them over the 41 real stacks, which is the only way a rule tightened
    later is checked against what is already committed -- the `internal` plus
    open-port rule was added after three stacks already broke it.

    Rendered with declared_only, like every other repo-level check: a host's
    .env legitimately overrides ports, so reading it would make this pass or
    fail depending on which machine ran it.
    """
    from compose import stack_config, stack_dirs

    problems = []
    for directory in stack_dirs():
        config = stack_config(directory, declared_only=True)
        if config is None:
            problems.append(f"{directory.name}: will not render")
            continue
        for error in status.parse_intent(config)[1]:
            problems.append(f"{directory.name}: {error}")
    assert not problems, "\n".join(problems)


def test_capture_says_out_loud_what_it_repaired(tmp_path, capsys, monkeypatch):
    """The repair is the one exception to append-only, so it announces itself.

    Without this, deleting the print block in capture() left every test green
    -- the promise was in a commit message and a docstring, and nowhere that
    would fail.
    """
    # A real stack name, so capture() walks it: wger is `planned` and has
    # never run here, which is exactly the shape that was contradicting
    # itself on the first server.
    # ONE record with TWO defects, which is what yesterday's cleanup script
    # actually produced: it deleted a key and left a stale last-up on the same
    # stack. Counting messages instead of records reports that as two damaged
    # records, so this pins the count as well as the detail lines.
    state = status._skeleton()
    record = {k: v for k, v in status.SHAPE.items()
              if k != "first_observed_running"}
    state["stacks"]["wger"] = {**record, "present": True,
                               "last_seen_up": "2026-09-06T00:00:00Z"}
    path = tmp_path / "status.json"
    path.write_text(json.dumps(state))

    monkeypatch.setattr(status, "state_path", lambda: path)
    monkeypatch.setattr(status, "observe_services", lambda project: {})
    status.capture()

    err = capsys.readouterr().err
    # One record, not two, despite two repair messages.
    assert "repaired 1 state record" in err, err
    # And both defects are still reported, each naming the stack.
    assert err.count("  wger: ") == 2, err
    assert "first_observed_running was missing" in err
    assert "2026-09-06T00:00:00Z" in err and "never run" in err


def test_every_enum_field_actually_rejects_a_bad_value():
    """One case per enum key, because a MISSING key disables validation.

    parse_intent only checks a field if ENUMS names it, so dropping an entry
    turns that field's validation off and raises nothing. That is not
    hypothetical: `data` was lost when the schema moved to x_homelab.py on
    2026-09-06 and went unvalidated until 2026-09-07.

    test_every_stack_in_the_repo_has_valid_metadata cannot catch this. It
    renders 41 stacks that all hold valid values, so a broken validator and a
    working one produce identical output. That test checks the corpus; this
    one checks the rules.

    Parametrized over ENUMS itself rather than a written-out list, so a key
    added later is covered without anyone remembering to come back here.
    """
    for field in status.ENUMS:
        block = {**VALID["x-homelab"], field: "definitely-not-valid"}
        errors = status.parse_intent({"x-homelab": block})[1]
        assert any(f"x-homelab.{field}" in e for e in errors), (
            f"{field} accepted a bogus value -- is it still in ENUMS?")


def test_required_fields_with_a_vocabulary_are_all_in_enums():
    """The gap that let `data` go unvalidated: it is required, it has fixed
    values, and nothing tied those two facts together."""
    free_text = {"purpose", "prerequisites"}
    missing = [f for f in status.REQUIRED
               if f not in free_text and f not in status.ENUMS]
    assert not missing, f"{missing} are required with fixed values but have no ENUMS entry"


# --- userland-proxy: the latent half -----------------------------------------

def test_userland_proxy_false_is_ok(tmp_path):
    path = _daemon_json(tmp_path, '{"userland-proxy": false}')
    assert status.userland_proxy_problem(path) is None


def test_userland_proxy_missing_key_is_reported(tmp_path):
    """dockerd defaults userland-proxy to TRUE, so absence is the broken
    value, not an unset one. This is the regression that would silently
    return docker-proxy at the next daemon restart and close every
    remote_ip-gated vhost."""
    path = _daemon_json(tmp_path, '{"log-driver": "json-file"}')
    assert "not set" in status.userland_proxy_problem(path)


def test_userland_proxy_true_is_reported(tmp_path):
    path = _daemon_json(tmp_path, '{"userland-proxy": true}')
    assert "want false" in status.userland_proxy_problem(path)


def test_userland_proxy_wrong_types_are_reported(tmp_path):
    """0 and "false" are falsy or false-looking and must not read as
    configured -- the same reasoning as reading JSON instead of grepping."""
    for literal in ('0', '"false"', 'null'):
        path = _daemon_json(tmp_path, '{"userland-proxy": %s}' % literal)
        assert "want false" in status.userland_proxy_problem(path), literal


def test_userland_proxy_missing_file_is_reported(tmp_path):
    assert "does not exist" in status.userland_proxy_problem(
        tmp_path / "nope.json")


def test_userland_proxy_unparseable_is_reported(tmp_path):
    path = _daemon_json(tmp_path, '{not json')
    assert "will not parse" in status.userland_proxy_problem(path)


# --- docker-proxy: the live half ---------------------------------------------

def _pgrep(returncode, stdout="", stderr=""):
    import unittest.mock as mock
    return mock.patch("subprocess.run", return_value=mock.Mock(
        returncode=returncode, stdout=stdout, stderr=stderr))


def test_docker_proxy_runs_the_command_we_think_it_does():
    """The mocks below assert on the RESULT, so on their own they pass against
    an implementation that greps for the wrong process name or drops the
    timeout. Verified: mutating the name to a nonexistent process left all
    four result-tests green. This pins the call itself."""
    import unittest.mock as mock
    with mock.patch("subprocess.run", return_value=mock.Mock(
            returncode=1, stdout="0\n", stderr="")) as run:
        status.docker_proxy_running_problem(timeout=15)
    args, kwargs = run.call_args
    assert args[0] == ["pgrep", "-c", "docker-proxy"], args[0]
    assert kwargs["timeout"] == 15, kwargs
    # Without capture_output the stdout parse below reads None.
    assert kwargs.get("capture_output") is True and kwargs.get("text") is True


def test_docker_proxy_no_matches_is_healthy():
    """pgrep exits 1 when it matches NOTHING, and that is the healthy case.
    Treating non-zero as an error would invert this check into one that is
    green exactly when docker-proxy is running."""
    with _pgrep(1, stdout="0\n"):
        assert status.docker_proxy_running_problem() is None


def test_docker_proxy_running_is_reported():
    with _pgrep(0, stdout="7\n"):
        problem = status.docker_proxy_running_problem()
    assert "7 docker-proxy" in problem and "restart docker" in problem


def test_docker_proxy_usage_error_is_not_a_pass():
    """Exit 2 is a pgrep usage error: the check did not run. Unchecked must
    not be indistinguishable from healthy."""
    with _pgrep(2, stderr="pgrep: bad option"):
        problem = status.docker_proxy_running_problem()
    assert "NOT checked" in problem


def test_docker_proxy_missing_binary_is_not_a_pass():
    import unittest.mock as mock
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("pgrep")):
        assert "cannot check" in status.docker_proxy_running_problem()


# --- netalertx_stale_problem ------------------------------------------------

def test_netalertx_fresh_import_is_not_a_problem():
    assert status.netalertx_stale_problem(4.0) is None


def test_netalertx_at_the_limit_is_not_a_problem():
    assert status.netalertx_stale_problem(30.0) is None


def test_netalertx_stale_import_is_a_problem():
    problem = status.netalertx_stale_problem(31.0)
    assert problem is not None and "31" in problem


def test_netalertx_unreadable_is_a_problem():
    """Running here but unanswerable: a crashed container must not read as fine."""
    assert status.netalertx_stale_problem(None) is not None
