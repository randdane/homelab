"""No deployment identity may be written literally into a config file.

`PUBLIC_DOMAIN`, `DUCKDNS_HOST` and `HEADSCALE_SERVER_URL` live in the
repo-root `.env` (docs/conventions.md). On 2026-09-04 they were moved out of
seven config files; nothing then stopped the eighth from reintroducing one,
and a hostname pasted into a Caddyfile works perfectly, so no test failure and
no runtime error ever announces it.

Scanned: every tracked file EXCEPT `*.md`. Documentation names these hostnames
on purpose -- an operational command you can paste is worth more than a
placeholder, and docs are not what gets deployed. Do not "fix" that by
widening this test; if the repo is ever published, the docs are a separate
decision made once, whereas config is edited continuously and needs a guard.
"""
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from compose import ROOT, shared_env_file

IDENTITY_VARS = ("PUBLIC_DOMAIN", "DUCKDNS_HOST", "HEADSCALE_SERVER_URL")


def hostname_of(value):
    """The bare host in a value that may or may not be a URL.

    `urlsplit().hostname` strips scheme, credentials, port, path and query,
    and lowercases. The earlier `split("://")[-1].strip("/")` did none of
    that past the scheme: `https://host.example:8443/path` came out as
    `host.example:8443/path`, which appears in no config file and so matched
    nothing. A check that cannot fail is worse than no check.

    A bare hostname has no scheme, and `urlsplit` then parses it as a *path*
    with `hostname` None -- hence the conditional rather than an unconditional
    call. DUCKDNS_HOST and PUBLIC_DOMAIN are bare by design.
    """
    if "://" not in value:
        return value.strip().strip("/") or None
    return urllib.parse.urlsplit(value).hostname


def identity_values():
    """Every literal that must not appear, or {} when nothing is configured.

    Reads the FILE and the process environment and searches for both. They can
    differ -- shared_env() lets an override win -- and if this searched only
    the winner, exporting DUCKDNS_HOST would hide the value the containers are
    actually deployed with. The union cannot be masked from either side.
    """
    values = {}
    for name in IDENTITY_VARS:
        hosts = {hostname_of(v) for v in
                 (shared_env_file(name), os.environ.get(name)) if v and v.strip()}
        hosts.discard(None)
        if hosts:
            values[name] = sorted(hosts)
    return values


def _short(path):
    """Repo-relative when it can be; the plain path otherwise, so a synthetic
    file from a tmp_path in this file's own tests does not raise instead of
    reporting."""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def scanned_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.split("\0")
            if p and not p.endswith(".md")]


def all_tracked_files():
    """Every tracked file, docs included. The two bans below use this: site
    addresses and personal words belong in no tracked file at all, and the
    docs were scrubbed for publication on 2026-09-30. The PUBLIC_DOMAIN test
    above keeps its docs exemption; its docstring explains why."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.split("\0") if p]


@pytest.mark.parametrize("var", IDENTITY_VARS)
def test_no_identity_literal_in_config(var):
    values = identity_values()
    if var not in values:
        pytest.skip(
            f"{var} not set in {ROOT / '.env'} -- nothing to search for. "
            f"This is why host-setup.md provisions the root .env on EVERY "
            f"host, laptop included: without it this guard is inert.")
    needles = values[var]
    hits = []
    for path in scanned_files():
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable; not somewhere a hostname hides
        for n, line in enumerate(text.splitlines(), 1):
            for needle in needles:
                if needle in line:
                    hits.append(f"{_short(path)}:{n} ({needle})")
    assert not hits, (
        f"{var}'s value appears literally in {len(hits)} config location(s): "
        f"{hits}. It belongs in the repo-root .env and reaches config through "
        f"`env_file` or shared_env() -- see docs/conventions.md. Docs are "
        f"exempt and are not scanned; config is not.")


def test_the_scan_actually_reaches_config_files():
    """A parametrized test over real files passes vacuously if the file list
    breaks -- and this one would then report success over an empty scan."""
    names = {p.name for p in scanned_files()}
    assert "Caddyfile" in names, "extensionless files must be scanned"
    assert "compose.yaml" in names
    assert not any(p.suffix == ".md" for p in scanned_files())
    assert len(scanned_files()) > 100, "the scan surface collapsed"


def test_a_planted_literal_would_be_caught(tmp_path, monkeypatch):
    """The check above passes today. Prove it can fail."""
    fake = tmp_path / "compose.yaml"
    fake.write_text("  environment:\n    - URL=https://example.invalid/x\n")
    monkeypatch.setattr(sys.modules[__name__], "scanned_files", lambda: [fake])
    # A LIST, because that is what identity_values() returns -- one entry per
    # distinct hostname across the file and the environment. A bare string
    # here still made the test pass, but for the wrong reason: the scanner
    # iterates its needles, so it searched for the CHARACTERS of
    # "example.invalid" and matched almost any line containing a vowel.
    monkeypatch.setattr(sys.modules[__name__], "identity_values",
                        lambda: {"PUBLIC_DOMAIN": ["example.invalid"]})
    with pytest.raises(AssertionError, match="appears literally"):
        test_no_identity_literal_in_config("PUBLIC_DOMAIN")


@pytest.mark.parametrize("value, expected", [
    ("https://host.example.org/", "host.example.org"),
    ("https://host.example.org", "host.example.org"),
    # The four the old string-splitting got wrong. Each produced a needle
    # containing a port, path or credentials, which matches nothing in any
    # config file -- so the scan passed while the hostname sat there.
    ("https://host.example.org:8443/path", "host.example.org"),
    ("http://user:pw@host.example.org/a?b=1", "host.example.org"),
    ("https://host.example.org:443", "host.example.org"),
    ("https://host.example.org/a/b/c", "host.example.org"),
    # Bare hostnames have no scheme; urlsplit alone returns None for these.
    ("host.example.org", "host.example.org"),
    ("example.com", "example.com"),
])
def test_hostname_of_reduces_to_the_bare_host(value, expected):
    assert hostname_of(value) == expected


def test_identity_values_searches_file_and_environment(monkeypatch):
    """An exported override must not hide the value the containers use."""
    # Clear the real ones first: this asserts on the whole dict, and CI
    # supplies all three through the environment. Without this the test
    # passes locally and fails only in CI, which is the worst combination.
    for name in IDENTITY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys.modules[__name__], "shared_env_file",
                        lambda n: "deployed.example.org" if n == "DUCKDNS_HOST" else None)
    monkeypatch.setenv("DUCKDNS_HOST", "override.example.org")
    assert identity_values() == {
        "DUCKDNS_HOST": ["deployed.example.org", "override.example.org"]}


# --- Site addresses: banned from tracked config -----------------------------
#
# LAN and tailnet host addresses belong to a site, not to this repo. They go
# in a .env, or in the SITE_DIR checkout (site.example/ shows the layout).
# This was a shrinking allowlist until 2026-09-30, when it reached zero.
# Network ranges (`/24`, `/10`) are generic and pass.
SITE_ADDRESS = re.compile(
    r"\b(?:192\.168|100\.64)\.\d{1,3}\.\d{1,3}(?![\d/])")


def files_with_site_addresses(paths):
    found = set()
    for path in paths:
        try:
            if SITE_ADDRESS.search(path.read_text()):
                found.add(str(_short(path)))
        except (OSError, UnicodeDecodeError):
            continue
    return found


def test_no_site_address_in_config():
    # Minus this file: its pattern tests below are made of example addresses.
    found = sorted(files_with_site_addresses(
        p for p in all_tracked_files() if p != Path(__file__).resolve()))
    assert not found, (
        f"site LAN/tailnet address written into {found}. Put it in a .env "
        f"(docs/conventions.md) or the SITE_DIR checkout (site.example/).")


@pytest.mark.parametrize("text, hit", [
    ("upstream 192.168.1.78:8096", True),
    ("KNOWN=100.64.0.2", True),
    ("allow 192.168.1.0/24", False),
    ("ts range 100.64.0.0/10", False),
    ("resolver 100.100.100.100", False),
    ("docker 10.201.7.1", False),
])
def test_site_address_pattern(text, hit):
    assert bool(SITE_ADDRESS.search(text)) is hit


# --- Personal words: banned, needles kept out of the repo --------------------
#
# Handles, the tailnet domain, the Headscale user, an email -- anything that
# names this owner. They cannot be written here without being the leak, so
# they are read from `.site-words` at the repo root (untracked, one per line,
# `#` comments) or, for CI, the comma-separated SITE_WORDS variable. Not the
# root .env: `env_file` injects every key of that into containers.
SITE_WORDS_FILE = ROOT / ".site-words"


def site_words():
    words = set(filter(None, (w.strip() for w in
                              os.environ.get("SITE_WORDS", "").split(","))))
    try:
        for line in SITE_WORDS_FILE.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                words.add(line.strip())
    except OSError:
        pass
    return words


def test_no_site_word_in_config():
    words = site_words()
    if not words:
        pytest.skip(f"no {SITE_WORDS_FILE.name} and no SITE_WORDS -- nothing "
                    f"to search for, so this guard is inert.")
    found = []
    for path in all_tracked_files():
        try:
            text = path.read_text().lower()
        except (OSError, UnicodeDecodeError):
            continue
        if any(w.lower() in text for w in words):
            found.append(str(_short(path)))
    # Paths only: printing the matched word would put it in CI logs.
    assert not found, (f"a personal word from {SITE_WORDS_FILE.name} appears "
                       f"in {sorted(found)}. Move it to a .env or the SITE_DIR "
                       f"checkout (site.example/).")


def test_site_words_reads_file_and_environment(tmp_path, monkeypatch):
    f = tmp_path / "words"
    f.write_text("# comment\nalpha\n\n  beta  \n")
    monkeypatch.setattr(sys.modules[__name__], "SITE_WORDS_FILE", f)
    monkeypatch.setenv("SITE_WORDS", "gamma, ,delta")
    assert site_words() == {"alpha", "beta", "gamma", "delta"}
