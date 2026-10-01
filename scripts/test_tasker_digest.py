"""The phone-side digest watchdog, tested from the XML Tasker imports.

The logic lives in JavaScriptlets inside `stacks/uptime-kuma/tasker/*.xml`,
not in Tasker conditions, so it can be run here with node against stub
versions of Tasker's `global`, `local`, `setGlobal` and `performTask`. The
script text is read from the XML itself, so this tests exactly what gets
imported.

Found 2026-09-10 in review: `Digest record` matched the digest pattern against
`%NTITLE` and every `%evtprm1`-`%evtprm8`. A failure alert whose *body* read
`homelab digest <today>: OK` recorded today's digest, and so did any
notification that fired while `%NTITLE` still held a digest title, because
`%NTITLE` is the last notification shown, not the one that fired the profile.
Tasker's guide says to use `%evtprm2`, the event's own title. The acceptance
run missed it because its unrelated alert had an unrelated body too.

Skipped where node is not installed.
"""
import html
import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

TASKER = Path(__file__).parent.parent / "stacks/uptime-kuma/tasker"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def script(filename):
    """The first action's arg0 -- the JavaScriptlet's code.

    Extracted with a regex rather than an XML parser: these are files this
    repo generates, with one JavaScriptlet each, and a parser buys nothing but
    an XXE finding on trusted input.
    """
    text = (TASKER / filename).read_text()
    match = re.search(r'<Action sr="act0"[^>]*>.*?<Str sr="arg0"[^>]*>(.*?)</Str>',
                      text, re.DOTALL)
    assert match, f"no JavaScriptlet arg0 in {filename}"
    return html.unescape(match.group(1))


def run(js, *, globals_=None, locals_=None):
    """Run one JavaScriptlet; return the Tasker side effects it produced.

    `today` is computed by the script from the machine's clock, like on the
    phone, so the expected dates use the same clock.
    """
    harness = (
        "var calls = [];\n"
        f"var G = {json.dumps(globals_ or {})}, L = {json.dumps(locals_ or {})};\n"
        "function global(n) { return G[n] || ''; }\n"
        "function local(n) { return L[n] || ''; }\n"
        "function setGlobal(n, v) { calls.push('set ' + n + '=' + v); }\n"
        "function performTask(n) { calls.push('run ' + n); }\n"
        + js +
        "\nconsole.log(JSON.stringify(calls));\n")
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                         timeout=30, check=True)
    return json.loads(out.stdout)


RECORDED = [f"set DigestDate={TODAY}"]


# -- Digest record -----------------------------------------------------------

def record(**kw):
    return run(script("Digest_received.prf.xml"), **kw)


def test_digest_received_listens_to_the_ntfy_app():
    """The digest arrives through ntfy. An owner app left on Home Assistant
    never fires, and the 09:00 alarm then goes off every single day."""
    text = (TASKER / "Digest_received.prf.xml").read_text()
    assert "<appPkg>io.heckel.ntfy</appPkg>" in text
    assert "io.homeassistant" not in text


def test_todays_digest_title_is_recorded():
    assert record(locals_={"evtprm2": f"homelab digest {TODAY}: OK"}) == RECORDED


def test_todays_problem_digest_is_recorded():
    assert record(locals_={"evtprm2": f"homelab digest {TODAY}: 2 problems"}) == RECORDED


def test_yesterdays_digest_is_not_recorded():
    assert record(locals_={"evtprm2": f"homelab digest {YESTERDAY}: OK"}) == []


def test_unrelated_alert_is_not_recorded():
    assert record(locals_={"evtprm2": "homelab-backup.service failed",
                           "evtprm3": "acceptance test, ignore"}) == []


def test_digest_text_in_the_body_is_not_recorded():
    """The review's reproduction: the title is a failure alert."""
    assert record(locals_={
        "evtprm1": "ntfy",
        "evtprm2": "homelab-backup.service failed",
        "evtprm3": f"homelab digest {TODAY}: OK",
        "evtprm4": f"homelab digest {TODAY}: OK",
    }) == []


def test_a_digest_in_ntitle_does_not_count_for_another_notification():
    """%NTITLE is the last notification shown, not the one that fired."""
    assert record(globals_={"NTITLE": f"homelab digest {TODAY}: OK"},
                  locals_={"evtprm2": "Some other alert"}) == []


def test_title_must_start_with_the_prefix():
    assert record(locals_={"evtprm2": f"re: homelab digest {TODAY}: OK"}) == []


# -- Digest check ------------------------------------------------------------

def check(**kw):
    return run(script("Digest_missing.prf.xml"), **kw)


def test_no_alarm_when_today_was_recorded():
    assert check(globals_={"DigestDate": TODAY}) == []


def test_alarm_when_nothing_was_recorded():
    assert check() == ["run Digest alarm"]


def test_alarm_when_only_yesterday_was_recorded():
    assert check(globals_={"DigestDate": YESTERDAY}) == ["run Digest alarm"]


def test_the_alarm_task_exists_under_the_name_check_starts():
    alarm = (TASKER / "Digest_alarm.tsk.xml").read_text()
    assert re.search(r"<Task\b.*?<nme>Digest alarm</nme>", alarm, re.DOTALL)
    assert "performTask('Digest alarm'" in script("Digest_missing.prf.xml")
