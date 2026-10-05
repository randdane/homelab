# Adding a stack

The ordered runbook, from "should this exist" to `lifecycle: production`.
Each step points at the doc that holds the rule and the reason; this file only
holds the order and the stop points. `docs/conventions.md` is the rules,
`docs/lessons-learned.md` is why they exist.

Work through it top to bottom and tick as you go. A step that does not apply
gets a one-line reason in the commit, not silence.

**🛑 marks a stop point:** an action that restarts a shared service, touches an
appliance or a third party, or needs a browser. An agent hands the user the
exact command or click-path there and waits for them.

## 0. Decide

- [ ] It earns its place. `homelab` (VM 101 on `pve`) has ~11 GB; each stack
      costs RAM, disk, and an update stream. Check headroom:
      `ssh homelab 'free -h; df -h /; docker network ls -q | wc -l'`
      (network count well under the ~31-pool ceiling, `lessons-learned.md` §11)
- [ ] `x-homelab` answers written down before any YAML (`conventions.md`,
      schema in `scripts/x_homelab.py`):
  - `host` — `always-on` is `homelab`. `server` is a bigger box powered up only when wanted.
  - `exposure` — what can *reach* it. A gated vhost is `lan`, not `internet`.
  - `data` — `precious` / `replaceable` / `none`, judged against a dead disk.
- [ ] Reached how? One of:
  - published port only: `http://${HOMELAB_HOST}:<port>`. Only for a
    service with no login screen: a UI with one needs the vhost, because
    §3b does
  - gated vhost `https://<name>.${PUBLIC_DOMAIN}`: the usual answer for a UI
    used off-LAN or from the phone. Do §3.
  - public vhost: needs a written reason, `crowdsec`, and an entry in
    `PUBLIC_HOSTS` in `scripts/check_vhosts.py`. Ask first.
- [ ] Behind Authentik how? **Every production UI is, unless recorded
      otherwise** (§3b). Pick the first that applies:
  - **native OIDC**: the app has its own OpenID Connect login
  - **forward-auth**: a web UI with no OIDC; Caddy asks Authentik first
  - **neither**: no interactive login (an API, a push endpoint, a relay) or
    SSO is paywalled. Write the reason in the README and the commit

## 1. Repo (laptop)

- [ ] `cp -r _template stacks/<name>`, or finish the existing directory
- [ ] Upstream's own compose read for any multi-service app
- [ ] Every tag resolves (`docker manifest inspect`) and is pinned if it
      migrates on-disk state
- [ ] No secret has a default: `${VAR:?}` in compose, blank in `.env.example`
      with a generation command
- [ ] Signups/registration off, or a README step saying when to turn them off
- [ ] Healthcheck from verified binaries (`conventions.md` → Healthchecks)
- [ ] `container_name` is `<stack>-<service>` for multi-service stacks
- [ ] Homepage labels on the UI service. `href` is one of the two shapes
      `scripts/test_homepage_links.py` accepts, the vhost when one exists
- [ ] Every named volume is either registered in `stacks/backup/compose.yaml`
      (mount **and** `external: true`) or `homelab.backup: exclude` with a
      comment. Then `uv run scripts/check_backups.py`
- [ ] `uv run scripts/ports.py`: no collision, `docs/ports.md` regenerated
- [ ] Any config file outside the `.gitignore` allowlist gets its own `!` line;
      `git status --ignored stacks/<name>` confirms
- [ ] README: what, why, URL, what will bite you, what is unverified
- [ ] `lifecycle: developing` (never `production` yet; `conventions.md` →
      "`lifecycle` describes reality")
- [ ] `./scripts/check.sh` passes (renders every stack, runs the tests)
- [ ] Commit and `git push origin main`

## 2. Deploy (`homelab`)

`uv` there is `~/.local/bin/uv`; a non-interactive `ssh` does not have it on
`PATH`.

- [ ] `cd /opt/homelab && git pull`; `git status` clean
- [ ] `stacks/<name>/.env` from `.env.example`, secrets filled. **Check for an
      existing `.env` first**: stacks migrated from the legacy setup may already
      have one with stale values (mealie's pinned `BASE_URL` to localhost).
      The repo-root `.env` on `homelab` has no `PUBLIC_DOMAIN`; set it here.
      `HOMELAB_HOST=<LAN address>` if labels use it, `chmod 600 .env`
- [ ] `docker ps -q | wc -l` before. Start **this stack only**:
      `docker compose up -d` inside its directory, never a loop over `stacks/*`.
      Count again afterwards
- [ ] Healthy: `docker compose ps`
- [ ] Default credentials changed now, before anything else: some images
      ship a known admin login (mealie's is `changeme@example.com` /
      `MyPassword`), live on the LAN port from the first start
- [ ] **Its actual function tested**, not just "container is Up". Example:
      save a bookmark and see the snapshot, not only the login page loading
- [ ] `uv run scripts/status.py --capture`

## 3. Vhost (skip for port-only)

- [ ] UI service joins the external `edge` network (see
      `stacks/uptime-kuma/compose.yaml`); the stack keeps `default` too
- [ ] Caddy: a `@<name>` matcher **and** `handle` in the wildcard block of
      `stacks/caddy/config/Caddyfile`, `remote_ip` line copied verbatim from
      its neighbours, upstream by container name, placed before the final
      `handle { abort }`
- [ ] `scripts/check_vhosts.py`: the name goes in `PHONE_PROBES`, or in
      `NOT_PHONE_PROBED` with a reason. `uv run scripts/check_vhosts.py`.
- [ ] App's own base-URL setting (`NEXTAUTH_URL`, `APP_URL`, …) points at the
      vhost
- [ ] Commit, push, pull on `homelab`
- [ ] 🛑 **Public A record** `<name>.${PUBLIC_DOMAIN} → <HOMELAB_HOST>` at the DNS provider.
      The wildcard CNAME is gone, so without it the name does not resolve at
      all — deliberately: a wildcard makes a deleted record look healthy
- [ ] 🛑 **Pi-hole Local DNS Record**, same mapping. Pi-hole drops the public
      answer (a public name with a private address) as DNS rebinding
- [ ] 🛑 **Recreate Caddy** so the bind-mounted Caddyfile is re-read. If the
      app takes forward-auth (§3b), add the `import` first: one recreate
      covers both
      (`conventions.md` → "Keeping the two checkouts in sync"):
      `cd /opt/homelab/stacks/caddy && docker compose up -d --force-recreate caddy`,
      then `docker exec caddy grep -c <name> /etc/caddy/Caddyfile`
- [ ] From a workstation: `curl -sI https://<name>.${PUBLIC_DOMAIN}` returns the app, and
      `https://nosuch.${PUBLIC_DOMAIN}` still closes with no response
- [ ] 🛑 **Tasker probe**, only now that the name resolves: a probe run before
      that pushes `down`. `PHONE_PROBES` only records what the phone should
      check. **Plug the phone in over USB** and the agent edits the task with
      adb. The live task is `Vhost Probe2`; its export lives on the phone
      only, newest of `/sdcard/Tasker/tasks/Vhost_Probe2*.tsk.xml` (it holds
      the Kuma push token, so it is not in the repo). The agent copies one
      probe block (Var Clear, HTTP Request, If, Var Append `%failed`, End If)
      per new host after the last one, pushes it as a new file, and you import it
      (Tasks tab, long-press → Import Task, replace) and run it once.
      **Verify from the run log, not the push**: count the `HTTP Request`
      actions in the latest run in `/sdcard/Tasker/log/runlog.txt`; it should
      be one per `PHONE_PROBES` entry plus two (guard and push). On
      2026-09-29 that count showed karakeep had been in `PHONE_PROBES` for
      eleven days without ever being probed

## 3b. Authentik (skip only with the reason from §0)

Either path needs the vhost from §3 first: Authentik redirects back to an
HTTPS name, and a bare `http://IP:port` gets upgraded by browsers and fails.
With an Authentik API token saved for it, an agent
does this step through the API; nothing here needs the admin UI.

**Native OIDC:**

- [ ] Read the app's OIDC settings **from the image or the pinned version's
      source**, not memory: variable names, the callback path, and how it
      links an SSO login to an existing account
- [ ] Authentik: group `<app>-users` with you in it; OAuth2 provider `<app>`
      (confidential, implicit-consent flow, scopes openid/email/profile,
      strict redirect URI); application `<app>` bound to that group.
      **Set `grant_types` to `authorization_code`, `refresh_token`
      explicitly**: via the API it defaults to `[]`, and the only error is
      "The request is otherwise malformed"
- [ ] Linking by email: if the app requires `email_verified` (Mealie,
      Vikunja, anything on PocketBase such as Beszel), use the Authentik mapping `homelab: email (admin-verified)`
      in place of the stock email mapping, which always sends `false`.
      Otherwise the app refuses the login or silently creates a second user
- [ ] The UI service is on `edge`, so its backend reaches Authentik through
      `@authentik_oidc_internal`; nothing to add to Caddy. **If the stack's
      name sorts before `edge`** (`beszel`, `authentik`…), its default route
      is its own bridge and the request arrives SNAT'd as the refused
      gateway: give `edge` `gw_priority: 1` (see `stacks/beszel/compose.yaml`)
- [ ] Client ID and secret straight into the host's `.env` (never printed);
      blank-by-default variables in compose, entries in `.env.example`.
      No domain literal in committed config (`test_identity_leak.py`)
- [ ] Password login stays enabled: it is the way in when Authentik is down
- [ ] 🛑 **Test in an incognito window, from the app's login page**, clicking
      its Authentik button. An existing app session looks exactly like a
      working SSO login. Then verify, don't trust the page:
  - Authentik's log shows a `/application/o/token/` 200 **from the app's
    container address**. No token exchange means SSO never ran
  - the app's user table still has **one** account for you. A second one
    must be deleted, not only the mapping fixed: most apps match
    issuer+subject before email, so the stray keeps catching every login

**Forward-auth:**

- [ ] Inside the app's `handle`, after its `remote_ip` gate:
      `route { import authentik_forward_auth` + `reverse_proxy … }`
- [ ] Access is the Authentik group `admin-uis`: one proxy provider,
      forward_domain, one sign-in for all of them. Nothing to create per app
- [ ] An app that checks the `Host` header (Duplicati) needs the vhost name
      in its allowed-hostnames setting, plus loopback for local callers
- [ ] A dangerous admin UI goes on the `admin` network, not `edge`
- [ ] 🛑 Recreate Caddy (unless §3's recreate already carried the import);
      without a session the vhost must 302 to
      `https://authentik.<domain>/application/o/authorize/`. A redirect to
      `localhost` means the embedded outpost's `authentik_host` was reset
- [ ] Monitors and probes: Kuma targets the container over `edge` and the
      phone probe accepts any status, so neither changes

**Either path:** the README says which one, the Authentik group, and what
broke on the way.

## 4. Backup, monitoring, promotion

- [ ] 🛑 **Recreate the backup container** if a volume was registered, or it
      archives the old set:
      `cd /opt/homelab/stacks/backup && docker compose up -d --force-recreate`.
      Then `uv run scripts/check_backups.py` **on `homelab`**
- [ ] 🛑 **Uptime Kuma monitor** by container name over `edge`
      (`http://<container>:<port>`), not the host IP. The UI trap is in
      `stacks/uptime-kuma/README.md`. Then `uv run scripts/check_monitors.py`
- [ ] §3b done, or its skip reason is in the README: nothing reaches
      `production` with a login screen Authentik does not stand in front of
- [ ] Lived with for a day, then `lifecycle: production`. From here
      `status.py` alerts when it is down, so promote only what is meant to
      stay up
- [ ] README updated with what was measured; commit
