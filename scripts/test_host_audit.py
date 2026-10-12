"""Tests for host_audit.py: the matching, parsing and ranking, plus the pinned
TLS path against a local server. Nothing here reaches a real host."""
import hashlib
import http.server
import ssl
import subprocess
import threading

import pytest

import host_audit as h

DEVICES = """\
| Name | What | Address |
|---|---|---|
| `pve` | Proxmox VE 9.2.21, 32 GB, i5-9500 | 10.0.0.10 · `aa:bb:cc:00:00:01` |
| `box` | Debian 13 | 10.0.0.1 · `aa:bb:cc:00:00:02` |

- **Pinned:**

  | Name | MAC | Pinned to |
  |---|---|---|
  | `lamp` | `aa:bb:cc:00:00:03` | 10.0.7.127 |
"""


def test_row_for_finds_the_main_table_row_only():
    assert h.row_for(DEVICES, "pve").startswith("| `pve` |")
    assert h.row_for(DEVICES, "lamp") is None  # indented: not a main-table row
    assert h.row_for(DEVICES, "nosuch") is None


def test_matching_facts_are_not_drift():
    row = h.row_for(DEVICES, "pve")
    assert h.fact_findings("pve", row, {"pve": "9.2.21", "ram": "32 GB", "cpu": "i5-9500"}) == []


def test_case_does_not_matter():
    assert h.fact_findings("box", h.row_for(DEVICES, "box"), {"os": "DEBIAN 13"}) == []


def test_a_value_the_row_lacks_is_low_drift_naming_the_value():
    [(rank, where, what, fix)] = h.fact_findings("pve", h.row_for(DEVICES, "pve"), {"pve": "9.2.22"})
    assert (rank, where) == (h.LOW, "pve")
    assert "9.2.22" in what and "9.2.22" in fix


def test_a_probe_with_no_answer_is_not_checked_not_drift():
    [(rank, *_)] = h.fact_findings("pve", h.row_for(DEVICES, "pve"), {"pve": None})
    assert rank == h.INFO


def test_a_host_with_no_row_is_reported():
    [(rank, _, what, _)] = h.fact_findings("ghost", None, {"os": "Debian 13"})
    assert rank == h.LOW and "no row" in what


def test_address_in_an_indented_table_matches():
    # The bug the first real run found: a pinned-address table inside a list
    # item is indented, and was skipped, so a correct address read as drift.
    assert h.address_findings(DEVICES, [("lamp", "AA:BB:CC:00:00:03", "10.0.7.127")]) == []


def test_address_drift_is_medium():
    [(rank, where, what, _)] = h.address_findings(DEVICES, [("box", "aa:bb:cc:00:00:02", "10.0.0.99")])
    assert (rank, where) == (h.MEDIUM, "box") and "10.0.0.99" in what


def test_an_address_prefix_is_not_a_match():
    # 10.0.0.1 is a prefix of 10.0.0.10; pve's row must not vouch for it.
    [_] = h.address_findings(DEVICES, [("pve", "aa:bb:cc:00:00:01", "10.0.0.1")])


def test_macs_the_doc_does_not_name_are_ignored():
    assert h.address_findings(DEVICES, [("phone", "de:ad:be:ef:00:00", "10.0.0.50")]) == []


@pytest.mark.parametrize("probe,raw,want", [
    ("kernel", "7.0.14-20-pve", "7.0.14-pve"),
    ("kernel", "6.12.111+deb13-cloud-amd64", "6.12.111+deb13-cloud-amd64"),
    ("ram", "34359738368", "32 GB"),
    ("os", "Debian GNU/Linux|13", "Debian 13"),
    ("os", "Pop!_OS|24.04", "Pop!_OS 24.04"),
    ("cpu", "Model name:   Intel(R) Core(TM) i5-9500 CPU @ 3.00GHz", "i5-9500"),
    ("pve", "pve-manager/9.2.21/4f6e0ac86f9e8c7f (running kernel: 7.0.14-20-pve)", "9.2.21"),
    ("pbs", "proxmox-backup-server 4.2.8-1 running version: 4.2.8", "4.2.8"),
    ("jellyfin", "12.2+deb13", "Jellyfin 12.2"),
    ("macos", "15.6.1", "macOS 15"),
    ("darwin", "24.6.0", "Darwin 24.6"),
    ("macram", "34359738368", "32 GB"),
])
def test_probe_parsers(probe, raw, want):
    assert h.probe_cmd(probe)[1](raw) == want


def test_mac_probe_reads_the_named_interface():
    cmd, parse = h.probe_cmd("mac:eth0")
    assert "/sys/class/net/eth0/address" in cmd and parse("bc:24:11:b5:a8:55\n") == "bc:24:11:b5:a8:55"


ANSIBLE = """\
TASK [Firewall files, written from the site repo] ******
changed: [pve] => (item=/etc/pve/firewall/200.fw)
skipping: [pve] => (item=/etc/pve/firewall/101.fw)
TASK [Journal cap] ******
changed: [homelab]
PLAY RECAP *****
pve : ok=3 changed=1 unreachable=0 failed=0
homelab : ok=3 changed=1 unreachable=0 failed=0
"""


def test_changed_tasks_with_and_without_items():
    assert h.changed_tasks(ANSIBLE) == [
        ("Firewall files, written from the site repo", "pve", "/etc/pve/firewall/200.fw"),
        ("Journal cap", "homelab", None)]


def test_playbook_drift_is_high_with_the_apply_command():
    found = h.playbook_findings("pve-host.yml", 0, ANSIBLE)
    assert [f[0] for f in found] == [h.HIGH, h.HIGH]
    assert "200.fw" in found[0][2]
    assert found[0][3].startswith("ansible-playbook") and "pve-host.yml --diff" in found[0][3]


def test_a_clean_playbook_is_silent():
    assert h.playbook_findings("x.yml", 0, "PLAY RECAP\npve : ok=3 changed=0 unreachable=0 failed=0\n") == []


def test_a_playbook_that_did_not_run_is_reported():
    [(rank, _, what, _)] = h.playbook_findings("x.yml", 4, "ERROR! the inventory is missing\n")
    assert rank == h.MEDIUM and "did not run" in what


def test_failed_hosts_are_reported():
    found = h.playbook_findings("x.yml", 2, "PLAY RECAP\npve : ok=1 changed=0 unreachable=0 failed=1\n")
    assert [f[0] for f in found] == [h.MEDIUM]


def test_render_ranks_worst_first_and_lists_commands_to_run():
    findings = [(h.LOW, "box", "doc drift", "update the row"),
                (h.HIGH, "pve", "firewall drift", "ansible-playbook -i inv pve-host.yml --diff"),
                (h.INFO, "offbox", "did not answer over ssh", "ssh offbox")]
    out = h.render(findings, ["pve", "box"], "2026-10-12")
    assert out.index("firewall drift") < out.index("doc drift")
    assert "## Run these yourself" in out and "pve-host.yml --diff" in out.split("Run these yourself")[1]
    assert "**Not checked:**" in out and "`offbox`" in out.split("Not checked")[1]


def test_render_with_nothing_wrong_says_so():
    out = h.render([(h.INFO, "offbox", "did not answer over ssh", "")], ["pve"], "2026-10-12")
    assert "**No drift.**" in out and "Run these yourself" not in out


# --- the pinned TLS path ------------------------------------------------------

@pytest.fixture
def tls_server(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=router", "-keyout", key, "-out", crt],
                   check=True, capture_output=True)
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("X-API-KEY"))
            body = b'{"data": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(crt.read_text())).hexdigest()
    yield f"127.0.0.1:{srv.server_port}", fp, seen
    srv.shutdown()


def test_pinned_get_sends_the_key_to_the_pinned_cert(tls_server):
    host, fp, seen = tls_server
    assert h.pinned_get(host, fp, "/x", {"X-API-KEY": "secret"}) == {"data": []}
    assert seen == ["secret"]


def test_pinned_get_never_sends_the_key_to_another_cert(tls_server):
    host, _, seen = tls_server
    with pytest.raises(ssl.SSLError, match="not the pinned one"):
        h.pinned_get(host, "00" * 32, "/x", {"X-API-KEY": "secret"})
    assert seen == []


def test_alert_text_lists_worst_first_skips_low_and_names_the_report():
    findings = [(h.LOW, "box", "doc drift", ""), (h.MEDIUM, "lamp", "address drift", ""),
                (h.HIGH, "pve", "firewall drift", "")]
    text = h.alert_text(findings, "/r/2026-10-12.md")
    assert text.splitlines() == ["high: pve: firewall drift", "medium: lamp: address drift",
                                 "Report: /r/2026-10-12.md"]


def test_alert_text_caps_the_list():
    findings = [(h.HIGH, f"h{i}", "x", "") for i in range(8)]
    assert "...and 3 more" in h.alert_text(findings, "r")
