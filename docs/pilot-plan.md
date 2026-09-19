# Pilot plan and maintainer cadence

*Item 58 · M6 condition **C7** — pilot KPIs defined, and the maintainer cadence
documented.*

Every mechanism named here already exists and has run. This document decides
**how often**, **who acts**, and **what the number has to be** — the parts no
amount of code supplies.

---

## 1 · The pilot KPI

**Five runs reach close-out in eight weeks.**

Counted from `metrics.json`: `runs.closed`, over runs a **person** opened.

**Why person-opened.** The harness opens runs by itself — `debt.py --file` when
close-out surfaces debt, and P10's auto-intake when a probe fails. Those are the
Operate→Plan loop working, and counting them as adoption would let the tool
inflate its own score. A run is person-opened when its `00-intake.json` source
kind is not `debt` or `probe` — the two `src_kind` values those two tools write.

**Why five, and why eight weeks.** The measured baseline on 2026-08-30 was **9
runs opened and 1 closed**, with the open ones sitting at P0 (4) and P7 (3), P8
(1) and P10 (1). Runs open easily and close rarely. Five in eight weeks is roughly
one a fortnight — ambitious against that, and long enough that a stall at one
phase reads as a pattern rather than a bad week.

**The number is an instrument, not a target to hit.** Where runs stall matters
more than how many finished, which is why the miss rule below asks for the stall
before it asks for a verdict.

### Supporting measures — reported, never the gate

Read from the same document, for continuity rather than judgement:

| measure | why it is not the gate |
|---|---|
| `gates.forced` and `gates.forced_advances` | An override is a decision somebody took on purpose. A pilot that forced nothing may have avoided hard changes rather than run clean ones. |
| `review.median_duration_s` | **M4 ruled on this**: a time cap on human review has the incentive backwards, because G3 exists so every agent change is looked at properly and gating on speed rewards the rubber stamp. Reported, never graded. |
| `debt.outstanding` and debt round-trips | The Operate→Plan loop turning is the hardest thing to fake, and the slowest to accumulate. |
| `corpus.replay_age_days` | Whether the nightly replay is still running at all. |

### If the KPI is missed

**A miss is a finding, not a verdict.** At eight weeks, if fewer than five
person-opened runs closed:

1. Read **where** they stalled, from `runs.by_phase` and the dashboard's team
   view. A pile at P0 means runs are opened and abandoned; a pile at P7 means
   triage is where the lifecycle costs more than it returns.
2. Write that up as a ruling on the pilot, the way `docs/m4/gate-record.md` and
   `docs/m5/gate-record.md` are written: the evidence, the verdict, and what is
   NOT claimed.
3. Re-rule on that evidence.

Extending the window is a recalibration and costs the same as any other: a
written rationale, on the record. This project has spent exactly one (M1, numeric
floors, 2026-08-20) and declined a second at M4.

---

## 2 · The maintainer cadence

Four recurring checks. Three are runbook steps a maintainer performs; one is a
scheduled job, because its value decays fastest if nobody runs it.

### Weekly — publish the snapshot *(scheduled)*

```
build.py --workspace <ws> --out <out> --publish-snapshot
```

Writes each component's extracted **facts** into that component's own
`.sdlc/graph/`. Facts only — what link passes derive is recomputed at load and
never committed, which is what keeps the snapshot diffable.

It refuses when the build had failures or contract violations, so a red build
cannot quietly become the new baseline.

**This one is a workflow** (`sdlc_templates/snapshot-publish.yml`, written by
`sdlc-init.py --snapshot-cadence`) for the reason item 101 established: a
committed baseline nobody refreshes goes stale silently, and stale is
indistinguishable from current until somebody acts on it. The job is **opt-in**,
because a scheduled build on a repository that cannot build one reddens every
week, and a recurring red is how a real check gets switched off.

### Weekly — check for drift *(runbook)*

```
build.py --workspace <ws> --out <out> --check-snapshot
```

Rebuilds and byte-compares against each committed shard. Exit 2 names the
component whose files stopped extracting to their committed bytes — or, when no
component has a committed shard at all, refuses a tree it could not check rather
than report it clean (publish the baseline first).

**Drift has two opposite causes and the check cannot tell them apart. Ask the
manifest first:**

| `manifest.stale_repos` says | what it means | what to do |
|---|---|---|
| HEADs have moved | the code changed; the snapshot is simply old | re-publish |
| HEADs are unchanged | the same input extracts to different bytes | **an extractor regression** — a defect, not a refresh |

Re-publishing a drift you did not diagnose overwrites the evidence of the second
case with the remedy for the first. That is the one step in this document worth
being slow about.

### Weekly — is the nightly replay still running? *(runbook)*

```
corpus_replay.py --target <repo> --check-fresh
```

Four answers, and three of them are not fine: `never`, `stale`, `unreadable`,
`fresh`. **A schedule that silently stopped and a schedule that finds no
regressions produce the same output**, and GitHub disables scheduled workflows
after 60 days of repository inactivity without saying so.

### On cause — re-pin vendored binaries *(runbook)*

```
third_party.py --verify
```

**Re-pin on cause, never on a calendar.** A reason means one of:

- a known advisory against the pinned version;
- a defect the newer version fixes;
- an item that needs a capability the pinned version lacks.

A scheduled bump arrives on a day nobody chose and changes behaviour nobody asked
to change. This project has been surprised twice by exactly that shape —
`osv-scanner`'s walker honouring git exclusions (item 40) and the tree-sitter
TypeScript platform split that made two developers' graphs differ by 782 edges
(item 88).

`--verify` is still run weekly: it re-hashes what is on disk against the manifest
and exits non-zero for a tool that is declared and unpinned. That is a different
question from *should we bump*, and it is the one with a mechanical answer.

**Dropping a pin is as legitimate as adding one.** `difft` was declared at
`needed_by: M4` for a diff render nothing consumed, and was removed rather than
carried (`7f5d392`).

---

## 3 · What this plan does not claim

- **No pilot has run.** This defines the KPI, the window and the cadence; it
  reports no result, and the baseline quoted above is one machine's own use
  rather than a pilot's.
- **The KPI counts completion, not value.** Five closed runs say the lifecycle
  can be finished, not that finishing it was worth the cost. The supporting
  measures are where that argument would have to be made.
- **DORA stays unmeasurable** until something reaches production. Four nulls with
  reasons, not four zeros — `metrics.py`'s own rule, and the reason the deploy
  behind M5's C2 does not count: it was a local runtime.
