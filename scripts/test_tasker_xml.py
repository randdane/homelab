"""Tasker exports must parse and carry the suffix Tasker's import expects.

Tasker refuses a task file not named *.tsk.xml ("bad filename"), and each
refusal costs a round trip to the phone (2026-09-11). Profiles are *.prf.xml.
Covers this repo and the site checkout ($SITE_DIR, else ../homelab-private).
"""
import os
# Our own committed exports, not untrusted input; expat resolves no external
# entities by default, so defusedxml would be a dependency for nothing.
import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(os.environ.get("SITE_DIR") or ROOT.parent / "homelab-private")


def tasker_files():
    for base in (ROOT, SITE):
        if base.is_dir():
            yield from (p for p in base.rglob("*.xml") if ".git" not in p.parts
                        and ET.parse(p).getroot().tag == "TaskerData")  # nosemgrep


def expected_suffix(path):
    kinds = {c.tag for c in ET.parse(path).getroot()}  # nosemgrep
    return ".prf.xml" if "Profile" in kinds else ".tsk.xml"


def test_tasker_files_found():
    assert any(tasker_files()), "no Tasker XML found; the scan is looking in the wrong place"


def test_tasker_suffixes():
    bad = [f"{p}: expected {expected_suffix(p)}" for p in tasker_files()
           if not p.name.endswith(expected_suffix(p))]
    assert not bad, "\n".join(bad)


def test_suffix_rule(tmp_path):
    t = tmp_path / "x.xml"
    t.write_text("<TaskerData><Task/></TaskerData>")
    assert expected_suffix(t) == ".tsk.xml"
    t.write_text("<TaskerData><Profile/><Task/></TaskerData>")
    assert expected_suffix(t) == ".prf.xml"
