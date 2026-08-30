# M5 gate record

*Defect + deploy flows · learning loop v1 · observe/operate.*

Written the way M0–M4's records were: the pre-registered text quoted first, then a
verdict per condition carried by named tests **plus field runs of the CLIs**, then
the limitations, then a decision left where decisions belong.

---

## M5 kill gate — defect + deploy flows + learning loop — 2026-08-30

### 1. The first finding is about this record's own subject

**The gate is pre-registered TWICE in the reference and the two lists differ.**
The master plan has been tracking a one-line summary of it — *"Defect + deploy
flows E2E · one learned rule round-trips G3 and `--undo` · corpus floors +
nightly replay"* — and on 2026-08-30 that summary was read as **four**
conditions, three met, then four met.

It is **ten**.

This is not a new lesson. It is the same one M4's record found on 2026-08-25,
where the tracked "five conditions" turned out to be a compression of ten, and
the same one its 2026-08-26 amendment wrote down as a standing instruction:
**read the condition text, not a summary of it.** The instruction was on file
and was not followed, which is worth more than the miscount: a rule that only
works when somebody remembers it is the shape this project keeps finding in its
own code.

**The pre-registered text, quoted.**

Part C, the milestone entry (reference line 240):

> **Kill gate: one real defect + one deploy change E2E green; one distilled rule
> change round-trips G3 and reverts via `--undo` (audit shows both); corpus eval
> suite green; one release cut end-to-end (version, changelog, notes, tag) and
> one tech-debt item round-trips close-out → intake.**

The verification plan (reference line 404):

> M5: real defect + real deploy change E2E; one learned rule change passes the
> **deepeval corpus suite**, lands via G3, then reverts via `--undo` (audit shows
> both); CI watch + P10 probe paths exercised; nightly corpus replay publishes
> precision@k; `metrics.py` emits `metrics.json`.

Judged below against their **union**. Neither list is discarded: a condition
appearing in only one of them is still a condition somebody wrote down before
the evidence existed, which is the whole point of pre-registering.

**One transcription correction, in the same class as M4's two.** The
verification plan names a **deepeval** corpus suite. Item 50 measured that out
and recorded why: `deepeval` is an uninstalled optional extra, and item 51 had
built a corpus regression a day earlier, so using deepeval would have been a
second implementation of one gate — the same ruling item 11 reached about
duckdb and item 52 reached again. The condition is judged as *the corpus gate*,
which is what exists.

### 2. The conditions

| # | Condition | Pre-registered in | Verdict |
|---|---|---|---|
| C1 | One real defect E2E green | both | **MET** |
| C2 | One deploy change E2E green | both | **MET** |
| C3 | One distilled rule change round-trips G3 and reverts via `--undo`, audit shows both | both | **MET** |
| C4 | Corpus eval suite green | Part C | **MET** |
| C5 | One release cut end-to-end — version, changelog, notes, tag | Part C | **NOT MET** |
| C6 | One tech-debt item round-trips close-out → intake | Part C | **PARTIAL** |
| C7 | CI watch exercised | verification | **MET** |
| C8 | P10 probe paths exercised | verification | **NOT MET** |
| C9 | **Nightly** corpus replay publishes precision@k | verification | **PARTIAL** |
| C10 | `metrics.py` emits `metrics.json` | verification | **MET** |

**6 MET · 2 PARTIAL · 2 NOT MET.**

**C1 — MET.** Item 45. A real defect entered as a **symptom** — a failing test
with the numbers quoted and no diagnosis offered, on purpose — and went P0 → G4
against a live repository. P1 derived the cause from the bitmask itself: a
`DeserializationFeature` mask written as an ordinal literal, which later Jackson
releases re-encoded by inserting members. **Nothing in the repository was
broken**, which is a conclusion the pipeline had to be able to reach and argue.
Every gate taken by the owner; **G1, G2 and G3 each went VOID and were re-taken**
when corrections moved approved bytes. Merge gate: `OK - the record holds`,
exit 0.

**C2 — MET, and the evidence includes a false green and its correction.** Item
46 (`d1abef5`). A one-line environment change went P0 → G5 → P9 against a real
local runtime — a rebuilt artifact replacing a running process, not a
simulation. **The first attempt reported `exit 0 (deployed)` for a change that
never shipped**: P9 ran the deploy command with the repository ROOT as its
working directory, so it built the BASE; its own process died on a bound port;
and the health check passed because the process it was replacing was still
serving. Fixed, re-run, and **verified four ways** — `where` is the run's
worktree, the jar that run built carries the changed value inside it, the port
is held by the pid the deploy started, and that pid is running the worktree's
jar. `stage --verify --phase P9` → `P9: clean`, exit 0.

A green that had never been wrong would be weaker evidence than this one.

**C3 — MET.** Item 50, demonstrated with every component real: both gates refuse
and then pass; the declared finding is suppressed while an undeclared one still
fires; the profile is restored byte-for-byte and `--audit` reads **applied AND
undone**, with `restored == before`. The condition's parenthetical — *audit
shows both* — is the part that is checked, not assumed.

**C4 — MET.** Item 51, field-verified on a real harvest of **132 cases**: all
three floors met (scoping 112/30, review 34/15, defect 22/10) at exit 0, and an
absent corpus exits 1 saying so rather than reporting zero.

**C5 — NOT MET.** Item 65 is pending and nothing has cut a release. Measured
rather than inferred: `version_command` and `changelog_command` are **blank** in
the profile of the only repository the harness has a gated run against, so the
machinery has no commands to run. No version, no changelog, no notes, no tag.

**C6 — PARTIAL.** Item 66 is built and field-verified: against the live
repository it finds exactly one real debt item — a quarantined test still
declared at the base — and correctly excludes the rejected finding from the same
run. **But nothing has actually round-tripped.** Filing is `--file`, deliberately
an explicit act, and it has not been taken, so no close-out has produced an
intake. The condition says *round-trips*, and finding-what-would-be-filed is one
half of a trip.

**C7 — MET.** Item 47, on a real pull request: push allowed, PR opened on a fork
the owner controls, **`pending` then `red` both correct**, the real CI log
fetched, and item 44's ledger gate refusing for a correct reason. Four of the
five verdicts this watch can return are not green, and each is judged rather
than folded into a pass.

**C8 — NOT MET. P10 has never run, anywhere.** Item 48 is pending; no probes are
configured on any profile; there is no post-deploy verification, no rollback
proposal, and no auto defect intake from a failed rollout. This is the condition
the deploy of C2 makes *possible* and does not satisfy: there is now, for the
first time, a running service to probe.

**C9 — PARTIAL.** The replay exists, scores **per case** under each case's own
sha, and publishes precision@k — item 51, field-verified both ways. **Nothing
schedules it**, and item 51's own record says so out loud, with the reason: this
repository has no corpus, so a job here would report nothing and read as
coverage. The condition's word is *nightly*, and a mechanism nobody runs on a
cadence does not meet it. Recorded as partial rather than met, because the gap
is a scheduler and a corpus-bearing repository, not code.

**C10 — MET, measured for this record rather than cited.**
`metrics.py --write` against the fork emits `metrics.json`, and the **DORA
section has changed meaning**: where it read four nulls *"defined against
production, and this repository has never deployed"*, it now reads *"1 run(s)
reached a deploy phase and the measure is not implemented yet — the premise now
holds, so this is work rather than an absence."* The deploy turned an absence
into named work, which is the behaviour item 52 was built for.

### 3. What is NOT claimed

- **No run has gone P0 → P11 in one piece.** The defect run stopped at G4; the
  deploy run entered at P9 on an already-merged premise. Every phase has been
  exercised except P10 — but not consecutively, in one run, by one change.
- **The deploy was to a LOCAL runtime, not production.** That is why C10's DORA
  measures stay null rather than counting it, and it is stated here so a later
  reader does not mistake this record for a production rollout.
- **The open pull request on the fork is a test fixture, not a gated change.**
  Its run has two history entries, both P0, and **no approvals file at all** — it
  exists to give C7's CI watch something real to watch. Item 44's ledger gate
  refuses it, correctly, and it has not been merged. A record that let that PR
  count as a merged run would be this project merging over its own gate.
- **C6 and C9 are partial for opposite reasons**, and neither is a code defect:
  C6 needs somebody to take an act the tool deliberately does not take for them,
  and C9 needs a cadence and a corpus-bearing repository.

### 4. Decision

The gate reads **all-pass** on its own wording — *"else the pipeline does not go
to breadth"* — so **on its pre-registered terms M5 does not pass today**.

Three options, stated as M4's record stated them:

1. **Hold.** Take C8 (item 48, P10 + rollback) and C5 (item 65, release cut),
   then close C6 by filing one debt item and C9 by scheduling the replay where a
   corpus exists. Two are line items already on the plan; two are acts, not code.
2. **Recalibrate with rationale.** This project has spent exactly one such
   recalibration, on M1's numeric floors on 2026-08-20, and M4 declined to spend
   a second. Note the asymmetry M4 recorded: M1 moved numeric FLOORS, whereas
   dropping C5 or C8 would remove a condition about a phase that has never run.
3. **Halt the line.**

**Decision taken:** _______________________

*A gate ruling is the owner's. Every prior milestone here was funded by its gate
record, and M6 should not open until this line is filled in.*


---

## M5 gate — amendment: C8 NOT MET → MET — 2026-08-30

**Appended, never edited into section 2.** The verdict above stood for the
hours it stood, and a gate record that quietly rewrites its own history is not
evidence.

**C8 — P10 probe paths exercised — is now MET.** Item 48 landed the same day
(`a976213`, CI 4181 passed) and was field-verified **both ways** against the
service item 46's deploy had put on this machine:

| | |
|---|---|
| Healthy | `PASS`, exit 0, 0.07s |
| Service stopped, same command re-run | `unreachable` — **not** `fail` — verdict FAIL, exit 1 |
| What it produced | a rollback proposal naming the real branch, base commit, deploy command and tree; a defect run opened automatically |
| Probed a third time | filed nothing, said *already open* |

Section 2's own wording is what made this cheap to close: *"this is the
condition the deploy of C2 makes possible and does not satisfy: there is now,
for the first time, a running service to probe."* That was true when it was
written and stopped being true four hours later.

**The gate now stands at 7 MET · 2 PARTIAL · 1 NOT MET.** It still does not
pass, and **Decision taken** in section 4 remains unfilled.

**What is left, and none of it is large:**

- **C5 — one release cut end-to-end (version, changelog, notes, tag).** Item 65,
  the only NOT MET. `version_command` and `changelog_command` are blank, so this
  needs commands as well as code.
- **C6 — one tech-debt item round-trips close-out → intake.** PARTIAL and not
  code: `debt.py` finds exactly one real item on the repository to hand and has
  never been asked to FILE it, because filing is deliberately an act.
- **C9 — a NIGHTLY corpus replay.** PARTIAL and not code: the replay publishes
  precision@k per case and nothing schedules it.

**One property of item 48 is worth recording here rather than only on its own
row**, because it is about what this gate can be trusted to mean. P10 refuses to
verify a run with no deploy record, and refuses a run whose deploy failed, with
the reason stated in the refusal: *a probe that passes here would be describing
whatever was already running.* A post-deploy check that could be satisfied by an
unrelated healthy service is the same defect as the false green C2 records, one
phase later — and this record's own C2 evidence is why the refusal exists.


---

## M5 gate — amendment: C5 NOT MET → MET, C6 PARTIAL → MET — 2026-08-30

**Appended, not edited in.** Two more conditions moved the same day, both by
line items landing rather than by anything being reinterpreted.

**C5 — one release cut end-to-end — is MET.** Item 65 (`094ad25`, CI 4221
passed) gave `release.version_command`, `changelog_command`, `tag_command` and
`notes_from_run` their first reader; they had shipped in the profile template
since item 60 with nothing reading any of them. Cut on a real repository:

| Step | Result |
|---|---|
| version | `2.9.3`, from the module's own pom with `-SNAPSHOT` dropped |
| changelog | an entry naming that version, from real commits |
| notes | drafted from the run's own artifacts |
| tag | created locally — `git ls-remote --tags` confirms **not pushed** |

Section 2's verdict said this was *"measured rather than inferred: the commands
are blank, so the machinery has no commands to run."* Both halves have been
answered — there is machinery, and there are commands.

**C6 — one tech-debt item round-trips close-out → intake — is MET.** The trip
was taken, not just made possible: `debt.py --file` found the one real item the
corpus repository declares (a quarantined test still red at the base, because
the run that fixes it is on an unmerged branch), opened run
`20260830-032131-ef8926` through `intake.open_run` — the ordinary front door,
not a private path — and the item now reports *open* rather than outstanding.
The dirty-tree override is recorded on the run, because the repository carries
an uncommitted corpus and `intake` refused rather than assuming.

**The gate now stands at 9 MET · 1 PARTIAL · 0 NOT MET.**

**It still does not pass, and what is left is one word.** C9 reads *nightly*
corpus replay. The replay exists, publishes precision@k **per case** under each
case's own sha, and is field-verified both ways — and **nothing schedules it**.
That is a cadence and a corpus-bearing repository to run it in, not code. The
honest options are a scheduled job the installer can write into an adopter
repository, or an explicit ruling that *nightly* meant *repeatable* — and the
second is a recalibration, which this project has spent once and declined once.

**Decision taken** in section 4 remains unfilled.


---

## M5 gate — ruling: C9 MET, gate CLOSED — 2026-08-30

**Ruled by the owner on this record**, after choosing to run the replay once
before ruling rather than ruling on the machinery. That choice is the reason
this section exists in the shape it does.

### A. The owner's ruling on C9

**C9 — a nightly corpus replay publishing precision@k — is MET.**

What stands behind it, in the order it was built:

| | |
|---|---|
| The replay itself | item 51 — per-case scoring under each case's own sha |
| A schedule, and a way to tell it stopped | item 101 — the workflow, plus `--check-fresh` with four answers |
| A corpus that is not the harness's own bookkeeping | item 103 |
| **A real run, published** | 2026-08-30 — three cases, three graph builds at historical parents, a row in `corpus-replay.jsonl` |

**The reasoning, stated so it can be argued with.** What C9 asked for was a
replay that runs on a cadence and publishes precision@k. The replay exists, it
publishes per case, it is schedulable by a workflow the installer writes, a
stopped schedule is detectable rather than silent, and it has now been run
against a real corpus and published a real number. **What is left is a cron
having fired**, and that is a thing a timer does, not a thing a gate ruling can
conjure or a thing more code can supply.

This is judged NOT to be a recalibration. The condition's substance — that
ranking quality is measured repeatedly and published, rather than proven once
and remembered — is satisfied by mechanisms that exist and have run. The
project's one spent recalibration (M1, 2026-08-20) moved numeric FLOORS; nothing
here moves a threshold or excuses an unbuilt thing.

### B. A wrong turn, on the record

**The zeros were diagnosed as the monorepo modelling gap, and that was wrong.**
All three cases scored `precision@10 = 0.0`, and the operator concluded from
`policy.components(<the corpus repo>) == 1` that the replay was ranking over a
single-node graph — citing item 51's own recorded caveat about exactly that.

Measured afterwards, the replay builds its **own** derived workspace:
`derived_workspace` links every sibling component in place and extracts the
judged one at its parent commit, so the graph carries **27 components** and the
judged component is among them, holding real code. Item 51's caveat does not
apply to this setup.

It is recorded because a gate record that quietly drops a wrong turn is not
evidence, and because the error has a name this project already uses: **an
answer inferred where it could have been measured**, on a question where
measuring took one command.

### C. What the numbers actually say

| case | truth size | first true positive at rank | recall@20 |
|---|---|---|---|
| `e22c7c545aa3` | 49 | 24 | 0.0 |
| `653ae81c3508` | 2 | 133 | 0.0 |
| `1b8f6a68b2bc` | 2 | none | 0.0 |

The engine ranks true files, and ranks them deep. For context item 18's blind
A/B measured recall@20 at 0.09 on its three judged commits, so ~0 on a different
three is a low reading rather than a broken one. **The row is a valid baseline**:
future runs compare per case, and the regression rule fires on `is_now == 0 and
was > 0`, so a case that stays at zero raises no false alarm.

### D. The gate

**10 MET · 0 PARTIAL · 0 NOT MET.**

C1 defect E2E · C2 deploy E2E · C3 learned-rule round trip · C4 corpus floors ·
C5 release cut · C6 tech-debt round trip · C7 CI watch · C8 P10 probes ·
**C9 nightly replay** · C10 `metrics.json`.

**Decision taken: PASS.** Ruled by the owner, 2026-08-30, on this record. M6 is
open.

### E. What this ruling does NOT claim

- **The nightly job has never fired on a schedule anywhere.** The workflow
  exists and is installed nowhere; the run behind C9 was started by hand.
- **The scores are LOW.** C9 asks that ranking quality be measured and
  published, not that it be good. What the first real measurement says is that
  on this estate the impact engine puts the first true file at rank 24 in the
  best of three cases — which is information the project did not have this
  morning, and is the argument for the cadence rather than against it.
- **No run has gone P0 → P11 in one piece.** Every phase has been exercised;
  not consecutively, by one change.
- **The deploy behind C2 was to a LOCAL runtime, not production**, which is why
  item 52's DORA measures stay null rather than counting it.

### F. What closing this gate cost, and what that says

Three items were built while trying to close ONE condition — 101 (the schedule
and the freshness check), 102 (a wall of digests nothing read, shipping 269
false secret findings into every adopter's repository), and 103 (the corpus
scoring the harness's own bookkeeping, which would have published a meaningless
zero every night forever).

**Two of the three were found by running something, not by reading it**, and
neither was visible from the code. That is the same finding item 46 produced at
P9 and item 45 produced at P0, and it is the strongest single argument this
milestone makes for the pipeline it exists to gate.
