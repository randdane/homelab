# /// script
# requires-python = ">=3.11"
# ///
"""Render headscale's DERP map from its template.

    uv run scripts/render_derp_map.py

`stacks/headscale/config/derp/custom.yaml.in` is tracked; the rendered
`custom.yaml` beside it is gitignored, because it carries DUCKDNS_HOST and
DERP_IPV4 (the public IP), site data that must not be committed, and a
deployment hostname must not be committed to a config file. See
docs/conventions.md and scripts/test_identity_leak.py -- that guard caught
this exact file on 2026-09-22.

Headscale reads the derp map as a plain YAML file and does no environment
expansion of its own, which is why this step exists at all. `server_url`
avoids it by being an env var headscale maps onto a config key; file contents
have no such route.

Re-run it after changing the template, DUCKDNS_HOST or DERP_IPV4, then
`docker compose up -d --force-recreate` -- a plain `up -d` re-reads nothing,
because the config is a bind mount.

Exit codes: 0 rendered, 1 could not.
"""

import sys
from pathlib import Path

from compose import ROOT, shared_env

TEMPLATE = ROOT / "stacks" / "headscale" / "config" / "derp" / "custom.yaml.in"
RENDERED = TEMPLATE.with_suffix("")          # .../custom.yaml
# Repo-wide .env variable -> the @NAME@ it fills. Both are site data: the
# relay's hostname and the house's public IPv4 it is pinned to.
VARIABLES = ("DUCKDNS_HOST", "DERP_IPV4")


def render(text, values):
    """Substitute every @NAME@ from `values`, refusing to leave one behind.

    An unsubstituted placeholder would be written into a file headscale then
    parses happily -- the region would simply name a host that does not exist,
    every client would fail to dial the relay, and nothing would say why. A
    render that silently half-worked is the failure this whole file exists to
    avoid, so it is an error rather than a warning.
    """
    out = text
    for name, value in values.items():
        out = out.replace(f"@{name}@", value)
    left = [n for n in VARIABLES if f"@{n}@" in out]
    if left:
        raise ValueError(f"still present after substitution: {left}")
    return out


def self_test():
    v = {"DUCKDNS_HOST": "h.example", "DERP_IPV4": "203.0.113.7"}
    assert render("hostname: \"@DUCKDNS_HOST@\"\nipv4: @DERP_IPV4@\n", v) == \
        "hostname: \"h.example\"\nipv4: 203.0.113.7\n"
    try:
        render("ipv4: @DERP_IPV4@\n", {"DUCKDNS_HOST": "h.example"})
    except ValueError:
        pass
    else:
        raise AssertionError("an unset placeholder must be an error")
    # No placeholder at all is not an error: a template may legitimately stop
    # needing one, and failing here would block a correct render.
    assert render("nothing to do\n", v) == "nothing to do\n"
    print("self-test: OK", file=sys.stderr)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        sys.exit(0)

    values = {name: shared_env(name) for name in VARIABLES}
    missing = [n for n, v in values.items() if not v]
    if missing:
        print(f"{', '.join(missing)} not set in the repo-wide .env -- cannot "
              f"render {RENDERED.relative_to(ROOT)}. Copy .env.example to .env "
              "and fill it in; see docs/host-setup.md.", file=sys.stderr)
        sys.exit(1)

    if not TEMPLATE.exists():
        print(f"{TEMPLATE.relative_to(ROOT)} is missing.", file=sys.stderr)
        sys.exit(1)

    RENDERED.write_text(render(TEMPLATE.read_text(), values))
    print(f"rendered {RENDERED.relative_to(ROOT)} for {values['DUCKDNS_HOST']}",
          file=sys.stderr)
