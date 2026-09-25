"""The thin vertical slice — item 41: one requirement walked from P0 to G4.

Every piece of the pipeline existed and nothing had ever driven them together
in one run. That is the gap this closes, and it closed a real hole on the way:
**a hard gate could be walked past**. `approve.py` refused a yes about work
that did not exist, `validate.py` refused a merge with no approval behind it,
and the halt itself — the thing the lifecycle is named for — was a paragraph in
an SOP that the phase machine did not enforce. `stage.halted_at` is that
sentence made mechanical, and this walk is its proof.

**Every gate is proven twice.** At each one the slice first tries to move on
*without* the approval and requires a refusal; only then does it approve and
proceed. A walk that only ever takes the happy path proves the pipeline runs,
which is not the same as proving the gates hold — and the gates are the product.

*Every* gate, including the last one, and that took a second pass. The loop
tries the move when the run LEAVES the phase a gate stands at, so a workspace
whose `phases[]` stops at `triage` — a shape this module names as legal below —
never left P7, took G4 unopposed, and printed GREEN, because the verdict was
written not to ask about the last gate. The attempt for that gate is made
against the next phase of the SUPERSET instead: the workspace does not run it,
and what comes back refuses by naming the GATE rather than the phase, which is
the same proof every other gate gets.

**It runs against any workspace.** The task line named three components of the
author's own estate; a test pinned to one machine's repositories cannot run in
CI and cannot be the M4 evidence. So the slice takes `--target` and
`--components`, the suite points it at the synthetic fixture, and pointing it
at a real estate is the same command with a different argument.

**It writes only inside the run folder and the worktrees**, so a slice over a
real workspace leaves the developer's checkouts as it found them. What it
cannot do is write the phase artifacts' *contents*: there is no model here.
Each phase's artifact is a stub naming what a runner would have put in it, and
the slice says so in its report — this proves the MACHINERY, never the agent.
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
from tools.sdlc import intake as intake_mod  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import validate as gate  # noqa: E402
from tools.sdlc import worktree as trees  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
REPORT = "slice.json"

#: The walk ends at the merge gate. P8 onward publishes and deploys, which a
#: rehearsal has no business doing to anybody's remote. DERIVED from
#: `policy.MERGE_GATE` so "the merge gate" is named in exactly one place.
LAST_GATE = guardrails.MERGE_GATE


class SliceError(RuntimeError):
    """The walk could not continue, and stopping is the honest answer."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stub(phase: str, run_id: str, components: list[str]) -> str:
    """What a phase's artifact says when no agent wrote it.

    Named a stub in the file itself, because an artifact that looks like real
    output is how a rehearsal gets mistaken for a run. Item 29's rule, applied
    to artifacts instead of to green rows.
    """
    s = stages.by_phase(phase)
    return (f"# {phase} — {s.name} (SLICE STUB)\n\n"
            f"Written by `slice.py` for run `{run_id}`. **No agent produced this.**\n"
            f"A real {phase} artifact carries what the SOP at\n"
            f"`sdlc/stages/{phase}-{s.name}.md` asks for; this one exists so the\n"
            f"phase contract has something to verify and the gate has something\n"
            f"to stand on.\n\n"
            f"Components in this slice: {', '.join(components) or '(none)'}\n")


def deliver(target: Path, run_id: str, phase: str, components: list[str]) -> list[str]:
    """Write and record what the phase owes. Asks the contract; invents nothing."""
    folder = ledger.run_dir(target, run_id)
    written = []
    for rel in stages.by_phase(phase).produces:
        path = folder / rel.rstrip("/")
        if rel.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            for name in components or ["work"]:
                (path / f"{name}.md").write_text(stub(phase, run_id, [name]),
                                                 encoding="utf-8", newline="\n")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # LF on every platform (batch-2 review, F4): these bytes are PINNED next,
            # and the run folder is now committed byte for byte, so a Windows walk
            # froze CRLF into the record where a Linux walk wrote LF.
            path.write_text(stub(phase, run_id, components), encoding="utf-8",
                            newline="\n")
        ledger.record_artifact(target, run_id, rel)
        written.append(rel)
    return written


def hard_here(target: Path) -> list[str]:
    """The RECONCILED hard gates (T548): `policy.hard_gates` alone iterates the
    literal GATES, so it could never hold an `extra_gates` stop, and the walk
    then never took one - it refused at the step instead, unable to finish."""
    return stages.resolved_hard_gates(target)


def try_to_skip(target: Path, run_id: str, phase: str, nxt: str,
                gate_id: str = "") -> str:
    """Attempt to leave a gated phase with no approval, and REQUIRE a refusal.

    This is the assertion the whole item exists for, and it is made by the
    walk rather than only by the suite: an operator running the slice over
    their own workspace should watch each gate refuse before it is taken.
    """
    try:
        stages.advance(target, run_id, nxt)
    except stages.StageError as exc:
        return str(exc)
    raise SliceError(
        f"the run moved from {phase} to {nxt} with {gate_id or 'its gate'} "
        f"unapproved - the gate is skippable, which is the one thing this walk is "
        f"here to disprove")


def walk(target: Path, components: list[str], approver: str,
         title: str = "a thin vertical slice", verbose: bool = True) -> dict:
    """P0 to G4, gate by gate, through the real CLIs' own functions."""
    target = Path(target).resolve()
    log: list[dict] = []

    def say(text: str) -> None:
        log.append({"at": _now(), "note": text})
        if verbose:
            print(text)

    active = [s.phase for s in stages.active(target)]
    # Where each gate STANDS in this workspace's pipeline, read once (T548). The
    # walk read `Stage.gate`, so a re-anchored builtin or an `extra_gates` stop
    # was never taken, and the walk refused at that step without finishing.
    standing = stages.gates_by_phase(target)
    # Asked of the pipeline, not of `triage` by name (review D-2): a workspace
    # that deletes `triage` re-anchors the merge gate onto the step before, and
    # the old "does not run the triage phase, so G4 never stands anywhere" was
    # then untrue - the case re-anchoring exists for.
    carriers = [p for p in active if LAST_GATE in standing.get(p, [])]
    if not carriers:
        raise SliceError(f"no active phase carries {LAST_GATE}, so this workspace "
                         f"cannot reach a merge decision - active: "
                         f"{', '.join(active)}")
    hard = hard_here(target)
    say(f"regime: hard gates {', '.join(hard) or '(none)'}; phases "
        f"{', '.join(active)}")

    # ── P0, through the real intake adapter ─────────────────────────────────
    spec = (f"title: {title}\n\n"
            f"WHEN the slice runs THE SYSTEM SHALL walk P0 to {LAST_GATE}, "
            f"stopping at every hard gate on the way.")
    record = intake_mod.open_run(target, spec, kind="requirement",
                                 src_kind="slice", src_name="slice.py",
                                 allow_dirty=True)
    run_id = record["run_id"]
    say(f"P0 intake: run {run_id} opened, {stages.by_phase('P0').produces[0]} recorded")

    made = trees.create(target, run_id, components or None)
    for name, entry in sorted(made["components"].items()):
        say(f"  {name}: {entry['mode']} on {entry['branch']}")

    stopped: list[dict] = []
    for i, phase in enumerate(active):
        if phase == "P0":
            continue
        previous = active[i - 1]
        # Leaving the previous phase: its artifacts, then EVERY hard gate that
        # stands at its end - a builtin and an extra stop are two stops, and
        # the move is refused while either is untaken.
        for gate_id in standing.get(previous, []):
            if gate_id not in hard:
                continue
            refusal = try_to_skip(target, run_id, previous, phase, gate_id)
            stopped.append({"gate": gate_id, "phase": previous, "refused": refusal})
            say(f"  {gate_id} REFUSED the move: {refusal.splitlines()[0]}")
            entry = approvals.record(target, run_id, gate_id, "approve",
                                     approver, notes="slice walk")
            say(f"  {gate_id} approved by {approver} (entry {entry['hash'][:12]})")
        stages.advance(target, run_id, phase, note="slice walk")
        deliver(target, run_id, phase, list(made["components"]))
        say(f"{phase} {stages.by_phase(phase).name}: "
            f"{', '.join(stages.by_phase(phase).produces)} recorded")

    # ── the last active step ────────────────────────────────────────────────
    # The loop proves a gate by LEAVING its step, and never leaves the last one,
    # so every hard gate standing there is proven here - the merge gate when
    # `phases[]` stops at it, and an `extra_gates` stop beside it or at the end
    # of the lifecycle (review D-1). Exempting them let the slice print GREEN
    # over a hard gate nobody took, or stay RED for good over one it never tried.
    last = active[-1]
    beyond = stages.PHASES[stages.PHASES.index(last) + 1:]
    for gate_id in standing.get(last, []):
        if gate_id not in hard or gate_id in {s["gate"] for s in stopped}:
            continue
        if beyond:
            # Against the phase that follows in the SUPERSET rather than in
            # this workspace's `phases[]`: `advance` weighs the gates of the
            # phase being LEFT, so the refusal names them, and stops naming
            # each one the moment its approval is recorded.
            refusal = try_to_skip(target, run_id, last, beyond[0], gate_id)
        else:
            # Nothing follows the end of the lifecycle, so no move can be
            # attempted - but a gate that holds the run is one `halted_at`
            # names, which is the same proof without the move.
            named = [h for h in stages.halted_at(target, run_id, last) if gate_id in h]
            if not named:
                raise SliceError(
                    f"{gate_id} stands at the end of {last} and the run is not held "
                    f"there with it unapproved - the gate does not halt, which is the "
                    f"one thing this walk is here to disprove")
            refusal = named[0]
        stopped.append({"gate": gate_id, "phase": last, "refused": refusal})
        say(f"  {gate_id} REFUSED the move: {refusal.splitlines()[0]}")
        if gate_id != LAST_GATE:     # the merge gate is taken just below
            entry = approvals.record(target, run_id, gate_id, "approve", approver,
                                     notes="slice walk")
            say(f"  {gate_id} approved by {approver} (entry {entry['hash'][:12]})")
    ok, why = approvals.gate_state(target, run_id, LAST_GATE)
    if not ok:
        entry = approvals.record(target, run_id, LAST_GATE, "approve", approver,
                                 notes="slice walk")
        say(f"{LAST_GATE} approved by {approver} (entry {entry['hash'][:12]})")
        ok, why = approvals.gate_state(target, run_id, LAST_GATE)
    if not ok:
        raise SliceError(f"{LAST_GATE} is not satisfied at the end of the walk: {why}")

    problems = approvals.verify_chain(target, run_id)
    branch = made["components"][sorted(made["components"])[0]]["branch"]
    merge = gate.merge_ready(target, run_id)
    say(f"ledger: {len(approvals.read_chain(target, run_id))} entr(ies), "
        f"{'intact' if not problems else str(len(problems)) + ' problem(s)'}")
    say(f"merge readiness on {branch}: "
        f"{'ready' if not merge else '; '.join(merge)}")

    report = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "at": _now(),
        "target": str(target), "components": sorted(made["components"]),
        "hard_gates": hard, "phases": active, "gates_standing": standing,
        "gates_refused_before_approval": stopped,
        "chain": problems, "merge_ready": merge,
        "artifacts": sorted(ledger.load(target, run_id).get("artifacts", {})),
        "log": log,
        "honesty": ("Artifacts are STUBS written by this walk, not agent output. "
                    "A green slice proves the machinery holds a run together and "
                    "that its gates refuse; it proves nothing about an agent."),
    }
    out = ledger.run_dir(target, run_id) / REPORT
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    return report


def verdict(report: dict) -> list[str]:
    """Why this slice did not pass. Empty is the only clean answer."""
    problems = list(report["chain"]) + list(report["merge_ready"])
    # The hard gates the walk had to stop at: every one standing at a step on
    # its path, the last step included - the walk proves those too, by a move
    # past it or, at the end of the lifecycle, by `halted_at` (T548; review D-1).
    standing = report.get("gates_standing")
    if standing is None:
        # A report written before T548 carries no map: read each step's own
        # gate, as the walk that wrote it did.
        standing = {p: [stages.by_phase(p).gate] for p in report["phases"]
                    if stages.by_phase(p).gate}
    on_the_way = {g for p in report["phases"] for g in standing.get(p, [])}
    expected = [g for g in report["hard_gates"] if g in on_the_way | {LAST_GATE}]
    refused = {s["gate"] for s in report["gates_refused_before_approval"]}
    for g in expected:
        if g not in refused:
            problems.append(f"{g} is hard here and the walk never had to stop at it - "
                            f"a gate that does not refuse is not a gate")
    if not report["artifacts"]:
        problems.append("the run pinned no artifacts, so every gate stood on nothing")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the workspace to walk a slice through")
    ap.add_argument("--component", action="append", default=[], metavar="NAME",
                    help="limit the slice to these components (default: all)")
    ap.add_argument("--approver", default="", metavar="WHO",
                    help="who takes the gates (default: the repository's git identity)")
    ap.add_argument("--title", default="a thin vertical slice", metavar="TEXT")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()

    try:
        approver = approvals.who(target, args.approver)
        if not approver:
            print("!! no approver: pass --approver, or set git config user.email in "
                  "the target - a gate taken by nobody is not taken")
            return 2
        report = walk(target, args.component, approver, args.title,
                      verbose=not (args.quiet or args.json))
        problems = verdict(report)
        if args.json:
            print(json.dumps({**report, "problems": problems}, indent=2, sort_keys=True))
        else:
            for p in problems:
                print(f"!! {p}")
            print(f"slice {'GREEN' if not problems else 'RED'} - run "
                  f"{report['run_id']}, {len(report['gates_refused_before_approval'])} "
                  f"gate(s) refused before they were taken")
            print(report["honesty"])
        return 1 if problems else 0
    except (SliceError, stages.StageError, trees.WorktreeError,
            guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
