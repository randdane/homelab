# /// script
# requires-python = ">=3.11"
# ///
"""Report pending image updates for the stacks whose patching is urgent.

    uv run scripts/check_updates.py
    uv run scripts/check_updates.py --all
    uv run scripts/check_updates.py --self-test

Cup already answers "which images have updates" -- it just answers it in a web
UI nobody opens. This turns that into something that arrives. It adds no new
registry polling of its own: it reads Cup's JSON and cross-references the
stacks' own `x-homelab` metadata -- see alert_priority().

Why only the internet-priority ones raise an alert. Everything in this repo is
worth keeping current, but four stacks are the only ones where being behind is
an *exposure* rather than a chore: an unpatched Jellyfin is one CVE away from
being someone else's, while an unpatched Grocy sits behind the tailnet where
reaching it already requires a key. Three of the four are reachable from
outside; authentik is the fourth for a different reason, being the identity
provider the public one trusts. Alerting on
all forty would mean alerting every week, which is the same as not alerting.

This is deliberately a report, not an upgrade. Nothing updates anything here
automatically -- Watchtower was removed on 2026-10-05
(docs/declined-stacks.md). That is a defensible position for an internet-facing media
server whose users are family: an unattended 04:00 upgrade that breaks the TV
app is discovered by someone else, at the worst time, with no idea what
changed. The tradeoff only works if somebody is told when an update is
waiting, which is what this script is for.

One stack is built rather than pulled. `homelab/caddy-crowdsec` exists in no
registry, so Cup can only answer "Unauthorized" about it forever -- and Caddy
is the only thing here listening to the internet, which made the most exposed
component the one nothing watched. A built service is therefore checked by its
Dockerfile's base image instead, and a base image Cup cannot see at all is an
alert rather than a silence.

Jellyfin is also checked against its own GitHub releases, wherever it runs.
Cup missed 12.0 and 12.1 (September 2026) because the tags went from three
parts to two; and once Jellyfin moves to an LXC as an apt package, Cup cannot
see it at all. See native_jellyfin_behind().

What "alerting" means here is `x-homelab.patch_priority`, falling back to
`exposure` -- what being out of date COSTS, which is not always the same as
who can reach the service. authentik is the case that separates them: its
vhost is allowlisted to the LAN and tailnet, but it issues the tokens public
Jellyfin accepts, so its updates are internet-tier while its exposure is not.
An unreadable priority is an error, not a low priority.

Exit codes: 0 nothing waiting, 1 an image at internet patch priority is behind,
its base image went unchecked, or a stack's priority could not be read, 2 could
not check (Cup unreachable or returned nothing usable).
"""

import argparse
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

import compose
import x_homelab

# Cup's own default port, published on the LAN by stacks/cup. Not a secret and
# not authenticated -- it reports image versions, which the registry will tell
# anyone who asks.
CUP_URL = "http://localhost:8010/api/v3/json"

# Printed after every rebuild instruction. `build --pull` alone does not clear
# a base-image alert: with BuildKit it fetches the new base into the build
# cache and leaves the local tag -- the one Cup reads -- on the old digest.
# Measured on the first server 2026-09-15: caddy rebuilt on the new caddy:2.11.4 layers,
# and Cup kept reporting it behind until `docker pull` and a refresh.
CUP_REFRESH = ("       curl -fsS -o /dev/null http://localhost:8010/api/v3/refresh"
               "   # Cup caches; rescan so the alert clears")
TIMEOUT = 30

# The patch priorities that alert rather than merely being worth doing.
# Policy, not schema, and this is its only consumer -- so it lives here and
# not in x_homelab.py, which owns the vocabulary. Compared against
# alert_priority() and not against exposure: the two were the same field until
# 2026-09-06 and are deliberately not any more.
ALERTING = {"internet"}


def alert_priority(meta):
    """(priority, error) for one stack's x-homelab block.

    `patch_priority` when set, `exposure` otherwise. The two answer different
    questions and usually give the same answer: exposure is who can reach the
    service, priority is what being out of date costs. authentik is the case
    that separates them -- its vhost is allowlisted to the LAN and tailnet, so
    `exposure: internet` was simply false, but it issues the tokens Jellyfin
    accepts and Jellyfin IS public. An unpatched identity provider is an
    internet-facing problem reached through a LAN-only door.

    A value that is present and unrecognised is an ERROR and not a priority.
    This function used to return whatever string it found, and a typo then
    failed open in the worst available direction: `patch_priority: interent`
    is not in ALERTING, so the stack silently left the alert set and the run
    exited 0. Reproduced on 2026-09-06. The key exists to keep the identity
    provider alerted on, so a misspelling of it must not be the thing that
    stops alerting -- and the unit sets SuccessExitStatus=2, which is why the
    caller exits 1.

    PRESENCE is tested with `in`, not truthiness. `patch_priority:` with
    nothing after it is YAML null, and `patch_priority: ""` is a blank
    string; both are present-and-invalid, and both fell through to `exposure`
    while the first version of this fix was on disk. A key someone wrote and
    left empty is the likeliest way to get an empty one, so it is the last
    case that should be read as "not set".

    Having NEITHER key is also an error. `exposure` is required, so its
    absence means the metadata is broken, and answering "unknown" for a
    broken block is the same silent non-alerting in a different costume.

    x_homelab.py owns the vocabulary, so this file and status.py cannot
    disagree about what a valid value is. parse_intent() is deliberately not
    reused -- it demands all six required keys and would report errors about
    metadata this script has no opinion on.
    """
    for key in ("patch_priority", "exposure"):
        if key not in meta:
            continue
        value = meta[key]
        if value not in x_homelab.ENUMS["patch_priority"]:
            return None, (f"x-homelab.{key}: {value!r} is not one of "
                          f"{', '.join(x_homelab.ENUMS['patch_priority'])}")
        return value, None
    return None, ("x-homelab has neither patch_priority nor the required "
                  "exposure -- there is nothing to judge urgency by")


class Built(NamedTuple):
    """One stack's image that is built here rather than pulled.

    Named because it was a bare 5-tuple, and three call sites unpacked it
    positionally with `_` for the fields they did not want -- readable only by
    counting commas against the line that built it.
    """
    stack: str
    priority: str      # patch_priority when set, else exposure
    image: str         # what the build produces
    base: str          # the FROM line, which is what Cup can actually watch
    plugins: list      # [(module, pin)] from `xcaddy build --with`

# `FROM [--platform=x] image[:tag] [AS stage]`. Deliberately not a Dockerfile
# parser: no ARG interpolation, no heredocs. A `FROM ${BASE}` here would be
# read literally and reported as an image nobody can find, which is the loud
# failure -- see unchecked_bases().
FROM_LINE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$",
    re.IGNORECASE)


def cup_images(url=CUP_URL, keep=(), drop=()):
    """Return Cup's report as {image reference: has_update}.

    Only images Cup says are in use: it remembers images that are merely
    present on the host, and a dangling layer from a rollback is not something
    to be told about every day.

    `keep` overrides that for references that matter even when nothing runs
    them -- the base image of a locally built one, which is present on the host
    only as a leftover of the build. `drop` removes references outright: a
    locally built image exists in no registry, so Cup can only ever answer with
    an auth error, and a permanent entry in the unknown list is how that list
    stops being read.
    """
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        payload = json.load(response)

    images = {}
    for entry in payload.get("images", []):
        reference = entry["reference"]
        if reference in drop:
            continue
        if not entry.get("in_use") and reference not in keep:
            continue
        result = entry.get("result") or {}
        # A per-image error is not the same as "no update": a rate-limited or
        # renamed registry lookup returns has_update false, which would read
        # as up to date. Carry it through as unknown instead.
        images[reference] = (
            None if result.get("error") else bool(result.get("has_update"))
        )
    return images


def base_image(build):
    """The registry image a Dockerfile's FINAL stage builds FROM, or None.

    The final stage is the one that ships: an earlier `FROM ... AS builder`
    contributes a compiled binary and nothing else, so its version is a
    build-time detail while the last one supplies the runtime the container
    actually runs on. Both are pinned together in stacks/caddy/Dockerfile, and
    reading the last is what keeps this honest if they ever diverge.

    `FROM builder` names an earlier stage rather than an image; those are
    skipped, which leaves the last real registry reference -- and that is the
    right answer, since a final stage built on an earlier one inherits its base
    transitively.

    Compose normalizes `build` to an absolute context with `dockerfile`
    defaulted, so no path guessing is needed here.
    """
    path = Path(build["context"]) / build.get("dockerfile", "Dockerfile")
    try:
        text = path.read_text()
    except OSError:
        return None
    stages, base = set(), None
    for line in text.splitlines():
        found = FROM_LINE.match(line)
        if not found:
            continue
        image, alias = found.group(1), found.group(2)
        if image.lower() not in stages:
            base = image
        if alias:
            stages.add(alias.lower())
    return base


def images_by_priority():
    """Return ({image: [(stack, priority)]}, [Built], [bad metadata]) for every stack.

    A LIST, not a single owner. Shared images are the reason: `postgres` backs
    both authentik (internet) and wger (lan), and a dict keyed by reference
    would keep whichever stack was walked last. Half the time that is the lan
    one, and a pending Postgres update on an internet-priority stack would then
    be filed as not worth alerting on -- silently, and only for the images
    where sharing makes the update most worth knowing about.

    A stack that will not render is skipped rather than fatal, matching the
    rest of scripts/: one broken compose file should not blind the check for
    the other thirty-nine.

    Also returns the built stacks as
    a list of Built records.
    A service with `build:` is filed under its Dockerfile's base image, not
    under the tag it builds to: `homelab/caddy-crowdsec:2.11.4` is in no
    registry, so asking about it can only ever return an auth error, while
    `caddy:2.11.4` is a real image with a real answer. Caddy is the only thing
    on this host listening to the internet, so leaving it permanently
    unanswerable made the most exposed component the one nothing watched.
    """
    found, built = {}, []
    bad = []
    for stack_dir in compose.stack_dirs():
        config = compose.stack_config(stack_dir, declared_only=True)
        if config is None:
            continue
        priority, error = alert_priority(config.get("x-homelab") or {})
        if error:
            bad.append(f"{stack_dir.name}: {error}")
            continue
        for service in (config.get("services") or {}).values():
            image = service.get("image")
            build = service.get("build")
            if build and (base := base_image(build)):
                built.append(Built(stack_dir.name, priority, image, base,
                                   plugin_pins(build)))
                image = base
            if image:
                found.setdefault(image, []).append((stack_dir.name, priority))
    return found, built, bad


def problems_for(updates, priorities, alerting=ALERTING):
    """Split Cup's answer into the images that warrant an alert and the rest.

    An image Cup does not mention at all is silently absent rather than
    reported clean -- see the caller, which counts them.
    """
    alerts, others, unknown = [], [], []
    for reference, has_update in sorted(updates.items()):
        owners = priorities.get(reference) or [("?", "unknown")]
        # Highest priority wins. One owner at internet priority is enough to
        # make a shared image's update worth being told about -- `postgres`
        # backs both authentik and wger.
        priority = "internet" if any(
            e in alerting for _, e in owners) else owners[0][1]
        row = (reference, ", ".join(s for s, _ in owners), priority)
        if has_update is None:
            unknown.append(row)
        elif has_update and priority in alerting:
            alerts.append(row)
        elif has_update:
            others.append(row)
    return alerts, others, unknown


def unchecked_bases(built, updates, alerting=ALERTING):
    """Built stacks whose base image Cup never mentioned, highest priority first.

    This is the failure the rest of this function exists to make loud. Cup
    reports on images present on the host, and a base image is present only as
    a leftover of the build that consumed it -- `docker image prune -a` removes
    it, Cup stops reporting it, and the check goes quiet while looking exactly
    like a pass. Absence has to be an alert, not a silence: the same shape as a
    CI job that was green because it scanned nothing.

    The fix when it fires is to rebuild rather than to pull, since the base is
    only half the image: `docker compose build --pull caddy`.
    """
    return sorted([b for b in built if b.base not in updates],
                  key=lambda b: (b.priority not in alerting, b.stack))


# `--with github.com/owner/repo[/subpath]@vX.Y.Z`, the xcaddy form. Anything
# not on github.com is skipped rather than guessed at: this asks GitHub's tag
# list, and a module hosted elsewhere has no answer here.
XCADDY_WITH = re.compile(
    r"--with\s+(github\.com/[^\s@]+)@(\S+)", re.IGNORECASE)

GITHUB_TAGS = "https://api.github.com/repos/{}/tags?per_page=100"

SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def plugin_pins(build):
    """[(module path, pinned version)] from a Dockerfile's xcaddy build lines.

    Nothing else in this repo can see these. They are `RUN` arguments, so they
    are not a FROM line for Cup and not a go.mod for Dependabot, which leaves
    the CrowdSec bouncer -- the only active defence on the public vhost -- and
    the Porkbun provider, which holds the DNS API credentials, as the two most
    exposed versions here and the two nothing watched.
    """
    path = Path(build["context"]) / build.get("dockerfile", "Dockerfile")
    try:
        text = path.read_text()
    except OSError:
        return []
    return XCADDY_WITH.findall(text)


def plugin_repo(module):
    """`github.com/owner/repo/sub` -> `owner/repo`, or None.

    The module path may carry a subpackage -- the bouncer is imported as
    `.../caddy-crowdsec-bouncer/http` -- but tags live on the repository, so
    everything past the second segment is dropped. A path with fewer than two
    segments after the host cannot name a repository and returns None instead
    of building a URL that 404s.
    """
    parts = module.split("/")
    if len(parts) < 3:
        return None
    return f"{parts[1]}/{parts[2]}"


def newest_tag(repo, timeout=TIMEOUT):
    """The highest semver tag on a GitHub repo, or None if it cannot be read.

    Compared as integer tuples, never as strings: `v0.9.2` sorts above
    `v0.14.1` lexically, which would report the newest release as an update
    available on an older one and then go quiet after someone "fixed" it by
    downgrading.

    Unauthenticated -- 60 requests an hour against a handful of plugins, and a
    token here would be a credential stored to read public tags. A rate-limit
    or network failure returns None, which the caller reports as unknown
    rather than as up to date.
    """
    request = urllib.request.Request(
        GITHUB_TAGS.format(repo),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "homelab-check-updates"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            tags = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    versions = []
    for tag in tags:
        # Subpath modules can tag as `http/v1.2.3`; the version is the last
        # segment either way.
        found = SEMVER.match(str(tag.get("name", "")).rsplit("/", 1)[-1])
        if found:
            versions.append((tuple(int(g) for g in found.groups()),
                             tag["name"]))
    if not versions:
        return None
    return max(versions)[1]


def outdated_plugins(built, lookup=newest_tag):
    """[(stack, priority, module, pinned, latest_or_None)] needing attention.

    A pin that is current produces no row. A lookup that failed produces a row
    with latest None -- absence of an answer is not an answer, the same rule
    unchecked_bases() exists for.
    """
    rows = []
    for b in built:
        for module, pinned in b.plugins:
            repo = plugin_repo(module)
            latest = lookup(repo) if repo else None
            if latest is None or latest.rsplit("/", 1)[-1] != pinned:
                rows.append((b.stack, b.priority, module, pinned, latest))
    return sorted(rows, key=lambda r: (r[1] not in ALERTING, r[0], r[2]))


# Jellyfin outside Docker: an apt package in its own LXC on pve (see
# $SITE_DIR/docs/superpowers/plans/2026-09-18-pve-migration.md). Cup reads images, so it
# cannot see it; Jellyfin reports its own version without auth instead.
# While stacks/jellyfin exists the running version is its pinned tag, and the
# LXC is not probed -- it does not exist yet, and would alert daily.
# The LXC's address is JELLYFIN_HOST in the repo-root .env, as for Caddy.
JELLYFIN_INFO = "http://{}:8096/System/Info/Public"
JELLYFIN_RELEASE = "https://api.github.com/repos/jellyfin/jellyfin/releases/latest"


def version_tuple(text):
    """`v12.1` / `10.11.11` -> (12, 1, 0) / (10, 11, 11), or None.

    Padded to three parts: the release tag and the installed version do not
    promise the same number of components, and (12, 1) < (12, 1, 0) as tuples.
    """
    found = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    if not found:
        return None
    parts = [int(p) for p in found.group(1).split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def fetch_json_field(url, field, timeout=TIMEOUT):
    """One field of a JSON document at url, or None if it cannot be read."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json",
                      "User-Agent": "homelab-check-updates"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get(field)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError,
            AttributeError):
        return None


def native_jellyfin_behind(installed, latest):
    """None if the installed version is current, else (installed, latest).

    Either side unreadable is a row, never a pass -- the rule the rest of this
    file keeps. Installed AHEAD of the latest release (a hotfix build) is fine.
    """
    have, want = version_tuple(installed), version_tuple(latest)
    if have is not None and want is not None and have >= want:
        return None
    return installed, latest


def self_test():
    # Native Jellyfin: numeric, padded, and unreadable is never current.
    assert version_tuple("v12.1") == (12, 1, 0)
    assert version_tuple("10.11.11") == (10, 11, 11)
    assert version_tuple("12.1.0-rc1") is None
    assert native_jellyfin_behind("12.1.0", "v12.1") is None
    assert native_jellyfin_behind("12.2.0", "v12.1") is None
    assert native_jellyfin_behind("10.11.11", "v12.1") == ("10.11.11", "v12.1")
    assert native_jellyfin_behind("10.9.0", "v10.11.0") == ("10.9.0", "v10.11.0")
    assert native_jellyfin_behind(None, "v12.1") == (None, "v12.1")
    assert native_jellyfin_behind("12.1.0", None) == ("12.1.0", None)

    priorities = {
        "jellyfin/jellyfin:10.11.11": [("jellyfin", "internet")],
        "grocy:1": [("grocy", "lan")],
        "broken:1": [("x", "internet")],
        # The shared-image case: walked lan-first, so a "last one wins" map
        # would file this as not worth alerting on.
        "postgres:17": [("wger", "lan"), ("authentik", "internet")],
    }
    updates = {
        "jellyfin/jellyfin:10.11.11": True,
        "grocy:1": True,
        "broken:1": None,
        "unlisted:1": True,
        "postgres:17": True,
    }
    alerts, others, unknown = problems_for(updates, priorities)
    assert sorted(r[0] for r in alerts) == [
        "jellyfin/jellyfin:10.11.11", "postgres:17"], alerts
    # lan and an image no stack claims are both non-alerting.
    assert sorted(r[0] for r in others) == ["grocy:1", "unlisted:1"], others
    # A lookup error must never be read as "up to date".
    assert [r[0] for r in unknown] == ["broken:1"], unknown

    # The guard that matters: an image at internet patch priority with no
    # update pending must produce nothing at all.
    quiet, _, _ = problems_for({"jellyfin/jellyfin:10.11.11": False}, priorities)
    assert quiet == [], quiet

    # base_image(): the FINAL stage wins, and a stage name is not an image.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Dockerfile").write_text(
            "# FROM commented:1 must not count\n"
            "FROM caddy:2.11.4-builder AS builder\n"
            "RUN xcaddy build\n"
            "FROM caddy:2.11.4\n"
            "COPY --from=builder /usr/bin/caddy /usr/bin/caddy\n")
        assert base_image({"context": tmp}) == "caddy:2.11.4"

        (Path(tmp) / "final-stage").write_text(
            "FROM alpine:3.20 AS base\nFROM base\n")
        # `FROM base` is a stage, so the last real image stands.
        assert base_image(
            {"context": tmp, "dockerfile": "final-stage"}) == "alpine:3.20"
    # A Dockerfile that is not there is None, not a crash -- one stack must not
    # blind the check for the rest.
    assert base_image({"context": "/nonexistent"}) is None

    # A base image Cup never mentioned is an alert, not a pass. This is the
    # `docker image prune -a` case: the leftover is gone and the check would
    # otherwise go quiet while still printing "clean".
    built = [Built("caddy", "internet", "homelab/caddy-crowdsec:2.11.4",
                   "caddy:2.11.4", []),
             Built("other", "lan", "homelab/other:1", "alpine:3.20", [])]
    assert unchecked_bases(built, {"caddy:2.11.4": False}) == [
        Built("other", "lan", "homelab/other:1", "alpine:3.20", [])]
    # Highest priority first, so the line that matters is the one read.
    assert [b.stack for b in unchecked_bases(built, {})] == ["caddy", "other"]
    assert unchecked_bases(built, {"caddy:2.11.4": False,
                                   "alpine:3.20": True}) == []
    # plugin_pins(): the two xcaddy pins, and nothing else on the line.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Dockerfile").write_text(
            "FROM caddy:2.11.4-builder AS builder\n"
            "RUN xcaddy build \\\n"
            "    --with github.com/hslatman/caddy-crowdsec-bouncer/http@v0.14.1 \\\n"
            "    --with github.com/caddy-dns/porkbun@v0.3.1\n"
            "FROM caddy:2.11.4\n")
        assert plugin_pins({"context": tmp}) == [
            ("github.com/hslatman/caddy-crowdsec-bouncer/http", "v0.14.1"),
            ("github.com/caddy-dns/porkbun", "v0.3.1")]
    # A Dockerfile with no xcaddy line, and one that is not there at all.
    assert plugin_pins({"context": "/nonexistent"}) == []

    # plugin_repo(): the subpackage is dropped, tags live on the repo.
    assert plugin_repo(
        "github.com/hslatman/caddy-crowdsec-bouncer/http") == \
        "hslatman/caddy-crowdsec-bouncer"
    assert plugin_repo("github.com/caddy-dns/porkbun") == "caddy-dns/porkbun"
    assert plugin_repo("github.com/lonely") is None

    # alert_priority(): the override wins, exposure is the default, and a
    # stack with neither is "unknown" rather than quietly safe.
    assert alert_priority({"exposure": "lan",
                           "patch_priority": "internet"}) == ("internet", None)
    assert alert_priority({"exposure": "lan"}) == ("lan", None)
    # Blank and null are PRESENT and invalid, not absent. Both fell through to
    # `exposure` while the first version of this fix was on disk.
    for empty in ("", None):
        value, error = alert_priority({"exposure": "lan",
                                       "patch_priority": empty})
        assert value is None and "patch_priority" in error, empty
    # Neither key is an error too: `exposure` is required, so its absence
    # means the block is broken, and "unknown" would be the same silent
    # non-alerting in a different costume.
    value, error = alert_priority({})
    assert value is None and "neither" in error
    # A typo is an error, not a priority. This is the fail-open that shipped
    # on 2026-09-06: "interent" is not in ALERTING, so the stack left the
    # alert set silently and the run exited 0.
    value, error = alert_priority({"exposure": "lan",
                                   "patch_priority": "interent"})
    assert value is None and "patch_priority" in error and "interent" in error
    # The same applies to a misspelled exposure, which names the right key.
    value, error = alert_priority({"exposure": "lann"})
    assert value is None and "exposure" in error
    # The real authentik stack, which is the reason the key exists: LAN-only
    # reachability, internet-tier patching. If this line ever reads "lan" the
    # identity provider has stopped being alerted on.
    authentik = compose.stack_config(compose.ROOT / "stacks/authentik",
                                     declared_only=True)
    assert authentik is not None, "authentik will not render"
    assert alert_priority(authentik["x-homelab"]) == ("internet", None)
    assert authentik["x-homelab"]["exposure"] == "lan"
    # Every stack in the repo has a readable priority, so the check below
    # cannot be passing merely because nothing reaches it.
    _, _, bad = images_by_priority()
    assert bad == [], bad

    # outdated_plugins(): current is silent, behind is a row, and a failed
    # lookup is a row with no answer rather than a pass.
    caddy = [Built("caddy", "internet", "homelab/caddy-crowdsec:2.11.4",
                   "caddy:2.11.4",
                   [("github.com/caddy-dns/porkbun", "v0.3.1")])]
    assert outdated_plugins(caddy, lambda repo: "v0.3.1") == []
    assert outdated_plugins(caddy, lambda repo: "v0.4.0") == [
        ("caddy", "internet", "github.com/caddy-dns/porkbun", "v0.3.1",
         "v0.4.0")]
    assert outdated_plugins(caddy, lambda repo: None) == [
        ("caddy", "internet", "github.com/caddy-dns/porkbun", "v0.3.1", None)]
    # A subpath tag (`http/v0.14.1`) is the same version as the pin `v0.14.1`.
    sub = [Built("caddy", "internet", "i", "b",
                 [("github.com/o/r/http", "v0.14.1")])]
    assert outdated_plugins(sub, lambda repo: "http/v0.14.1") == []

    # The comparison is numeric. As strings, "v0.9.2" > "v0.14.1", which would
    # report the newest release as behind an older one.
    assert max([((0, 14, 1), "v0.14.1"), ((0, 9, 2), "v0.9.2")])[1] == "v0.14.1"

    print("self-test passed", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=CUP_URL, help="Cup JSON endpoint")
    parser.add_argument("--all", action="store_true",
                        help="also list updates below internet patch priority")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    # Local and offline, so do it before the network call: the base images it
    # finds are what cup_images() must be told to keep and to drop.
    priorities, built, bad_metadata = images_by_priority()

    # Before the network call, and exit 1 rather than 2: the unit sets
    # SuccessExitStatus=2 for "could not reach Cup", so a 2 here would be
    # reported as a successful run. A stack whose priority cannot be read is
    # a stack that is not being checked.
    if bad_metadata:
        print("Cannot tell how urgent these stacks' updates are:",
              file=sys.stderr)
        for line in bad_metadata:
            print(f"  {line}", file=sys.stderr)
        print("\n  Fix the x-homelab block. An unreadable priority is not a "
              "low one --\n  this exits 1 rather than quietly filing the "
              "stack as not worth alerting.", file=sys.stderr)
        sys.exit(1)

    try:
        updates = cup_images(
            args.url,
            keep={base for _, _, _, base, _ in built},
            drop={image for _, _, image, _, _ in built if image},
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"could not reach Cup at {args.url}: {exc}", file=sys.stderr)
        print("  is the cup stack up?  docker ps --filter name=cup",
              file=sys.stderr)
        sys.exit(2)

    if not updates:
        print("Cup reported no in-use images -- that is not 'up to date', "
              "it is 'did not check'", file=sys.stderr)
        sys.exit(2)

    alerts, others, unknown = problems_for(updates, priorities)
    unchecked = unchecked_bases(built, updates)
    stale_plugins = outdated_plugins(built)
    # Checked against GitHub even while Jellyfin is a Docker image: Cup missed
    # 12.0 and 12.1 entirely (2026-09), because Jellyfin moved from three-part
    # tags to two-part ones and Cup only compares tags of the same shape.
    stack = compose.ROOT / "stacks" / "jellyfin" / "compose.yaml"
    in_docker = stack.exists()
    if in_docker:
        pinned = re.search(r"image:\s*jellyfin/jellyfin:(\S+)", stack.read_text())
        installed = pinned.group(1) if pinned else None
    else:
        host = compose.shared_env("JELLYFIN_HOST")
        info = JELLYFIN_INFO.format(host) if host else "JELLYFIN_HOST (unset in .env)"
        installed = fetch_json_field(info, "Version") if host else None
    native = native_jellyfin_behind(
        installed, fetch_json_field(JELLYFIN_RELEASE, "tag_name"))

    if native:
        installed, latest = native
        where = ("stacks/jellyfin/compose.yaml" if in_docker else info)
        print(f"Jellyfin is behind or unreadable: running "
              f"{installed or 'unknown -- ' + where + ' did not say'}, "
              f"latest {latest or 'unknown -- GitHub did not answer'}.",
              file=sys.stderr)
        if in_docker:
            print("  Read the release notes -- a major version migrates the "
                  "database -- then bump\n  the tag in "
                  "stacks/jellyfin/compose.yaml as for any image below.",
                  file=sys.stderr)
        else:
            print("  Read the release notes, then follow \"The version is "
                  "held\" in\n  stacks/jellyfin/README.md: vzdump first (the "
                  "bind mount rules out\n  pct snapshot), and on a major "
                  "version swap the LDAP plugin too.", file=sys.stderr)

    for reference, stack, _ in others if args.all else []:
        print(f"  (not alerting) {stack}: {reference}", file=sys.stderr)

    for reference, stack, priority in unknown:
        print(f"  unknown, registry lookup failed: {stack}: {reference}"
              f"{' (internet patch priority -- exits 1)' if priority in ALERTING else ''}",
              file=sys.stderr)

    if unchecked:
        print("Base images nothing checked -- the build leftover is gone:",
              file=sys.stderr)
        for b in unchecked:
            print(f"  {b.stack} ({b.priority}): {b.image} builds FROM {b.base}, "
                  f"which Cup did not report", file=sys.stderr)
        print("\n  Cup only sees images present in this host's image store.\n"
              "  Pull the base back, rebuild on it, and have Cup rescan:",
              file=sys.stderr)
        for b in unchecked:
            print(f"       docker pull {b.base}", file=sys.stderr)
        print("       cd /opt/homelab/stacks/<stack>\n"
              "       docker compose build --pull <service> && docker compose "
              f"up -d <service>\n{CUP_REFRESH}", file=sys.stderr)

    if stale_plugins:
        print("Caddy plugin pins that are behind or unreadable:",
              file=sys.stderr)
        for stack, priority, module, pinned, latest in stale_plugins:
            where = f"{latest} available" if latest else "could not read tags"
            print(f"  {stack} ({priority}): {module} pinned {pinned} -- "
                  f"{where}", file=sys.stderr)
        print("\n  These are `RUN xcaddy build --with` arguments, so nothing "
              "else sees them:\n"
              "  not Cup, which reads images, and not Dependabot, which reads "
              "manifests.\n"
              "  Bump the version in stacks/<stack>/Dockerfile -- both the "
              "--with line and\n"
              "  the matching homelab.plugin.* LABEL -- then rebuild and "
              "confirm it took:\n"
              "       docker compose build --pull caddy && docker compose up "
              "-d caddy\n"
              "       docker exec caddy caddy build-info | grep -E "
              "'crowdsec|porkbun'\n"
              "  A plugin that failed to register still yields a working "
              "caddy binary,\n  just without the module -- so the build "
              "succeeding is not the check.",
              file=sys.stderr)

    if alerts:
        # "at internet patch priority", not "internet-facing". authentik is
        # the reason: it is `exposure: lan` and would be misreported by the
        # old wording -- which was the whole point of separating the two.
        print("Images at internet patch priority are behind:",
              file=sys.stderr)
        for reference, stack, _ in alerts:
            print(f"  {stack}: {reference}", file=sys.stderr)
        print("\n  Read the release notes before pulling -- these are the "
              "stacks family reaches,\n  and a broken one is discovered by "
              "them. Then on the server:\n"
              "       cd /opt/homelab/stacks/<stack>\n"
              "       edit compose.yaml to the new tag, commit here, git pull "
              "there\n"
              "       docker compose up -d\n"
              "  Tags are pinned in this repo on purpose; pulling :latest "
              "would leave no\n  record of what is actually running.",
              file=sys.stderr)

        # The built stacks need different instructions, and giving them the
        # pull-and-restart ones would be worse than saying nothing: `up -d`
        # rebuilds nothing, so the alert would clear on the next run while the
        # old binary kept serving.
        behind = {reference for reference, _, _ in alerts}
        for stack, _, image, base, _ in built:
            if base in behind:
                print(f"\n  {stack} is BUILT, not pulled: {image} builds FROM "
                      f"{base}.\n"
                      f"  Editing compose.yaml alone changes nothing. For a "
                      f"new tag, bump every FROM in\n"
                      f"  stacks/{stack}/Dockerfile and the `image:` tag "
                      f"together. For the same tag\n"
                      f"  rebuilt upstream, edit nothing. Either way:\n"
                      f"       docker pull {base}   # the NEW tag, if you bumped it\n"
                      f"       docker compose build --pull && docker compose "
                      f"up -d\n{CUP_REFRESH}", file=sys.stderr)
        sys.exit(1)

    # `unchecked` holds Built records, so read the field by name -- the
    # NamedTuple exists so this cannot break silently when a field moves.
    if any(b.priority in ALERTING for b in unchecked):
        sys.exit(1)

    # A failed lookup is not "up to date". Printing it and then saying clean
    # would hide exactly the image this check exists for.
    if any(priority in ALERTING for _, _, priority in unknown):
        sys.exit(1)

    if any(priority in ALERTING
           for _, priority, _, _, _ in stale_plugins):
        sys.exit(1)

    # Jellyfin is internet patch priority wherever it runs.
    if native:
        sys.exit(1)

    print(f"clean: {len(updates)} images checked, "
          f"{sum(len(p) for *_, p in built)} plugin pins checked, "
          f"nothing at internet patch priority is behind", file=sys.stderr)


if __name__ == "__main__":
    main()
