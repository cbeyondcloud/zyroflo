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

from tools.sdlc import approve as approvals  # noqa: E402
from tools.sdlc import intake as intake_mod  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import validate as gate  # noqa: E402
from tools.sdlc import worktree as trees  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
REPORT = "slice.json"

#: The walk ends at the merge gate. P8 onward publishes and deploys, which a
#: rehearsal has no business doing to anybody's remote.
LAST_GATE = "G4"


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
                                                 encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(stub(phase, run_id, components), encoding="utf-8")
        ledger.record_artifact(target, run_id, rel)
        written.append(rel)
    return written


def gate_at(phase: str) -> str:
    return stages.by_phase(phase).gate


def hard_here(target: Path) -> list[str]:
    rules = guardrails.load(target)
    return guardrails.hard_gates(rules, guardrails.profile_dials(target),
                                 guardrails.gate_overrides(target))


def try_to_skip(target: Path, run_id: str, phase: str, nxt: str) -> str:
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
        f"the run moved from {phase} to {nxt} with {gate_at(phase)} unapproved - "
        f"the gate is skippable, which is the one thing this walk is here to "
        f"disprove")


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
    if "P7" not in active:
        raise SliceError(f"{target} does not run the triage phase, so {LAST_GATE} "
                         f"never stands anywhere - active: {', '.join(active)}")
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
        # Leaving the previous phase: its artifacts, then its gate.
        if gate_at(previous) and gate_at(previous) in hard:
            refusal = try_to_skip(target, run_id, previous, phase)
            stopped.append({"gate": gate_at(previous), "phase": previous,
                            "refused": refusal})
            say(f"  {gate_at(previous)} REFUSED the move: {refusal.splitlines()[0]}")
            entry = approvals.record(target, run_id, gate_at(previous), "approve",
                                     approver, notes="slice walk")
            say(f"  {gate_at(previous)} approved by {approver} "
                f"(entry {entry['hash'][:12]})")
        stages.advance(target, run_id, phase, note="slice walk")
        deliver(target, run_id, phase, list(made["components"]))
        say(f"{phase} {stages.by_phase(phase).name}: "
            f"{', '.join(stages.by_phase(phase).produces)} recorded")

    # ── the merge gate ──────────────────────────────────────────────────────
    # It is normally taken inside the loop above, when the run leaves the phase
    # it stands at. It is taken HERE only when that phase is the last active
    # one, so the loop never had to leave it — a workspace whose `phases[]`
    # stops at `triage` is a legal shape and must still reach a merge decision.
    if LAST_GATE not in {gate_at(p) for p in active}:
        raise SliceError(f"no active phase carries {LAST_GATE}, so this workspace "
                         f"cannot reach a merge decision - active: "
                         f"{', '.join(active)}")
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
        "hard_gates": hard, "phases": active,
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
                   encoding="utf-8")
    return report


def verdict(report: dict) -> list[str]:
    """Why this slice did not pass. Empty is the only clean answer."""
    problems = list(report["chain"]) + list(report["merge_ready"])
    expected = [g for g in report["hard_gates"]
                if g in {gate_at(p) for p in report["phases"] if gate_at(p)}
                and g != LAST_GATE]
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
