#!/usr/bin/env python3
"""Write LVM thin-pool usage for node_exporter's textfile collector.

    lvm_thin_metrics.py [OUTPUT]   default /var/lib/prometheus/node-exporter/lvm_thin.prom
    lvm_thin_metrics.py --self-test

node_exporter has no LVM collector, and a full thin pool is the failure that
hits every guest on it at once: writes to any thin volume error out, data and
metadata alike. `local-lvm` on pve is shared by every VM's disk, so one guest
filling its volumes (kennel's Incus pool, for one) can stall the rest.

Run by pve-lvm-thin.timer. Standard library only: it runs as root on pve.
The file is written to a temp name and renamed, so a scrape never reads half
of it. lvm_thin_last_run_timestamp_seconds lets an alert tell "the pool is
fine" from "this stopped running".
"""

import json
import os
import subprocess
import sys
import tempfile
import time

DEFAULT_OUT = "/var/lib/prometheus/node-exporter/lvm_thin.prom"
LVS = ["lvs", "--reportformat", "json", "--units", "b", "--nosuffix",
       "-o", "vg_name,lv_name,lv_attr,data_percent,metadata_percent,lv_size"]

# kennel's Incus pool sits inside its own disk, out of pve's sight; read it
# through the guest agent so no credential lives on the untrusted VM.
KENNEL_VMID, KENNEL_POOL = 200, ("incus", "IncusThinPool")
KENNEL_STAMP = "/var/lib/homelab/kennel-lvm-thin.ok"


def guest_report():
    """(report or None, last success epoch). A failure keeps the old stamp,
    so the stale alert fires rather than the series vanishing."""
    try:
        r = subprocess.run(["qm", "guest", "exec", str(KENNEL_VMID), "--timeout", "20", "--", *LVS],
                           check=True, capture_output=True, text=True, timeout=40)
        rep = json.loads(json.loads(r.stdout)["out-data"])
        os.makedirs(os.path.dirname(KENNEL_STAMP), exist_ok=True)
        open(KENNEL_STAMP, "w").close()
    except Exception as e:
        print(f"kennel pool not read: {e}", file=sys.stderr)
        rep = None
    try:
        return rep, os.path.getmtime(KENNEL_STAMP)
    except OSError:
        return rep, 0


def render(report, now, guest=None):
    """Prometheus text for every thin pool in an `lvs` JSON report.
    guest: (report or None, last success epoch) for kennel's Incus pool."""
    lines = [
        "# HELP lvm_thin_data_percent Thin pool data space used, percent.",
        "# TYPE lvm_thin_data_percent gauge",
        "# HELP lvm_thin_metadata_percent Thin pool metadata space used, percent.",
        "# TYPE lvm_thin_metadata_percent gauge",
        "# HELP lvm_thin_size_bytes Thin pool size.",
        "# TYPE lvm_thin_size_bytes gauge",
    ]
    for lv in (lv for r in report["report"] for lv in r["lv"]):
        if not lv["lv_attr"].startswith("t"):   # 't' = thin pool
            continue
        labels = f'vg="{lv["vg_name"]}",lv="{lv["lv_name"]}"'
        lines.append(f'lvm_thin_data_percent{{{labels}}} {float(lv["data_percent"])}')
        lines.append(f'lvm_thin_metadata_percent{{{labels}}} {float(lv["metadata_percent"])}')
        lines.append(f'lvm_thin_size_bytes{{{labels}}} {int(lv["lv_size"])}')
    if guest and guest[0]:
        for lv in (lv for r in guest[0]["report"] for lv in r["lv"]):
            # Untrusted VM: only its pool, fixed labels, values must parse.
            if (lv["vg_name"], lv["lv_name"]) == KENNEL_POOL:
                labels = f'vg="incus",lv="IncusThinPool",vmid="{KENNEL_VMID}"'
                lines.append(f'lvm_thin_data_percent{{{labels}}} {float(lv["data_percent"])}')
                lines.append(f'lvm_thin_metadata_percent{{{labels}}} {float(lv["metadata_percent"])}')
    lines += ["# HELP lvm_thin_last_run_timestamp_seconds When this last succeeded.",
              "# TYPE lvm_thin_last_run_timestamp_seconds gauge",
              f"lvm_thin_last_run_timestamp_seconds {int(now)}"]
    if guest:
        lines.append(f'lvm_thin_last_run_timestamp_seconds{{vmid="{KENNEL_VMID}"}} {int(guest[1])}')
    return "\n".join(lines) + "\n"


def self_test():
    report = {"report": [{"lv": [
        {"vg_name": "pve", "lv_name": "data", "lv_attr": "twi-aotz--",
         "data_percent": "8.50", "metadata_percent": "0.53", "lv_size": "883685326848"},
        {"vg_name": "pve", "lv_name": "vm-200-disk-0", "lv_attr": "Vwi-aotz--",
         "data_percent": "5.00", "metadata_percent": "", "lv_size": "34359738368"},
    ]}]}
    out = render(report, 1_800_000_000)
    assert 'lvm_thin_data_percent{vg="pve",lv="data"} 8.5' in out, out
    assert 'lvm_thin_metadata_percent{vg="pve",lv="data"} 0.53' in out, out
    assert "vm-200-disk-0" not in out, "thin volumes are not pools"
    assert out.endswith("lvm_thin_last_run_timestamp_seconds 1800000000\n"), out
    g = {"report": [{"lv": [
        {"vg_name": "incus", "lv_name": "IncusThinPool", "lv_attr": "twi-aotz--",
         "data_percent": "7.12", "metadata_percent": "12.93", "lv_size": "68581064704"},
        {"vg_name": 'x"}', "lv_name": "evil", "lv_attr": "twi-aotz--",
         "data_percent": "1", "metadata_percent": "1", "lv_size": "1"}]}]}
    out = render(report, 1_800_000_000, (g, 1_799_999_000))
    assert 'lvm_thin_data_percent{vg="incus",lv="IncusThinPool",vmid="200"} 7.12' in out, out
    assert "evil" not in out, "only kennel's own pool is taken from the guest"
    assert out.endswith('lvm_thin_last_run_timestamp_seconds{vmid="200"} 1799999000\n'), out
    out = render(report, 1_800_000_000, (None, 0))
    assert "incus" not in out and out.endswith('{vmid="200"} 0\n'), out
    print("self-test passed")


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    report = json.loads(subprocess.run(LVS, check=True, capture_output=True, text=True).stdout)
    text = render(report, time.time(), guest_report())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".lvm_thin.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)


if __name__ == "__main__":
    main()
