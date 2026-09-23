"""What is waiting for a human — item 32's other half.

`status.py` answers two questions and nothing else. *Whose turn is it?* — the
runs sitting at a gate, the gate they are sitting at, and how long they have
been there. And *does the record still hold?* — every run's approval chain
walked and every approved artifact re-hashed, exiting non-zero when any of it
fails, which is the signal item 44 will wire into CI so a tampered ledger is
unmergeable rather than merely detectable.

It reads; it never writes. A status command that could change a run's state
would be one more way for a gate to move without an entry in the ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import approve as approvals  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()


def standing_gates(target: Path) -> tuple[dict[str, list[str]], str]:
    """({phase: [gate ids that STAND there]}, why-not) from the RESOLVED pipeline.

    **T450b.** This was a static `PHASE_GATE` derived from the builtin
    `approve.GATE_PHASE`, so it knew neither an `extra_gates` stop (`G-test`)
    nor a builtin the profile had re-anchored (G1 at P1): a run halted at either
    was "waiting a gate", in the one command whose job is saying who a run is
    waiting on. A profile the resolver refuses gives `({}, the refusal)` — the
    caller says so once rather than naming gates it could not resolve.
    """
    try:
        pipe = stages.pipeline(target)
    except (stages.StageError, guardrails.PolicyError, ValueError) as exc:
        return {}, str(exc)
    at: dict[str, list[str]] = {}
    for g in pipe.gates:
        at.setdefault(g.after_phase, []).append(g.id)
    return at, ""


def _age(iso: str) -> str:
    """How long a run has been waiting, in the coarsest honest unit."""
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "?"
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def waiting_on(state: dict, standing: dict[str, list[str]],
               target: Path | None = None, run_id: str = "") -> str:
    """The gate this run is waiting at, or "" when nobody is being waited on.

    `standing` is `standing_gates(target)[0]`. Where two gates stand at one step
    (a builtin and an `extra_gates` stop), the ones not yet satisfied are named
    — given the run to ask about — and all of them otherwise.
    """
    if state.get("status") != "awaiting_approval":
        return ""
    gates = standing.get(state.get("phase", ""), [])
    if len(gates) > 1 and target is not None and run_id:
        open_ = [g for g in gates if not approvals.gate_state(target, run_id, g)[0]]
        gates = open_ or gates
    return ", ".join(gates) or "a gate"


def position(target: Path, run_id: str, state: dict) -> list[str]:
    """One run's place on the resolved pipeline, drawn by `stage.render_text`.

    Steps before the run's phase are `done`; its phase is `here` (or the
    terminal status: `closed`, `rejected`, `halted`); each gate carries its
    live verdict (`approved`, `rejected`) or `waiting` when the run stands at
    it. Loop-backs — the run moving to an EARLIER step — are read from the
    run's own history and counted beneath, so rework is visible, not inferred.
    """
    pipe = stages.pipeline(target)
    hard = set(stages.resolved_hard_gates(target))
    phase, status = state.get("phase", ""), state.get("status", "")
    order = [s.phase for s in pipe.steps]
    step_marks: dict[str, str] = {}
    notes: list[str] = []
    if status == "closed":
        step_marks = {p: "done" for p in order}
    elif phase in order:
        here = order.index(phase)
        step_marks = {p: "done" for p in order[:here]}
        step_marks[phase] = {"rejected": "rejected", "halted": "halted"}.get(status, "here")
    else:
        notes.append(f"   (the run is at {phase or '?'}, which this pipeline does not "
                     f"run - the profile changed after the run moved there)")
    gate_marks: dict[str, str] = {}
    for entry in approvals.live_entries(target, run_id):
        gate, verdict = entry.get("gate"), entry.get("verdict")
        if verdict in approvals.VERDICTS:
            gate_marks[gate] = "approved" if verdict == "approve" else "rejected"
    if status == "awaiting_approval":
        for g in pipe.gates:
            if g.after_phase == phase and gate_marks.get(g.id) != "approved":
                gate_marks[g.id] = "waiting"
    loops: dict[str, int] = {}
    prev = None
    for h in state.get("history") or []:
        cur = h.get("phase") if isinstance(h, dict) else None
        if (cur in stages.PHASES and prev in stages.PHASES
                and stages.PHASES.index(cur) < stages.PHASES.index(prev)):
            key = (f"{prev} {stages.by_phase(prev).name} -> "
                   f"{cur} {stages.by_phase(cur).name}")
            loops[key] = loops.get(key, 0) + 1
        if cur in stages.PHASES:
            prev = cur
    lines = stages.render_text(pipe, hard, step_marks, gate_marks) + notes
    if loops:
        lines.append("   loop-backs: " + "; ".join(
            f"{k} (x{n})" for k, n in loops.items()))
    return lines


#: What a field with no readable value renders as. A committed ledger is
#: hand-editable, so an entry can be valid JSON and a dict yet miss a key the
#: detail view once indexed directly - which raised KeyError and printed a
#: traceback in the one module whose whole job is showing the ledger back.
COULD_NOT_READ = "(could not read)"


def _shown(entry: dict, key: str) -> str:
    """One field of an entry for display, or `COULD_NOT_READ` when it is absent
    or null. Never raises: a reader that dies on the record it is showing back
    is worse than one that says which field it could not read."""
    value = entry.get(key)
    return COULD_NOT_READ if value is None else str(value)


def overview(target: Path, standing: dict[str, list[str]] | None = None) -> list[dict]:
    """Every run, with what it waits for and whether its ledger holds."""
    if standing is None:
        standing, _ = standing_gates(target)
    rows = []
    for row in ledger.list_runs(target):
        try:
            st = ledger.load(target, row["run_id"])
        except RuntimeError as exc:
            rows.append({**row, "waiting": "", "problems": [str(exc)], "entries": 0,
                         "updated_at": ""})
            continue
        problems = list(approvals.verify_chain(target, row["run_id"]))
        if row.get("kind") == "unreadable":
            # `list_runs` classified the record as damaged - e.g. a null phase
            # (T433) - yet `load` still returned a dict, so the RuntimeError
            # branch above did not fire. Surface the reason so `--verify` flags
            # it and the row prints as a named refusal rather than a bare "?".
            problems.insert(0, row.get("unreadable", "state record is unreadable"))
        chain = approvals.read_chain(target, row["run_id"])
        # The last verdict per gate; an earlier one the P6-to-P4 loop
        # superseded pinned bytes the loop existed to replace.
        for entry in approvals.live_entries(target, row["run_id"]):
            problems += approvals.verify_artifacts_for(target, row["run_id"], entry)
        problems += [f"artifact {p}" for p in
                     ledger.verify_artifacts(target, row["run_id"])]
        rows.append({**row, "waiting": waiting_on(st, standing, target, row["run_id"]),
                     "problems": problems,
                     "entries": len(chain), "updated_at": st.get("updated_at", "")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="What is waiting for a human, and whether the record still "
                    "holds (item 32).")
    ap.add_argument("--target", required=True, metavar="DIR")
    ap.add_argument("--run", metavar="RUN_ID", help="one run, in full")
    ap.add_argument("--awaiting", action="store_true",
                    help="only the runs waiting on a human")
    ap.add_argument("--verify", action="store_true",
                    help="exit 1 if any ledger or approved artifact fails")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2

    standing, unresolved = standing_gates(target)
    rows = overview(target, standing)
    # The relative location for messages, derived from the constant rather than
    # typed as a literal (T396, following T381's pattern).  `runs_dir(target)`
    # is always `target / RUNS_REL`, so the relative form is RUNS_REL itself;
    # archiving lives underneath it, so the label is correct for both active
    # and archived runs.
    rel = ledger.RUNS_REL.as_posix()
    if args.run:
        rows = [r for r in rows if r["run_id"] == args.run]
        if not rows:
            print(f"!! no run {args.run} under {rel}")
            return 2
    if args.awaiting:
        rows = [r for r in rows if r["waiting"]]

    if unresolved and not args.as_json:
        # Said once, never silently: a gate this cannot resolve is shown as
        # "a gate", and this is why.
        print(f"!! gates shown unnamed - the pipeline could not be resolved: {unresolved}")
    if args.as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    elif not rows:
        print("nothing is waiting on a human" if args.awaiting
              else f"no runs under {rel}")
    else:
        for r in rows:
            if r.get("kind") == "unreadable":
                # A damaged record is a ROW, not a crash: name it and move on,
                # the way state.py --list does. Its display columns would be a
                # null the `:<4`/`:<26` spec cannot format (T433), so this path
                # never reaches the fixed-width line below.
                why = r.get("unreadable") or "could not be read"
                print(f"!! {r['run_id']}  could not be read: {why}")
                continue
            flag = "!!" if r["problems"] else ("->" if r["waiting"] else "  ")
            wait = f"waiting {r['waiting']} ({_age(r['updated_at'])})" if r["waiting"] \
                else r["status"]
            print(f"{flag} {r['run_id']}  {r['phase']:<4} {wait:<26} "
                  f"{r['entries']} approval(s)  {r['title']}")
            for p in r["problems"]:
                print(f"     !! {p}")

    if args.run and not args.as_json:
        # T450b: the run's place on the same picture `stage.py --list` draws.
        if not unresolved and rows and rows[0].get("kind") != "unreadable":
            try:
                for line in position(target, args.run, ledger.load(target, args.run)):
                    print(line)
            except (RuntimeError, stages.StageError, guardrails.PolicyError) as exc:
                print(f"   (position not drawn: {exc})")
        entries = approvals.read_chain(target, args.run)
        for i, e in enumerate(entries, 1):
            if "_unreadable" in e:
                print(f"   {i}. (unreadable line)")
                continue
            # Item 135's THIRD site, and the filing named only two — found by
            # sweeping rather than by trusting the report, which is the item's
            # own lesson. This is the worst of the three: it prints the WHOLE
            # chain, so every rejection in a run's history read `G1 rejectd`,
            # in the one module whose entire job is showing the ledger back.
            #
            # Item 139: read every field through `_shown`, never index it. A
            # readable-but-incomplete entry (valid JSON, a dict, a key missing)
            # used to raise KeyError HERE and print a traceback, right after
            # verify_chain had correctly named the break above.
            verdict = e.get("verdict")
            said = approvals.said(str(verdict)) if verdict is not None \
                else COULD_NOT_READ
            print(f"   {i}. {_shown(e, 'gate')} {said} "
                  f"by {_shown(e, 'approver')} at {_shown(e, 'at')}"
                  + (" [forced]" if e.get("forced") else ""))
            if e.get("notes"):
                print(f"      {e['notes']}")

    if args.verify:
        return 1 if any(r["problems"] for r in rows) else 0
    # A survey that could read no run at all is not a clean survey - the twin of
    # state.py --list's exit 2 (T433). A mix still exits 0 so one damaged record
    # never hides the healthy runs the operator came to see.
    if rows and all(r.get("kind") == "unreadable" for r in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
