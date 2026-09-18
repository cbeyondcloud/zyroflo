# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in **this** repository.

> This repo *is* an SDLC harness. It is not currently run *by* one — `/sdlc` is what it
> installs into other repos. Do not assume a `.sdlc/` directory here.

## What this repo is

**One generic, gated software-development lifecycle**, shipped in three tiers. Scope and
substrate are *capabilities*, not separate products: one repo with zero dependencies and a
multi-repo workspace with a graph substrate run the same lifecycle, differing only in which
capabilities are present and which phases the profile activates.

```
sdlc-init.py          Tier 0 — THE installer. Stdlib only, no network, no API keys.
sdlc_templates/         Canonical templates it installs (SKILL.md, profile.yml, rubric, …)
sdlc/LIFECYCLE.md       The normative 12-phase superset + capability/gate tables
tools/graph/          Tier 2 — the graph substrate (needs uv, Docker, Neo4j)
  core/                 Estate-AGNOSTIC: model, SPIs, lib/, store/ behind GraphStore
  profiles/<estate>/    Estate-SPECIFIC extractors and config  (not yet built)
docs/m0/              M0 evidence artifacts (estate check, de-scope register, protocols)
third_party/          Vendored binaries + checksum manifest (rg, fd pinned; rest at their milestone)
workflows/            Markdown SOPs (convention carried from the predecessor project)
```

Tier 1 (the gated pipeline, `tools/sdlc/`) is **not built yet**.

## Ground rules

1. **`.env` holds every credential.** Never put a secret in any other file, ever — not in a
   test, a fixture, a docstring, or a commit message. `.env` is gitignored and a secret in
   git history outlives the commit that added it. `.env.example` documents keys with
   placeholder values only.
2. **Confirm before any paid API call or credit-consuming action.**
3. **Don't create or overwrite `workflows/*.md` unless asked.** They are the user's
   persistent instructions.
4. **Do not start implementing a plan without explicit approval.** Plan approval is not the
   same as permission to begin; wait for an unambiguous go-ahead.

## Working here

```bash
uv sync --extra dev --extra serve   # pytest is the dev extra, fastmcp the serve extra
                                    # (the MCP suite imports it); a bare `uv sync` omits both
uv run pytest -q             # testpaths = ["tools"] — a narrower root silently skips suites
uv run pytest -q --cov=tools --cov=sdlc-init --cov-report=term-missing
docker compose -f docker-compose.neo4j.yml up -d    # Tier 2 only; needs NEO4J_PASSWORD in .env
```

`uv.lock` is canonical. `tools/requirements.txt` is **generated** — regenerate it via the
command in its header rather than editing it, then **put that header back**: `uv export`
writes its own over it. `tools/tests/test_requirements_matches_the_lock.py` checks both, and
exists because the file had silently drifted to a `tree-sitter` version the lock never
resolved — handing the wrong environment to exactly the people who have no lock to fall back
on.

## Conventions that are load-bearing

- **Zero silent drops.** An extractor that cannot resolve a reference records it in
  `ExtractResult.unresolved`; it never discards it. A graph that looks complete but isn't is
  worse than one that admits its gaps, because a human will act on it.
- **Every edge carries provenance** — `source_file`, `line`, `extractor`, `confidence`, and
  whether the fact was `EXTRACTED`, `INFERRED`, or `AMBIGUOUS`. This is what lets a context
  pack cite a line for every row a human is asked to trust.
- **`core/` names no language, framework, or estate.** Enforced by
  `tools/graph/tests/test_core_genericity.py`, not by discipline. (The Java fact extractor
  is `srcfacts.py`, not `javasrc.py`, for exactly this reason.)
- **Identity is built in one place** — `core/lib/ids.py`. Two spellings of one service
  silently becoming two nodes, or two services becoming one, is the expensive failure class.
- **Determinism is a feature.** The committed snapshot is diffed in PRs and
  `rebuild == snapshot` doubles as an extractor regression test, so build metadata (like
  `run_id`) must never reach the serialized record.
- **Windows is a first-class target**, and this has bitten twice. Reconfigure stdout to
  UTF-8, use `pathlib`, handle `\\?\` long paths and directories with spaces.

## The rule that has caught the most bugs here

**An external binary returning nothing at exit 0 is not evidence of absence.**

It has produced two confidently wrong answers in this project: a multi-pattern `fd` call
reported a workspace as having no GitHub Actions when most of its components had them, and
`fd --full-path` matches a *backslash* path on Windows, so any glob containing `/` matched
nothing. Neither failed loudly.

So: `repo_walk` uses fd for traversal only and decides membership in Python; a non-zero exit
is never read as "no results"; and both backends are asserted equal in tests. Apply the same
suspicion to every vendored tool — treat an empty result as unverified until the pure-Python
fallback confirms it.

## Testing expectations

Every change carries a test that **fails against the previous behavior**. A test that passes
both before and after documents behavior; it does not defend it. When fixing a bug, run the
new test against the unfixed source and confirm it fails before considering the work done.

Prefer proving a claim by running it over reasoning about it — seed the real database, invoke
the real binary, run the real installer.

**A CLI's contract is a process, not a function.** Calling `main()` in-process cannot tell a
refusal from a traceback — pytest renders both as a failing test — so a test that asks what a
*person* sees must spawn it and read the exit code and the output. Item 117 found `policy.py
--validate` handing over a traceback for an unreadable profile, which is the one document
class that module exists to refuse; every in-process test of it passed.

## The rule that took main red once and recurred twice

**A rebase invalidates any commit sha a tracked file names.**

The core two-commit protocol writes a baseline row naming the change commit's own sha;
rebasing that pair gives the change a NEW sha while the row still names the old one. Locally
the dangling object survives in the object store, so `git cat-file -e` passes and every local
test is green; a fresh CI clone has no such object and answers `fatal: bad object`. This took
main red once, and the same class recurred on T323 and T352.

**Convention: name commits by their subject line, not their sha.** A subject survives a
rebase; a sha does not. The only commit shas that belong in a tracked file are ones already
on main (immovable). Non-commit objects (blob digests, vendored checksums) are fine.

**Forward corrections name the corrected commit by subject, never by sha.** A commit body
saying "CORRECTION to `abc1234`" points at nothing once that sha is rebased, and commit
bodies cannot be amended. Write "CORRECTION to 'the commit subject'" instead.

Enforced by `tools/tests/test_no_stale_shas.py`, which walks every tracked file for
sha-shaped tokens and classifies each one against `git rev-list HEAD` (the immovable
ancestor set) and one `git cat-file --batch-check` (everything else this clone's object
store can resolve at all) — two git processes total, not one-to-three per token. A
positive control calls the real scanner against a synthetic repo, so it fails if the
scanner is broken rather than merely if its own duplicate logic is.

**What the guard can and cannot prove.** A CONFIRMED-stale verdict is reliable wherever it
fires, because it only fires when the dangling object is actually present to inspect — the
author's or the integrator's machine, pre-push. A token this clone's object store cannot
resolve at all is carried in the scan result as UNRESOLVED rather than folded into "fine"
— not printed on an ordinary passing run, but visible in pytest's own failure output
whenever the guard does fail on a CONFIRMED-stale sha elsewhere in the same run.
`git clone`/`git fetch` never transmit an object no ref points at, so a rebase-orphaned
commit is unreachable from any fresh clone by construction — CI's own checkout included,
regardless of fetch depth. A green run of this guard on a fresh clone confirms the tree is
unchanged from the last locally-verified state; it does not independently re-verify
staleness for content it was never given the bytes to check. Two narrower refusals close
the gaps that leaves silent: an empty or unreadable tracked-file listing, and a shallow
checkout (`actions/checkout` defaults to depth 1) both raise `ban.py`'s `CouldNotLook`
rather than scanning zero files and calling it clean.

## Self-improvement loop

When something breaks: fix the tool → verify the fix with a test → record what was learned in
the relevant workflow's **Edge Cases / Notes**, or here if it is repo-wide. Constraints
discovered the hard way (the fd behavior above) belong in writing, or they get rediscovered.
