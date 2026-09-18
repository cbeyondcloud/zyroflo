# M7 record — completeness, gateless by design

*Written 2026-09-10 in the form M5's and M6's records use, with one difference
stated at the top: the other six milestones quote a pre-registered gate before
judging it, and this one has no gate to quote. Why that is a ruling rather than
an omission is §2. What it costs is §5, which is the section this document
exists for.*

*First committed at `269b01d` (CI run `34432380000`) with M7 reading 63 of 64,
revised in place the same day when the sixty-fourth row closed, **revised on
2026-09-10** when four open P2 defects became items 171–174 and took M7 to 68 of
68, and **revised again on 2026-09-11** when four further defects landed green
as items **175, 176 and 177**, taking M7 to **71 of 71**. The append-only rule
stated in §1 and §8 governs OVERRULING a ruling; no ruling here has changed and
the decision line at §8 is still unfilled. What changed is a count, the fate of
several rows, and the open remainder — which **went UP again** (§5). A record
that reports a state has to report the state it is in. **A gate record that is
stale about its own milestone is the defect this document exists to describe.***

## §1 · The opening ruling, and the closing that is still the owner's

| | |
|---|---|
| **Opened** | 2026-08-31, by the owner, on the ruling that the config wizard (item 107) comes LAST — after the harness's own functionality is finished. The opening text says, in the owner's own words, **"no pre-registered kill gate"** |
| **Scope** | the MEASURED remainder rather than a wish list: items 108–117 plus 107, which then grew ten times — nine as the lifecycle was RUN, once as the plan set was READ |
| **Ruled** | 2026-09-09 — **GATELESS BY DESIGN, closing on a written record** — by the leader under the owner's delegation of the same day |
| **Registered** | in the reference on 2026-09-09, in Part C and in the verification plan, **marked post hoc and saying so**. M0–M6 were each registered twice before their evidence existed; this entry was written after it |
| **State on the day this was revised** | **71 of 71 — every row carrying M7 is done.** The denominator is **not** the M7 column: the plan set states its own rule — the 70 rows spelling `M7` (items 108–177, contiguous) **plus item 107**, whose milestone cell reads `M6 (non-gating)` because the owner's ruling moved the config wizard last rather than re-milestoning it. 70 + 1 = 71. **Two workers have now nearly shipped the wrong number by counting the column instead of reading the rule**, each catching itself; the rule is restated here because reading it is what the column cannot make you do. The plan set's headline reads **177 items, 176 done**, its one open row being item 33's codex E2E (§6) |
| **Closes on** | a written record of this form **and the owner's decision**. The record is this document. The decision line at §8 is **unfilled**, and a full row count does not fill it: §5 and §6 say what a completed count still leaves owed |

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
is **already falsified ten times** (§4) and would predictably fail again. The
tenth falsification is the sharpest case for it: the re-audit found something,
the finding became the sixty-fourth row, and three measurements then refuted
every design that row could have had. A gate reading *nothing new* would have
failed on a finding that turned out not to be buildable.

**"107 closes it" is recorded as a scope ORDERING, not a gate.** It was met on
2026-09-02, and **every one of the ten reopenings in §4 came afterwards** — the
first three the next day. A closing condition that is met and then followed by
ten reopenings was never a gate; reading it as one would have closed this
milestone on the day its most productive week began.

## §3 · The evidence behind the seventy-one

**M7 stands at 71 of 71, as of 2026-09-11: every row carrying M7 is done.** The
sixty-fourth was **item 170**, filed on 2026-09-10 — which reopened the
milestone for the tenth time (§4) — and closed the same day as **REFUTED by
measurement rather than built**, on this plan set's own precedent for items
113, 128 and 124. What that refusal measured is in §4, because a refusal
without its measurements is an opinion. The denominator moved ten times, and it
moved in the same pass as the work each time. What follows is the evidence
behind the seventy-one, which is what this section can honestly claim. **It is
not a close**; §8 says why.

**The last three — items 175, 176 and 177 — are four defects the resumed sweep
found, all landed green on 2026-09-11.** Each carries a red-first proof and a
mutation pass, and each was found by a partition sweep rather than by a run:

| Item | Row | What it closed | Landed | CI |
|---|---|---|---|---|
| 175 | T229 | **A learned rule could BRICK the governing document.** The profile writer emitted values **unescaped**, so one double-quote in a finding made the PyYAML side unreadable — disarming `profile_limits`, the second independent statement of the file's shape, **permanently, because re-render is idempotent** — and one line break raised a `ProfileError` item 117 measured reaching all 67 call sites. Reachable with **no adversarial input**: a hand-written quoted entry read back clean, then applying any unrelated rule re-emitted it broken. **`test_learning.py:601` asserted exactly this property and could not fire**, because it parametrised the SPELLING axis. The escaping mirrors the decoder already in the reader rather than inventing one; the review fuzzed **7,380 values** through the encoder into both readers with 0 disagreements. **Third serious defect in the only writer of `.sdlc/profile.yml`**, after items 129 and 146 | `c05ebc0` + `6064dbc` + `157cb5b` | `34491056494` green, suite 11m57s |
| 176 | T233 | **The snapshot check was still writing into the graph it audits.** `--check-snapshot` exits 0 and leaves `build_report.json` and `manifest.json` MODIFIED — the `run_id` replaced, `published` swapped for `drifted` — while the control, `--check-fresh`, leaves the tree byte-clean. Item 133 removed only the scratch directory; **the question still replaced the committed provenance of the publish that made the snapshot**, and this is the SIXTH instance of a-check-that-writes after items 96, 100, 65, 120 and 133. **Why nothing saw it is worth more than the fix**: the test is *named* for that property, and its tree helper returned **paths only, no bytes**, so a modified file was invisible by construction — the helper got its first positive control in the same pass | `adc752b` + `1d0bb9a` + `bfc5b5e` | `34494775017` green, suite 14m40s |
| 177 | T221 + T222 | **Two defects in one reader, filed as one item because they share one cause.** (a) A **zero-indent sequence** is legal YAML the hand reader could not depth-track, so a component's dials were promoted to the RUN — `block_on` among them, a control moving with nobody deciding — **and the component was lost at the same time**, `components()` falling back to synthesis. (b) **The crosscheck asked a reader its consumers do not use**: it read top-level only while every consumer reads at any depth, so `coverage_threshold` read 10 against PyYAML's 90 and the guard reported nothing. **It is item 130's own guard** — the item that widened the crosscheck from key sets to values for exactly this reason. Ruled fixed by depth-tracking, not by refusing a spelling PyYAML accepts | `1b03be3` + `990e091` + `57b5494` | `34498872728` green, suite 15m23s |

**The four before those — items 171 to 174 — are the four P2 defects an earlier
revision of this document listed as open in §5.** They were built, reviewed and
landed on 2026-09-10, each with a red-first proof and a mutation pass:

| Item | Row | What it closed | Landed | CI |
|---|---|---|---|---|
| 171 | T147 | **The credential sinks.** Item 161's defect at three more addresses — a secret-named value under eight characters printed intact into `.sdlc/metrics/`, which the installer's own text says is meant to be COMMITTED. The row itself was **wrong twice** and the measurement said so: `policy.py:857` is not a sink at all, and `guardrail.jsonl` is written by a third module (now T212). A **second defect at the same line** was found on the way: a comparison key built from `dict(os.environ)`, blind to any key the harness `.env` supplies, refusing a genuinely green runner by name | `e15b8f0` + `2629233` | `34471495812` green, suite 11m47s |
| 172 | T157 | **The CI verdict vocabulary.** The bucket table held only the CLI's words while the REST rows filled buckets from `.conclusion`, so a genuine RED mapped to `unknown` and neither the log fetch nor the defect route ever fired. **Ruled a boundary TRANSLATION, not a merged table** — merging would have silently accepted a `success` from the subcommand that never emits it, so a producer change would stop being reportable. `rest_rows` was measured NOT dead before anything was built | `931d3a2` | `34473173987` green, suite 13m39s |
| 173 | T160 | **The timed subprocesses, including the push wall.** Four sites with no handler at all, two of them inside the module that had adopted the bounded helper — a rule hardened where somebody was looking, in the item meant to end that class. **Three adopt the helper; the fourth cannot** — its stdout is a TAR and the helper forces text mode — so it catches the timeout by hand, which is what item 167's two-way rule exists for, with a control proving a real archive still returns BYTES | `931d3a2` | `34473173987` green, suite 13m39s |
| 174 | T143 | **The Windows spawn.** `--plan` promised a runner would start where the spawn could not, because the plan check resolved the way `shutil.which` does and the spawn appended only `.exe`; and a `.cmd` runner is launched by the shell, which truncated the prompt at the first newline and expanded machine paths into it. **The altitude was neither option posed** — one function, both callers, agreement by construction. It took **four review rounds, each finding a narrower measured gap**, ending in a twelfth skip site pointing the opposite way that three reviews had missed because it only skipped on the platform every proof run happened on | `ad20c27` | `34478084297` green, suite 14m01s |

**Item 174's cost is stated in §6 rather than here**, because it is a cost and
this section is evidence.

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
| CI at the M7 boundary, `09f897d` | run `34396545382` SUCCESS, **5,438 collected**, suite 13m10s, secret scan 12s |
| Full local Windows suite, same commit | **pytest exit 0 — 5,421 passed + 17 skipped in 1h13m02s** |
| Reconciliation | 5,421 + 17 = **5,438**, the identical total CI collected; the 17 skips are local-only absences CI supplies |
| CI after items 171–174, `ad20c27` | run `34478084297` SUCCESS, **5,480 collected**, suite 14m01s, secret scan 13s |
| CI after items 175–177, `57b5494` | run `34498872728` SUCCESS, **5,556 collected**, suite 15m23s, secret scan 12s |
| **What has NOT been re-taken** | the full local Windows run. The boundary figures above are the ones that were measured; the seven later landings carry CI evidence only, which is Linux evidence only (§6). The suite job has grown from 13m10s at the boundary to 15m23s here — **a 15-minute job is the healthy figure, not a stall**, and it is written down because a stale duration is what makes the next reader misread a working run |

**The plan set agrees with itself at the level its checker can see**: item
148's checker reports `0 disagreement(s) over 354 row reading(s)`, exit 0,
re-run for this revision rather than quoted — 348 at the previous revision,
grown by items 175–177. What that checker cannot see is
§6's last bullet and the reason item 170 was ever filed — and, as §4 records,
nobody has yet built a checker that can.

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
| 10 | 2026-09-10 | **reading the plan set's own prose** — a sweep for filings the same documents had already closed | **item 170 — closed the same day, REFUTED by measurement rather than built** |

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

**And item 170 is the finding the instrument could not act on.** It was filed
as the sixty-fourth row, taken to the owner, given a GO, and then **measured
three times and refused three times**. It closes **Done-as-REFUTED**, which is
this plan set's own precedent — items 113, 128 and 124 all closed on a
measurement rather than on a build. The measurements, because a refusal without
them is an opinion:

- **(a) Read a filing from its own text.** The *names a finished item*
  condition matched **14 of 14** candidate sentences: with the set standing at
  168 of 170 done, almost every item named anywhere is a finished one, so the
  condition collapses into *contains the string `item NN`*. Worse, all four
  true positives fired on the citation idiom — *"Filed, not fixed (item 45's
  rule)"* — where **item 45 is the doctrine being cited, item 150 is the row
  that actually closed the filing, and item 150 is never named.** The check was
  reading the wrong number and scoring itself right.
- **(b) The closer names the filer.** Zero prose graded: for each row saying it
  was filed by item N, assert item N's row carries the closure. **0 false
  positives**, and recall **2 of 9**. The edges exist in **3 rows out of 170**,
  so its ceiling is **3 of 9** — it cannot reach two thirds of the defect no
  matter how well it is written.
- **(c) The filing names its tracking item** — item 113's rule, applied to
  prose. **Coverage ZERO of 47**, at **27 false positives**. It finds none of
  what it is for and objects to what it is not.

**What refused all three at once**: every route bottoms out on the same shared
prose test — *does this paragraph carry a closure clause* — and that test
measured **4 false negatives in 16**. A guard that misses a quarter of the
locations it is pointed at cannot block a gate, and one that cannot block a
gate is a report, not a check.

**The reversal condition, which is the useful half.** None of this is
detectable because **a filing is a SENTENCE and item 148's checker reads
ROWS.** That is the whole distance. A filing written *as a row* would already
be visible to the tool this project has: no new checker would be needed, and
the class would close by construction. So **item 170 reopens if the plan set
adopts a structured form for filings** — a row shape a filing must take, going
forward. **This is not a mandate to rewrite the items that exist.** Retrofitting
174 items of prose is a larger act than the defect, and it is not what this
condition asks for.

**CORRECTED 2026-09-18 UTC: the plan set now carries 177 items, not 174.** Items
175, 176 and 177 were added on 2026-09-11 (§3); the sentence above is not
edited because the append-only rule governs sections that already stood.

## §5 · The open remainder

**This section is the reason this document exists.** The working tracker for
the closure pass lives in `agent-swarm/`, which is **gitignored**, and the plan
set lives outside this repository entirely. A defect recorded only in those two
places is invisible to anyone who clones this repository. **What follows is the
record.**

**The census, re-derived from the tracker by parsing rather than quoted**, on
2026-09-11, after items 175–177 landed: **127 task rows** (127 distinct ids),
**29 audit-partition rows** and 7 integration-batch rows. Each table was parsed
**against its own column count** — the task table is 7 columns, the partition
table 5 and the batch table 6 — because one column index applied to all three
silently mis-reads two of them; and the task table is **interrupted by a blank
line**, so a parser that stops at the first non-row line reads 86 of its 127
rows and loses the rest without saying so. Both traps were hit and corrected in
this pass.

The tracker's declared open vocabulary is `TODO`, `ASSIGNED`, `BLOCKED`,
`RULING-PENDING` and `REVIEW`. **75 rows are open** — **66 task rows and 9 audit
partitions**; 73 carry `TODO` and 2 partitions carry `ASSIGNED`, being swept as
this was written. By priority, exactly as parsed: **P1 0 · P2 3 · P3 39 · P4 21
· P5 3**, with **P1 EMPTY**.

**STALE — these figures were taken on 2026-09-11.** A refreshed census,
measured at 2026-09-18 20:00 UTC, is appended at the end of this section.

**The count went UP again while four more defects were fixed, and that is the
finding, not an accounting artefact.** The previous revision recorded 60 rows a
next reader must act on. Four partitions were then swept and returned
**seventeen findings**; four of those became items 175–177 and landed; closing
them **filed four more** (a reader that cannot escape a backslash, an unanchored
fixture guard, a refusal resting on a premise the measurement does not support,
and a placement no output test can pin); and three sweeps left **tails** their
workers could not reach inside their stops, filed as new partitions. 60 → **75**.
**A defect fixed is a place looked at**, and a place looked at is what files
rows; a remainder that shrinks monotonically would be evidence that nobody was
looking hard. **P2 is no longer empty**, and that too is the sweep working: the
three P2 rows below did not exist a day ago.

Every row below is **open, filed and unfixed**. Priority is by internal
dependency, not by severity alone.

### P2 — three, all new

**P1 is empty. P2 was empty at the previous revision and is not now** — every
row here was filed by the sweeps of 2026-09-10/11.

| Row | The defect, plainly | Lives in |
|---|---|---|
| T225 | **The reference runner points the pre-pass at the repository root — the one thing its own contract says twice not to do.** Pointed there it reviews the BASE commit, so it reports a defect the change just fixed and cannot see one it introduced. And it *cannot* do otherwise: the word `worktree` appears nowhere in it, so it has no pattern for the value the stage generates. The contract files this runner as the proof that the contract is EXECUTABLE — producer and consumer failing to compose, in the one program written to prove they do | `tools/sdlc/reference_runner.py`, `sdlc_templates/RUNNER_CONTRACT.md` |
| T234 | **A target nobody can meet is graded `met`.** `--targets` with a metric name that does not exist exits **0**, the word *unknown* appears nowhere, and the last line reads `verdict: met` — while the MCP tool refuses the identical input by name. Two layers, one question, and only the layer somebody was looking at got the guard | `tools/graph/validate.py`, `tools/graph/mcp_server.py` |
| T235 | **Nothing loaded, reported as a clean build.** `--from-snapshot` with no component carrying a shard exits **0** with `status: "ok"` and `{nodes: 0, edges: 0}` — 0 of 7 components loaded — contradicting `build()`'s own rule twelve lines away, in the path an adopter uses to rebuild from the committed snapshot | `tools/graph/build.py` |

### P3 — thirty-nine

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
| T212 | **A THIRD redaction rule that never reads the environment.** It writes a ledger into `.sdlc/metrics/`, which is committed, and it is the file item 171's own row mis-attributed to another module — so the harness has three rules for one question and the third cannot see the credentials the harness itself injects. Item 161's defect, at an address nobody has looked at. Measure first whether an injected value can reach that writer at all | `tools/sdlc/guard.py` |
| T213 | **The PATHEXT gap may exist a second time.** Item 174 made the driver's plan check and its spawn agree about a shell shim; the conformance smoke resolves a runner command too, and item 149 welded the two precisely so they could not form two opinions. If the gap is there, a smoke can go green against a runner the driver cannot start | `tools/provider_smoke.py` |
| T214 | **An external binary's exit code is ignored** — a non-zero search reads as "no matches" — in the A/B measurement module that item 18's kill gate was decided on. This is this repository's single headline rule, in the file that compares a grep baseline against the impact engine | `tools/impact_ab.py` |
| T216 | **A fifth short-secret address, found by item 171's review**: the runner still gates on a length floor, so a secret-named value under eight characters passes there too. Mitigated in-harness because the driver re-scrubs unfloored, and exposed only on a DIRECT invocation — which item 104 made a supported path. Item 115 exists because this exact module drifted from its harness twin at birth | `tools/sdlc/http_runner.py` |
| T223 | A YAML-1.1 boolean **nested under a mapping** is not crosschecked: `block_on: [on]` gives PyYAML `[True]` and the hand reader `['on']`, silently. The top-level spellings correctly refuse, which is agreeing-by-refusing — so the defect is the NESTING, not the vocabulary. Item 177's neighbour, left filed rather than folded into a reviewed diff | `tools/sdlc/policy.py` |
| T224 | **Duplicate keys are refused by name in one reader and not in the profile reader**, so hand takes the first and PyYAML the last. With the PyYAML import absent, `gate_profile` reads `solo` where PyYAML says `regulated` — **a loosening**. The wider finding is that every one of these refusals VANISHES without PyYAML, which inverts the property the stdlib reader exists for: the harness must govern a machine with no packages | `tools/sdlc/policy.py` |
| T226 | **The first command the graph skill tells an adopter to run cannot answer.** Run as an adopter would, on a real install committed as the installer instructs: `--check-fresh` exits 2, *"STALE — no freshness stamps"*, identically before and after the commit. The stamp-maker is a flag the document never names | `sdlc_templates/GRAPH-SKILL.md` |
| T231 | An unparsable payload and an unrecognised argument **both exit 2**, though the CLI module states that SystemExit passes through precisely so 2 means a typo, and both siblings use 3. A caller cannot tell a bad command line from a payload it could not read | `tools/sdlc/connect.py` |
| T232 | **HYPOTHESIS, NOT REPRODUCED, and the row says so.** A failed audit append may leave a profile change the undo then refuses, making the change invisible to the one check built to find it. **Measure first whether the ordering is reachable** — a hypothesis filed as a defect is what this project keeps having to correct | `tools/sdlc/learning.py` |
| T236 | **A stated property that does not hold, and nothing composes the layers to find out**: the MCP layer is documented as reusing the CLIs' own refusal wording, and three valid pairs all differ. The sharp instance is a number — a budget floor commented *mirrored from the CLI* against a bare literal **no test can read** — and the test named for the composition asserts it in its title only | `tools/graph/mcp_server.py`, `tools/graph/pack.py` |
| T237 | A **deleted**, a **truncated** and a **wrong-shape** build report all yield the same empty run id and status, so could-not-read and empty are one answer — for the document that says whether the graph is trustworthy. The control sits twenty lines below, where an absent file IS stated explicitly | `tools/graph/mcp_server.py` |
| T238 | **The stdlib profile reader tracks quotes without honouring a backslash**, so a value containing `" #` or `","` cannot be encoded and is refused instead. Correct today — refusing beats writing what one reader misreads — but a legitimate finding carrying either sequence cannot be suppressed at all. **The asymmetry item 175 exposed rather than caused**: fed unescaped bytes the hand reader returned the right answer while PyYAML refused the file, which is why a stdlib-only readback could not see a P1 | `tools/sdlc/policy.py` |
| T240 | **Two characters sit in a refusal set on a premise the measurement does not support.** The docstring argues from PyYAML folding a line break; PyYAML was measured returning both verbatim. A refusal resting on a false premise is this project's recurring class, and there are two honest exits: measure the round trip and narrow, or find the real reason and write it | `tools/sdlc/learning.py` |

### P4 — twenty-one

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
| T211 | The length-floored scrub rule has **no production caller** since item 171 moved three sinks to the named rule — this project's most-found class, declared-and-never-read. Filed rather than folded into item 171, because widening a reviewed diff is how it stops being the reviewed diff. Measure first: what scrubs free text now, and does removing the floor re-open the mangling of `true` and `8080` the floor exists for | `tools/sdlc/policy.py` |
| T215 | A by-catch attributed to one module belongs to another — the named exception does not occur in the module the row blames — the **third wrong file attribution written into a tracker row in one day** from a worker's report without tracing. Measure whether the catch is a defect at all before treating it as one | `tools/sdlc/worktree.py` |
| T217 | **One comment states a rule whose consequence flips by site.** One reader slices the note LEADING, so the cut removes the captured output; the two sites item 173 added slice TAILS, so the cut keeps it. Each is right for its own reader and the shared comment is now true of neither in general — item 115's drift class, arriving through a COMMENT rather than a copied constant | `tools/sdlc/release.py` |
| T227 | **A count in the runner contract that moved and was not updated**: it says the first five lines are common to every phase and there are six — the sixth added by item 134, whose whole subject was a runner meeting a command it could not run. The measure-counts-before-they-enter-a-requirement class, again | `sdlc_templates/RUNNER_CONTRACT.md`, `tools/sdlc/stage.py` |
| T228 | **Three phases declare no `reads:` and nobody has checked whether that is correct.** P6's empty declaration was measured CORRECT and seven phases declare one; P5, P8 and P9 have none and were not examined. Item 132's defect was a phase whose procedure described a document its prompt never named, and it cost a paid runner | `sdlc_templates/` |
| T239 | **A fixture guard that matches a basename anywhere under the work tree** — an unanchored existence check, satisfiable by a same-named file at any depth. Reported by item 176's reviewer and deliberately NOT folded in, because widening a reviewed diff is how it stops being the reviewed diff | `tools/graph/tests/test_snapshot_shards.py` |

### P5 — three

| Row | What it is |
|---|---|
| T100 | Final verification of the closure pass — tree clean, HEAD pushed, last CI green, no open rows — which by construction cannot be satisfied while §5 has entries |
| T218 | A comment in `tools/sdlc/release.py` that is simply false and pre-existing: it claims every reader of a message takes a leading slice, and its only two readers return the whole string. A false comment about truncation, in the module where item 155's review blocked twice on truncation |
| T241 | **Nothing pins WHERE the output rebinding sits in `tools/graph/build.py`'s `build()`, only that the program behaves** — and it is a genuine limit rather than a gap. Item 176's reviewer proved by AST enumeration that the two placements are the SAME PROGRAM, so no output test can separate them, and killed both arms of the conditional to show no untested branch hides there. Pinning the placement would need a source-shape assertion, which is idiomatic here; ruled *file, land as is*, because the property it would protect is already caught |

### The nine unswept audit partitions, by file

Each of these names files that **were never read**. They are not clean bills;
they are unexamined ground. Every one carries the same exit criterion: swept
for the twelve recurring classes, each finding filed with `file:line` and a
reproduction, or recorded CLEAN by file. **Six are ground nobody has opened.
Three — A1e, A4d and A7d — are TAILS**: the remainder a sweep could not reach
inside its stop, filed as its own partition rather than left implied, which is
the only honest way to record a sweep that ran out of budget. Two of the nine
were being swept as this was written.

| Partition | Never read |
|---|---|
| A1d | `build()` lines 880–1099, `build_from_snapshot()`, `_report()`/`_print_report()` and the `discover*` functions in `tools/graph/build.py`; a read-only graph directory; an extractor that RAISES; the MCP refusal wording compared against the CLIs; the vacuous-test sweep over both modules' tests |
| A1e | **A tail.** `discover()` and `discover_package()` in `tools/graph/build.py`, unswept entirely, plus roughly 700 lines of `test_build.py` and two blocks of `test_mcp_server.py` unread for mirror tests |
| A2b-2 | the four link packages under `tools/graph/extractors/`, plus the golden-chain and snapshot tests — including the unanswered question of whether any golden is regenerated from the build it checks |
| A2b-3b | extractors `gradle`, `npm`, `openapi`, `http_calls`, `http_routes`, `frontend`, `tests`, all of `spring`, `_java`, `_bindings`, `_canonical`, `_lang` — **and the extractor TESTS**, where the golden-derived-from-the-code class lives |
| A3c | `tools/field_run.py` from line 305, `tools/review/loop.py` from line 200, `tools/review/hitl.py`, `tools/corpus_harvest.py`, `tools/corpus_replay.py`, `tools/impact_ab.py`, `tools/plan_audit.py`, `tools/tests/ban.py` and their tests — three of the twelve classes got no pass at all here |
| A4d | **A tail.** The BODIES of `stage.verify` and `stage.stale`, and `stage.main()` spawned with `--verify`, `--advance` and `--set` — what A4c could not reach after finding item 177 |
| A5b | `tools/sdlc/feedback.py` (bar the one line already fixed) and `tools/sdlc/schema.py` and their tests |
| A7d | **A tail.** `connect.py:93-175`, `debt.py:79-143`, the vacuous-test sweep over `test_debt.py`/`test_connect.py`/`test_metrics.py`, the empty-parametrise sweep across all four, and whether anything gates on `home` |
| A8b | `--bundle`, `--dump-templates`, `--dry-run`, workspace installs, the emitter-spawn drift path, the vendored-asset negative cases (empty asset list, mismatched checksum, absent binary), and the unwritable-target and newer-install refusals in `sdlc-init.py` |

### Census refreshed — measured at 2026-09-18 20:00 UTC

**The census above was taken on 2026-09-11.** The figures below are a fresh
derivation from the tracker, parsed the same way (per table, against its own
column count), at **2026-09-18 20:00 UTC**. The tracker is live and these
numbers are a snapshot at that instant.

**279 task rows** (279 distinct ids), **36 audit-partition rows** and
7 integration-batch rows. **96 rows are open** — **87 task rows and 9 audit
partitions**; 86 task rows carry `TODO` and 1 carries `NEEDS-RULING`; all 9
partitions carry `TODO`. By priority, exactly as parsed: **P1 0 · P2 6 · P3 32
· P4 40 · P5 5**, with **OWNER 3 · LAST 1** (non-standard priorities on rows
T369, T370, T371, T388); **P1 still EMPTY**.

**What moved.** The previous census (2026-09-11) recorded 127 task rows, 29
partitions and 75 open. The M8 swarm added **152 task rows** (items 178–197
plus defect rows), **7 partition rows** (new sweeps and tails), and the open
count rose from 75 to **96** — the same pattern §5 recorded within M7: a
sweep that files more rows than it closes.

**Of the nine partitions the 2026-09-11 census listed as unswept, seven
closed** (A2b-2, A2b-3b, A3c, A4d, A5b, A7d, A8b) **and seven new ones
opened** (A1f, A3e, A4e, A5c, A7e, A7f, A8d), keeping the count at nine.
**A1d and A1e remain open from the original list.** The current nine:

| Partition | What is unswept |
|---|---|
| A1d | `build()` 880–1099, `build_from_snapshot()`, `_report()`/`_print_report()`, `discover*`; a read-only graph directory; an extractor that RAISES; the MCP refusal-wording comparison; the vacuous-test sweep over both modules' tests |
| A1e | `discover()` and `discover_package()` in `build.py`, unswept entirely; plus ~700 lines of `test_build.py` and two blocks of `test_mcp_server.py` unread for mirror tests |
| A1f | `test_build.py:235–918` (~684 lines), the bodies A1e did not reach |
| A3e | `test_review_fixes.py` (314 lines, unopened); `field_run.py`'s four gate bodies below line 380; the tail from A3c |
| A4e | `verify`'s conditional-judge branch, backward `--advance`, the routing-refusal branch — the tail from A4c after finding item 177 |
| A5c | `repin`'s `capture` read-back; `schema.py` against `policy.validate`; the `importorskip` question under `SDLC_FORBID_SKIPS=1` |
| A7e | `metrics.py` itself (never opened); `test_debt.py`, `test_connect.py` and `test_metrics.py` never run whole; the tail from A7d |
| A7f | `test_connect.py` (1522 lines) and `test_debt.py` (787 lines) — A7e ran them but only read the regions near `metrics.py` |
| A8d | `--estate` with the graph build chained, `--from-snapshot`, `--bundle`, and what a `--single-component` refusal leaves behind — the tail from A8b |

## §6 · What is NOT claimed

- **NOT that M7 is closed.** Every row carrying M7 is done — 71 of 71 — and
  that is a fact about ROWS, not a closure. Whether the milestone closes is the
  owner's, the decision line at §8 is unfilled, and this record is written in
  that state rather than against it.
- **NOT that no further defect exists.** The reopening history says otherwise
  ten times. The next walk — or the next reading — may reopen this milestone
  again, and **that is the milestone's nature, not a gate failing.**
- **NOT that the audit is finished, and the last four landings did not move
  that.** Nine partitions above were never swept, **six of them ground nobody
  has opened**. Twenty of the twenty-nine partitions have been swept; the four
  swept since the previous revision returned **seventeen findings**, so there is
  no basis whatever for believing the remaining nine hold none — the last
  sample says the opposite.
- **NOT that the remainder is shrinking — it ROSE while the work was being
  done.** Four defects landed as items 175–177 and the remainder went from 60
  to **75**: the four sweeps that found them filed seventeen rows, closing them
  filed four more, and three of the sweeps left tails they could not reach.
  **P2, empty at the previous revision, now holds three.** Thirty-nine P3 rows,
  twenty-one P4 rows and three P5 rows are filed and unfixed. This is what a
  working sweep looks like from the inside, and it is said here rather than
  hidden, because a remainder that only ever falls is a measurement of
  attention, not of defects.
- **NOT that a finding named in a sweep is a finding anyone can count.** One of
  the seventeen — a P2 — is named in its partition's own record and **carries
  no row anywhere in the tracker**, so every count above, including this
  document's, is blind to it. That is item 170's defect exactly: the record
  compares ROWS and the claim lives in PROSE, one level below where any checker
  looks. It is stated here because §5 cannot carry what has no id.
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
  (Fifteen was the first count and **sixteen is the measured one**. The reason
  recorded in this parenthesis until 2026-09-10 — *one cited location does not
  exist and one section is two paragraphs* — was itself **superseded and left
  standing**, which is a stale explanation inside the document about stale
  explanations. The measured cause: the earlier sweep **folded two distinct
  filings, 49 lines apart and closed by two different items — 144 and 145 —
  into a single slot.** The number was right; the reason was not, and the
  reason is what a reader would have relied on.)
- **The prose-level staleness check was filed, it REOPENED THIS MILESTONE, and
  it was then REFUSED — so the defect it names is still there.** It is **item
  170**, filed 2026-09-10, the tenth reopening in §4 and the sixty-fourth row
  of M7, closed the same day as Done-as-REFUTED. The reason it was filed is
  exact: item 148's checker compares ROWS, every one of the nine stale filings
  lives in PROSE, and on the morning they were all stale the checker reported
  `0 disagreement(s) over 338 row reading(s)` against every one of them — a
  guard hardened where somebody was looking, in the bookkeeping of the item
  built to end that class. **The reason it was refused is measured, in §4**, and
  the honest reading is not that the gap closed; it is that **three designs
  were measured and none of them could block a gate**. What would close it is
  written into §4 as a reversal condition, and it is a change to the FORM
  filings take rather than another checker.
- **NOT that CI green means Windows green — and item 174 made that sharper
  rather than softer.** CI is Linux-only. The boundary Windows run is the
  per-milestone duty; it passed at exit 0 at `09f897d` and has **not been
  re-taken since — seven landings ago now, items 171–177** (§3). T164 records
  that it takes 1h13m and that one test is most of it — a check nobody can
  afford to run is a check that stops being run.
- **NOT that items 175–177 closed the classes they belong to.** Each landed
  green and each left its neighbours filed: item 175's reader still cannot
  escape a backslash (T238) and still refuses two characters on a premise the
  measurement does not support (T240); item 176's fixture guard is still
  unanchored (T239) and its placement is pinned by nothing (T241); item 177's
  own reader still fails to crosscheck a nested YAML-1.1 boolean (T223) and
  still takes the first of two duplicate keys where PyYAML takes the last —
  **a loosening, and one that vanishes entirely when PyYAML is absent** (T224).
  They were filed rather than folded in, deliberately, because widening a
  reviewed diff is how it stops being the reviewed diff; the cost of that rule
  is that a landed fix is a narrower claim than its class.
- **NOT that the checks now watch what they were written for — three of the
  four landings were guards that could not fire.** Item 175's property was
  asserted by a test that parametrised the wrong axis; item 176's test is
  *named* for the property it could not see, because its helper compared paths
  and not bytes; item 177's crosscheck asked a reader its consumers do not use.
  In all three the guard was PRESENT. **A guard nobody has watched fire is not
  evidence**, and the general form of that is this repository's headline rule.
- **Item 174's cost, stated rather than buried: its Windows tests now run on
  BOTH platforms, and CI still has no Windows evidence.** This repository
  tolerates no skip of any kind — the conftest turns any skip into exit 1 under
  the flag CI sets, deliberately, because a permanently-red gate gets disabled —
  so a Windows-only test cannot exist here and the eleven were rewritten to
  assert the correct POSIX behaviour on the other branch. That removes the skip;
  **it does not supply the evidence.** Measured on a real Linux: the two mutants
  that matter — the shell-suffix table and the `.exe`-append rule — **SURVIVE**,
  unreachable behind the platform guard, **with a positive control dying at six
  failures to prove that is a measurement and not a blind harness.** A reader
  must not conclude from a green build that the Windows behaviour is covered.
  Three reviews also missed a twelfth skip site pointing the opposite way,
  because it only skipped on the platform every proof run happened on.
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
document.** M7 now reads 71 of 71 — **and that changes nothing here.** A row
count is a fact about rows: it says the seventy-one things this milestone
enumerated have each reached an outcome, one of them a refutation rather than a
build. It does not say the milestone is finished, because §5 carries **66 filed
repository rows and 9 unswept audit partitions** that no M7 row ever counted,
§6 names what else is owed, and §4's tenth reopening closed by being refused
rather than by the defect going away. **A full count is not a closure, and
reading it as one is exactly the mistake this record exists to prevent** — the
sharper version of which is that the remainder **grew from 60 to 75** in the
same pass that took the numerator from 68 to 71.

That is why the line below is still blank. M4's record judged every one of its
conditions and left `Decision taken` unfilled for the same reason: the judging
was delegated and the closing was not. This record is the form the close will
take; it is not the close.

**Decision taken:** _______________________

*A gate ruling is the owner's. The gateless-by-design ruling above was
delegated and is stated plainly so it can be overruled. This line is not
delegated and is left unfilled, in M5's form. Amend by appending, never by
editing a section above.*
