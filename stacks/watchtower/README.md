# Watchtower

**What:** Watches for new images and recreates opted-in containers with them.
**Why I care:** A handful of stateless services should just stay current
without ceremony.
**URL:** none — it has no web interface.

## Notes

**A live Gmail app password was hardcoded in the legacy definition.** It sat
in plaintext in `WATCHTOWER_NOTIFICATION_EMAIL_SERVER_PASSWORD`. It was not
copied here and should be revoked at
<https://myaccount.google.com/apppasswords>. Notifications here take a
shoutrrr URL from `.env` if you want them at all — Cup already answers "what
has an update".

**Scoped by label, and the scope is currently empty.**
`WATCHTOWER_LABEL_ENABLE=true` means it touches only containers carrying:

```yaml
    labels:
      com.centurylinklabs.watchtower.enable: "true"
```

Nothing in this repo opts in yet. Add the label deliberately, per service.

**Never label a stack that holds data.** Immich, Paperless, Gitea, Vaultwarden,
Authentik, Home Assistant, ownCloud, and Jellyfin all migrate their on-disk
state at startup and do not migrate back. An unattended 4am upgrade of one of
those is not something a restart fixes — you would need the backup, and you
would find out days later. Reasonable candidates are the stateless ones:
IT-Tools, Dozzle, Whoogle-style services.

**It also breaks a tracker field.** Recreating a container resets its
`RestartCount` and `.Created`, which is why `status.py` keeps
`first_observed_running` in its own state file rather than trusting Docker.

**Pi-hole deserves special mention:** an unattended restart takes DNS down for
the entire house, and the symptom looks like "the internet is broken" rather
than "a container restarted".

**Using the maintained fork.** `containrrr/watchtower`'s last release was
1.7.1 in 2023. This is `ghcr.io/nicholas-fedor/watchtower`, drop-in compatible
and still receiving fixes — which matters for the one container that holds a
read-write Docker socket and can restart everything else.

**The socket is read-write and cannot be proxied.** Pulling and recreating
containers *are* write operations, so unlike Dockge this cannot sit behind a
read-only socket proxy. The label scope is the containment.
