"""P9 — deploy, and never before a human has said yes. Item 46.

The lifecycle marks P9 with **G5**, and G5 is the only gate that repeats: *"a
fresh sign-off every time a deploy costs something"*. Every other gate approves
a change once. This one approves an ACT, and the act has a bill attached — so
the approval is about this deploy, of this build, to this place, and never about
deploys in general.

**Nothing here runs without that yes.** `deploy_command` is executed by exactly
one function, and it refuses unless G5 stands for this run with its artifacts
unmoved. That is `approve.py`'s answer, not a second opinion: the same
`live_entries` and `verify_artifacts_for` the merge gate and the learning loop
already use.

**A blank `deploy_command` is not a deployment that succeeded.** The profile
says `blank = no deploy phase`, and a phase that ran nothing must say so rather
than report a clean exit. This is the shape item 40's SCA gate wore for months
and item 47 met again at P8: *nothing ran* and *nothing failed* are identical
output unless something insists they differ.

**What is deployed is recorded, because a deploy is the one act that changes
something outside the repository.** `90-deploy.json` carries the command, the
exit code, when it ran, and who approved it - so P10 can verify a deployment
that actually happened rather than one somebody remembers ordering.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1
PHASE = "P9"

#: The gate standing at the end of this phase. Hard in every regime: the halt
#: rule is not a dial, and a deploy is the act it exists for.
GATE = "G5"

#: What a deploy attempt leaves behind. Asked for rather than spelled - the name
#: has one home, which the duplicate-declaration guard now enforces.
RESULT = stages.DEPLOY_RESULT


class DeployError(RuntimeError):
    """The deploy could not be attempted, which is never a deploy that worked."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def command_of(target: Path, component: str = "") -> tuple[str, str]:
    """`(command, source)` for this deploy, or `("", "")` when none is declared.

    Blank is a DECLARATION that this repository has no deploy phase, and the
    caller must say so out loud rather than treat an empty command as one that
    ran.
    """
    from tools.sdlc import suite as engine

    try:
        every = guardrails.components(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        raise DeployError(f"the profile could not be read: {exc}") from None
    comp = None
    for c in every:
        if not component or c.get("name") == component:
            comp = c
            break
    own = str((comp or {}).get("deploy_command", "") or "").strip()
    if own:
        return own, "component"
    top = str(guardrails.profile_scalar(Path(target), "deploy_command") or "").strip()
    return (top, "top-level") if top else ("", "")


def gate_problems(target: Path, run_id: str) -> list[str]:
    """Why G5 does not permit a deploy of this run right now.

    `approve.py` owns what an approval is - whether the chain holds, whose
    verdict is current, and whether the approved bytes have moved. A deploy
    riding on a gate whose artifacts changed would be a yes about a different
    build.
    """
    from tools.sdlc import approve

    try:
        entries = approve.live_entries(Path(target), run_id)
    except Exception as exc:
        return [f"the approval ledger for {run_id} could not be read: {exc}"]
    taken = [e for e in entries
             if e.get("gate") == GATE and e.get("verdict") == "approve"]
    if not taken:
        return [f"{run_id} has no live {GATE} approval - a deploy costs "
                f"something, and this gate is the record of who accepted that "
                f"cost for THIS build"]
    return [f"{GATE} on {run_id} no longer speaks for what it approved: {p}"
            for entry in taken
            for p in approve.verify_artifacts_for(Path(target), run_id, entry)]


def recorded(target: Path, run_id: str) -> dict | None:
    """What this run recorded about its deploy, or None if it never attempted."""
    path = ledger.run_dir(Path(target), run_id) / RESULT
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeployError(f"{path} could not be read: {exc}") from None
    return found if isinstance(found, dict) else None


def run(target: Path, run_id: str, component: str = "",
        timeout: int = 1800) -> dict:
    """Execute the deploy. **Refuses first, deploys second, records always.**

    The recording happens whether the command succeeded or not: a failed deploy
    is the state P10 most needs to know about, and a phase that only wrote
    evidence on success would hide exactly the case somebody has to act on.
    """
    target = Path(target)
    if blocked := gate_problems(target, run_id):
        raise DeployError("; ".join(blocked))
    command, source = command_of(target, component)
    if not command:
        raise DeployError(
            "no deploy_command is declared, so there is nothing to run - a "
            "phase that executed nothing has not deployed, and reporting a "
            "clean exit for it would be a green nobody earned")
    # **The RUN'S TREE, not the repository root.** Since item 37 a run's change
    # never lives in the root: it is on the branch, in the worktree. Deploying
    # from the root builds the BASE and reports it as the change - measured on
    # the first real deploy, where a config change of 15000 to 30000 was
    # deployed as 15000 and recorded `deployed: true`. Exactly the defect
    # `6a00bfc` fixed for P6's review, one phase further on and costing more:
    # a review of the wrong tree misreports, a DEPLOY of the wrong tree ships.
    where = _worktree_for(target, run_id, component)
    started = _now()
    t0 = time.perf_counter()
    # **What a hung deploy printed is kept.** `run_bounded` hands back the
    # partial streams `TimeoutExpired` carries; the branch this replaced
    # overwrote both with "", so the one record P10 reads about a deploy
    # nobody watched finish held its verdict and none of its evidence.
    code, out, err, note = cli.run_bounded(
        command, shell=True, cwd=str(where), timeout=timeout,
        timed_out="the deploy did not finish within {timeout}s",
        could_not_start="the deploy could not be started: {error}")
    err = "\n".join(part for part in (err, note) if part)
    row = {
        "schema_version": SCHEMA_VERSION, "run": run_id, "at": started,
        "seconds": round(time.perf_counter() - t0, 1),
        "command": command, "command_source": source,
        # Recorded, because "which tree did this deploy" is the question the
        # first real deploy could not answer about itself.
        "where": str(where),
        # None when the command could not be run to completion. Not 0, and not
        # 1: "could not look" is its own answer here as everywhere else.
        "exit_code": code,
        "deployed": code == 0,
        "stdout": out[-8000:], "stderr": err[-8000:],
        "approved_by": _approver(target, run_id),
    }
    path = ledger.run_dir(target, run_id) / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return row


def _worktree_for(target: Path, run_id: str, component: str = "") -> str:
    """The tree this run's change lives in, or the root when it has none.

    A component denied a tree by `vcs.deny_raw_git`, and a run that never made
    one, both resolve to the main checkout - which is the truth in each case
    rather than a default, because that is where their change actually is.
    """
    from tools.sdlc import worktree as trees

    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
    except (trees.WorktreeError, OSError):
        return str(target)
    entry = homes.get(component) if component else None
    if entry is None and len(homes) == 1:
        entry = next(iter(homes.values()))
    if not isinstance(entry, dict):
        return str(target)
    return str(entry.get("home") or "").strip() or str(target)


def _approver(target: Path, run_id: str) -> str:
    from tools.sdlc import approve

    try:
        for entry in approve.live_entries(Path(target), run_id):
            if entry.get("gate") == GATE and entry.get("verdict") == "approve":
                return str(entry.get("approver") or "")
    except Exception:
        return ""
    return ""


def obligations(target: Path, run_id: str) -> list[str]:
    """What P9 owes, for `stage.verify` through the CONDITIONAL_JUDGE seam."""
    try:
        found = recorded(Path(target), run_id)
    except DeployError as exc:
        return [str(exc)]
    try:
        at = str(ledger.load(Path(target), run_id).get("phase") or "")
    except Exception:
        return []
    if at != PHASE:
        return []
    # **Owed only once G5 STANDS**, and the circularity is why. G5 sits at the
    # END of P9 and authorises the deploy, so the deploy record is produced
    # AFTER the gate, not before it. Making it a precondition of the gate
    # deadlocked both: `approve.record` calls `stage.verify`, verify asked this
    # judge, and this judge demanded the very artifact the approval exists to
    # permit. A gate cannot require the thing it authorises.
    if gate_problems(Path(target), run_id):
        return []
    command, _ = command_of(Path(target), "")
    if not command:
        # A repository that declares no deploy is not one whose deploy failed.
        # The phase is inactive here and `stage.active` already says so; this
        # judge does not invent an obligation for it.
        return []
    if found is None:
        return [f"{PHASE} has recorded no deploy ({RESULT}) - a deploy nobody "
                f"recorded is one P10 cannot verify and nobody can roll back "
                f"(tools/sdlc/deploy.py --target <repo> --run <id> --deploy)"]
    if found.get("exit_code") is None:
        return [f"the deploy did not run to completion: "
                f"{found.get('stderr', 'no reason recorded')[:200]} - which is "
                f"not the same as a deploy that failed, and neither is a "
                f"deploy that worked"]
    if not found.get("deployed"):
        return [f"the deploy exited {found.get('exit_code')} - the change is "
                f"not out, and P10 has nothing healthy to verify"]
    return []


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", required=True, metavar="ID")
    ap.add_argument("--component", default="", metavar="NAME")
    ap.add_argument("--deploy", action="store_true",
                    help="run the deploy command; without it nothing executes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    command, source = command_of(target, args.component)
    blocked = gate_problems(target, args.run)

    if not args.deploy:
        found = {"command": command, "command_source": source,
                 "gate": GATE, "refused": blocked,
                 "recorded": recorded(target, args.run)}
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True, default=str))
        else:
            print(f"deploy command: {command or '(none declared)'}"
                  + (f"  [{source}]" if source else ""))
            print(f"{GATE}: " + ("stands" if not blocked else "REFUSED"))
            for line in blocked:
                print(f"  !! {line}")
            print("  --deploy runs it; nothing was executed")
        return 1 if (blocked or not command) else 0

    row = run(target, args.run, args.component)
    if args.json:
        print(json.dumps(row, indent=2, sort_keys=True))
    else:
        print(f"deploy: exit {row['exit_code']} in {row['seconds']}s "
              f"({'deployed' if row['deployed'] else 'NOT deployed'})")
        print(f"  approved by: {row['approved_by'] or '(unattributed)'}")
    return 0 if row["deployed"] else 1


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which must never read as a deploy that
    worked."""
    cli.use_utf8()
    return cli.door(_main, argv, "the deploy phase", 3)


if __name__ == "__main__":
    raise SystemExit(main())
