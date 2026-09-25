"""Gate feedback capture — item 34: the difference between what the agent
proposed and what the human actually accepted.

Every other record in this harness says what *happened*. `approvals.jsonl`
says a gate was taken, `state.json` says an artifact exists, `reviews.jsonl`
says a review took four minutes. None of them says the thing item 50's retro
agent needs to learn from: **the human changed something before saying yes,
and here is what.** That difference is the only signal in the lifecycle that
is about the harness rather than about the change.

`feedback.jsonl` lives in the run folder beside the approval ledger and is
appended to at exactly one moment — **when a gate verdict is recorded** —
because that is the only moment "what the human finally accepted" is defined.
Before the verdict the artifacts are still moving; after it they are pinned.
`approve.record()` calls this, so there is no capture step anyone can skip:
the corrections accrue on every gate of every run, or the gate did not happen.

**What running it found, and it is not a small thing.** A human who improves
the agent's design before approving it *voided their own approval*. Item 32
pins "the artifact hashes at the moment of approval" — but it copied
`state.json`'s recorded map, which holds the hash from the moment the AGENT
wrote the file. Edit the file, then approve: the entry pins bytes that no
longer exist, and `gate_state` answers

    G2 approved different bytes - 30-design.md: changed since G2 approved it

about an edit the approver made themselves, before saying yes. Every fixture
in a 3,188-test suite played the human as a rubber stamp, so nothing caught
it. Capturing the delta is what fixes it: an artifact that changed is
**re-pinned to the accepted bytes** and the change is recorded here, so item
32's property is stronger than before — a gate pins what the human saw, and a
post-approval edit still voids it.

An artifact that is MISSING or unhashable at approval time is *not* re-pinned.
It keeps the digest it had, so the void still fires; a run whose own output
disappeared before the gate must not be quietly approved because the pin was
refreshed to nothing. It gets a row saying so.

**Contents are not copied here, and that is deliberate.** A row names the
artifact and both digests. It does not carry the before-and-after text,
because the before-text no longer exists on disk by the time the gate is taken
— and the run folder is committed with the code, so `git log -p` on it is the
diff, authoritative and singular. Copying corrections into a second file would
create two records of one edit, and one of them would eventually be wrong.

**The approver is named.** `reviews.jsonl` deliberately carries no identity
(item 27: a field that exists gets queried) and this file deliberately does,
because these rows are decisions, `approvals.jsonl` already binds the same
name to the same decision one file over, and two records of one act that
disagree about who performed it are worse than either alone. The anti-Goodhart
control is the *view* rule the plan puts in `policy.yml` — individual metrics
visible only to that individual — not the absence of the audit fact.

**The denominator travels with the corrections.** A file of nothing but
rejected findings invites "agent acceptance rate = 1 - len(file)", which is
wrong. The gate row therefore carries the review totals it counted, so item
52 can compute the rate from this file without needing to be told twice.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.review import hitl  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
FEEDBACK = "feedback.jsonl"

#: What a row can be. `gate` is the verdict and the human's own words;
#: everything else is a correction — something the agent proposed and the human
#: changed, declined, or left undecided.
KINDS = ("gate", "artifact-edit", "artifact-lost", "artifact-unpinnable",
         "finding-rejected", "proposal")

#: Dispositions that mean the human did NOT take the agent's word. `accepted`
#: is not among them: an accepted finding is not a correction, it is the tool
#: working, and its count reaches item 52 through the gate row's totals.
CORRECTIONS = ("rejected", "deferred")

#: The `50-scope-add.md` decision box, as the phrases item 38 actually writes.
#: `test_feedback.py` asserts each one appears in `suite.scope_add_text`'s own
#: output, because a checkbox this file reads and that file renames is a silent
#: `undecided` forever — the same "two spellings nobody required to agree"
#: defect item 64 found between the profile and the graph.
SCOPE_DECISION = (("widen the scope", "accepted"), ("out of scope", "declined"))
_BOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_of(target: Path, run_id: str) -> Path:
    return ledger.run_dir(Path(target), run_id) / FEEDBACK


def read(target: Path, run_id: str) -> list[dict]:
    """Every row, in file order.

    An unreadable line is returned as a marker rather than dropped. This file
    is a corpus rather than a chain, so a bad line does not stop the walk — but
    it is never silently skipped, because a learning set that quietly loses
    rows teaches from a sample nobody chose.

    The marker carries `_problem`, the reason `cli.jsonl_rows` already worked
    out, so a line that PARSED but is not an object is not later mislabelled as
    unreadable JSON: `_render` reads that reason instead of naming every kind of
    damage the same thing.
    """
    return [line.row if line.row is not None
            else {"_unreadable": line.number, "_problem": line.problem}
            for line in cli.jsonl_rows(path_of(target, run_id))]


def append(target: Path, run_id: str, rows: list[dict]) -> int:
    """Write rows whose `id` is not already in the file. Returns how many
    landed."""
    if not rows:
        return 0
    seen = {r.get("id") for r in read(target, run_id) if "_unreadable" not in r}
    fresh = [r for r in rows if r.get("id") not in seen]
    if not fresh:
        return 0
    path = path_of(target, run_id)
    ledger.append_record(path, "".join(json.dumps(row, sort_keys=True, ensure_ascii=False)
                                       + "\n" for row in fresh))
    return len(fresh)


# ── the artifact delta, which is also the fix ────────────────────────────────


def pending_edits(target: Path, run_id: str) -> tuple[list[tuple[str, str]],
                                                      list[tuple[str, str, str]]]:
    """(edited, lost) — the run's recorded artifacts against the tree right now.

    `edited` is `(rel, digest the agent left)`; `lost` is `(rel, kind, detail)`
    for anything missing, no longer hashable, or `converted` - changed only in
    its line endings (T483). A conversion is not an edit: re-pinning it would
    approve the bytes a checkout made, so its pin is left and the gate says why.
    One walk, `state.drift`'s, so this cannot drift from what the gates compare.
    """
    state = ledger.load(Path(target), run_id)
    recorded = state.get("artifacts", {})
    edited: list[tuple[str, str]] = []
    lost: list[tuple[str, str, str]] = []
    for rel, kind, detail in ledger.drift(Path(target), run_id, recorded):
        if kind == "changed":
            edited.append((rel, recorded[rel]))
        else:
            lost.append((rel, kind, detail))
    return edited, lost


def repin(target: Path, run_id: str, rels: list[str]) -> dict[str, str]:
    """Re-hash each artifact and return `rel -> the accepted digest`.

    This is the half that makes a human edit legal instead of fatal. It runs
    only for artifacts that still hash, and only from `capture`, one step
    before the approval entry copies the map — so what a gate pins is what the
    approver was looking at.

    Keyed by `state.artifact_key`, which is the same normalisation the ledger
    keys its own map by. This site used to re-index with the RAW argument, so a
    `rel` spelled with the other separator would re-pin one key and read back
    another — and, because the read has a default, hand the entry an empty
    digest rather than an error.

    **That fix closed this site and not the class, and the docstring said
    otherwise for a while.** It used to argue the defect never happened because
    every `rel` here comes from a key the ledger recorded — but a key the
    ledger recorded can itself carry the other separator (`key_refusal` speaks
    only for absolute and climbing paths), and `capture`, the one caller, went
    on indexing the returned map by that RAW key. So it DID happen, one frame
    up, and wrote an empty accepted digest into a durable record for bytes this
    function had really re-hashed (T416). The caller now reads back through
    `artifact_key` too, and the two are asserted TOGETHER rather than each in
    isolation, by `test_the_verdict_records_the_digest_repin_really_computed`.

    A recorded key the ledger would now REFUSE — an absolute path, or one that
    climbs out with `..`, both of which it used to write — is kept exactly as
    recorded and is not re-pinned. This runs from `capture`, inside
    `approve.record`, so raising here would mean a human taking a gate on a
    repository that carries such a run got a refusal naming a path they never
    typed, and no verdict recorded: the fix becoming the outage. Reading our
    own record is COULD-NOT-LOOK (`state.key_refusal` says why); `capture`
    names the entry and the reason in the feedback record.
    """
    out: dict[str, str] = {}
    for rel in rels:
        if ledger.key_refusal(rel):
            out[rel] = ledger.load(Path(target), run_id).get(
                "artifacts", {}).get(rel, "")
            continue
        key = ledger.artifact_key(rel)
        state = ledger.record_artifact(Path(target), run_id, key)
        out[key] = state.get("artifacts", {}).get(key, "")
    return out


# ── the proposals a human answered, or did not ───────────────────────────────


def adr_dismissals(target: Path, run_id: str) -> list[dict]:
    """Triggers the design fired and a human refused, as corrections.

    Item 64 already records these with a reason and a name; nothing had ever
    read them as *feedback about the harness*, which is what a dismissal is —
    the trigger rules said an ADR was owed and a person said it was not.
    """
    from tools.sdlc import adr as records  # local: it pulls the graph layer in

    try:
        rows = records.dismissals(Path(target), run_id)
    except records.AdrError:
        # A malformed dismissal log is item 64's refusal to raise, not this
        # file's: the gate it blocks is the one that should report it.
        return []
    return [{"id": f"adr:{trigger}", "kind": "finding-rejected",
             "source": "adr-trigger", "finding": trigger,
             "disposition": "rejected", "because": row.get("because", ""),
             "by": row.get("by", ""), "decided_at": row.get("at", "")}
            for trigger, row in sorted(rows.items())]


def scope_decision(text: str) -> tuple[str, list[str]]:
    """(decision, the boxes that were ticked) for a `50-scope-add.md` body.

    `undecided` is a real answer and the common one: `suite.obligations`
    requires the file to EXIST and never reads the box inside it, so a
    scope-add proposal can sit unanswered under a green P5. Recording that is
    the point — a proposal nobody decided is exactly the correction item 50
    should see.
    """
    ticked = [body for line in text.splitlines()
              if (m := _BOX.match(line)) and m.group(1) in "xX"
              for body in (m.group(2),)]
    got = {name for body in ticked for phrase, name in SCOPE_DECISION
           if phrase in body.lower()}
    if len(got) > 1:
        return "contradictory", ticked
    return (got.pop() if got else "undecided"), ticked


def scope_proposals(target: Path, run_id: str, gate: str) -> list[dict]:
    """The scope-add proposal's decision, if the run made one."""
    from tools.sdlc import stage as stages  # local: keeps this module light

    folder = ledger.run_dir(Path(target), run_id)
    rows: list[dict] = []
    for rel in stages.by_phase("P5").conditional:
        path = folder / rel
        if not path.is_file():
            continue
        decision, ticked = scope_decision(path.read_text(encoding="utf-8",
                                                         errors="replace"))
        rows.append({"id": f"scope-add:{gate}:{decision}", "kind": "proposal",
                     "proposal": "scope-add", "artifact": rel,
                     "decision": decision, "ticked": ticked,
                     # Who ticked the box is not knowable from the box, and the
                     # approver did not necessarily tick it. An observation
                     # with a name on it would be a guess presented as a fact.
                     "by": ""})
    return rows


def review_corrections(target: Path, run_id: str) -> tuple[list[dict], dict]:
    """(corrections, totals) from the review sessions belonging to this run.

    Item 27's rows land in the adopter's `.sdlc/metrics/reviews.jsonl` and
    carried no run id at all, so "which findings did the human reject on THIS
    change" was unanswerable — two records of the same review with no join
    between them. `hitl --run` supplies it; a row without one is another run's,
    or predates the field, and is counted as neither.
    """
    reviews, _ = hitl.paths(Path(target))
    totals = {"sessions": 0, "findings": 0, "corrections": 0, "unreadable": 0}
    out: list[dict] = []
    for line in cli.jsonl_rows(reviews):
        # A row that is not an object is DAMAGE. It used to leave through the
        # same `continue` as a row belonging to another run, so the `unreadable`
        # counter sitting right here could not see it and a corrupt corpus was
        # indistinguishable from a small one - the absent-vs-invalid fold this
        # module's own `read()` docstring argues against two functions above.
        if line.row is None:
            totals["unreadable"] += 1
            continue
        row = line.row
        if row.get("run") != run_id or row.get("kind") != "review":
            continue
        totals["sessions"] += 1
        started = row.get("started_at", "?")
        # `.get("findings", [])` is defeated by a PRESENT null, and a finding
        # that is not an object has no `.get`. `hitl.show` reads this same file
        # and was hardened for both; this reader of it was not.
        #
        # Row damage and finding damage share the one counter deliberately:
        # this number exists to stop a DAMAGED corpus reading as a small one,
        # and for that question "how many things here could I not use" is the
        # whole answer. `hitl.show` splits them because it prints both beside
        # a median someone is about to trust.
        findings = row.get("findings")
        if not isinstance(findings, list):
            totals["unreadable"] += 1
            continue
        for f in findings:
            if not isinstance(f, dict):
                totals["unreadable"] += 1
                continue
            totals["findings"] += 1
            if f.get("disposition") not in CORRECTIONS:
                continue
            totals["corrections"] += 1
            out.append({"id": f"review:{started}:{f.get('id', '?')}",
                        "kind": "finding-rejected", "source": f.get("source", ""),
                        "finding": f.get("id", "?"),
                        "disposition": f.get("disposition", ""),
                        "severity": f.get("severity", ""),
                        "path": f.get("path", ""),
                        "component": row.get("component", ""),
                        # Blank, and deliberately. The reviewer who rejected
                        # this finding is not necessarily the person recording
                        # the verdict, and `reviews.jsonl` carries no identity
                        # by construction — stamping the approver's name here
                        # would invent an attribution AND put back the field
                        # item 27 removed on purpose.
                        "by": ""})
    return out, totals


#: How many DISTINCT RUNS must reject the same finding before it is a pattern
#: rather than an opinion. Runs, never occurrences: two rejections inside one
#: review are one person deciding one thing once, and counting them twice is
#: how a single reviewer's preference becomes a rule everybody inherits.
GROUP_FLOOR = 2


def group_corrections(target: Path, floor: int = GROUP_FLOOR) -> list[dict]:
    """Corrections across EVERY run, grouped by the finding they are about.

    The lexical seam item 50 names, and it is lexical on purpose — the finding
    id is what a human rejected, and matching it exactly asks nothing of an
    embedding nobody can audit. Chroma stays deferred.

    Groups are ordered by how many runs rejected them, then by name, so the
    output is stable and a proposal derived from it is reproducible.

    **A group is not a decision.** It is evidence that humans keep saying no to
    the same thing, which is the beginning of an argument and not the end of
    one - `learning.py` turns a group into a PROPOSAL, and a human still takes
    the gate.
    """
    target = Path(target)
    base = ledger.runs_dir(target)
    groups: dict[str, dict] = {}
    if not base.is_dir():
        return []
    for entry in sorted(base.iterdir()):
        # Folders, not `state.list_runs`: item 44's reason. A sweep that dies
        # on one malformed state.json distils nothing from the other fifty.
        if not entry.is_dir() or entry.name == ledger.ARCHIVE_NAME:
            continue
        # No blanket catch. `review_corrections` reads one shared reviews file
        # and COUNTS a damaged line rather than raising, so the sweep does not
        # need a guard to survive damaged content - and the only thing a bare
        # `except: continue` here could catch is a genuine could-not-look, which
        # it turned into a run that silently distilled to nothing: the absent-
        # vs-could-not-look fold this file argues against two functions up. A
        # real failure belongs at the caller's door, named, not swept under an
        # empty result a learning loop then reads as "nobody corrects anything".
        rows, _ = review_corrections(target, entry.name)
        for row in rows:
            name = str(row.get("finding") or "").strip()
            if not name or name == "?":
                continue
            got = groups.setdefault(name, {
                "finding": name, "runs": [], "dispositions": [],
                "severities": [], "paths": [], "components": []})
            for key, value in (("runs", entry.name),
                               ("dispositions", row.get("disposition", "")),
                               ("severities", row.get("severity", "")),
                               ("paths", row.get("path", "")),
                               ("components", row.get("component", ""))):
                if value and value not in got[key]:
                    got[key].append(value)
    out = [dict(g, run_count=len(g["runs"])) for g in groups.values()
           if len(g["runs"]) >= floor]
    out.sort(key=lambda g: (-g["run_count"], g["finding"]))
    return out


# ── capture ──────────────────────────────────────────────────────────────────


def next_gate_number(target: Path, run_id: str, gate: str) -> int:
    """The number the next `gate:<gate>:N` row takes.

    One above the highest N already on file, which for an undamaged record is
    exactly "how many verdicts this gate has taken, plus one". It stops being
    that the moment a line cannot be read, and that is the point: a damaged
    line does not free the number a readable row after it still holds.

    A readable id whose N is longer than `int()` will parse - past CPython's
    digit limit - is a damaged record too, and it is refused in this module's
    own words. The interpreter's own error names that limit and a
    `sys.set_int_max_str_digits()` call - true about the interpreter and
    useless to a person taking a gate. The record is what is wrong, so the
    record is what the refusal names.
    """
    pattern = re.compile(rf"^gate:{re.escape(gate)}:(\d+)$")
    highest = 0
    for row in read(target, run_id):
        if "_unreadable" in row:
            continue
        if found := pattern.match(str(row.get("id", ""))):
            digits = found.group(1)
            try:
                highest = max(highest, int(digits))
            except ValueError:
                # CPython refuses to parse an integer literal past its digit
                # limit and says so in its own vocabulary. A `gate:<g>:N` id
                # this long is a damaged feedback line, not a verdict count:
                # refuse by naming the record and the file to repair, the way
                # the concurrent-write guard below does, rather than handing a
                # person the interpreter's digit-limit error.
                raise RuntimeError(
                    f"{FEEDBACK} carries a {gate} gate row whose sequence number "
                    f"is {len(digits)} digits long - that is a damaged record, "
                    f"not a real gate count, and the next verdict cannot be "
                    f"numbered past it. Repair {path_of(target, run_id)} and take "
                    f"the gate again") from None
    return highest + 1


def capture(target: Path, run_id: str, gate: str, verdict: str, approver: str,
            notes: str = "") -> list[dict]:
    """Record everything this verdict is a correction of, and re-pin what the
    human accepted. Returns the rows that landed.

    Called from `approve.record()` immediately before the entry is built, so
    the entry copies the refreshed map. It runs for a REJECT too: a reject is
    about the bytes on disk as much as an approve is, and item 42 compares the
    two by pinned fingerprint — pinning a stale map on one side and a fresh one
    on the other would make a contested gate stop contesting.
    """
    target = Path(target)
    edited, lost = pending_edits(target, run_id)
    proposed = ledger.load(target, run_id).get("artifacts", {})
    accepted = repin(target, run_id, [rel for rel, _ in edited])
    stamp = _now()
    # No `by` in the shared part. A name belongs on the rows where a named
    # person did the thing — the verdict, and the bytes they accepted. Sharing
    # it would attribute a rejected review finding, or an unticked checkbox, to
    # whoever happened to record the gate.
    common = {"schema": SCHEMA_VERSION, "run": run_id, "at": stamp,
              "gate": gate}
    rows: list[dict] = []
    for rel, was in edited:
        # A key the ledger would refuse today but wrote itself yesterday. It is
        # left exactly as recorded rather than re-pinned or dropped, so the row
        # says which entry was left alone and why instead of an `artifact-edit`
        # claiming a fresh digest for bytes nobody re-hashed.
        if why := ledger.key_refusal(rel):
            rows.append({**common, "id": f"unpinnable:{gate}:{rel}",
                         "kind": "artifact-unpinnable", "artifact": rel,
                         "by": approver, "proposed": was, "detail": why})
            continue
        # Read the re-pin back through the SAME normalisation `repin` keyed it
        # by. This site indexed the map with the RAW recorded key, so a legacy
        # separator spelling — which `key_refusal` does NOT refuse, because
        # `artifact_key` normalises separators rather than rejecting them —
        # was re-hashed under one key and read back under another, and the
        # `.get` default wrote an EMPTY accepted digest for bytes that HAD
        # been re-hashed. `repin`'s own docstring records that defect as
        # fixed, and it was: inside `repin`, while it went on living here, one
        # frame up. `key_refusal` returned "" above, so this cannot raise.
        now = accepted.get(ledger.artifact_key(rel), "")
        rows.append({**common, "id": f"edit:{gate}:{rel}:{now[7:19]}",
                     "kind": "artifact-edit", "artifact": rel, "by": approver,
                     "proposed": was, "accepted": now,
                     "detail": "the contents are in git: the run folder is "
                               "committed with the code it describes"})
    for rel, kind, detail in lost:
        rows.append({**common, "id": f"lost:{gate}:{rel}:{kind}",
                     "kind": "artifact-lost", "artifact": rel, "by": approver,
                     "problem": kind, "detail": detail,
                     "proposed": proposed.get(rel, "")})
    rows += [{**common, **row} for row in adr_dismissals(target, run_id)]
    rows += [{**common, **row} for row in scope_proposals(target, run_id, gate)]
    findings, totals = review_corrections(target, run_id)
    rows += [{**common, **row} for row in findings]

    # The corrections are written FIRST, and the gate row counts what actually
    # landed. Counting `rows` instead would let a second capture at one gate
    # claim corrections that were deduped away — a gate row saying it carried
    # three when it carried none, which is the kind of number a learning loop
    # would then divide by.
    landed = append(target, run_id, rows)
    # The number comes from the highest one ON FILE, not from a count of the
    # rows we could read. Two readers of this file have to agree about what a
    # damaged line is, and they did not: this site counted readable gate rows,
    # so one truncated line made the count one lower, while `append`'s dedupe
    # still saw the id that number collides with sitting on a readable row. It
    # dropped the new row and this function returned it anyway — three approves
    # with the first truncated, then a REJECT, and the file kept saying
    # `approve, approve`. A damaged line is COULD-NOT-LOOK, not one-fewer
    # verdict, so no number at or below the highest one present is free.
    row = {**common, "id": f"gate:{gate}:{next_gate_number(target, run_id, gate)}",
           "kind": "gate", "by": approver, "verdict": verdict, "notes": notes,
           "review": totals, "corrections": landed}
    # And the last line of defence, because `append` reports a dedupe as a
    # quiet 0 — which is right for a correction and a lie for a verdict. A
    # record that still holds this id is one where the verdict cannot be
    # written, and writing nothing while `approve.record` goes on to build the
    # entry is the one failure this file cannot survive: a human said no, the
    # record does not carry it, and the harness said it did. Refusing loses an
    # approve that recording would have kept; that is the trade, taken because
    # a refusal is loud and a person can act on it, and an absent verdict is
    # discovered by nobody. The corrections above are already on file and stay
    # there — they are true about the artifacts whichever way the gate goes.
    if append(target, run_id, [row]) != 1:
        raise RuntimeError(
            f"the {gate} verdict was not recorded: {FEEDBACK} already holds a "
            f"row with id {row['id']!r}, so it would be deduped away rather "
            f"than written. Repair {path_of(target, run_id)} and take the gate "
            f"again - recording nothing and reporting success is worse")
    return rows + [row]


def summary(target: Path, run_id: str) -> dict:
    """Counts by kind, plus the unreadable tally. Item 50's `group_corrections`
    seam starts from this — lexically, per the plan, with embeddings deferred."""
    rows = read(target, run_id)
    counts: dict[str, int] = {}
    unreadable = 0
    for row in rows:
        if "_unreadable" in row:
            unreadable += 1
            continue
        counts[row.get("kind", "?")] = counts.get(row.get("kind", "?"), 0) + 1
    return {"run": run_id, "rows": len(rows) - unreadable,
            "by_kind": dict(sorted(counts.items())), "unreadable": unreadable}


# ── CLI ──────────────────────────────────────────────────────────────────────


def _render(target: Path, run_id: str) -> str:
    rows = read(target, run_id)
    if not rows:
        return (f"no feedback recorded for {run_id} - it is written when a gate "
                f"verdict is, so a run that has taken none has none")
    head = summary(target, run_id)
    lines = [f"feedback: {head['rows']} row(s) for {run_id}",
             "  " + (", ".join(f"{k}={v}" for k, v in head["by_kind"].items())
                     or "(none)")]
    if head["unreadable"]:
        lines.append(f"  !! {head['unreadable']} line(s) are not readable JSON")
    for row in rows:
        if "_unreadable" in row:
            lines.append(f"  !! line {row['_unreadable']}: "
                         f"{row.get('_problem') or 'could not be read'}")
        elif row.get("kind") == "gate":
            lines.append(f"  {row['gate']} {row.get('verdict')} by "
                         f"{row.get('by')} - {row.get('corrections', 0)} "
                         f"correction(s) captured with it")
            if row.get("notes"):
                lines.append(f"      {row['notes']}")
        elif row.get("kind") == "artifact-edit":
            lines.append(f"  {row['gate']}: {row['artifact']} was edited before "
                         f"the verdict, and the verdict pins the edited bytes")
        elif row.get("kind") == "artifact-unpinnable":
            lines.append(f"  !! {row['gate']}: {row['artifact']} was edited, "
                         f"and its recorded key cannot be read back, so it is "
                         f"left exactly as recorded - {row.get('detail')}")
        elif row.get("kind") == "artifact-lost":
            lines.append(f"  !! {row['gate']}: {row['artifact']} is "
                         f"{row.get('problem')} - the pin is left as it was, so "
                         f"the gate still reports void")
        elif row.get("kind") == "proposal":
            lines.append(f"  {row['gate']}: {row.get('proposal')} - "
                         f"{row.get('decision')}")
        else:
            lines.append(f"  {row['gate']}: {row.get('source')} finding "
                         f"{row.get('finding')} was {row.get('disposition')}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="What the human changed before saying yes: the run's "
                    "feedback record (item 34).")
    ap.add_argument("--target", required=True, metavar="DIR")
    ap.add_argument("--run", required=True, metavar="RUN_ID")
    ap.add_argument("--json", action="store_true",
                    help="the rows themselves, for a reader rather than a person")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2
    try:
        ledger.load(target, args.run)
    except RuntimeError as exc:
        print(f"!! {exc}")
        return 2
    if args.json:
        print(json.dumps({"summary": summary(target, args.run),
                          "rows": read(target, args.run)},
                         indent=2, sort_keys=True))
        return 0
    print(_render(target, args.run))
    return 0


def main(argv: list[str] | None = None) -> int:
    # The call site is the rule `cli.door` states: 2 is this CLI's could-not-
    # look - the code its own `--target` and `--run` guards already return - so
    # an unexpected failure lands there rather than on Python's default exit 1,
    # which for this CLI would be a crash wearing "everything ran".
    return cli.door(_main, argv, "the feedback record", 2)


if __name__ == "__main__":
    raise SystemExit(main())
