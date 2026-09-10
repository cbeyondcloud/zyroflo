# M7 record — completeness, gateless by design

*Written 2026-09-10 in the form M5's and M6's records use, with one difference
stated at the top: the other six milestones quote a pre-registered gate before
judging it, and this one has no gate to quote. Why that is a ruling rather than
an omission is §2. What it costs is §5, which is the section this document
exists for.*

## §1 · The opening ruling and the closing ruling

| | |
|---|---|
| **Opened** | 2026-08-31, by the owner, on the ruling that the config wizard (item 107) comes LAST — after the harness's own functionality is finished. The opening text says, in the owner's own words, **"no pre-registered kill gate"** |
| **Scope** | the MEASURED remainder rather than a wish list: items 108–117 plus 107, which then grew ten times — nine as the lifecycle was RUN, once as the plan set was READ |
| **Ruled** | 2026-09-09 — **GATELESS BY DESIGN, closing on a written record** — by the leader under the owner's delegation of the same day |
| **Registered** | in the reference on 2026-09-09, in Part C and in the verification plan, **marked post hoc and saying so**. M0–M6 were each registered twice before their evidence existed; this entry was written after it |
| **State on the day this was written** | **OPEN — 63 of 64.** M7 was **REOPENED A TENTH TIME on 2026-09-10 by item 170**, filed that day and NOT built. The plan set's headline reads **170 items, 168 done** |
| **Closes on** | a written record of this form. Not today: this one is written while the milestone is open, and §5 and §6 say what is still owed |

The two rulings are kept apart at §8 — the delegated one, and the one still
unfilled because it is the owner's. **The owner can overrule by
appending**, which is the same rule M5's record set: an amendment is appended,
never edited into a section that already stood.

## §2 · Why no kill gate applies

Three measurements, taken before the ruling:

1. **The reference registers M0 through M6 twice each, BEFORE their evidence
   existed. M7 is pre-registered nowhere.** It is not unregistered: it has the
   same two entries every other milestone has — the Part C row and the
   verification plan — but both were written on 2026-09-09, after the evidence,
   and both say so. Every other milestone's gate exists in two places that have
   to be reconciled — the lesson M4 wrote down and M5 learned twice.
2. **The doctrine says every milestone ends at a kill gate WITH AN OFF-RAMP.**
   The off-ramp is the point: a gate that cannot fail is not a gate, and what
   failure BUYS is a re-scope.
3. **A completeness milestone opened after M0–M6 shipped has nothing to
   re-scope to.** There is no reduced product on the other side of a failed M7.
   So an off-ramp cannot be written, and a gate without one is a ceremony.

A condition registered *now* would be post hoc, which contradicts the
doctrine's own justification — that a condition is written before the evidence
exists. And the obvious candidate condition, *"a re-audit finds nothing new"*,
is **already falsified ten times** (§4) and would predictably fail again.

**"107 closes it" is recorded as a scope ORDERING, not a gate.** It was met on
2026-09-02, and **every one of the ten reopenings in §4 came afterwards** — the
first three the next day. A closing condition that is met and then followed by
ten reopenings was never a gate; reading it as one would have closed this
milestone on the day its most productive week began.

## §3 · The evidence behind the sixty-three

**M7 stands at 63 of 64 and is OPEN, as of 2026-09-10.** Sixty-three rows are
done; the sixty-fourth is **item 170**, filed 2026-09-10 and not built, which
reopened the milestone for the tenth time (§4). The denominator moved ten
times, and it moved in the same pass as the work each time. What follows is the
evidence behind the sixty-three, which is what this section can honestly
claim — not a close.

**The closure audit of 2026-09-09/10.** The tree was partitioned by module
group and swept for twelve recurring defect classes. Nineteen items — **151
through 169** — were found, built with red-first proof and a mutation pass in
isolated worktrees, reviewed by a second pair of eyes, and landed in **six
integration batches**:

| Batch | Items | Landed | CI |
|---|---|---|---|
| B1 | 151–154 | `858fe99` | `34337753689` green |
| B2 | 155–157 | `25de6b5` → repaired at `78cdc06` | first push RED (`34363710257`), then `34366945284` green |
| B3 | 158–160 | `856b766` | `34368629444` green |
| B4 | 161–163 | `18164f0` → repaired at `0a7ec24` | first push RED (`34370707886`), then `34372756306` green |
| B5 | 164–166 | `8370969` | `34394584380` green |
| B6 | 167–169 | `09f897d` | `34396545382` green, suite 13m10s |

**The two reds are in this table on purpose.** M5's record kept a false green
and its correction because a green that had never been wrong is weaker
evidence; the same rule applies here.

- **B2's red** was `test_no_shipped_module_imports_something_it_never_uses`: a
  fix replaced the last `subprocess.run` call in two modules and left
  `import subprocess` behind. The guard that caught it is the one this project
  built for that class, and it is a **repo-wide sweep**, so it runs only in a
  full-suite run — which means no targeted `-k` run in the fixer's, the
  reviewer's or the integrator's hands could have seen it. The instruction to
  size files and use `-k` is what hid it. A mandatory whole-file pre-push sweep
  set was added (six files, measured at ~886 passed in ~21s against an ~11m CI
  job) and **caught the same class again hours later, before a push**.
- **B4's red** was `test_the_core_was_not_touched` answering `fatal: bad
  object`. The core two-commit protocol writes a baseline row naming the change
  commit's own sha; a rebase gave the change a new sha while the row kept the
  old one, which survives locally as a dangling object and was never pushed —
  **every local run passed and a fresh clone could not resolve it**. Re-stamped
  rather than reverted. The rule added: prefer a plain fast-forward for any
  branch carrying a self-referential sha, and after any rebase ask the PUSHED
  history about every sha a tracked file names.

**Suite state at the boundary.** CI is the gate of record and is Linux-only, so
the recorded per-milestone duty is a full local Windows run:

| | |
|---|---|
| CI on `09f897d` | run `34396545382` SUCCESS, **5,438 collected**, suite 13m10s, secret scan 12s |
| Full local Windows suite | **pytest exit 0 — 5,421 passed + 17 skipped in 1h13m02s** |
| Reconciliation | 5,421 + 17 = **5,438**, the identical total CI collected; the 17 skips are local-only absences CI supplies |

**The plan set agrees with itself at the level its checker can see**: item
148's checker reports `0 disagreement(s) over 340 row reading(s)`, exit 0. What
that checker cannot see is §6's last bullet and the reason item 170 exists.

## §4 · The ten reopenings, and the finding that outranks them

| # | When | Reopened by | Closed by |
|---|---|---|---|
| 1 | 2026-09-03 | walk 2 of the lifecycle | items 117–128's span |
| 2 | 2026-09-03 | walk 3 | same span |
| 3 | 2026-09-03 | the max-effort review of items 117–128 — **the one that came from reading commits rather than running** | same span |
| 4 | 2026-09-04 | walk 4, which found that P2's SOP told a runner to seed from *"a file the requirements name"* while P2's prompt named no document at all | item 132 |
| 5 | 2026-09-04 | the same walk that had just closed it: `--check-snapshot` writing 837 KB of scratch into the committed graph directory it audits | item 133 |
| 6 | 2026-09-04 | the first attempt ever made to ANSWER a rejected gate, hours after closure | item 138 |
| 7 | 2026-09-05 | the first RUN of the reject→re-scope loop, which destroyed the document it was re-scoping | item 139 |
| 8 | 2026-09-05 | the first run in which that loop actually WORKED: a correct 218-line document, and a false positive in the claims checker then blocking the human gate | item 140 |
| 9 | 2026-09-06 | the first walk driven under an explicitly named model: a runner's declared identity checked against nothing | item 149 |
| 10 | 2026-09-10 | **reading the plan set's own prose** — a sweep for filings the same documents had already closed | **item 170, open** |

**The tenth reopening breaks the rule the first nine established, and the break
is the more useful finding.** Nine reopenings came from RUNNING the lifecycle —
walking a phase, driving a runner, opening a real defect — and that was
recorded here as a standing finding: reading the plan set had never once
reopened it. Item 170 came from reading. It was found by sweeping the plan
set's own prose for filings the same documents had already closed, and it
turned up nine of them across sixteen locations. So the rule is not that
reading finds nothing. It is that **running finds defects in the CODE and
reading finds defects in the RECORD**, and this project had only ever pointed
one of those instruments at itself.

## §5 · The open remainder

**This section is the reason this document exists.** The working tracker for
the closure pass lives in `agent-swarm/`, which is **gitignored**, and the plan
set lives outside this repository entirely. A defect recorded only in those two
places is invisible to anyone who clones this repository. **What follows is the
record.**

**The census, re-derived from the tracker by parsing rather than quoted**, on
2026-09-10: 91 task rows (91 distinct ids) and 26 audit-partition rows. The
tracker's open vocabulary is exactly `TODO`, and **58 rows carry it** — 48 task
rows and 10 audit partitions.

**Two of the 58 are this document's own bookkeeping**: the row that commissioned
this record, and the row that checks it landed. Excluding those two, the
**durable remainder is 56** — **46 defect and work rows in this repository** and
**10 unswept audit partitions**. By priority, excluding the same two:
**P1 0 · P2 4 · P3 26 · P4 15 · P5 1.** P4 is 15 and not 16 because one row was
closed as a duplicate of the row that commissioned this record.

An earlier pass over the same tracker read 61 open and 58 durable. **56 is the
authoritative number**, because two rows the earlier pass counted as defects
have since closed and one was withdrawn as a duplicate; that pass's figures are
recorded here so the two readings can be told apart rather than averaged.

Every row below is **open, filed and unfixed**. Priority is by internal
dependency, not by severity alone.

### P2 — four

| Row | The defect, plainly | Lives in |
|---|---|---|
| T143 | `--plan` says a runner will start and the spawn then cannot, because the plan check resolves a command the way `shutil.which` does (honouring PATHEXT) while the spawn appends only `.exe` — and a `.cmd` runner is launched by the shell, which truncates the prompt at the first newline and expands machine paths into it | `tools/sdlc/drive.py` |
| T147 | The same short-secret hole at three more sinks: they scrub with the length-floored value set, so a secret-named value under eight characters prints intact into records the installer's own header says are meant to be committed | `tools/provider_smoke.py`, `tools/sdlc/policy.py` |
| T157 | The CI fallback speaks a vocabulary its reader does not know — the bucket table holds only the CLI's words while the REST rows fill buckets from `.conclusion`, so a genuine RED maps to `unknown` and neither the log fetch nor the defect route ever fires | `tools/sdlc/release.py` |
| T160 | Four timed subprocesses with no handler at all, two of them inside the module that adopted the bounded-run helper — including **the push wall itself**, where a hung `git push` raises out of the function that decides whether publishing is allowed | `tools/sdlc/release.py`, `tools/impact_ab.py` |

### P3 — twenty-six

| Row | The defect, plainly | Lives in |
|---|---|---|
| T103 | A UI-URL metric denominates on every unresolved row of an extractor whose only kinds mean *the file was never read*, so one could-not-read row reads as a 50% miss | `tools/graph/validate.py` |
| T104 | The git helper folds absent, timed-out and non-zero into `""`, so an unprobeable checkout reports clean and empty — identical to a healthy one | `tools/graph/workspace_report.py` |
| T105 | A present-but-null confidence renders as a word the consumer's citation matcher cannot match, so the edge loses its provenance silently | `tools/graph/export_okf.py` |
| T106 | The genericity guard applies only the technology terms to string literals and never the language terms — 11 of 17 — and core violates one today | `tools/graph/tests/test_core_genericity.py`, `tools/graph/core/lib/repo_walk.py` |
| T107 | `read_text` folds `OSError` into `""` at 32 read sites so an unreadable file and an empty file are one value, while a second read helper in the same core has the OPPOSITE failure semantics — one file either reads as empty or aborts the whole build | `tools/graph/core/lib/repo_walk.py`, `tools/graph/core/lib/props.py` |
| T110 | The path-frame fix applied to the fd backend is absent from the ripgrep backend, and every parity assertion compares basenames — so the property parity exists FOR is asserted nowhere | `tools/graph/core/lib/repo_walk.py`, `tools/graph/tests/test_core_lib.py` |
| T111 | A schema version is written into every shard header and nothing reads it; unknown record kinds are dropped silently; the version is declared independently in two modules | `tools/graph/core/lib/emit.py`, `tools/graph/core/lib/manifest.py`, `tools/graph/core/model.py` |
| T113 | The own-worktree de-duplication matches tool rows by a name no row carries, so pre-existing findings inside the harness's own trees are emptied and their source deleted while the row still claims a count, and the summary totals disagree | `tools/review/prepass.py` |
| T115 | The absolute target path may be serialised into the scope of a document written to the run folder, which is committed at merge — confirm on a fixture run, then record a name or a relative marker | `tools/review/prepass.py`, `tools/review/sca.py` |
| T116 | The metrics record writes a resolved absolute output directory — a path carrying the operator's username — into a file the installer says is meant to be committed, past a docstring claiming every recorded string is scrubbed | `tools/provider_smoke.py` |
| T120 | Three reproduced defects in the Helm extractor: a line lookup needing an exact `key: value` substring gives line 0; a templated `data:` block emits nothing with no unresolved row; a per-FILE identity makes two config maps in one multi-document file collide | `tools/graph/extractors/helm/__init__.py` |
| T121 | A compose service with an empty body is skipped with no unresolved row, and a `services:` key written as a LIST is valid YAML so the unparsed row never fires and nothing is emitted — invalid reported as absent | `tools/graph/extractors/compose/__init__.py` |
| T122 | Two files with different inline defaults for one environment key leave the value from the first and the provenance from the second, with no superseded-declaration row, while two sibling extractors guard the identical site | `tools/graph/extractors/env_keys/__init__.py` |
| T126 | The release push wall: a refused push is never written to the guardrail ledger; the component's VCS block never reaches the check in any multi-component workspace; and the unpublished-commit question is asked of the wrong tree, so in a multi-repo estate the wall vanishes silently | `tools/sdlc/release.py` |
| T127 | Absolute `where` paths are recorded into four documents under the committed run folder | `tools/sdlc/deploy.py`, `tools/sdlc/release.py`, `tools/sdlc/verify.py`, `tools/sdlc/worktree.py` |
| T128 | An unparsable workspace profile is synthesised into a single component named after the target, so a could-not-look becomes a confident one-component answer at four phases and one gate | `tools/sdlc/policy.py`, `tools/sdlc/worktree.py` |
| T130 | A profile with one missing bracket in its `phases:` list is read as declaring no phases: the listing exits 0, activates the full default with its gates, and prints that a phase the file lists is skipped — the gates a run has, decided by a document the harness misread | `tools/sdlc/policy.py`, `tools/sdlc/stage.py` |
| T132 | Fourteen further shell spellings walk past the destruction wall — brace grouping, prefix flags whose VALUE becomes the program, four unlisted prefix programs, `eval`, an interpreter named by variable, and a backslash line continuation | `tools/sdlc/policy.py` |
| T134 | A profile value a phase names and the profile does not declare renders as a bare blank in the prompt at exit 0, where documents in the same position were given four distinguishing sentences | `tools/sdlc/stage.py`, `sdlc_templates/` |
| T138 | The live-entry reader returns the last entry per gate REGARDLESS of verdict, so a REJECTION's pins are checked and the tool reports bytes changed since an approval that never happened | `tools/sdlc/approve.py`, `tools/sdlc/validate.py` |
| T139 | The reader that shows the ledger back dies on what it should show: a readable entry missing a key is a traceback, and a malformed run state raises where the sibling validator returns a sentence | `tools/sdlc/status.py` |
| T140 | The claims checker guards only a full stop as a token's left neighbour, so a date, a line range and an item range each read back as a false count on the same gate-blocking path; and `--check --json` returns 0 with disagreements present | `tools/sdlc/claims.py` |
| T153 | The installer's own file at the adopter's root activates an extractor and emits a bare-header shard, so the extractor SET changes depending on whether the install is committed; and the dirtiness check runs AFTER the build wrote its own output into the tree it then measures | `tools/graph/build.py` |
| T155 | A fully-elided section prints "(none reached)" and then "… 2 more row(s) elided", so a trimmed list reads like a complete one — the loud-elision rule inverted | `tools/graph/pack.py` |
| T163 | Sixteen refusal branches nobody spawns — twelve in one module, four in the other; exactly one test spawns the first and ZERO spawn the second. All behave correctly today, so this is a coverage gap, not a live defect — and it is the gap that cost item 117 a traceback from the one document class its module exists to refuse | `tools/graph/build.py`, `tools/graph/mcp_server.py` |
| T164 | The full Windows suite takes 6.5× CI's Linux run and ONE history-walking test is ~85% of it — roughly 118,000 child processes, one `git show` per blob per commit. A suite nobody can run locally stops being run locally, which is precisely how a Windows-only defect reaches CI | `tools/tests/test_history_is_clean.py` |

### P4 — fifteen

| Row | The defect, plainly | Lives in |
|---|---|---|
| T117 | The restatement weld reads top-level assignments only, so a threshold restated INSIDE a function is invisible to it; and two runner-command validators disagree on whether a project flag must be quoted | `tools/sdlc/http_runner.py`, `tools/provider_smoke.py`, `tools/sdlc/reference_runner.py` |
| T119 | An uncompilable query returns `[]` and promises the loss surfaces in the validation report; zero callers read that | `tools/graph/core/lib/srcfacts.py` |
| T123 | A document that PARSES to the wrong shape is a traceback rather than a refusal, at two doors of one CLI; and a present-but-non-numeric line raises without naming the row | `tools/graph/validate.py`, `tools/graph/core/store/load.py` |
| T124 | Six of 35 pinned asset entries carry no installed checksum, so the verify RECORDS the value on first run instead of comparing — trust-on-first-use inside a check, said nowhere in its output | `tools/third_party.py` |
| T129 | `confidence` and `canonical` are deliberately unpromised fields and their defaults are the STRONGEST values a reader could pick — `1.0` and true — so an edge that never carried a confidence reads as fully trusted. Measure what each reader defaults to and what a missing value should mean | `tools/graph/core/` |
| T135 | A guard checks that unresolved kinds are DECLARED, not that a site still emits them, so a deleted emit site stayed green under mutation | `tools/graph/tests/test_unresolved_emission_sites.py` |
| T136 | The plan checker compares ROWS, so a narrative section whose status line disagrees with its own row is invisible — the checker's own class one level below where it looks | `tools/plan_audit.py` |
| T141 | The component's own module id is still built from the DECLARED group while its dependencies now resolve through the effective one, so an aggregator inheriting its group gets an id with an empty group — the identity class, deliberately left until the moving ids are measured | `tools/graph/extractors/maven/__init__.py` |
| T146 | Three installer refusal branches nobody spawns, and an explicitly empty tool list installs at exit 0 because an unconditional insert repairs the list before the validity check | `sdlc-init.py` |
| T150 | A docstring teaches naming a regression test by PATH, while the hunk reader deliberately drops every file header, so a test named that way can never be grounded in the patch | `tools/sdlc/defect.py` |
| T152 | A run id that is a path traversal round-trips out of a branch name, and a run id is used to BUILD a folder path | `tools/sdlc/policy.py` |
| T158 | **Item 148** — the same item §3 cites as its plan-set evidence — has a table row and no narrative section, so the plan set's own idiom is broken exactly where its checker cannot see it: T136's class, in the bookkeeping of the item built to end it. Write the section, or record why that item has none | the plan set, which lives outside this repository |
| T159 | A loose acceptance that only the console can see: the verify accepts either half of a rename and says which, but the durable record stores only problems, so a run where everything matched exactly is indistinguishable from one where a rename was accepted loosely | `tools/provider_smoke.py` |
| T161 | Two ways past the timeout sweep, neither reachable today: a `subprocess.run(timeout=)` inside a nested `def` within a guarded `try`, which the sweep does not walk into, and `import subprocess as sp` — an ALIASED module import, the same trap that hid the dead import behind B2's red. And where a timeout is caught by hand, `release.py:192` drops `exc.stdout` while the module's headline claims both: the rule pins that the process SURVIVED, not that it kept what it printed. Resolve aliased imports, walk nested bodies, and either widen the rule to evidence or narrow the headline to survival | `tools/tests/test_bounded_subprocess.py`, `tools/sdlc/release.py:192` |
| T162 | A rename is counted as two files in a metric where the honest count is one; a second diff walk agrees with the first on every real input today with nothing structurally forcing it; and a docstring names the wrong blind spot — spaces parse fine, non-ASCII does not | `tools/provider_smoke.py`, `tools/graph/impact.py` |

### P5 — one

| Row | What it is |
|---|---|
| T100 | Final verification of the closure pass — tree clean, HEAD pushed, last CI green, no open rows — which by construction cannot be satisfied while §5 has entries |

### The ten unswept audit partitions, by file

Each of these names files that **were never read** by the closure audit. They
are not clean bills; they are unexamined ground. Every one carries the same
exit criterion: swept for the twelve recurring classes, each finding filed with
`file:line` and a reproduction, or recorded CLEAN by file.

| Partition | Never read |
|---|---|
| A1b | `tools/graph/build.py` and `tools/graph/mcp_server.py` — the two this row never reached (its siblings covered their flags, incremental path, report and tool shield) |
| A1d | `build()` lines 880–1099, `build_from_snapshot()`, `_report()`/`_print_report()` and the `discover*` functions in `tools/graph/build.py`; a read-only graph directory; an extractor that RAISES; the MCP refusal wording compared against the CLIs; the vacuous-test sweep over both modules' tests |
| A2b-2 | the four link packages under `tools/graph/extractors/`, plus the golden-chain and snapshot tests — including the unanswered question of whether any golden is regenerated from the build it checks |
| A2b-3b | extractors `gradle`, `npm`, `openapi`, `http_calls`, `http_routes`, `frontend`, `tests`, all of `spring`, `_java`, `_bindings`, `_canonical`, `_lang` — **and the extractor TESTS**, where the golden-derived-from-the-code class lives |
| A3c | `tools/field_run.py` from line 305, `tools/review/loop.py` from line 200, `tools/review/hitl.py`, `tools/corpus_harvest.py`, `tools/corpus_replay.py`, `tools/impact_ab.py`, `tools/plan_audit.py`, `tools/tests/ban.py` and their tests — three of the twelve classes got no pass at all here |
| A4c | `policy.parse`/`validate` (YAML-1.1 booleans, duplicate keys) and the highest-value probe named and not taken: whether a dial inside a `components:` entry becomes the RUN's dial; plus all of `stage.verify`/`stale`/`record_command`/`capable`/`active` and their unspawned `main()` branches |
| A5b | `tools/sdlc/feedback.py` (bar the one line already fixed) and `tools/sdlc/schema.py` and their tests |
| A7c | `tools/sdlc/learning.py` (481 lines) and `tools/sdlc/debt.py` (267) and `tools/sdlc/connect.py` (272) **entirely**, `tools/sdlc/metrics.py`'s internals, the `main()`-refusal spawn survey across all eight modules, and the vacuous-test sweep over their test files |
| A8b | `--bundle`, `--dump-templates`, `--dry-run`, workspace installs, the emitter-spawn drift path, the vendored-asset negative cases (empty asset list, mismatched checksum, absent binary), and the unwritable-target and newer-install refusals in `sdlc-init.py` |
| A9b | the runner contract's first two sections against `drive.spawn` and `reference_runner`; the lifecycle table rows against the policy presets; the graph skill's taught commands; and P6's absent `reads:` block |

## §6 · What is NOT claimed

- **NOT that M7 is closed.** It is open at 63 of 64, reopened on 2026-09-10 by
  item 170, and this record is written in that state rather than against it.
- **NOT that no further defect exists.** The reopening history says otherwise
  ten times. The next walk — or the next reading — may reopen this milestone
  again, and **that is the milestone's nature, not a gate failing.**
- **NOT that the audit is finished.** Ten partitions above were never swept.
  Sixteen of the twenty-six partitions were, and they returned nineteen items;
  there is no basis for believing the remaining ten hold none.
- **The four P2 defects are open and named**, and one of them is the push wall
  itself. Twenty-six P3 rows and fifteen P4 rows are filed and unfixed.
- **NOT one continuous run.** No change has gone P0 → P11 in one piece; every
  phase has been exercised, not consecutively by one change. M5's record said
  this first and it is still true.
- **Item 33's codex E2E is still owed.** It is a machine act rather than work —
  the tool is not installed here and no model credential is present — and it
  gates nothing.
- **Walk 2 is still owed**, and it needs a second-agent runner. One plan
  document said so while the master's summary read as though nothing but item
  33 remained. That contradiction was real; it was amended in the plan set on
  2026-09-10, and it is recorded here rather than papered over. Walk 2 is owed
  in PROSE and carried by no row, which is why nothing counting rows could see
  it.
- **The plan set carried nine stale filings across SIXTEEN locations** — rows
  describing a defect that a later row had already fixed. They were amended on
  2026-09-10, append-only, and the amendment landed *outside* this repository.
  (Fifteen was the first count; a second worker measured sixteen, because one
  cited location does not exist and one section is two paragraphs. Sixteen is
  the measured number and the one item 170's justification carries.)
- **The prose-level staleness check was FILED, not built, and filing it
  REOPENED THIS MILESTONE.** It is **item 170**, filed 2026-09-10, the tenth
  reopening in §4 and the sixty-fourth row of M7. The reason it exists is exact:
  item 148's checker compares ROWS, every one of the nine stale filings lives in
  PROSE, and the checker therefore reported 0 disagreements over 340 row
  readings against every one of them — a guard hardened where somebody was
  looking, in the bookkeeping of the item built to end that class.
- **NOT that CI green means Windows green.** CI is Linux-only. The boundary
  Windows run is the per-milestone duty and it passed at exit 0, but T164
  records that it takes 1h13m and that one test is most of it — a check nobody
  can afford to run is a check that stops being run.
- **NOT that this record is the tracker.** The working tracker holds the
  reasoning, the reproductions and the review verdicts behind every row above,
  and it is gitignored. §5 is the durable summary, deliberately one sentence
  per row; a reader who needs the reproduction needs the tracker.

## §7 · The forward rule

**The next milestone the owner opens has its kill gate registered BEFORE its
evidence exists** — in both places the reference registers gates, with the two
texts agreeing, and with an off-ramp that says what a failure buys.

M7 is the exception this project can afford exactly once: a milestone whose
scope was the measured remainder of everything else, which is the one shape for
which an off-ramp cannot be written. Any future milestone that cannot state its
off-ramp is not a milestone; it is a backlog, and it should be tracked as one.

## §8 · The two rulings, kept apart

**Ruled 2026-09-09 — M7 IS GATELESS BY DESIGN, and closes on a written record
of this form.** By the leader, under the owner's delegation of 2026-09-09, on
the three measurements in §2. That ruling stands and is not in question here.

**Whether M7 CLOSES is a separate question, and it is not answered by this
document.** M7 is open at 63 of 64: item 170 reopened it on 2026-09-10 and is
not built, §5 carries 46 filed repository rows and 10 unswept audit partitions,
and §6 names what else is owed. This record is the form the close will take; it
is not the close.

**Decision taken:** _______________________

*A gate ruling is the owner's. The gateless-by-design ruling above was
delegated and is stated plainly so it can be overruled. This line is not
delegated and is left unfilled, in M5's form. Amend by appending, never by
editing a section above.*
