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


def render(report, now):
    """Prometheus text for every thin pool in an `lvs` JSON report."""
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
    lines += ["# HELP lvm_thin_last_run_timestamp_seconds When this last succeeded.",
              "# TYPE lvm_thin_last_run_timestamp_seconds gauge",
              f"lvm_thin_last_run_timestamp_seconds {int(now)}"]
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
    print("self-test passed")


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    report = json.loads(subprocess.run(LVS, check=True, capture_output=True, text=True).stdout)
    text = render(report, time.time())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".lvm_thin.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)


if __name__ == "__main__":
    main()
