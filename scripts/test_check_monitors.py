# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_check_monitors.py -v

Tests the classification, not Docker. The pure functions are the part that
decides whether an alert fires; the subprocess plumbing around them is
exercised by running the script for real (see stacks/uptime-kuma/README.md).
"""

from datetime import datetime, timezone

from check_monitors import (
    hours_down,
    missing_required,
    parse_since,
    probe_command,
    probe_verdict,
    no_retry_monitors,
    silent_monitors,
    stack_identifiers,
    suspect_monitors,
    uncovered_stacks,
)

NOW = datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc)


def monitor(name, status, since, **kw):
    return {"name": name, "type": "http", "url": f"http://{name}/h",
            "status": status, "since": since, **kw}


GREEN = monitor("green", 1, "2026-08-27 05:00:00.000")
RED_11H = monitor("vikunja", 0, "2026-08-26 19:00:00.000")
RED_10M = monitor("fresh", 0, "2026-08-27 05:50:00.000")


# -- parsing -----------------------------------------------------------------

def test_kuma_timestamps_are_read_as_utc():
    """Kuma stores naive UTC. Reading it as local time would mis-age every
    monitor by the UTC offset -- five hours here, enough to invent or hide a
    suspect monitor on its own."""
    assert parse_since("2026-08-26 19:45:31.129") == datetime(
        2026, 8, 26, 19, 45, 31, 129000, tzinfo=timezone.utc)


def test_timestamp_without_fractional_seconds_still_parses():
    """SQLite writes no fractional part for a row landing exactly on the
    second. Returning None there would silently exempt that monitor."""
    assert parse_since("2026-08-26 19:45:31") is not None


def test_unparseable_timestamps_are_none_not_exceptions():
    for bad in (None, "", "yesterday", "2026-13-45 99:99:99"):
        assert parse_since(bad) is None


# -- the sibling rule --------------------------------------------------------

def test_the_real_incident_is_flagged():
    """Vikunja red for eleven hours while four siblings were green."""
    flagged = suspect_monitors([GREEN, RED_11H], NOW)
    assert [m["name"] for m, _ in flagged] == ["vikunja"]
    assert flagged[0][1] == 11.0


def test_a_service_that_just_went_down_is_not_yet_suspect():
    """Restarts and deploys make monitors blink. Alerting on that would train
    the alert away, which is the failure this whole script guards against."""
    assert suspect_monitors([GREEN, RED_10M], NOW) == []


def test_everything_red_is_an_outage_not_a_misconfiguration():
    """The 2026-08-26 total outage: sixteen containers down. Reporting that as
    'suspect the check' would be the wrong alert at the worst moment."""
    all_red = [dict(GREEN, status=0), RED_11H]
    assert suspect_monitors(all_red, NOW) == []


def test_a_red_monitor_with_no_recorded_transition_is_not_timed():
    """A monitor with no important heartbeat yet cannot be aged, and guessing
    zero would flag every newly created monitor."""
    assert suspect_monitors([GREEN, monitor("new", 0, None)], NOW) == []


def test_empty_input_does_not_raise():
    assert suspect_monitors([], NOW) == []


def test_threshold_is_honoured():
    assert suspect_monitors([GREEN, RED_11H], NOW, threshold=12.0) == []
    assert suspect_monitors([GREEN, RED_10M], NOW, threshold=0.1) != []


def test_green_monitors_have_no_downtime():
    assert hours_down(GREEN, NOW) is None


# -- probing -----------------------------------------------------------------

def test_probe_targets_the_monitors_own_url():
    assert probe_command(GREEN)[-1] == "http://green/h"


def test_probe_runs_inside_the_kuma_container():
    """Reachability depends on where the probe runs. Running it on the host is
    exactly the mistake that hid the incident: the same URL answered from the
    host and timed out from the bridge."""
    cmd = probe_command(GREEN)
    assert cmd[:3] == ["docker", "exec", "uptime-kuma"]


def test_non_http_monitors_are_not_probed():
    """port/ping/dns monitors have no URL to curl. Returning a command would
    fetch the string 'None'; returning None reports them unprobeable."""
    assert probe_command({"type": "port", "url": None}) is None
    assert probe_command({"type": "http", "url": ""}) is None


def test_an_error_status_still_counts_as_reachable():
    """A 502 proves something answered. Whether the response is acceptable is
    Kuma's judgement, not this script's -- this check only asks whether the
    target can be reached at all."""
    assert probe_verdict("502", 0, "")[0] is True


def test_connection_refused_and_timeout_are_failures():
    """The two shapes of the incident: dropped by the firewall (timeout) and
    nothing listening (refused)."""
    assert probe_verdict("", 28, "curl: (28) Operation timed out")[0] is False
    assert probe_verdict("000", 7, "curl: (7) Failed to connect")[0] is False


def test_failure_detail_survives_into_the_alert():
    """An alert saying only 'unreachable' sends you back to the terminal."""
    ok, detail = probe_verdict("", 28, "curl: (28) Operation timed out after 10001 ms")
    assert not ok and "28" in detail


# --- coverage -----------------------------------------------------------

MONITORS = [
    {"url": "http://jellyfin:8096/health"},
    {"url": "https://derp.example.org/health"},
]
STACKS = {
    "jellyfin": {"jellyfin"},
    "headscale": {"headscale", "derp.example.org"},
    "forgejo": {"forgejo", "forgejo-postgres"},
}


def test_a_stack_no_monitor_mentions_is_reported():
    """The gap the other two checks structurally cannot see: both start from
    the monitor list, and forgejo has no monitor to start from."""
    assert uncovered_stacks(MONITORS, STACKS, exempt={}) == ["forgejo"]


def test_a_stack_matched_by_an_alias_is_covered():
    """headscale is watched at a public hostname containing nothing like its
    own name. Without the alias it would be reported as unwatched forever,
    and a check that cries wolf is one nobody reads."""
    assert "headscale" not in uncovered_stacks(MONITORS, STACKS, exempt={})


def test_exempt_stacks_are_not_reported():
    """'No monitor' has to be expressible as a decision, or the check can
    only ever be silenced by deleting it."""
    assert uncovered_stacks(MONITORS, STACKS, exempt={"forgejo": "why"}) == []


def test_required_monitor_present_is_not_reported():
    assert missing_required([{"name": "Vhosts from phone"}],
                            required={"Vhosts from phone"}) == []


def test_required_monitor_absent_is_reported():
    """The push monitor is the only positive check that a gated vhost is
    reachable -- Kuma cannot probe those itself. Losing it looks like a
    quiet healthy system, so its absence must be a finding."""
    assert missing_required([{"name": "other"}],
                            required={"Vhosts from phone"}) == ["Vhosts from phone"]


def test_a_monitor_with_no_notifier_is_reported():
    """The 2026-09-12 cascade: every monitor green and reachable, none able
    to alert. Nothing else in this script sees it."""
    rows = [{"name": "Forgejo", "notifiers": 0}, {"name": "ntfy", "notifiers": 1}]
    assert silent_monitors(rows) == ["Forgejo"]


def test_a_row_without_a_notifier_count_is_not_guessed_at():
    assert silent_monitors([{"name": "x"}]) == []


def test_a_monitor_with_no_retries_is_reported():
    """2026-10-08: monitors left at Kuma's default of 0 paged on a reboot."""
    rows = [{"name": "immich", "maxretries": 0}, {"name": "ntfy", "maxretries": 2},
            {"name": "x"}]
    assert no_retry_monitors(rows) == ["immich"]


def test_identifiers_include_service_and_container_names():
    """A monitor addresses a container, not a directory: the jellyfin stack is
    matched by `http://jellyfin:8096`, life-queue only by `life-queue-app`."""
    ids = stack_identifiers(
        {"services": {"app": {"container_name": "life-queue-app"}}}, "life-queue")
    assert {"life-queue", "app", "life-queue-app"} <= ids
