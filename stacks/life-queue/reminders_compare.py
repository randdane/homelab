# /// script
# requires-python = ">=3.11"
# ///
"""Compare what n8n and the reminders service sent, from the moment the service
first sent anything or --since (epoch). Run on the host. Read-only: n8n's
database is copied out with its WAL, as the README explains.

    uv run reminders_compare.py [--since EPOCH]
    python3 reminders_compare.py --self-test
"""
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WINDOW = 35 * 60  # a poll on each side, plus a little


def n8n_messages(run_data):
    """(task, text, accepted) from one execution's flatted JSON."""
    table = json.loads(run_data)

    def text(i):
        return table[int(i)] if isinstance(i, str) else i

    wanted, accepted = [], set()
    for row in table:
        if not isinstance(row, dict):
            continue
        if "source" in row and "task_id" in row and "message" in row:
            wanted.append((row["task_id"], text(row["message"])))
        elif "click" in row and "message" in row:
            accepted.add(text(row["message"]))
    return [(task, msg, msg in accepted) for task, msg in wanted]


def read_n8n(since):
    with tempfile.TemporaryDirectory() as d:
        for f in ("database.sqlite", "database.sqlite-wal", "database.sqlite-shm"):
            subprocess.run(["docker", "cp", "-q", f"life-queue-n8n:/home/node/.n8n/{f}", f"{d}/{f}"],
                           check=True)
        db = sqlite3.connect(f"{d}/database.sqlite")
        rows = db.execute("select e.startedAt, d.data from execution_entity e "
                          "join execution_data d on d.executionId = e.id").fetchall()
        db.close()
    out = []
    for started, data in rows:
        at = int(datetime.fromisoformat(started).replace(tzinfo=timezone.utc).timestamp())
        if at >= since:
            out += [(at, *m) for m in n8n_messages(data)]
    return sorted(out)


def read_service():
    log = subprocess.run(["docker", "logs", "life-queue-reminders"], capture_output=True,
                         text=True, check=True).stdout
    return [(int(m[1]), int(m[2]), m[3]) for m in re.finditer(r"^sent (\d+) (\d+) (.*)$", log, re.M)]


def pair(n8n, service):
    """Greedy by time: each message pairs with one on the other side, same task."""
    left = list(service)
    matched, only_n8n = [], []
    for row in n8n:
        at, task = row[0], row[1]
        hit = next((s for s in left if s[1] == task and abs(s[0] - at) <= WINDOW), None)
        if hit:
            left.remove(hit)
            matched.append((row, hit))
        else:
            only_n8n.append(row)
    return matched, only_n8n, left


def when(at):
    return datetime.fromtimestamp(at).strftime("%m-%d %H:%M")


def self_test():
    a = (1000, 1, "Reminder: x", True)
    b = (5000, 2, "Overdue: y", False)
    c = (1100, 1, "Reminder: x")
    d = (9000, 3, "Overdue: z")
    matched, only_n8n, only_service = pair([a, b], [c, d])
    assert matched == [(a, c)] and only_n8n == [b] and only_service == [d]
    # Too far apart, and one message cannot pair twice.
    assert pair([(0, 1, "x", True)], [(WINDOW + 1, 1, "x")])[0] == []
    assert len(pair([(0, 1, "x", True), (1, 1, "x", True)], [(0, 1, "x")])[1]) == 1
    flat = json.dumps([{"x": "1"}, {"task_id": 7, "source": "2", "message": "3"}, "reminder", "hello",
                       {"click": "2", "message": "3"}, {"task_id": 8, "source": "2", "message": "6"}, "lost"])
    assert n8n_messages(flat) == [(7, "hello", True), (8, "lost", False)]
    print("self-test passed")


def main():
    if "--self-test" in sys.argv:
        return self_test()
    service = read_service()
    if "--since" in sys.argv:
        since = int(sys.argv[sys.argv.index("--since") + 1])
    elif service:
        since = service[0][0]
    else:
        print("the reminders service has sent nothing yet")
        since = None
    n8n = read_n8n(since) if since is not None else []
    matched, only_n8n, only_service = pair(n8n, service)
    print(f"n8n {len(n8n)} (accepted by ntfy {sum(r[3] for r in n8n)}), service {len(service)}, paired {len(matched)}")
    for r, s in matched:
        if r[2] != s[2]:
            print(f"DIFFERENT TEXT {when(r[0])} task {r[1]}: n8n {r[2]!r} service {s[2]!r}")
    for r in only_n8n:
        print(f"ONLY N8N {when(r[0])} task {r[1]} {'sent' if r[3] else 'FAILED'}: {r[2]}")
    for s in only_service:
        print(f"ONLY SERVICE {when(s[0])} task {s[1]}: {s[2]}")


if __name__ == "__main__":
    main()
