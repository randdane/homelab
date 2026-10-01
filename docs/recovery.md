# Recovery: getting the homelab back from a backup

> [!NOTE]
> A copy of this runbook also lives in the Obsidian vault at
> `AoI/Homelab/Backups/Homelab-Recovery.md`, for reading away from the repo —
> including from a phone while the server is down, which is the likeliest way
> you will read it. **This file is the canonical one.** Edit here, then copy
> across.

Written 2026-08-24, against a real drill run end to end on the first server: an archive
was pulled from Backblaze B2, decrypted, extracted, and both databases were
stood up in throwaway containers and queried. Timings below are measured, not
estimated.

Companion documents: `stacks/backup/README.md` (what is archived and why),
`stacks/duplicati/ROTATION.md` (rotating these same secrets),
`docs/host-setup.md` (rebuilding the host itself).

---

## 0. What you need before you can start

Recovery has a **circular dependency**, and this is the part that bites:

- Reaching B2 needs the **B2 application key**
- That key is stored in `Duplicati-server.sqlite`, encrypted
- That database is inside the archive — which lives in B2
- The key that decrypts it, `SETTINGS_ENCRYPTION_KEY`, is in
  `stacks/duplicati/.env`, which **is not backed up by anything**

So these must exist outside the homelab, in a password manager — an external
one. Vaultwarden is in this repo but is `lifecycle: planned` and has never
run, so it is not the answer here and would be the wrong answer even deployed:
a password manager that lives in the lab cannot help you recover the lab.

The "Lives in" column is where the value exists *today*, which for the first three is
somewhere you cannot reach after a disk failure — that is the whole point:

| Secret | Lives in | Needed for |
|---|---|---|
| **B2 bucket and path** | Duplicati's `TargetURL`, in `Duplicati-server.sqlite` | naming the bucket at all |
| B2 application key ID + secret | same `TargetURL` | reaching the bucket |
| Duplicati restore **passphrase** | Duplicati settings, same DB — masked in API *responses*, but readable back off a running server (see [Reading the passphrase back](#reading-the-passphrase-back-off-a-running-server)) | decrypting the archives |
| `SETTINGS_ENCRYPTION_KEY` | `stacks/duplicati/.env` | reading Duplicati's own config DB |
| `WEBSERVICE_PASSWORD` | `stacks/duplicati/.env` | the API, and `run_backups.sh` |
| `AUTHENTIK_SECRET_KEY` | `stacks/authentik/.env` | keeping sessions/tokens valid after restore |

Without the first three, the backups are unreadable and nothing below works.

**The bucket name is a secret in the sense that matters: you cannot guess it.**
It carries a random suffix (`homelab-offsite-<random>`), it is not in any
`.env`, and it lives only inside the Duplicati database that is itself in the
bucket. An earlier version of this table said the B2 credentials lived in
`stacks/duplicati/.env` — they do not; that file holds only `PGID`, `PORT`,
`PUID`, `SETTINGS_ENCRYPTION_KEY`, `TZ` and `WEBSERVICE_PASSWORD`. If your
password manager is missing the bucket, the fallback is logging into the
Backblaze web UI with the **B2 account** login (a seventh thing to keep) and
reading the bucket name off it.

### Prove it, do not assume it

Every ordinary restore starts from a working Duplicati that already knows the
bucket and holds the keys, so the password-manager-only path is the one thing
never exercised until the day it has to work:

```bash
./scripts/recovery-drill.sh        # run this somewhere that is NOT homelab
```

It prompts for the bucket, the B2 key, and the passphrase, refuses to read
anything from the repo or the host, and lists, decrypts and restores one file
in a throwaway container. If you reach for a `.env` to answer a prompt, that
is the finding.

| Drill run | Result |
|---|---|
| 2026-08-27, from the laptop | **PASS.** 7 versions listed, 10 files in the newest, restored `homelab-2026-08-21T17-23-46.tar.gz` — 9,575,584 bytes, valid gzip, 81 members. Password manager alone was enough. |
| 2026-09-03, from the laptop at home | **PASS.** Ran clean end to end on the password-manager-only path. Surfaced one real defect — the drill could not delete what it restored; see below. |
| 2026-09-02, from the laptop on a foreign wifi | **INCONCLUSIVE — not a failure.** Step 1 died in the TLS handshake to `api.backblazeb2.com`, so no credential was ever transmitted. `curl` reproduced it host-side and in a container: `rc=35`, `ssl_verify_result=1`, six for six, ~0.53 s each. The network intercepts or blocks B2. |

**The second run is in this table because of what the script said, not what
happened.** It reported `FAILED -- ... that is the bucket path or the B2 key`,
which is a confident, specific, and wrong instruction to go rotate a
credential that had not been used. A blocked handshake and a rejected key are
unmistakable in the stack trace and identical in a one-line verdict.

The script now checks for that shape first and exits **3** — distinct from the
credential exit of 1 — saying the connection never completed. The general
lesson is worth more than the fix: a drill that misreports *why* it failed is
more dangerous than one that does not run, because it spends real effort on
the wrong secret and leaves the operator believing they tested something.

Run this drill from a network you control. A corporate or campus wifi that
inspects TLS cannot answer the question the drill is asking.

**A passing drill used to leave decrypted backup data behind.** The restore
runs as root inside the container, so the archive landed in the temp directory
owned by `root` mode `444`; the cleanup `rm -rf` then failed and the script
exited announcing success:

```
drwx------ r    r     /tmp/tmp.XXXXXXXX
drwxr-xr-x root root  └─ restored/
-r--r--r-- root root     homelab-2026-08-21T17-23-46.tar.gz   9,575,584 bytes
```

Every run leaked another copy, and the invoking user could not remove any of
them. `mktemp -d` gives 0700 so no other user could read them, which is the
only reason this was untidy rather than a disclosure.

The cleanup now hands ownership back through a throwaway container before
deciding what to do with the directory. Note the shape of this one: the
drill's *stated* result was correct and its *side effect* was not, so nothing
in the pass/fail output could ever have revealed it. It took someone trying to
delete the directory by hand.

**The first fix for it was itself incomplete, which is the more useful half of
the story.** It made a failed removal print a warning — and a warning is not a
result. An `EXIT` trap inherits the status it was entered with, so the script
printed `WARNING: could not remove ...` and still exited 0. `recovery-drill.sh
&& echo clean` reported clean over a decrypted archive, and any caller
scripting the drill would have believed it. The chown made the warning rare
without making the exit correct.

Since 2026-09-03 the trap's status is decided by **whether the directory is
gone**, not by what `rm` returned, and leftover plaintext exits **4** —
distinct from the drill's own 1, 2 and 3 so that "the backup is fine, the
scratch directory is not" stays separable from "the backup is broken". Testing
the postcondition rather than the return code is what makes the silent chown
failure harmless: it is allowed to fail precisely because something downstream
checks the thing that matters.

`scripts/test_recovery_drill.py` now runs the real `cleanup()` — extracted from
the script, not copied — against a directory it cannot delete, made
undeletable by taking write permission off the parent rather than by needing
root. Those tests fail against the warning-only version with
`exited 0 -- a caller cannot tell plaintext remains`, which is the sentence
that should have existed the first time.

That first real run took four attempts, and **every failure was the drill, not
the backup** — a tab the version regex would not match, a missing `"*"` filter
that listed versions where it claimed to list files, and a deprecated option
whose warning made `duplicati-cli` exit non-zero over a restore that had just
written 9.13 MiB. A drill that has never run is not a drill; it is a script
that has never been wrong out loud.

> [!IMPORTANT]
> The bucket has **Object Lock with 14-day governance retention**. Backups
> cannot be deleted or encrypted in place inside that window, including by you
> or by ransomware. It also means a rotated passphrase leaves two chains
> coexisting for up to two weeks — see `stacks/duplicati/ROTATION.md`.

---

## 1. Decide which failure you are in

| Situation | Where the data is | Go to |
|---|---|---|
| A service is broken, host is fine | local archives on `homelab` | §3 (skip §2) |
| `homelab`'s disk is dead, replaced | B2 only | §2 |
| A file was deleted, need one volume | either | §3 |

Local archives live at
`~/.local/state/homelab/backups/homelab-<timestamp>.tar.gz` — retention is 14
days (`BACKUP_RETENTION_DAYS`). B2 holds 30 days (`keep-time=30D`).

Note the two are **different retention windows**. Anything older than 14 days
exists only in B2.

### What you do not get back: `/srv/media`

**588 GB of films and TV in `/srv/media` is deliberately not backed up, and a
dead disk loses all of it.** This is a decision, not an oversight -- do not
"fix" it during a recovery.

Backing it up would mean keeping a second server powered for its 6 TB drive, at
~55 W, which is half the lab's entire always-on draw and the reason that
machine was retired. That is a standing cost to insure files that can be
downloaded again.

The accepted loss is **weeks of re-downloading over a 100 Mb/s link**, not
anything irreplaceable: 109 films and 26 series, all publicly released, none
of it personal. Nothing else in `/srv/media` is unique -- `music/` is an empty
placeholder.

Worth being clear about the geometry, because it is easy to misread: the
media and every Docker volume share one 1 TB 5400rpm disk (`/dev/sda2`, 74%
full). A disk failure takes both. The volumes come back from B2; the media
does not. That split is intentional and correctly sized -- the half that is
hard to rebuild is the half that is insured.

---

## 2. Retrieving an archive from B2

### 2a. Confirm B2 is reachable and the data is intact

This is the fastest possible check that offsite recovery is possible at all,
and it costs one API call. It downloads sample volumes, decrypts them, and
verifies their hashes:

```bash
# on the host running duplicati
PW=$(grep -E '^WEBSERVICE_PASSWORD=' /opt/homelab/stacks/duplicati/.env | cut -d= -f2-)
TOK=$(curl -s -X POST http://127.0.0.1:8200/api/v1/auth/login \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c 'import json,sys;print(json.dumps({"Password":sys.argv[1]}))' "$PW")" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessToken"])')

# what versions exist in the bucket
curl -s http://127.0.0.1:8200/api/v1/backup/1/filesets -H "Authorization: Bearer $TOK"

# verify: downloads + decrypts + hashes. Poll /api/v1/task/<ID> until Completed.
curl -s -X POST http://127.0.0.1:8200/api/v1/backup/1/verify -H "Authorization: Bearer $TOK"
```

`Status: Completed` with `ErrorMessage: null` means the offsite copy is good.
**Measured: 10.8s end to end on 2026-08-27.** This is the whole DR check, and
it now runs itself:

    homelab-verify.timer    # monthly, scripts/run_backups.sh --verify

It used to say "run this monthly", which meant it ran never. `status.py`
already watches the newest archive's *age* hourly, which catches backups that
stopped; this catches backups that keep running and write output nobody can
read. Trigger one by hand any time with:

    ./scripts/run_backups.sh --verify

> [!NOTE]
> Poll `/api/v1/task/<ID>`, not `/api/v1/progressstate`. progressstate still
> reported `Phase=Verify_Running` after task 4 had finished on 2026-08-27 —
> a poller watching it waits out its own timeout and then fails a verify that
> passed.

> [!WARNING]
> The API **masks secrets in ordinary responses.** `TargetURL`'s
> `auth-password` and the `passphrase` setting both come back as 15 asterisks
> from `GET /api/v1/backup/<id>`. If you extract those and hand them to
> `duplicati-cli`, B2 answers `401 bad_auth_token` and it looks like a
> credential failure at Backblaze. It is not.
>
> Masking is a property of that response, **not of the stored data** — see
> below for the endpoint that returns the real values.

### Reading the passphrase back off a running server

This document used to say the passphrase could not be read back at all. That
was wrong, and wrong in the direction that costs you everything: it would stop
you trying on the one day it matters. Recovered this way on 2026-08-27, after
the passphrase was forgotten and rotation was very nearly run instead — which
would have wiped the settings store holding the only surviving copy.

While the Duplicati container is **running**, the export-as-command-line
endpoint returns every stored secret in cleartext — bucket, B2 key ID, B2
application key, and passphrase:

```bash
cd /opt/homelab/stacks/duplicati
PW=$(grep -E '^WEBSERVICE_PASSWORD=' .env | cut -d= -f2-)
TOK=$(curl -s -X POST http://127.0.0.1:8200/api/v1/auth/login \
      -H 'Content-Type: application/json' \
      -d "{\"Password\":\"$PW\",\"RememberMe\":false}" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessToken"])')

curl -s "http://127.0.0.1:8200/api/v1/backup/1/export-cmdline?export-passwords=true" \
  -H "Authorization: Bearer $TOK"
```

`export-cmdline`, **not** `export` — the latter answers `400` with an empty
body and no hint why. The route is the one the web UI's own Export → "As
Command-line" button calls; if it moves again, read
`/app/duplicati/webroot/ngax/scripts/controllers/ExportController.js` inside
the container rather than guessing.

Redirect it to a `chmod 600` file rather than the terminal, and delete that
file once the value is in the password manager.

> [!CAUTION]
> This works **only while the server runs and its settings DB is intact.** The
> stored value is an `enc-v1:` blob encrypted with `SETTINGS_ENCRYPTION_KEY`,
> and nothing here decrypts it offline — Duplicati's own `DatabaseTool` has no
> decrypt command. Lose the disk and this route is gone with it. It is a
> second chance, not a backup: the password manager is still the answer.

### 2b. Actually pulling files down

Easiest is the **web UI** at `http://<host>:8200` → Restore. It uses the
stored credentials, so nothing needs extracting.

> [!WARNING]
> **On a host whose source files still exist, the web UI does not read B2.**
> Duplicati rebuilds files from matching blocks it finds locally, so a restore
> of an archive that is still in `~/.local/state/homelab/backups` finishes in
> seconds and proves nothing about the offsite copy. Measured 2026-09-24:
> 5 s through the API, with a hash identical to the local file.
>
> To **test** the offsite copy, force every block to come from the bucket.
> Run `duplicati-cli` inside the container with `--no-local-blocks=true` and a
> fresh `--dbpath`. Take the target URL and passphrase from `export-cmdline`
> (see above), and put the passphrase in a `--parameters-file`. That took 49 s
> for 76.5 MiB and logged each `dblock` download. Exit code 2 means warnings,
> not failure; under `set -e` it still stops the script, so do cleanup in a
> `trap`. First run 2026-09-24.

On a *rebuilt* host with no Duplicati config, restore direct from the bucket
with credentials from your password manager:

```bash
docker run --rm -v /tmp/restore:/out duplicati/duplicati:latest \
  duplicati-cli restore \
  "b2://homelab-offsite-<suffix>/homelab?auth-username=<KEY_ID>&auth-password=<APP_KEY>" \
  "*" --restore-path=/out --passphrase="<PASSPHRASE>"
```

Duplicati rebuilds its index from the remote first, so this is slower than a
local extract. Add `--version=N` to pick an older snapshot (`0` is newest).

> [!TIP]
> Put the passphrase in a `--parameters-file` instead of on the command line
> if you care about it appearing in `ps` output.

---

## 3. Extracting one volume from an archive

Archive members are stored **with a leading `/`**, so literal paths do not
match and `tar` silently reports "Not found in archive". Use wildcards:

```bash
A=~/.local/state/homelab/backups/homelab-2026-08-24T03-09-16.tar.gz

tar tzf "$A" | awk -F/ '{print $2}' | sort -u          # what is in here
tar xzf "$A" -C /tmp/dr --wildcards '*/authentik-pg/*' '*/jellyfin-config/*'
```

Everything lands under `/tmp/dr/backup/<name>/`.

---

## 4. Proving an archive is good before trusting it

Do this in throwaway containers. It touches nothing live.

### 4a. SQLite (Jellyfin, headscale, vikunja)

```bash
python3 - <<'EOF'
import sqlite3
p = "/tmp/dr/backup/jellyfin-config/data/jellyfin.db"
c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
print("integrity:", list(c.execute("PRAGMA integrity_check"))[0][0])
for u, pw, prov in c.execute(
        "SELECT Username, Password, AuthenticationProviderId FROM Users"):
    print(u, "pw=" + ("set" if pw else "NONE"), prov.split(".")[-1])
EOF
```

Expect `integrity: ok` and **no `jellyfin.db-wal` file** beside the database.
A stray WAL means the container was running during the archive — check its
`docker-volume-backup.stop-during-backup` label.

> [!WARNING]
> When reading a **live** SQLite database (not a restored one), copy
> `-wal` and `-shm` too. `docker cp` of just the `.db` shows stale data, and
> recently written rows appear to be missing. This produced a false
> "the user does not exist" during the drill.

### 4b. PostgreSQL (Authentik)

```bash
docker volume create drpg
docker run --rm -v /tmp/dr/backup/authentik-pg:/src:ro -v drpg:/dst alpine \
  sh -c 'cp -a /src/. /dst/ && chown -R 70:70 /dst && chmod 700 /dst'
docker run -d --name drpg-test -v drpg:/var/lib/postgresql/data postgres:16-alpine

docker logs drpg-test 2>&1 | grep -iE 'ready to accept|recovery|PANIC'
docker exec drpg-test psql -U authentik -d authentik -c \
  'SELECT username, type, is_active FROM authentik_core_user ORDER BY username;'
```

`chown 70:70` is the `postgres` uid in the Alpine image, and `chmod 700` is
mandatory — Postgres refuses to start on a loose data directory.

**Measured: ready in 6 seconds, no crash recovery**, because the container is
stopped during the archive.

Useful sanity queries — put SQL in a **file** and use `psql -f`; nested shell
quoting mangles string literals into identifiers:

```sql
SELECT g.name, string_agg(u.username, ', ')
FROM authentik_core_group g
LEFT JOIN authentik_core_user_groups m ON m.group_id = g.group_uuid
LEFT JOIN authentik_core_user u ON u.id = m.user_id
GROUP BY g.name;

SELECT COUNT(*) FROM authentik_flows_flow;              -- expect ~15
SELECT COUNT(*) FROM authentik_policies_policybinding;  -- expect ~14
```

A password of exactly **41 characters** is Django's *unusable password*
marker (`!` + 40 random chars): the account exists but has no usable
credential and cannot log in. Real hashes are much longer.

Tear down when done:

```bash
docker rm -f drpg-test; docker volume rm drpg; rm -rf /tmp/dr
```

---

## 5. Restoring into a live volume

Verified 2026-08-24 by restoring `life-queue_vikunja-data` in both directions
(archive → live, then safety copy → live) and confirming byte-identical
checksums and a working app.

### Always take a safety copy first

The archive is hours old; the live volume is current. Restoring **discards
everything since the last backup**. Make the operation reversible before it
begins:

```bash
mkdir -p /tmp/safety
docker run --rm -v <volume>:/v:ro -v /tmp/safety:/out alpine \
  tar czf /out/<volume>-pre-restore.tar.gz -C /v .
docker run --rm -v <volume>:/v:ro alpine \
  sh -c 'cd /v && find . -type f -exec md5sum {} + | sort -k2' > /tmp/safety/live.md5
```

`/tmp` on `homelab` is tmpfs — the safety copy dies at reboot. Move it to
`~/` if the work will span one.

### Stop the writer, then PROVE it stopped

```bash
cd /opt/homelab/stacks/<stack>
docker compose config --services      # service names != container names
docker compose stop <service>
docker ps -a --filter name=<container> --format '{{.Names}}\t{{.Status}}'
```

> [!CAUTION]
> **Confirm the container actually exited before continuing.** During the
> drill `docker compose stop life-queue-app` failed with `no such service`
> — the compose services are `vikunja` and `n8n`, while the *container* is
> `life-queue-app`. The volume was then rewritten under a live SQLite
> writer. It survived, but that is luck, not method. A stop command that
> printed an error is not a stopped container.

### Replace the contents

```bash
docker run --rm -v /tmp/dr/backup/<vol>:/src:ro -v <volume>:/dst alpine \
  sh -c 'rm -rf /dst/* /dst/.[!.]* 2>/dev/null; cp -a /src/. /dst/ && chown -R <uid>:<gid> /dst'
```

Ownership is per image and must match what was there before — check with
`ls -la` on the volume first. Observed: Vikunja `1000:0`, Jellyfin config
`1000:1000`, Postgres `70:70` plus `chmod 700`.

> [!IMPORTANT]
> **Restore `-wal` and `-shm` alongside the `.db`, never the `.db` alone.**
> Volumes whose container shuts down cleanly (Jellyfin, Postgres) archive
> with no WAL. Vikunja's does *not*: its archive carried a 4 MB
> `vikunja.db-wal`, holding transactions absent from the main file. Restoring
> only `vikunja.db` would have silently rolled the data back.

### Verify, then start

```bash
docker run --rm -v <volume>:/v:ro alpine \
  sh -c 'cd /v && find . -type f -exec md5sum {} + | sort -k2' > /tmp/safety/after.md5
diff -u /tmp/safety/live.md5 /tmp/safety/after.md5   # when undoing, expect no output

docker compose up -d
docker inspect <container> -f '{{.State.Status}} exit={{.State.ExitCode}}'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/<healthpath>
docker logs <container> --since 2m 2>&1 | grep -iE 'error|panic|corrupt'
```

Measured for Vikunja: container running, `HTTP 200`, migrations clean,
`integrity_check: ok`, 12 tasks / 7 projects — matching the pre-test state
exactly.

### Rebuilding the whole host

`docs/host-setup.md` first (Docker pools, log caps, lid behaviour), then
`.env` files from the password manager, then the volumes above. The backup
stack will not start until every volume it declares exists, which on a
half-restored host is never — run `uv run scripts/backup_here.py` and bring it
up with the `compose.host.yaml` that writes, then re-render as stacks land.

---

## 6. Things that will waste your time

- **`/source` is mounted read-only** into Duplicati. A restore aimed at the
  original location cannot clobber the archives. This is deliberate.
- **The API masks secrets.** See §2a.
- **`POST /api/v1/backup/1/restore` with an empty body returns `200 OK`** and
  starts a task that restores nothing. Success there does not mean success.
  Always pass explicit paths and confirm files landed.
- **`restore-path` in the JSON body was not honoured** in Duplicati 2.3.0; the
  task completed having restored zero files. Prefer the UI or the CLI.
- **Container cron is deliberately set to 31 February** and never fires.
  Scheduling is the `homelab-backup` systemd timer, which has
  `Persistent=true` so a run missed while the host was off happens at boot.
- **Compose service names are not container names.** `docker compose stop`
  takes the service (`vikunja`), not the container (`life-queue-app`), and
  fails loudly-but-harmlessly if you use the wrong one — leaving the writer
  running while you overwrite its files.
- **The backup container's mount list is fixed at creation.** Add a stateful
  stack, then recreate it, or it keeps archiving the old set while reporting
  success. This silently excluded `authentik_database` and `caddy_data`
  until 2026-08-23.

---

## 6b. Headscale: what a restore brings back, and what it does not

Headscale's state lives in four places, and they restore by different routes.
Three come back on their own. **One does not, and that is the one that fails
silently.**

| What | Where it lives | Restored by |
|---|---|---|
| control-plane DB, DERP private key | `headscale_data` volume | the nightly volume archive (§3) |
| node keys, tags, approved routes | inside the DB above | the volume archive |
| `config.yaml`, `derp/custom.yaml.in` | the git checkout at `/opt/homelab` | `git clone`, or the VM 101 `vzdump` |
| `policy.hujson`, `extra-records.json` | the site checkout, `SITE_DIR` (`/opt/homelab-private`) | `git clone` of `homelab-private`, or the VM 101 `vzdump` |
| **`derp/custom.yaml`** — the rendered DERP map | **nowhere; it is generated** | **`uv run scripts/render_derp_map.py`** |

**`derp/custom.yaml` is what makes direct connections possible, and it is
deliberately NOT in git.** It carries `DUCKDNS_HOST`, and
`scripts/test_identity_leak.py` forbids a deployment hostname in any config
file. The tracked file is the template `custom.yaml.in`; the rendered file is
ignored by name in `.gitignore`, the same way `stacks/backup/compose.host.yaml`
is. A restore therefore brings back the template and not the thing headscale
reads.

### Restoring, in order

The order matters: render before recreating, or headscale starts with
`derp.paths` pointing at a file that does not exist.

The policy and DNS records mount from the site checkout, `SITE_DIR` in
`stacks/headscale/.env` (normally `/opt/homelab-private`). Clone it first:
a missing `SITE_DIR` directory stops the container from starting at all.

```bash
git clone <your site repo> /opt/homelab-private   # SITE_DIR; see site.example/
ls /opt/homelab-private/stacks/headscale/site   # policy.hujson  extra-records.json
cd /opt/homelab
uv run scripts/render_derp_map.py            # writes config/derp/custom.yaml
cd stacks/headscale && docker compose up -d --force-recreate
```

`--force-recreate` is not optional. The config is a bind mount, so the compose
definition has not changed and a plain `up -d` prints
`Container headscale Running` and re-reads nothing.

Verify the checkout is intact — an untracked config here fails silently:

```bash
git ls-files stacks/headscale/config/
# must list: config.yaml, derp/custom.yaml.in
# must NOT list derp/custom.yaml -- if it appears, the rendered file has been
# committed and your public hostname is in the repo.
git -C /opt/homelab-private ls-files stacks/headscale/site/
# must list: extra-records.json, policy.hujson
```

### The check that actually matters, because the failure is silent

A headscale that starts, serves DNS, and relays every packet through DERP
looks completely healthy. That was the real state of this tailnet for months
(`stacks/headscale/README.md`). So after a restore — and after any Tailscale
client update — confirm direct connections came back:

```bash
ssh homelab 'tailscale netcheck | grep IPv4:'      # must be the PUBLIC address
ssh homelab 'tailscale status'                      # peers say "direct", not relay
```

| Reading | Meaning |
|---|---|
| `IPv4: yes, <your public IP>:...` | correct |
| `IPv4: yes, <gateway>:...` or `10.201.7.1:...` | the DERP map was never rendered, or is not being read — everything will relay |
| a peer showing `relay "headscale"` after a minute | same, or the external STUN is blocked |

**You do not have to remember any of this.** `scripts/check_derp.py`, run
daily by `homelab-derp-check.timer`, exits 1 if the rendered map is missing,
still contains `@DUCKDNS_HOST@` because the render never ran, names a
different host than `DUCKDNS_HOST` because the template changed and was not
re-rendered, or if `netcheck` reports a non-routable address. Run it by hand
after a restore rather than waiting for the timer:

```bash
ssh homelab 'cd /opt/homelab && uv run scripts/check_derp.py'
```

**If the WAN address has changed**, edit `ipv4:` in
`stacks/headscale/config/derp/custom.yaml.in` — the **template** — then
re-render and force-recreate as above. Two traps: editing the rendered
`custom.yaml` works until the next render silently reverts it, and the `ipv4:`
under `derp.server` in `config.yaml` is dead config that only feeds the
auto-generated region, which is disabled. That dead line is the one a search
finds first.

## 7. Monthly, in five minutes

1. Run the §2a verify. Expect `Completed`, no error.
2. `ls -lt ~/.local/state/homelab/backups | head -3` — newest is < 24h old.
3. `docker exec backup sh -c 'cd /backup && for d in *; do printf "%-24s %s\n" "$d" "$(ls -A "$d" | wc -l)"; done'`
   — every stack you actually run shows a non-zero count.
4. Once or twice a year, do §3 + §4 for real. A backup you have never
   restored is a hypothesis.
