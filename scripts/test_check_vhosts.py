# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_check_vhosts.py -v

Tests the parsing and classification, not Docker. The pure functions decide
whether an alert fires; the subprocess plumbing is exercised by running the
script for real.
"""

from check_vhosts import CADDYFILE, caddy_hosts, site_blocks

WILDCARD_AND_SITE = """\
{$SITE_ADDRESS} {
\tlog
\treverse_proxy headscale:8080
}

jellyfin.{$DUCKDNS_HOST} {
\tcrowdsec
\treverse_proxy jellyfin:8096
}

*.{$PUBLIC_DOMAIN} {
\t@jellyfin host jellyfin.{$PUBLIC_DOMAIN}
\thandle @jellyfin {
\t\treverse_proxy jellyfin:8096
\t}

\t@vikunja {
\t\thost vikunja.{$PUBLIC_DOMAIN}
\t\tremote_ip 100.64.0.0/10 192.168.1.0/24
\t}
\thandle @vikunja {
\t\treverse_proxy life-queue-app:3456
\t}

\thandle {
\t\tabort
\t}
}

http://name.example {
\t@allowed remote_ip 100.64.0.0/10
\thandle @allowed {
\t\treverse_proxy life-queue-app:3456
\t}
\thandle {
\t\tabort
\t}
}
"""


def test_site_blocks_finds_every_top_level_block():
    """Four blocks, and the wildcard's nested matchers must not be mistaken
    for top-level blocks of their own."""
    headers = [h for h, _ in site_blocks(WILDCARD_AND_SITE)]
    assert headers == [
        "{$SITE_ADDRESS}",
        "jellyfin.{$DUCKDNS_HOST}",
        "*.{$PUBLIC_DOMAIN}",
        "http://name.example",
    ]


def test_a_snippet_is_not_a_site():
    """`(name) { ... }` is reusable config, not an address Caddy answers
    for. Counting it as a site flags it as an ungated, undeclared host."""
    text = "(authentik_forward_auth) {\n\tforward_auth x:9000\n}\n" + WILDCARD_AND_SITE
    headers = [h for h, _ in site_blocks(text)]
    assert "(authentik_forward_auth)" not in headers
    assert "(authentik_forward_auth)" not in caddy_hosts(text)


def test_host_matchers_are_discovered_with_their_gate_state():
    """The braced matcher carries remote_ip; the one-line matcher does not."""
    hosts = caddy_hosts(WILDCARD_AND_SITE)
    assert hosts["vikunja.{$PUBLIC_DOMAIN}"] is True
    assert hosts["jellyfin.{$PUBLIC_DOMAIN}"] is False


def test_site_addresses_are_discovered_too():
    """The form that has no `host` directive at all. Parsing only matchers
    misses a gated whole-site block entirely, and misses it silently."""
    hosts = caddy_hosts(WILDCARD_AND_SITE)
    assert hosts["name.example"] is True          # scheme stripped, gated
    assert hosts["{$SITE_ADDRESS}"] is False
    assert hosts["jellyfin.{$DUCKDNS_HOST}"] is False


def test_the_wildcard_container_is_not_itself_a_host():
    """Its matchers are the targets. Treating it as a host would make every
    conceivable subdomain look served."""
    assert "*.{$PUBLIC_DOMAIN}" not in caddy_hosts(WILDCARD_AND_SITE)


from check_vhosts import (classification_problems, phone_coverage_problem,
                          wildcard_fallback_problem)

PUBLIC = {"{$SITE_ADDRESS}": "control plane", "jellyfin.{$PUBLIC_DOMAIN}": "family",
          "jellyfin.{$DUCKDNS_HOST}": "legacy name"}


def test_a_gate_deleted_from_a_host_matcher_is_a_failure():
    """The fail-open this whole check exists for: delete remote_ip from
    @vikunja and the task list is served to anyone sending the Host header."""
    hosts = caddy_hosts(WILDCARD_AND_SITE.replace(
        "\t\tremote_ip 100.64.0.0/10 192.168.1.0/24\n", ""))
    assert any("vikunja" in p for p in classification_problems(hosts, PUBLIC))


def test_a_gate_deleted_from_a_site_address_is_a_failure():
    """The other form. Only one of the two was parsed in the first draft."""
    hosts = caddy_hosts(WILDCARD_AND_SITE.replace(
        "\t@allowed remote_ip 100.64.0.0/10\n", ""))
    assert any("name.example" in p for p in classification_problems(hosts, PUBLIC))


def test_a_new_ungated_undeclared_host_fails_closed():
    """Forgetting to declare a genuinely public host is a failure, not a pass.
    The alternative fails open, which is the wrong direction to be lenient."""
    hosts = {"new.{$PUBLIC_DOMAIN}": False}
    assert classification_problems(hosts, PUBLIC)


def test_declared_public_hosts_pass_without_a_gate():
    assert classification_problems(caddy_hosts(WILDCARD_AND_SITE), PUBLIC) == []


def test_declaring_one_jellyfin_does_not_exempt_the_other():
    """jellyfin exists as a matcher under one domain and its own site block
    under another. A key of "jellyfin" would exempt both while appearing to
    exempt one."""
    only_one = {"{$SITE_ADDRESS}": "x", "jellyfin.{$PUBLIC_DOMAIN}": "x"}
    problems = classification_problems(caddy_hosts(WILDCARD_AND_SITE), only_one)
    assert any("jellyfin.{$DUCKDNS_HOST}" in p for p in problems)


def test_removing_the_wildcard_abort_is_a_failure():
    """That fallback is what makes an unmatched name safe. Replace it with a
    reverse_proxy and every conceivable subdomain is served."""
    assert wildcard_fallback_problem(WILDCARD_AND_SITE) is None
    broken = WILDCARD_AND_SITE.replace("\thandle {\n\t\tabort\n\t}",
                                       "\thandle {\n\t\treverse_proxy x:80\n\t}", 1)
    assert wildcard_fallback_problem(broken) is not None


def test_phone_coverage_notices_a_gated_host_it_does_not_probe():
    """Tasker cannot read this repo, so a fifth gated host would pass
    classification and be silently absent from the only positive check."""
    hosts = {"a.{$PUBLIC_DOMAIN}": True, "b.{$PUBLIC_DOMAIN}": True}
    assert phone_coverage_problem(hosts, ("a.{$PUBLIC_DOMAIN}",), {}) is not None
    assert phone_coverage_problem(hosts, ("a.{$PUBLIC_DOMAIN}",
                                          "b.{$PUBLIC_DOMAIN}"), {}) is None


def test_a_declared_unprobed_host_is_not_a_gap_but_must_still_exist():
    hosts = {"a.{$PUBLIC_DOMAIN}": True, "b.{$PUBLIC_DOMAIN}": True}
    declared = {"b.{$PUBLIC_DOMAIN}": "admin UI"}
    assert phone_coverage_problem(hosts, ("a.{$PUBLIC_DOMAIN}",), declared) is None
    # A stale exemption for a host Caddy no longer serves is drift too.
    gone = {"a.{$PUBLIC_DOMAIN}": True}
    assert phone_coverage_problem(gone, ("a.{$PUBLIC_DOMAIN}",), declared) is not None
    # Listed in both is a contradiction, not coverage.
    assert phone_coverage_problem(hosts, ("a.{$PUBLIC_DOMAIN}", "b.{$PUBLIC_DOMAIN}"),
                                  declared) is not None


from check_vhosts import negative_verdict


def test_a_refused_gated_vhost_passes():
    """Measured 2026-09-09: `handle { abort }` over HTTP/2 gives curl 92."""
    assert negative_verdict(0, 92)[0] == "pass"


def test_a_gated_vhost_that_answers_is_fail_open():
    """The allowlist admitted a bridge address. This is the finding, not a
    warning: someone widened the gate, or removed it."""
    assert negative_verdict(0, 0)[0] == "fail"


def test_a_failed_control_is_inconclusive_not_a_pass():
    """A refusal and an inability to reach Caddy look identical from outside.
    Without the control, a broken container-to-Caddy path would read as a
    perfectly working gate."""
    assert negative_verdict(7, 7)[0] == "inconclusive"
    assert negative_verdict(7, 92)[0] == "inconclusive"


def test_an_unexpected_transport_error_is_inconclusive():
    """Reachable Caddy, but the gated host failed in a way that is not the
    abort we expect. Do not call that a working gate."""
    verdict, detail = negative_verdict(0, 35)
    assert verdict == "inconclusive" and "35" in detail


def test_the_real_caddyfile_is_clean():
    """The spec's pre-deploy half. The fixture tests prove the logic; this
    proves it against the file actually deployed, using the module's own
    PUBLIC_HOSTS and PHONE_PROBES defaults so those are guarded too."""
    text = CADDYFILE.read_text()
    hosts = caddy_hosts(text)
    assert classification_problems(hosts) == []
    assert wildcard_fallback_problem(text) is None
    assert phone_coverage_problem(hosts) is None


TWO_NAME_MATCHER_ONE_LINE = """\
*.{$PUBLIC_DOMAIN} {
\t@both host a.example b.example
\thandle @both {
\t\treverse_proxy a:80
\t}
\thandle {
\t\tabort
\t}
}
"""

TWO_NAME_MATCHER_BRACED = """\
*.{$PUBLIC_DOMAIN} {
\t@both {
\t\thost a.example b.example
\t\tremote_ip 100.64.0.0/10
\t}
\thandle @both {
\t\treverse_proxy a:80
\t}
\thandle {
\t\tabort
\t}
}
"""


def test_a_two_name_one_line_matcher_yields_both_hosts():
    """Caddy allows `@name host a b`. Capturing only the first name would
    silently drop the second -- ungated here, so a served host with no gate
    and a green check, the exact failure this file exists to catch."""
    hosts = caddy_hosts(TWO_NAME_MATCHER_ONE_LINE)
    assert hosts == {"a.example": False, "b.example": False}


def test_a_two_name_braced_matcher_yields_both_hosts():
    hosts = caddy_hosts(TWO_NAME_MATCHER_BRACED)
    assert hosts == {"a.example": True, "b.example": True}


def test_a_hyphenated_matcher_name_is_read_with_its_gate():
    """`@it-tools` is a valid Caddy matcher. Matching names with `\\w+` alone
    skipped the block entirely, so a gated host read as missing."""
    text = TWO_NAME_MATCHER_BRACED.replace("@both", "@it-tools")
    assert caddy_hosts(text) == {"a.example": True, "b.example": True}
