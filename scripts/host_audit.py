# /// script
# requires-python = ">=3.11"
# ///
"""Audit the fleet against what the docs say it is.

    uv run scripts/host_audit.py                 # full run, report to stdout and a file
    uv run scripts/host_audit.py --no-playbooks  # skip the slow ansible --check runs
    uv run scripts/host_audit.py --notify        # also ntfy the phone when it finds drift

Three questions, each answered by something that already knows the truth:

- **Does devices.md still describe each host?** Every probe's live value
  (a version, a MAC, RAM) must appear in that host's row. Nothing parses the
  prose: a value the row does not contain is drift, so the check survives any
  rewording of the doc.
- **Are the router's addresses where the doc says?** UniFi's client list is
  matched by MAC against every table row that names it.
- **Has config drifted from the playbooks?** Each playbook runs in --check;
  any task that would change is reported.

Read-only throughout. Anything that would change a host is listed under
"Run these yourself" rather than run. Hosts, probes and playbooks are site
data, in $SITE_DIR/audit/hosts.toml; devices.md is $SITE_DIR/docs/devices.md.
The report also goes to ${XDG_DATA_HOME:-~/.local/share}/_r_/host-audit/<date>.md.
"""
import argparse
import datetime
import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(os.environ.get("SITE_DIR") or ROOT.parent / "homelab-private")
TOKENS = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "_r_"

HIGH, MEDIUM, LOW, INFO = 3, 2, 1, 0
LABEL = {HIGH: "high", MEDIUM: "medium", LOW: "low", INFO: "info"}

# probe -> (remote command, parser from its stdout to the string devices.md records)
PROBES = {
    "os": (". /etc/os-release; echo \"$NAME|$VERSION_ID\"",
           lambda s: f"{s.split('|')[0].split()[0]} {s.split('|')[1]}"),
    "codename": (". /etc/os-release; echo $VERSION_CODENAME", str.strip),
    # "7.0.14-20-pve" is recorded as "7.0.14-pve": drop the ABI build number.
    "kernel": ("uname -r", lambda s: re.sub(r"-\d+-", "-", s.strip())),
    "ram": ("lsmem -b --summary=only | awk '/online/{print $NF}'",
            lambda s: f"{round(int(s) / 2**30)} GB"),
    "cpu": ("lscpu | grep 'Model name'",
            lambda s: re.search(r"i\d-\w+", s).group(0)),
    "pve": ("pveversion", lambda s: re.search(r"pve-manager/([\d.]+)", s).group(1)),
    "pbs": ("proxmox-backup-manager versions",
            lambda s: re.search(r"proxmox-backup-server ([\d.]+)", s).group(1)),
    "jellyfin": ("dpkg-query -W -f '${Version}' jellyfin-server",
                 lambda s: "Jellyfin " + ".".join(s.split("+")[0].split(".")[:2])),
    "macos": ("sw_vers -productVersion", lambda s: "macOS " + s.split(".")[0]),
    "darwin": ("uname -r", lambda s: "Darwin " + ".".join(s.split(".")[:2])),
    "macram": ("sysctl -n hw.memsize", lambda s: f"{round(int(s) / 2**30)} GB"),
}


def probe_cmd(name):
    if name.startswith("mac:"):
        return f"cat /sys/class/net/{name[4:]}/address", str.strip
    return PROBES[name]


# --- devices.md -------------------------------------------------------------

def row_for(devices, name):
    """The host's line in the main table, or None."""
    for line in devices.splitlines():
        if line.startswith(f"| `{name}` |"):
            return line
    return None


def fact_findings(name, row, facts):
    """facts: {probe: live value or None (probe failed)}."""
    if row is None:
        return [(LOW, name, f"no row for `{name}` in devices.md",
                 f"add a `| `{name}` | ... |` row to devices.md")]
    out = []
    for probe, value in facts.items():
        if value is None:
            out.append((INFO, name, f"probe `{probe}` gave no answer", "check by hand"))
        elif value.lower() not in row.lower():
            out.append((LOW, name, f"`{probe}` is `{value}`; devices.md row does not say so",
                        f"update the `{name}` row in devices.md to `{value}`"))
    return out


def address_findings(devices, clients):
    """clients: [(name, mac, ip)] from the router. A MAC devices.md names
    must share a table row with its live address."""
    # Tables nested in list items are indented; they count too.
    rows = [line.strip().lower() for line in devices.splitlines() if line.lstrip().startswith("|")]
    out = []
    for name, mac, ip in clients:
        mac = mac.lower()
        mine = [r for r in rows if mac in r]
        if mine and ip and not any(re.search(rf"(?<![\d.]){re.escape(ip)}(?![\d])", r) for r in mine):
            out.append((MEDIUM, name or mac, f"router has `{mac}` at `{ip}`; devices.md rows for it say otherwise",
                        "fix the Fixed IP on the router, or the address in devices.md and extra-records.json"))
    return out


# --- ansible ----------------------------------------------------------------

def changed_tasks(output):
    """(task, host, item) for each change an ansible --check run reports."""
    task, out = None, []
    for line in output.splitlines():
        if m := re.match(r"TASK \[(.*)\]", line):
            task = m.group(1)
        elif m := re.match(r"changed: \[([^\]]+)\](?: => \(item=(.*)\))?", line):
            out.append((task, m.group(1), m.group(2)))
    return out


def playbook_findings(book, rc, output):
    if rc != 0 and "PLAY RECAP" not in output:
        return [(MEDIUM, book, f"`{book} --check` did not run (rc {rc})",
                 f"run it by hand and read the error: {output.strip().splitlines()[-1:] }")]
    out = [(HIGH, host, f"`{book}`: \"{task}\" would change" + (f" ({item})" if item else ""),
            f"ansible-playbook -i \"$SITE_DIR/ansible/inventory.yml\" {book} --diff")
           for task, host, item in changed_tasks(output)]
    if re.search(r"(failed|unreachable)=[1-9]", output):
        out.append((MEDIUM, book, f"`{book} --check` reported failed or unreachable hosts",
                    "rerun with --check --diff and read the failing task"))
    return out


# --- report -----------------------------------------------------------------

def render(findings, checked, date):
    ranked = sorted(findings, key=lambda f: -f[0])
    lines = [f"# Host audit {date}", "",
             "Checked: " + ", ".join(checked) if checked else "Checked: nothing", ""]
    real = [f for f in ranked if f[0] > INFO]
    if not real:
        lines.append("**No drift.**")
    else:
        lines += ["| Rank | Where | Finding | Remediation |", "|---|---|---|---|"]
        lines += [f"| {LABEL[s]} | `{w}` | {what} | {fix} |" for s, w, what, fix in real]
    info = [f for f in ranked if f[0] == INFO]
    if info:
        lines += ["", "**Not checked:**"] + [f"- `{w}`: {what}" for _, w, what, _ in info]
    run = sorted({fix for _, _, _, fix in real if fix.startswith("ansible-playbook")})
    if run:
        lines += ["", "## Run these yourself", "", "They change hosts; read the --diff first.",
                  "", "```bash", "cd ansible"] + run + ["```"]
    return "\n".join(lines) + "\n"


def alert_text(findings, report_path):
    """The phone message: worst problems first, and where the full report is."""
    real = sorted((f for f in findings if f[0] >= MEDIUM), key=lambda f: -f[0])
    lines = [f"{LABEL[s]}: {w}: {what}" for s, w, what, _ in real[:5]]
    if len(real) > 5:
        lines.append(f"...and {len(real) - 5} more")
    return "\n".join(lines + [f"Report: {report_path}"])


# --- collection (I/O) -------------------------------------------------------

def run_on(host, probes):
    """Run every probe in one ssh call. Returns {probe: value or None}, or
    None if the host did not answer."""
    marker = "@@probe@@"
    prefix = host.get("exec", "")
    script = "; ".join(f"echo {marker}; {prefix} sh -c {sh_quote(probe_cmd(p)[0])} 2>/dev/null"
                       for p in probes)
    cmd = ["bash", "-c", script] if host.get("local") else \
          ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host["ssh"], script]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode == 255:
        return None
    parts = r.stdout.split(marker + "\n")[1:]
    facts = {}
    for p, raw in zip(probes, parts + [""] * len(probes)):
        try:
            facts[p] = probe_cmd(p)[1](raw.strip()) if raw.strip() else None
        except (AttributeError, ValueError, IndexError):
            facts[p] = None
    return facts


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def pinned_get(host, sha256, path, headers):
    """GET over TLS to a self-signed host, pinned by certificate fingerprint.
    The router's certificate is not a CA, so it cannot be loaded as a trust
    root; instead the certificate presented on THIS connection is checked
    before the request, and its API key, is sent."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # replaced by the fingerprint check below
    conn = http.client.HTTPSConnection(host, context=ctx, timeout=10)
    try:
        conn.connect()
        seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if seen != sha256.replace(":", "").lower():
            raise ssl.SSLError(f"{host} certificate is {seen}, not the pinned one")
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        if r.status != 200:
            raise OSError(f"{path}: HTTP {r.status}")
        return json.load(r)
    finally:
        conn.close()


def unifi_clients(router):
    key = TOKENS / "unifi" / "token"
    if not key.exists() or not router:
        return None
    headers = {"X-API-KEY": key.read_text().strip()}
    base = "/proxy/network/integration/v1/sites"
    def get(path):
        return pinned_get(router["host"], router["cert_sha256"], path, headers)
    try:
        return [(c.get("name"), c.get("macAddress", ""), c.get("ipAddress"))
                for site in get(base)["data"]
                for c in get(f"{base}/{site['id']}/clients?limit=200")["data"]]
    except OSError:  # ssl.SSLError is an OSError
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-playbooks", action="store_true")
    ap.add_argument("--notify", action="store_true",
                    help="send an ntfy message when anything ranks medium or high")
    args = ap.parse_args()

    conf = tomllib.loads((SITE / "audit" / "hosts.toml").read_text())
    devices = (SITE / "docs" / "devices.md").read_text()
    findings, checked = [], []

    for host in conf["host"]:
        facts = run_on(host, host["probes"])
        if facts is None:
            rank = HIGH if host.get("always_on") else INFO
            findings.append((rank, host["name"], "did not answer over ssh",
                             f"ssh {host.get('ssh', host['name'])} -- is it up?"))
            continue
        checked.append(host["name"])
        findings += fact_findings(host["name"], row_for(devices, host["name"]), facts)

    clients = unifi_clients(conf.get("router"))
    if clients is None:
        findings.append((INFO, "router", "UniFi client list unavailable", "check the unifi token and the pinned cert_sha256 in hosts.toml"))
    else:
        checked.append(f"router ({len(clients)} clients)")
        findings += address_findings(devices, clients)

    if not args.no_playbooks:
        inv = SITE / "ansible" / "inventory.yml"
        for book in conf.get("playbooks", []):
            r = subprocess.run(["ansible-playbook", "-i", str(inv), book, "--check"],
                               cwd=ROOT / "ansible", capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=900)
            checked.append(book)
            findings += playbook_findings(book, r.returncode, r.stdout + r.stderr)

    date = datetime.date.today().isoformat()
    report = render(findings, checked, date)
    out = TOKENS / "host-audit"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{date}.md"
    path.write_text(report)
    print(report)
    drift = any(f[0] >= MEDIUM for f in findings)
    if drift and args.notify:
        import notify  # beside this script
        # notify.py defaults to the server's checkout; this runs on the laptop,
        # whose copy of the ntfy stack's .env is in this repo (as vault-commit.sh).
        os.environ.setdefault("HOMELAB_NOTIFY_ENV", str(ROOT / "stacks" / "ntfy" / ".env"))
        if notify.deliver("host audit: drift found", alert_text(findings, path), notify.HIGH) != 0:
            print("could not notify through ntfy", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
