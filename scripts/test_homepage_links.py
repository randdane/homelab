# /// script
# requires-python = ">=3.11"
# ///
"""Every `homepage.href` label points somewhere a viewer can actually reach.

The dashboard is the one place a wrong URL is invisible: a tile renders
identically whether its link works or not, and nobody clicks all forty. On
2026-09-05 thirty of them pointed at `http://localhost:<port>` -- the viewer's
own machine -- because `homepage.href` interpolates `${HOMELAB_HOST:-localhost}`
from each stack's OWN .env, and only four of thirty-four stacks had ever set
it. Nothing failed. The links had been dead for as long as the repo had been
off the laptop.

So this checks the two shapes an href is allowed to have:

  http://${HOMELAB_HOST:-localhost}:<port>[/path]   a published port
  https://<name>.${PUBLIC_DOMAIN:?msg}[/path]       a Caddy vhost

and, for the first shape, that <port> is a port the stack really publishes --
which is the half that rots silently when a port moves. For the second, that
Caddy actually answers to that name: the shape check alone accepted
`https://typo.${PUBLIC_DOMAIN}`, and the wildcard site block does not save it.
A name with no matcher falls through to `handle { abort }` and the connection
closes with no response, which reads as a network fault rather than a typo.

It deliberately does NOT check that HOMELAB_HOST is set. That is deploy state,
not repo state: this file must pass on a laptop with no .env at all, the same
reason docs/ports.md is rendered with `declared_only`. What it guarantees is
that setting HOMELAB_HOST once is SUFFICIENT -- that no href can be wrong for
any other reason.
"""

import re

from compose import ROOT, stack_config, stack_dirs

# `(.+?)`, not `(\S+)`: a `${VAR:?message}` guard contains spaces, and the
# non-space form silently matched NOTHING on such a line -- dropping the stack
# out of the scan entirely rather than complaining. The whole-repo count below
# is what stops that class of miss from being invisible again.
HREF = re.compile(r"^\s*homepage\.href:\s*(.+?)\s*$", re.M)

# The literal templates, matched before interpolation. Reading the raw file
# rather than the rendered config is the point: rendering resolves
# ${HOMELAB_HOST:-localhost} to `localhost`, which is exactly the bug, and a
# host that HAS set it would render a URL this repo cannot check.
VIA_HOST = re.compile(r"^http://\$\{HOMELAB_HOST:-localhost\}:(?:\$\{\w+:-)?(\d+)\}?(?:/\S*)?$")
# `${PUBLIC_DOMAIN:?message}` and nothing else. The guard is REQUIRED, not
# preferred: unset, a bare ${PUBLIC_DOMAIN} renders the valid-looking
# `https://kuma.` on a tile that looks identical to a working one, which is
# the failure this whole file exists for. docs/conventions.md has said the
# guard is mandatory since 2026-09-06; this pattern accepted the bare form
# anyway until 2026-09-07, so reverting a label passed the checker that was
# supposed to prevent it.
#
# `:-default` is rejected for the same reason -- a default IS the silent
# fallback, wearing the syntax of a guard.
VIA_VHOST = re.compile(
    r"^https://([a-z0-9-]+)\.\$\{PUBLIC_DOMAIN:\?[^}]+\}(?:/\S*)?$")


CADDYFILE = ROOT / "stacks/caddy/config/Caddyfile"

# Matches both spellings the file uses: the one-line `@jellyfin host x.{$...}`
# and the `host x.{$...}` line inside a braced matcher block.
CADDY_HOST = re.compile(r"^\s*(?:@\w+\s+)?host\s+([a-z0-9-]+)\.\{\$PUBLIC_DOMAIN\}\s*$",
                        re.M)


def caddy_vhosts(text=None):
    """Subdomains under PUBLIC_DOMAIN that Caddy has a host matcher for.

    Deliberately the MATCHERS and not the `*.{$PUBLIC_DOMAIN}` site address.
    The wildcard answers to every name in the domain, so reading it would make
    every conceivable subdomain look served -- while in fact anything without
    a matcher reaches `handle { abort }`.
    """
    return set(CADDY_HOST.findall(
        text if text is not None else CADDYFILE.read_text()))


def published_ports(config):
    return {
        int(p["published"])
        for service in (config.get("services") or {}).values()
        for p in service.get("ports", [])
        if p.get("published") is not None
    }


def problems(stack_dir):
    """Every complaint about one stack, as human-readable strings."""
    text = (stack_dir / "compose.yaml").read_text()
    hrefs = HREF.findall(text)
    if not hrefs:
        return []
    config = stack_config(stack_dir, declared_only=True)
    if config is None:
        return []          # stack_config already printed a SKIP line
    ports = published_ports(config)
    found = []
    vhosts = caddy_vhosts()
    for href in hrefs:
        if (m := VIA_VHOST.match(href)):
            if m.group(1) not in vhosts:
                found.append(f"{stack_dir.name}: {href!r} names a vhost Caddy "
                             f"has no host matcher for (it serves "
                             f"{sorted(vhosts)}). The wildcard block will not "
                             f"save it -- an unmatched name is aborted.")
            continue
        via_host = VIA_HOST.match(href)
        if not via_host:
            found.append(f"{stack_dir.name}: {href!r} matches neither allowed "
                         f"form (see this file's docstring)")
        elif int(via_host.group(1)) not in ports:
            found.append(f"{stack_dir.name}: {href!r} links to port "
                         f"{via_host.group(1)}, which this stack does not "
                         f"publish (it publishes {sorted(ports) or 'nothing'})")
    return found


def test_shapes():
    """The two allowed forms are accepted and everything else is not."""
    assert VIA_HOST.match("http://${HOMELAB_HOST:-localhost}:${PORT:-8096}")
    assert VIA_HOST.match("http://${HOMELAB_HOST:-localhost}:8091/admin")
    assert VIA_VHOST.match("https://kuma.${PUBLIC_DOMAIN:?set it in .env}")
    assert VIA_VHOST.match("https://vikunja.${PUBLIC_DOMAIN:?set it}/projects")
    # The bare form is NOT accepted. Unset it renders `https://kuma.`, and a
    # checker that tolerates it cannot stop the label being reverted.
    assert not VIA_VHOST.match("https://kuma.${PUBLIC_DOMAIN}")
    # A default defeats the guard: unset, this renders `https://kuma.example`.
    assert not VIA_VHOST.match("https://kuma.${PUBLIC_DOMAIN:-example}")
    # An empty message is not a guard anyone can act on.
    assert not VIA_VHOST.match("https://kuma.${PUBLIC_DOMAIN:?}")
    # The line the old `(\S+)` capture could not see at all.
    assert HREF.findall(
        "      homepage.href: https://kuma.${PUBLIC_DOMAIN:?set it}\n"
    ) == ["https://kuma.${PUBLIC_DOMAIN:?set it}"]
    # The bug this file exists for: a bare host, however plausible.
    assert not VIA_HOST.match("http://localhost:8010")
    assert not VIA_HOST.match("http://100.101.0.2:3004")
    # A hardcoded domain would pass a naive check and fail test_identity_leak.
    assert not VIA_VHOST.match("https://kuma.example.com")
    # http:// to a vhost loses TLS silently.
    assert not VIA_VHOST.match("http://kuma.${PUBLIC_DOMAIN}")


def test_caddy_vhosts_reads_matchers_not_the_wildcard():
    assert caddy_vhosts(
        "*.{$PUBLIC_DOMAIN} {\n"
        "\t@jellyfin host jellyfin.{$PUBLIC_DOMAIN}\n"
        "\t@kuma {\n"
        "\t\thost kuma.{$PUBLIC_DOMAIN}\n"
        "\t\tremote_ip 192.168.1.0/24\n"
        "\t}\n"
        "\thandle { abort }\n"
        "}\n"
    ) == {"jellyfin", "kuma"}


def test_the_real_caddyfile_still_parses():
    """This cross-check is worthless if the file stops matching the pattern --
    an empty vhost set would fail every href instead of passing vacuously,
    but the message would blame the labels."""
    assert len(caddy_vhosts()) >= 4, (
        f"only {caddy_vhosts()} parsed out of the Caddyfile -- the matcher "
        f"spelling changed and CADDY_HOST no longer reads it")


def test_port_check_reads_published_ports():
    assert published_ports({"services": {
        "a": {"ports": [{"published": "8096", "target": 8096}]},
        "b": {"ports": [{"target": 80}]},          # random host port
    }}) == {8096}


# Every stack that has a tile. Below this, something has stopped being seen --
# a label renamed, or a regex that no longer matches the line it is meant to.
# A scan that quietly examines nothing reports success just as loudly.
MIN_HREFS = 30


def test_every_href_in_the_repo_is_well_formed():
    """The whole-repo scan.

    This was a main() run by a bespoke check.sh step, alongside a hand-written
    `if __name__ == "__main__"` block listing the unit tests by name. That
    list had to be updated by hand for every test added, which is exactly how
    a test in test_status.py was deleted without anyone noticing. pytest
    collects; nobody has to remember.

    Needs a Docker daemon, because it renders each stack to read its published
    ports. So does test_status.py's repo-wide audit -- the suite as a whole
    is no longer runnable without one, and that is deliberate rather than
    accidental.
    """
    total = sum(len(HREF.findall((d / "compose.yaml").read_text()))
                for d in stack_dirs())
    assert total >= MIN_HREFS, (
        f"only {total} homepage.href labels found, expected at least "
        f"{MIN_HREFS} -- the scan is missing labels, not passing")

    found = [p for d in stack_dirs() for p in problems(d)]
    assert not found, "\n".join(found)
