# site.example

The layout of a site-data checkout: the files that describe one particular
site rather than how this repo works. Copy this directory to a private repo
of your own, fill it in, clone it on the server, and point `SITE_DIR` at it
(`stacks/headscale/.env`; exported in your shell for Ansible).

| Path | Read by |
|---|---|
| `stacks/headscale/site/policy.hujson` | Headscale, mounted at `/etc/headscale-site` |
| `stacks/headscale/site/extra-records.json` | Headscale, re-read on change |
| `stacks/ytdl-sub/site/subscriptions.yaml` | ytdl-sub, mounted at `/ytdl-site` |
| `pve/firewall/101.fw`, `102.fw` | copied by hand to `/etc/pve/firewall/` on Proxmox; the Jellyfin isolation depends on them |
| `ansible/inventory.yml` | `ansible-playbook -i "$SITE_DIR/ansible/inventory.yml"` |

A missing `SITE_DIR` directory stops Headscale from starting, on purpose:
see `stacks/headscale/compose.yaml`. Keep secrets out of it; they belong in
the untracked `.env` files.
