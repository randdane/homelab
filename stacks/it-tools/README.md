# IT-Tools

**What:** A large collection of small dev utilities — base64, JWT decoding,
hashes, cron expressions, UUIDs, colour conversion, JSON formatting.
**Why I care:** These are the things otherwise pasted into a random website.
Anything sensitive — a JWT, a hash, a token — should not go to someone else's
server.
**URL:** http://localhost:8080

## Notes

Stateless. No volumes, nothing to back up, nothing to lose. Delete and
recreate freely.

`:latest` is deliberate — there is no on-disk state to migrate, so an
unattended update cannot break anything that a restart will not fix. This is a
reasonable stack to include in Watchtower's label scope.

Chosen over OmniTools, which does the same job and was the less maintained of
the two.
