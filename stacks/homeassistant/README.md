# Home Assistant

**What:** Home automation hub — devices, sensors, dashboards, automations.
**Why I care:** Once automations exist, everything else in the house quietly
depends on them still running.
**URL:** http://localhost:8123

## Notes

**Auto-discovery does not work in bridge networking.** Home Assistant finds
most devices (Chromecast, HomeKit, ESPHome, mDNS/SSDP generally) by
broadcasting on the local network, and those broadcasts do not cross Docker's
bridge. Devices you add by IP work fine; discovery does not. On the server,
switch to host networking:

```yaml
    network_mode: host
    # and delete the ports: block -- it is invalid with host networking
```

Left in bridge mode here because the laptop has no devices to find anyway.

**USB devices (Zigbee/Z-Wave dongles) need explicit passthrough**, which is
also not set up here — nothing to pass through on a laptop:

```yaml
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
```

**No healthcheck.** The image does not reliably ship `curl` or `wget` across
base-image changes, and a healthcheck calling a missing binary reports
unhealthy forever — worse than none. The tracker falls back to presence.

**The `config` volume is everything**: your YAML, the secrets file, and
`home-assistant_v2.db` with all recorded history. Rebuilding a mature setup by
hand is days of work, so this one genuinely needs its backups to work.

**Pinned to a dated release, not `stable`.** Home Assistant ships breaking
changes monthly and migrates its database on upgrade. Read the release notes,
then bump deliberately.
