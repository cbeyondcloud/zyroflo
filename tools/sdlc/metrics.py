"""The metrics engine — item 52: what the records already know, counted once.

Every number here comes from a record some gate already writes: run state,
the approval ledger, review sessions, the learning audit, the debt register.
Nothing is instrumented for this module, because a metric with its own
collection path is a second account of the same events that can disagree with
the first.

**No duckdb, and the reason is measured rather than preferred.** The task line
names it; the real data on a live repository is **53 JSONL rows totalling 65 KB
across four runs**. Scale a mature adopter a thousandfold and it is still tens
of thousands of rows read once — `json.loads` in a loop, not a query engine. The
structural half matters more: item 62's zero-dependency parity gate asserts
single-repo mode completes with NO third-party packages, enforced by an import
gate that refuses them, so a metrics engine on duckdb would be dead in exactly
the configuration this harness promises to work in. Item 11 was specified the
same way and reached the same answer — the 8th measure-first correction — and
item 50 met the shape again with `deepeval` days ago. This is the third time,
and doing it a third way would be the inconsistency.

**A metric nobody can compute is NEVER zero.** This is the whole of the design
and it is where a metrics document usually lies. *Deployment frequency: 0* reads
as "we deploy nothing"; the truth here is "this harness has never deployed, and
P9/P10 are not built". Those are different sentences and only one of them is
true, so an unmeasurable metric carries `value: null` and a REASON, and the
document ends with a roll-up of every one of them. A reader sees what it does
not know before deciding what to trust.

**DORA is reported as four nulls, on purpose.** All four of its measures are
defined against production — deployments, failed deployments, time to restore
service — and nothing here has ever reached production. `time_to_merge_ready`
is offered beside them and is deliberately NOT called lead time: DORA's lead
time runs from commit to RUNNING IN PRODUCTION, and renaming a merge-readiness
proxy after it would be measuring the wrong subject and reporting it under the
right name. The M4 ruling on C8 is the same lesson from the other end.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: Written beside the other records, in the adopter's tree.
RESULT = Path(".sdlc") / "metrics" / "metrics.json"

#: DORA's four, each defined against PRODUCTION. Named here so the document
#: cannot quietly report three of them and imply the fourth.
DORA = ("deployment_frequency", "lead_time_for_changes",
        "change_failure_rate", "time_to_restore_service")


class MetricsError(RuntimeError):
    """The records cannot be read, which is not the same as no activity."""


def value(v) -> dict:
    return {"value": v}


def cannot(why: str) -> dict:
    """A metric that could not be computed, and why.

    `value` stays None rather than 0 so no caller can average it, sum it, or
    render it on a chart as a floor. The reason travels with it because a null
    without one is indistinguishable from a bug in this module - ENFORCED
    here rather than merely stated in prose: a mutant that swapped a real
    call's reason for "" passed the whole suite and left `unmeasured()`'s
    roll-up rendering a bare label with nothing after the colon.
    """
    if not why or not why.strip():
        raise ValueError("cannot() needs a non-empty reason - a null without "
                         "one is indistinguishable from a bug in this module")
    return {"value": None, "why": why}


def _finite_number(x) -> bool:
    """Whether `x` is safe to use as a real, finite number - not merely
    shaped like one.

    ROUND 8 (the fourteenth - the most reachable defect on this row, and it
    needed no stub): `math.isfinite()` converts its argument to a C double
    internally, and RAISES `OverflowError` on a Python integer too large to
    fit - which this record's own JSONL format can carry in an ordinary,
    hand-writable row (`duration_s`), and which a foreign module can compute
    just as easily (`age_days`). An `int` is finite by construction - this
    language has no integer NaN or Infinity - so it is never handed to
    `math.isfinite` at all; only a genuine `float` is. A `bool` is excluded
    everywhere this is used: never a number, despite being an `int` subclass.
    """
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    return isinstance(x, float) and math.isfinite(x)


def _computable_number(x) -> bool:
    """Whether `x` is not just finite but SAFE TO COMPUTE WITH.

    ROUND 9 (the fifteenth): `_finite_number` correctly treats every `int`
    as finite - this language has no integer NaN or Infinity - but its
    CALLERS need more than "not NaN". `statistics.median()` averages the two
    middle values of an even-length list, and that division overflows
    converting an astronomically large Python int to a C double - the exact
    failure `_finite_number` was built to prevent, arriving one function
    later instead of being removed. For an ODD-length list the same huge
    value needs no division and is returned unchanged, landing an
    enormous number - hundreds of characters long - directly in the
    published, diffed record with no bound on it at all - round 8 bounded
    the REASON text and not the VALUE.

    A SEPARATE, STRICTER check from `_finite_number` rather than a
    replacement: the disposition-count leaf only ever ADDS ints together
    (which never overflows, arbitrary precision) and is confirmed safe with
    the looser check, so it keeps using that one. Anywhere a value may be
    divided, averaged, or otherwise pushed through float arithmetic - or
    published as a value with no length bound at all - needs this one.
    """
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except OverflowError:
        return False


def _when(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _field(record: dict, key: str, damage: dict, record_channel: str) -> str:
    """A record's own label field - `kind`, `status`, `phase`, `gate`,
    `verdict` - distinguishing ABSENT (the key was never written) from
    PRESENT AND NULL (the key was written empty). T418: `record.get(key,
    "?")`'s default only fires on the former; a hand-edited record can carry
    the key with a JSON `null`, which walks straight past it, and `str(None)`
    is `"None"` - the four-letter word - stringified into a tally key that
    then reads exactly like a real answer, with `damage` staying empty
    because nothing raised. An absent field stays the existing `"?"`
    sentinel, unchanged and un-damaged - this is not about missing data, only
    about a key that is there and empty. A present null is `"?"` too (so the
    published tally never grows a new, uglier token).

    ROUND 2 (review, T418): a present-null FIELD is not the same fact as an
    UNREADABLE RECORD, and the first cut counted both on the bare `"runs"` /
    `"approvals"` channel `run_states()`/`_rows()` already use for a whole
    excluded run or row - so one run with three hand-edited null fields
    (included, total unaffected) and three run folders with no `state.json`
    at all (excluded, total down by three) produced the IDENTICAL
    `unreadable` dict and the whole document compared equal, which is
    exactly the ambiguity `unreadable`'s own CLI text ("unreadable rows")
    promises never happens.

    ROUND 3 (review note, T418): round 2 fixed the COLLISION but not its
    cause - the compound shape `"<record-channel>.<field>"` was a
    convention the five call sites happened to follow, composed AT the call
    site and merely enumerated by the tests, so a sixth site could still
    pass a bare `"runs"` straight through and silently reopen the identical
    collapse, with nothing failing. `record_channel` is now only ever the
    BASE name (`"runs"`, `"approvals"`) - the same one `run_states()`/
    `_rows()` already use for a whole excluded record - and this function
    alone composes `f"{record_channel}.{key}"`. A caller cannot produce the
    collapsed, bare shape any more; it no longer has a parameter to put it
    in.
    """
    if key in record and record[key] is None:
        channel = f"{record_channel}.{key}"
        damage[channel] = damage.get(channel, 0) + 1
        return "?"
    return str(record.get(key, "?"))


def _rows(path: Path, counter: dict, label: str) -> list[dict]:
    """Every readable object in a JSONL record; damage is COUNTED, not dropped.

    **RULING (round 4, corrected round 5): a count, not a per-row identity.**
    `damage`/`doc["unreadable"]` names the CHANNEL (`"runs"`, `"reviews"`,
    ...) and how many rows on it were unreadable, never which row, which key,
    or what was in it. Deliberate, not an oversight, for the same reason
    item 52's own docstring gives for reading this file with plain
    `json.loads` over a query engine: the real data on a live repository is
    53 JSONL rows across four runs, tens of thousands at the scale this
    module already reasons about being fine for - and at that scale the
    record is grepped open, not searched, because the FILE is already named
    by the caller and it is the adopter's own file on their own disk, not a
    black box. This scale argument is independently true and sufficient on
    its own to justify a count.
    (CORRECTED: this docstring previously also claimed that widening the
    record would break an existing reader that compares `doc["unreadable"]`'s
    values to an int - reviewed and no such reader was found; every other use
    of that key is an unrelated counter in a different module, and the CLI's
    own text path just prints the whole mapping. That claim is removed rather
    than left as an unsupported reason beside a correct one.) The argument
    that IS true instead: `RESULT` (`.sdlc/metrics/metrics.json`) is written
    to the adopter's tree as a durable, published record, and this module
    does not control who else reads it - a schema change is a compatibility
    break whether or not TODAY's codebase has a reader that would notice.
    If the scale argument ever stops holding, this is the one place to widen
    - a count strung across three call sites, not three designs.

    **Both approaches coexist on purpose.** The row-level damage counted here
    (and by the leaf guards in `reviews()`, `run_states()`) is a COUNT, for
    the reason above; a single-metric `cannot()` failure with one value
    directly at hand (`commits()`'s non-numeric stdout, the tenth's non-finite
    `age_days`) DOES name the rejected value in its reason string, because
    naming it there costs nothing extra and there is exactly one candidate to
    name - the two are different problems at different scales, not an
    inconsistency.
    """
    if not path.is_file():
        return []
    out = []
    for line in cli.jsonl_rows(path):
        if line.row is None:
            counter[label] = counter.get(label, 0) + 1
            continue
        out.append(line.row)
    return out


def run_states(target: Path) -> tuple[list[dict], dict]:
    """Every run's state, and what could not be read.

    Folders rather than `state.list_runs`, for item 44's reason: a sweep that
    dies on one malformed state.json reports nothing about the other fifty.
    """
    target = Path(target)
    base = ledger.runs_dir(target)
    out, damage = [], {}
    for where in (base, ledger.archive_dir(target)):
        if not where.is_dir():
            continue
        for entry in sorted(p for p in where.iterdir() if p.is_dir()):
            if entry.name == ledger.ARCHIVE_NAME:
                continue
            path = entry / ledger.STATE_NAME
            if not path.is_file():
                damage["runs"] = damage.get("runs", 0) + 1
                continue
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                damage["runs"] = damage.get("runs", 0) + 1
                continue
            # Parses fine and is not a mapping - a bare `null`, a list, a number
            # - is damage `json.loads` cannot raise on. Undetected here it
            # reaches `st.get(...)` in every reader below (`runs`, `gates`,
            # `time_to_merge_ready`, `dora`) and crashes there instead, which is
            # exactly the "dies on one malformed state.json" this function
            # exists to refuse.
            if not isinstance(parsed, dict):
                damage["runs"] = damage.get("runs", 0) + 1
                continue
            out.append(parsed)
    return out, damage


def runs(states: list[dict], damage: dict) -> dict:
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    for st in states:
        # T418: `str(st.get("kind", "?"))` reads clean but the default cannot
        # fire on a present null - `_field` is the same guard `gates()` below
        # now shares for `gate`/`verdict`. "runs" is the RECORD channel -
        # `_field` itself composes "runs.<field>", the same base
        # `run_states()` uses for a whole excluded run, so a present-null
        # FIELD on a record still counted in `total` can never collapse onto
        # that whole-record channel the way round 2's review measured.
        kind = _field(st, "kind", damage, "runs")
        status = _field(st, "status", damage, "runs")
        phase = _field(st, "phase", damage, "runs")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_phase[phase] = by_phase.get(phase, 0) + 1
    closed = by_status.get("closed", 0)
    return {"total": value(len(states)), "by_kind": value(dict(sorted(by_kind.items()))),
            "by_status": value(dict(sorted(by_status.items()))),
            "by_phase": value(dict(sorted(by_phase.items()))),
            "open": value(len(states) - closed - by_status.get("rejected", 0)),
            "closed": value(closed)}


def gates(target: Path, states: list[dict], damage: dict) -> dict:
    """Approvals, and the two things a bare count would hide: overrides, and
    gates that had to be taken more than once."""
    from tools.sdlc import approve

    per_gate: dict[str, int] = {}
    verdicts: dict[str, int] = {}
    forced = 0
    retaken: dict[str, int] = {}
    for st in states:
        rid = str(st.get("run_id") or "")
        if not rid:
            continue
        seen: dict[str, int] = {}
        for row in _rows(ledger.run_dir(Path(target), rid) / approve.APPROVALS,
                         damage, "approvals"):
            # T418: `gate`/`verdict` share the identical present-null shape
            # `runs()` above now guards with `_field`. "approvals" is the
            # RECORD channel, same reasoning as `runs()`'s comment above -
            # `_field` composes the field onto it, so no call site can pass
            # the collapsed, bare shape.
            gate = _field(row, "gate", damage, "approvals")
            per_gate[gate] = per_gate.get(gate, 0) + 1
            verdict = _field(row, "verdict", damage, "approvals")
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if row.get("forced"):
                forced += 1
            seen[gate] = seen.get(gate, 0) + 1
        for gate, n in seen.items():
            if n > 1:
                retaken[gate] = retaken.get(gate, 0) + n - 1
    # A forced ADVANCE is an override too, and it is not in this ledger - it
    # lives in the run's history, because `state.advance` records the move and
    # `stage.advance` is the layer with the opinion. Counting only forced
    # APPROVALS reported `0 forced` for a session that had forced a phase past
    # 103 open supply-chain findings: the exact shape of false assurance this
    # module exists to refuse, produced by this module, on its first real run.
    #
    # Item 112: the FIELD is the answer, matching how a forced approval is
    # recorded, so this asks one question instead of matching a sentence. The
    # note match stays for entries written BEFORE the field existed - a run
    # folder is a durable record and an adopter's history does not migrate
    # itself - and `or` rather than two counters, because one override recorded
    # both ways is one override.
    from tools.sdlc import stage as stages

    forced_advances = 0
    for st in states:
        for step in st.get("history") or []:
            if step.get("forced") or stages.FORCED_NOTE in str(step.get("note", "")):
                forced_advances += 1
    return {
        "approvals": value(sum(per_gate.values())),
        "forced_advances": value(forced_advances),
        "by_gate": value(dict(sorted(per_gate.items()))),
        "by_verdict": value(dict(sorted(verdicts.items()))),
        # An override is a gate somebody walked past on purpose. Counting it
        # beside the approvals is the only way a reader can tell a clean record
        # from one that was forced through.
        "forced": value(forced),
        # A gate taken twice is a gate that went VOID and was re-taken - the
        # P6-to-P4 loop, or an artifact edited after approval. It is a sign of
        # the machinery working, and invisible in a total.
        "re_taken": value(dict(sorted(retaken.items()))),
    }


def reviews(target: Path, damage: dict) -> dict:
    from tools.review import hitl

    path, _ = hitl.paths(Path(target))
    rows = _rows(path, damage, "reviews")
    done = [r for r in rows if r.get("kind") == "review"]
    abandoned = [r for r in rows if r.get("kind") == "review-abandoned"]
    # Found in the round-3 re-sweep, the same shape as the disposition-count
    # leaf below: TYPE is not enough. NaN and both infinities are valid JSON
    # and are floats, so they passed a bare `isinstance(x, (int, float))` -
    # neither `statistics.median` nor `round()` raises on them, and
    # `json.dumps` serialises the result as a bare `NaN`/`Infinity` token by
    # default, so a non-finite duration would not crash; it would reach the
    # RENDERED document disguised as a real median, past the `is None` check
    # every reader of this document relies on to find a gap. A fractional
    # duration is a legitimate answer ("12.5 seconds"), unlike a disposition
    # COUNT, so only finiteness is required here - not integer-valued.
    #
    # ROUND 9 (the fifteenth, and the ninth shape - a correct rejection that
    # left no trace): `_finite_number` alone still let an astronomically
    # large but finite int through - `statistics.median` divides the two
    # middle values of an even-length list, which overflows converting one
    # to a float, and for an odd-length list returns the huge value
    # UNCHANGED, publishing an enormous number - hundreds of characters
    # long - with no bound at all. `_computable_number` closes both. And
    # a PRESENT duration that
    # fails it is now DAMAGE on the reviews channel, exactly like every
    # sibling guard in this function - before this, filtering it out of
    # `seconds` was silent: the damage record stayed empty and the roll-up
    # said "no session recorded a duration", indistinguishable from a
    # section where nobody ever tried. An ABSENT duration (most rows, and
    # every abandoned review) stays silent, unchanged - only PRESENT and
    # unusable is damage.
    seconds = []
    for r in done:
        if "duration_s" not in r:
            continue
        d = r["duration_s"]
        if _computable_number(d):
            seconds.append(d)
        else:
            damage["reviews"] = damage.get("reviews", 0) + 1
    disp: dict[str, int] = {}
    for r in done:
        # A PRESENT `counts: null` walks past `.get("counts", {})`'s default -
        # the key IS there - so a chained `.get("by_disposition")` on it would
        # raise AttributeError and take every other metric in the document
        # down with it. Absent stays silent (the default `{}` is itself a dict
        # and flows through). Present-and-wrong-shape is damage, counted on
        # the SAME "reviews" channel `_rows` already uses for this section -
        # at every level this loop touches: `counts` itself, `by_disposition`,
        # and each individual disposition's own count below - so one row's
        # corruption, however deep, costs a disposition tally rather than the
        # whole document.
        counts = r.get("counts", {})
        if not isinstance(counts, dict):
            damage["reviews"] = damage.get("reviews", 0) + 1
            continue
        by_disposition = counts.get("by_disposition", {})
        if not isinstance(by_disposition, dict):
            damage["reviews"] = damage.get("reviews", 0) + 1
            continue
        for name, n in by_disposition.items():
            # The leaf: a disposition's own count can itself be present and
            # wrong-shaped even when the two mappings above it are fine - and
            # TYPE is not enough to prove it is usable. `null` is not an
            # `(int, float)` and was caught by round 2; NaN and both
            # infinities ARE valid JSON and ARE floats, so they passed that
            # check and died in `int()` anyway - NaN with `ValueError`,
            # infinity with `OverflowError`, which is an `ArithmeticError`
            # and not a `ValueError`, so the instinctive except would have
            # missed it too. A count is a WHOLE number: never a `bool` (an
            # `int` subtype in this language, so `True`/`False` would
            # otherwise silently tally as 1/0, and a flag is never a tally);
            # an `int`; or a FINITE, integer-valued `float` - a fractional
            # value like `2.7` would otherwise truncate to `2` with no sign
            # anything was wrong, the same present-wrong-shape defect one
            # step quieter than a crash. Both exclusions are decisions, not
            # incidental - a disposition count is a tally of reviews, and
            # none of a flag, a fraction, or an unrepresentable magnitude is
            # ever a legitimate answer to "how many".
            #
            # A NEGATIVE int (e.g. -5) is deliberately left UNGUARDED and
            # accepted as-is. Not because the writer-trace above cleared it -
            # that trace examined booleans and fractions only, never
            # negatives, and its silence on the question is not evidence
            # either way. Reviewed separately and confirmed correct on its
            # own merits: a negative count is not one of the shapes this
            # guard exists for (it does not crash, does not walk a present
            # null past a default, and does not reach a renderer as a
            # non-finite literal), so it stays out of scope for this row.
            # `_finite_number` (round 8): this branch was already safe against
            # the fourteenth (its own isfinite call sits inside the FLOAT arm,
            # never reached for an int), but shares the same helper now
            # rather than a third hand-written copy of the same reasoning.
            is_count = _finite_number(n) and (isinstance(n, int) or n.is_integer())
            if not is_count:
                damage["reviews"] = damage.get("reviews", 0) + 1
                continue
            disp[str(name)] = disp.get(str(name), 0) + int(n)
    return {
        "sessions": value(len(done)),
        # Abandoned reviews are rows, never deletions - dropping them would bias
        # every duration here toward the reviews that went well.
        "abandoned": value(len(abandoned)),
        "median_duration_s": (value(round(statistics.median(seconds), 1))
                              if seconds else
                              cannot("no completed review session records a "
                                     "duration, so there is no median to take")),
        "findings_by_disposition": value(dict(sorted(disp.items()))),
    }


def learning(target: Path, damage: dict) -> dict:
    from tools.sdlc import learning as learn

    rows = _rows(Path(target) / learn.LOG, damage, "learning")
    applied = [r for r in rows if r.get("action") == "applied"]
    undone = [r for r in rows if r.get("action") == "undone"]
    # Standing is the LAST disposition of each rule in EVENT ORDER, never a set
    # difference. A rule applied, undone, then re-applied is in BOTH the applied
    # and undone sets, so subtracting them cancels a rule that is in force back
    # to absent (measured: applied 2, undone 1, standing 0, where 1 is right).
    # `learning.standing` already walks the log forward per proposal and returns
    # the live applied row or None, so asking it here keeps the two readers of
    # "is this rule in force" from drifting into two answers.
    ids = {r.get("proposal") for r in applied}
    standing = [pid for pid in ids if learn.standing(rows, pid) is not None]
    return {"applied": value(len(applied)), "undone": value(len(undone)),
            "standing": value(len(standing))}


def debt(target: Path) -> dict:
    from tools.sdlc import debt as owed

    try:
        every = owed.candidates(Path(target))
        outstanding = owed.outstanding(Path(target))
    except owed.DebtError as exc:
        # RULING (round 3): `cannot()` now raises on an empty reason (round
        # 2's fix for the mutant that swapped one out), and `str(exc)` here is
        # text `tools.sdlc.debt` authored, not this module - two of its raise
        # sites (`debt.py:263`, `:349`) pass a bare value with no guaranteed
        # prefix, so an upstream exception with an empty message would reach
        # `cannot()` empty and crash the WHOLE document. Nothing produces one
        # today (latent, per review), but the constructor that exists so a
        # gap in ONE metric cannot take down the rest must not become the
        # thing a blank upstream message turns into exactly that. Decision:
        # `cannot()` stays strict - an empty reason authored BY THIS MODULE is
        # a real bug and must be loud - and the two callers that forward
        # foreign text (here, and `corpus()` below) guarantee their own
        # non-empty fallback instead of trusting what they were handed.
        #
        # ROUND 4: `x or fallback` and `cannot()`'s own guard (`not why or not
        # why.strip()`) used two DIFFERENT definitions of "empty" - `or` only
        # fires on a falsy string, and a string of spaces is truthy, so
        # whitespace-only text passed this line and then hit `cannot()`'s
        # STRICTER check and raised, uncaught, in the very call meant to
        # prevent exactly that. `.strip()` first makes both readers agree.
        why = str(exc).strip() or (f"the debt register could not be read and "
                                   f"the underlying error ({type(exc).__name__}) "
                                   f"carried no message")
        return {"declared": cannot(why), "outstanding": cannot(why)}
    by_source: dict[str, int] = {}
    for item in every:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    return {"declared": value(len(every)),
            "by_source": value(dict(sorted(by_source.items()))),
            "outstanding": value(len(outstanding))}


def deploys_recorded(target: Path, states: list[dict]) -> int:
    """How many runs reached a deploy phase. The premise every DORA measure
    rests on, computed rather than assumed absent."""
    reached = 0
    for st in states:
        for step in st.get("history") or []:
            if str(step.get("phase", "")) in ("P9", "P10"):
                reached += 1
                break
    return reached


def dora(target: Path, states: list[dict]) -> dict:
    """DORA's four, and why each is null here.

    **Every one is defined against PRODUCTION** — deployments, failed
    deployments, time to restore service. Reporting `0` for any of them would
    say the harness deploys and fails at nothing, when the truth is that it has
    never deployed. Computed from the run history rather than asserted: if a run
    ever reaches P9 or P10, this section stops being null and starts being work.
    """
    reached = deploys_recorded(Path(target), states)
    if not reached:
        why = ("no run has reached a deploy phase (P9/P10), so nothing here has "
               "been deployed - DORA's four measures are all defined against "
               "production and none of them is zero, they are unmeasured")
        return {**{name: cannot(why) for name in DORA},
                "deploys_recorded": value(0)}
    return {**{name: cannot(f"{reached} run(s) reached a deploy phase and the "
                            f"measure is not implemented yet - the premise now "
                            f"holds, so this is work rather than an absence")
               for name in DORA},
            "deploys_recorded": value(reached)}


def time_to_merge_ready(states: list[dict]) -> dict:
    """Run opened to the merge gate approved, in seconds.

    **Deliberately NOT called lead time.** DORA's lead time for changes runs
    from commit to running in production, and renaming a merge-readiness proxy
    after it would be measuring the wrong subject and reporting it under the
    right name. This is a real number about a real thing; it is simply a
    different thing.
    """
    spans = []
    for st in states:
        opened = _when(st.get("created_at"))
        if opened is None:
            continue
        for step in st.get("history") or []:
            if "G4 approved" in str(step.get("note", "")):
                done = _when(step.get("at"))
                if done is not None:
                    spans.append((done - opened).total_seconds())
                break
    if not spans:
        return cannot("no run has a recorded G4 approval, so nothing has "
                      "reached merge-ready")
    return value(round(statistics.median(spans), 1))


def commits(target: Path) -> dict:
    """The repository's total commit count, or why it could not be read.

    Empty stdout at exit 0 is not a real zero - CLAUDE.md's own headline rule,
    an external binary returning nothing at exit 0 is not evidence of absence -
    and non-numeric stdout at exit 0 must not kill the document either. Both
    are `cannot()`, never a guessed number and never an uncaught crash.
    """
    try:
        proc = subprocess.run(["git", "-C", str(target), "rev-list", "--count", "HEAD"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return cannot(f"git did not run: {exc}")
    if proc.returncode != 0:
        # A slice, never `[0]`: stderr that is present but WHITESPACE-ONLY
        # strips to "", whose `.splitlines()` is `[]` - indexing that raised
        # IndexError uncaught. The fallback below names the command, the
        # target and the exit code - everything already in hand - rather
        # than the two-word "git failed" this used to say when stderr had
        # nothing usable in it, which told a reader nothing they could act on.
        lines = (proc.stderr or "").strip().splitlines()[:1]
        fallback = (f"git rev-list --count HEAD against {target} exited "
                   f"{proc.returncode} with no readable error output")
        return cannot(lines[0][:160] if lines else fallback)
    out = proc.stdout.strip()
    if not out:
        return cannot("git rev-list --count HEAD printed nothing at exit 0, "
                      "so the commit count could not be read")
    try:
        return value(int(out))
    except ValueError:
        return cannot(f"git printed a non-numeric commit count: {out!r}"[:160])


def unmeasured(doc: dict) -> list[str]:
    """Every metric this document could not compute, with its reason.

    The roll-up exists so a reader meets the gaps before deciding what to
    trust. A metrics file whose nulls are scattered through it is one where the
    reader finds them last, or not at all.
    """
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            if "value" in node and node["value"] is None:
                out.append(f"{path}: {node.get('why', 'no reason recorded')}")
                return
            for key, sub in node.items():
                walk(sub, f"{path}.{key}" if path else str(key))

    walk(doc, "")
    return sorted(out)


def corpus(target: Path) -> dict:
    """Whether the nightly replay is still running, and how old its answer is.

    **A number nobody can compute is never zero**, and a schedule nobody is
    running is the sharpest case of that: a stopped nightly replay reports no
    regressions for exactly the reason a healthy one does. So `never` and
    `stale` carry `value: null` and a REASON, and only a fresh replay produces a
    number.

    This exists because `--check-fresh` needed a reader. A check with a CLI and
    no consumer is the shape this project has now found six times over - a field
    written, or askable, and asked by nothing.
    """
    from tools import corpus_replay as replay

    try:
        found = replay.freshness(Path(target))
    except Exception as exc:                      # never a metric that crashed
        return {"replay_age_days": cannot(f"the replay history could not be "
                                          f"read: {exc}")}
    if found["state"] == replay.FRESH:
        # ROUND 4 (the tenth): `age_days` is FOREIGN - `tools.corpus_replay`
        # computes it, this module never passes it through `value()`/`cannot()`
        # - and it was rendered bare. Unlikely to ever be non-finite in the
        # real writer (review traced it), but it is the identical shape two
        # lines from everything else fixed here, so it gets the identical
        # guard rather than a note saying it is probably fine.
        age = found["age_days"]
        # `_finite_number` (round 8): this line was the SECOND site calling
        # `math.isfinite` on a value that could be an integer too large to
        # convert to a C double - `10 ** 400` overflows uncaught here exactly
        # as it did in `seconds` above, and this one needs no plain row: any
        # foreign producer of `age_days` can hand it one.
        #
        # ROUND 9: `_computable_number`, not `_finite_number` - "do the same
        # for the corpus age". There is no arithmetic on `age_days` within
        # this module, but round 8 already showed a huge-but-finite value
        # publishes UNBOUNDED (only the rejection reason was bounded, not
        # the accepted value) - the identical not-safe-to-publish shape, so
        # it gets the stricter check too. Rejecting it here also leaves a
        # trace for free: `corpus()` has no damage dict, but the `cannot()`
        # branch below already NAMES the rejected value in its reason.
        if _computable_number(age):
            return {"replay_age_days": {"value": age, "at": found["at"]}}
        # ROUND 7 (the thirteenth - the sixth shape, one function up, in code
        # written two rounds ago): `{age!r}` on an arbitrary object writes
        # ITS MEMORY ADDRESS into this published, diffed record - the same
        # hazard fixed at the sibling `why` site in round 6, still present in
        # the guard written one round before it. A rule hardened where
        # somebody was looking. Name the TYPE always; the value only when it
        # is a simple, reproducible SCALAR (str/int/float/bool/None) - never
        # an arbitrary object's repr, which can differ between two runs of
        # the identical input.
        #
        # ROUND 8, two more at the same guard, both the eighth shape (a type
        # TEST taken as a value GUARANTEE - passing a check constrains
        # neither the representation, nor the size, nor which operations
        # succeed): (1) `isinstance(age, (str, ...))` is TRUE for a SUBCLASS
        # too, and a one-line subclass can override `__repr__` to print
        # whatever it wants, address included - an EXACT type check is
        # required here specifically, because this guard's whole job is
        # choosing what text is safe to publish. (2) nothing bounded the
        # result's length, while the sibling `commits()` truncates every
        # reason built from external text at 160 characters in three places;
        # matched here.
        age_type = type(age)
        article = "an" if age_type.__name__[:1].lower() in "aeiou" else "a"
        descr = (repr(age) if age_type in (str, int, float, bool, type(None))
                 else f"{article} {age_type.__name__}")[:160]
        return {"replay_age_days": cannot(
            f"the replay history reports a fresh age_days that is not a "
            f"finite number ({descr}), so it cannot be rendered as one")}
    # Same ruling as `debt()` above: `found["why"]` is text
    # `tools.corpus_replay` authored, not this module - `corpus_replay.py:270`
    # returns `str(exc)` on one path with no guaranteed prefix, so an
    # unreadable-history exception with an empty message would reach
    # `cannot()` empty and crash the document. Latent today; guarded anyway.
    #
    # ROUND 4: `.strip()` before the `or`, same drift and same fix as `debt()`
    # above - a whitespace-only `why` was truthy here and raised, uncaught,
    # inside `cannot()`.
    #
    # ROUND 5 (the eleventh - this row's OWN opening defect, one method
    # later): `found["why"].strip()` ASSUMED `why` is already a string. The
    # pre-round-4 `found["why"] or fallback` tolerated a present `None`
    # (`None or fallback` picks the fallback); adding `.strip()` in front of
    # it broke that - `None.strip()` (and equally, a number's `.strip()`)
    # raises `AttributeError` UNCAUGHT, before the `or` is ever reached. A
    # present value walked past a guard into a chained access that assumed a
    # type nobody had checked - finding (1) of this row, verbatim, wearing a
    # different method name.
    #
    # ROUND 6 (the twelfth - a SIXTH shape, one that fails nothing): round 5
    # fixed the crash with `str(found["why"]).strip()`, coercing BEFORE the
    # falsy test - but `str(None)` is `"None"`, the four-letter word, which
    # is TRUTHY. So a present-null `why` no longer crashes; it launders past
    # this line AS "None" and past `cannot()`'s own guard (non-empty,
    # non-whitespace), reaching the published document as the literal text
    # for nothing - indistinguishable, per that constructor's own docstring,
    # from a bug in this module. The variants are worse: `str([])` is `"[]"`,
    # `str({})` is `"{}"`, and a bare object's `str()` form embeds ITS MEMORY
    # ADDRESS into a file this project diffs for determinism. Coerce the
    # value OR AN EMPTY STRING - never blindly to text - THEN trim, THEN
    # choose: a non-string `why` is treated as carrying nothing, the same as
    # an absent one, rather than misrepresented as some.
    raw_why = found["why"]
    why = (raw_why if isinstance(raw_why, str) else "").strip() or (
        f"the replay history is not fresh (state={found['state']!r}) and "
        f"carried no reason")
    return {"replay_age_days": cannot(why)}


# ── T446c: the audit trail, counted - and flattened for somebody else's tool ──
#
# Every number below comes from `events.jsonl` (T446a/b), the record the code
# paths that act already write: there is no second collection path. A number
# that cannot be computed is `cannot(why)`, never 0 - a step whose runner
# reported no tokens is "not measurable", not "cost nothing".


def event_rows(target: Path, states: list[dict]) -> list[dict]:
    """Every run's events, runs in id order and events in chain order -
    deterministic, so an export diffs cleanly."""
    from tools.sdlc import events as ev

    rows: list[dict] = []
    for run_id in sorted(str(st.get("run_id")) for st in states if st.get("run_id")):
        try:
            rows += [e for e in ev.read(target, run_id) if "_unreadable" not in e]
        except (OSError, ValueError, RuntimeError):
            continue
    return rows


def _percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile of whole numbers, or None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(round(q * len(ordered) + 0.5)) - 1))]


def _spread(values: list[int], what: str) -> dict:
    if not values:
        return {"p50": cannot(f"no {what} was recorded"),
                "p90": cannot(f"no {what} was recorded")}
    return {"p50": value(_percentile(values, 0.5)), "p90": value(_percentile(values, 0.9))}


def _tokens_and_cost(finished: list[dict]) -> dict:
    """Totals over the steps that REPORTED; how many did not, said beside them."""
    counted = [e for e in finished
               if (e.get("tokens") or {}).get("source") in ("reported", "self-reported")]
    missing = len(finished) - len(counted)
    if counted:
        tin = sum((e["tokens"].get("input") or 0) for e in counted)
        tout = sum((e["tokens"].get("output") or 0) for e in counted)
        tokens = {"input": value(tin), "output": value(tout),
                  "steps_reporting": value(len(counted)),
                  "steps_not_reporting": value(missing)}
    else:
        why = "no step in these runs reported tokens" if finished else "no step ran under the driver"
        tokens = {"input": cannot(why), "output": cannot(why),
                  "steps_reporting": value(0), "steps_not_reporting": value(missing)}
    priced = [e for e in finished if (e.get("cost") or {}).get("source") == "priced"]
    by_currency: dict[str, float] = {}
    for e in priced:
        cur = str(e["cost"].get("currency", "unspecified"))
        by_currency[cur] = round(by_currency.get(cur, 0.0) + float(e["cost"].get("amount") or 0), 6)
    cost = (value(dict(sorted(by_currency.items()))) if priced else
            cannot("no step's cost could be priced - declare price_in/out_per_mtok "
                   "in .sdlc/providers.toml"))
    return {"tokens": tokens, "cost": cost}


def steps(target: Path, states: list[dict]) -> dict:
    """Per step, per model, per gate - the audit trail's own numbers."""
    rows = event_rows(target, states)
    finished = [e for e in rows if e.get("event") == "step_finished"]
    by_step: dict[str, dict] = {}
    for name in sorted({e.get("step_name") or e.get("step") or "?" for e in finished}):
        mine = [e for e in finished if (e.get("step_name") or e.get("step") or "?") == name]
        outcomes: dict[str, int] = {}
        actors: dict[str, int] = {}
        for e in mine:
            outcomes[str(e.get("outcome"))] = outcomes.get(str(e.get("outcome")), 0) + 1
            actors[str(e.get("actor"))] = actors.get(str(e.get("actor")), 0) + 1
        durations = [e["duration_ms"] for e in mine if isinstance(e.get("duration_ms"), int)]
        by_step[name] = {"attempts": value(len(mine)),
                         "duration_ms": _spread(durations, "duration"),
                         "outcomes": value(dict(sorted(outcomes.items()))),
                         "actors": value(dict(sorted(actors.items()))),
                         **_tokens_and_cost(mine)}
    by_model: dict[str, dict] = {}
    for spec in sorted({f"{e.get('provider')}/{e.get('model')}" for e in finished
                        if e.get("provider") and e.get("model")}):
        mine = [e for e in finished if f"{e.get('provider')}/{e.get('model')}" == spec]
        by_model[spec] = {"steps": value(len(mine)), **_tokens_and_cost(mine)}
    waits: dict[str, list[int]] = {}
    for e in rows:
        if e.get("event") == "gate_decided" and isinstance(e.get("waited_ms"), int):
            waits.setdefault(str(e.get("gate")), []).append(e["waited_ms"])
    decided = sorted({str(e.get("gate")) for e in rows if e.get("event") == "gate_decided"})
    loops: dict[str, int] = {}
    for e in rows:
        if e.get("event") == "loop_back":
            key = f"{e.get('from')}->{e.get('to')}"
            loops[key] = loops.get(key, 0) + 1
    return {"by_step": by_step if by_step else cannot("no step has run under the driver yet"),
            "by_model": by_model if by_model else cannot("no step was routed to a model"),
            "gate_wait_ms": ({g: _spread(waits.get(g, []), f"wait at {g}") for g in decided}
                             if decided else cannot("no gate has been decided yet")),
            "loop_backs": value(dict(sorted(loops.items()))),
            "all": _tokens_and_cost(finished)}


#: The stable columns of each exported event type: the common fields, then the
#: type's own, with `tokens` and `cost` expanded to fixed sub-columns so a
#: spreadsheet needs no JSON parser. Renaming or dropping one breaks somebody's
#: dashboard - a test pins them by name.
_TOKEN_COLS = ("source", "reason", "calls", "input", "output", "cached", "total")
_COST_COLS = ("source", "reason", "amount", "currency")


def export_columns(event: str) -> list[str]:
    from tools.sdlc import events as ev
    cols: list[str] = []
    for f in (*ev.COMMON, *ev.FIELDS[event]):
        if f == "tokens":
            cols += [f"tokens_{c}" for c in _TOKEN_COLS]
        elif f == "cost":
            cols += [f"cost_{c}" for c in _COST_COLS]
        else:
            cols.append(f)
    return cols


def _flat(row: dict, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        if c.startswith("tokens_") and "tokens" in row:
            out[c] = (row.get("tokens") or {}).get(c[len("tokens_"):])
        elif c.startswith("cost_") and "cost" in row:
            out[c] = (row.get("cost") or {}).get(c[len("cost_"):])
        elif c == "artifacts":
            out[c] = json.dumps(row.get(c) or {}, sort_keys=True)
        else:
            out[c] = row.get(c)
    return out


def export(target: Path, fmt: str, out: Path) -> list[Path]:
    """One file per event type in `out`, every run's events in it: NDJSON keeps
    the nested fields, CSV flattens them to the stable columns. Loadable by any
    BI tool, spreadsheet, DuckDB or pandas with no harness installed."""
    import csv
    from tools.sdlc import events as ev

    states, _ = run_states(target)
    rows = event_rows(target, states)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for event in ev.TYPES:
        mine = [r for r in rows if r.get("event") == event]
        path = out / f"events-{event}.{fmt}"
        if fmt == "ndjson":
            path.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
                                    for r in mine), encoding="utf-8", newline="\n")
        else:
            cols = export_columns(event)
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
                w.writeheader()
                for r in mine:
                    w.writerow(_flat(r, cols))
        written.append(path)
    return written


#: T447a: the audit store's tables and their STABLE columns. The column
#: dictionary (`docs/metrics-columns.md`) documents each, and a test holds the
#: two equal by name - somebody's dashboard depends on every one of these.
STORE_VERSION = 1
STORE_TABLES: dict[str, list[str]] = {
    "runs": ["run_id", "kind", "project", "owner", "criticality", "opened_at",
             "closed_status", "closed_at"],
    "events": ["hash", "run_id", "seq", "at", "event", "step", "step_name", "prev",
               "payload"],
    "steps": [c for c in ("hash", "run_id", "seq", "at", "step", "step_name", "attempt",
                          "started_at", "ended_at", "duration_ms", "actor", "runner",
                          "provider", "model", "protocol", "tokens_source",
                          "tokens_reason", "tokens_calls", "tokens_input",
                          "tokens_output", "tokens_cached", "tokens_total",
                          "cost_source", "cost_reason", "cost_amount", "cost_currency",
                          "exit_code", "outcome")],
    "gates": ["hash", "run_id", "seq", "at", "event", "step", "gate", "hard",
              "decision", "approver", "ledger_hash", "waited_ms", "forced"],
    "loop_backs": ["hash", "run_id", "seq", "at", "from_step", "to_step"],
    "artifacts": ["step_hash", "run_id", "step", "path", "digest"],
}
#: Views over the tables, for the questions a dashboard asks most.
STORE_VIEWS = {
    "models": """CREATE VIEW IF NOT EXISTS models AS
        SELECT provider, model, COUNT(*) AS steps,
               SUM(tokens_input) AS tokens_input, SUM(tokens_output) AS tokens_output,
               SUM(CASE WHEN tokens_source IN ('reported','self-reported') THEN 1 ELSE 0 END)
                   AS steps_reporting,
               SUM(cost_amount) AS cost_amount
        FROM steps WHERE provider IS NOT NULL GROUP BY provider, model""",
}


def export_sqlite(target: Path, db: Path) -> dict[str, int]:
    """`audit.db`: the audit trail as tables Grafana's SQLite source reads.

    DERIVED and disposable - regenerated from `events.jsonl`, gitignored,
    never the record. Every row is keyed by its event's chain hash and written
    `INSERT OR IGNORE`, so exporting the same runs twice - or merging many
    repositories' stores into one (T452) - never double-counts. Returns the
    row count per table."""
    import sqlite3

    states, _ = run_states(target)
    rows = event_rows(target, states)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    try:
        cur = con.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('store_version', ?)",
                    (str(STORE_VERSION),))
        for table, cols in STORE_TABLES.items():
            key = {"runs": "run_id", "artifacts": "step_hash, path"}.get(table, "hash")
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ("
                        + ", ".join(cols) + f", PRIMARY KEY ({key}))")
        for sql in STORE_VIEWS.values():
            cur.execute(sql)

        def put(table: str, values: dict) -> None:
            cols = STORE_TABLES[table]
            cur.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES "
                        f"({', '.join('?' * len(cols))})", [values.get(c) for c in cols])

        for e in rows:
            common = {k: e.get(k) for k in ("hash", "run_id", "seq", "at", "event",
                                           "step", "step_name", "prev")}
            payload = {k: v for k, v in e.items() if k not in common and k != "schema_version"}
            put("events", {**common, "payload": json.dumps(payload, sort_keys=True)})
            kind = e.get("event")
            if kind == "run_opened":
                put("runs", {"run_id": e.get("run_id"), "kind": e.get("kind"),
                             "project": e.get("project"), "owner": e.get("owner"),
                             "criticality": e.get("criticality"), "opened_at": e.get("at")})
            elif kind == "run_closed":
                cur.execute("UPDATE runs SET closed_status = ?, closed_at = ? WHERE run_id = ?",
                            (e.get("status"), e.get("at"), e.get("run_id")))
            elif kind == "step_finished":
                put("steps", {**common, **_flat(e, export_columns("step_finished"))})
                for path, digest in sorted((e.get("artifacts") or {}).items()):
                    put("artifacts", {"step_hash": e.get("hash"), "run_id": e.get("run_id"),
                                      "step": e.get("step"), "path": path, "digest": digest})
            elif kind in ("gate_waiting", "gate_decided"):
                put("gates", {**common, **{k: e.get(k) for k in STORE_TABLES["gates"]
                                            if k in e}})
            elif kind == "loop_back":
                put("loop_backs", {**common, "from_step": e.get("from"), "to_step": e.get("to")})
        con.commit()
        return {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in STORE_TABLES}
    finally:
        con.close()


def compute(target: Path) -> dict:
    target = Path(target)
    states, damage = run_states(target)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "runs": runs(states, damage),
        "gates": gates(target, states, damage),
        "review": reviews(target, damage),
        "learning": learning(target, damage),
        "debt": debt(target),
        "corpus": corpus(target),
        "delivery": {"time_to_merge_ready_s": time_to_merge_ready(states),
                     "commits": commits(target)},
        "dora": dora(target, states),
        "steps": steps(target, states),
    }
    doc["unreadable"] = dict(sorted(damage.items()))
    doc["unmeasured"] = unmeasured(doc)
    return doc


def write(target: Path, doc: dict) -> Path:
    path = Path(target) / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--write", action="store_true",
                    help=f"write {RESULT.as_posix()}; without it nothing lands")
    ap.add_argument("--dashboard", action="store_true",
                    help="render the org/team/developer views as a self-contained "
                         "page plus a flat series file; nothing is written without it")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--export", choices=("ndjson", "csv", "sqlite"),
                    help="flatten every run's events.jsonl into one file per event "
                         "type (T446c)")
    ap.add_argument("--out", metavar="DIR",
                    help="with --export: where to write (default .sdlc/metrics/export)")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    # Absent is not zero, at the DOOR as well as in the document. `compute`
    # answers a null-with-reason for one metric it cannot read, but an absent
    # target is the whole subject missing, not one metric - it reached the record
    # readers, which treat a missing file as empty, and the door emitted a whole
    # document carrying `debt.declared: 0` at exit 0. So this refuses ABOVE the
    # document: the shared check names it, MetricsError carries could-not-look.
    if why := cli.missing_target(target):
        raise MetricsError(why)
    if args.out and not args.export:
        raise MetricsError("--out names where --export writes; pass --export too")
    if args.export:
        if args.export == "sqlite":
            db = (Path(args.out) / "audit.db" if args.out
                  else target / ".sdlc" / "metrics" / "audit.db")
            counts = export_sqlite(target, db)
            print(f"exported {db}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
            return 0
        out = Path(args.out) if args.out else target / ".sdlc" / "metrics" / "export"
        for path in export(target, args.export, out):
            print(f"exported {path}")
        return 0
    doc = compute(target)
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        r, g = doc["runs"], doc["gates"]
        print(f"runs     : {r['total']['value']} "
              f"({r['open']['value']} open, {r['closed']['value']} closed)")
        print(f"gates    : {g['approvals']['value']} approval(s), "
              f"{g['forced']['value']} forced approval(s), "
              f"{g['forced_advances']['value']} forced advance(s), "
              f"{sum(g['re_taken']['value'].values())} re-taken")
        rv = doc["review"]["median_duration_s"]
        print(f"review   : {doc['review']['sessions']['value']} session(s), "
              f"median "
              + (f"{rv['value']}s" if rv["value"] is not None else "unmeasured"))
        print(f"learning : {doc['learning']['standing']['value']} rule(s) standing")
        dv = doc["debt"]["outstanding"]["value"]
        # Same guard as the review line above: `cannot()`'s own docstring says
        # `value` stays None so no caller can render it as a floor, and this
        # line was rendering it as exactly that - the literal text "None"
        # where a count belongs, at exit 0, in the one line a reader sees
        # without opening the roll-up.
        print(f"debt     : "
              + (f"{dv} outstanding" if dv is not None else "unmeasured"))
        if doc["unreadable"]:
            print(f"unreadable rows: {doc['unreadable']}")
        print(f"unmeasured: {len(doc['unmeasured'])}")
        for line in doc["unmeasured"]:
            print(f"  -- {line}")
    if args.write:
        print(f"wrote {write(target, doc)}")
    if args.dashboard:
        # `run_states` again rather than threading it out of `compute`: the views
        # need the raw states, and `compute` deliberately returns the DOCUMENT.
        # Widening its return to serve one caller would make every other reader
        # carry a tuple it does not want.
        from tools.sdlc import dashboard as views_mod

        states, _ = run_states(target)
        view = views_mod.views(target, doc, states)
        page, rows = views_mod.write(target, view)
        print(f"dashboard: {page}")
        print(f"series   : {rows} ({len(views_mod.series(view))} row(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look: records that cannot be read are not an
    absence of activity."""
    cli.use_utf8()
    return cli.door(_main, argv, "the metrics engine", 3)


if __name__ == "__main__":
    raise SystemExit(main())
