# impact-review — review a change with the dependency picture first

## Purpose

An impact-aware review reads the blast radius BEFORE the code: the reviewer
opens on what the change touches — contracts other teams see, files reached
through config seams, the tests that cover the radius — each claim carrying
`source_file:line` provenance. Findings then split honestly into what the graph
showed and what code-reading found. This is the SOP the M2 dogfood gate
executes against a real merge.

## Inputs

- A unified diff: a PR, a commit (`git show`), or the working tree
  (`git diff`), saved to a file for the CLI or passed as text over MCP.
- The component the diff's paths are relative to (diffs do not say; the
  refusal lists every alias if you guess wrong).

## Access tiers

MCP tools (`sdlc-graph` server) first; the Python CLIs are the fallback that
makes this executable by any runner. CLI prefix:

```
uv run --project <harness> python <harness>/tools/graph/<tool>.py ...
```

**Every `tools/...` path in the steps below lives under `<harness>` — never
under the repository being reviewed — and is run through that prefix.** So
step 1's `tools/review/hitl.py` is:

```
uv run --project <harness> python <harness>/tools/review/hitl.py --target <repo> --begin
```

Do not run a step's path relative to the repository: the file is not there,
and the error that produces has sent a runner improvising. `<harness>` is the
checkout AGENTS.md's own taught command names.

## Steps

1. **Start the clock.** CLI `tools/review/hitl.py --target <repo> --begin`
   (`--component`, `--diff-files` if known; `--run <id>` when the review
   belongs to a gated run, so the findings you reject reach that run's
   feedback record — a review with no run named joins to nothing, which is
   correct for an ad-hoc review and wrong for a gated one). Review time and finding
   acceptance are the harness's #1 adoption risk measured as data — the M4
   gate "median review < 15 min" reads what this records, and it can only be
   read if recording started here, at M2. Rows carry no reviewer identity by
   construction. If a previous session is still open, close it honestly:
   `--end <verdict>` or `--abandon` (an abandoned review is recorded — deleting
   the sessions that went badly would bias the median).
2. **Fresh graph or state why not.** Run the `graph-build` workflow's
   freshness step. Reviewing over a stale graph is allowed only with the
   staleness stated in the findings.
3. **Run the deterministic pre-pass.** CLI `tools/review/prepass.py --target
   <repo> --diff <file> --out <dir>` (add `--rules-dir` for extra rule sets on
   top of the harness defaults and the repo's own `.sdlc/review-rules/`) —
   `gitleaks` (secrets, redacted at the boundary) and `ast-grep` (rule matches)
   land machine findings in `prepass.md`/`prepass.json` before any code is
   read. Exit 0 clean, 1 findings, 3 a gate that could not run — and 3 outranks
   1: findings from the tool that ran do not make the change reviewed while the
   other gate never fired. Carry every pre-pass diagnostic into the findings
   list as machine-found, cited `file:line` + rule id.
4. **Pack the diff.** MCP `context_pack` with `diff_text` (`format` "md" for
   the page, "json" for the complete machine copy), or CLI
   `pack.py --graph-dir <graph-dir> --diff <file> --component <alias>
   --out <dir>` (add `--budget` to size the page, `--no-svg` to skip the
   render). The md trims globally by score and states every elision; the json
   is never trimmed.
5. **Read the Seeds block first.** Per changed file: mode `hunk` (facts at the
   changed lines), `provenance` (the file's facts), or `none` — a `none` file
   contributed no graph evidence, so anything you say about its radius is
   code-reading, not graph. `lines_missed` means the hunks matched no cited
   line and the whole file stood in.
6. **Review in pack order.** Contracts first (interface nodes with
   `declared_at` citations — breaks here are visible to other teams), then
   Direct, Config coupling, Tests to run-or-edit, Indirect. A row flagged
   generated says "regenerate, don't edit" and names `regenerate_from` — a
   finding on such a file is a finding about its source.
7. **Chase depth mid-review.** MCP `impact` (seed file, optional `lines`),
   `impact_diff` (the whole diff's ranked radius without the pack page — the
   quick "how loud is this" answer), `node_neighbors` (per-edge provenance,
   truncation counted), `search_nodes` — or CLI `impact.py --graph-dir
   <graph-dir> --seed <alias>/<relpath>` (`--lines`, `--diff`, `--hops`,
   `--top`, `--json` as needed).
8. **Write findings with provenance.** Every graph-backed claim cites the
   file:line the pack row carries and the route that put it there. Findings
   the graph could not have produced are labeled as code-reading. Name the
   tests the pack lists as run-or-edit, and whether the diff touches them.
9. **Record each finding's fate, then close the session.** As the human rules
   on findings: `hitl.py --target <repo> --finding <id> --source
   prepass|graph|agent|human --disposition accepted|rejected|deferred`
   (`--severity`, `--path`, `--line` when known) — acceptance-by-source is the
   review-precision signal the learning loop reads. Then
   `hitl.py --target <repo> --end approved|rejected|rework`: the one summary
   row (duration, verdict, dispositions) lands in `.sdlc/metrics/reviews.jsonl`.
   `--show` prints the aggregate view — the only view this layer offers.

## Verify

- Every changed file appears in the Seeds block with its mode named.
- No finding cites a file the pack elided without saying the full list is in
  `pack.json`.
- The Docs section's honesty note is preserved in the review: absence of doc
  rows is absence of capability, not absence of affected docs.
- The instrumentation session is closed (`--end` or `--abandon`) — an open
  `review-in-progress` is an unfinished review, and unrecorded time biases
  the median the M4 gate reads.

## Edge Cases / Notes

- A diff spanning components is one pack per component; the union is the
  review's scope, and saying which component each finding sits in is part of
  the finding.
- Diff paths are component-relative exactly as extractors cite them; a diff
  taken from the workspace root needs its prefix stripped.
- Config edits: a changed key seeds only the facts cited at the changed lines
  (hunk mode) — a thirty-key file changed at one key does not drag in thirty
  keys' consumers.
- The estate's config section can be empty from a source-file seed (config
  facts rank through weak cfg-node paths until the semantic layer, item 79);
  an empty section there is a stated limit, not "no config impact".
