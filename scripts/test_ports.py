# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Run: uv run --with pytest pytest scripts/test_ports.py -v

Tests operate on the JSON `docker compose config` produces, so they cover
formatting and sorting. The parsing itself is Compose's job.
"""

import subprocess

from ports import Mapping, parse_ports, render, stack_config

# Exactly what `docker compose config --format json` emits. Note that the
# dynamic port (compose `ports: [7000]`) has NO "published" key.
CONFIG = {
    "services": {
        "web": {
            "container_name": "demo-web",
            "ports": [
                {"mode": "ingress", "target": 80, "published": "8080", "protocol": "tcp"},
                {"mode": "ingress", "target": 9090, "published": "9090",
                 "protocol": "tcp", "host_ip": "127.0.0.1"},
                {"mode": "ingress", "target": 6000, "published": "6000", "protocol": "udp"},
            ],
        },
        "dynamic": {
            "ports": [{"mode": "ingress", "target": 7000, "protocol": "tcp"}],
        },
        "internal": {"image": "redis"},
    }
}


def test_dynamic_ports_are_excluded():
    """A container-only port reserves no host port, so it belongs in no registry."""
    assert all(m.published is not None for m in parse_ports(CONFIG))
    assert "dynamic" not in [m.label for m in parse_ports(CONFIG)]


def test_label_falls_back_to_service_name():
    cfg = {"services": {"svc": {"ports": [
        {"target": 80, "published": "8080", "protocol": "tcp"}]}}}
    assert parse_ports(cfg)[0].label == "svc"


def test_container_name_wins_over_service_name():
    assert parse_ports(CONFIG)[0].label == "demo-web"


def test_sorts_numerically_not_lexically():
    """443 must come before 3000. String sort would put 3000 first."""
    cfg = {"services": {"a": {"ports": [
        {"target": 3000, "published": "3000", "protocol": "tcp"},
        {"target": 443, "published": "443", "protocol": "tcp"}]}}}
    assert [m.published for m in parse_ports(cfg)] == [443, 3000]


def test_udp_and_host_ip_are_annotated_not_dropped():
    """Two services can share a port number across tcp/udp. Hiding the
    protocol would make the registry claim a false conflict."""
    out = render([Mapping(6000, 6000, "udp", None, "a"),
                  Mapping(9090, 9090, "tcp", "127.0.0.1", "b")])
    assert "6000:6000/udp a" in out
    assert "127.0.0.1:9090:9090 b" in out


def test_empty_input_renders_without_crashing():
    assert "# Ports" in render([])


def test_stack_config_handles_empty_stderr_on_failure(monkeypatch, tmp_path):
    """A non-zero exit with empty/whitespace-only stderr must skip the stack,
    not raise IndexError and abort the whole run. Real `docker compose config`
    always writes a message on failure, so this is reproduced by monkeypatching
    subprocess.run rather than shelling out to a real failing stack."""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="   ")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert stack_config(tmp_path) is None
