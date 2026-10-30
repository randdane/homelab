# /// script
# requires-python = ">=3.11"
# ///
"""List, or with --remove delete, the reminders service's Vikunja webhooks.
Only for backing out of the side-by-side run; import.py adds them. Run on the
host, with the stack's .env in this directory. n8n's webhooks are not touched.

    uv run reminders_webhooks.py [--remove]
"""
import json
import sys
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:3456/api/v1"
TARGET = "http://reminders:8080/webhook/vikunja-reminder"


def call(token, method, path):
    req = urllib.request.Request(URL + path, method=method,
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or "null")


def main():
    env = dict(l.split("=", 1) for l in Path(".env").read_text().splitlines()
               if "=" in l and not l.startswith("#"))
    token = env["VIKUNJA_TOKEN"]
    for p in call(token, "GET", "/projects"):
        for w in call(token, "GET", f"/projects/{p['id']}/webhooks") or []:
            ours = w["target_url"] == TARGET
            print(f"{p['title']}: {w['target_url']}{' (reminders)' if ours else ''}")
            if ours and "--remove" in sys.argv:
                call(token, "DELETE", f"/projects/{p['id']}/webhooks/{w['id']}")
                print("  removed")


if __name__ == "__main__":
    main()
