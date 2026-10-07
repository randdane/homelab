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
