# /// script
# requires-python = ">=3.11"
# ///
"""What stacks exist, what they are for, and what has happened to them.

    uv run scripts/status.py             # capture, then print the table
    uv run scripts/status.py --capture   # capture only, silent (the timer)
    uv run scripts/status.py --long      # add per-service detail
    uv run scripts/status.py --markdown  # same content, Markdown table

Homepage answers "what is running right now". On a laptop where hardly
anything stays running, that answer is almost always "nothing". This answers
"what exists at all", including stacks that have never been started.

Intent is authored by hand as an x-homelab block in each compose file and read
from disk, so a stack that has never run is still fully described. Observation
comes from the daemon and is merged into an append-only per-host state file:
`docker compose down` destroys a container and every timestamp on it, so a
record has to outlive the container it describes.
"""

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from compose import (ROOT, DOCKER_TIMEOUT, volumes_needing_backup, registered_volumes, stack_config,
                     stack_dirs)
from check_backups import mounted_volumes

from x_homelab import ENUMS, REQUIRED  # noqa: F401  (re-exported)


def _is_loopback(host_ip):
    """Whether a published port's host_ip is unreachable from another machine.

    None (Compose's default, every interface) and anything that will not parse
    are reachable, deliberately: an unknown answer is not a safe one.
    """
    if not host_ip:
        return False
    try:
        return ipaddress.ip_address(host_ip).is_loopback
    except ValueError:
        return False


def _homepage_description(config):
    """First homepage.description label on any service, or None.

    Services with a UI already carry this sentence for the dashboard; making
    x-homelab.description optional keeps them from writing it twice.
    """
    for service in (config.get("services") or {}).values():
        labels = service.get("labels") or {}
        value = labels.get("homepage.description")
        if value and value.strip():
            return value.strip()
    return None


def parse_intent(config):
    """Return (intent, errors) for one stack's config.

    intent always has every REQUIRED key plus "description"; absent values
    are None.
    errors is empty when the block is complete and every enum value is known.
    """
    block = config.get("x-homelab") or {}
    intent = {}
    errors = []

    for key in REQUIRED:
        raw = block.get(key)
        if raw is not None and not isinstance(raw, str):
            intent[key] = None
            errors.append(f"x-homelab.{key}: {raw!r} is not a string")
            continue
        value = raw.strip() if isinstance(raw, str) else raw
        if value is None or value == "":
            intent[key] = None
            errors.append(f"x-homelab.{key} is missing or blank")
            continue
        allowed = ENUMS.get(key)
        if allowed and value not in allowed:
            errors.append(
                f"x-homelab.{key}: {value!r} is not one of {', '.join(allowed)}"
            )
        intent[key] = value

    # patch_priority is optional and defaults to exposure, so it is validated
    # here rather than in the REQUIRED loop. It exists because the two
    # questions came apart: `exposure` answers "what can reach this", which is
    # what a firewall or an allowlist decides, while check_updates.py needs
    # "how bad is being out of date", which for an identity provider is not
    # the same answer. Conflating them meant a stack could only get patch
    # alerts by claiming a reachability it does not have.
    # `in`, not `is not None`: `patch_priority:` with nothing after it is YAML
    # null, and a key someone wrote and left empty is the likeliest way to get
    # an empty one -- so it is the last thing that should be read as "never
    # set". Absent is fine; present-and-empty is a mistake worth naming.
    if "patch_priority" in block:
        raw = block["patch_priority"]
        value = raw.strip() if isinstance(raw, str) else raw
        if value not in ENUMS["patch_priority"]:
            errors.append(
                f"x-homelab.patch_priority: {value!r} is not one of "
                f"{', '.join(ENUMS['patch_priority'])}")
        else:
            intent["patch_priority"] = value

    # `internal` is a claim about reachability, and a published port on
    # 0.0.0.0 contradicts it. Three stacks said `internal` while publishing on
    # every interface (homepage 3001, vault 8201) -- not
    # because anyone decided that, but because `internal` reads as "not very
    # important" and nothing ever compared it to the ports block. A word that
    # drifts into meaning "unimportant" is no longer a record of what is
    # exposed, which is the only thing it is for.
    #
    # Only a LOOPBACK bind is exempt, and that is narrower than it first
    # looks. An earlier version asked whether host_ip was 0.0.0.0 or ::, which
    # let `host_ip: <LAN address>` through -- a LAN address, published on the
    # LAN, called internal. It also exempted target-only ports, and Compose
    # gives those a random host port on every interface, so they are as
    # reachable as any other. Missing, unparseable and random binds are all
    # treated as reachable: the question is "can another machine connect",
    # and anything we cannot prove is loopback answers yes.
    if intent.get("exposure") == "internal":
        reachable = sorted({
            str(port.get("published") or f"random->{port.get('target')}")
            for service in (config.get("services") or {}).values()
            for port in (service.get("ports") or [])
            if not _is_loopback(port.get("host_ip"))
        })
        if reachable:
            errors.append(
                f"x-homelab.exposure is 'internal' but {', '.join(reachable)} "
                f"is reachable from another machine -- use 'lan', or bind the "
                f"port to 127.0.0.1")

    raw = block.get("description")
    description = raw.strip() if isinstance(raw, str) else None
    intent["description"] = description or _homepage_description(config)

    return intent, errors


def is_up(service):
    """Whether one service counts as actually running.

    Docker reports `Running=true` for a container in the `restarting` state:
    it is mid-crash-loop, briefly alive between attempts. Verified against a
    container looping on `exit 1`:

        Status=restarting  Running=true  RestartCount=7

    Trusting `Running` alone is why headscale crash-looped for 45 hours while
    this tool reported it `up` (INCIDENT-2026-08-25). A restarting container
    is the opposite of up, so it is excluded here rather than at each call
    site -- `merge` must agree with `aggregate_status`, or `last_seen_up`
    advances hourly through an outage.
    """
    return service["running"] and service.get("status") != "restarting"


def aggregate_status(defined, observed, *, daemon_ok, ever_ran):
    """Collapse a stack's services into one status.

    `up` requires every defined service to be genuinely running (see is_up)
    AND every healthcheck that exists to report healthy. A stack where Loki is
    dead and Grafana is fine is `degraded`, not `up` -- that distinction is
    the reason this function exists rather than a simple any/all.
    """
    if not daemon_ok:
        return "unknown"

    running = {name for name, s in observed.items() if is_up(s)}
    if not running:
        return "down" if ever_ran else "never"

    if running != set(defined):
        return "degraded"

    # health is None for the many images that define no healthcheck; absence
    # of a check is not a failed check.
    unhealthy = [s for s in observed.values()
                 if s["health"] is not None and s["health"] != "healthy"]
    return "degraded" if unhealthy else "up"


def observe_services(project):
    """Runtime detail per service for one Compose project.

    Returns service-name -> detail, or None when the daemon is unreachable.
    Containers are joined to services by the labels Compose sets itself, so
    there is no naming convention to keep in sync.

    `.Config.Image`, not `.Image`: the latter is a digest (sha256:3ed750...),
    the former the readable tag (grafana/grafana:13.1.2).
    Both calls are bounded: a wedged dockerd makes `docker ps` block forever,
    and returning None on a timeout reuses the daemon-unreachable path that
    already renders the stack `unknown`.
    """
    try:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--filter",
             f"label=com.docker.compose.project={project}",
             "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if listing.returncode != 0:
        return None

    ids = listing.stdout.split()
    if not ids:
        return {}

    try:
        detail = subprocess.run(
            ["docker", "inspect", "--format",
             "{{index .Config.Labels \"com.docker.compose.service\"}}\t"
             "{{.State.Status}}\t{{.State.Running}}\t{{.RestartCount}}\t"
             "{{.Config.Image}}\t"
             "{{if .State.Health}}{{.State.Health.Status}}{{end}}", *ids],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if detail.returncode != 0:
        return None

    services = {}
    for line in detail.stdout.rstrip("\n").splitlines():
        name, state, running, restarts, image, health = line.split("\t")
        services[name] = {
            # .State.Status, not just .State.Running: a crash-looping
            # container reports Running=true between restart attempts, so
            # liveness alone reads a crash-loop as healthy. See is_up().
            "status": state,
            "running": running == "true",
            # RestartCount resets whenever Compose or Watchtower recreates the
            # container, so this counts the current instance only.
            "restarts": int(restarts),
            "image": image,
            "health": health or None,
        }
    return services


def host_name():
    """HOMELAB_HOST is the repo-wide convention; hostname is the fallback."""
    return os.environ.get("HOMELAB_HOST") or socket.gethostname()


def state_path():
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "homelab" / f"status-{host_name()}.json"


# Fixed-width UTC, and that is load-bearing: merge() compares these
# timestamps as plain strings, which is only correct because every field is
# zero-padded to the same width and the zone is always Z.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso():
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def _skeleton():
    return {"host": host_name(), "updated": None,
            "last_archive": None, "archive_dir": None, "stacks": {}}


def load_state(path):
    """Read the state file, quarantining it if it will not parse.

    Losing history is bad; silently writing into a broken file is worse.
    """
    if not path.exists():
        return _skeleton()
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = path.with_name(f"{path.name}.{stamp}.corrupt")
        path.rename(quarantine)
        print(f"state file unreadable ({exc}); moved to {quarantine}",
              file=sys.stderr)
        return _skeleton()


SHAPE = {
    "present": False,
    "intent": None,
    "first_observed_running": None,
    "last_seen_up": None,
    "last_seen_down": None,
    "services": {},
}


def repair_record(record):
    """Fix one state record in place; return a list of what was changed.

    THIS IS THE ONE EXCEPTION TO THE APPEND-ONLY RULE that merge() and
    README.md both state, which is why it is a function of its own rather than
    four lines inside the merge. It runs only against states no capture can
    produce, so it cannot destroy an observation -- every repair here is
    undoing an edit, and it says what it undid rather than doing it quietly.

    A state file is external input. It outlives the code that wrote it,
    survives a schema addition, and gets hand-edited; both repairs below come
    from one cleanup script on 2026-09-06.

    - A MISSING KEY. Readers index these fields directly rather than guarding
      each access, so a gap is a KeyError -- and the cleanup deleted a key
      instead of setting it to None, after which every hourly capture died.
      On a timer that is silence, not an error anyone sees. None restores the
      "here and never ran" state, so the record is honest and not merely
      non-crashing.

    - A LAST-UP ON A STACK THAT NEVER RAN. first_observed_running and
      last_seen_up are written together in merge() and only together, so this
      pairing is unreachable from any capture. The same cleanup cleared the
      first and left the second, and 26 planned stacks then printed
      `STATUS=never` beside `LAST UP=2026-09-06` -- one line contradicting
      itself. "Never ran" is the honest reading: the run being remembered was
      an accidental `up -d` across every stack in the repo, undone within the
      hour.
    """
    repairs = []
    for key, default in SHAPE.items():
        if key not in record:
            record[key] = default
            repairs.append(f"{key} was missing, set to {default!r}")
    if record["first_observed_running"] is None and record["last_seen_up"]:
        repairs.append(
            f"last_seen_up was {record['last_seen_up']} on a stack that has "
            f"never run, cleared")
        record["last_seen_up"] = None
    return repairs


def merge(state, name, *, present, intent, observed, now):
    """Fold one capture into the state record for `name`, in place.

    Returns a list of repair messages, normally empty -- see repair_record(),
    which is the sole exception to the rule below.

    Append-only by design. `docker compose down` destroys the container and
    every timestamp on it, so nothing here is ever deleted or moved backwards:

    - first_observed_running is written once and never modified. It is NOT the
      date the directory appeared -- that is just when you ran `git add`.
    - last_seen_up only moves forward.
    - a stack whose directory is gone keeps its record and its last-known
      intent, so it reports "removed (last known: production)" rather than
      being confused with one you deliberately retired.
    - the initial record seeds every timestamp as None rather than omitting
      the keys. That is a third state, not an empty one: absent means this
      host has never seen the stack, None means it is here and has never run,
      a timestamp means it ran. It is also what lets every reader index these
      fields directly instead of guarding each access -- and note that a
      dict .get(key, default) will NOT save a caller here, since the default
      fires only when the key is absent, never when it is present and None.
    """
    record = state["stacks"].setdefault(name, {**SHAPE, "present": present})
    repairs = repair_record(record)

    record["present"] = present
    if present and intent is not None:
        record["intent"] = intent  # frozen once the stack is gone

    if observed is None:  # daemon unreachable: observe nothing, claim nothing
        return repairs

    record["services"] = observed
    running = any(is_up(s) for s in observed.values())

    if running:
        if record["first_observed_running"] is None:
            record["first_observed_running"] = now
        if record["last_seen_up"] is None or now > record["last_seen_up"]:
            record["last_seen_up"] = now
    elif observed or record["first_observed_running"]:
        if record["last_seen_down"] is None or now > record["last_seen_down"]:
            record["last_seen_down"] = now
    return repairs


def save_state(path, state):
    """Write state atomically, tolerating a concurrent writer.

    A manual run can race the hourly capture timer. `Path.replace` is atomic,
    but only a per-process temp name keeps the two writers from interleaving
    into one file -- a shared name lets one process's partial write get
    clobbered by the other's before either reaches replace(). If both still
    land, it's last-writer-wins between two observations of the same moment;
    that's fine, because merge() is monotonic and the loser's data is
    re-derived on the next capture. No lock file needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = now_iso()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


class Row(NamedTuple):
    name: str
    lifecycle: str | None
    presence: str
    host: str | None
    status: str
    last_up: str
    data: str | None
    backup: str
    purpose: str | None
    description: str | None
    errors: list
    services: dict


LIFECYCLE_ORDER = {v: i for i, v in enumerate(ENUMS["lifecycle"])}

COLUMNS = ("STACK", "LIFECYCLE", "PRESENCE", "HOST", "STATUS",
           "LAST UP", "DATA", "BACKUP", "PURPOSE")


def sort_rows(rows):
    """Lifecycle order first, then name. An unrecognised lifecycle sorts last
    rather than raising -- a bad value is already reported as an error, and
    losing the whole table over it would defeat the point."""
    return sorted(rows, key=lambda r: (
        LIFECYCLE_ORDER.get(r.lifecycle, len(LIFECYCLE_ORDER)), r.name))


# Stacks whose lifecycle says they are meant to be running. `planned` and
# `developing` are expected to be down; `retired`/`not-needed` deliberately so.
ALERT_LIFECYCLES = ("production",)


def has_run_here(state, name):
    """Whether this host has ever observed the stack running.

    Scoping by history rather than by x-homelab.host: that field names a role
    ("always-on", "server"), not a hostname, so it cannot say whether a stack
    belongs on the machine running this check. `first_observed_running` can --
    it is only ever set by an observation made here.
    """
    return (state["stacks"].get(name) or {}).get("first_observed_running") is not None


def failing_stacks(rows, state):
    """Production stacks that have run on THIS host and are no longer up.

    Without the has_run_here scoping the laptop alerts on every server-only
    stack.

    `never` and `unknown` are excluded on purpose: a stack that has never run
    is not broken, and an unreachable daemon is a different failure that makes
    the whole capture meaningless rather than one stack bad.
    """
    return [row for row in rows
            if row.lifecycle in ALERT_LIFECYCLES
            and row.status in ("down", "degraded")
            and has_run_here(state, row.name)]


def runs_production_here(rows, state):
    """Whether this host is one that actually carries production stacks.

    Gates the host-level checks below so the laptop, which runs the same timer,
    does not report on a daemon configuration that only matters on the server.
    """
    return any(row.lifecycle in ALERT_LIFECYCLES and has_run_here(state, row.name)
               for row in rows)


DAEMON_JSON = Path("/etc/docker/daemon.json")
MIN_SHUTDOWN_TIMEOUT = 30


def shutdown_timeout_problem(path=DAEMON_JSON, minimum=MIN_SHUTDOWN_TIMEOUT):
    """Check dockerd's graceful-stop budget for host shutdown, or None if fine.

    On shutdown, dockerd SIGTERMs every container, waits `shutdown-timeout`,
    then SIGKILLs. The default is 15s, and a Postgres checkpoint on the first server's
    5400rpm disk can outrun that -- silently, because an abrupt shutdown looks
    exactly like a clean one until you next read the database.

    This lived in scripts/safe-shutdown.sh, which refused to power off when the
    setting was missing. That guard only fired if you remembered to use the
    script instead of `poweroff`, which is not a guard. Here it runs hourly
    whether anyone remembers anything. The script is gone (2026-08-26).

    Read as JSON, not grepped: a key that is absent, commented out, or a string
    must not read as configured.
    """
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return f"{path} does not exist; dockerd defaults to a 15s shutdown timeout"
    except PermissionError:
        return None  # unreadable is not misconfigured; do not cry wolf
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return f"{path} will not parse: {exc}"

    # Same guard as userland_proxy_problem: a valid JSON document whose root
    # is not an object has no .get(), and this would die on it rather than
    # report. The two functions deliberately still DIFFER on PermissionError
    # above -- see the note there.
    if not isinstance(config, dict):
        return (f"{path} does not contain a JSON object "
                f"(root is {type(config).__name__}); dockerd will not start "
                f"with it")
    value = config.get("shutdown-timeout")

    if not isinstance(value, int) or isinstance(value, bool):
        return (f"shutdown-timeout is {value!r} in {path}, want an integer "
                f">= {minimum} (containers get 15s then SIGKILL)")
    if value < minimum:
        return f"shutdown-timeout is {value}s in {path}, want >= {minimum}s"
    return None


# Absent is not the same as false here, and the difference is the whole point:
# dockerd defaults userland-proxy to TRUE, so a missing key is the broken
# value rather than an unset one.
_MISSING = object()


def userland_proxy_problem(path=DAEMON_JSON):
    """Whether dockerd will rewrite client source addresses, or None if fine.

    `userland-proxy: false` is load-bearing, not tuning. With it true,
    docker-proxy accepts the connection and opens a NEW one to the container,
    so the container sees the proxy rather than the client. Every Caddy vhost
    gated on `remote_ip` then refuses everything, closing connections with
    status=0 -- which looks like a network failure, not a rejection. See
    stacks/caddy/README.md and $SITE_DIR/docs/devices.md.

    This is the LATENT half of the check: it reports the configuration that
    will apply at the next daemon restart. docker_proxy_running_problem()
    reports what is happening now. They disagree in both directions and each
    disagreement is real -- a corrected file with a stale daemon is broken
    while looking fine, and a wrong file with a clean daemon is fine while
    being one restart from breaking.

    Read as JSON, not grepped, for the same reason as shutdown_timeout_problem:
    a key that is absent, commented out, or a string must not read as
    configured. Absence is specifically reported, because dockerd's own
    default is the value that breaks this.
    """
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return (f"{path} does not exist; dockerd defaults userland-proxy to "
                f"true, which rewrites client source addresses and breaks "
                f"every remote_ip-gated vhost")
    except PermissionError:
        # DELIBERATELY not None, and deliberately different from
        # shutdown_timeout_problem's "do not cry wolf" above. An unreadable
        # daemon.json here leaves the setting unverified, and unverified plus
        # a clean process count reports fully green while every gated vhost
        # could be one daemon restart from refusing everything. "Not checked"
        # must not be indistinguishable from "checked and fine" -- the failure
        # this repo keeps hitting.
        return (f"{path} is unreadable, so userland-proxy was NOT checked -- "
                f"this is not a pass")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return f"{path} will not parse: {exc}"

    # A JSON document whose root is a list, string, number or null is valid
    # JSON and has no .get(); dockerd rejects it, so it is a real
    # misconfiguration rather than a crash for this script to die on.
    if not isinstance(config, dict):
        return (f"{path} does not contain a JSON object "
                f"(root is {type(config).__name__}); dockerd will not start "
                f"with it")
    value = config.get("userland-proxy", _MISSING)

    if value is _MISSING:
        return (f"userland-proxy is not set in {path}; dockerd defaults it to "
                f"true, so at the next daemon restart every remote_ip-gated "
                f"vhost will refuse every request")
    # `is not False` rather than a truthiness test: 0 and "false" are wrong
    # types that happen to be falsy, and dockerd would reject or ignore them.
    if value is not False:
        return (f"userland-proxy is {value!r} in {path}, want false -- "
                f"docker-proxy rewrites client source addresses to the bridge "
                f"gateway and every remote_ip-gated vhost refuses every request")
    return None


def docker_proxy_running_problem(timeout=15):
    """Whether docker-proxy is rewriting source addresses right now.

    The LIVE half. `userland-proxy: false` only takes effect when dockerd
    restarts, so the file can be correct while the running daemon still has
    docker-proxy processes from before the change.

    TRAP: `pgrep` exits 1 when it matches nothing, and that is the HEALTHY
    case here. Treating non-zero as an error inverts this check into one that
    is green exactly when docker-proxy is running. Exit codes are pgrep(1):
    0 matched, 1 no match, 2 usage error, 3 fatal.

    A check that could not run is reported rather than passed: an absent
    pgrep, a timeout or a usage error all mean this went unchecked, and
    "unchecked" must not be indistinguishable from "healthy".
    """
    try:
        probe = subprocess.run(["pgrep", "-c", "docker-proxy"],
                               capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"cannot check for docker-proxy processes: {exc}"

    if probe.returncode == 1:
        return None                      # no matches -- the healthy case
    if probe.returncode != 0:
        return (f"pgrep failed with exit {probe.returncode} -- docker-proxy "
                f"was NOT checked: "
                f"{probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else 'no error output'}")

    count = probe.stdout.strip()
    return (f"{count} docker-proxy process(es) are running, so client source "
            f"addresses are being rewritten to the bridge gateway and every "
            f"remote_ip-gated vhost refuses every request. The daemon is "
            f"running with userland-proxy enabled whatever /etc/docker/daemon.json "
            f"now says; it needs `systemctl restart docker`.")


def daemon_problem():
    """Whether dockerd itself is unreachable, or None if it answers.

    Every stack goes `unknown` when the daemon is down, and failing_stacks
    excludes `unknown` on purpose -- one alert per stack for a single host
    failure is noise. But excluding it everywhere means a dead daemon raises
    nothing at all until stale_backup_problem notices 36 hours later, which is
    the same shape of silence as INCIDENT-2026-08-25.
    """
    try:
        probe = subprocess.run(["docker", "ps", "-q"],
                               capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"cannot run docker at all: {exc}"
    if probe.returncode != 0:
        return ("docker daemon is unreachable, so every stack reports unknown "
                f"and nothing here is being monitored: "
                f"{probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else 'no error output'}")
    return None


# Any bit set for group or other. A .env holds the credential that makes the
# stack work -- Porkbun API keys, Duplicati recovery credentials, database
# passwords -- and `umask 002` on Ubuntu writes 0664 by default, so the
# insecure mode is the one you get by not thinking about it.
INSECURE_ENV_BITS = 0o077


def env_permission_problem(dirs=None):
    """Stack .env files readable by anyone but their owner, or None if fine.

    Checked here rather than in check.sh because .env files are gitignored:
    they exist per host, they differ per host, and an offline repo check
    cannot see the ones that matter. This runs hourly on the machine that
    actually holds them.

    A single-user box makes this cheap rather than urgent -- but the modes
    were already inconsistent (four files at 0600, the rest at 0664), which
    means somebody had decided this mattered and it did not stick. That is
    the thing a check fixes and a convention does not.
    """
    bad = []
    for directory in sorted(dirs if dirs is not None else stack_dirs()):
        env = directory / ".env"
        try:
            mode = env.stat().st_mode & 0o777
        except FileNotFoundError:
            continue
        except OSError:
            continue  # unreadable is not misconfigured; do not cry wolf
        if mode & INSECURE_ENV_BITS:
            bad.append(f"{directory.name}/.env is {mode:04o}")
    if not bad:
        return None
    return (f"{len(bad)} .env file(s) readable beyond their owner: "
            f"{', '.join(bad[:5])}"
            f"{f' and {len(bad) - 5} more' if len(bad) > 5 else ''}"
            f" -- fix with: chmod 600 stacks/*/.env")


# Percent of a filesystem that must stay free. 10% of the first server's 915 G is ~91 G,
# which is a lot of runway -- deliberately. /srv/media sits on the same
# filesystem and grows on purpose, so this drifts toward the threshold in
# normal use and the alert wants to arrive while there is still time to move
# something, not once writes are already failing.
MIN_FREE_PERCENT = 10

# Inodes are checked separately because they exhaust independently: a
# filesystem can be 3% full by bytes and completely unwritable. The first server is at 2%
# of 61M, so this will not fire -- it is here because when it does happen the
# symptom ("No space left on device" on a disk with 800 G free) reads as a
# hardware fault, and an alert that names inodes saves the afternoon.
MIN_FREE_INODES_PERCENT = 10

# Where full hurts: images and the nightly archives. All of these are on / for
# the first server, so this usually collapses to one filesystem -- deduped by device below.
#
# /var/lib/containerd is here because that is where the images actually are.
# The first server ran the containerd snapshotter (`docker info` reports
# Driver=overlayfs), which stores image layers under containerd and leaves
# /var/lib/docker holding little but volumes and metadata -- 67 G against
# 2.7 G when this was measured. Watching only /var/lib/docker gets the right
# answer here purely because the two share a filesystem; give the image store
# its own mount, as a rebuild might, and the check would silently be watching
# the wrong disk. Both are listed so the check does not depend on that.
#
# The archive directory is NOT in this tuple, because it is not a constant:
# BACKUP_ARCHIVE_DIR can put it on another mount entirely, and host-setup.md
# tells a server to do exactly that. capture() resolves the effective path
# with archive_dir() and problems_for() passes it in, so a rebuilt server
# following that advice still gets its archive filesystem watched.
WATCHED_PATHS = (Path("/var/lib/docker"), Path("/var/lib/containerd"),
                 Path.home() / ".local" / "state")


def disk_problem(paths=WATCHED_PATHS, min_free=MIN_FREE_PERCENT,
                 min_inodes=MIN_FREE_INODES_PERCENT):
    """Filesystems too close to full, by bytes or by inodes, or None if fine.

    SMART cannot see this. A disk filling up is not a failing disk -- every
    counter stays zero and the drive reports PASSED right up to the write that
    fails, which is why smartd being healthy says nothing about it.
    """
    problems, seen = [], set()
    for path in paths:
        try:
            device = path.stat().st_dev
            if device in seen:
                continue
            stats = os.statvfs(path)
        except OSError:
            continue  # not present on this host, or unreadable; not a problem
        seen.add(device)

        if stats.f_blocks:
            free = 100 * stats.f_bavail / stats.f_blocks
            if free < min_free:
                gib = stats.f_bavail * stats.f_frsize / 1024**3
                problems.append(f"{path} filesystem is {100 - free:.0f}% full "
                                f"({gib:.0f} GiB free, want >= {min_free}%)")
        if stats.f_files:
            free_inodes = 100 * stats.f_favail / stats.f_files
            if free_inodes < min_inodes:
                problems.append(f"{path} filesystem has {100 - free_inodes:.0f}% "
                                f"of its inodes used ({stats.f_favail} free) -- "
                                f"writes will fail with 'No space left on "
                                f"device' however many bytes are free")
    return "; ".join(problems) or None


MAX_ARCHIVE_AGE_HOURS = 36
# Measured: the first server reaches graphical.target 2m18s after power-on and its
# containers settle shortly after. One minute clears the boot race without
# delaying a real alert meaningfully.
RETRY_SECONDS = 60


def stale_backup_problem(last, now, maximum=MAX_ARCHIVE_AGE_HOURS):
    """Whether the newest archive is too old, given nightly backups.

    This is the recency half of backup monitoring. The coverage half -- whether
    the right set of volumes is archived at all -- is scripts/check_backups.py.

    The existing alert is systemd `OnFailure` on homelab-backup.service, which
    fires once, when a run fails. That is the wrong shape for this: a failed
    run is not retried (`Persistent=true` only catches up runs that were
    *missed*, not ones that ran and exited non-zero), so one failure at 03:00
    silently becomes a 48-hour gap, and the single notification announcing it
    has already scrolled off the phone. On 2026-08-26 that notification also
    said "backups failed" during a total outage -- the right alert naming the
    wrong thing.

    Age is the honest question anyway. It does not care why there is no
    archive: a failed run, a stopped timer, a full disk and a container that
    was never recreated all look the same here, and all of them mean the same
    thing to whoever needs a restore.

    36h, not 24h: a nightly archive is between 0 and 24 hours old in normal
    operation, so 24 would fire on ordinary jitter. 36 means at least one
    nightly run has genuinely not produced an archive, with half a day of
    slack for a late boot.
    """
    if last is None:
        return ("no backup archive exists at all -- this host has run the "
                "backup stack, so it should have written one")
    hours = (datetime.strptime(now, TIMESTAMP_FORMAT)
             - datetime.strptime(last, TIMESTAMP_FORMAT)).total_seconds() / 3600
    if hours <= maximum:
        return None
    return (f"newest backup archive is {hours:.0f}h old, want <= {maximum}h "
            f"(written {last})")


def unmounted_backup_problem(registered, mounted, daemon_down=False):
    """Registered volumes the running backup container does not mount.

    The coverage half of backup monitoring, which stale_backup_problem cannot
    see: a container created before a volume was registered keeps writing
    fresh, incomplete archives. ntfy_data sat in that gap on 2026-09-14 while
    the digest said OK.

    `mounted` None means the container could not be inspected -- no docker,
    a timeout, or no `backup` container at all. The caller only asks on a host
    that has run the backup stack, so there that is a problem, not a clean
    result: a deleted backup container is exactly what should page. Skipped
    when dockerd itself is down, which daemon_problem() already names.
    """
    if registered is None:
        return None
    if mounted is None:
        if daemon_down:
            return None
        return ("cannot inspect the backup container's mounts -- is it "
                "gone? docker inspect backup")
    missing = sorted(registered - mounted)
    if not missing:
        return None
    return (f"backup container is not archiving {len(missing)} registered "
            f"volume(s): {', '.join(missing)} -- recreate it: cd stacks/backup "
            f"&& docker compose up -d --force-recreate")


def describe_failure(row):
    """One line per failing stack, naming the service that is actually wrong.

    This lands in the journal, which is what notify.py forwards to the phone,
    so "headscale: down" alone would send you to the machine anyway.
    """
    detail = []
    for name, svc in sorted(row.services.items()):
        if is_up(svc) and (svc["health"] is None or svc["health"] == "healthy"):
            continue
        state = svc.get("status") or ("running" if svc["running"] else "stopped")
        if svc["health"]:
            state += f"/{svc['health']}"
        if svc["restarts"]:
            state += f" restarts={svc['restarts']}"
        detail.append(f"{name}={state}")
    return f"{row.name}: {row.status}" + (f" ({', '.join(detail)})" if detail else "")


def _cells(row):
    flag = " !" if row.errors else ""
    return [
        row.name + flag,
        row.lifecycle or "?",
        row.presence,
        row.host or "?",
        row.status,
        row.last_up,
        row.data or "?",
        row.backup,
        row.purpose or "?",
    ]


def render(rows, last_archive, *, long=False, markdown=False):
    rows = sort_rows(rows)
    header = f"last archive: {last_archive or 'never'}"
    table = [_cells(r) for r in rows]

    if markdown:
        lines = [header, "", "| " + " | ".join(COLUMNS) + " |",
                 "| " + " | ".join("---" for _ in COLUMNS) + " |"]
        for row, cells in zip(rows, table):
            lines.append("| " + " | ".join(cells) + " |")
            if long:
                if row.description:
                    lines.append(f"| &nbsp;&nbsp;_{row.description}_ |"
                                 + " |" * (len(COLUMNS) - 1))
                for name, svc in sorted(row.services.items()):
                    lines.append(
                        f"| &nbsp;&nbsp;{name} | {svc['health'] or '-'} | "
                        f"{svc['image']} | restarts(current): {svc['restarts']} |"
                        + " |" * (len(COLUMNS) - 4))
        return "\n".join(lines) + "\n"

    widths = [max(len(c) for c in col)
              for col in zip(COLUMNS, *table)] if table else \
             [len(c) for c in COLUMNS]
    lines = [header, ""]
    lines.append("  ".join(h.ljust(w) for h, w in zip(COLUMNS, widths)).rstrip())
    for row, cells in zip(rows, table):
        lines.append("  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip())
        if long:
            if row.description:
                lines.append(f"  {row.description}")
            for name, svc in sorted(row.services.items()):
                lines.append(
                    f"  {name:<12} {svc['health'] or '-':<10} "
                    f"{svc['image']:<40} restarts(current): {svc['restarts']}")
        for error in row.errors:
            lines.append(f"  ! {error}")
    return "\n".join(lines) + "\n"


def _relative(timestamp, now):
    """'now' for the current capture, otherwise the date. Precision below a
    day is noise in a table whose whole point is spotting months-old stacks."""
    if timestamp is None:
        return "-"
    if timestamp == now:
        return "now"
    return timestamp[:10]


def archive_dir(backup_config):
    """Where archives land, read off the backup service's /archive mount.

    Compose has already interpolated the chained XDG default, so reading the
    mount avoids keeping a second copy of that path expression in sync.
    """
    services = backup_config.get("services") or {}
    for mount in (services.get("backup") or {}).get("volumes") or []:
        if mount.get("target") == "/archive":
            return Path(mount["source"])
    return None


def last_archive(backup_config):
    """Timestamp of the newest archive, or None if there has never been one.

    One archive covers every registered volume, so this is global -- there is
    no per-stack backup time to report.
    """
    directory = archive_dir(backup_config)
    if directory is None or not directory.is_dir():
        return None
    archives = [p for p in directory.iterdir() if p.is_file()]
    if not archives:
        return None
    newest = max(p.stat().st_mtime for p in archives)
    return datetime.fromtimestamp(newest, timezone.utc).strftime(TIMESTAMP_FORMAT)


def backup_state(volumes, registered):
    """BACKUP column for one stack.

    `registered` is None when stacks/backup itself would not parse -- on a
    fresh clone every stack fails to interpolate until its .env exists. That
    is not "nothing is registered"; reporting `no` there would be an alarming
    false negative indistinguishable from a real one.
    """
    if registered is None:
        return "?"
    if not volumes:
        return "n/a"
    return "yes" if volumes <= registered else "no"


def _stored_row(name, record, *, presence, status, now, backup, errors):
    """A row built from the state file alone, for a stack whose compose file
    cannot be read right now -- removed, or failing to parse. The frozen
    intent and history are exactly what you want to see at that moment."""
    frozen = (record or {}).get("intent") or {}
    return Row(
        name, frozen.get("lifecycle"), presence, frozen.get("host"), status,
        _relative((record or {}).get("last_seen_up"), now), frozen.get("data"),
        backup, frozen.get("purpose"), frozen.get("description"), errors, {})


def problems_for(rows, state):
    """Everything worth paging about, from one capture.

    Split out of __main__ so the retry there can re-evaluate the whole list
    against a second capture rather than re-testing one condition.
    """
    problems = [describe_failure(row) for row in failing_stacks(rows, state)]

    # NOT gated on runs_production_here: a leaked credential is a leaked
    # credential on the laptop too, and the laptop's HOMELAB_ALERT=0 already
    # keeps it from paging.
    env_problem = env_permission_problem()
    if env_problem:
        problems.append(env_problem)

    if runs_production_here(rows, state):
        watched = WATCHED_PATHS
        if state.get("archive_dir"):
            watched += (Path(state["archive_dir"]),)
        for problem in (daemon_problem(), shutdown_timeout_problem(),
                        userland_proxy_problem(),
                        docker_proxy_running_problem(),
                        disk_problem(watched)):
            if problem:
                problems.append(problem)

    # Scoped to hosts that have actually run the backup stack, so the laptop
    # does not report a missing archive it was never supposed to write.
    if has_run_here(state, "backup"):
        stale = stale_backup_problem(state.get("last_archive"), now_iso())
        if stale:
            problems.append(stale)
        backup_config = stack_config(ROOT / "stacks" / "backup")
        unmounted = unmounted_backup_problem(
            registered_volumes(backup_config) if backup_config else None,
            mounted_volumes(),
            daemon_down=daemon_problem() is not None)
        if unmounted:
            problems.append(unmounted)

    return problems


def capture():
    """Observe every stack, fold into state, and build the rows to render.

    Returns (state, rows, archive_display); archive_display is "unknown" when
    stacks/backup could not be read, as opposed to a real "never".
    """
    path = state_path()
    state = load_state(path)
    now = now_iso()

    # Probe the daemon ONCE, before any per-stack work. Without this the run
    # is unbounded in the way that matters: 42 stacks x DOCKER_TIMEOUT is
    # 21 minutes against homelab-status.service's TimeoutStartSec=300, so a
    # wedged dockerd would still blow the unit ceiling and alert as a bare
    # "homelab-status.service failed" with nothing saying why. Skipping the
    # observation reuses the existing observed=None path -- every stack
    # renders `unknown` -- and problems_for() reports daemon_problem() by
    # name, which is the message worth waking up to.
    daemon_down = daemon_problem()
    if daemon_down:
        print(f"  {daemon_down}", file=sys.stderr)

    backup_config = stack_config(ROOT / "stacks" / "backup")
    # Reuses check_backups' registration logic rather than reimplementing the
    # two-entry rule in a second place where it could drift.
    if backup_config is None:
        # Leave the stored last_archive alone: unreadable is not "gone".
        registered, archive_display = None, "unknown"
    else:
        registered = registered_volumes(backup_config)
        state["last_archive"] = last_archive(backup_config)
        archive_display = state["last_archive"]
        # Recorded so the disk check can watch it: BACKUP_ARCHIVE_DIR may put
        # archives on a filesystem that WATCHED_PATHS does not touch.
        directory = archive_dir(backup_config)
        state["archive_dir"] = str(directory) if directory else None

    on_disk = {d.name: d for d in stack_dirs()}
    rows, repairs = [], []

    for name, directory in sorted(on_disk.items()):
        config = stack_config(directory)
        if config is None:
            rows.append(_stored_row(
                name, state["stacks"].get(name), presence="present",
                status="unknown", now=now, backup="?",
                errors=["compose config failed to parse"]))
            continue

        intent, errors = parse_intent(config)
        project = config.get("name", name)
        observed = None if daemon_down else observe_services(project)
        for line in merge(state, name, present=True, intent=intent,
                          observed=observed, now=now):
            repairs.append((name, line))

        record = state["stacks"][name]
        defined = set((config.get("services") or {}).keys())
        status_value = aggregate_status(
            defined, observed or {}, daemon_ok=observed is not None,
            ever_ran=record["first_observed_running"] is not None)

        rows.append(Row(
            name, intent["lifecycle"], "present", intent["host"], status_value,
            _relative(record["last_seen_up"], now), intent["data"],
            backup_state(volumes_needing_backup(config), registered),
            intent["purpose"], intent["description"], errors,
            record["services"]))

    for name in list(state["stacks"]):
        if name in on_disk:
            continue
        for line in merge(state, name, present=False, intent=None,
                          observed=None, now=now):
            repairs.append((name, line))
        record = state["stacks"][name]
        rows.append(_stored_row(
            name, record, presence="removed",
            status="down" if record["first_observed_running"] else "never",
            now=now, backup="n/a", errors=[]))

    # Loud, not silent. A repair means the state file held something no
    # capture could have written -- it was edited -- and the append-only rule
    # says an observation is never destroyed. Naming the record and what
    # changed is what keeps this an exception rather than a licence.
    if repairs:
        # Count RECORDS, not messages. One record can need two repairs -- a
        # missing key and a stale last-up -- and counting the messages would
        # report that as two damaged records.
        print(f"repaired {len({name for name, _ in repairs})} state "
              f"record(s) -- these held values no capture can produce:",
              file=sys.stderr)
        for name, line in repairs:
            print(f"  {name}: {line}", file=sys.stderr)

    save_state(path, state)
    return state, rows, archive_display


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", action="store_true",
                        help="capture only, print nothing (the timer's entry point)")
    parser.add_argument("--long", action="store_true",
                        help="add per-service health, image and restart detail")
    parser.add_argument("--markdown", action="store_true",
                        help="emit a Markdown table")
    args = parser.parse_args()

    state, rows, archive_display = capture()
    if not args.capture:
        print(render(rows, archive_display,
                     long=args.long, markdown=args.markdown), end="")

    # Exit non-zero so systemd marks the unit failed and the existing
    # OnFailure=homelab-failure-notify@.service forwards it to the phone.
    # Every capture re-reports a stack that is still broken, which is
    # deliberate: this ran hourly for 45 hours during INCIDENT-2026-08-25 and
    # said nothing, and a repeated alert is the lesser failure.
    problems = problems_for(rows, state)

    # One retry before alerting, and only for the timer: a run shortly after a
    # boot can land while containers are still `starting` and their
    # healthchecks have no verdict yet. On 2026-08-29 that paged five stacks
    # that were all healthy by the next hourly run. `starting` is deliberately
    # NOT excluded from aggregate_status -- a container stuck in start_period
    # forever is a real failure -- so the second look is what tells the two
    # apart. Interactive runs skip this: a 60-second hang at a terminal is
    # worse than a stale-looking table you can just run again.
    if problems and args.capture:
        print(f"{len(problems)} problem(s), re-checking in {RETRY_SECONDS}s "
              "before alerting (the stack may still be starting):",
              file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        time.sleep(RETRY_SECONDS)
        state, rows, archive_display = capture()
        problems = problems_for(rows, state)

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)

    # Alerting defaults ON and is opted out of, never opted into. A monitor
    # that is silently disabled is the exact failure this whole mechanism
    # exists to prevent, so a host nobody configured is loud rather than mute.
    # The dev laptop sets HOMELAB_ALERT=0 in its user unit: production stacks
    # there are down most of the time by design ("on a laptop where hardly
    # anything stays running" -- the module docstring), so every hourly run
    # failed and paged. Found 2026-08-26, after it had been doing exactly that.
    if problems and os.environ.get("HOMELAB_ALERT", "1") != "0":
        sys.exit(1)
