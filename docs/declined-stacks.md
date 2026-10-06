# Declined stacks

Stacks considered and deliberately not run, with the reason, so the question
is not reopened from scratch. Each entry says what would change the decision;
if that has not happened, the answer stands.

Add an entry when a stack is removed from `stacks/` or rejected before it gets
there. Undecided candidates stay in `stacks/` as `lifecycle: planned` instead.

## Watchtower — unattended image updates

**Decided 2026-10-05. Removed.**

Updates here are a human bumping a pinned tag after Cup or
`scripts/check_updates.py` reports one. An unattended updater works against
that in three ways:

- **It upgrades what migrates.** Most stacks worth keeping (authentik, Immich,
  Paperless, Jellyfin's database) run a one-way schema migration at startup.
  An unattended pull is an upgrade nobody chose and nobody can undo by
  reverting the tag.
- **It leaves no record.** A pinned tag in git says what is running. A
  container that recreated itself at 04:00 does not, and the person who finds
  the breakage is family.
- **It needs a read-write Docker socket**, the one capability that is host
  takeover on its own, for a service whose only safe scope was "nothing".

It was only ever deployed with an empty label scope, so it did nothing.

**Would change if:** the repo grew enough stateless, `:latest` services that
bumping them by hand became real toil. Even then, prefer a scheduled job that
opens a commit over something that recreates containers.

## Dockge — web UI for compose stacks

**Decided 2026-10-05. Removed.**

- **Git is the source of truth for compose files.** Dockge's purpose is
  editing and running stacks from a browser, which creates a second copy of the
  YAML that drifts from the repo. The only safe configuration was a read-only
  socket proxy, with the stacks directory pointed at an empty volume, and that
  disables every button that makes Dockge worth running.
- **What is left of it is covered.** Container state is `scripts/status.py`
  and Homepage, logs are Dozzle, host metrics are Beszel, updates are Cup.
- **Write access would mean socket access**, behind a login, on the host
  running every stack.

**Would change if:** the repo stopped being how stacks are deployed. That is a
much bigger decision than adding a UI.

## Pi-hole — as a container

**Decided 2026-10-05. Removed.**

Pi-hole already runs on its own Raspberry Pi appliance, which is the DNS server
the LAN uses. The plan is to replace that with **Technitium**, not to add a
containerised Pi-hole beside it. Technitium adds conditional forwarding, local
zones as first-class records, and DNS-over-HTTPS/TLS upstreams, where Pi-hole
leans on dnsmasq and per-record workarounds. One example is the RFC 1918
rebind filter that forces local records for `vikunja.` and the other gated
names (`docs/devices.md`, homelab-private).

`docs/host-setup.md` §2, freeing port 53, applies to any containerised DNS
server and was kept for that reason.

**Would change if:** Technitium is evaluated and rejected. Then the question
is Pi-hole appliance vs container, not this entry.
