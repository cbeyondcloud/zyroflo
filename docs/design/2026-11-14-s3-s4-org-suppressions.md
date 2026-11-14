# Design note: an organisation's review suppressions (S3) and a per-run disposition reader (S4)

**Status:** design pass, 2026-11-14, for the owner's sign-off. No code, no backlog edit. The two
rows this note proposes are drafted in the appendix and are filed only on the owner's word
(ground rule 5, backlog-first). Line numbers are against `origin/main` at "docs(backlog): file
T714 (fixed on its branch, not merged) and T715 as Deferred".

**The two seams.**

- **S3.** An organisation's policy may list finding ids to suppress in review. One new
  `policy.yml` key, read together with the repository's own `review_suppress` through
  `suppress_of`, reported as org-set, and unable to silence a finding the gate weighs. Nothing
  here is about who writes the list or how it reaches a repository.
- **S4.** A public reader, `feedback.review_dispositions(target, run_id)`, that yields
  `(finding, source, disposition)` for every finding recorded in that run's review sessions,
  accepted ones included, and nothing else: no path, severity or line.

---

## 1. The stop condition: does tighten-only allow S3?

**Verdict: yes, with two conditions, and S3 stops if either is dropped.**

**What tighten-only actually constrains.** `policy.yml` is "the LOOSEST each dial may ever be",
and the run configuration (`.sdlc/profile.yml`, flags, environment) "may only TIGHTEN"
(`tools/sdlc/policy.py:3-10`; `sdlc_templates/policy.yml:1-11`; `sdlc/LIFECYCLE.md:81-82`).
The rule is a relation between two documents. It does not forbid the constitution from holding a
loosening: "Loosening now costs a change to `policy.yml`, which goes through the same gated run
and the same review as any other change" (`policy.py:8-10`). The constitution already holds one
loosening list, `budget_exempt` (`policy.py:182-183`, `sdlc_templates/policy.yml:197-203`), which
the profile has no key for and which `budget` reads straight from the policy
(`policy.py:5265-5297`, the match at `:5280`).

**Where an org-set suppression would break it.** Two places, both answerable:

1. *If the profile could carry it.* Then a run could loosen review by editing the file that
   configures it, which is the exact failure the item exists to prevent (`policy.py:5-8`).
   **Condition 1: the key lives only in `policy.yml`.** `suppress_of` reads it from the policy,
   never from the profile.
2. *If it could silence a finding the gate weighs.* `block_on_floor` is "the severities that must
   ALWAYS block the review loop's exit" (`sdlc_templates/policy.yml:26-29`), and the effective
   `block_on` is the stricter of the constitution, the regime and the profile
   (`policy.py:1641-1645`, `:1666-1675`, `effective` at `:1737-1760`). Today `assess` removes a
   declared suppression before it looks at severity at all (`tools/review/loop.py:381-390`), so a
   repository-declared critical never holds the loop. An org list run through the same code would
   let one key of the constitution defeat another key of the same document, and would let the
   organisation loosen past a profile that tightened `block_on` - so a run could no longer "always
   be stricter than the constitution" (`policy.py:1614-1615`). **Condition 2: an org-set entry
   silences a finding occurrence only when that occurrence has a legal severity that is outside
   the run's resolved `block_on`.** At a `block_on` severity, or ungraded, it still fires, and
   the verdict says why.

**With both conditions, an org-set entry cannot change any review verdict.** A finding below the
`block_on` line never holds the loop: the open-rows loop skips it (`loop.py:450`), a rejected one
was split out (`:426-428`) and a deferred one is held only at a blocking severity (`:452-453`).
So removing such a finding changes `counts` and what the report shows, and nothing `may_exit`
reads (`loop.py:481-483`). That is a weaker loosening than `budget_exempt`, which does let a batch
pass a budget it would otherwise fail. It is the property the tests in section 4 pin as an
invariant over every combination of severity, disposition, source and pre-existing flag: for any
findings, `assess` raises exactly when it raised without the list, and `open`, `ungraded`,
`deferred_blockers` and `pre_existing` are identical.

**What does not change, stated.** The repository's own `review_suppress` keeps its present power:
it silences at any severity, ungraded included (`loop.py:381-390`;
`tools/review/tests/test_review_loop.py:843-848`). It is gated by the learning loop and made
visible by `learning --audit` (`tools/sdlc/learning.py:603-631`; `sdlc/stages/P6-review.md:118-128`).
S3 does not touch it. Section 6 notes it as an observation, not part of this row.

**If the owner does not sign conditions 1 and 2.** Revision 1's stop applies as written: S3 is
abandoned, not reshaped, and an organisation's suppression is delivered as one gated run per
repository that edits that repository's own `review_suppress` through the existing learning loop
(`learning.py:137-173`, applied under `corpus_gate` and a live G3, `:176-250`). Nothing public
changes in that case.

---

## 2. S3: the six design questions

### Q1. Direction: is an org-set list the same kind of thing as `budget_exempt`?

**Evidence.** `budget_exempt` is a policy-only list whose entries take work out of a measurement
(`policy.py:182-183`, `:5280`); the profile has no such key (`sdlc_templates/profile.yml` names
`review_suppress` alone, `:217`). Its strictness order is *subset*: a policy listing fewer
patterns is at least as strict. The two org floors run the other way: `required_phases` and
`required_gates` are stricter as *supersets* (`policy.py:195-203`, `:1682-1702`).

**Answer.** Yes, under condition 2, and only because of it. More names is looser; a list that is a
subset of another is at least as strict. Without condition 2 the two are not the same kind:
`budget_exempt` leaves every file in the diff in front of the reviewer, while a suppression would
remove a finding from the gate.

**Recommendation.** The key name is `org_review_suppress`. It reads beside the profile's
`review_suppress`, and "org" is the label the report prints. Its docstring and template comment
state the subset order, so anything that compares one repository's policy with an organisation's
reference treats it as `budget_exempt` is treated (within = subset), never as `required_gates`.
No profile key may un-suppress an org entry. The gate is unchanged, so there is nothing to tighten
past, and the strictness a profile can add (`block_on`) already overrides the list (Q2).

### Q2. The floors: an org-set entry must not defeat `block_on_floor`

**Evidence.** Suppression is judged first, by id alone (`loop.py:381-390`). The id carries no
severity, and one id can occur at different severities within one review (an agent or human
finding is graded per occurrence, `loop.py:346-353`; `hitl.py:150-168`). `assess` is given the
run's *effective* `block_on`, since `block_on_of` resolves profile, regime and floor together
(`loop.py:545-552`). An illegal severity is refused by `rank` (`:449`), and an unknown
disposition by the check at `:406-415`.

**Answer.** The decision is made per occurrence, inside `assess`, after the existing validation.

- The org filter runs **after** the disposition check (`:406-415`) and **before** the rejected
  split (`:426`). It applies to findings that are not pre-existing, and only to ids the
  repository's own list did not already silence.
- For each such occurrence named by the org list: ungraded keeps it (`why`: not shown to be
  outside `block_on`). Otherwise `rank(severity)` runs, so an illegal severity still raises as it
  does today, and a severity in `block_on` keeps it (`why`: severity is in `block_on`). Only
  then is it silenced.
- The comparison is against the **resolved** `block_on` that `assess` already receives, not
  `policy["block_on_floor"]` alone. A `regulated` run (adds `medium`, `policy.py:103-104`) and a
  profile that added a severity both keep their tightening, so the organisation cannot loosen past
  a run that tightened.
- Pre-existing findings are left alone (`:391-399`): they never gate, and never reach `rank`, so
  filtering them would add a refusal where the loop gave none.

**Recommendation.** As above. Held occurrences are listed with their `why` so a reader sees that
the organisation asked for silence and the floor refused it.

### Q3. Where a run can write it

**Evidence.** `**/policy.yml` is in the default `protected_paths` (`policy.py:134-136`), and
`protected` matches by path, both as written and in the Windows spelling
(`policy.py:3320-3331`). Both policy locations, `sdlc/policy.yml` and `.sdlc/policy.yml`
(`POLICY_LOCATIONS`, `policy.py:221`), are matched by that glob. The profile is deliberately not
protected (`learning.py:606-616`), which is why condition 1 matters.

**Answer.** A run cannot write the new key, because it cannot write the file. The key is exactly as
protected as `block_on_floor`. Removing `**/policy.yml` from `protected_paths` is itself a
change to `policy.yml`, which is gated. Nothing new is needed.

**Recommendation.** One test for each location: a run's write to the file holding the key is
refused by `protected`. One more test: a profile that carries `org_review_suppress` silences
nothing (condition 1), because `suppress_of` never reads that key from the profile.

### Q4. Visibility: org-set must read as org-set

**Evidence.** `assess` reports `suppressed`, one sorted list of ids with no origin
(`loop.py:388`, `:469`). `learning.unbacked` calls any declared suppression without a standing
audit row "written by hand" (`learning.py:603-631`), and `--audit` exits 1 on any
(`:656-662`). The loop's committed record (`iterate`, `loop.py:579-597`) and `render`
(`:629-660`) carry no suppression line at all (section 6).

**Answer.**

- `assess` keeps `suppressed` exactly as today (the repository's list). When the org list is
  non-empty it adds `org_suppressed` (sorted ids it silenced) and `org_suppress_held` (one row per
  occurrence it named but did not silence: `id`, `source`, `severity`, `why`). An id in both lists
  is reported once, under `suppressed`, because the repository's list is the one that silenced it.
- `iterate` writes the same two keys into the iteration entry, and `render` prints one line for
  each ("silenced by the organisation's policy (org_review_suppress): ..."; "org-set, still
  reported: X [high] - an org-set suppression never silences a finding at a block_on severity"),
  **only when the org list is non-empty**. An absent or empty key therefore leaves the verdict, the
  committed record and the printed text byte for byte as they are.
- `learning.unbacked` judges only the repository's entries. `--audit` prints the org-set entries on
  one informational line ("N org-set entr(ies) in policy.yml - set by the organisation, not
  learned here"), and its exit code is unchanged.

**Recommendation.** As above. The learning loop's `propose` is unchanged: an org-set id can still
be proposed for the repository's own list, which is a stronger act (it would silence at every
severity) and keeps its own gates.

### Q5. Mixed versions

**Evidence.** `validate` refuses any key outside `DEFAULTS` (`policy.py:536-540`), and `load`
raises on it (`:678-691`), so every door that loads the policy stops: `block_on_of` (`loop.py:549`),
`approve` (`tools/sdlc/approve.py:895-907`) and the guards. The schema is derived from `DEFAULTS`
with `additionalProperties: false` (`tools/sdlc/schema.py:173-211`). Every `DEFAULTS` key is in
the template, and the installer stages `.sdlc/policy.yml.new` when an installed file differs from
the template it would now write (`sdlc-init.py:2648-2671`; T712's upgrade note records the effect).

**Answer.**

- *Older harness, newer policy:* it refuses outright, by name ("unknown key(s):
  org_review_suppress"). That fails closed, never silently. But it means the policy holding the key
  and a harness that knows it must arrive together, harness first.
- *Newer harness, older policy:* the key is absent, the default is `[]`, and nothing changes.
- `SCHEMA_VERSION` stays 1. Raising it would make every newer harness refuse every existing policy,
  which costs more than the problem it would solve.

**Recommendation.** The row's upgrade note says: "a policy carrying `org_review_suppress` is
refused by a harness older than this row, so move the harness first". The template gains
`org_review_suppress: []` with its comment, following the every-key convention. The note also
records that the first installer run after the upgrade stages `.sdlc/policy.yml.new` once on an
existing install. The alternative, leaving the template unchanged as T712 did, would make this the
only `DEFAULTS` key the template does not document; it is offered for the owner to choose.

### Q6. One reader

**Evidence.** `suppress_of` is the one door (`loop.py:555-566`), with three callers: the loop's
`iterate` (`:576`), the non-iterate path (`:726`) and `learning.unbacked` (`learning.py:621`).
Six test assertions compare its result to a list (`tools/tests/test_learning.py:462`, `:469`,
`:1176`, `:1222`, `:1228`). The policy has exactly one parser and validator: `policy.load`
(parse, PyYAML cross-check, validate).

**Answer.** `suppress_of(target)` returns a frozen, **non-iterable** `Suppressions(repository:
tuple[str, ...], org: tuple[str, ...])`. `repository` comes from `profile_list(target,
"review_suppress")` as today. `org` comes from `guardrails.load(target)["org_review_suppress"]`,
the one parser, never a second read of the file. Because the value cannot be iterated, a caller
that treats it as a list fails loudly (`TypeError`) instead of silently merging the two lists. A
merged list would bypass condition 2. `assess(findings, block_on, suppress=None)` accepts a
`Suppressions` or, as today, a plain list meaning repository entries only, so the roughly 35
existing `assess(..., suppress=[...])` calls stand. The union is made in one place, inside
`assess`. A malformed list is a `PolicyError`, which the loop already turns into exit 2
(`loop.py:737`).

**Recommendation.** As above. The six list assertions change to `.repository`, by design, and the
row says so.

---

## 3. S3: what the row changes

- `tools/sdlc/policy.py`: `DEFAULTS["org_review_suppress"] = []` with its comment (the subset
  order; policy-only; never silences at a `block_on` severity). `validate` requires a list of
  strings, each non-empty after stripping. It refuses glob characters (`*`, `?`, `[`), because
  matching is exact and a pattern would only look like a wildcard. It refuses `(unnamed)`, the
  placeholder the loop gives every finding with no id (`loop.py:346`, `:289`), which as an
  org-wide entry would silence every unnamed finding. Duplicates are read once and not refused, as
  in `review_suppress`.
- `tools/review/loop.py`: `Suppressions`, `suppress_of`, the filter in `assess`, the two
  conditional keys in `iterate`'s entry, and `render`'s lines.
- `tools/sdlc/learning.py`: `unbacked` reads `.repository`, and `--audit` prints the org-set line.
- `sdlc_templates/policy.yml`: the key and its comment. `sdlc_templates/schemas/policy.schema.json`
  is regenerated by `schema.py`.
- `sdlc/LIFECYCLE.md` (beside "Org floors") and `sdlc/stages/P6-review.md` step 7: one paragraph
  each.
- Out of scope: wildcards, any change to `review_suppress` or how a profile declares it, how the
  list is written, and any comparison tool.

## 4. S3: tests first, and the mutations they kill

Each test is run red against `origin/main` before the code is written.

| # | Test (file) | Red on main because |
|---|---|---|
| 1 | Absent key and `[]`: the verdict JSON, the committed loop record and the rendered text are byte-identical to a golden captured on main (`tools/review/tests/test_review_loop.py`) | golden captured first; it pins that nothing appears |
| 2 | An org-listed id at `low` (block_on `[critical, high]`) is silenced, listed in `org_suppressed`, absent from `counts`, absent from `suppressed` (same) | key unknown, refused |
| 3 | The same id at `high` in the same review is not silenced, is open, and appears in `org_suppress_held` with `why` naming `block_on` | same |
| 4 | An ungraded org-listed finding still holds the loop as `ungraded`, held with its `why` (contrast `test_review_loop.py:843-848` for the repository's list) | same |
| 5 | A `regulated` run and a profile that adds `medium`: an org-listed `medium` finding is held | same |
| 6 | Invariant, every combination of severity (each legal one, blank), disposition (each known one), source (each of `hitl.SOURCES`) and pre-existing flag: with and without the org list, `assess` raises alike, and `open`, `ungraded`, `deferred_blockers`, `pre_existing` and `may_exit` are equal | same |
| 7 | An illegal severity or unknown disposition on an org-listed finding raises `LoopError` exactly as without the list | same |
| 8 | An id in both lists is reported once, under `suppressed`, and silenced at any severity (the repository's power unchanged) | same |
| 9 | A profile carrying `org_review_suppress` silences nothing | the profile value is ignored today too, so this is proved by mutation M5, as CLAUDE.md requires of a test that passes on main |
| 10 | `suppress_of` returns `Suppressions`, `.org` read from both policy locations; iterating it raises `TypeError` | the function returns a list |
| 11 | `validate` refuses a non-list, a blank entry, `(unnamed)`, `*` and `a?b`, each by name; `policy.py --validate` (spawned) knows the key; the `PolicyModel` second opinion (`tools/tests/test_policy.py:72`, `:232-248`) learns it | unknown key |
| 12 | `learning --audit` (spawned) does not call an org-set entry hand-written, prints the org-set line, and keeps its exit code | entry unknown |
| 13 | `loop.py --iterate` (spawned): the org lines appear in the output and in the record only when the list is non-empty | same |
| 14 | `protected` refuses a run's write to `sdlc/policy.yml` and `.sdlc/policy.yml` | passes on main, so it guards against a later narrowing of `protected_paths`' default; mutation M7 |

**Mutations (each must turn a test above red):**
M1 silence before the floor check, i.e. merge the org list into the repository's list (tests 3-6).
M2 compare against `policy["block_on_floor"]` instead of the resolved `block_on` (test 5).
M3 decide per id rather than per occurrence (test 3).
M4 report an org-set entry under `suppressed` (tests 2, 12).
M5 read the key from the profile too (test 9).
M6 let `[""]` or `(unnamed)` through `validate` (test 11).
M7 drop `**/policy.yml` from the default `protected_paths` (test 14).
M8 always emit the new keys (test 1).
M9 run the org filter before the disposition check (test 7).

## 5. S4: `feedback.review_dispositions`

**Evidence.** Accepted dispositions live only in `.sdlc/metrics/reviews.jsonl`, one `kind:
"review"` row per closed session, written by `hitl.end` with `run` when the session was opened
with `--run` (`tools/review/hitl.py:170-199`, the run at `:191-192`). Each finding carries `id`,
`source`, `disposition`, and optionally `severity`, `path` and `line` (`:150-168`). The only
public reader, `review_corrections`, keeps corrections alone (`tools/sdlc/feedback.py:321-322`,
`CORRECTIONS` at `:88`) and returns `path`, `severity` and `component` (`:323-330`). Its damage
rules are the house rules (`:289-320`; `tools/tests/test_feedback.py:994-1060`).

### API

```python
def review_dispositions(target: Path, run_id: str) -> tuple[list[dict], dict]:
    """(rows, totals) - every finding recorded in this run's closed review sessions."""
```

- **rows**: in file order and then finding order, one per recorded occurrence, each exactly
  `{"finding": str, "source": str, "disposition": str}`. All three of `hitl.DISPOSITIONS`
  (`accepted`, `rejected`, `deferred`, `hitl.py:45`) are included. Occurrences are not
  de-duplicated; a consumer counts distinct runs.
- **totals**: `{"sessions": n, "findings": n, "unreadable": n}`, where `findings == len(rows)`.
- **Which rows count**: `kind == "review"` and `run == run_id`. `review-abandoned` rows
  (`hitl.py:201-228`), other runs' rows, rows without `run`, and the open session file are neither
  read nor counted.
- **Damage is counted as `unreadable` and never skipped silently**: a line that is not JSON or not
  an object (`cli.jsonl_rows`); this run's review row whose `findings` is not a list; a finding
  that is not an object; a finding whose `id` or `source` is not a non-empty string, or whose
  `disposition` is not in `hitl.DISPOSITIONS`. The last of these is deliberately stricter than
  `review_corrections`, which yields `"?"`: a reader that exists to be counted must not hand over
  an id nobody recorded. A damaged *line* cannot be attributed to a run, so, exactly as
  `review_corrections` does, it is counted on every run's read. A consumer must not sum
  `unreadable` across runs, and the docstring says so.
- **Values are returned as recorded.** Any form check on an id (for example, refusing one shaped
  like a path) belongs to the consumer.
- **One walker.** A private `_review_sessions(target, run_id, totals)` holds the shared walk and
  damage counting, and both public readers use it. `review_corrections`' output must not move by a
  byte (test 9 below).
- No CLI flag. The reader is a library function, and the row changes nothing else.

**A consequence to state, not hide.** S4 carries no severity, by its scope. A consumer that uses
it to measure whether an org-set entry would silence an accepted finding therefore cannot apply
S3's floor rule (condition 2). It will count an accepted occurrence at a `block_on` severity as
silenced, although S3 would let it fire. Such a measurement is an upper bound: it can over-report
regression, never under-report it. That is the safe direction. Closing the gap would mean adding
severity, a scope change for the owner, not a design choice here.

### Tests first (`tools/tests/test_feedback.py`; not `tools/sdlc/tests/`, which does not exist)

| # | Test | Red on main because |
|---|---|---|
| 1 | A session with one accepted, one rejected and one deferred finding yields three rows, in order, with those dispositions | the function does not exist |
| 2 | Each row's key set is exactly `{finding, source, disposition}`; canary values in `path`, `severity`, `line`, `component` and `started_at` appear nowhere in `repr(rows, totals)` | same |
| 3 | Another run's session, a run-less session and a `review-abandoned` row for this run yield nothing and are not sessions | same |
| 4 | A run with no `reviews.jsonl` and a run with no session both yield `([], {sessions: 0, findings: 0, unreadable: 0})` | same |
| 5 | Each damage shape (non-JSON line, non-object line, null `findings`, non-object finding, blank `id`, missing `source`, disposition `"acepted"`) adds 1 to `unreadable`, and a good finding after it is still yielded | same |
| 6 | Two occurrences of one id in one session are two rows | same |
| 7 | An intact record reports `unreadable == 0` (the negative control, as `test_feedback.py:1047-1060`) | same |
| 8 | A record written by real `hitl.begin(run=...)`, `finding` and `end` calls round-trips, so the fixture is not hand-shaped only | same |
| 9 | Characterisation: `review_corrections` on a fixture with every shape above returns what it returns on main (golden captured before the refactor) | passes on main; mutation M6 |

**Mutations:**
M1 include `path` (or any fourth key) (test 2).
M2 drop accepted rows by filtering on `CORRECTIONS` (test 1).
M3 fold damage into "none", i.e. `continue` without counting (test 5).
M4 drop the run filter (test 3).
M5 accept any `kind` beginning with `review` (test 3).
M6 change `review_corrections` while sharing the walker, e.g. apply the stricter id rule to it
(test 9).
M7 de-duplicate occurrences (test 6).
M8 raise on the first damaged line (test 5).

---

## 6. Found, not in scope (for the owner to file or not)

- **The repository's own suppressions are not in the committed loop record or the rendered text.**
  `iterate`'s entry (`loop.py:579-597`) and `render` (`:629-660`) carry no `suppressed`, and
  `--iterate --json` prints the record, so what was silenced is visible only through
  `loop.py --json` without `--iterate`. That is weaker than step 7's "COUNTED in the loop's
  verdict" (`P6-review.md:122-124`). S3 adds the org lines, conditionally, and leaves this as it
  is; adding the repository's lines would change the record for every adopter that has a
  suppression, which is a decision of its own.
- **`review_suppress` accepts `(unnamed)`**, which silences every finding without an id. S3's
  validation refuses it for the org key only.

---

## Appendix: draft backlog rows (not filed; next free numbers today)

| T716 | P3 | Open | **An organisation cannot suppress a finding id across its repositories' reviews without editing each repository's profile.** A suppression exists only as a repository's own `review_suppress` (`.sdlc/profile.yml`), written by the learning loop one repository at a time, so a finding an organisation has judged noise everywhere fires in every repository until each one learns it separately. **Fix:** `policy.yml` gains `org_review_suppress`, a list of finding ids (default empty), read with `review_suppress` through `suppress_of` and reported as org-set; an org-set entry never silences a finding at a `block_on` severity or an ungraded one, so it can change what a review reports and never whether it may exit. Design: `docs/design/2026-11-14-s3-s4-org-suppressions.md`, awaiting the owner's sign-off (its stop condition: tighten-only holds only while the key is policy-only and the floor rule stands; otherwise the row is abandoned and an organisation's suppression ships as one gated run per repository through the learning loop). Scope: `policy.DEFAULTS` and `validate` (a list of exact ids; blanks, glob characters and `(unnamed)` refused by name), `suppress_of` returning both lists, the per-occurrence filter in `assess` after its validation and against the resolved `block_on`, `org_suppressed` and `org_suppress_held` in the verdict, the loop record and `render` only when the list is non-empty, `learning --audit` naming org-set entries apart from hand-written ones, the template, the regenerated schema, `LIFECYCLE.md` and `P6-review.md`. Out of scope: wildcards, any change to `review_suppress`, how the list is written. Upgrade notes: a policy carrying the key is refused by an older harness, so move the harness first; an absent key changes no output; the template changes, so the first installer run stages `.sdlc/policy.yml.new` once; `suppress_of` returns a `Suppressions` value, not a list. |

| T717 | P4 | Open | **No public reader yields a run's review dispositions without paths, and none yields accepted findings at all.** `feedback.review_corrections` keeps only rejected and deferred findings and returns each one's `path`, `severity` and `component`, so a caller that needs to count how often a finding was accepted, per run and without location data, has to parse `reviews.jsonl` itself. **Fix:** `feedback.review_dispositions(target, run_id)` returns `(rows, totals)`: one `{finding, source, disposition}` per finding recorded in that run's closed review sessions, all three dispositions included, never a path, severity or line; damage is counted as `unreadable`, never skipped; one walker shared with `review_corrections`, whose output does not move. Design: `docs/design/2026-11-14-s3-s4-org-suppressions.md` section 5, awaiting the owner's sign-off. Out of scope: a CLI, any form check on ids (the consumer's), severity (a measurement built on this reader is an upper bound on what T716's entries would silence, stated). Upgrade note: a new function; nothing existing changes. |
