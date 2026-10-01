# OliveTin

**What:** Turns a whitelist of shell commands into buttons on a web page.
**Why I care:** Some chores want a button on a phone, not an SSH session —
and a fixed list of commands is much safer than a shell.
**URL:** http://localhost:1337

## Notes

**`config/config.yaml` is the application.** OliveTin ships no useful default:
with no actions defined it starts, reports healthy, and shows an empty page.
The three actions in there now (ping, `df -h`, `uptime`) are deliberately
trivial — enough to prove it works.

**The point is that it is not a shell.** Only listed commands can run. That
guarantee holds exactly as long as arguments are constrained: the `type:`
field on each argument is the safety boundary, and `ascii` rejects the shell
metacharacters that would turn an argument into command injection. An action
that interpolates unconstrained free text is a remote shell with a nicer UI.

**No volumes, no state.** Its entire configuration lives in git, which is
also why there is nothing here to back up.

**It has no authentication configured.** Anyone who can reach port 1337 can
press the buttons. That is acceptable for `uptime`; it is not acceptable for
an action that restarts a stack. Before adding anything consequential, put it
behind Authentik — or at minimum keep it `exposure: lan`.

**Actions that manage containers need the Docker socket mounted**, which is
deliberately absent. Add it only when you add such an action, and never with a
free-text container name as an argument.
