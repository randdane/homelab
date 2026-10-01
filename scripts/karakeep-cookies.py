#!/usr/bin/env python3
"""Convert a browser cookie export (Cookie-Editor JSON) into Karakeep's
BROWSER_COOKIE_PATH format, keeping only the given domain's cookies.

Karakeep validates the file strictly and aborts crawler startup on any
mismatch, so this normalizes sameSite and the expiry field name.

  karakeep-cookies.py export.json reddit.com > cookies.json
  karakeep-cookies.py --self-test
"""
import json
import sys

SAMESITE = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}


def convert(cookies, domain):
    out = []
    for c in cookies:
        d = c.get("domain", "")
        if not (d.lstrip(".") == domain or d.endswith("." + domain)):
            continue
        k = {"name": c["name"], "value": c["value"], "domain": d, "path": c.get("path", "/")}
        exp = c.get("expires", c.get("expirationDate"))
        if isinstance(exp, (int, float)) and exp > 0:
            k["expires"] = exp
        for f in ("httpOnly", "secure"):
            if isinstance(c.get(f), bool):
                k[f] = c[f]
        ss = SAMESITE.get(str(c.get("sameSite", "")).lower())  # "unspecified" etc. dropped
        if ss:
            k["sameSite"] = ss
        out.append(k)
    return out


def self_test():
    got = convert([
        {"name": "reddit_session", "value": "v", "domain": ".reddit.com", "expirationDate": 1.5,
         "httpOnly": True, "secure": True, "sameSite": "no_restriction", "hostOnly": False},
        {"name": "x", "value": "v", "domain": "www.reddit.com", "sameSite": "unspecified", "session": True},
        {"name": "evil", "value": "v", "domain": "notreddit.com"},
        {"name": "g", "value": "v", "domain": ".google.com"},
    ], "reddit.com")
    assert [c["name"] for c in got] == ["reddit_session", "x"], got
    assert got[0] == {"name": "reddit_session", "value": "v", "domain": ".reddit.com", "path": "/",
                      "expires": 1.5, "httpOnly": True, "secure": True, "sameSite": "None"}, got[0]
    assert "sameSite" not in got[1] and "expires" not in got[1], got[1]
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        src, domain = sys.argv[1], sys.argv[2]
        out = convert(json.load(open(src)), domain)
        if not out:
            sys.exit(f"no cookies for {domain} in {src}")
        json.dump(out, sys.stdout, indent=1)
        print(f"{len(out)} cookies: {', '.join(c['name'] for c in out)}", file=sys.stderr)
