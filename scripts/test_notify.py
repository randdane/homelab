# /// script
# requires-python = ">=3.11"
# ///
"""Tests for notify.py, which is what tells you anything else broke.

This script had a --self-test that nothing ran: not check.sh, not CI, only
the systemd unit and only with --unit. That is the worst place in the repo to
have untested code. Every timer here uses `OnFailure=homelab-failure-notify@`,
so if this is broken, a failed backup is marked failed and nothing else
happens -- which is indistinguishable from a backup with nothing to say. The
failure that matters is the silent one, and this is the thing that breaks the
silence.

The old self-test claimed to cover "parsing and truncation". It covered
parsing.
"""

import json
import urllib.error

import pytest

import notify


# ---------------------------------------------------------------- read_env

def test_read_env_survives_values_the_shell_would_mangle(tmp_path):
    """The reason this is hand-parsed rather than sourced.

    A token containing `$` expands under `source` and produces a credential
    that is subtly wrong rather than obviously missing -- so the alert fails
    at the moment it is needed, with a 401 that looks like a server problem.
    """
    path = tmp_path / ".env"
    path.write_text(
        '# comment\n'
        'NTFY_TOKEN="ab$6cd"\n'
        "NTFY_URL=http://x:2586\n"
        "SINGLE='qu$ted'\n"
        "  SPACED  =  value  \n"
        "\n"
        "bare\n"
        "EMPTY=\n"
        "URL_WITH_EQUALS=http://x/?a=b&c=d\n"
    )
    env = notify.read_env(path)
    assert env["NTFY_TOKEN"] == "ab$6cd"
    assert env["NTFY_URL"] == "http://x:2586"
    assert env["SINGLE"] == "qu$ted"
    assert env["SPACED"] == "value"
    # A line with no `=` is not a key. It is also not a crash.
    assert "bare" not in env
    assert env["EMPTY"] == ""
    # partition(), not split(): only the FIRST `=` separates.
    assert env["URL_WITH_EQUALS"] == "http://x/?a=b&c=d"


def test_read_env_missing_file_is_empty_not_an_exception():
    """main() turns {} into exit 2, "no usable credentials". A raise here
    would instead crash the OnFailure handler for the unit that just failed."""
    assert notify.read_env("/nonexistent/path") == {}


# ------------------------------------------------------------------- send

class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Capture the request send() would make, without a network."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_send_truncates_to_what_the_phone_will_show(sent):
    """An untruncated body loses the end on the phone -- which is where a
    traceback puts the actual error."""
    notify.send("http://ntfy:2586", "tok", "alerts", "t", "y" * 5000)
    message = sent["body"]["message"]
    assert len(message) == notify.MAX_MESSAGE
    assert message.endswith("..."), "truncation must be visible, not silent"

    notify.send("http://ntfy:2586", "tok", "alerts", "t", "short...")
    assert sent["body"]["message"] == "short..."


def test_send_posts_json_to_the_server_root(sent):
    notify.send("http://ntfy:2586/", "tok", "alerts", "title", "body")
    # The trailing slash is normalised, so no `//`.
    assert sent["url"] == "http://ntfy:2586/"
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert sent["body"] == {"topic": "alerts", "title": "title", "message": "body"}


def test_send_never_sends_tags(sent):
    """ntfy renders an emoji-named tag in front of the title, and the phone's
    digest watchdog matches the title from its first character."""
    notify.send("http://ntfy:2586", "tok", "alerts", "t", "m", priority=notify.HIGH)
    assert "tags" not in sent["body"]
    assert sent["body"]["priority"] == 4


def test_a_non_latin1_title_is_sent_rather_than_raising(sent):
    """The reason this is JSON and not a Title header: urllib encodes headers
    as Latin-1, so this title would raise UnicodeEncodeError before sending."""
    notify.send("http://ntfy:2586", "tok", "alerts", "🔔 disk – sdb", "m")
    assert sent["body"]["title"] == "🔔 disk – sdb"
    assert "Title" not in sent["headers"]


# ------------------------------------------------------------------- main

def test_an_http_error_never_echoes_the_response_body(monkeypatch, capsys,
                                                      tmp_path):
    """A 401 body can quote the request headers back, and those hold the
    bearer token. Printing it would write the credential into the journal."""
    env = tmp_path / ".env"
    env.write_text("NTFY_URL=http://ntfy:2586\nNTFY_TOKEN=SUPERSECRET\n"
                   "NTFY_TOPIC=alerts\n")
    monkeypatch.setenv("HOMELAB_NOTIFY_ENV", str(env))

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("sys.argv", ["notify.py", "--title", "t", "--message", "m"])

    assert notify.main() == 1
    out = capsys.readouterr()
    assert "401" in out.err
    assert "SUPERSECRET" not in out.err + out.out


def test_missing_credentials_exit_2_rather_than_pretending_to_send(
        monkeypatch, tmp_path):
    """Exit 2 is distinct from 1 on purpose: nothing was sent AND nothing was
    tried, which is a configuration problem rather than an outage."""
    empty = tmp_path / ".env"
    empty.write_text("NTFY_URL=http://ntfy:2586\n")   # token and topic missing
    monkeypatch.setenv("HOMELAB_NOTIFY_ENV", str(empty))
    monkeypatch.setattr("sys.argv", ["notify.py", "--title", "t", "--message", "m"])
    assert notify.main() == 2


def test_the_unit_form_is_high_priority(monkeypatch):
    """A failed unit is the alert that matters; a --title message is not."""
    calls = []
    monkeypatch.setattr(notify, "unit_report", lambda unit: ("u failed", "body"))
    monkeypatch.setattr(notify, "deliver",
                        lambda t, m, priority=None: calls.append(priority) or 0)
    monkeypatch.setattr("sys.argv", ["notify.py", "--unit", "x.service"])
    assert notify.main() == 0
    monkeypatch.setattr("sys.argv", ["notify.py", "--title", "t", "--message", "m"])
    assert notify.main() == 0
    assert calls == [notify.HIGH, None]


def test_dry_run_contacts_nothing(monkeypatch, capsys):
    """The flag exists so this can be checked on a machine with no
    credentials, so it must not read them or open a socket."""
    def explode(*a, **k):
        raise AssertionError("dry-run must not open a connection")

    monkeypatch.setattr(notify.urllib.request, "urlopen", explode)
    monkeypatch.setattr("sys.argv",
                        ["notify.py", "--title", "T", "--message", "M", "--dry-run"])
    assert notify.main() == 0
    assert "T" in capsys.readouterr().out


def test_unit_report_always_returns_something_sendable():
    """A unit that does not exist still has to produce a title and a body."""
    title, body = notify.unit_report("definitely-not-a-real-unit.service")
    assert title == "definitely-not-a-real-unit.service failed"
    assert body, "an empty body would send a useless notification"


# ---------------------------------------------------------------- deliver

CREDS = "NTFY_URL=http://ntfy:2586\nNTFY_TOKEN=t\nNTFY_TOPIC=alerts\n"


def _env_file(tmp_path, monkeypatch, body):
    path = tmp_path / "notify.env"
    path.write_text(body)
    monkeypatch.setenv("HOMELAB_NOTIFY_ENV", str(path))


class Clock:
    """Fake time. sleep() advances it, and so does a send that times out, so a
    test can see exactly how long the retry would have taken."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(notify.time, "monotonic", c.monotonic)
    monkeypatch.setattr(notify.time, "sleep", c.sleep)
    return c


def test_deliver_sends_with_credentials_from_env(tmp_path, monkeypatch, clock):
    _env_file(tmp_path, monkeypatch, CREDS)
    sent = []
    monkeypatch.setattr(notify, "send",
                        lambda url, token, topic, title, message, priority=None, timeout=None:
                        sent.append((url, token, topic, title, message, priority)) or 200)
    assert notify.deliver("T", "M") == 0
    assert sent == [("http://ntfy:2586", "t", "alerts", "T", "M", None)]


def test_deliver_without_credentials_is_2(tmp_path, monkeypatch, clock):
    _env_file(tmp_path, monkeypatch, "NTFY_URL=http://ntfy:2586\n")
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("must not send"))
    assert notify.deliver("T", "M") == 2


def test_deliver_retries_through_a_backup_stop(tmp_path, monkeypatch, clock):
    """ntfy is stopped for the nightly backup. A message published into that
    window never reaches the cache, so the publisher has to wait it out."""
    _env_file(tmp_path, monkeypatch, CREDS)
    attempts = []

    def flaky(*a, **k):
        attempts.append(clock.now)
        if len(attempts) < 3:
            raise urllib.error.URLError("connection refused")
        return 200

    monkeypatch.setattr(notify, "send", flaky)
    assert notify.deliver("T", "M") == 0
    assert attempts == [0.0, 10.0, 20.0]


def test_deliver_retries_a_5xx(tmp_path, monkeypatch, clock):
    _env_file(tmp_path, monkeypatch, CREDS)
    attempts = []

    def overloaded(url, *a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        return 200

    monkeypatch.setattr(notify, "send", overloaded)
    assert notify.deliver("T", "M") == 0
    assert len(attempts) == 2


def test_deliver_never_retries_a_4xx(tmp_path, monkeypatch, clock):
    """A revoked token will not fix itself in three minutes, and retrying it
    only delays the unit's own failure."""
    _env_file(tmp_path, monkeypatch, CREDS)
    attempts = []

    def forbidden(url, *a, **k):
        attempts.append(1)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(notify, "send", forbidden)
    assert notify.deliver("T", "M") == 1
    assert len(attempts) == 1
    assert clock.now == 0.0


def test_deliver_gives_up_inside_the_deadline_including_the_request(
        tmp_path, monkeypatch, clock):
    """The callers' timeouts (failure-notify 300 s, SMART 240 s) are sized for
    RETRY_SECONDS. If the in-flight request's timeout were added on top, the
    caller would kill this before it reported anything."""
    _env_file(tmp_path, monkeypatch, CREDS)
    timeouts = []

    def hangs(*a, timeout=None, **k):
        timeouts.append(timeout)
        clock.now += timeout          # the whole timeout elapses, then fails
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(notify, "send", hangs)
    assert notify.deliver("T", "M") == 1
    assert len(timeouts) > 1, "it must actually retry"
    assert all(t <= notify.REQUEST_TIMEOUT for t in timeouts)
    assert clock.now <= notify.RETRY_SECONDS + 1
