# /// script
# requires-python = ">=3.11"
# ///
"""Push a notification to the phone through ntfy.

    uv run scripts/notify.py --title "..." --message "..."
    uv run scripts/notify.py --unit homelab-backup.service

This exists for `OnFailure=`. Every timer in this repo ran unwatched until
2026-08-24: a failed unit is marked failed and nothing else happens, so a
backup that stops running looks exactly like a backup with nothing to say.
The failure that matters is the silent one.

`--unit` is the OnFailure form. It reads the unit's result and its last
journal lines and sends those, at high priority, because
"homelab-backup.service failed" alone sends you to the machine anyway.

Credentials come from the ntfy stack's .env, which the life-queue n8n
container also reads. Deliberately NOT a second copy: two files holding the
same token drift, and the stale one fails exactly when you need the alert.
Override the path with HOMELAB_NOTIFY_ENV.

It retries for up to RETRY_SECONDS, because ntfy is stopped for each nightly
backup and a message published into that window never reaches the cache.
Every caller's timeout is sized to outlast that; see the ntfy spec.

Exit codes: 0 sent, 1 could not send, 2 no usable credentials.

Tested by scripts/test_notify.py. Being the OnFailure handler for every timer
here, it is the last thing that should be unverified: broken, it makes a
failed unit silent, and silence is what a healthy unit looks like too.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENV = "/opt/homelab/stacks/ntfy/.env"
JOURNAL_LINES = 15
# The phone shows the start of a long notification and expands the rest only
# so far. The end of a journal excerpt is where the error is, so keep it short
# enough to reach.
MAX_MESSAGE = 900
# Longer than ntfy is stopped for the nightly backup, which is one step inside
# a 1m33s-2m05s homelab-backup run. Includes the request in flight.
RETRY_SECONDS = 180
RETRY_DELAY = 10
REQUEST_TIMEOUT = 30
# ntfy's "high". Sent for --unit only.
HIGH = 4


def read_env(path):
    """Parse a KEY=VALUE file without letting the shell near it.

    Values here contain `$` and quotes. Sourcing this file expands them and
    produces a token that is subtly wrong rather than obviously missing.
    """
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def unit_report(unit):
    """Title and body describing why `unit` failed."""
    fields = ("Result", "ExecMainStatus", "ActiveState")
    try:
        shown = subprocess.run(
            ["systemctl", "show", unit, "--property=" + ",".join(fields)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        shown = ""

    try:
        log = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(JOURNAL_LINES),
             "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        log = ""

    body = "\n".join(p for p in (shown, log) if p) or "no detail available"
    return f"{unit} failed", body


def send(url, token, topic, title, message, priority=None, timeout=REQUEST_TIMEOUT):
    """Publish one message as JSON to the server root.

    JSON rather than a Title header: urllib encodes headers as Latin-1, so a
    title with an emoji would raise instead of sending. No tags, ever -- ntfy
    renders an emoji tag in front of the title, which breaks the phone's
    digest watchdog."""
    if len(message) > MAX_MESSAGE:
        message = message[: MAX_MESSAGE - 3] + "..."
    payload = {"topic": topic, "title": title, "message": message}
    if priority is not None:
        payload["priority"] = priority
    request = urllib.request.Request(
        url.rstrip("/") + "/",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def retryable(exc):
    """A 5xx or no connection may clear up; a 4xx will not."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return True


def deliver(title, message, priority=None):
    """Read credentials and send, retrying until RETRY_SECONDS. Shared by
    main() and digest.py, so the two senders cannot disagree about where the
    token lives or what failure means.

    Exit codes: 0 sent, 1 could not send, 2 no usable credentials."""
    env = read_env(os.environ.get("HOMELAB_NOTIFY_ENV", DEFAULT_ENV))
    url = env.get("NTFY_URL")
    token = env.get("NTFY_TOKEN")
    topic = env.get("NTFY_TOPIC")
    if not (url and token and topic):
        print("no NTFY_URL / NTFY_TOKEN / NTFY_TOPIC available", file=sys.stderr)
        return 2

    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        try:
            status = send(url, token, topic, title, message, priority,
                          timeout=max(1, min(REQUEST_TIMEOUT, remaining)))
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                # Never echo the response body: it can quote the request headers.
                print(f"ntfy returned {exc.code}", file=sys.stderr)
            else:
                print(f"could not reach ntfy: {exc}", file=sys.stderr)
            if not retryable(exc) or time.monotonic() + RETRY_DELAY >= deadline:
                return 1
            time.sleep(RETRY_DELAY)
            continue
        print(f"sent ({status}): {title}")
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", help="failed unit to report on")
    parser.add_argument("--title")
    parser.add_argument("--message")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent, contact nothing")
    args = parser.parse_args()

    if args.unit:
        title, message = unit_report(args.unit)
    elif args.title and args.message:
        title, message = args.title, args.message
    else:
        parser.error("need --unit, or both --title and --message")

    if args.dry_run:
        print(f"title:   {title}")
        print(f"message: {message[:MAX_MESSAGE]}")
        return 0

    return deliver(title, message, HIGH if args.unit else None)


if __name__ == "__main__":
    sys.exit(main())
