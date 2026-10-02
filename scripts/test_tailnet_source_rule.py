"""tailnet-source-rule.sh against a fake iptables that plays Tailscale's boot.

The race it covers: at boot the script ran before tailscaled had installed its
POSTROUTING jump, found nothing to sit above, reported ok, and Tailscale then
put its jump at position 1. A test that only checks the script's exit code
cannot see that, so this one lets "Tailscale" arrive after the script's own
reads, and again after the script exits, and checks the final chain.
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("tailnet-source-rule.sh")

OURS = "-A POSTROUTING -s 100.64.0.0/10 -d 10.201.0.0/16 -j ACCEPT"
TS_JUMP = "-A POSTROUTING -j ts-postrouting"
DOCKER = "-A POSTROUTING -o br-edge -m addrtype --src-type LOCAL -j MASQUERADE"

# Keeps the chain in a file. Tailscale "starts" on the Nth `-S` read: its jump
# goes in at position 1, as tailscaled does it.
FAKE_IPTABLES = r'''#!/usr/bin/env python3
import os, sys
state = os.environ["FAKE_STATE"]
rules = open(state).read().splitlines()
args = sys.argv[1:]
reads = os.path.join(os.path.dirname(state), "reads")
# args: -t nat -S|-D|-I POSTROUTING [1] RULE...
if "-S" in args:
    n = int(open(reads).read()) + 1 if os.path.exists(reads) else 1
    open(reads, "w").write(str(n))
    jump = "-A POSTROUTING -j ts-postrouting"
    if n == int(os.environ["TS_AFTER_READS"]) and jump not in rules:
        rules.insert(0, jump)
    print("-P POSTROUTING ACCEPT")
    print("\n".join(rules))
elif "-D" in args:
    rule = "-A POSTROUTING " + " ".join(args[4:])
    if rule not in rules:
        sys.exit(1)
    rules.remove(rule)
elif "-I" in args:
    rules.insert(int(args[4]) - 1, "-A POSTROUTING " + " ".join(args[5:]))
open(state, "w").write("\n".join(rules) + "\n" if rules else "")
'''


def run(tmp_path, tailscaled_active, ts_after_reads):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "iptables").write_text(FAKE_IPTABLES)
    (bin_dir / "systemctl").write_text(
        f"#!/bin/sh\nexit {0 if tailscaled_active else 3}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    state = tmp_path / "chain"
    state.write_text(DOCKER + "\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "FAKE_STATE": str(state), "TS_AFTER_READS": str(ts_after_reads),
           "TAILNET_SOURCE_POLL": "0"}
    result = subprocess.run(["bash", str(SCRIPT)], env=env,
                            capture_output=True, text=True, timeout=30)
    rules = state.read_text().splitlines()
    if tailscaled_active and TS_JUMP not in rules:
        rules.insert(0, TS_JUMP)  # Tailscale arrives after the script exits
    return result, rules


def test_waits_for_tailscales_jump_then_sits_above_it(tmp_path):
    result, rules = run(tmp_path, tailscaled_active=True, ts_after_reads=6)
    assert result.returncode == 0, result.stderr
    assert rules[0] == OURS, rules
    assert TS_JUMP in rules and DOCKER in rules


def test_does_not_wait_for_a_jump_when_tailscaled_is_stopped(tmp_path):
    result, rules = run(tmp_path, tailscaled_active=False, ts_after_reads=10**6)
    assert result.returncode == 0, result.stderr
    assert rules == [OURS, DOCKER]
