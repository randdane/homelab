# /// script
# requires-python = ">=3.11"
# ///
"""A morning digest that doubles as a check on the alert path.

    uv run scripts/digest.py             # collect and send
    uv run scripts/digest.py --dry-run   # collect and print, send nothing

Nothing on disk records whether an alert reached the phone. Sending one
notification every morning through the same ntfy path (scripts/notify.py)
tests that hop daily, and a Tasker watchdog on the phone alarms when today's digest has
not arrived. See $SITE_DIR/docs/superpowers/specs/2026-09-10-morning-digest-design.md.

"OK" means every source was read and none reported a problem. A source that
raises, reads empty or reads unknown is a problem line, never an omission:
dropping it would turn a failure into "OK".

Exit codes follow notify.deliver: 0 sent, 1 could not send, 2 no credentials.
Problems found are content, not failure.
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import check_monitors
import notify
import status

PREFIX = "homelab digest "
# Named rather than taken from the host, so the first server's clock setting cannot
# change which day a digest claims to cover. The phone is in the same zone.
TZ = ZoneInfo("America/Chicago")
# The error status.capture() attaches to a stack whose compose will not build.
PARSE_ERROR = "compose config failed to parse"


def stack_problems(rows, state):
    """What problems_for() deliberately leaves out.

    failing_stacks() excludes `unknown`. A parse failure is reported for any
    stack, history or not, without claiming it should be running. Otherwise
    `unknown` is reported only where the stack is production and has run here.
    """
    out = []
    for row in rows:
        if PARSE_ERROR in row.errors:
            out.append(f"{row.name}: {PARSE_ERROR}")
        elif (row.status == "unknown"
              and row.lifecycle in status.ALERT_LIFECYCLES
              and status.has_run_here(state, row.name)):
            out.append(f"{row.name}: status unknown")
    return out


def backup_status(archive_display, now):
    """(summary_line, problem) from capture()'s archive_display.

    Never read state["last_archive"] here: when the backup config is
    unreadable, capture() keeps the cached timestamp and returns "unknown".
    """
    if archive_display == "unknown":
        return None, "backup: config unreadable, archive age unknown"
    if archive_display is None:
        return None, "backup: no archive ever written"
    then = datetime.strptime(archive_display, status.TIMESTAMP_FORMAT).replace(
        tzinfo=timezone.utc)
    hours = int((now - then).total_seconds() // 3600)
    return f"last backup {hours}h ago", None


def classify_monitors(monitors):
    """(problems, up_count). Only status 1 is healthy.

    load_monitors() returns [] for no active monitors; the refusal to call that
    clean lives in check_monitors.main(), so it is repeated here.
    """
    if not monitors:
        return ["kuma: no active monitors"], 0
    problems, up = [], 0
    for m in monitors:
        s = m.get("status")
        if s == 1:
            up += 1
        elif s == 0:
            problems.append(f"{m['name']}: down")
        elif s is None:
            problems.append(f"{m['name']}: no heartbeat")
        else:
            problems.append(f"{m['name']}: status {s}")
    return problems, up


def failed_units(stdout):
    """Unit names from `systemctl list-units --failed --plain --no-legend`."""
    return [line.split()[0] for line in stdout.splitlines() if line.strip()]


def compose(problems, summary, today):
    """(title, body). The identifier and date live in the title because
    notify.send() truncates only the message."""
    stamp = PREFIX + today.isoformat()
    if not problems:
        return f"{stamp}: OK", summary
    noun = "problem" if len(problems) == 1 else "problems"
    body = "\n".join(problems + ([summary] if summary else []))
    return f"{stamp}: {len(problems)} {noun}", body


def host_section(now):
    """(problems, backup_summary). A capture that raises is one problem line.

    capture() can raise, not only return unknowns: load_state() catches only
    bad JSON, last_archive() stats files that rotation may delete, and
    save_state() fails on a full disk -- when this digest matters most. The
    exception type is kept so the line says disk or permissions.
    """
    try:
        state, rows, archive_display = status.capture()
        problems = status.problems_for(rows, state) + stack_problems(rows, state)
    except Exception as exc:  # noqa: BLE001 -- any failure here must still send
        return [f"host: could not capture status ({type(exc).__name__})"], None
    summary, backup_problem = backup_status(archive_display, now)
    if backup_problem:
        problems.append(backup_problem)
    return problems, summary


def monitor_section():
    """(problems, up_count or None when Kuma could not be read)."""
    try:
        monitors = check_monitors.load_monitors()
    except Exception:  # noqa: BLE001 -- docker missing, exec error, bad JSON, timeout
        return ["kuma: could not read monitors"], None
    return classify_monitors(monitors)


def unit_section():
    """(problems, failed_count or None when systemd could not be asked)."""
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--failed", "--plain", "--no-legend",
             "homelab-*"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ["systemd: could not list units"], None
    if result.returncode != 0:
        return ["systemd: could not list units"], None
    units = failed_units(result.stdout)
    return [f"{u}: failed" for u in units], len(units)


def collect(now):
    """(problems, summary). Every section runs whatever the others did."""
    problems, summary = [], []

    host_problems, backup = host_section(now)
    problems += host_problems
    if backup:
        summary.append(backup)

    monitor_problems, up = monitor_section()
    problems += monitor_problems
    if up is not None:
        summary.append(f"{up} monitors up")

    unit_problems, failed = unit_section()
    problems += unit_problems
    if failed is not None:
        summary.append(f"{failed} failed units")

    return problems, " · ".join(summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent, send nothing")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    problems, summary = collect(now)
    title, body = compose(problems, summary, now.astimezone(TZ).date())

    if args.dry_run:
        print(f"title:   {title}")
        print(f"message: {body[:notify.MAX_MESSAGE]}")
        return 0
    return notify.deliver(title, body)


if __name__ == "__main__":
    sys.exit(main())
