#!/usr/bin/env python3
"""Minimal Vikunja API access, with the whole-object POST trap made unwriteable.

    import os, vikunja
    vikunja.patch(22, done=True)          # read-modify-write, safe
    vikunja.call("GET", "/tasks/22")      # anything else

Reads VIKUNJA_TOKEN from the environment, and VIKUNJA_URL if the server is not
at http://localhost:3456. Nothing is taken as an argument: an argument lands in
the shell history of whatever box you ran it from.

WHY THIS FILE EXISTS
--------------------
`POST /api/v1/tasks/{id}` REPLACES the task. It does not patch it. A request
body of {"done": true} returns 200 and blanks the description, the due date,
the labels and everything else the body omitted.

That was documented in README.md, in plain language, with the word "replaces"
in bold -- and on 2026-09-03 a session that had read it closed task #14 with a
bare POST anyway and destroyed an 881-character description. Documentation had
already failed once, so this file does not document the trap again: `call`
REFUSES the dangerous request, and `patch` is the thing that works.

Same reasoning as the directory-mount change in docs/lessons-learned.md 18:
when a documented trap recurs, remove the trap rather than describe it better.
"""

import json
import os
import re
import urllib.request

BASE = os.environ.get("VIKUNJA_URL", "http://localhost:3456").rstrip("/") + "/api/v1"

# POST to this path replaces the whole task. Every other endpoint is fine.
_WHOLE_OBJECT_POST = re.compile(r"^/tasks/\d+$")


def _reject(method, path, body):
    """Return an error string if this request would silently destroy fields.

    Split out from `call` so it can be tested without a server. A body that
    came from a GET always carries "id"; a hand-written partial like
    {"done": True} does not, and that difference is the entire signal.
    """
    if method != "POST" or not _WHOLE_OBJECT_POST.match(path):
        return None
    if not isinstance(body, dict) or "id" in body:
        return None
    return (
        f"POST {path} REPLACES the task -- every field you omitted would be "
        f"blanked, and the server would return 200.\n"
        f"You passed only: {sorted(body)}\n"
        f"Use patch({path.split('/')[-1]}, ...) instead, which reads the task "
        f"first and preserves the rest."
    )


def call(method, path, body=None):
    """One API request. Raises ValueError rather than let a partial POST through."""
    problem = _reject(method, path, body)
    if problem:
        raise ValueError(problem)
    token = os.environ["VIKUNJA_TOKEN"]  # KeyError here is the right failure
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as f:
        return json.load(f)


def patch(task_id, **fields):
    """Change some fields of a task, leaving the rest alone.

    Read-modify-write, because the API offers no partial update. Note the race
    that implies: a concurrent writer's changes between the GET and the POST
    are silently discarded. With one writer -- the normal case here -- that is
    theoretical. If n8n ever writes tasks while a script does, it is not.
    """
    task = call("GET", f"/tasks/{task_id}")
    task.update(fields)
    return call("POST", f"/tasks/{task_id}", task)


def _self_check():
    """The smallest thing that fails if the guard breaks. No server needed."""
    # The exact call that destroyed #14 must be refused.
    assert _reject("POST", "/tasks/22", {"done": True})
    assert _reject("POST", "/tasks/9", {"title": "x"})
    # A full object round-tripped from GET carries id, and must pass.
    assert _reject("POST", "/tasks/22", {"id": 22, "done": True, "description": "kept"}) is None
    # Everything else is none of this guard's business.
    assert _reject("GET", "/tasks/22", None) is None
    assert _reject("PUT", "/projects/5/tasks", {"title": "new"}) is None
    assert _reject("POST", "/tasks/22/attachments", {"x": 1}) is None
    assert _reject("POST", "/projects/5", {"title": "x"}) is None
    # A non-dict body cannot be a partial task.
    assert _reject("POST", "/tasks/22", None) is None
    print("self-check ok")


if __name__ == "__main__":
    _self_check()
