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

## Deployed on `homelab` 2026-11-05 (plan step 2)

- Docker user networks 29 → 30. Containers 48 → 51.
- `probe_success` 1 for `http://ntfy:80/v1/health`. A bad target
  (`http://nosuch-container:80/`) showed `probe_success` 0 with `up` 1, the
  reason the rule keys on `probe_success`. It was removed before the 2 m
  `for:` window, so no alert went out.
- `docker compose down -v`, then `up -d`: the data source, dashboard, both
  rules and the ntfy contact point all came back from the files.
- On first start both rules sit in `Pending (NoData)` for one evaluation,
  before the first scrape, then go to Normal. `for: 2m` keeps that silent.
- Memory a few minutes after start: Grafana 331 MiB, VictoriaMetrics 45 MiB,
  Blackbox 10 MiB.
- Two fixes found on deploy: config is mounted as a directory (a single-file
  mount kept the old inode, so `/-/reload` reread a stale copy), and the
  VictoriaMetrics healthcheck uses `127.0.0.1` (`localhost` is `::1` in the
  image).
- The scrape interval is the 60 s default. A test needs a full minute
  before its result shows up.

## Vhost and SSO, 2026-11-05 (plan step 3)

`https://grafana.<PUBLIC_DOMAIN>`, Caddy `@grafana`: same `remote_ip`
allowlist as kuma, **no forward-auth**. Grafana's own login is the gate.
Native OIDC against Authentik:

- Provider `grafana`: confidential, strict redirect
  `https://grafana.<PUBLIC_DOMAIN>/login/generic_oauth`. `grant_types` is set
  explicitly. The flows, signing key and scope mappings were copied from
  `mealie`, using the stock email mapping: Grafana does not require
  `email_verified`.
- Application `grafana`, bound to group `grafana-users`. Grafana also checks
  `allowed_groups`. Members of `grafana-admins` get Admin, everyone else
  Viewer. The owner is in both.
- Client ID and secret were generated locally, set on the provider through
  the API, and written straight into `.env` on `homelab`. They were never
  printed.
- Setting names were read from the pinned image's `defaults.ini`
  (`[auth.generic_oauth]`).

Verified: an incognito login through Authentik worked. Authentik logged one
`/application/o/token/` 200 from `10.201.7.24`, Grafana's `edge` address. That
is also the proof that no `gw_priority` is needed: `monitoring` sorts after
`edge`. Grafana has one SSO user (auth label `Generic OAuth`, role Admin)
beside the local `admin`, which still works as the way in when Authentik is
down. `https://nosuch.<PUBLIC_DOMAIN>` sent to Caddy still closes with no
response.

## Unverified

- **ntfy message formatting.** The webhook sends Grafana's default JSON body;
  how it reads on the phone is decided by test in plan step 5 (custom payload
  vs ntfy `?template=yes`). Delivery itself (a real fire reaching ntfy) is not
  yet tested; that is plan step 4.
- Alert `for: 2m`; both rules `noDataState: Alerting`, so a vanished
  `probe_success` series alerts rather than reading as healthy. With more
  than one target, another target's data can mask one going missing: add a
  per-target absence check then. Not exercised by a real failure yet.
- Resource use and 30-day disk use: measure after a week (step 6).
- The `docker network` count gate: a deploy-time check (plan step 2).

## Notes

- Reload scrape config: `curl -X POST http://127.0.0.1:8428/-/reload` on
  `homelab` (flag `-promscrape.configCheckInterval` is off by default).
- Containers run as the images' defaults (VictoriaMetrics and Blackbox root,
  Grafana uid 472).
