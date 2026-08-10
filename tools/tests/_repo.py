"""Facts about the git checkout the tests are running against.

Three tests derive their claim from history: that no banned coordinate survives in any blob
ever committed, that `tools/graph/core/` is byte-identical to a recorded baseline, and that
the fixture's answer key was committed before the extractors that read it.

**A shallow checkout makes all three unanswerable, and two of them answer anyway.** A
`git clone --depth 1` holds one commit that "adds" every file, so a scan of every blob in
every commit silently narrows to a scan of the working tree — which another guard already
covers — and a first-commit comparison compares a commit with itself. Both print green
having checked nothing, which is this repository's cardinal failure shape: an empty result
reading exactly like a clean one.

It arrived through CI. `actions/checkout` defaults to depth 1, so the job written to keep the
guards running was the thing that stopped two of them from checking anything.

One implementation, deliberately. "Is this checkout shallow?" answered separately in each
file that cares is two chances to answer it differently, and the answer decides whether a
guard means anything at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

#: Said once, so a red build in any of the three places names the same cause and the same fix.
SHALLOW_MESSAGE = (
    "this is a SHALLOW checkout (`git clone --depth N`), so a claim about history cannot be "
    "checked here.\n"
    "  CI:      add `fetch-depth: 0` to the actions/checkout step\n"
    "  locally: `git fetch --unshallow`\n"
    "This FAILS rather than passing, because a depth-1 clone holds a single commit that adds "
    "every file: a scan of all history becomes a scan of the working tree, and a "
    "first-commit comparison compares a commit with itself. Both are green. Both checked "
    "nothing."
)


def is_shallow(root: Path) -> bool:
    """True when this checkout's history has been truncated.

    A directory that is not a repository at all answers False rather than raising. That is a
    different failure with its own assertion, and reporting a missing checkout as a shallow
    one would send the reader to the wrong fix.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout.strip() == "true"
