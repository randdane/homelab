# Resource savings

What has been done to make `homelab` use less RAM, CPU and disk, what each
change saved, and what is still worth trying. Add a row when you make a
change, with the figure you measured before and after.

RAM is the binding limit today. The VM was raised once already (12 to
16 GiB, 2026-10-26), so the order of preference is: stop paying for idle
services, then cap the greedy ones, then add RAM.

Measure with `free -m` on the VM and
`docker stats --no-stream --format '{{.MemUsage}}\t{{.Name}}' | sort -h -r`.
Read "available" in `free`, not "used": page cache counts as used and is
handed back on demand.

## Done

| Date | Change | Saves | Cost |
|---|---|---|---|
| 2026-10-26 | VM RAM 12 to 16 GiB | +4 GiB headroom (not a saving) | 4 GiB less for other guests on `pve` |
| 2026-10-28 | Sablier stops Stirling PDF when idle (`stacks/sablier`) | ~0.8 GiB while idle (available 7.4 to 8.2 GiB, measured 2026-10-28) | ~75 s wait on the first visit; two small containers (Sablier and its socket proxy) |

Baseline 2026-10-28, before Sablier: 16 GiB total, 8.6 GiB used,
7.4 GiB available, no swap.

## Methods

**Stop on idle (Sablier).** Fits a service that only a browser opens, through
Caddy, where a wait on the first visit is acceptable. Does not fit anything
reached without Caddy: phone sync, webhooks, SMB, schedules, or another
container calling it. How to add one: `stacks/sablier/README.md`.

**Cap memory (`mem_limit`).** For apps that grow to fill what is free (JVM,
Chromium). A cap below the working set gets the container OOM-killed, so set
it from observed peak use plus margin, and watch for restarts after.

**Swap or zram.** Doesn't reduce use. It turns an out-of-memory kill into
slowdown, which buys time to notice.

**Remove what is not used.** The cheapest saving. Check every consumer
before calling something unused (`docker compose ls`, Caddyfile, Homepage,
Uptime Kuma).

## Candidates

Considered 2026-10-28.

| Service | RAM | Idle-stop? | Why |
|---|---|---|---|
| Duplicati, Homebox, Grocy, Mealie, SearXNG | ~0.7 GiB together | yes | Browser only. Check whether any app or schedule reaches them first |
| Karakeep (app, Chrome, Meilisearch) | ~1 GiB | no | The browser extension and mobile app save in the background. Candidate for a cap on Chrome instead |
| Paperless (web, Tika) | ~0.9 GiB | no | The scanner delivers over SMB at any time |
| n8n | ~0.4 GiB | no | Webhooks and schedules |
| Immich, Authentik, Supernote | — | no | Phones, SSO and the tablet reach them without a browser |
