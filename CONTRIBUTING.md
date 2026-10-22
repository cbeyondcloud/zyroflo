# Contributing to Zyroflo

Thank you for helping. Zyroflo is a governed lifecycle, and the repository holds itself to the
same standard it asks of the repositories it runs in: every change is traceable to a backlog row,
proven by a test that failed first, and gated by local CI before it merges (the maintainers'
two-repository check-in is the one exception, recorded in [`CLAUDE.md`](CLAUDE.md)).

## Before you start

- **Find or add a backlog row.** [`SDLC-harness-backlog.md`](SDLC-harness-backlog.md) is the single
  source of truth for work. Nothing is designed, implemented or merged unless a row names it
  first - open an issue proposing one if you are not a maintainer.
- **Read [`CLAUDE.md`](CLAUDE.md).** It is the contributor doctrine for people and agents alike: the
  conventions that are load-bearing (zero silent drops, provenance on every edge, identity built in
  one place, determinism) and the rules that have caught the most bugs here.

## Set up

```bash
uv sync --extra dev --extra serve --extra sdlc
uv run pytest -q
```

`uv sync` is exact: an extra left off is uninstalled. The suite's root is `tools/` - a narrower
root silently skips suites. The graph tier's tests need Docker and Neo4j
(`docker compose -f docker-compose.neo4j.yml up -d`, with `NEO4J_PASSWORD` in `.env`).

## The rules a change is held to

1. **A test that fails against the previous behaviour.** A test that passes before and after
   documents behaviour; it does not defend it. Run the new test against the unfixed code and see
   it fail before you call the work done.
2. **A command-line tool is tested as a process.** Spawn it and read the exit code and the output;
   calling `main()` in-process cannot tell a refusal from a traceback.
3. **Never skip on a platform.** CI runs with skips forbidden; branch per platform and assert.
4. **Windows is a first-class target.** UTF-8 output, `pathlib`, long paths and paths with spaces.
5. **No secret in any file, ever.** Credentials live in `.env`, which git ignores; `.env.example`
   documents keys with placeholder values only - not in a test, a fixture, a docstring or a
   commit message.
6. **`tools/graph/core/` changes in two commits:** the change, which leaves
   `test_the_core_was_not_touched` red on purpose, then a separate commit appending its row to
   `tools/graph/tests/core_baseline.md`. Never rebase that pair.
7. **Name commits by their subject, never their sha,** in any tracked file: a rebase gives a commit
   a new sha and leaves the old one dangling.
8. **An empty answer from an external tool is not evidence of absence.** Treat it as unverified
   until the pure-Python fallback agrees.

## Run CI before you ask for a merge

CI runs locally, in Linux containers, against a clean clone of your branch:

```bash
python tools/ci_local.py --ref <your-branch>
```

It runs the suite (skips forbidden) and gitleaks. Exit 0 means both passed. Say in your pull
request that it was green, on the head you are asking to merge.

## Pull requests

- One logical chunk of work per pull request, against `main`, naming its backlog row(s).
- Describe what changed, how it was proven (the test that failed first), and anything you decided
  on the way.
- Maintainers merge with a merge commit; history on `main` is never rewritten.

## Security and conduct

Report a vulnerability privately, as [`SECURITY.md`](SECURITY.md) describes - never in a public
issue. Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
