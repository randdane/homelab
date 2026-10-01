# /// script
# requires-python = ">=3.11"
# ///
"""Check that Uptime Kuma's monitors are asking answerable questions.

    uv run scripts/check_monitors.py
    uv run scripts/check_monitors.py --list

Kuma watches the services. Nothing watches Kuma, and a monitor is the one
piece of configuration on this host that is not a file in this repo: monitors
live in Kuma's SQLite database, created by hand through a web UI. They are
never reviewed in a diff, so a monitor can go on pointing at an address that
stopped being reachable and the only symptom is an alert that looks like an
outage.

That is not hypothetical. Monitor 5 probed the host's own tailnet IP,
the host's tailnet address and port, from inside a container on the `edge` bridge. UFW is
default-deny inbound with 22/tcp as its only rule, so every probe was dropped
in INPUT. Vikunja served perfectly for eleven hours while its monitor reported
`timeout of 48000ms exceeded`. See
$SITE_DIR/docs/incident_reports/2026-08-26-vikunja-monitor-red.md.

Three checks, because they fail at different times:

  reachable   Probe every monitor's target from inside the Kuma container.
              Catches a misconfigured monitor immediately, before it has had
              time to go red and be mistaken for an outage.

  suspect     A monitor red for hours while its siblings are green. Catches
              the same class after the fact and without knowing anything about
              URLs -- including causes this script cannot anticipate.

  coverage    A production stack that no monitor mentions at all. Both checks
              above start from the monitor list, so neither can see a service
              that was never given a monitor -- the gap is invisible to
              exactly the tooling meant to find gaps.

Deliberately NOT a diff of the database against a table in
stacks/uptime-kuma/README.md (which no longer keeps one). That check would have passed throughout the
incident above: the README and the database both named the tailnet address, in
perfect agreement, and both were wrong. Comparing two copies of a value tests
transcription. It does not test whether the value is true, which was the
entire failure. Probing is what tests that.

Exit codes: 0 clean, 1 a monitor is broken or suspect, 2 could not check
(no Docker, container not running).
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

from compose import shared_env, stack_config, stack_dirs

CONTAINER = "uptime-kuma"
DB = "/app/data/kuma.db"

# Hours a monitor must be red, with a green sibling, before it is called
# suspect. An hour is far longer than any restart or deploy here, and short
# enough to beat the eleven hours the real incident ran for.
SUSPECT_AFTER_HOURS = 1.0

# Monitors that must exist, because losing one loses coverage silently. The
# push monitor is the ONLY positive check that a gated vhost is reachable --
# Kuma cannot probe those itself, so the phone reports in. Deleting it looks
# exactly like a quiet healthy system.
REQUIRED_MONITORS = {"Vhosts from phone"}

# Kuma stores heartbeat times as naive UTC.
TIME_FMT = "%Y-%m-%d %H:%M:%S.%f"

QUERY = """
select m.id, m.name, m.type, m.url, m.active,
  (select status from heartbeat where monitor_id=m.id order by id desc limit 1) as status,
  (select time from heartbeat where monitor_id=m.id and important=1 order by id desc limit 1) as since,
  (select msg from heartbeat where monitor_id=m.id order by id desc limit 1) as msg,
  (select count(*) from monitor_notification mn join notification n
     on n.id=mn.notification_id and n.active=1
   where mn.monitor_id=m.id) as notifiers
from monitor m where m.active=1 order by m.id;
"""


def _run(cmd, timeout=30):
    """Run a command with stdin closed.

    stdin=DEVNULL is not decoration. `sqlite3` with an argument it does not
    understand ignores the query and reads stdin instead, and `docker exec`
    without it will happily inherit a terminal -- either way the check hangs
    rather than failing, and a monitoring script that hangs is worse than one
    that is wrong, because a timer will pile up runs behind it.
    """
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def parse_since(value):
    """Kuma's heartbeat timestamp -> aware UTC datetime, or None.

    Tolerates a whole-second timestamp with no fractional part, which SQLite
    will produce for a row written exactly on the second.
    """
    if not value:
        return None
    for fmt in (TIME_FMT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def hours_down(monitor, now):
    """How long this monitor has been red, or None if it is green/unknown."""
    if monitor.get("status") != 0:
        return None
    since = parse_since(monitor.get("since"))
    if since is None:
        return None
    return (now - since).total_seconds() / 3600.0


def suspect_monitors(monitors, now, threshold=SUSPECT_AFTER_HOURS):
    """Red monitors that a green sibling makes suspicious.

    The sibling rule is the whole point. One monitor red while the others are
    green is evidence about the *monitor*: a genuine outage of one service
    does not usually last hours while everything beside it stays perfect.

    When every monitor is red the rule deliberately reports nothing. That is
    the shape of a real outage, or of the host being off -- and calling a
    total outage "probably a config error" would be exactly the wrong alert at
    exactly the wrong moment.
    """
    if not any(m.get("status") == 1 for m in monitors):
        return []
    out = []
    for m in monitors:
        h = hours_down(m, now)
        if h is not None and h >= threshold:
            out.append((m, h))
    return out


def probe_command(monitor):
    """The command that asks a monitor's own target whether it answers.

    Run inside the Kuma container on purpose. Reachability is a property of
    where the probe runs, not of the URL -- that is the entire lesson of the
    incident this script exists for, where the same URL worked from the host
    and timed out from the bridge one hop away.

    Only `http` monitors have a URL to fetch. Others (port, ping, dns) are
    reported as unprobeable rather than silently counted as fine.
    """
    if monitor.get("type") != "http" or not monitor.get("url"):
        return None
    return [
        "docker", "exec", CONTAINER,
        "curl", "-sS", "-o", "/dev/null", "-m", "10",
        "-w", "%{http_code}", monitor["url"],
    ]


def probe_verdict(code, returncode, stderr):
    """Classify one probe result into (ok, detail).

    A 4xx/5xx still proves the target is *reachable*, which is what this check
    is about; Kuma itself judges whether the response is acceptable. Only a
    transport failure -- the timeout, refusal or drop this script was written
    to catch -- counts as broken here.
    """
    if returncode != 0:
        return False, (stderr.strip().splitlines() or ["no output"])[-1][:120]
    if code.isdigit() and int(code) >= 200:
        return True, f"HTTP {code}"
    return False, f"unexpected curl output {code!r}"


# --- coverage: a production stack that no monitor watches ---------------

# Identifying a stack in a monitor URL is substring matching on names the
# stack actually owns -- its directory, its service keys, its container_name.
# That covers a container URL like `http://jellyfin:8096` and the stack's own
# public vhost alike, without either being written down twice.
#
# Where a stack is reached by a name it does not own, say so here rather than
# widening the match. headscale is monitored at its public DERP hostname,
# which contains nothing resembling "headscale" on purpose.
#
# Read, never hardcoded: the hostname is identity, and test_identity_leak.py
# fails the build if it appears literally in a script.
def monitor_aliases():
    duckdns = shared_env("DUCKDNS_HOST")
    return {"headscale": (duckdns,)} if duckdns else {}


# Production stacks that deliberately have no Kuma monitor, and what watches
# them instead. A reason is mandatory: the point of this check is that "no
# monitor" must be a decision someone made, not a gap nobody noticed.
NO_MONITOR = {
    "backup": "archive freshness is status.py's stale_backup_problem, and an "
              "HTTP probe of a cron container would prove nothing",
    "duckdns": "check_derp.py already asserts the name resolves to the "
               "current public IP, which is the only thing it does",
    "caddy": "the public jellyfin vhost monitor traverses it -- if Caddy is "
             "down that monitor is down, so a second one adds no signal",
    "cup": "check_updates.py reads Cup's own JSON API and fails loudly when it "
           "does not answer, on its own timer with its own OnFailure",
    "duplicati": "run_backups.sh refuses to finish a run it cannot push "
                 "offsite, so a dead Duplicati fails homelab-backup.service",
    "uptime-kuma": "nothing can watch the watcher from inside itself -- a "
                   "monitor is down exactly when it cannot report. This "
                   "script returning 2 on an unreadable database is the "
                   "external check, hourly, with OnFailure",
    "dozzle": "a log viewer holding no data that nothing else depends on; "
              "its outage costs one refresh when you next open it",
}


def stack_identifiers(config, name, aliases=None):
    """Names this stack answers to, for matching against a monitor URL."""
    out = {name}
    for service, spec in (config.get("services") or {}).items():
        out.add(service)
        if isinstance(spec, dict) and spec.get("container_name"):
            out.add(spec["container_name"])
    out.update((aliases if aliases is not None else monitor_aliases()).get(name, ()))
    return {i for i in out if i}


def production_stacks(dirs=None):
    """{stack name: identifiers} for every stack marked production."""
    out, aliases = {}, monitor_aliases()
    for d in (dirs if dirs is not None else stack_dirs()):
        config = stack_config(d)
        if not config:
            continue
        if (config.get("x-homelab") or {}).get("lifecycle") != "production":
            continue
        out[d.name] = stack_identifiers(config, d.name, aliases)
    return out


def uncovered_stacks(monitors, stacks, exempt=NO_MONITOR):
    """Production stacks no active monitor mentions, minus the exempt ones.

    Kuma's monitors are the one piece of configuration here that is not a file
    in this repo, so a stack can be deployed, added to the dashboard and
    backed up while nothing ever notices it stop. The other two checks in this
    script both start from the monitor list, which cannot see a service that
    was never given a monitor at all.
    """
    urls = " ".join((m.get("url") or "") for m in monitors).lower()
    return sorted(name for name, ids in stacks.items()
                  if name not in exempt
                  and not any(i.lower() in urls for i in ids))


def missing_required(monitors, required=REQUIRED_MONITORS):
    """Required monitor names absent from the current monitor rows."""
    present = {m.get("name") for m in monitors}
    return sorted(required - present)


def silent_monitors(monitors):
    """Active monitors with no active notification attached.

    Such a monitor probes, goes red and records it -- and tells nobody. Every
    other check here passes it: its target is reachable, it is not stale-red,
    and it covers its stack. Found 2026-09-12, when deleting Kuma's old
    notification cascaded and left all twelve monitors with none.

    Only an explicit 0 counts. A row without the key came from somewhere
    other than QUERY, and guessing would turn a plumbing change into alerts.
    """
    return sorted(m.get("name") for m in monitors if m.get("notifiers") == 0)


def load_monitors():
    r = _run(["docker", "exec", CONTAINER, "sqlite3", "-batch", "-json", DB, QUERY])
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()[:200]
        raise RuntimeError(f"could not read {CONTAINER}:{DB} -- {err}")
    out = r.stdout.strip()
    return json.loads(out) if out else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print monitors and exit 0")
    ap.add_argument("--suspect-after", type=float, default=SUSPECT_AFTER_HOURS,
                    metavar="H", help=f"hours red before suspect (default {SUSPECT_AFTER_HOURS})")
    args = ap.parse_args()

    try:
        monitors = load_monitors()
    except FileNotFoundError:
        print("docker not found -- CHECK SKIPPED, not passed.", file=sys.stderr)
        return 2
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"{exc} -- CHECK SKIPPED, not passed.", file=sys.stderr)
        return 2

    if not monitors:
        # An empty monitor list is the loudest failure available: Kuma is
        # running and watching nothing at all. Never report that as clean.
        print("uptime-kuma has NO active monitors -- nothing is being watched.",
              file=sys.stderr)
        return 1

    if args.list:
        for m in monitors:
            state = {1: "up", 0: "down"}.get(m.get("status"), "?")
            print(f"  {m['id']:>2}  {state:<4} {m['name']:<22} {m.get('url') or m['type']}")
        return 0

    now = datetime.now(timezone.utc)
    problems = []

    for m in monitors:
        cmd = probe_command(m)
        if cmd is None:
            continue
        try:
            r = _run(cmd, timeout=20)
        except subprocess.TimeoutExpired:
            problems.append(f"{m['name']}: probe timed out -- {m['url']}")
            continue
        ok, detail = probe_verdict(r.stdout.strip(), r.returncode, r.stderr)
        if not ok:
            problems.append(
                f"{m['name']}: unreachable from inside {CONTAINER} -- "
                f"{m['url']} ({detail})")

    for m, h in suspect_monitors(monitors, now, args.suspect_after):
        problems.append(
            f"{m['name']}: red {h:.1f}h while other monitors are green -- "
            f"suspect the check, not the service ({(m.get('msg') or '').strip()[:60]})")

    stacks = production_stacks()
    for name in uncovered_stacks(monitors, stacks):
        problems.append(
            f"{name}: production stack with no monitor -- nothing would "
            f"report it down. Add one, or record why not in NO_MONITOR.")

    for name in silent_monitors(monitors):
        problems.append(
            f"{name}: no active notification attached -- it can go red but "
            f"cannot tell anyone. Attach one in the monitor's settings.")

    for name in missing_required(monitors):
        problems.append(
            f"{name}: required monitor is missing -- its coverage is gone "
            f"silently. Was it paused or deleted in the Kuma web UI?")

    if problems:
        print("uptime-kuma monitor problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\n  A monitor is reachable from where it runs, not from where you\n"
            "  are. Probe it in the container before believing any URL:\n"
            f"    docker exec {CONTAINER} curl -sS -o /dev/null -w '%{{http_code}}\\n' <url>\n"
            "  Address siblings by container name on a shared network. The host's\n"
            "  tailnet IP is NOT reachable from a bridge -- UFW is default-deny\n"
            "  inbound (lessons-learned 25).\n"
            "  Edit the monitor in the web UI; `--list` shows the result.",
            file=sys.stderr)
        return 1

    print(f"clean: {len(monitors)} active monitors, all targets reachable from "
          f"inside {CONTAINER}, none stale-red, all with a notifier; "
          f"{len(stacks)} production stack(s) all covered", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
