# M6 kill-gate record — portability + OKF + pilot

*Written 2026-08-31 against the pre-registered text, which — per the standing
lesson M4 wrote down and M5 learned twice — is registered TWICE in the
reference (Part C's milestone entry and the verification plan) and was
corrected to SEVEN conditions on 2026-08-30, BEFORE judging: the first time
this project caught the compression in advance rather than in a gate record.*

## §1 · The conditions, verbatim, and their verdicts

| # | Condition (pre-registered text) | Verdict | Evidence |
|---|---|---|---|
| C1 | OKF bundle consumed by a non-Claude agent | **MET** | `docs/m6/c1-consumption.md` — gpt-4o, via item 104's endpoint runner, 4 of 4 expected neighbours with verbatim citations under a bar pre-registered in code before any call fired. gpt-4o-mini's three failures (identity conflation ×2, edge-end inversion ×1) stand beside the pass as the capability-floor finding |
| C2 | goose executes `impact-review` E2E from `AGENTS.md` + CLIs alone | **MET** | `docs/m6/c2-goose.md` — goose v1.48.0 + gpt-4o, green at the AGENT tier on the recurring record; `provider_smoke --status --milestone M6-C2 --require agent` answers OK at exit 0. Five attempts, three harness fixes, all item 33's class |
| C3 | Both fixture workspaces build without touching core, on the SQLite `GraphStore` backend, Neo4j-free | **MET** | Item 53 (`6d51d66` + `f958d47`): pantry and orderflow both load Neo4j-free with ZERO stubs; the store contract suite is parametrised over BOTH backends; core crossed item 19's gate by that gate's own two-commit protocol |
| C4 | A toy 2-repo estate profile builds clean on the SQLite backend | **MET** | Item 53's estate half — kept separate from C3 in the pre-registered text because a fixture and an estate are different claims, and judged on the orderflow two-component workspace loading clean through `core/store/load.py` |
| C5 | The drop-in regression suite passes — fresh-repo install, all 6 tool fan-outs, `--bundle` mode, existing `.sdlc/` upgrade without clobbering user edits | **MET** | Item 67 (`714e770`): tests SPAWN the real installer; the expectation is a FROZEN six-row table after the first cut's derivation proved to be a mirror (6 of 8 mutations survived it); 8 of 8 killed since |
| C6 | `metrics.py --dashboard` renders org/team/developer views | **MET** | Item 57 (`32377ef`): org = workspace, team = component, developer = gate approver — mapped to units that exist rather than inventing a team model; the developer view reads the approval ledger and never `reviews.jsonl`, asserted by AST |
| C7 | Pilot KPIs defined AND maintainer cadence documented | **MET** | Item 58 (`01b9b23`): five person-opened runs reach close-out in eight weeks, against a measured baseline of 9 opened / 1 closed; re-pin on cause only; the one executable part (weekly snapshot publish) opt-in |

## §2 · What the two live conditions taught, because that is what M6 was for

**C1's finding outranks its pass.** A raw model reading the bundle reproduced,
in an agent, the exact defect class the graph's identity machinery exists to
prevent in extractors: asked about `svc:pantry-api@pantry-api` it answered
about `svc:pantry-api@deploy-compose` — twice, reproducibly. The format
carried everything needed on every page; the capability floor is real and now
measured. A consumer below it needs a stronger model or a tool loop.

**C2's finding is that item 33's class is LAYERED.** Three fixes, each one
layer deeper in the same class — a requirement the runner is never told: the
prompt never said *follow the document* (an agent can "execute the workflow it
names" in its own head); the SOP never said step paths live under `<harness>`;
the prompt never said which flag `hitl-target` replaces. **Fix 2 caused
failure 4's shape** — telling the agent to follow the document strictly made
it take the document's literal `<repo>` over the prompt's redirection — which
is what makes this a class to expect at every layer of indirection, not a
list to finish. The reference runner re-ran green, free, after every fix: the
instructions tightened, the mechanism never moved.

**Item 105's architecture was vindicated on its first real machine-specific
need**: the Windows shell hint (goose's cmd.exe versus gpt-4o's POSIX habits)
rides the runner DECLARATION (`goose run --system "..."`), and the harness
prompt stays provider-neutral, naming no shell.

## §3 · What is NOT claimed

- **Repeatability of C2 per attempt**: attempt 3 died at a stumble attempt 2
  had recovered from. The variance is measured and on the smoke's own ledger —
  five rows, four red, one green, none deleted.
- **Review depth**: goose's green session recorded 0 findings on a 20-line
  scratch diff. C2 tests that a second agent can EXECUTE the lifecycle, not
  that its review is good — review quality is items 25–28's subject.
- **Generalisation below gpt-4o**: C1's record carries the mini failures.
- **Production anything**: the deploy evidence in this project remains local
  (M5's record), and item 52's DORA measures stay null on purpose.

## §4 · Suite state at the boundary

CI (gate of record): **4,569 passed at zero skips** on `261d54b`, run
`33328106557`, 2026-08-30 — the tree carrying every M6 artifact and fix.

Milestone-boundary full local Windows suite (the recorded per-milestone duty;
CI is Linux-only): **4,565 passed + 4 skipped = 4,569 collected, 0 failures,
pytest exit 0** — the identical tree CI runs, the 4 skips being local-only
absences CI supplies at zero skips.

*Two process notes belong in this record rather than out of it. The first
boundary run was launched through `| tail -2`, so its exit code was tail's and
its summary line was destroyed — the pipe rule's third recorded violation,
paid for with a full re-run. The re-run then produced no summary line either
(`addopts = "-q"` in pyproject doubles a `-q` into `-qq`), so the count above
is measured off the run's own progress bar — 4,565 `.` + 4 `s`, zero `F`/`E` —
which is the run's per-test output, not arithmetic about it.*

## §5 · The ruling

The gate reads ALL-pass. All seven conditions carry MET verdicts, each on
named, committed evidence. Per this project's constitution the ruling is the
owner's, and every prior milestone was funded by its ruled record.

**Decision taken:** _____________

*(To be filled by the owner. M6 is the final pre-registered milestone; a PASS
closes the milestone strip. Items 106, 107 and 33's codex E2E remain open by
design and gate nothing.)*
