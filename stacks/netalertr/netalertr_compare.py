# /// script
# requires-python = ">=3.11"
# ///
"""Compare netalertr with NetAlertX during the side-by-side run. Run on the
host. Read-only: netalertr's state file is copied out with `docker cp`, and
NetAlertX's database is copied with SQLite's backup API, run inside its
container, so a write-ahead log cannot give a torn read.

    uv run netalertr_compare.py
    python3 netalertr_compare.py --self-test

Exit 1 if anything below says FAIL.
"""
import io
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Read from the running NetAlertX 2026-10-31: table, columns, text timestamps. The
# newest timestamp reads as 127 s old in UTC and hours ahead in its local zone, so UTC.
NETALERTX_CONTAINER = "netalertx"
NETALERTX_DB = "/data/db/app.db"
NETALERTX_TABLE = "Devices"
NETALERTX_MAC_COLUMN = "devMac"
NETALERTX_FIRST_COLUMN = "devFirstConnection"
NETALERTX_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # text, UTC
NETALERTX_TIME_ZONE = timezone.utc
NETALERTX_ENV = Path(__file__).resolve().parent.parent / "netalertx" / ".env"
NETALERTX_OWN_MAC_KEY = "NETALERTX_MAC"  # NetAlertX's own interface; excused
# Its /tmp is a mode-1700 tmpfs owned by this user, and root there has no override.
NETALERTX_USER = "netalertx"

NETALERTR_CONTAINER = "netalertr"
# The four network names `dream` reports come from NETALERTR_EXPECTED_NETWORKS
# (comma-separated) in this stack's .env, so they stay out of the repo.
# Placeholders until then, and the compare says so.
EXPECTED_NETWORKS = {"<network-1>", "<network-2>", "<network-3>", "<network-4>"}


def parse_tar_json(blob):
    """The state file from a `docker cp <ctr>:<path> -` tar stream."""
    import json
    with tarfile.open(fileobj=io.BytesIO(blob)) as t:
        member = next(m for m in t.getmembers() if m.isfile())
        return json.load(t.extractfile(member))


def read_state():
    blob = subprocess.run(["docker", "cp", f"{NETALERTR_CONTAINER}:/data/state.json", "-"],
                          capture_output=True, check=True).stdout
    return parse_tar_json(blob)


# The source opens read-only (a wrong path errors rather than creating an empty
# file); only the temp copy is writable.
def backup_snippet(src, dst):
    return ("import sqlite3;"
            f"s=sqlite3.connect('file:'+{src!r}+'?mode=ro',uri=True);d=sqlite3.connect({dst!r});"
            "s.backup(d);d.close();s.close()")


BACKUP = backup_snippet(NETALERTX_DB, "/tmp/netalertr-compare.db")


def parse_time(text):
    if text is None or str(text).strip() == "":
        return None
    return int(datetime.strptime(str(text).strip(), NETALERTX_TIME_FORMAT)
               .replace(tzinfo=NETALERTX_TIME_ZONE).timestamp())


def read_netalertx():
    """[(mac, first_connection_epoch or None)] from a backup-API copy."""
    c = NETALERTX_CONTAINER
    tmp = "/tmp/netalertr-compare.db"
    ex = ["docker", "exec", "-u", NETALERTX_USER, c]
    subprocess.run([*ex, "python3", "-c", BACKUP], check=True)
    try:
        with tempfile.TemporaryDirectory() as d:
            # `docker cp` cannot read a tmpfs.
            with open(f"{d}/c.db", "wb") as out:
                subprocess.run([*ex, "cat", tmp], stdout=out, check=True)
            db = sqlite3.connect(f"{d}/c.db")
            rows = db.execute(f"select {NETALERTX_MAC_COLUMN}, {NETALERTX_FIRST_COLUMN} "
                              f"from {NETALERTX_TABLE}").fetchall()
            db.close()
    finally:
        subprocess.run([*ex, "rm", "-f", tmp], check=False)
    return [(str(m).lower(), parse_time(t)) for m, t in rows if m]


def read_own_mac():
    """Only the one key is read from NetAlertX's .env; nothing else is kept."""
    try:
        text = NETALERTX_ENV.read_text()
    except OSError:
        return None
    m = re.search(rf"^{NETALERTX_OWN_MAC_KEY}=(\S+)", text, re.M)
    return m[1].lower() if m else None


def analyse(state, rows, own_mac, excused=frozenset()):
    """Findings as plain data; each list is one report section."""
    known = set(state["known"])
    events = state["events"]
    new = [e for e in events if e["type"] == "new"]
    new_macs = {e["mac"] for e in new}
    seeded_at = state.get("seeded_at")
    na_macs = {m for m, _ in rows}
    networks = Counter((k.get("network") or "(unknown)") for k in state["known"].values())
    return {
        "na_count": len(rows),
        "no_first_time": sum(1 for _, t in rows if t is None),
        "missing": sorted(na_macs - known - {own_mac} - excused),
        "excused_hardware": sorted((na_macs - known) & excused),
        "own_excused": own_mac is not None and own_mac in na_macs - known,
        "added_without_new": sorted(m for m, t in rows
                                    if seeded_at is not None and t is not None and t > seeded_at
                                    and m not in new_macs),
        "new_unknown_to_na": sorted(new_macs - na_macs),
        "unalerted": sorted(e["mac"] for e in new if not e.get("alerted_at")),
        "outbox": len(state["outbox"]),
        "per_mac": Counter(e["mac"] for e in events),
        "networks": networks,
        "unexpected_networks": sorted(set(networks) - EXPECTED_NETWORKS - {"(unknown)"}),
        "empty_networks": sorted(EXPECTED_NETWORKS - set(networks)),
        "placeholders": any(n.startswith("<") for n in EXPECTED_NETWORKS),
        "seeded": bool(state.get("seeded")),
    }


def report(f):
    """Lines to print, and whether the compare passes."""
    out, fail = [], False

    def say(line, bad=False):
        nonlocal fail
        fail = fail or bad
        out.append(("FAIL " if bad else "") + line)

    if not f["seeded"]:
        say("netalertr has not seeded yet", True)
    if f["na_count"] == 0:
        say("NetAlertX device table is empty; nothing to compare", True)
    if f["no_first_time"]:
        say(f"{f['no_first_time']} NetAlertX device(s) have no first-connection time; "
            "their add time cannot be checked", True)
    for m in f["missing"]:
        say(f"missing from netalertr: {m}", True)
    if f["own_excused"]:
        say("excused: NetAlertX's own interface MAC is not in netalertr")
    for m in f["excused_hardware"]:
        say(f"excused: network hardware, not a client: {m}")
    for m in f["added_without_new"]:
        say(f"added to NetAlertX after the seed, no new event here: {m}", True)
    for m in f["new_unknown_to_na"]:
        say(f"new event here, unknown to NetAlertX (explain): {m}")
    for m in f["unalerted"]:
        say(f"new event without alerted_at: {m}", True)
    say(f"outbox size {f['outbox']}", f["outbox"] > 0)
    if f["placeholders"]:
        say("NETALERTR_EXPECTED_NETWORKS is not set in .env; fill it in on the host", True)
    for n in f["empty_networks"]:
        if not n.startswith("<"):
            say(f"expected network has no known MAC: {n}", True)
    for n in f["unexpected_networks"]:
        say(f"unexpected network name (not a failure): {n}")
    say("known MACs per network: " + ", ".join(f"{n}={c}" for n, c in sorted(f["networks"].items())))
    say("events per MAC: " + (", ".join(f"{m}={c}" for m, c in f["per_mac"].most_common()) or "none"))
    say("RESULT: " + ("FAIL" if fail else "ok"))
    return out, fail


def self_test():
    global EXPECTED_NETWORKS
    keep = EXPECTED_NETWORKS
    EXPECTED_NETWORKS = {"lan", "iot"}
    try:
        k = lambda net: {"first_recorded": 1, "ip": None, "network": net, "vlan": None}
        state = {
            "seeded": True, "seeded_at": 1000,
            "known": {"02:00:00:00:00:01": k("lan"), "02:00:00:00:00:02": k("iot"),
                      "02:00:00:00:00:05": k("guest"), "02:00:00:00:00:06": k(None)},
            "events": [
                {"at": 2000, "mac": "02:00:00:00:00:02", "type": "new", "from": None, "to": None,
                 "vlan": None, "alerted_at": 2001},
                {"at": 2100, "mac": "02:00:00:00:00:05", "type": "new", "from": None, "to": None,
                 "vlan": None},
                {"at": 2200, "mac": "02:00:00:00:00:01", "type": "ip_changed", "from": "192.0.2.1",
                 "to": "192.0.2.2", "vlan": None},
            ],
            "outbox": [{"mac": "02:00:00:00:00:05", "queued_at": 2100}],
        }
        rows = [("02:00:00:00:00:01", 500), ("02:00:00:00:00:02", 2000), ("02:00:00:00:00:03", 600),
                ("02:00:00:00:00:04", 600), ("02:00:00:00:00:07", 3000)]
        f = analyse(state, rows, "02:00:00:00:00:04")
        assert f["missing"] == ["02:00:00:00:00:03", "02:00:00:00:00:07"], f["missing"]
        assert f["own_excused"]
        assert f["added_without_new"] == ["02:00:00:00:00:07"]
        assert f["new_unknown_to_na"] == ["02:00:00:00:00:05"]
        assert f["unalerted"] == ["02:00:00:00:00:05"] and f["outbox"] == 1
        assert f["per_mac"]["02:00:00:00:00:01"] == 1
        assert f["unexpected_networks"] == ["guest"] and f["empty_networks"] == []
        lines, fail = report(f)
        assert fail and any("excused" in x for x in lines) and any("unexpected network" in x for x in lines)
        # A clean run passes, and the own MAC is not named when it is present.
        clean = {"seeded": True, "seeded_at": 1000, "outbox": [], "events": [],
                 "known": {"02:00:00:00:00:01": k("lan"), "02:00:00:00:00:02": k("iot")}}
        lines, fail = report(analyse(clean, [("02:00:00:00:00:01", 5), ("02:00:00:00:00:02", 5)], None))
        assert not fail, lines
        # An expected network with no known MAC fails.
        lines, fail = report(analyse({**clean, "known": {"02:00:00:00:00:01": k("lan")}},
                                     [("02:00:00:00:00:01", 5)], None))
        assert fail and any("iot" in x for x in lines)
        # An empty NetAlertX table fails, and so do devices with no first-connection time.
        lines, fail = report(analyse(clean, [], None))
        assert fail and any("table is empty" in x for x in lines)
        lines, fail = report(analyse(clean, [("02:00:00:00:00:01", 5), ("02:00:00:00:00:02", None)], None))
        assert fail and any(x.startswith("FAIL 1 NetAlertX device") for x in lines), lines
        # The source opens read-only: a wrong path errors and creates nothing.
        with tempfile.TemporaryDirectory() as d:
            bad = f"{d}/nope.db"
            r = subprocess.run([sys.executable, "-c", backup_snippet(bad, f"{d}/copy.db")],
                               capture_output=True)
            assert r.returncode != 0 and not Path(bad).exists()
            good = f"{d}/real.db"
            db = sqlite3.connect(good)
            db.execute("create table t(x)")
            db.commit()
            db.close()
            r = subprocess.run([sys.executable, "-c", backup_snippet(good, f"{d}/copy.db")],
                               capture_output=True)
            assert r.returncode == 0 and Path(f"{d}/copy.db").exists(), r.stderr
        # Placeholders fail until filled in.
        EXPECTED_NETWORKS = keep
        assert report(analyse(clean, [("02:00:00:00:00:01", 5), ("02:00:00:00:00:02", 5)], None))[1]
        # Excused network hardware is named, not failed; only the listed MAC is excused.
        EXPECTED_NETWORKS = {"lan", "iot"}
        two = [("02:00:00:00:00:01", 5), ("02:00:00:00:00:02", 5)]
        lines, fail = report(analyse(clean, [*two, ("02:00:00:00:00:08", 5)], None, {"02:00:00:00:00:08"}))
        assert not fail and any("network hardware" in x and "02:00:00:00:00:08" in x for x in lines), lines
        lines, fail = report(analyse(clean, [*two, ("02:00:00:00:00:09", 5)], None, {"02:00:00:00:00:08"}))
        assert fail and not any("network hardware" in x for x in lines), lines
        # Expected names come from the .env; absent means placeholders stay.
        with tempfile.TemporaryDirectory() as d:
            e = Path(d) / ".env"
            assert read_expected_networks(e) is None
            e.write_text("X=1\nNETALERTR_EXPECTED_NETWORKS=lan, iot\n")
            assert read_expected_networks(e) == {"lan", "iot"}
            e.write_text("X=1\n")
            assert read_expected_networks(e) is None
        # Timestamp parser and tar unpacking.
        assert parse_time("1970-01-01 00:01:40") == 100 and parse_time("") is None
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            data = b'{"seeded": true}'
            info = tarfile.TarInfo("state.json")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        assert parse_tar_json(buf.getvalue()) == {"seeded": True}
    finally:
        EXPECTED_NETWORKS = keep
    print("self-test passed")


def read_expected_networks(env_file):
    """The names from the stack's .env, or None if unset; nothing else is kept."""
    try:
        m = re.search(r"^NETALERTR_EXPECTED_NETWORKS=(.+)$", Path(env_file).read_text(), re.M)
    except OSError:
        return None
    return {n.strip() for n in m[1].split(",") if n.strip()} if m else None


def read_excused_macs(env_file):
    """UniFi hardware that NetAlertX lists and the client lists do not."""
    try:
        m = re.search(r"^NETALERTR_EXCUSED_MACS=(.+)$", Path(env_file).read_text(), re.M)
    except OSError:
        return set()
    return {n.strip().lower() for n in m[1].split(",") if n.strip()} if m else set()


def main():
    global EXPECTED_NETWORKS
    if "--self-test" in sys.argv:
        return self_test()
    EXPECTED_NETWORKS = read_expected_networks(Path(__file__).resolve().parent / ".env") or EXPECTED_NETWORKS
    state = read_state()
    excused = read_excused_macs(Path(__file__).resolve().parent / ".env")
    lines, fail = report(analyse(state, read_netalertx(), read_own_mac(), excused))
    print("\n".join(lines))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
