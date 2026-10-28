# Resource savings

Ways to make the host use less RAM, CPU and disk, and when each one fits.
What this deployment has actually done, with measurements, is in
`$SITE_DIR/docs/resource-savings.md`; add a row there for each change.

Order of preference: stop paying for idle services, then cap the greedy
ones, then add RAM.

Measure with `free -m` and
`docker stats --no-stream --format '{{.MemUsage}}\t{{.Name}}' | sort -h -r`.
Read "available" in `free`, not "used": page cache counts as used and is
handed back on demand.

## Methods

**Stop on idle (Sablier).** Fits a service reached only through Caddy, where
a wait on the first request is acceptable. Browsers get a waiting page
(`dynamic`); an API path whose client cannot follow one gets `blocking`,
which holds the request until the container is up. Does not fit anything
reached without Caddy: phone sync over another port, webhooks, SMB,
schedules, or another container calling it -- unless that caller starts
and stops it itself, as `run_backups.sh` does for Duplicati. A stopped container reads as
down to Uptime Kuma, so its monitor goes; `status.py` reports it `idle`
rather than `down`, and `check_monitors.py` skips it. How to add one:
`stacks/sablier/README.md`.

**Cap a runtime's heap.** For JVM and similar apps with no limit set, which
default to a share of host RAM. Set it through the runtime's environment
(`JAVA_TOOL_OPTIONS` for Java) when the image's command line is fixed. Read
the actual heap use first (`jcmd <pid> GC.heap_info`) and leave margin.

**Cap memory (`mem_limit`).** For apps that grow to fill what is free
(Chromium). A cap below the working set gets the container OOM-killed, so
set it from observed peak use plus margin, and watch for restarts after.

**Swap: zram first, then a swapfile.** Neither reduces use. zram at high
priority absorbs overflow in compressed RAM; a small swapfile at lower
priority catches what zram cannot. Together they turn an out-of-memory kill
into slowdown. `ansible/swap.yml` sets both up; prove it on `harden-test` first.

**Remove what is not used.** The cheapest saving, but check its size first:
a static web app may hold a few MiB. Check every consumer before calling
something unused (`docker compose ls`, Caddyfile, Homepage, Uptime Kuma).
