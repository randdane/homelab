# /// script
# requires-python = ">=3.11"
# ///
"""The `x-homelab` metadata schema: what keys exist and what values are legal.

Its own module because two scripts need the vocabulary and neither should own
it. status.py reads the whole block to render intent; check_updates.py reads
one field to decide what to alert on. Before this, check_updates imported the
status CLI for a single enum -- a check script depending on a reporting tool
so that both would agree what "internet" means.

Only the schema lives here -- what the keys are and which values are legal.
Policy does not: ALERTING, which decides that `internet` is the priority worth
waking someone for, belongs to check_updates.py and has no other consumer.
Nor does behaviour: parse_intent() stays in status.py, since it demands every
required key and reports on ports, which is status's business and not a
question check_updates has an opinion about.

Every key in REQUIRED that has a fixed vocabulary must appear in ENUMS.
parse_intent validates a field only if ENUMS names it, so omitting one here
turns its validation off without any error anywhere.
"""

REQUIRED = ("lifecycle", "purpose", "host", "exposure", "data", "prerequisites")

ENUMS = {
    "lifecycle": ("planned", "developing", "production", "retired", "not-needed"),
    # "always-on" is homelab, the 24/7 host (VM 101 on pve), and is distinct from
    # "server" (the spare server). Nine stacks already used it while this enum still
    # rejected it, so every one of them carried a validation warning that
    # everybody had learned to scroll past.
    "host": ("laptop", "server", "always-on", "both"),
    # What can REACH the stack. Not how sensitive it is, and not how urgent
    # its updates are -- see patch_priority.
    "exposure": ("internal", "lan", "internet"),
    # Optional, defaulting to exposure. What being out of date COSTS.
    "patch_priority": ("internal", "lan", "internet"),
    # Whether losing this stack's volumes matters, which is what the
    # backup-coverage check reads. Dropped from this dict when the schema was
    # extracted here on 2026-09-06 and unvalidated until 2026-09-07: because
    # parse_intent only checks keys that appear in ENUMS, a missing key
    # disables validation for that field silently rather than raising.
    "data": ("none", "replaceable", "precious"),
}
