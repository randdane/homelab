# Rotating the Duplicati secrets

> [!NOTE]
> A copy of this runbook also lives in the Obsidian vault at
> `AoI/Homelab/Backups/Duplicati-Rotation-Steps.md`, for reading away from the
> repo. **This file is the canonical one** — it versions alongside the stack it
> describes. Edit here, then copy across; two copies drift the moment only one
> is updated.

Rotating the secrets behind the Duplicati offsite backup on `homelab`: each
job's B2 application key and restore passphrase, the settings encryption key,
and the web service password.

Written 2026-10-13 against Duplicati 2.3.0.4 (`linuxserver/duplicati:2.3.0`),
stack at `/opt/homelab/stacks/duplicati`. API routes and the settings-key
behaviour were checked against the upstream source at tag
`v2.3.0.4_stable_2026-07-09`; re-check them if the image has moved on.

## Table of Contents

- [When to use this](#when-to-use-this)
- [What each secret protects](#what-each-secret-protects)
- [The two jobs](#the-two-jobs)
- [Before any rotation](#before-any-rotation)
- [Rotating one job's B2 key and passphrase](#rotating-one-jobs-b2-key-and-passphrase)
  - [1. Create the replacement B2 key](#1-create-the-replacement-b2-key)
  - [2. Load the new secrets silently](#2-load-the-new-secrets-silently)
  - [3. Generate the new passphrase](#3-generate-the-new-passphrase)
  - [4. Edit the job](#4-edit-the-job)
  - [5. Drop the job's local database](#5-drop-the-jobs-local-database)
  - [6. Run it](#6-run-it)
  - [7. Verify](#7-verify)
  - [8. Close the old doors](#8-close-the-old-doors)
  - [9. Delete the old chain, later](#9-delete-the-old-chain-later)
- [Rotating the settings encryption key](#rotating-the-settings-encryption-key)
- [Rotating the web service password](#rotating-the-web-service-password)
- [Why this order](#why-this-order)

## When to use this

- A secret's provenance is unknown or unverifiable.
- A secret has been exposed — pasted into a chat, a ticket, a screenshot.
- Routine rotation.

Rotate per job. Each job has its own bucket, key and passphrase precisely so
that one can change without touching the other; nothing in the per-job
procedure below reads or writes the other job.

## What each secret protects

| Secret | Lives in | Protects | Scope |
|---|---|---|---|
| B2 application key | the job's `TargetURL`, in `Duplicati-server.sqlite` | read/write/delete on one bucket | one job |
| Restore passphrase | the job's settings, same DB, + your password manager | the backup contents themselves | one job |
| `SETTINGS_ENCRYPTION_KEY` | `.env` | the secrets inside `Duplicati-server.sqlite` | **every job** |
| `WEBSERVICE_PASSWORD` | `.env` | the API, and `scripts/run_backups.sh`'s login | the server |

> [!WARNING]
> The restore passphrase is the only secret that protects data rather than
> access. It is not in the repo, not in `.env`, and cannot be derived from the
> bucket. Store it in your password manager or on paper *before* running the
> backup that uses it.
>
> If you have forgotten it, **stop and read `docs/recovery.md` → "Reading the
> passphrase back off a running server"** before touching this runbook. While
> the container runs, the value can be exported in cleartext. On 2026-08-27
> that was the only surviving copy, and an earlier version of this runbook
> would have deleted it.
>
> **Not Vaultwarden.** That stack is `lifecycle: planned` and has never run. Do
> not put the one unrecoverable secret in the lab the secret exists to recover.
> Somewhere outside the house is the requirement.

`docs/recovery.md` has the drill that proves the passphrase you stored is the
one the bucket wants: `./scripts/recovery-drill.sh`.

## The two jobs

| Job (ID) | Sources | Filters (exclude) | Bucket / prefix | `keep-time` |
|---|---|---|---|---|
| `offsite-data` (5) | `/source/`, `/source-state/` | `/source-state/backups/` | `homelab-data-<suffix>/duplicati` | `10D` |
| `offsite-photos` (6) | `/source-photos/` | `/source-photos/thumbs/`, `/source-photos/encoded-video/` | `homelab-photos-<suffix>/duplicati` | `90D` |

Both: `encryption-module aes`, `compression-module zip`,
`--zip-compression-level 1`, `dblock-size 50MB`; auto-compact on (the
default — there is no `no-auto-compact` setting). No Duplicati schedule:
`scripts/run_backups.sh` runs the IDs in `DUPLICATI_JOBS` in `.env`.

The per-job procedure **edits** a job, so all of this survives untouched. If
you ever have to recreate one instead, the IDs change, and `DUPLICATI_JOBS`
must change with them.

Both buckets have Object Lock, governance, 7 days. The Duplicati keys cannot
bypass it (no `bypassGovernance`), so nothing they hold can delete an object
younger than a week. That is why this runbook never purges a bucket.

## Before any rotation

Run on `homelab`, from the stack directory. Stay clear of the nightly run at
03:05 to 03:15 (`homelab-backup.timer`): it stops and restarts Duplicati for the local
archive, then runs both jobs.

```sh
cd /opt/homelab/stacks/duplicati
login() {
  TOK=$(curl -s -X POST http://127.0.0.1:8200/api/v1/auth/login \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c 'import json,sys;print(json.dumps({"Password":sys.argv[1]}))' \
              "$(grep -E '^WEBSERVICE_PASSWORD=' .env | cut -d= -f2-)")" \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessToken"])')
}
login
api() { curl -s -H "Authorization: Bearer $TOK" "http://127.0.0.1:8200/api/v1$@"; }
```

Tokens expire; if a call answers 401 part-way through, run `login` again.

## Rotating one job's B2 key and passphrase

Set the job first; every step below uses these:

```sh
JOB=5;  BUCKET=homelab-data-<suffix>     # or: JOB=6; BUCKET=homelab-photos-<suffix>
OLD_PREFIX=duplicati                     # read the current one off the job: api /backup/$JOB
NEW_PREFIX=duplicati-$(date +%Y%m%d)
```

**A new passphrase means a new chain.** Existing volumes are encrypted with
the old one and cannot be extended, and the bucket cannot be emptied first:
Object Lock refuses. So the job moves to a new prefix in the same bucket, and
the old chain stays where it is until it is old enough to delete (step 9).

**Rotating only the B2 key does not need any of that.** Same chain, same
prefix, same local database: do steps 1, 2, 4 (leave `PASSPHRASE` unset and
`NEW_PREFIX` equal to `OLD_PREFIX`), 6, 7 and 8, and skip 3, 5 and 9.

Budget for the new chain. It is a full upload, and both chains are billed
until step 9: about 1.2 GB for data, about 21 GB for photos (sizes from the
first runs on 2026-10-12).

### 1. Create the replacement B2 key

Create a **second** key; do not delete the old one yet. Do it over the API,
not the web UI: the UI's "Read and Write" key includes `bypassGovernance`,
which is the one capability these keys must not have.

Run this on your workstation, not `homelab` — it needs the B2 account's
master key, which never belongs on the server.

```sh
read -rsp 'B2 master keyID: '          MKID;  echo
read -rsp 'B2 master applicationKey: ' MKEY;  echo
read -rp  'bucket name: '              BUCKET
export MKID MKEY BUCKET
python3 - <<'PY'
import json, os, base64, urllib.request
def call(url, body=None, auth=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))
basic = "Basic " + base64.b64encode(f"{os.environ['MKID']}:{os.environ['MKEY']}".encode()).decode()
a = call("https://api.backblazeb2.com/b2api/v3/b2_authorize_account", auth=basic)
api, tok = a["apiInfo"]["storageApi"]["apiUrl"], a["authorizationToken"]
b = call(api + "/b2api/v3/b2_list_buckets",
         {"accountId": a["accountId"], "bucketName": os.environ["BUCKET"]}, tok)["buckets"][0]
k = call(api + "/b2api/v3/b2_create_key", {
    "accountId": a["accountId"], "bucketId": b["bucketId"],
    "keyName": f"duplicati-homelab-{os.environ['BUCKET'].split('-')[1]}-2",
    "capabilities": ["listBuckets", "listFiles", "readFiles", "writeFiles", "deleteFiles"]}, tok)
caps = sorted(k["capabilities"])
print("capabilities:", caps)
assert "bypassGovernance" not in caps, "STOP: key can bypass Object Lock -- delete it"
print("keyID:         ", k["applicationKeyId"])
print("applicationKey:", k["applicationKey"])
PY
unset MKID MKEY
```

- **Name** after the consumer and bump the number, so revoking a key tells
  you which job to go fix.
- **One bucket, never all.** `bucketId` is singular on API v3.
- **`deleteFiles` is required**, or retention and compaction fail.
- **No expiry.** An expiring key stops backups on a date you have forgotten,
  and the failure looks like a network problem months later.

B2 shows the application key once. Put both values in your external
password manager now, then clear the terminal.

### 2. Load the new secrets silently

On `homelab`. `read -rs` does not echo and does not enter shell history.
Paste from the password manager, not from the terminal you just cleared —
that checks the transcription too.

```sh
read -rsp 'new B2 keyID: '          B2KEYID;  echo
read -rsp 'new B2 applicationKey: ' B2APPKEY; echo
export B2KEYID B2APPKEY
```

### 3. Generate the new passphrase

```sh
PASSPHRASE=$(openssl rand -base64 30 | tr -d '/+=' | head -c 32); export PASSPHRASE
printf '%s\n' "$PASSPHRASE"
```

> [!WARNING]
> Put this in your **external** password manager now, before continuing, in
> the same entry as the new bucket path `<bucket>/$NEW_PREFIX` and the new
> key. Everything after this point encrypts with it. Not Vaultwarden.
>
> **Keep the old passphrase, key ID and path too**, marked as the old chain.
> The old chain is still a restorable backup until step 9 deletes it, and
> those are the only values that open it.
>
> Then check the transcription — read it back **out of the password manager**:
>
> ```sh
> read -rsp 'paste it back from the password manager: ' CHECK; echo
> if [ "$CHECK" = "$PASSPHRASE" ]; then echo match; else echo "** MISMATCH — fix it now **"; fi
> unset CHECK
> ```
>
> Do **not** run `recovery-drill.sh` yet. The new prefix is empty until step
> 6, so the drill must fail; the old values would pass and prove nothing about
> what you just stored. It belongs in step 7.

### 4. Edit the job

This reads the job, changes only the target URL and the passphrase, and
writes it back. Sources, filters, retention and the other settings go back
exactly as read. Values left masked in the read (`auth-password`,
`passphrase`) are restored by the server from what it already holds, so an
unset `PASSPHRASE` keeps the current one.

```sh
export OLD_PREFIX NEW_PREFIX
api /backup/$JOB > /tmp/job.json; chmod 600 /tmp/job.json
python3 - /tmp/job.json <<'PY'
import json, os, sys, urllib.parse as up
path = sys.argv[1]
d = json.load(open(path)); b = d["Backup"]
u = up.urlsplit(b["TargetURL"])
bucket, prefix = u.netloc, u.path.strip("/")
assert prefix == os.environ["OLD_PREFIX"], f"job writes to {prefix!r}, not OLD_PREFIX"
q = dict(up.parse_qsl(u.query, keep_blank_values=True))
if os.environ.get("B2KEYID"):
    q["auth-username"], q["auth-password"] = os.environ["B2KEYID"], os.environ["B2APPKEY"]
new = os.environ["NEW_PREFIX"]
if os.environ.get("PASSPHRASE"):
    assert new != prefix, "a new passphrase needs a new prefix"
    for s in b["Settings"]:
        if s["Name"] == "passphrase":
            s["Value"] = os.environ["PASSPHRASE"]
b["TargetURL"] = f"b2://{bucket}/{new}?" + up.urlencode(q, quote_via=up.quote)
json.dump({"Backup": b, "Schedule": d.get("Schedule")}, open(path, "w"))
print(f"job {b['ID']} {b['Name']}: b2://{bucket}/{prefix} -> b2://{bucket}/{new}")
PY
api /backup/$JOB -X PUT -H 'Content-Type: application/json' --data-binary @/tmp/job.json; echo
shred -u /tmp/job.json
```

`PUT` answers `{"Status":"OK"}`
on success. Read the job back (`api /backup/$JOB`) and check the path,
sources, filters and settings against [the table](#the-two-jobs).

`Schedule` goes back as `null`, as read. The jobs have no Duplicati schedule
on purpose; do not add one.

### 5. Drop the job's local database

Skip this for a key-only rotation.

The local database records every volume of the old chain. Pointed at the new,
empty prefix, Duplicati finds all of them missing and refuses to run until
it is repaired, and repairing would mean rebuilding the old chain into the
new prefix. Delete it; the next run starts a fresh one at the same path.

```sh
api /backup/$JOB/isactive; echo          # must not be active
api /backup/$JOB/deletedb -X POST; echo
```

This deletes the file named by the job's `DBPath` and nothing else. The other
job's database, and the settings store, are separate files. The old chain does
not need it: a restore from there uses `--no-local-db`.

### 6. Run it

```sh
TASK=$(api /backup/$JOB/run -X POST | python3 -c 'import json,sys;print(json.load(sys.stdin)["ID"])')
until api /task/$TASK | python3 -c '
import json,sys; t=json.load(sys.stdin); print(t["Status"], t.get("ErrorMessage") or "")
sys.exit(t["Status"] not in ("Completed","Failed","Aborted"))'; do sleep 15; done
```

Expect `Completed` with no message. Poll the task, not `/progressstate`,
which goes on reporting a phase after the task has ended. Photos took 8.5
minutes for its first full upload; if the token expires mid-wait, `login` and
re-run the `until` loop.

### 7. Verify

> [!IMPORTANT]
> An untested backup is a belief, not a backup. Do not skip this, and do not
> substitute "the job went green" — a job can report success while restoring
> nothing.

Restore one file from the new prefix with no local database, compare it with
its source, then prove a wrong passphrase gets nothing. Run `duplicati-cli`
as `abc`, **never as root**: on 2026-10-12 a root run re-owned `/config` and
every login, `run_backups.sh`'s included, answered 401 until it was chowned
back.

```sh
# a file to restore: data -> newest archive; photos -> a small original
FILE=$(docker exec duplicati sh -c 'ls -1t /source/*.tar.gz | head -1')          # data
# FILE=$(docker exec duplicati sh -c 'find /source-photos/library -type f -size +1k | head -1')  # photos

q() { python3 -c 'import os,sys,urllib.parse as u;print(u.quote(os.environ[sys.argv[1]],safe=""))' "$1"; }
URL="b2://$BUCKET/$NEW_PREFIX?auth-username=$(q B2KEYID)&auth-password=$(q B2APPKEY)"

restore() {   # $1 = passphrase, $2 = scratch dir
  printf -- '--passphrase=%s\n' "$1" \
    | docker exec -i -u abc duplicati sh -c "umask 077; cat > $2.params; rm -rf $2; mkdir -p $2"
  docker exec -u abc duplicati /app/duplicati/duplicati-cli restore "$URL" "$FILE" \
    --parameters-file="$2.params" --restore-path="$2" --no-local-db=true 2>&1 | tail -3
}

restore "$PASSPHRASE" /tmp/rtest
ORIG=$(docker exec duplicati sha256sum "$FILE" | cut -d' ' -f1)
REST=$(docker exec duplicati sh -c 'find /tmp/rtest -type f -exec sha256sum {} +' | cut -d' ' -f1)
if [ "$ORIG" = "$REST" ]; then echo "MATCH — new chain verified end to end"; else echo "** MISMATCH — stop and investigate **"; fi

restore wrong-on-purpose /tmp/wp >/dev/null
echo "files recovered with wrong passphrase: $(docker exec duplicati sh -c 'find /tmp/wp -type f | wc -l')"  # expect 0

docker exec -u abc duplicati rm -rf /tmp/rtest /tmp/rtest.params /tmp/wp /tmp/wp.params
```

If you did not rotate the key, step 2 left `B2KEYID` unset: read the current
values from the password manager into it first.

`--no-local-db` tests the *disaster* path, where the local database died with
the machine. A restore that only works with it intact is not the restore you
will need. The wrong-passphrase check distinguishes "encrypted with a
passphrase only I hold" from "sitting behind an account login" — the
difference that matters if the B2 key ever leaks.

`duplicati-cli` exits 2 on warnings; that is not a failure.

#### Then the drill

Everything above ran with `$PASSPHRASE` already in the shell and the
container to hand. That proves the new chain restores. It does not touch the
path you are on after `homelab`'s disk dies, where the only inputs are what
you wrote down:

```sh
./scripts/recovery-drill.sh        # from your workstation, not the server
```

Answer every prompt from the password manager, including the bucket **and
the new prefix** — the drill asks for `<bucket>/<path>`, and the path is
what just changed. If you reach for `$PASSPHRASE` in this shell, or for
`.env`, the rotation is not finished.

Record the result in the drill table in `docs/recovery.md`.

### 8. Close the old doors

> [!WARNING]
> Not until step 7 printed `MATCH` and `0`, and the drill passed. Until then
> the old key is the only one known to work.

```sh
unset B2KEYID B2APPKEY PASSPHRASE
```

Then delete the old application key in Backblaze (web UI, or
`b2_delete_key`). A key-only rotation is finished here.

### 9. Delete the old chain, later

Nothing deletes the old prefix on its own: the job no longer looks at it,
and the bucket lifecycle rule only removes *hidden* versions. It sits there,
billed, until you delete it.

**When.** Not before Object Lock allows — 7 days after the old chain's last
upload, which was the run before step 6. Whether to wait longer is a
judgement about history: the old chain holds the versions the new one does
not have yet.

- **data**: wait out `keep-time` (10 days), and the new chain has caught up.
- **photos**: `keep-time` is 90 days, the protection against an unnoticed
  mass delete in Immich. Deleting the old chain at day 7 gives that up for
  three months; keeping it costs about 21 GB of storage. If the rotation was
  because the old passphrase or key leaked, delete at day 7 anyway.

Use the new Duplicati key; its `deleteFiles` covers the whole bucket. The
prefix **must end in `/`**: `duplicati` without it also matches
`duplicati-20261020/`, the chain you are keeping.

```sh
read -rsp 'B2 keyID (the job key): '          B2KEYID;  echo
read -rsp 'B2 applicationKey (the job key): ' B2APPKEY; echo
export B2KEYID B2APPKEY
OLD=duplicati/ python3 - <<'PY'
import json, os, base64, urllib.request, urllib.error
old = os.environ["OLD"]
assert old.endswith("/"), "prefix must end in /"
basic = base64.b64encode(f"{os.environ['B2KEYID']}:{os.environ['B2APPKEY']}".encode()).decode()
a = json.load(urllib.request.urlopen(urllib.request.Request(
    "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
    headers={"Authorization": "Basic " + basic})))
tok, api = a["authorizationToken"], a["apiInfo"]["storageApi"]["apiUrl"]
bid = a["apiInfo"]["storageApi"]["bucketId"]
def post(p, body):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        api + p, data=json.dumps(body).encode(),
        headers={"Authorization": tok, "Content-Type": "application/json"})))
ok = locked = 0; start = None
while True:
    body = {"bucketId": bid, "prefix": old, "maxFileCount": 1000}
    if start: body.update(start)
    r = post("/b2api/v3/b2_list_file_versions", body)
    for f in r["files"]:
        try:
            post("/b2api/v3/b2_delete_file_version", {"fileName": f["fileName"], "fileId": f["fileId"]}); ok += 1
        except urllib.error.HTTPError:
            locked += 1
    if not r.get("nextFileName"): break
    start = {"startFileName": r["nextFileName"], "startFileId": r["nextFileId"]}
print(f"deleted {ok}, refused {locked} (still locked -- run again later)")
PY
unset B2KEYID B2APPKEY
```

Any refusals mean some object is still inside its lock; re-run after it
expires. Once the old prefix is empty, remove the old chain's entry from the
password manager.

## Rotating the settings encryption key

`SETTINGS_ENCRYPTION_KEY` encrypts the secrets inside
`Duplicati-server.sqlite` — every job's B2 key and passphrase at once. It is
not per job, and it changes nothing in B2.

**Duplicati 2.3 has no "change key" operation.** The server takes exactly
one key at startup; started with a different key it cannot read the stored
fields. What it does have is a rewrite on startup when the encryption *state*
changes (`ReWriteAllFieldsIfEncryptionChanged` in the server source): start
once with encryption disabled, and it decrypts every field with the old key
and stores it plain; start again with a new key, and it encrypts them all
with that. Two restarts, no job touched. This is read from the source at the
2.3.0.4 tag, not yet run here — so take the rollback copy first.

Between the two restarts the job secrets sit unencrypted in the `config`
volume. Keep the gap to minutes, and never across 03:00: the nightly archive
copies that volume, and from there it goes offsite.

```sh
cd /opt/homelab/stacks/duplicati
login; api /serversettings | python3 -c 'import json,sys;print("encrypted-fields:",json.load(sys.stdin)["encrypted-fields"])'
# expect True

# rollback: the store as it is now, still under the old key
docker compose stop
docker run --rm -v duplicati_config:/c alpine:3.20 cp -p /c/Duplicati-server.sqlite /c/Duplicati-server.sqlite.pre-rekey
cp -p .env .env.pre-rekey

# phase 1: old key, encryption off -> fields rewritten in plaintext
cat > /tmp/rekey.yaml <<'EOF'
services:
  duplicati:
    environment:
      - DUPLICATI__DISABLE_DB_ENCRYPTION=true
EOF
docker compose -f compose.yaml -f /tmp/rekey.yaml up -d
until curl -sf -o /dev/null http://127.0.0.1:8200/api/v1/systeminfo; do sleep 2; done
login; api /serversettings | python3 -c 'import json,sys;print("encrypted-fields:",json.load(sys.stdin)["encrypted-fields"])'
# expect False. If it is still True, stop here: see rollback below.

# phase 2: new key, encryption on -> fields rewritten under the new key
docker compose stop
sed -i "s|^SETTINGS_ENCRYPTION_KEY=.*|SETTINGS_ENCRYPTION_KEY=$(openssl rand -base64 32)|" .env
awk -F= '/^SETTINGS_ENCRYPTION_KEY=/{print "key length:", length($2)}' .env   # expect 44
docker compose up -d                     # without the override
rm /tmp/rekey.yaml
until curl -sf -o /dev/null http://127.0.0.1:8200/api/v1/systeminfo; do sleep 2; done
login; api /serversettings | python3 -c 'import json,sys;print("encrypted-fields:",json.load(sys.stdin)["encrypted-fields"])'
# expect True
```

`openssl` writes straight into `sed`, so the key never reaches the terminal.
Base64 cannot contain `|` or `&`, so it is safe with that delimiter. Copy the
new key into the password manager from `.env` (`grep`, copy, clear) — losing
it means re-entering both jobs by hand.

**Check it worked**, on both jobs:

```sh
for J in 5 6; do api /backup/$J | python3 -c '
import json,sys; b=json.load(sys.stdin)["Backup"]; print(b["ID"], b["Name"], b["TargetURL"].split("?")[0], len(b["Sources"]), "sources")'; done
/opt/homelab/scripts/run_backups.sh --verify  # downloads, decrypts, hashes both jobs
```

The verify reads the B2 key out of the re-encrypted store and the passphrase
with it; if either had been mangled it fails. Then the per-job `MATCH` check
from [step 7](#7-verify) is optional, since no job's secrets changed.

Then remove the rollback copies:

```sh
docker run --rm -v duplicati_config:/c alpine:3.20 rm /c/Duplicati-server.sqlite.pre-rekey
shred -u .env.pre-rekey
```

**Rollback**, if any check fails: `docker compose stop`, copy
`Duplicati-server.sqlite.pre-rekey` back over `Duplicati-server.sqlite`,
restore `.env.pre-rekey`, `docker compose up -d` without the override. The
copy was made under the old key, so the old key reads it.

**If the two-phase rewrite does not work at all**, the fallback is export and
re-import of both jobs: `api "/backup/$J/export-cmdline?export-passwords=true"`
into a `chmod 600` file for each (see `docs/recovery.md`), wipe
`Duplicati-server.sqlite` with the new key in `.env`, and recreate each job
with `POST /api/v1/backups` — which demands the passphrase — then fix
`DUPLICATI_JOBS`, because the IDs will be new. The local databases can be
kept: point each new job's `DBPath` at its old file with
`POST /backup/<id>/updatedb`. That is much more work and more ways to get a
setting wrong; it is the fallback for a reason.

Old copies of the store, encrypted under the old key, remain in the local
archives (3 days) and in `offsite-data` (10 days). Keep the old key in the
password manager until those have aged out, or a restore of the `config`
volume from that window cannot be read.

## Rotating the web service password

`WEBSERVICE_PASSWORD` in `.env` is read twice: Compose passes it to the
container as `DUPLICATI__WEBSERVICE_PASSWORD`, and `scripts/run_backups.sh`
greps it from the same file to log in. One edit covers both — as long as
nothing else holds a copy.

```sh
cd /opt/homelab/stacks/duplicati
cp -p .env .env.bak
sed -i "s|^WEBSERVICE_PASSWORD=.*|WEBSERVICE_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')|" .env
docker compose up -d                  # recreates the container with the new value
/opt/homelab/scripts/run_backups.sh --dry-run    # logs in exactly as the nightly run does
```

Expect `dry-run: authenticated OK`. If it fails, restore `.env.bak` and
`docker compose up -d`. Otherwise `shred -u .env.bak`, and put the new value
in the password manager: it is the password for the web UI too, behind
Caddy and Authentik.

## Why this order

Every step keeps a working path until its replacement is proven:

1. The new B2 key is created **before** the old one is revoked.
2. The passphrase is stored **before** anything encrypts with it.
3. The new chain goes to a new prefix, so the old chain stays restorable
   until the new one has passed a restore.
4. The settings store is copied before it is rewritten, and the copy
   survives until both jobs verify.
5. The old B2 key is deleted, and the old chain removed, **last**.

The failure this avoids: rotating a credential, discovering the replacement
is wrong, and finding the old one already destroyed. Backups are the one
system where that mistake has no undo.
