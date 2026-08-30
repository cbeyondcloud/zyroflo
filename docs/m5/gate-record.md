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
