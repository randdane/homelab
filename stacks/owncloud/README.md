# ownCloud

**What:** File sync and share — desktop and mobile clients keep a folder in
step across devices.
**Why I care:** The documents in it are working copies that exist nowhere else
once they leave the laptop.
**URL:** http://localhost:8095 (admin user from `.env`)

## Notes

**The legacy definition was a single container with no database.** ownCloud
needs MariaDB, and wants Redis for file locking — without the lock cache,
concurrent sync clients corrupt each other's uploads. Both are here now.

**`OWNCLOUD_DOMAIN` and `OWNCLOUD_TRUSTED_DOMAINS` must match how you actually
reach it.** ownCloud rejects any request whose `Host` header is not on the
trusted list, and the failure looks like a blank page rather than an error.
Update both when Caddy goes in front.

**Admin credentials come from `.env` and are only read at first run.**
Changing them afterwards does nothing — the account already exists in the
database. Change the password in the UI instead.

**`files` and `mysql` are both backed up.** ownCloud stores file *contents* on
disk and all metadata — shares, versions, trash, users — in MariaDB. Restoring
files without the database gives you an instance that cannot see them.

**Redis is excluded from backup**: cache and locks only, rebuilt on start.

**MariaDB is pinned to 10.11** and its `--max-allowed-packet` and
`--innodb-log-file-size` flags come from ownCloud's own reference compose;
they are not decorative — large file uploads fail without them.
