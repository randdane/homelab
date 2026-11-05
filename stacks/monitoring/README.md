# Monitoring

**What:** VictoriaMetrics (stores and scrapes), Grafana (dashboards, unified
alerting, no Alertmanager) and Blackbox Exporter (HTTP probes).
**Why I care:** metric history and alerts to try beside Uptime Kuma and Beszel,
which stay as they are. Plan: `homelab-private/docs/superpowers/plans/2026-11-05-monitoring.md`.
**URL:** none yet. Break-glass over `ssh -L` to `127.0.0.1:3007` (Grafana) and
`127.0.0.1:8428` (vmui, `/targets`) on `homelab`. The vhost and Authentik OIDC
are plan step 3. Grafana has no Homepage tile until then (the link check
requires a Caddy matcher for a vhost href).

**Status:** `lifecycle: developing`, step 1 (repo only). Not yet deployed.

## Everything is files

Data source, dashboard, alert rules, contact point and notification policy are
provisioned from `config/`. Both volumes are `homelab.backup: exclude`: they
hold only history and regenerable state. A dashboard edited in the UI is lost
unless exported back into `config/grafana/dashboards/` (`allowUiUpdates: false`).

Phase 1 only: one Blackbox target (`http://ntfy:80/v1/health`), two rules:
`probe_success == 0` and `up{job="blackbox"} == 0`. No cAdvisor (host-root via
the Docker socket), no `pve` jobs (plan step 5; placeholder in `scrape.yml`).

## Secrets (`.env`, see `.env.example`)

`GRAFANA_ADMIN_PASSWORD`, `NTFY_TOKEN`; both `${VAR:?}` in compose. OIDC
variables arrive with step 3, the pve exporter password with step 5.

## Verified 2026-11-05 against the pinned images

Each was run locally in throwaway containers (and a full `docker compose up`
with dummy env values and a stand-in ntfy, then `down -v`).

| Claim | How |
|---|---|
| Tags resolve: `victoria-metrics:v1.155.0`, `grafana:13.2.5`, `blackbox-exporter:v0.29.1` | `docker manifest inspect` |
| `-retentionPeriod` (`30d`, `d` suffix valid), `-promscrape.config`, `-storageDataPath`, `-httpListenAddr` | `docker run IMAGE --help` |
| `%{VAR}` substitution in the scrape file, **no** `-envflag.enable` needed | `-promscrape.config.dryRun` with `scrape_interval: "%{IV}"`: `IV=bogus` fails to parse, `IV=15s` passes |
| Blackbox `--config.file`, `--config.check`; `scrape.yml` and `blackbox.yml` valid | `--help`, `--config.check`, `dryRun` |
| Grafana provisioning root `/etc/grafana/provisioning` with `datasources/`, `dashboards/`, `alerting/` | image `defaults.ini` and `conf/provisioning/*/sample.yaml` |
| Alert rules, contact point, policy load; both rules evaluate `ok`; `$NTFY_TOKEN` expands into the webhook bearer credential | Grafana provisioning API and `contact-points/export?decrypt=true` |
| Data source and dashboard load; `probe_success` = 1 for the target in VictoriaMetrics | Grafana API; `/api/v1/query` |
| Healthchecks use `wget`, present in all three images (no `curl`) | `command -v wget curl` in each |

## Unverified

- **ntfy message formatting.** The webhook sends Grafana's default JSON body;
  how it reads on the phone is decided by test in plan step 5 (custom payload
  vs ntfy `?template=yes`). Delivery itself (a real fire reaching ntfy) is not
  yet tested; that is plan step 4.
- Alert `for: 2m`; both rules `noDataState: Alerting`, so a vanished
  `probe_success` series alerts rather than reading as healthy. With more
  than one target, another target's data can mask one going missing: add a
  per-target absence check then. Not exercised by a real failure yet.
- Grafana's address on `edge` and whether `gw_priority` is needed: step 3.
- Resource use and 30-day disk use: measure after a week (step 6).
- The `docker network` count gate: a deploy-time check (plan step 2).

## Notes

- Reload scrape config: `curl -X POST http://127.0.0.1:8428/-/reload` on
  `homelab` (flag `-promscrape.configCheckInterval` is off by default).
- Containers run as the images' defaults (VictoriaMetrics and Blackbox root,
  Grafana uid 472).
