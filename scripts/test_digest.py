# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_digest.py -v

The digest's promise is that "OK" can only mean every source was read and
nothing was wrong. These tests are ordered by that promise -- the ways a
source can silently read as healthy -- before formatting.
"""

from datetime import date, datetime, timezone

from digest import (
    PARSE_ERROR,
    backup_status,
    classify_monitors,
    compose,
    failed_units,
    stack_problems,
)
from status import Row

NOW = datetime(2026, 9, 10, 12, 15, tzinfo=timezone.utc)


def row(name, status, lifecycle="production", errors=()):
    return Row(name, lifecycle, "present", "server", status, "-", None, "?",
               None, None, list(errors), {})


def has_run(*names):
    return {"stacks": {n: {"first_observed_running": "2026-08-01T00:00:00Z"}
                       for n in names}}


# -- stacks -------------------------------------------------------------------

def test_parse_failure_is_reported_without_any_history():
    rows = [row("newstack", "unknown", lifecycle=None, errors=[PARSE_ERROR])]
    assert stack_problems(rows, {"stacks": {}}) == [
        "newstack: compose config failed to parse"]


def test_parse_failure_is_not_also_reported_as_unknown():
    rows = [row("jellyfin", "unknown", errors=[PARSE_ERROR])]
    assert stack_problems(rows, has_run("jellyfin")) == [
        "jellyfin: compose config failed to parse"]


def test_unknown_production_stack_that_has_run_here_is_a_problem():
    assert stack_problems([row("jellyfin", "unknown")], has_run("jellyfin")) == [
        "jellyfin: status unknown"]


def test_unknown_stack_that_never_ran_here_is_not_claimed_to_be_down():
    assert stack_problems([row("arr", "unknown", lifecycle="planned")],
                          {"stacks": {}}) == []


def test_up_stacks_add_nothing():
    assert stack_problems([row("jellyfin", "up")], has_run("jellyfin")) == []


# -- backup -------------------------------------------------------------------

def test_unreadable_backup_config_is_a_problem_not_a_cached_age():
    summary, problem = backup_status("unknown", NOW)
    assert summary is None
    assert problem == "backup: config unreadable, archive age unknown"


def test_no_archive_is_distinct_from_unknown():
    summary, problem = backup_status(None, NOW)
    assert summary is None
    assert problem == "backup: no archive ever written"


def test_backup_age_in_whole_hours():
    summary, problem = backup_status("2026-09-10T08:05:00Z", NOW)
    assert (summary, problem) == ("last backup 4h ago", None)


# -- monitors -----------------------------------------------------------------

def mon(name, status):
    return {"name": name, "status": status}


def test_empty_monitor_list_is_a_problem_not_zero_down():
    assert classify_monitors([]) == (["kuma: no active monitors"], 0)


def test_only_status_1_is_healthy():
    problems, up = classify_monitors([
        mon("a", 1), mon("b", 0), mon("c", None), mon("d", 2), mon("e", 3),
        mon("f", 7)])
    assert up == 1
    assert problems == ["b: down", "c: no heartbeat", "d: status 2",
                        "e: status 3", "f: status 7"]


# -- units --------------------------------------------------------------------

def test_failed_units_parsed_from_plain_output():
    out = ("homelab-backup.service loaded failed failed Run the nightly backup\n"
           "homelab-smart.service  loaded failed failed Monthly SMART\n")
    assert failed_units(out) == ["homelab-backup.service", "homelab-smart.service"]


def test_no_failed_units():
    assert failed_units("") == []


# -- message ------------------------------------------------------------------

TODAY = date(2026, 9, 10)


def test_ok_title_carries_prefix_and_date():
    title, body = compose([], "last backup 4h ago · 14 monitors up · 0 failed units",
                          TODAY)
    assert title == "homelab digest 2026-09-10: OK"
    assert body == "last backup 4h ago · 14 monitors up · 0 failed units"


def test_problem_title_counts_and_body_lists_problems_then_summary():
    title, body = compose(["b: down", "kuma: no active monitors"], "last backup 4h ago",
                          TODAY)
    assert title == "homelab digest 2026-09-10: 2 problems"
    assert body == "b: down\nkuma: no active monitors\nlast backup 4h ago"


def test_one_problem_is_singular():
    title, _ = compose(["b: down"], "", TODAY)
    assert title == "homelab digest 2026-09-10: 1 problem"


def test_title_survives_a_body_long_enough_to_truncate():
    import notify
    problems = [f"stack{i}: status unknown" for i in range(200)]
    title, body = compose(problems, "", TODAY)
    assert len(body) > notify.MAX_MESSAGE
    assert title.startswith("homelab digest 2026-09-10: ")


# -- collection ---------------------------------------------------------------

import pytest

import check_monitors
import digest
import notify
import status


def test_capture_raising_is_a_problem_naming_the_type(monkeypatch):
    def boom():
        raise PermissionError("state file")
    monkeypatch.setattr(status, "capture", boom)
    assert digest.host_section(NOW) == (
        ["host: could not capture status (PermissionError)"], None)


def test_capture_raising_still_reports_kuma_and_systemd(monkeypatch):
    def boom():
        raise OSError("disk")
    monkeypatch.setattr(status, "capture", boom)
    monkeypatch.setattr(check_monitors, "load_monitors",
                        lambda: [mon("a", 1), mon("b", 0)])
    monkeypatch.setattr(digest, "unit_section", lambda: (["x.service: failed"], 1))
    problems, summary = digest.collect(NOW)
    assert problems == ["host: could not capture status (OSError)",
                        "b: down", "x.service: failed"]
    assert summary == "1 monitors up · 1 failed units"


def test_unknown_archive_never_shows_the_cached_timestamp(monkeypatch):
    state = {"stacks": {}, "last_archive": "2026-09-10T08:05:00Z"}
    monkeypatch.setattr(status, "capture", lambda: (state, [], "unknown"))
    monkeypatch.setattr(status, "problems_for", lambda rows, st: [])
    problems, summary = digest.host_section(NOW)
    assert problems == ["backup: config unreadable, archive age unknown"]
    assert summary is None


def test_monitor_load_failure_is_a_problem(monkeypatch):
    def fail():
        raise RuntimeError("could not read uptime-kuma")
    monkeypatch.setattr(check_monitors, "load_monitors", fail)
    assert digest.monitor_section() == (["kuma: could not read monitors"], None)


def test_systemctl_failure_is_a_problem(monkeypatch):
    def fail(*a, **kw):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(digest.subprocess, "run", fail)
    assert digest.unit_section() == (["systemd: could not list units"], None)


# -- exit status --------------------------------------------------------------

def test_delivered_problem_digest_exits_0(monkeypatch):
    monkeypatch.setattr(digest, "collect", lambda now: (["b: down"], ""))
    sent = []
    monkeypatch.setattr(notify, "deliver", lambda t, m: sent.append(t) or 0)
    assert digest.main([]) == 0
    assert sent[0].endswith(": 1 problem")


def test_delivery_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(digest, "collect", lambda now: ([], "ok"))
    monkeypatch.setattr(notify, "deliver", lambda t, m: 1)
    assert digest.main([]) == 1


def test_dry_run_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(digest, "collect", lambda now: ([], "ok"))
    monkeypatch.setattr(notify, "deliver", lambda t, m: pytest.fail("sent"))
    assert digest.main(["--dry-run"]) == 0
    assert "homelab digest " in capsys.readouterr().out


def test_main_dates_by_chicago_not_utc(monkeypatch):
    """03:00 UTC on the 11th is still the evening of the 10th in Chicago."""
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(digest, "datetime", FixedDateTime)
    monkeypatch.setattr(digest, "collect", lambda now: ([], "ok"))
    titles = []
    monkeypatch.setattr(notify, "deliver", lambda t, m: titles.append(t) or 0)
    assert digest.main([]) == 0
    assert titles == ["homelab digest 2026-09-10: OK"]


def test_monitor_load_failure_does_not_hide_other_sources(monkeypatch):
    """Spec: a failure to load monitors is a problem, and the other sources
    still appear."""
    state = {"stacks": {}}
    monkeypatch.setattr(status, "capture",
                        lambda: (state, [], "2026-09-10T08:05:00Z"))
    monkeypatch.setattr(status, "problems_for", lambda rows, st: [])

    def fail():
        raise RuntimeError("could not read uptime-kuma")
    monkeypatch.setattr(check_monitors, "load_monitors", fail)
    monkeypatch.setattr(digest, "unit_section", lambda: ([], 0))

    problems, summary = digest.collect(NOW)
    assert problems == ["kuma: could not read monitors"]
    assert "last backup" in summary
    assert "0 failed units" in summary
    assert "monitors up" not in summary
