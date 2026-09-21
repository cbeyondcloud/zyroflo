# SDLC-harness — backlog

Pending items **not being done now**, so they are tracked here rather than lost in prose. Frozen 2026-09-19 from the closure-swarm tracker (`agent-swarm/TASK-TRACKER.md`, gitignored) and reconciled against the landed PRs #3–#9 (the entire P2 tier and all reviewed branches are on `main` and are NOT listed here).

## Working doctrine (owner ruling, 2026-09-19)

**This backlog is the single source of truth for work.** From 2026-09-19 onwards, nothing is designed, implemented, or merged unless it is **first referenced from — or added to — this table**. New work: add a row here (id, severity, description) *before* planning it; then design → implement → merge against that row. This keeps every change traceable to an agreed item and stops scope entering through the side door.

Severity: **P3** = real but low-consequence hardening · **P4** = minor/cosmetic · **P5** = trivial · **OWNER** = needs an owner decision. Status: **Deferred** = read once, not scheduled · **Waived** = consciously not doing (reason given) · **Owner decision** = awaiting a ruling.

| Item | Severity | Status | Description |
|---|---|---|---|
| T371 | LAST | Deferred | The config wizard — item 107 shipped the schema contract (policy/profile/workspace + `schema.py --check`); the wizard itself (interface, scope, location) is unscoped. Owner deferred 2026-09-19 (other items first); when scheduled: scope → owner approval → build. |
| T369 | OWNER | Waived — needs paid LLM tooling; outside the correctness bar | Item 114 walk 2: a real P0→P11 lifecycle run end to end (needs goose + an LLM credential). A machine act, gates nothing. Waived 2026-09-19; revisit if an E2E proof is wanted. |
| T370 | OWNER | Waived — needs paid LLM tooling; outside the correctness bar | Item 33 codex end-to-end run (needs codex installed + an LLM credential). A machine act, gates nothing. Waived 2026-09-19; revisit if an E2E proof is wanted. |
| T388 | OWNER | Owner decision | OWNER DECISION - workflows/graph-build.md still teaches the step order T226 just corrected in the skill, and workflows are the owner's own instructions |
| T110 | P3 | Deferred | rule hardened where somebody looked + vacuous parity |
| T128 | P3 | Deferred | could-not-look folds into a one-component workspace at P5/P8/P9 and G1 |
| T246 | P3 | Deferred | Renaming every extractor in a frozen set leaves its disjointness assertion green |
| T273 | P3 | Deferred | The remaining A3c findings, carried so none is lost: A3c-3, A3c-4, A3c-7, A3c-13, A3c-18 |
| T285 | P3 | Deferred | Eight further guards carry hand-written exclusion lists, measured LEAK-FREE today only because they are subtree-confined |
| T289 | P3 | Deferred | The fix for T277 has no regression guard, and the kill that was claimed for one depended on a fixture the pass deleted |
| T293 | P3 | Deferred | Eight assertions of SOURCE-TEXT LITERALS remain across five files, and one of them fails in the SILENT direction |
| T300 | P3 | Deferred | The pre-push procedure that stands between a fixer and a red CI run lives ONLY in a gitignored file |
| T328 | P3 | Deferred | An unguarded loop over a constant, so emptying the constant leaves the test green — and its sibling one file over is guarded |
| T332 | P3 | Deferred | The no-skips rule and three legitimate platform skips contradict each other, and every worker on Windows meets it |
| T333 | P3 | Deferred | The private-coordinate grep has a predictable FALSE POSITIVE class, and every worker pays to open it |
| T338 | P3 | Deferred | The supply-chain preflight row that T305 just made honest FAILS NOTHING |
| T339 | P3 | Deferred | The estate preflight has no CLI-level spawn, so its contract is pinned only in process |
| T360 | P3 | Deferred | worktrees.json `home` is an absolute machine path, ~10 readers |
| T400 | P3 | Deferred | rule the absolute-path-in-a-refusal class ONCE, across the whole tree, instead of one module per row |
| T405 | P3 | Deferred | a plan file that exists on disk but is not referenced by the master is invisible to the whole audit, and nothing says so |
| T411 | P3 | Deferred | the parser library's declared floor admits versions the code cannot run on, and only the lock is hiding it |
| T415 | P3 | Deferred | the remedy the harness tells a person to run does not run as printed, because the command omits its interpreter |
| T417 | P3 | Deferred | thirteen conditional-skip guards can never run, because the import they guard already happened at module scope |
| T419 | P3 | Deferred | two modules raise refusals their callers cannot name, so every catch around them must be wider than it should be |
| T422 | P3 | Deferred | two installer messages state things that are not true, one of them about what it is doing at that moment |
| T423 | P3 | Deferred | the build asks whether the tree is dirty AFTER writing its own output into it, so it can see its own artefact as the dirt |
| T426 | P3 | Deferred | the one command a fresh clone runs to rebuild from the snapshot cannot be made reproducible, because it stamps a fresh identifier into a committed file |
| T427 | P3 | Deferred | a marker whose own comment calls it the failure the report exists to remove is guarded by nothing, and a proposal command may write into the directory it was told only to audit |
| T428 | P3 | Deferred | one unreadable moment permanently deletes the installer's recorded state, and the adopter is then told forever that nothing can be known |
| T430 | P3 | Deferred | the completeness guard asks the planner, and one installer write never reaches the planner, so its destination is invisible |
| T431 | P3 | Deferred | both of T413's guards watch one spelling of one helper, and a hand-rolled reader is invisible to both — one already exists in the tree |
| T438 | P3 | Deferred | the vendored-tool check asks the search path, so a parity claim can be made against a binary nobody vouched for |
| T456 | P3 | Done 2026-09-20 | Zyroflo investor video: 8 standalone 1920x1080 deck slides (a silent title card `s00` + 7 narrated scenes) + `script.js` narration under `assets/video/`, plus the deck→MP4 pipeline (`render_voice.py`, `render_frames.mjs`, `assemble.py`) reconstructed as saved tools from the TokenLean build recipe, and `build_pptx.py` — a native/editable PowerPoint export of the same 8 slides (python-pptx, Segoe UI + Consolas, narration in speaker notes) to `dist/zyroflo-deck.pptx`. Marketing asset — ships no harness code, excluded from `testpaths`, `dist/` gitignored (a 1080p MP4 exceeds GitHub's 100 MB limit; the ~180 KB .pptx is a build output, regenerate with `build_pptx.py`) |
| T117 | P4 | Deferred | weld blind spots, measured-first |
| T129 | P4 | Deferred | defaults that are the strongest value |
| T135 | P4 | Deferred | a guard that checks declarations, not behaviour |
| T158 | P4 | Deferred | item 148 has a Foundation table row and no narrative section |
| T161 | P4 | Deferred | two ways past the timeout sweep, and a rule that pins survival rather than evidence |
| T162 | P4 | Deferred | three notes on the rename work |
| T211 | P4 | Deferred | no production caller |
| T215 | P4 | Deferred | A by-catch mis-attributed to `release.py` actually belongs to `worktree.py` |
| T217 | P4 | Deferred | One comment describes a rule whose consequence FLIPS by site |
| T227 | P4 | Deferred | A count in the runner contract that moved and was not updated |
| T239 | P4 | Deferred | A fixture guard that matches a basename ANYWHERE under the work tree |
| T248 | P4 | Deferred | Three refusal branches are tested only in-process while their docstring claims the real CLI contract |
| T280 | P4 | Deferred | A refusal carries CPython's own error text instead of a sentence a person can act on |
| T296 | P4 | Deferred | The tracker stopped recording WHICH SWEEP found a row, so provenance is no longer answerable by script |
| T311 | P4 | Deferred | Does any other guard use a COUNT as its protection against vacuity? |
| T340 | P4 | Deferred | The deferred graph-surface pass may drop blocked operations with no note |
| T346 | P4 | Deferred | One sentence in the pre-pass module claims for BOTH gates what is true of only one |
| T368 | P4 | Deferred | A second `-q` hides pytest's summary line, and CLAUDE.md teaches exactly that spelling |
| T373 | P4 | Deferred | mcp_server.py mislabels a BOM-prefixed valid build_report as damaged, and two readers of one file have drifted |
| T374 | P4 | Deferred | busybox applet prefix is an unrecognised wrapper: `busybox sh -c 'rm -rf X'` walks past the destruction wall |
| T375 | P4 | Deferred | pack.py renders a non-string run_id or nested workspace name as its repr instead of refusing the shape |
| T377 | P4 | Deferred | render_d2 in pack.py truncates the diagram silently, the same shape T155 fixed one function away |
| T379 | P4 | Deferred | learning history reports an applied row as standing when the file on disk no longer matches its after_sha |
| T380 | P4 | Deferred | learning undo writes the profile before appending its own audit row, the ordering T232 just fixed in apply |
| T383 | P4 | Deferred | seven other INFERRED / AMBIGUOUS producers carry no provenance pin, so flipping any of them to EXTRACTED leaves the suite green |
| T385 | P4 | Deferred | the pre-pass resolves ast-grep.exe but the vendored Windows binary is sg.exe, so every swarm pre-pass on this machine runs one gate and reports the other not-run |
| T386 | P4 | Deferred | a Windows-only newline guard cannot fail on Linux-only CI, so deleting it would land green and silently re-break Windows |
| T387 | P4 | Deferred | the ledger-gate determinism test has the same single-process blindness T327 just fixed in test_debt |
| T390 | P4 | Deferred | field_run.py's door refusal is never spawned, and the audit that said it was counted a `python -c "pass"` as the spawn |
| T391 | P4 | Deferred | stale's `unhashable` remedy names --record, which exits 2 for an emptied recorded directory - T283 one kind over |
| T399 | P4 | Deferred | parse_gitleaks has the same `.get(key, default)`-past-a-present-null shape T397 fixed in parse_astgrep, in a second tool's schema that nobody has measured |
| T404 | P4 | Deferred | a parameter that is never read, and a gate found by matching a sentence when the field is right there |
| T409 | P4 | Deferred | two inert details in test_review_fixes.py, filed together because each is cheap and each teaches a reader something false |
| T410 | P4 | Deferred | a junk value in the accepted field is truthy, so the status report states a finding about it as though it were real |
| T432 | P4 | Deferred | a version check can compare against the wrong constant of two and no test can tell, because both constants are 1 |
| T434 | P4 | Deferred | two more coerce-before-testing sites in the metrics module, feeding a boolean rather than a published tally |
| T100 | P5 | Final verification (session close) | Final verification: tree clean, HEAD pushed, last CI green, tracker has no open rows → leader declares "plan done, session ready to be clo |
| T218 | P5 | Deferred | A comment that is simply false, PRE-EXISTING |
| T358 | P5 | Deferred | docstring overclaims CLI wording reuse |
| T376 | P5 | Deferred | U+2028 / U+2029 could round-trip through the stdlib profile reader only by changing three splitlines sites together, one of which is relied on to eat VT/FF |
| T384 | P5 | Deferred | orderflow EXPECTED.md names S1's confidence but not its provenance, while S3's row names both |
