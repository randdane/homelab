# Rotating the Duplicati secrets

> [!NOTE]
> A copy of this runbook also lives in the Obsidian vault at
> `AoI/Homelab/Backups/Duplicati-Rotation-Steps.md`, for reading away from the
> repo. **This file is the canonical one** — it versions alongside the stack it
> describes. Edit here, then copy across; two copies drift the moment only one
> is updated.

Rotating every secret used by the Duplicati offsite backup on `homelab`: the
settings encryption key, the Backblaze B2 application key, and the restore
passphrase.

> [!IMPORTANT]
> **Object Lock landed after this runbook was written (2026-08-23).** The
> bucket now has governance retention of 14 days, which changes the passphrase
> rotation specifically: a new passphrase starts a new backup chain, and the
> objects encrypted under the old one cannot be deleted until their retention
> expires — not without a key holding `bypassGovernance`. Expect both chains to
> coexist for up to two weeks, and budget the storage rather than trying to
> clean up early.
>
> Also rotate `WEBSERVICE_PASSWORD`, which this runbook does not cover:
> `scripts/run_backups.sh` reads it from `stacks/duplicati/.env` to trigger the
> offsite job, so changing it in one place silently breaks the nightly chain.
>
> Deferred and tracked as task 12 in life-queue (Homelab, due 2026-08-30)
> rather than done inline.

Written 2026-08-21 against Duplicati 2.3.0 (linuxserver image), stack at
`/opt/homelab/stacks/duplicati`, bucket `homelab-offsite-<suffix>`. Commands are
version-specific — re-check paths and API routes if Duplicati has moved on.

## Table of Contents

- [When to use this](#when-to-use-this)
- [What each secret protects](#what-each-secret-protects)
- [0. Create the replacement B2 key](#0-create-the-replacement-b2-key)
- [1. Load the new secrets silently](#1-load-the-new-secrets-silently)
- [2. Generate the new restore passphrase](#2-generate-the-new-restore-passphrase)
- [3. Rotate the settings key](#3-rotate-the-settings-key)
- [4. Wipe the old settings store](#4-wipe-the-old-settings-store)
- [5. Purge the old chain from B2](#5-purge-the-old-chain-from-b2)
- [6. Recreate the job](#6-recreate-the-job)
- [7. Run it](#7-run-it)
- [8. Verify](#8-verify)
- [9. Close the old doors](#9-close-the-old-doors)
- [Why this order](#why-this-order)

## When to use this

- A secret's provenance is unknown or unverifiable.
- A secret has been exposed — pasted into a chat, a ticket, a screenshot.
- Routine rotation.

Duplicati has **no in-place rotation** for the settings encryption key.
`duplicati-server` accepts `--settings-encryption-key` but offers no re-encrypt
option, so rotation means deleting the settings database and recreating the
job.

## What each secret protects

| Secret | Lives in | Protects | Recoverable? |
|---|---|---|---|
| `SETTINGS_ENCRYPTION_KEY` | `.env` | Duplicati's settings DB, which holds the B2 credentials | No — but cheap to replace by wiping the DB |
| B2 application key | Duplicati settings DB | Write/delete access to the bucket | Yes — make a new one in Backblaze |
| Restore passphrase | Duplicati settings DB + your password manager | The backup contents themselves | **Only off a running server** — see `docs/recovery.md`. Once the disk is gone, no. |

> [!WARNING]
> The restore passphrase is the only one that protects data rather than
> access. It is not in the repo, not in `.env`, and cannot be derived from the
> bucket. Store it in your password manager or on paper *before* running the
> backup that uses it.
>
> If you have forgotten it, **stop and read
> `docs/recovery.md` → "Reading the passphrase back off a running server"
> before you touch this runbook.** While the container still runs, the value
> can be exported in cleartext. Step 4 below deletes the settings store, which
> on 2026-08-27 was the only surviving copy — running this runbook to "fix" a
> forgotten passphrase destroys the thing that could still have recovered it,
> and Object Lock then keeps the unreadable chain around for 14 days.
>
> **Not Vaultwarden.** This document used to say Vaultwarden, and that stack is
> `lifecycle: planned` — it has never run. Do not put the one unrecoverable
> secret in the lab the secret exists to recover, and do not wait for a stack
> that is not deployed. Somewhere outside the house is the requirement.

`docs/recovery.md` has the drill that proves the passphrase you stored is
still the one the bucket wants: `./scripts/recovery-drill.sh`.

## 0. Create the replacement B2 key

In Backblaze, create a **second** key. Do not delete the old one yet.

- Name: `duplicati-myserver-2` — name keys after the consumer, so revoking one
  tells you which machine to go fix.
- Bucket: `homelab-offsite-<suffix>` only, never "All".
- Capabilities: read, write, **delete**, list. Delete is required or retention
  pruning fails silently.
- Duration: **blank**. An expiring key means backups stop on a date you have
  forgotten, and the failure looks like a network problem months later.

## 1. Load the new secrets silently

`read -rs` does not echo and does not enter shell history.

```sh
cd /opt/homelab/stacks/duplicati
read -rsp 'new B2 keyID: '          B2KEYID;  echo
read -rsp 'new B2 applicationKey: ' B2APPKEY; echo
export B2KEYID B2APPKEY
```

## 2. Generate the new restore passphrase

```sh
PASSPHRASE=$(openssl rand -base64 30 | tr -d '/+=' | head -c 32); export PASSPHRASE
printf '%s\n' "$PASSPHRASE"
```

> [!WARNING]
> Put this in your **external** password manager now, before continuing.
> Everything after this point encrypts with it, and step 4 deletes the only
> other copy. Not Vaultwarden — see the warning above; that stack has never
> run, and a password manager inside the lab cannot help you recover the lab.
>
> Then check the transcription, which is all you can check here: read it back
> **out of the password manager** and compare.
>
> ```sh
> read -rsp 'paste it back from the password manager: ' CHECK; echo
> [ "$CHECK" = "$PASSPHRASE" ] && echo "match" || echo "** MISMATCH — fix it now **"
> unset CHECK
> ```
>
> Do **not** run `recovery-drill.sh` at this point. The bucket still holds only
> the old chain — the new job does not exist until step 6 and has never run
> until step 7 — so the new passphrase decrypts nothing and the drill must
> fail. The old passphrase would "pass" and prove nothing about the value you
> just stored. The drill belongs in step 8, and it is there.

## 3. Rotate the settings key

```sh
docker compose down
cp .env .env.bak
sed -i "s|^SETTINGS_ENCRYPTION_KEY=.*|SETTINGS_ENCRYPTION_KEY=$(openssl rand -base64 32)|" .env
awk -F= '/^SETTINGS_ENCRYPTION_KEY=/{print "key length:", length($2)}' .env   # expect 44
```

`openssl` writes straight into `sed`, so the value never reaches stdout or
scrollback. Base64 output cannot contain `|` or `&`, so it is safe with that
delimiter.

## 4. Wipe the old settings store

```sh
docker run --rm -v duplicati_config:/c alpine:3.20 \
  sh -c 'rm -f /c/Duplicati-server.sqlite /c/dbconfig.json /c/*.sqlite'
docker compose up -d
until curl -sf http://localhost:8200 >/dev/null; do sleep 2; done; echo "duplicati up"
```

This drops the job, schedule, stored B2 credentials, and the local block
database. The web UI password is unaffected — it comes from
`WEBSERVICE_PASSWORD` in the environment, not from that database.

## 5. Purge the old chain from B2

Existing objects are encrypted with the **old** passphrase, so they cannot be
extended; and the new job refuses to write into a non-empty prefix.

```sh
python3 - <<'PY'
import json,os,urllib.request,base64
auth=base64.b64encode(f"{os.environ['B2KEYID']}:{os.environ['B2APPKEY']}".encode()).decode()
a=json.load(urllib.request.urlopen(urllib.request.Request(
    "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
    headers={"Authorization":"Basic "+auth})))
tok=a["authorizationToken"]; api=a["apiInfo"]["storageApi"]["apiUrl"]
bid=a["apiInfo"]["storageApi"]["bucketId"]
def post(p,body):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        api+p, data=json.dumps(body).encode(),
        headers={"Authorization":tok,"Content-Type":"application/json"})))
n=0
while True:
    r=post("/b2api/v3/b2_list_file_versions",{"bucketId":bid,"maxFileCount":1000})
    files=r.get("files",[])
    if not files: break
    for f in files:
        post("/b2api/v3/b2_delete_file_version",
             {"fileName":f["fileName"],"fileId":f["fileId"]}); n+=1
    if not r.get("nextFileName"): break
print("deleted", n, "file versions")
PY
```

## 6. Recreate the job

```sh
python3 - > /tmp/job.json <<'PY'
import json,os,urllib.parse
u=urllib.parse.quote(os.environ["B2KEYID"], safe="")
p=urllib.parse.quote(os.environ["B2APPKEY"], safe="")
print(json.dumps({"Backup":{
  "Name":"homelab-offsite",
  "Description":"Nightly encrypted copy of the local backup archives to Backblaze B2",
  "Tags":[],
  "TargetURL":f"b2://homelab-offsite-<suffix>/homelab?auth-username={u}&auth-password={p}",
  "Sources":["/source/"],
  "Settings":[
    {"Name":"encryption-module","Value":"aes","Filter":""},
    {"Name":"compression-module","Value":"zip","Filter":""},
    {"Name":"dblock-size","Value":"50mb","Filter":""},
    {"Name":"passphrase","Value":os.environ["PASSPHRASE"],"Filter":""},
    {"Name":"--retention-policy","Value":"1W:1D,4W:1W,12M:1M","Filter":""},
    {"Name":"--zip-compression-level","Value":"1","Filter":""}],
  "Filters":[]},
  "Schedule":{"Repeat":"1D","Time":"2026-08-22T09:00:00Z","AllowedDays":[]}}))
PY

PW=$(grep '^WEBSERVICE_PASSWORD=' .env | cut -d= -f2-)
TOK=$(curl -s -X POST http://localhost:8200/api/v1/auth/login \
      -H 'Content-Type: application/json' -d "{\"Password\":\"$PW\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["AccessToken"])')

curl -s -X POST http://localhost:8200/api/v1/backups \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  --data-binary @/tmp/job.json; echo
shred -u /tmp/job.json 2>/dev/null || rm -f /tmp/job.json
```

> [!NOTE]
> `--zip-compression-level 1` is deliberate: the source files are already
> `.tar.gz`, so heavy recompression burns CPU for nothing.

## 7. Run it

```sh
curl -s -X POST http://localhost:8200/api/v1/backup/1/run -H "Authorization: Bearer $TOK"; echo
sleep 30
curl -s http://localhost:8200/api/v1/progressstate -H "Authorization: Bearer $TOK" \
  | python3 -c 'import sys,json;print("phase:", json.load(sys.stdin)["Phase"])'
```

Expect `Backup_Complete`.

## 8. Verify

> [!IMPORTANT]
> An untested backup is a belief, not a backup. Do not skip this, and do not
> substitute "the job went green" — a job can report success while restoring
> nothing.

```sh
URL="b2://homelab-offsite-<suffix>/homelab?auth-username=$(python3 -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["B2KEYID"],safe=""))')&auth-password=$(python3 -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["B2APPKEY"],safe=""))')"

docker exec duplicati sh -c 'rm -rf /tmp/rtest && mkdir -p /tmp/rtest'
docker exec duplicati /app/duplicati/duplicati-cli restore "$URL" '*.tar.gz' \
  --passphrase="$PASSPHRASE" --restore-path=/tmp/rtest --no-local-db=true 2>&1 | tail -3

ORIG=$(sha256sum "$(ls -1t ~/.local/state/homelab/backups/*.tar.gz | head -1)" | cut -d' ' -f1)
REST=$(docker exec duplicati sh -c 'sha256sum /tmp/rtest/*.tar.gz' | cut -d' ' -f1)
[ "$ORIG" = "$REST" ] && echo "MATCH — chain verified end to end" || echo "** MISMATCH — stop and investigate **"

# prove the encryption is real rather than assumed
docker exec duplicati sh -c 'rm -rf /tmp/wp && mkdir -p /tmp/wp'
docker exec duplicati /app/duplicati/duplicati-cli restore "$URL" '*.tar.gz' \
  --passphrase='wrong-on-purpose' --restore-path=/tmp/wp --no-local-db=true >/dev/null 2>&1
echo "files recovered with wrong passphrase: $(docker exec duplicati sh -c 'ls -A /tmp/wp | wc -l')"  # expect 0

docker exec duplicati sh -c 'rm -rf /tmp/rtest /tmp/wp'
```

`--no-local-db` matters: it tests the *disaster* path, where the local block
database died with the machine. A restore that only works with the local DB
intact is not the restore you will actually need.

The wrong-passphrase check distinguishes "encrypted with a passphrase only I
hold" from "sitting behind an account login" — the difference that matters if
the B2 key ever leaks.

### Then the drill

Everything above runs `docker exec duplicati` with `$PASSPHRASE` already in
the shell and the container's own stored credentials to hand. That proves the
new chain restores. It does not touch the path you are actually on after
`homelab`'s disk dies, where the only inputs are what you wrote down:

```sh
./scripts/recovery-drill.sh        # from your workstation, not the server
```

This is the first point in the rotation where it can pass: the new chain now
exists (step 6) and has been written to (step 7). Answer every prompt from the
password manager. If you reach for `$PASSPHRASE` in this shell, or for
`.env`, the rotation is not finished — you have stored a passphrase without
establishing that you stored the *right* one.

Record the result in the drill table in `docs/recovery.md`.

## 9. Close the old doors

> [!WARNING]
> Do not run this until step 8 printed `MATCH` and `0`. Until then the old key
> is the only thing still working.

```sh
shred -u .env.bak 2>/dev/null || rm -f .env.bak
unset B2KEYID B2APPKEY PASSPHRASE
```

Then delete the old application key in Backblaze.

## Why this order

Every step keeps a working path until its replacement is proven:

1. The new B2 key is created **before** the old one is revoked.
2. The passphrase is stored **before** anything encrypts with it.
3. `.env.bak` survives until the new key is known good.
4. The old B2 key is deleted **last**, after a verified restore.

The failure this avoids: rotating a credential, discovering the replacement is
wrong, and finding the old one already destroyed. Backups are the one system
where that mistake has no undo.
