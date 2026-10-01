# HashiCorp Vault

**What:** Secrets management — dynamic credentials, leases, audit logs,
encryption as a service.
**Why I care:** Learning it. Nothing here depends on it, and nothing should.
**URL:** http://localhost:8201

## Notes

**This runs in dev mode, and dev mode is not storage.** `server -dev` keeps
the entire encrypted store **in memory**. Every secret is gone on restart, the
vault auto-unseals itself, and the root token is whatever `.env` says. That is
exactly right for learning the CLI and the API, and completely wrong for
keeping anything.

**The legacy definition mounted a `vault-data` volume** at `/vault/file`,
which dev mode never writes to. An empty volume implying persistence that does
not exist is worse than no volume, so it is removed rather than left as a
false comfort. That is also why `data: none` is accurate here.

**The root token was hardcoded to `myroot`.** Now required from `.env`. Still
a dev token — it is not protecting anything — but not one sitting in a file
that could be pushed to a git remote.

**Port 8201, not the usual 8200** — Duplicati has 8200 in this repo.

**`lifecycle: developing`, `purpose: learning`, deliberately.** The tracker
exists to stop things being forgotten, and "a secrets manager is running" is
precisely the kind of fact that gets misremembered as "my secrets are
managed". If you later want Vault for real, that is a different stack: file or
Raft storage, TLS, a real unseal-key ceremony, and an answer to the question
of what unseals it after a reboot — a service that needs manual intervention
to come back up is a genuine liability on a home server.

**Vaultwarden is the thing that actually holds your passwords.** These two are
not alternatives to each other and do not overlap.
