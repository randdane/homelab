#!/usr/bin/env python3
"""Check both firewall layers that keep VM 200 `kennel` internet-only.

    kennel_fw_check.py [OUTPUT]   default /var/lib/prometheus/node-exporter/kennel_fw.prom
    kennel_fw_check.py --self-test

kennel runs untrusted agents. Two independent layers keep it off the LAN:
pve's firewall for VM 200 (200.fw) and dream's zone firewall policies for the `agents`
VLAN (homelab-private networks.md). The playbooks check both when they run;
nothing noticed drift between runs. This runs hourly on pve (pve-kennel-fw.timer)
and writes kennel_fw_ok 0/1 plus a timestamp for node_exporter, so Grafana
alerts both on a failed check and on the check not running. The reasons go
to the journal: `journalctl -u pve-kennel-fw` on pve.

Deliberately not a probe from inside kennel: the VM is untrusted and could lie.

Standard library only: it runs as root on pve, which has no uv.
"""

import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time

WANT = "/etc/homelab/kennel-fw.json"   # pve-host.yml, from $SITE_DIR/pve/kennel-fw.json
UNIFI_KEY = "/etc/homelab/unifi.key"   # made by hand, mode 600
DEFAULT_OUT = "/var/lib/prometheus/node-exporter/kennel_fw.prom"
# Every rule field is compared except these, so a new restriction (schedule,
# negation, source port...) shows up as a difference.
META = {"_id", "hits", "last_hit", "origin_id"}   # hits, last_hit: counters; origin_id is the classic rule it came from

# The site's expectation, kennel-fw.json:
#   vmid, dream (address), dream_cert_sha256 (hex sha256 of its DER cert),
#   zone (zone_key holding the network), policies {name: [every field of each
#   policy as the v2 API returns it]; "@group", "@network" and "@zone:<key>"
#   stand for the ids of the group, network and zones},
#   group {name, group_type, members}, network {name, vlan, ...}.
# Sample: self_test(). Live values: homelab-private networks.md.


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60).stdout


def check_pve(fw_live, fw_want, net_lines, fw_status, loaded, fw_path="the live .fw"):
    """Layer 1. loaded: (options dict, rule count, ipset count) from pvesh."""
    bad = []
    if fw_live.rstrip("\n") != fw_want.rstrip("\n"):
        bad.append(f"{fw_path} differs from the copy pve-host.yml installed")
    opts, nrules, nipset = loaded
    want_rules = len(re.findall(r"(?m)^(?:IN|OUT) ", fw_want))
    m = re.search(r"(?s)\[IPSET private\]\n(.*?)(?:\n\n|\Z)", fw_want)
    want_ipset = len(re.findall(r"(?m)^\S.*$", m.group(1))) if m else -1
    if (int(opts.get("enable", 0)), opts.get("policy_in"), int(opts.get("dhcp", 0))) != (1, "DROP", 1):
        bad.append(f"loaded options wrong: {opts}")
    if nrules != want_rules or nipset != want_ipset:
        bad.append(f"loaded {nrules} rules/{nipset} ipset, file has {want_rules}/{want_ipset}")
    nets = [l for l in net_lines if re.match(r"net\d+:", l)]
    if len(nets) != 1 or not nets[0].startswith("net0:"):
        bad.append(f"NICs: {nets}")
    else:
        parts = [p.split("=", 1) for p in nets[0].split(": ", 1)[1].split(",")]
        kv = dict(p for p in parts if len(p) == 2)
        if len(kv) != len(parts):   # a repeated key, or a part with no "="
            bad.append(f"net0 does not parse cleanly: {nets[0]}")
        elif kv.get("bridge") != "vmbr0" or kv.get("tag") != "5" or kv.get("firewall") != "1" or "trunks" in kv:
            bad.append(f"net0 not vmbr0/tag=5/firewall=1/no trunks: {nets[0]}")
    if fw_status.strip() != "Status: enabled/running":
        bad.append(f"pve-firewall: {fw_status.strip()!r}")   # also catches "pending changes"
    return bad


def resolve(v, ids):
    """Swap the placeholders kennel-fw.json uses for dream's ids, at any depth."""
    if isinstance(v, dict):
        return {k: resolve(x, ids) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve(x, ids) for x in v]
    return ids.get(v, v) if isinstance(v, str) else v


def check_dream(want, policies, groups, networks, zones):
    """Layer 2, from the UniFi zone-based firewall-policies and zone lists, and
    the rest/firewallgroup and networkconf lists. Dream moved from the classic
    LAN In rules to zones in Network 11 (2026-11-11): each rule became one
    policy per destination zone, all from the zone holding `agents`."""
    bad = []
    GROUP, NETWORK, POLICIES = want["group"], want["network"], want["policies"]
    g = [x for x in groups if x.get("name") == GROUP["name"]]
    n = [x for x in networks if x.get("name") == NETWORK["name"]]
    if len(g) != 1 or len(n) != 1:
        return [f"want one group {GROUP['name']} and one network {NETWORK['name']}, got {len(g)}/{len(n)}"]
    g, n = g[0], n[0]
    if g.get("group_type") != GROUP["group_type"] or set(g.get("group_members", [])) != set(GROUP["members"]):
        bad.append(f"group {GROUP['name']} is {g.get('group_type')} {sorted(g.get('group_members', []))}")
    if {k: n.get(k) for k in NETWORK} != NETWORK:
        bad.append(f"network {NETWORK['name']} is {({k: n.get(k) for k in NETWORK})}")
    keys = [z.get("zone_key") for z in zones]
    if len(set(keys)) != len(keys):
        return bad + [f"zone keys not unique: {keys}"]
    ids = {"@group": g["_id"], "@network": n["_id"], **{f"@zone:{z['zone_key']}": z["_id"] for z in zones}}
    src = [z for z in zones if n["_id"] in z.get("network_ids", [])]
    if len(src) != 1 or src[0].get("zone_key") != want["zone"]:
        bad.append(f"network {NETWORK['name']} is in zones {[z.get('zone_key') for z in src]}, want {want['zone']}")
    for name, want_list in POLICIES.items():
        got = sorted(([{k: v for k, v in p.items() if k not in META} for p in policies if p.get("name") == name]),
                     key=lambda p: json.dumps(p, sort_keys=True))
        exp = sorted(resolve(want_list, ids), key=lambda p: json.dumps(p, sort_keys=True))
        # Exact, empty values and types included (True is not 1).
        if json.dumps(got, sort_keys=True) != json.dumps(exp, sort_keys=True):
            bad.append(f"policy {name} differs: want {len(exp)}, have {len(got)}; "
                       f"have {json.dumps(got, sort_keys=True)}")
    # The block must reach every zone that holds a network: a zone added later
    # (a network moved to Dmz, say) would otherwise be open to agents.
    last = max(POLICIES, key=lambda k: max(p["index"] for p in POLICIES[k]))
    covered = {ids.get(p["destination"]["zone_id"]) for p in POLICIES[last]}
    for z in zones:
        if z.get("network_ids") and z["_id"] not in covered:
            bad.append(f"zone {z.get('zone_key')} holds networks but {last} does not cover it")
    # Anything else enabled in the same zone pair, ahead of the block, could
    # let agents through first: allow none (Codex review).
    for b in resolve(POLICIES[last], ids):
        pair = (b["source"]["zone_id"], b["destination"]["zone_id"])
        for p in policies:
            if (p.get("enabled") is not False and p.get("name") not in POLICIES
                    and (p.get("source", {}).get("zone_id"), p.get("destination", {}).get("zone_id")) == pair
                    and p.get("index", -1) < b["index"]):
                bad.append(f"policy {p.get('name')} ({p.get('index')}) runs before {last} in {pair}")
    return bad


def unifi(host, path, key, pin, v2=False):
    """GET from dream. The key is sent only after the cert matches the pin."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # self-signed: pinned below
    c = http.client.HTTPSConnection(host, timeout=20, context=ctx)
    c.connect()
    if hashlib.sha256(c.sock.getpeercert(binary_form=True)).hexdigest() != pin:
        raise RuntimeError(f"{host}'s TLS cert does not match the pin; not sending the key")
    url = f"/proxy/network/v2/api/site/default/{path}" if v2 else f"/proxy/network/api/s/default/rest/{path}"
    c.request("GET", url, headers={"X-API-KEY": key})
    r = c.getresponse()
    if r.status != 200:
        raise RuntimeError(f"{path}: HTTP {r.status}")
    d = json.loads(r.read())
    return d if v2 else d["data"]


def render(ok, now):
    return ("# HELP kennel_fw_ok 1 if both of kennel's firewall layers match what is recorded.\n"
            "# TYPE kennel_fw_ok gauge\n"
            f"kennel_fw_ok {int(ok)}\n"
            "# HELP kennel_fw_last_run_timestamp_seconds When the check last ran to the end.\n"
            "# TYPE kennel_fw_last_run_timestamp_seconds gauge\n"
            f"kennel_fw_last_run_timestamp_seconds {int(now)}\n")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    bad = []
    try:
        want = json.load(open(WANT))
        vmid, node = want["vmid"], os.uname().nodename
        get = lambda p: json.loads(run("pvesh", "get", p, "--output-format", "json"))
        fw = f"/nodes/{node}/qemu/{vmid}/firewall"
        live = f"/etc/pve/firewall/{vmid}.fw"
        bad += check_pve(open(live).read(), open(f"/etc/homelab/fw-want/{vmid}.fw").read(),
                         run("qm", "config", str(vmid)).splitlines(), run("pve-firewall", "status"),
                         (get(f"{fw}/options"), len(get(f"{fw}/rules")), len(get(f"{fw}/ipset/private"))), live)
        if int(get("/cluster/firewall/options").get("enable", 0)) != 1:
            bad.append("datacenter firewall is off, so VM rules do nothing")
    except Exception as e:
        bad.append(f"pve layer not read: {e!r}")
    try:
        key = open(UNIFI_KEY).read().strip()
        api = lambda p, v2=False: unifi(want["dream"], p, key, want["dream_cert_sha256"].lower(), v2)
        bad += check_dream(want, api("firewall-policies", True), api("firewallgroup"), api("networkconf"),
                           api("firewall/zone", True))
    except Exception as e:
        bad.append(f"router layer not read: {e!r}")
    for b in bad:
        print(b)
    print("ok" if not bad else f"{len(bad)} problem(s)")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".kennel_fw.")
    with os.fdopen(fd, "w") as f:
        f.write(render(not bad, time.time()))
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    return 1 if bad else 0


def self_test():
    fw = "[OPTIONS]\nenable: 1\n\n[IPSET private]\n10.0.0.0/8\nfc00::/7\n\n[RULES]\nIN ACCEPT -p tcp\nOUT REJECT -dest +private\n"
    good = ({"enable": 1, "policy_in": "DROP", "dhcp": 1}, 2, 2)
    net = ["net0: virtio=BC:24:11:BF:B1:FF,bridge=vmbr0,firewall=1,tag=5"]
    st = "Status: enabled/running\n"
    assert check_pve(fw, fw + "\n", net, st, good) == []
    assert check_pve(fw.replace("REJECT", "ACCEPT"), fw, net, st, good)
    assert check_pve(fw, fw, net, st, (good[0], 1, 2)), "a rule that did not load"
    assert check_pve(fw, fw, ["net0: virtio=x,bridge=vmbr0,firewall=1"], st, good), "untagged"
    assert check_pve(fw, fw, [net[0] + ",trunks=1;5"], st, good), "trunks"
    assert check_pve(fw, fw, net + ["net1: virtio=y,bridge=vmbr0"], st, good), "second NIC"
    assert check_pve(fw, fw, [net[0] + ",tag=7"], st, good), "repeated key"
    assert check_pve(fw, fw, [net[0] + ",junk"], st, good), "part with no ="
    assert check_pve(fw, fw, net, "Status: enabled/running (pending changes)", good)

    pol = lambda name, act, idx, dz, **kw: {
        "action": act, "enabled": True, "index": idx, "name": name, "predefined": False, "protocol": "all",
        "connection_states": ["NEW", "INVALID"], "schedule": {"mode": "ALWAYS"},
        "source": {"zone_id": "@zone:internal", "network_ids": ["@network"], "matching_target": "NETWORK"},
        "destination": {"zone_id": dz, "ip_group_id": "@group", "matching_target": "IP"}, **kw}
    dzs = ["@zone:internal", "@zone:external"]
    want = {"zone": "internal", "policies": {
        "allow-dns": [pol("allow-dns", "ALLOW", 10000, z, protocol="tcp_udp") for z in dzs],
        "block-private": [pol("block-private", "BLOCK", 10001, z) for z in dzs]},
        "group": {"name": "rfc1918", "group_type": "address-group",
                  "members": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]},
        "network": {"name": "agents", "vlan": 5, "vlan_enabled": True, "ip_subnet": "10.9.5.1/24"}}
    groups = [{"_id": "G", "name": "rfc1918", "group_type": "address-group", "site_id": "s",
               "group_members": ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]}]
    nets = [{"_id": "N", "name": "agents", "vlan": 5, "vlan_enabled": True, "ip_subnet": "10.9.5.1/24"}]
    zones = [{"_id": "ZI", "zone_key": "internal", "network_ids": ["L", "N"]},
             {"_id": "ZE", "zone_key": "external", "network_ids": ["W"]},
             {"_id": "ZG", "zone_key": "gateway", "network_ids": []}]
    ids = {"@group": "G", "@network": "N", "@zone:internal": "ZI", "@zone:external": "ZE"}
    live = [dict(p, _id=f"p{i}", hits=i, **({"last_hit": 1794405045984} if i else {})) for i, p in enumerate(resolve(
        [p for ps in want["policies"].values() for p in ps], ids))]
    live.append(dict(pol("allow-iot", "ALLOW", 10002, "ZI"), enabled=False, source={"zone_id": "ZI"}))
    live.append(dict(pol("Allow All Traffic", "ALLOW", 2147483647, "ZI"), predefined=True, source={"zone_id": "ZI"}))
    ok = lambda p, g=groups, n=nets, z=zones: check_dream(want, p, g, n, z)
    assert ok(live) == [], ok(live)
    mod = lambda i, **kw: [dict(p, **kw) if j == i else p for j, p in enumerate(live)]
    assert ok(mod(3, enabled=False)), "one block disabled"
    assert ok(mod(3, connection_states=["NEW", "INVALID", "ESTABLISHED"])), "state changed"
    assert ok(mod(3, schedule={"mode": "CUSTOM"})), "a new restriction"
    assert ok(mod(3, logging="")), "a new field, even empty"
    assert ok(mod(3, enabled=1)), "1 is not True"
    assert ok(mod(0, destination={**live[0]["destination"], "ips": ["10.9.9.10"]})), "nested field changed"
    assert ok(mod(2, index=1)), "block moved ahead of the allow"
    first = lambda **kw: live + [dict(pol("x", "ALLOW", 2000, "ZE"), source={"zone_id": "ZI"}, **kw)]
    assert ok(first()), "enabled policy ahead of the block"
    assert ok(first(enabled=None)), "enabled not False counts as on"
    assert ok(first(enabled=False)) == [], "disabled policy ahead is fine"
    assert ok(live + [live[3]]), "duplicate policy"
    assert ok(live[:3] + live[4:]), "block missing from one zone"
    assert ok(live, z=zones + [{"_id": "ZD", "zone_key": "dmz", "network_ids": ["X"]}]), "uncovered zone"
    assert ok(live, z=[dict(zones[0], network_ids=["L"]), zones[1], dict(zones[2], network_ids=["N"])]), "agents moved zone"
    assert ok(live, z=zones + [dict(zones[2], _id="ZG2")]), "duplicate zone key"
    assert ok(live, [dict(groups[0], group_members=["10.0.0.0/8"])]), "group shrunk"
    assert ok(live, n=[dict(nets[0], vlan=7)]), "network moved"
    assert ok([], [], [], []), "empty API"
    assert ok([], z=zones), "no policies"
    assert render(False, 5) .startswith("# HELP") and "kennel_fw_ok 0\n" in render(False, 5)
    print("self-test passed")


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else main())
