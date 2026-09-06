"""The learning loop — item 50: a correction people keep making becomes a
proposal, a proposal takes a gate, and everything is reversible.

`feedback.jsonl` has recorded what humans CHANGED about an agent's work since
item 34 — an artifact they edited before approving, a finding they rejected, an
ADR trigger they dismissed. Nothing read it back. A record of corrections that
never becomes a correction is a diary.

**A group is evidence, not a decision.** `feedback.group_corrections` says
humans keep rejecting the same finding; this module turns that into a PROPOSAL
with the evidence attached, and a human still takes the gate. Nothing here
applies itself.

**Two homes, decided by the FILE a proposal would edit.** Rev 12 splits learning
by what it is about: a generic prompt, rule or traversal weight is product and
belongs in the harness; a rule learned about one repository stays in that
repository's `.sdlc/`. That is a statement about intent, and intent is not
checkable — so the home here is read off the target PATH, which is. A proposal
that would edit a file inside the harness is `harness`; one inside the adopter's
tree is `repo`. The failure this prevents is the one Rev 12 names: a generic
harness silently accumulating one adopter's idiosyncrasies, which nobody decides
to do and nobody notices until portability fails.

**Applying is gated twice and refuses rather than assumes.**

* The corpus regression must have RUN and be clean. What that does and does not
  establish is written into `corpus_gate` rather than implied, because a gate
  whose meaning is assumed is worse than none.
* A run's G3 must be approved and live. Not a new gate — `approve.py` already
  owns what an approval is, and a second one would be a second answer to
  "did a human say yes".

**Undo appends; it never deletes.** The kill gate reads *"reverts via `--undo`
(audit shows both)"*, and an undo that erased its own application would leave an
audit showing neither.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import feedback as record  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: Protected by `policy.yml` since before anything wrote it — declared ahead of
#: this item on purpose, so the audit could never be written by a run.
LOG = Path(".sdlc") / "metrics" / "learning_log.jsonl"

#: Where a repo-local learned rule lands. The same idiom as `known_failures` and
#: `sca_known_findings`: DECLARE it, name it, and anything undeclared still
#: fires. A suppression nobody wrote down is not a suppression, it is a gap.
SUPPRESS_KEY = "review_suppress"

HOMES = ("repo", "harness")
ACTIONS = ("applied", "undone")


class LearningError(RuntimeError):
    """The proposal, the log, or the gate cannot be read."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── which home a change belongs to ──────────────────────────────────────────


def home_of(path: Path, harness: Path = ROOT) -> str:
    """`harness` if this file is part of the product, `repo` otherwise.

    Read off the PATH rather than guessed from the correction's content,
    because a path is checkable and an intention is not. A caller that got this
    wrong would either publish one adopter's habits into everybody's harness or
    strand a genuine product improvement in one repository.
    """
    try:
        Path(path).resolve().relative_to(Path(harness).resolve())
    except ValueError:
        return "repo"
    return "harness"


# ── proposals ───────────────────────────────────────────────────────────────


def proposal_id(finding: str, home: str, target_rel: str) -> str:
    return "learn:" + hashlib.sha256(
        f"{finding}|{home}|{target_rel}".encode("utf-8")).hexdigest()[:16]


def propose(target: Path, floor: int = record.GROUP_FLOOR) -> list[dict]:
    """Every group that has earned a proposal, as data a human can read.

    v1 emits ONE kind of change: a finding humans keep rejecting is proposed
    for the repository's own `review_suppress`. It is repo-local by
    construction, so it exercises the two-homes rule honestly rather than
    asserting it — a generic change is one that edits a harness file, and this
    module can carry one without being able to invent one.
    """
    target = Path(target)
    out = []
    for group in record.group_corrections(target, floor=floor):
        from tools.sdlc import policy as guardrails
        rel = Path(".sdlc") / guardrails.PROFILE_NAME
        home = home_of(target / rel)
        out.append({
            "schema_version": SCHEMA_VERSION,
            "id": proposal_id(group["finding"], home, rel.as_posix()),
            "finding": group["finding"],
            "home": home,
            "path": rel.as_posix(),
            "change": {"kind": "append-to-list", "key": SUPPRESS_KEY,
                       "add": [group["finding"]]},
            "evidence": {k: group[k] for k in
                         ("runs", "run_count", "dispositions", "severities",
                          "paths", "components")},
            "why": (f"{group['finding']} was rejected or deferred by a human in "
                    f"{group['run_count']} separate run(s) "
                    f"({', '.join(group['runs'])}). A finding people keep saying "
                    f"no to is costing review attention every time it fires. "
                    f"Declaring it in {SUPPRESS_KEY} says so out loud, and "
                    f"anything not declared still fires."),
        })
    return out


# ── the gates ───────────────────────────────────────────────────────────────


def corpus_gate(target: Path) -> list[str]:
    """Why the corpus regression does not permit an apply right now.

    **What this establishes, stated because a gate whose meaning is assumed is
    worse than none:** that the impact engine is not currently ranking worse
    than it did, on the adopter's own history. It does NOT test the proposed
    change — a `review_suppress` entry cannot move a precision@k, and claiming
    otherwise would be exactly the false assurance this project keeps finding.
    It is a precondition on the harness's health before rules start changing on
    the strength of feedback, and the change kind the corpus WOULD genuinely
    test is a traversal weight, which is the case the task line was written for.

    **A corpus that cannot be read refuses.** Could-not-look is not a pass.
    """
    from tools import corpus_replay as replay

    try:
        rows = replay.history(Path(target))
    except replay.ReplayError as exc:
        return [str(exc)]
    if not rows:
        return [f"no corpus replay has ever been recorded in {replay.HISTORY} - "
                f"a learned change may not land on the strength of a regression "
                f"check nobody has run (corpus_replay.py --replay --write)"]
    latest = rows[-1]
    owed = replay.problems(Path(target), latest, rows=rows[:-1])
    return [f"the last corpus replay is not clean: {line}" for line in owed]


def gate_taken(target: Path, run_id: str, gate: str = "G3") -> list[str]:
    """Why `gate` is not a live approval on this run.

    `approve.py` owns what an approval IS - whether the chain holds, whose
    verdict is current, and whether the approved bytes have moved. A second
    implementation here would be a second answer to "did a human say yes".
    """
    from tools.sdlc import approve

    try:
        entries = approve.live_entries(Path(target), run_id)
    except Exception as exc:
        return [f"the approval ledger for {run_id} could not be read: {exc}"]
    taken = [e for e in entries if e.get("gate") == gate
             and e.get("verdict") == "approve"]
    if not taken:
        return [f"{run_id} has no live {gate} approval - a learned change is a "
                f"change, and it takes the same gate every other change takes"]
    # "Was it approved" and "is it still what was approved" are the two
    # different questions `verify_artifacts_for` exists to keep apart. A gate
    # voided by an edited artifact is not an approval a learned change may
    # ride on.
    return [f"{gate} on {run_id} no longer speaks for what it approved: {p}"
            for entry in taken
            for p in approve.verify_artifacts_for(Path(target), run_id, entry)]


# ── the audit ───────────────────────────────────────────────────────────────


def history(target: Path) -> list[dict]:
    path = Path(target) / LOG
    if not path.is_file():
        return []
    rows, broken = [], 0
    for line in cli.jsonl_rows(path):
        if line.row is None:
            broken += 1
            continue
        rows.append(line.row)
    if broken:
        raise LearningError(
            f"{path}: {broken} unreadable row(s) - an audit with holes cannot "
            f"say what was applied, so it cannot say what to undo")
    return rows


def append_log(target: Path, row: dict) -> Path:
    path = Path(target) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def standing(rows: list[dict], proposal: str) -> dict | None:
    """The applied entry for this proposal that has NOT been undone.

    Read forward: an application followed by an undo followed by a second
    application is standing, and the last word is the one that counts.
    """
    live: dict | None = None
    for row in rows:
        if row.get("proposal") != proposal:
            continue
        if row.get("action") == "applied":
            live = row
        elif row.get("action") == "undone":
            live = None
    return live


# ── applying and reversing ──────────────────────────────────────────────────


def render(before: str, key: str, add: list[str]) -> str:
    """The file's new text: `key` gains the entries, in the restricted YAML the
    stdlib reader understands. An existing declaration is EXTENDED rather than
    replaced, because a learned rule must never quietly drop one somebody
    wrote.

    **Item 129 - it did not do that, and this docstring's own promise is what
    it broke.** The old walk matched `line.startswith(f"{key}:")` and parsed it
    with `policy.parse`, which reads the INLINE spelling alone. A block-form
    declaration therefore yielded `existing = []` while its `- "x"` rows, which
    that walk never matched, survived into `kept` - so writing one new entry
    deleted every existing suppression AND left its items dangling under the
    previous key. Nothing downstream refused the wreck: `profile_limits`
    answers `[]` and the readers answer normally on it, so findings a human
    deliberately silenced start firing again with no audit row, which is the
    invisibility `unbacked` exists to prevent.

    **The one reader, not a second grammar.** `policy.list_from_text` is
    `profile_list`'s own body, so what this function believes is already
    declared is by construction what `loop.py` and `unbacked` will read back.

    **And it REFUSES rather than guessing.** A declaration that is present and
    is not a list cannot be extended without dropping it, so this raises
    instead - item 117's rule, in the one place that WRITES the governing
    document rather than reading it.
    """
    from tools.sdlc import policy as guardrails

    lines = before.splitlines()
    # **Item 146: ASKED, not restated.** This walked the lines itself and item
    # 129's claim that the writer and the readers cannot disagree was true of
    # what a declaration SAYS and false of where it SITS. It stopped at a
    # comment between two items - leaving the rows below orphaned at top level,
    # so the file was no longer valid YAML - and it could not see a space
    # before the colon at all, so a SECOND declaration of the same key was
    # appended and `ProfileError` then refused the profile at all 67 of the
    # reader's call sites. A learned rule bricked the document it was written
    # into. There is one locator now and it is the readers' own.
    declared = guardrails.declared_lines(lines, key)
    existing = guardrails.list_from_text(before, key)
    if declared and existing is None:
        raise LearningError(
            f"{key} is already declared in this profile and is not a list, so "
            f"extending it would drop what is there - fix the declaration by "
            f"hand, or remove it, before a learned rule is applied to it")

    existing = [str(v) for v in (existing or [])]
    merged = existing + [a for a in add if a not in existing]
    rendered = ", ".join(f'"{v}"' for v in merged)
    kept = [line for i, line in enumerate(lines) if i not in declared]
    body = "\n".join(kept).rstrip("\n")
    return f"{body}\n{key}: [{rendered}]\n"


def apply(target: Path, proposal: dict, run_id: str, by: str,
          skip_gates: bool = False) -> dict:
    """Write the change and log it. Refuses unless both gates are satisfied."""
    target = Path(target)
    if not skip_gates:
        blocked = corpus_gate(target) + gate_taken(target, run_id)
        if blocked:
            raise LearningError("; ".join(blocked))
    path = target / proposal["path"]
    if not path.is_file():
        raise LearningError(f"{path} does not exist, so there is nothing to "
                            f"change and nothing to put back")
    before = path.read_text(encoding="utf-8")
    if standing(history(target), proposal["id"]):
        raise LearningError(f"{proposal['id']} is already applied and standing - "
                            f"applying it twice would record a change that "
                            f"changed nothing and an undo that reverts the wrong "
                            f"bytes")
    after = render(before, proposal["change"]["key"], proposal["change"]["add"])
    path.write_text(after, encoding="utf-8", newline="\n")
    row = {"schema_version": SCHEMA_VERSION, "action": "applied", "at": _now(),
           "by": by, "run": run_id, "proposal": proposal["id"],
           "finding": proposal["finding"], "home": proposal["home"],
           "path": proposal["path"], "why": proposal["why"],
           "evidence": proposal["evidence"],
           "before_sha": digest(before), "after_sha": digest(after),
           # The prior text, so an undo restores bytes rather than guessing at
           # them. These are small declaration files; a patch would be smaller
           # and would need a patch engine to reverse, which is a second thing
           # to be wrong.
           "before": before}
    append_log(target, row)
    return row


def undo(target: Path, proposal: str, by: str) -> dict:
    """Put the prior bytes back, and APPEND the reversal.

    The audit must show the application and the reversal both. An undo that
    deleted its own application would leave a record showing neither, which is
    the opposite of what an audit is for.
    """
    target = Path(target)
    rows = history(target)
    applied = standing(rows, proposal)
    if applied is None:
        raise LearningError(f"{proposal} is not applied and standing, so there "
                            f"is nothing to undo - the audit is the authority "
                            f"on that, not the file's current contents")
    path = target / applied["path"]
    now = path.read_text(encoding="utf-8") if path.is_file() else ""
    drifted = digest(now) != applied.get("after_sha")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(applied["before"], encoding="utf-8", newline="\n")
    row = {"schema_version": SCHEMA_VERSION, "action": "undone", "at": _now(),
           "by": by, "proposal": proposal, "run": applied.get("run", ""),
           "finding": applied.get("finding", ""), "path": applied["path"],
           "restored_sha": applied.get("before_sha", ""),
           # Recorded, never refused. Somebody editing the file by hand after a
           # learned change landed is ordinary; silently overwriting their work
           # without saying so is not.
           "drifted": drifted,
           "found_sha": digest(now)}
    append_log(target, row)
    return row


def unbacked(target: Path) -> list[str]:
    """Suppressions in the profile that no standing audit entry accounts for.

    **The hole this closes is one the gates cannot.** `learning.py` gates the
    learned path twice, but `profile.yml` is not in `policy.yml`'s
    `protected_paths` and must not be: a run legitimately edits its own profile
    — emptying `known_failures` when the failure is fixed is an ordinary act.
    So an agent could write `review_suppress` by hand and silence a finding
    without a corpus check, a gate, or a line in the audit.

    Protecting the file is the wrong fix and would break the legitimate case.
    Making the difference VISIBLE is the right one: a suppression with an audit
    entry behind it was gated, and one without it was not. Neither is refused
    here — a human may have written one deliberately — but *"nobody can tell
    which"* stops being true.
    """
    from tools.review import loop as review

    declared = review.suppress_of(Path(target))
    rows = history(Path(target))
    backed = {row.get("finding") for row in rows
              if row.get("action") == "applied"
              and standing(rows, row.get("proposal", "")) is not None}
    return [f"{SUPPRESS_KEY} names {name!r} and no standing entry in {LOG} "
            f"accounts for it - it was written by hand rather than learned, so "
            f"it passed neither the corpus check nor a gate. That may be "
            f"deliberate; it should not be invisible"
            for name in declared if name not in backed]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--floor", type=int, default=record.GROUP_FLOOR, metavar="N")
    ap.add_argument("--apply", metavar="PROPOSAL_ID")
    ap.add_argument("--run", metavar="ID", help="the run whose G3 approves it")
    ap.add_argument("--by", metavar="WHO", default="")
    ap.add_argument("--undo", metavar="PROPOSAL_ID")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="suppressions the audit does not account for")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()

    if args.audit:
        owed = unbacked(target)
        for line in owed:
            print(f"  !! {line}")
        if not owed:
            print(f"every {SUPPRESS_KEY} entry is accounted for in {LOG}")
        return 1 if owed else 0

    if args.history:
        rows = history(target)
        if args.json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            print(f"{len(rows)} entr(ies) in {LOG}")
            for row in rows:
                mark = "+" if row["action"] == "applied" else "-"
                print(f"  {mark} {row['at']}  {row['action']:<8} "
                      f"{row.get('proposal', '?')}  {row.get('finding', '')}"
                      + ("  [drifted]" if row.get("drifted") else ""))
        return 0

    if args.undo:
        row = undo(target, args.undo, args.by or "unattributed")
        print(f"undone: {row['proposal']} - {row['path']} restored to "
              f"{row['restored_sha'][:19]}"
              + ("  (it had drifted since; the change is recorded)"
                 if row["drifted"] else ""))
        return 0

    proposals = propose(target, floor=args.floor)

    if args.apply:
        wanted = [p for p in proposals if p["id"] == args.apply]
        if not wanted:
            print(f"refused: no current proposal has id {args.apply} - "
                  f"{len(proposals)} proposal(s) stand", file=sys.stderr)
            return 2
        if not args.run:
            print("refused: --apply needs --run, because a learned change takes "
                  "the same G3 every other change takes", file=sys.stderr)
            return 2
        row = apply(target, wanted[0], args.run, args.by or "unattributed")
        print(f"applied: {row['proposal']} -> {row['path']} ({row['home']})")
        return 0

    if args.json:
        print(json.dumps(proposals, indent=2, sort_keys=True))
    else:
        print(f"{len(proposals)} proposal(s) at floor {args.floor}")
        for p in proposals:
            print(f"  {p['id']}  [{p['home']}]  {p['finding']}")
            print(f"      {p['why']}")
    return 1 if proposals else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which must never read as "nothing to
    learn" - the rule `defect.py` and `adr.py` both state."""
    cli.use_utf8()
    return cli.door(_main, argv, "the learning loop", 3)


if __name__ == "__main__":
    raise SystemExit(main())
