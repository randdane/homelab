# /// script
# requires-python = ">=3.11"
# ///
"""Check headscale's advertised DERP address still matches reality.

    uv run scripts/check_derp.py
    uv run scripts/check_derp.py --self-test

`ipv4` in stacks/headscale/config/derp/custom.yaml is this house's public IP,
written literally. **It moved there on 2026-09-22.** It used to be
`derp.server.ipv4` in config.yaml, which now feeds only the auto-generated
DERP region -- and that region is disabled, so the old value is dead config.
This script read the dead one for a few hours after that change: a check
that passes by inspecting a setting with no effect.

Pinning the address at all is optional upstream. It exists so a client can
reach the relay when DNS is unavailable, which is the case that matters
because this host advertises an exit node: if the tunnel drops while a client
routes DNS through it, resolving the hostname to reconnect is circular.

A residential IP changes. When it does, nothing breaks loudly: the control
plane keeps working over the duckdns name, `headscale nodes list` stays green,
and only the DNS-less fallback path is dead -- exactly when you need it. See
docs/lessons-learned.md section 14.

This also compares the duckdns record, because a stored token that has gone
dead returns KO while the container still reports healthy.

And it asks whether the control plane is actually serving. Address drift and
liveness are different failures, and checking only the former is how headscale
crash-looped for 45 hours while this script reported `clean` every hour --
`derp.server.ipv4`, the public IP and the duckdns record all still matched
each other perfectly while nothing was answering. See
$SITE_DIR/docs/incident_reports/2026-08-25-headscale-crashloop.md and lessons-learned
section 1.

Anything that looks wrong is re-checked once after RETRY_SECONDS before it
alerts, because a run soon after a boot can land while Caddy and headscale
are still starting.

It also asks whether peers can actually connect directly. Until 2026-09-22 no
peer on this tailnet ever did -- every packet relayed through DERP, for
months, while every service worked and every monitor stayed green. The cause
was the DERP server answering STUN with a private address because it sits on
the same LAN as the nodes asking. That failure has no symptom you would ever
notice except latency, so it needs a check of its own; `tailscale netcheck`
reporting a *private* GlobalV4 is the signature. See stacks/headscale/README.md.

Exit codes: 0 clean, 1 drift, dead control plane or relaying, 2 could not
check (no network).
"""

import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from compose import ROOT, shared_env

CONFIG = ROOT / "stacks" / "headscale" / "config" / "config.yaml"
# The LIVE derp map. config.yaml's derp.server.ipv4 is dead -- see the
# module docstring.
DERPMAP = ROOT / "stacks" / "headscale" / "config" / "derp" / "custom.yaml"

# From the repo-wide .env -- see .env.example. Read at import so a missing
# value is one message at startup rather than a confusing None halfway down.
# There is deliberately no fallback literal: a default here would be the
# public hostname written into the repo, which is the thing keeping it in
# .env avoids.
DUCKDNS_HOST = shared_env("DUCKDNS_HOST")

# headscale needs the same name as a URL, and Compose cannot derive one from
# the other -- see .env.example. Written twice means they can disagree, and a
# server_url pointing somewhere else re-registers every node against a URL
# that does not work. Checking it here costs nothing: this script already
# runs hourly and already exists to catch headscale pointing at the wrong
# place.
HEADSCALE_SERVER_URL = shared_env("HEADSCALE_SERVER_URL")


def missing_config(duckdns_host, server_url):
    """Which repo-wide values this check needs and does not have.

    Both are required, and an earlier version required only DUCKDNS_HOST. That
    left the worst case green: with HEADSCALE_SERVER_URL unset, headscale has
    no server_url and will not start, while this monitor sailed past the drift
    check -- unset compared equal to everything -- and reported clean. The
    monitor whose whole job is noticing headscale points somewhere wrong was
    quietest exactly when headscale pointed nowhere at all.
    """
    return [name for name, value in
            (("DUCKDNS_HOST", duckdns_host),
             ("HEADSCALE_SERVER_URL", server_url)) if not value]


def server_url_problem(server_url, duckdns_host):
    """Whether HEADSCALE_SERVER_URL names a different host than DUCKDNS_HOST.

    Absence is missing_config()'s business, not this function's -- it answers
    only "do these two disagree", and two values it does not have cannot.
    """
    if not server_url or not duckdns_host:
        return None
    host = urllib.parse.urlsplit(server_url).hostname
    if host is None:
        return (f"HEADSCALE_SERVER_URL={server_url!r} has no hostname -- it "
                f"needs a scheme, e.g. https://{duckdns_host}")
    if host != duckdns_host:
        return (f"HEADSCALE_SERVER_URL points at {host}, but DUCKDNS_HOST is "
                f"{duckdns_host}. They name the same host in two forms and "
                f"have drifted; every tailnet node uses the server_url one.")
    return None
# Measured: this host reaches graphical.target 2m18s after power-on, and
# the stack settles shortly after. One minute is enough to clear the boot
# race without making a real outage wait meaningfully longer.
RETRY_SECONDS = 60


class NetcheckUnavailable(Exception):
    """netcheck could not be run or did not answer -- not a verdict."""


def derpmap_ipv4(text):
    """Read the relay node's pinned `ipv4` from the derp map.

    Narrow on purpose, like the config.yaml reader it replaces: it matches an
    `ipv4:` list-item key and ignores commented lines, rather than parsing the
    document. The file holds exactly one -- only the relay node is pinned to an
    address; the stunonly node is a hostname.
    """
    for line in text.splitlines():
        if line.split("#", 1)[0].strip().startswith("ipv4:"):
            m = re.match(r"^\s*ipv4:\s*([0-9.]+)\s*$", line.split("#", 1)[0])
            if m:
                return m.group(1)
    return None


def derpmap_hostname_problem(text, duckdns_host):
    """Whether the rendered derp map names the host it is supposed to.

    custom.yaml is generated from custom.yaml.in by scripts/render_derp_map.py
    because the hostname must not be committed (test_identity_leak). Two ways
    that goes wrong, both silent:

      - the template was edited and never re-rendered, so the map still names
        the previous hostname and every client fails to dial the relay;
      - the render ran without DUCKDNS_HOST and left `@DUCKDNS_HOST@` behind,
        which headscale parses happily as a hostname that does not resolve.

    Either way headscale starts, the control plane answers, and only the relay
    is dead -- so it needs asserting rather than assuming.
    """
    hosts = re.findall(r'(?m)^\s*hostname:\s*"?([^"\s#]+)"?\s*$', text)
    if not hosts:
        return "no hostname in the derp map at all"
    if "@DUCKDNS_HOST@" in text:
        return ("the derp map still contains @DUCKDNS_HOST@ -- it was never "
                "rendered. Run: uv run scripts/render_derp_map.py")
    if duckdns_host not in hosts:
        return (f"the derp map names {hosts!r} but DUCKDNS_HOST is "
                f"{duckdns_host} -- the template was changed and never "
                f"re-rendered. Run: uv run scripts/render_derp_map.py")
    return None


def netcheck_global_v4(timeout=60):
    """This node's reflexive address per `tailscale netcheck`, as "ip:port".

    Raises NetcheckUnavailable rather than returning None on failure, so a
    missing binary or a broken JSON contract can never be mistaken for a pass.
    The JSON is explicitly documented upstream as unstable.
    """
    try:
        out = subprocess.run(["tailscale", "netcheck", "--format=json"],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetcheckUnavailable(str(exc)) from exc
    if out.returncode != 0:
        raise NetcheckUnavailable(f"exit {out.returncode}: {out.stderr.strip()[:200]}")
    # A warning banner and log lines precede the object on stdout.
    brace = out.stdout.find("{")
    if brace < 0:
        raise NetcheckUnavailable("no JSON object in netcheck output")
    try:
        report = json.loads(out.stdout[brace:])
    except json.JSONDecodeError as exc:
        raise NetcheckUnavailable(f"netcheck JSON did not parse: {exc}") from exc
    if "GlobalV4" not in report:
        raise NetcheckUnavailable("netcheck JSON has no GlobalV4 field -- the "
                                  "upstream format is documented as unstable "
                                  "and may have changed")
    return report["GlobalV4"] or ""


def relaying_problem(global_v4, configured):
    """Whether this node has learned a reflexive address peers can reach it at.

    The failure being caught: the embedded DERP sits on the same LAN as the
    nodes it measures, so its STUN answer is a private address. Peers then try
    to hole-punch at something unroutable and relay everything instead, which
    is invisible -- the tunnel works, just slowly and through the house.

    A PRIVATE answer is the unambiguous signature, and is checked first
    because it is never legitimate: the observed wrong answers were
    the gateway (hairpin SNAT) and 10.201.7.1 (the docker bridge).

    It is then compared against the PINNED address from the derp map rather
    than against a freshly fetched public IP. That is deliberate: fetching the
    public IP twice can return two different addresses on a connection whose
    egress rotates, which is a false alarm with no defect behind it. Measured
    2026-09-22 -- one host reported 198.51.100.248 via STUN and
    198.51.100.254 via HTTPS seconds apart. The pinned value is a constant
    within a run, and `problems_for` separately checks it against the public
    IP, so all three still have to agree.
    """
    if not global_v4:
        return ("tailscale netcheck learned no public address at all "
                "(GlobalV4 empty) -- every peer will relay through DERP")
    ip = global_v4.rsplit(":", 1)[0]
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return f"tailscale netcheck reported an unparseable address {ip!r}"
    # `is_global` rather than `is_private`: CGNAT (100.64.0.0/10) is neither
    # private nor global in Python's classification, and a tailnet address
    # turning up here would be just as unreachable to a peer. Verified on
    # 3.12 -- a 100.64/10 address is private=False, global=False.
    if not parsed.is_global:
        return (f"tailscale netcheck reports this node's public address as "
                f"{ip}, which is NOT routable -- the DERP map is answering "
                f"STUN from inside the LAN, so peers hole-punch at an "
                f"unreachable address and relay everything through DERP. "
                f"Check stacks/headscale/config/derp/custom.yaml is present "
                f"and loaded; see docs/recovery.md 6b.")
    if ip != configured:
        return (f"tailscale netcheck observes {ip} but the derp map pins "
                f"{configured} -- peers are told an address this node is not "
                f"actually seen at, and will fall back to relaying.")
    return None


def public_ip(timeout=10):
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r"[0-9.]{7,15}", ip):
                return ip
        except Exception:
            continue
    return None


def resolved_ip(host):
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def health_ok(body):
    """Whether headscale's /health body reports a pass.

    The body is checked, not just the status code. A 200 proves Caddy answered;
    this vhost is a reverse proxy, and proving the thing behind it is alive is
    the entire point (lessons-learned section 1). Headscale answers:

        {"status":"pass"}
    """
    try:
        return json.loads(body).get("status") == "pass"
    except (json.JSONDecodeError, AttributeError):
        return False


def control_plane_problem(host, timeout=10):
    """Probe the control plane, returning a problem string or None.

    Deliberately NOT /key: that endpoint is version-negotiated and answers
    `400 unsupported client version` to anything it does not recognise, so it
    would need a client version kept in sync with headscale forever. Verified
    against v0.29.3: `/key?v=110` -> 400. /health is version-independent.

    The public URL is used rather than the container, so this exercises the
    path a real client takes -- DNS, the router, Caddy, then headscale.
    Confirmed the router hairpins correctly, so this works from the first server itself.
    """
    url = f"https://{host}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode()
            if r.status != 200:
                return f"{url} returned HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        # 502 specifically means Caddy answered and its upstream did not --
        # the exact signature of this outage. Other codes mean something else
        # and must not carry that hint into an alert.
        hint = " -- Caddy is up, headscale is not" if exc.code == 502 else ""
        return f"{url} returned HTTP {exc.code}{hint}"
    except Exception as exc:
        return f"{url} unreachable: {exc}"

    if not health_ok(body):
        return f"{url} answered but did not report healthy: {body.strip()[:120]}"
    return None


def problems_for(configured, actual, global_v4):
    """Every way the DERP address or the control plane can be wrong, right now.

    One function so the retry in __main__ re-runs the whole check rather than
    just the probe that happened to fail first: DNS resolution is as racy at
    boot as the health probe is.
    """
    problems = []

    # Liveness first: it is the failure that actually locks you out, and a
    # dead control plane makes the address comparison academic.
    dead = control_plane_problem(DUCKDNS_HOST)
    if dead:
        problems.append(dead)

    if configured != actual:
        problems.append(f"derp map ipv4 is {configured}, public IP is {actual}")

    relaying = relaying_problem(global_v4, configured)
    if relaying:
        problems.append(relaying)

    dns = resolved_ip(DUCKDNS_HOST)
    if dns is None:
        problems.append(f"{DUCKDNS_HOST} does not resolve")
    elif dns != actual:
        problems.append(f"{DUCKDNS_HOST} resolves to {dns}, public IP is {actual}")

    return problems


def self_test():
    derpmap = """regions:
  999:
    regionid: 999
    nodes:
      - name: "999"
        hostname: example.duckdns.org
        ipv4: 1.2.3.4
        stunport: -1
      - name: 999stun
        hostname: stun.l.google.com
        stunonly: true
"""
    assert derpmap_ipv4(derpmap) == "1.2.3.4", derpmap_ipv4(derpmap)
    # A commented-out address is not a pinned one.
    assert derpmap_ipv4("      # ipv4: 9.9.9.9\n") is None
    assert derpmap_ipv4("regions:\n  999:\n    regionid: 999\n") is None
    assert derpmap_ipv4("") is None
    # A trailing comment on a real line must not break the match.
    assert derpmap_ipv4("        ipv4: 5.6.7.8  # the relay\n") == "5.6.7.8"

    # --- the relaying check, against the addresses actually observed ---
    # Clean: netcheck agrees with the pinned address. A routable address on
    # purpose: 203.0.113.0/24 is documentation space, which is not global.
    assert relaying_problem("5.6.7.8:41641", "5.6.7.8") is None
    # The live defect, 2026-09-22: the router's hairpin SNAT.
    assert "NOT routable" in relaying_problem("10.0.0.1:43721", "203.0.113.7")
    # The other observed wrong answer: the docker bridge gateway.
    assert "NOT routable" in relaying_problem("10.201.7.1:58656", "203.0.113.7")
    # CGNAT and loopback are equally unreachable by a peer.
    assert "NOT routable" in relaying_problem("100.101.0.2:1", "203.0.113.7")
    assert "NOT routable" in relaying_problem("127.0.0.1:1", "203.0.113.7")
    # A public address that disagrees with the pin is still wrong, but is
    # reported as disagreement rather than as a private answer.
    dis = relaying_problem("1.2.3.4:41641", "203.0.113.7")
    assert "observes 1.2.3.4" in dis and "NOT routable" not in dis, dis
    # No reflexive address learned at all.
    assert "GlobalV4 empty" in relaying_problem("", "203.0.113.7")
    assert "GlobalV4 empty" in relaying_problem(None, "203.0.113.7")
    assert "unparseable" in relaying_problem("not-an-ip:41641", "203.0.113.7")

    # --- the rendered derp map names the right host ---
    good = '      - name: "999"\n        hostname: "h.example"\n'
    assert derpmap_hostname_problem(good, "h.example") is None
    assert "never rendered" in derpmap_hostname_problem(
        '        hostname: "@DUCKDNS_HOST@"\n', "h.example")
    assert "never re-rendered" in derpmap_hostname_problem(
        '        hostname: "old.example"\n', "h.example")
    assert "no hostname" in derpmap_hostname_problem("regions:\n", "h.example")
    # The stunonly node's hostname must not be mistaken for the relay's.
    both = ('        hostname: "h.example"\n'
            '        hostname: stun.l.google.com\n')
    assert derpmap_hostname_problem(both, "h.example") is None

    assert health_ok('{"status":"pass"}')
    assert not health_ok('{"status":"fail"}')
    assert not health_ok("")            # empty body
    assert not health_ok("<html>502</html>")  # a proxy error page, not JSON

    agree = server_url_problem("https://a.example.org", "a.example.org")
    assert agree is None, agree
    assert "drifted" in server_url_problem("https://b.example.org", "a.example.org")
    # A bare hostname parses with hostname None -- the missing scheme is the
    # defect, and headscale rejects it too.
    assert "needs a scheme" in server_url_problem("a.example.org", "a.example.org")
    # Port and path are not part of the identity; the host is.
    assert server_url_problem("https://a.example.org:443/x", "a.example.org") is None
    # Either side unset is not this check's business.
    assert server_url_problem(None, "a.example.org") is None
    assert server_url_problem("https://a.example.org", None) is None

    # Absence is a configuration failure, not a clean run. The regression this
    # guards: HEADSCALE_SERVER_URL unset used to sail through untouched.
    assert missing_config("a.example.org", "https://a.example.org") == []
    assert missing_config("a.example.org", None) == ["HEADSCALE_SERVER_URL"]
    assert missing_config(None, "https://a.example.org") == ["DUCKDNS_HOST"]
    assert missing_config(None, None) == ["DUCKDNS_HOST", "HEADSCALE_SERVER_URL"]
    assert missing_config("a.example.org", "") == ["HEADSCALE_SERVER_URL"]

    # netcheck_global_v4 must never turn a broken contract into a pass.
    import unittest.mock as _mock
    for stdout, rc in (("not json at all", 0), ("", 0), ('{"UDP":true}', 0)):
        with _mock.patch("subprocess.run",
                         return_value=_mock.Mock(returncode=rc, stdout=stdout, stderr="")):
            try:
                netcheck_global_v4()
            except NetcheckUnavailable:
                pass
            else:
                raise AssertionError(f"accepted bad netcheck output: {stdout!r}")
    with _mock.patch("subprocess.run", side_effect=FileNotFoundError("tailscale")):
        try:
            netcheck_global_v4()
        except NetcheckUnavailable:
            pass
        else:
            raise AssertionError("accepted a missing tailscale binary")
    with _mock.patch("subprocess.run", return_value=_mock.Mock(
            returncode=0, stdout='# warning\nlog line\n{"GlobalV4":"9.9.9.9:1"}', stderr="")):
        assert netcheck_global_v4() == "9.9.9.9:1"

    print("self-test: OK", file=sys.stderr)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        sys.exit(0)

    # Exit 1, not the "could not check" 2: 2 is a SuccessExitStatus in the
    # unit, so an unset name would leave this timer reporting success forever
    # while checking nothing. A monitor that has quietly stopped monitoring is
    # indistinguishable from a healthy one -- the failure this whole script
    # exists to prevent, one level up.
    absent = missing_config(DUCKDNS_HOST, HEADSCALE_SERVER_URL)
    if absent:
        print(f"{', '.join(absent)} not set in the repo-wide .env -- "
              f"CHECK NOT RUN. Copy .env.example to .env and fill it in.",
              file=sys.stderr)
        sys.exit(1)

    drift = server_url_problem(HEADSCALE_SERVER_URL, DUCKDNS_HOST)
    if drift:
        print(drift, file=sys.stderr)
        sys.exit(1)

    if not DERPMAP.exists():
        # Not "nothing pinned" -- the whole derp map is gone, which means
        # headscale is serving the auto-generated region again and every peer
        # is relaying. Exit 1: this is the failure, not a skipped check.
        print(f"{DERPMAP.relative_to(ROOT)} is MISSING -- the rendered DERP "
              "map is what keeps peers connecting directly, and it is NOT in "
              "git (it carries DUCKDNS_HOST). Regenerate it:\n"
              "       uv run scripts/render_derp_map.py\n"
              "       cd stacks/headscale && docker compose up -d --force-recreate\n"
              "  See docs/recovery.md 6b.", file=sys.stderr)
        sys.exit(1)

    derpmap_text = DERPMAP.read_text()
    stale = derpmap_hostname_problem(derpmap_text, DUCKDNS_HOST)
    if stale:
        print(stale, file=sys.stderr)
        sys.exit(1)

    configured = derpmap_ipv4(derpmap_text)
    if configured is None:
        print(f"no ipv4 pinned in {DERPMAP.relative_to(ROOT)} -- "
              "clients fall back to the hostname.", file=sys.stderr)
        sys.exit(0)

    actual = public_ip()
    if actual is None:
        print("could not determine the public IP (offline?) -- CHECK SKIPPED, "
              "not passed.", file=sys.stderr)
        sys.exit(2)

    try:
        global_v4 = netcheck_global_v4()
    except NetcheckUnavailable as exc:
        print(f"tailscale netcheck unavailable ({exc}) -- CHECK SKIPPED, "
              "not passed.", file=sys.stderr)
        sys.exit(2)

    problems = problems_for(configured, actual, global_v4)

    # One retry before alerting. A run shortly after a boot can land while
    # Caddy and headscale are still coming up -- on 2026-08-29 that read as
    # "unreachable: <urlopen error timed out>" and paged as address drift.
    # A real outage is still caught: it is simply reported a minute later.
    if problems:
        print(f"problems found, re-checking in {RETRY_SECONDS}s before "
              "alerting (the stack may still be starting):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        time.sleep(RETRY_SECONDS)
        try:
            global_v4 = netcheck_global_v4()
        except NetcheckUnavailable as exc:
            print(f"tailscale netcheck unavailable on retry ({exc}) -- "
                  "CHECK SKIPPED, not passed.", file=sys.stderr)
            sys.exit(2)
        problems = problems_for(configured, actual, global_v4)

    if problems:
        print("DERP problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(f"\n  drift fix: edit {DERPMAP.relative_to(ROOT)}, then on the server\n"
              "       cd /opt/homelab/stacks/headscale && docker compose up -d --force-recreate\n"
          "       (a plain `restart` re-reads nothing -- the config is a bind mount)\n"
              "  dead-control-plane fix: check whether the container is crash-looping\n"
              "       docker ps -a --filter name=headscale --format '{{.Status}}'\n"
              "       docker logs headscale --tail 30\n"
              "     A restart will NOT fix a stale mount table -- that needs\n"
              "       docker compose up -d --force-recreate   (lessons-learned 23)\n"
              "  note: restarting headscale cuts every session routed through the tailnet.\n"
              "        Do it from the LAN.", file=sys.stderr)
        sys.exit(1)

    print(f"clean: control plane healthy, derp ipv4 {configured} matches "
          f"public IP and {DUCKDNS_HOST}, netcheck sees {global_v4} "
          f"(peers can connect directly)", file=sys.stderr)
