## What this changes

<!-- One logical chunk of work. Name its backlog row(s) from SDLC-harness-backlog.md. -->

Backlog row(s):

## How it was proven

<!-- The test that fails against the previous behaviour, and that you watched fail first. A CLI is
tested as a process (exit code and output), not by calling main(). -->

- [ ] A test fails against the previous behaviour and passes now.
- [ ] `python tools/ci_local.py --ref <this-branch>` exits 0 (the suite with skips forbidden, then gitleaks) on the head this PR asks to merge.
- [ ] No secret in any file, test, fixture, docstring or commit message (`.env` only).
- [ ] A change under `tools/graph/core/` lands in its own commit, its baseline row in a second one.

## Anything decided on the way

<!-- A call you made that a reviewer should weigh, and what it costs if wrong. -->
