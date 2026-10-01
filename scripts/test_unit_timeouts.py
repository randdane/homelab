"""Every homelab service template must bound how long it can run.

A `Type=oneshot` unit has no start timeout by default. If its command hangs --
a wedged dockerd is enough, since `docker ps` then blocks forever -- the unit
never fails, so `OnFailure=homelab-failure-notify@` never fires, and systemd
will not start a unit that is still activating, so every later timer run is
skipped too. A hung check looks exactly like a quiet healthy one.

Found 2026-09-10 in the morning digest's branch review: `homelab-status`
calls `docker ps` and `docker inspect` per stack with no timeout, and its unit
had no `TimeoutStartSec` either. Four of the ten templates were unbounded.
The test exists because the next template will be written by copying one of
those, and nothing about a missing line looks wrong.

Scoped to `homelab-*.service.in`, the units `install-systemd.sh` renders --
which since 2026-09-25 includes `homelab-tailnet-source` and its `-check`
(formerly hand-installed). `laptop-lan-route.service`
is still installed by hand as root on the laptop and runs only `ip`.

`homelab-failure-notify@` is included on purpose: it is the alert path for
every other unit, so a hang there silences all of them.
"""
from pathlib import Path

import pytest

TEMPLATES = sorted(
    (Path(__file__).parent / "systemd").glob("homelab-*.service.in"))


def test_templates_were_found():
    """An empty glob would make every other test here pass vacuously."""
    assert len(TEMPLATES) >= 10


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_service_sets_a_start_timeout(template):
    lines = [line.strip() for line in template.read_text().splitlines()]
    timeouts = [line for line in lines if line.startswith("TimeoutStartSec=")]
    assert timeouts, (
        f"{template.name} has no TimeoutStartSec=. A hung command never fails "
        "the unit, so OnFailure never fires and later runs are skipped.")
    value = timeouts[-1].split("=", 1)[1].strip()
    assert value not in ("", "0", "infinity"), (
        f"{template.name} sets TimeoutStartSec={value}, which disables the "
        "timeout.")
