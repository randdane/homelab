# Technitium DNS

**What:** Recursive/authoritative DNS server with blocklists, per-client
policy and a web UI.
**Why I care:** Candidate replacement for Pi-hole (Vikunja #87, "Research
Technitium DNS Server as a Pi-hole replacement"). `lifecycle: developing`
until that research reaches a decision.
**URL:** http://localhost:5380 (admin user `admin`, password from `.env`).
DNS on `127.0.0.1:5300`: `dig @127.0.0.1 -p 5300 example.com`.

## Notes

- Laptop only, loopback only. Nothing on the LAN resolves through it, so it
  cannot break anything while being evaluated.
- Port 53 is deliberately not published: `systemd-resolved` holds
  `127.0.0.53:53`, and a real deployment needs its own decision about where
  DNS lives (`homelab` vs a CT on `pve` vs its own hardware, as Pi-hole is).
- `DNS_SERVER_ADMIN_PASSWORD` only applies on first start; after that the
  password lives in the volume.
- Image pinned: it migrates its config in `/etc/dns` on upgrade.
- Questions to answer are in #87: blocklists and per-client policy, serving
  `iot` and `agents` (VLAN 5's DNS is pinned to Pi-hole's address in `200.fw` and
  on `dream`), local records without rebind trouble, migration and rollback.

## Findings (2026-10-06, 15.6.0, laptop instance)

Pi-hole parity holds: every Pi-hole feature in use has a tested equivalent.

- **Apps exist under these names:** Advanced Blocking (block lists, regex,
  groups by client IP or subnet), DNS Rebinding Protection, Query Logs
  (Sqlite), and Log Exporter (file, HTTP or syslog).
- **Local records:** a primary zone named after the vhost itself
  (`vikunja.<PUBLIC_DOMAIN>`, A record at the apex) answers locally without
  shadowing the parent: sibling vhosts, the apex and MX still resolve
  publicly, unknown names still return NXDOMAIN, and names under the
  vhost's zone return NXDOMAIN. Never create a zone for `<PUBLIC_DOMAIN>`
  itself.
- **Rebinding:** with no app installed, a public name with a private answer
  passes straight through. That fixes the problem Pi-hole's local records exist
  to work around. With DNS Rebinding Protection on, the result is `NOERROR`
  with zero answers, same as Pi-hole, but locally hosted zones are exempt, so a
  vhost zone with a private address still answers.
- **Clustering:** join works and zones reach the secondary, but heartbeat and
  config sync (Allowed, Blocked, Apps, Settings) then fail with
  `UntrustedRoot` against the primary's self-signed web certificate.
  `ignoreCertificateErrors` covers the join call only. So a domain blocked on
  the primary is not blocked on the secondary. A two-node deployment needs
  each node's web service (port 53443) on a certificate the others trust.
  Not tested with a trusted certificate; the owner decided that is not needed
  for the decision.
- **Memory with Pi-hole's 48 block lists** (1,597,862 domains), measured on
  x86_64 from a cold restart under a cgroup cap, not on ARM:

  | Cap | Peak | Settled | Blocking |
  |---|---|---|---|
  | none | 760 MiB | 450 MiB | works |
  | 600 MiB | 481 MiB | 452 MiB | blocks within 5 s |
  | 400 MiB | 306 MiB | 176 MiB | **never loads** |

  At 400 MiB the load throws `System.OutOfMemoryException` in
  `BlockListZoneManager.LoadBlockLists`, and the server **fails open**: it
  keeps resolving with no container restart, no OOM kill and nothing blocked.
  A health check that only resolves a name stays green through that, so a
  real deployment needs a probe that expects a blocked name to return
  NXDOMAIN. The `pihole` Pi 3 has 1 GB, so the budget is tight but workable
  if Technitium runs alone on it.
