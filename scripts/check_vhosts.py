# /// script
# requires-python = ">=3.11"
# ///
"""Are the remote_ip-gated vhosts still private, and still reachable?

    uv run scripts/check_vhosts.py
    uv run scripts/check_vhosts.py --list

Three questions, which fail at different times:

  classification  Every host Caddy serves is either gated or declared public.
                  Catches a `remote_ip` line being deleted, which publishes
                  the vhost with nothing else to notice.

  negative probe  A caller that is NOT in the allowlist is refused. Catches
                  the allowlist being widened until it admits everything.

  phone coverage  PHONE_PROBES still matches the gated set, so the only
                  positive check there is has not silently stopped covering a
                  host. Tasker cannot read this repo.

There is deliberately NO positive probe here. homelab's own source address is
LOCAL, so Docker's LOCAL-source masquerade rewrites a self-probe and the
`homelab-tailnet-source` exemption is what makes it succeed -- a self-probe
would test that workaround rather than the path real clients take. See
$SITE_DIR/docs/devices.md and the spec.

Exit codes: 0 clean, 1 a real problem, 2 could not check.
"""

import argparse
import re
import subprocess
import sys
import time

from compose import ROOT, shared_env

RESOLVE_RETRY_S = 60

CADDYFILE = ROOT / "stacks/caddy/config/Caddyfile"


def _uncomment(line):
    return line.split("#", 1)[0]


def site_blocks(text):
    """Top-level `header { ... }` blocks as (header, body) pairs.

    Brace-counting rather than a regex: the wildcard block contains nested
    matcher and handle blocks, and a regex that stops at the first `}` would
    cut it in half.
    """
    out, depth, header, body = [], 0, None, []
    for raw in text.splitlines():
        line = _uncomment(raw)
        if depth == 0:
            if "{" in line:
                header = line.strip().rstrip("{").strip()
                body, depth = [], line.count("{") - line.count("}")
            continue
        depth += line.count("{") - line.count("}")
        if depth == 0:
            # Skip the global options block (no header) and snippets
            # (`(name)`): neither is a site Caddy answers for.
            if header and not header.startswith("("):
                out.append((header, "\n".join(body)))
        else:
            body.append(raw)
    return out


def matcher_hosts(body):
    """{host identity: gated} for the `@name` matchers inside a site block.

    Both spellings the Caddyfile uses: the one-line `@jellyfin host x` and the
    braced block that pairs `host` with `remote_ip`.
    """
    out, current, depth = {}, None, 0
    for raw in body.splitlines():
        line = _uncomment(raw)
        if current is None:
            if re.match(r"^\s*@[\w-]+\s*\{\s*$", line):
                current, depth = [], 1
            elif (m := re.match(r"^\s*@[\w-]+\s+host\s+(\S.*?)\s*$", line)):
                for host in m.group(1).split():
                    out[host] = False   # a matcher on one line carries no gate
            continue
        depth += line.count("{") - line.count("}")
        if depth == 0:
            chunk = "\n".join(current)
            gated = bool(re.search(r"^\s*remote_ip\s", chunk, re.M))
            for match in re.findall(r"^\s*host\s+(\S.*?)\s*$", chunk, re.M):
                for host in match.split():
                    out[host] = gated
            current = None
        else:
            current.append(line)
    return out


def caddy_hosts(text):
    """{host identity: gated} for every name Caddy will answer to.

    Discovery is deliberately independent of whether a gate is present. An
    earlier design derived the list FROM the gates, so deleting one removed
    the host from the list and the check went green -- backwards, and the
    exact fail-open this exists to catch.
    """
    out = {}
    for header, body in site_blocks(text):
        if header.startswith("*."):
            out.update(matcher_hosts(body))     # a container, not a target
            continue
        name = re.sub(r"^https?://", "", header)
        out[name] = bool(re.search(r"^\s*(?:@\w+\s+)?remote_ip\s", body, re.M))
    return out


# Hosts served with no remote_ip gate, on purpose. Keyed on FULL identity:
# jellyfin exists both as a matcher under PUBLIC_DOMAIN and as its own site
# block under DUCKDNS_HOST, and a key of "jellyfin" would exempt both while
# reading as though it exempted one.
#
# Anything discovered and not listed here must be gated. Default-deny, so
# forgetting to declare a public host fails closed rather than open.
PUBLIC_HOSTS = {
    "{$SITE_ADDRESS}":
        "headscale control plane and DERP relay -- the remote-access path to "
        "this host, and necessarily reachable from the internet",
    "jellyfin.{$DUCKDNS_HOST}":
        "legacy name; existing clients were set up against it and the "
        "wildcard certificate does not cover it",
    "jellyfin.{$PUBLIC_DOMAIN}":
        "family outside the house; uptime-kuma watches it three ways",
}

# What Tasker on the phone is expected to probe. This repo cannot configure
# Tasker, so the two drift silently unless something compares them.
PHONE_PROBES = (
    "authentik.{$PUBLIC_DOMAIN}",
    "forgejo.{$PUBLIC_DOMAIN}",
    "homepage.{$PUBLIC_DOMAIN}",
    "it-tools.{$PUBLIC_DOMAIN}",
    "karakeep.{$PUBLIC_DOMAIN}",
    "kuma.{$PUBLIC_DOMAIN}",
    "mealie.{$PUBLIC_DOMAIN}",
    "paperless.{$PUBLIC_DOMAIN}",
    "vikunja.{$PUBLIC_DOMAIN}",
)


def classification_problems(hosts, public=PUBLIC_HOSTS):
    """Hosts served with no gate that nobody declared public."""
    return [
        f"{host}: served with no remote_ip gate and not declared in "
        f"PUBLIC_HOSTS -- either restore the gate or record why it is public"
        for host, gated in sorted(hosts.items())
        if not gated and host not in public
    ]


def wildcard_fallback_problem(text):
    """Whether the wildcard block still closes with `handle { abort }`.

    Caddy runs the last unmatched `handle` as the fallback, so that block is
    what an unrecognised subdomain reaches. Replacing it with a reverse_proxy
    would serve every conceivable name -- the same fail-open class as deleting
    a gate, and just as silent.
    """
    for header, body in site_blocks(text):
        if not header.startswith("*."):
            continue
        # ponytail: `[^}]*` assumes the fallback body has no nested braces,
        # which holds for `abort`. Parse properly if it ever grows one.
        handles = re.findall(r"^\s*handle(\s+@\w+)?\s*\{([^}]*)\}", body, re.M)
        if not handles:
            return (f"{header}: no `handle` block at all -- an unmatched "
                    f"subdomain has no defined fallback")
        matcher, inner = handles[-1]
        if matcher.strip() or "abort" not in inner:
            return (f"{header}: the final handle is not a bare "
                    f"`handle {{ abort }}` -- an unmatched subdomain is now "
                    f"served rather than refused")
        return None
    return "no `*.` wildcard site block found in the Caddyfile"


# Gated hosts the phone deliberately does NOT probe, each with the reason.
# Listing a host here is a decision that nobody gets told when it breaks from
# off-LAN -- so it needs a reason, the same way PUBLIC_HOSTS does.
NOT_PHONE_PROBED = {
    "cup.{$PUBLIC_DOMAIN}":
        "admin UI, not used from the phone; check_updates.py reads Cup "
        "directly on homelab and fails loudly if it is gone",
    "duplicati.{$PUBLIC_DOMAIN}":
        "admin UI, not used from the phone; the nightly offsite job is "
        "watched by its own failure alerts, not by this page",
    "dozzle.{$PUBLIC_DOMAIN}":
        "admin UI, not used from the phone; losing it costs nothing that "
        "an ssh session does not already give",
}


def phone_coverage_problem(hosts, probes=PHONE_PROBES,
                           unprobed=NOT_PHONE_PROBED):
    """Whether PHONE_PROBES plus NOT_PHONE_PROBED still matches the gated set."""
    gated = {h for h, g in hosts.items() if g}
    missing = sorted(gated - set(probes) - set(unprobed))
    extra = sorted((set(probes) | set(unprobed)) - gated)
    both = sorted(set(probes) & set(unprobed))
    if both:
        missing += [f"{h} (in both PHONE_PROBES and NOT_PHONE_PROBED)" for h in both]
    if not missing and not extra:
        return None
    parts = []
    if missing:
        parts.append(f"gated but not probed by the phone: {', '.join(missing)}")
    if extra:
        parts.append(f"probed by the phone but not gated: {', '.join(extra)}")
    return ("PHONE_PROBES no longer matches the Caddyfile -- "
            + "; ".join(parts)
            + ". Update the Tasker task and PHONE_PROBES together.")


CONTAINER = "uptime-kuma"

# Measured 2026-09-09 from inside the container: `handle { abort }` over
# HTTP/2 surfaces as 92. 52 and 56 are the HTTP/1.1 spellings of the same
# thing, kept so a protocol change does not turn a working gate into a
# spurious failure.
ABORT_EXITS = frozenset({52, 56, 92})


def expand(identity):
    """`vikunja.{$PUBLIC_DOMAIN}` -> a real hostname, or None if unset.

    The tokens are what the Caddyfile holds, and what this repo may contain:
    test_identity_leak.py fails the build on a literal hostname in a script.
    """
    missing = False

    def sub(match):
        nonlocal missing
        value = shared_env(match.group(1))
        if not value:
            missing = True
            return match.group(0)
        return value

    expanded = re.sub(r"\{\$(\w+)\}", sub, identity)
    return None if missing else expanded


def _run(cmd, timeout=30):
    """Run a command with stdin closed.

    stdin=DEVNULL is not decoration: `docker exec` without it inherits a
    terminal and the check hangs rather than failing, and a monitoring script
    that hangs is worse than one that is wrong -- the timer piles runs up
    behind it.
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def probe(host, ip, container=CONTAINER, timeout=8):
    """curl `host` from inside the container, pinned to Caddy's bridge address.

    Returns curl's exit code. The container's source address is a bridge
    address -- neither LOCAL nor tailnet -- which is exactly the caller the
    allowlist must refuse.
    """
    return _run([
        "docker", "exec", container,
        "curl", "-sS", "-o", "/dev/null", "-m", str(timeout),
        "--resolve", f"{host}:443:{ip}", f"https://{host}/",
    ], timeout=timeout + 10).returncode


def caddy_bridge_ip(container=CONTAINER):
    """Caddy's address on the shared bridge, or None.

    Resolved from inside the container rather than hardcoded: it is assigned
    by Docker and changes when the network is recreated.
    """
    result = _run(["docker", "exec", container, "getent", "hosts", "caddy"])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0]


def negative_verdict(control_exit, host_exit, abort_exits=ABORT_EXITS):
    """Classify one gated host's probe into (verdict, detail).

    The control is the reachability test. Without it every failure mode --
    DNS, refused connection, TLS, a dead Caddy -- collapses into the same
    "no response" a working gate produces, and the check would report a
    healthy gate while proving nothing at all.
    """
    if control_exit != 0:
        return ("inconclusive",
                f"the ungated control request also failed (curl {control_exit}) "
                f"-- Caddy is unreachable from {CONTAINER}, so nothing can be "
                f"concluded about the gate")
    if host_exit == 0:
        return ("fail",
                "answered a request from the container bridge, which is not in "
                "the allowlist -- the gate is open")
    if host_exit in abort_exits:
        return ("pass", f"refused (curl {host_exit})")
    return ("inconclusive",
            f"unexpected curl exit {host_exit} while the control succeeded -- "
            f"not the abort a gate produces, so not evidence the gate works")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print what was discovered and exit 0")
    args = ap.parse_args()

    text = CADDYFILE.read_text()
    hosts = caddy_hosts(text)

    if args.list:
        for host, gated in sorted(hosts.items()):
            state = "gated" if gated else "public"
            note = "" if gated else f"  ({PUBLIC_HOSTS.get(host, 'UNDECLARED')})"
            print(f"  {state:<7} {host}{note}")
        return 0

    # --- static: no network, so these always run -----------------------
    problems = classification_problems(hosts)
    for check in (wildcard_fallback_problem(text), phone_coverage_problem(hosts)):
        if check:
            problems.append(check)

    # --- negative probe: needs Docker and a running Caddy ---------------
    unchecked = []
    try:
        bridge_ip = caddy_bridge_ip()
        if bridge_ip is None:
            # A catch-up backup after downtime stops the probe container for
            # ~25 s; one retry rides that out instead of alerting.
            time.sleep(RESOLVE_RETRY_S)
            bridge_ip = caddy_bridge_ip()
    except FileNotFoundError:
        bridge_ip = None
        unchecked.append("docker not found -- the negative probe did not run")
    except subprocess.TimeoutExpired:
        bridge_ip = None
        unchecked.append(f"docker exec into {CONTAINER} timed out")

    if bridge_ip is None and not unchecked:
        unchecked.append(
            f"could not resolve caddy from inside {CONTAINER} -- the container "
            f"is not running, or is not on the shared bridge")

    if bridge_ip:
        control_host = "jellyfin.{$PUBLIC_DOMAIN}"
        if control_host not in hosts:
            unchecked.append(
                f"{control_host} is no longer in the Caddyfile -- the control "
                f"host has moved or been renamed, so the negative probe did "
                f"not run")
        else:
            try:
                control = expand(control_host)
                if control is None:
                    unchecked.append("PUBLIC_DOMAIN is unset, so no control "
                                     "request could be built")
                else:
                    control_exit = probe(control, bridge_ip)
                    for host in sorted(h for h, gated in hosts.items() if gated):
                        name = expand(host)
                        if name is None:
                            unchecked.append(f"{host}: unset variable, cannot probe")
                            continue
                        verdict, detail = negative_verdict(
                            control_exit, probe(name, bridge_ip))
                        if verdict == "fail":
                            problems.append(f"{host}: {detail}")
                        elif verdict == "inconclusive":
                            unchecked.append(f"{host}: {detail}")
            except subprocess.TimeoutExpired:
                unchecked.append("a probe timed out -- the negative probe did "
                                 "not complete")

    if problems:
        print("vhost problems:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\n  A gate is the only thing making these names private -- the\n"
              "  names themselves are public and anyone can send the matching\n"
              "  Host header. See stacks/caddy/README.md.",
              file=sys.stderr)
        return 1

    if unchecked:
        # Exit 2, and this unit does NOT set SuccessExitStatus=2: "could not
        # determine whether the private vhosts are private" is worth waking
        # for. At 06:00, clear of boot and the backup window, it is rare.
        print("vhost checks could not complete -- NOT PASSED:", file=sys.stderr)
        for note in unchecked:
            print(f"  {note}", file=sys.stderr)
        return 2

    gated = sum(1 for g in hosts.values() if g)
    print(f"clean: {len(hosts)} host(s) served, {gated} gated and all refusing "
          f"the container bridge, {len(hosts) - gated} declared public",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
