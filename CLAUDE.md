# homelab

- Public repo. Site data (devices, plans, incident reports, Tasker exports, Headscale policy) lives in `../homelab-private`.
- Conventions, the deploy flow (commit, push, pull on `homelab`) and the pre-commit hook: `docs/conventions.md`. New stack: `docs/adding-a-stack.md`.
- Full check: `scripts/check.sh` (CI runs it on push).
- Deploys and outages: the `deploy-checklist` and `incident-response` skills.
