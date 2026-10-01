# Pi-hole

**What:** DNS server for the house that refuses to resolve ad and tracker
domains, plus local DNS records for the homelab's own names.
**Why I care:** It blocks ads on devices that cannot run an ad blocker — TVs,
phones, consoles.
**URL:** http://localhost:8091/admin

## Notes

**Port 53 is almost always taken.** `systemd-resolved` binds it on most Linux
desktops and on many server installs, including this laptop. The stack
overrides `DNS_PORT` to a high port locally; the committed default is 53 for
the server. On the server, free it first:

```bash
sudo mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNSStubListener=no\n' | sudo tee /etc/systemd/resolved.conf.d/no-stub.conf
sudo systemctl restart systemd-resolved
```

A Pi-hole on a non-standard port is useless for its actual job — clients only
ask port 53.

**`FTLCONF_dns_listeningMode=all` is required, not tuning.** Without it
Pi-hole answers only queries from the container's own subnet and silently
ignores the rest of the LAN.

**This stack is load-bearing while it runs.** Once the router hands out
Pi-hole's address, *every* device depends on it: if the container stops, DNS
for the whole house stops, and the failure looks like "the internet is down"
rather than "a container exited". Two mitigations worth setting up — give the
router a second upstream DNS server, and keep this out of Watchtower's scope
so it is never restarted unattended.

**`data: replaceable`, deliberately.** Blocklists re-download and local
records are a few lines of config. The volume is still backed up because
re-adding your own allow/deny entries from memory is unpleasant.
