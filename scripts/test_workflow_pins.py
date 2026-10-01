"""Every `uses:` in a workflow must name a 40-character commit SHA.

A tag is a mutable pointer. `actions/checkout@v4` means "whatever the owner
last called v4", and an owner -- or anyone who takes the account -- can repoint
it at any commit without the reference in this repo changing. That is how the
trivy-action and kics-github-action compromises worked.

It matters here because check.yml's job holds PUBLIC_DOMAIN, DUCKDNS_HOST and
HEADSCALE_SERVER_URL. A repointed tag runs attacker code with those in the
environment, and the run stays green.

Found by Semgrep on 2026-09-05, on both `uses:` lines the repo had. Pinning
them was a two-line fix; the reason this test exists is that the NEXT workflow
step will be added by hand, the same way these were, and nothing about writing
`uses: foo/bar@v1` looks wrong at the time.

The trailing `# v4` comment is the version record, and Dependabot reads it to
keep offering bumps -- so pinning does not mean going stale, it means the bump
arrives as a reviewable diff instead of silently.
"""
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).parent.parent / ".github/workflows").glob("*.y*ml"))

# `uses:` takes three forms. A local `./path` action is this repo's own
# committed code and needs no pin. The other two are both third-party code
# fetched by a mutable name, and both are policed here:
#
#   owner/repo@ref    pinned by a 40-character commit SHA
#   docker://image    pinned by a sha256 digest
#
# `docker://` was exempt until 2026-09-06, which made this test's own fixture
# assert that `docker://alpine:3` is acceptable. A tag on a registry image is
# mutable in exactly the way `@v4` is -- the publisher can repoint it, and the
# run stays green with the secrets below in scope. There are no `docker://`
# steps in this repo today; the exemption was a hole waiting for the first one.
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)
PINNED = re.compile(r"^[^./][^@]*@[0-9a-f]{40}$")
DOCKER_PINNED = re.compile(r"^docker://[^@]+@sha256:[0-9a-f]{64}$")


def third_party_uses(text):
    return [u for u in USES.findall(text) if not u.startswith("./")]


def is_pinned(use):
    """A digest for a registry image, a commit SHA for anything else."""
    if use.startswith("docker://"):
        return bool(DOCKER_PINNED.match(use))
    return bool(PINNED.match(use))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_shas(workflow):
    unpinned = [u for u in third_party_uses(workflow.read_text())
                if not is_pinned(u)]
    assert not unpinned, (
        f".github/workflows/{workflow.name} uses {unpinned} by tag or branch. "
        f"A tag is mutable and this job has secrets in scope. Resolve it with\n"
        f"    gh api repos/OWNER/REPO/git/ref/tags/TAG --jq .object.sha\n"
        f"and pin the full 40-character SHA, keeping the tag as a trailing "
        f"comment so Dependabot still offers bumps:\n"
        f"    uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n"
        f"If the ref is an ANNOTATED tag the first command returns a tag "
        f"object, not a commit -- dereference it with\n"
        f"    gh api repos/OWNER/REPO/git/tags/SHA --jq .object.sha\n"
        f"A `docker://` step is pinned by digest instead:\n"
        f"    docker://alpine@sha256:<64 hex>   (docker buildx imagetools "
        f"inspect alpine:3 --format '{{{{.Manifest.Digest}}}}')")


def test_there_are_workflows_to_check():
    """A glob that matches nothing makes every parametrized test vacuous."""
    assert WORKFLOWS, "no workflow files found -- the glob is wrong"


def test_the_check_can_actually_fail():
    text = (
        "steps:\n"
        "  - uses: actions/checkout@v4\n"                                    # tag
        "  - uses: astral-sh/setup-uv@main\n"                                # branch
        "  - uses: a/b@11d5960a326750d5838078e36cf38b85af677262 # v4\n"      # ok
        "  - uses: ./.github/actions/local\n"                                # exempt
        "  - uses: docker://alpine:3\n"                                      # tag
        "  - uses: docker://alpine@sha256:" + "a" * 64 + "\n"                # ok
        "  - uses: c/d@11d5960a326750d5838078e36cf38b85af6772\n"             # 38 chars
    )
    assert third_party_uses(text) == [
        "actions/checkout@v4",
        "astral-sh/setup-uv@main",
        "a/b@11d5960a326750d5838078e36cf38b85af677262",
        "docker://alpine:3",
        "docker://alpine@sha256:" + "a" * 64,
        "c/d@11d5960a326750d5838078e36cf38b85af6772",
    ]
    assert [u for u in third_party_uses(text) if not is_pinned(u)] == [
        "actions/checkout@v4",
        "astral-sh/setup-uv@main",
        "docker://alpine:3",
        "c/d@11d5960a326750d5838078e36cf38b85af6772",
    ], "a short SHA is not a pin -- it can be made to collide"
