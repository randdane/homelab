# lgtm

**What:** Loki, Grafana, Tempo, Mimir behind one OpenTelemetry Collector.
Logs, traces and metrics for this laptop and everything running on it.
**Why I care:** Learning the stack hands-on, and it is the only place that
answers "what was this machine doing at 3pm".
**URL:** http://localhost:3007 (admin / see `.env`)

## Demo traffic

    uv run demo/app.py
    curl localhost:8900/work

Emits all three signals. Every log line carries `trace_id=`, so Grafana's
derived field jumps from a log straight to its trace. If that round trip
breaks, this is the smallest thing that proves it.

## Notes

- Tags are pinned on purpose. Loki and Mimir migrate on-disk schemas between
  releases; an unattended `:latest` pull can hand you a format this config
  does not expect.
- Loki stops accepting writes above **90% disk usage on the host**, and the
  failure is deceptive: `/ready` returns 200 and the ring says ACTIVE while
  every push returns `500 Ingester is shutting down`. Check
  `loki_ingester_wal_disk_full_failures_total` on Loki's `/metrics`.
