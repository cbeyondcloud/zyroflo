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
#: The phase this module judges. Derived from the stage contract, not spelled.
PHASE = stages.by_name("deploy").phase

#: The gate standing at the end of this phase. Hard in every regime: the halt
#: rule is not a dial, and a deploy is the act it exists for. Derived from the
#: stage contract, not spelled, so the gate→phase binding has one home.
GATE = stages.gate_of(PHASE)

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
    if component and comp is None:
        # A mistyped --component matched nothing and fell through to the
        # top-level command - a deploy of the wrong thing, reported as the one
        # asked for (found beside T503).
        raise DeployError(f"no component named {component!r} - one of "
                          f"{', '.join(str(c.get('name')) for c in every) or '(none)'}")
    own = str((comp or {}).get("deploy_command", "") or "").strip()
    if own:
        return own, "component"
    top = str(guardrails.profile_scalar(Path(target), "deploy_command") or "").strip()
    return (top, "top-level") if top else ("", "")


def deploys_of(target: Path, run_id: str = "") -> tuple[bool, list[tuple[str, str, str]]]:
    """What a deploy of this run IS: `(by name?, [(component, command, source)])`.

    **One act** (`by name` False) where the workspace has one component, or
    where no component declares its own `deploy_command`: one command, run once,
    under the component name "" - exactly as before T503.

    **By name** otherwise (T503): each component THIS RUN CHANGED is its own
    deploy, with its own command or the top-level one it inherits - "blank =
    inherit" is the installer's rule for every per-component command - and a
    component with neither has nothing to deploy. Review B4-4: a deploy nobody's
    change needs is a cost G5 was not asked about.

    "Changed" is P5's own answer, `suite.changed_files`: a component whose tree
    differs from its recorded base, or whose difference could not be read
    (never assumed away, T502). Not the worktree record alone - P4 makes a tree
    for every component by default, so the record would owe them all. A run
    with no record cannot narrow the set, so every component is taken; a record
    nobody can read is refused, because which deploys are owed hangs on it.
    """
    try:
        every = guardrails.components(Path(target))
        top = str(guardrails.profile_scalar(Path(target), "deploy_command") or "").strip()
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        raise DeployError(f"the profile could not be read: {exc}") from None
    own = {str(c.get("name")): str(c.get("deploy_command", "") or "").strip()
           for c in every}
    if len(every) < 2 or not any(own.values()):
        command, source = command_of(target, "")
        return False, ([("", command, source)] if command else [])
    changed: set[str] | None = None          # None: nothing says, so all of them
    if run_id:
        from tools.sdlc import suite as engine
        from tools.sdlc import worktree as trees

        try:
            diffs, _notes, unread = engine.changed_files(Path(target), run_id)
        except (trees.WorktreeError, OSError) as exc:
            raise DeployError(f"which components this run changed could not be read "
                              f"({exc}), and the deploys it owes follow from them"
                              ) from None
        if diffs or unread:
            changed = {name for name, files in diffs.items() if files} | set(unread)
    units = []
    for name, command in own.items():
        if changed is not None and name not in changed:
            continue
        if command or top:
            units.append((name, command or top, "component" if command else "top-level"))
    return True, units


def needs_component(target: Path, run_id: str = "") -> str:
    """Why a deploy that names no --component cannot stand for this run, or ""
    when it can.

    **T503.** With no --component, `command_of` answered with the FIRST
    component's own command and `_worktree_for` with the workspace root, so a
    multi-component workspace deployed one component's command from the wrong
    tree, never deployed the others, and recorded `deployed: true`.
    """
    by_name, units = deploys_of(target, run_id)
    if not by_name:
        return ""
    if not units:
        return ("this workspace deploys its components one by one, and none of the "
                "components this run changed declares a deploy_command or inherits "
                "one - there is nothing of this run's to deploy")
    return (f"this workspace deploys its components one by one - deploy each with "
            f"--component NAME: {', '.join(name for name, _c, _s in units)}. With "
            f"none named there is no one command to run: each deploys with its own "
            f"command, or the top-level one it inherits, from its own tree")


def components_owed(target: Path, run_id: str, found: dict | None) -> list[str]:
    """What the record says is still owed, component by component.

    **T549.** The record is one file and each deploy rewrites it, so P9, P10 and
    close-out read the LAST deploy as the workspace's: deploying one of two
    components discharged P9, and a later success hid an earlier failure. The
    record keeps every named deploy under `components`, and this is the one
    reading of it that P9 and P10 share: every deploy recorded there must have
    worked, and in a workspace that deploys by name, every component this run
    changed must have one.
    """
    by_name, units = deploys_of(Path(target), run_id)
    done = (found or {}).get("components")
    done = done if isinstance(done, dict) else {}
    names = [name for name, _c, _s in units] if by_name else []
    owed = []
    for name in names + sorted(str(n) for n in done if n not in names):
        entry = done.get(name)
        if not isinstance(entry, dict):
            if name in names:
                owed.append(f"{name} has no recorded deploy - this workspace deploys "
                            f"its components one by one, and each one this run "
                            f"changed is owed (tools/sdlc/deploy.py --target <repo> "
                            f"--run <id> --deploy --component {name})")
        elif entry.get("exit_code") is None:
            owed.append(f"the deploy of {name} did not run to completion, which "
                        f"is neither a deploy that failed nor one that worked")
        elif not entry.get("deployed"):
            owed.append(f"the deploy of {name} exited {entry.get('exit_code')} - "
                        f"{name} is not out")
    return owed


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
    # Asked first: which deploy this is comes before whether it may run (T503).
    if not component and (why := needs_component(target, run_id)):
        raise DeployError(why)
    if blocked := gate_problems(target, run_id):
        raise DeployError("; ".join(blocked))
    command, source = command_of(target, component)
    if not command:
        raise DeployError(
            "no deploy_command is declared, so there is nothing to run - a "
            "phase that executed nothing has not deployed, and reporting a "
            "clean exit for it would be a green nobody earned")
    # T549: this run's earlier component deploys, read BEFORE anything runs. A
    # record nobody can read refuses here: rewriting it would drop the
    # components it holds, and P9 would owe them with nobody told why.
    carried: dict = {}
    if component:
        before = recorded(target, run_id)
        kept = (before or {}).get("components")
        carried = dict(kept) if isinstance(kept, dict) else {}
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
    row = {
        "schema_version": SCHEMA_VERSION, "run": run_id, "at": started,
        "seconds": round(time.perf_counter() - t0, 1),
        # Which component this deploy was ("" for a workspace-wide one), so the
        # record answers the question T503 showed it could not.
        "component": component,
        "command": command, "command_source": source,
        # Recorded, because "which tree did this deploy" is the question the
        # first real deploy could not answer about itself - RELATIVE to the run
        # root (item 120), so the committed record carries no machine path.
        # `where` above stays absolute for `cwd`; only the record is relativised.
        "where": record_where(target, where),
        # None when the command could not be run to completion. Not 0, and not
        # 1: "could not look" is its own answer here as everywhere else.
        "exit_code": code,
        "deployed": code == 0,
        # The note LEADS, because `obligations` below quotes the first 200
        # characters of this field: a deploy that printed more than that
        # before it hung would otherwise be reported without its reason.
        "stdout": out[-8000:], "stderr": cli.noted(note, err[-8000:]),
        "approved_by": _approver(target, run_id),
    }
    if component:
        carried[component] = {"at": started, "command": command,
                              "where": row["where"], "exit_code": code,
                              "deployed": code == 0}
        row["components"] = carried
    path = ledger.run_dir(target, run_id) / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return row


def record_where(target: Path, where) -> str:
    """`where` as it goes into the COMMITTED record: relative to the run root.

    Item 120 - an absolute machine path in a committed run folder is a private
    coordinate that means nothing in another checkout. Stored relative, readers
    resolve `target / where` against the LIVE workspace root; an OLD absolute
    record still reads because that division keeps an absolute right-hand side.
    A `where` that is not under `target` (a cross-drive tree, say) is left
    absolute rather than expressed with `..`, and target/<absolute> still
    resolves it - could-not-relativise is not a wrong path.
    """
    try:
        return Path(where).resolve().relative_to(Path(target).resolve()).as_posix()
    except (ValueError, OSError):
        return str(where)


def _worktree_for(target: Path, run_id: str, component: str = "") -> str:
    """The tree this run's change lives in, or the root when it has none.

    A component denied a tree by `vcs.deny_raw_git`, and a run that never made
    one, both resolve to the main checkout - which is the truth in each case
    rather than a default, because that is where their change actually is.

    **A NAMED component resolves to its own tree, or its own checkout - never to
    another component's** (review B4-2). "The only tree in the record" stands in
    for an unnamed deploy; for a named one it deployed somebody else's tree under
    this component's name, which is item 37's failure with a label on it. And a
    record that exists but cannot be read is refused rather than read as "no
    trees": the root is where the change is NOT, so deploying it ships the base.
    """
    from tools.sdlc import worktree as trees

    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
    except (trees.WorktreeError, OSError) as exc:
        raise DeployError(f"this run's worktree record could not be read ({exc}), so "
                          f"which tree holds its change is unknown") from None
    if component:
        entry = homes.get(component)
        home = str(entry.get("home") or "").strip() if isinstance(entry, dict) else ""
        return home or _checkout_of(target, component)
    entry = next(iter(homes.values())) if len(homes) == 1 else None
    if not isinstance(entry, dict):
        return str(target)
    return str(entry.get("home") or "").strip() or str(target)


def _checkout_of(target: Path, component: str) -> str:
    """Where a component lives in the main checkout: its declared `path`."""
    try:
        every = guardrails.components(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        raise DeployError(f"the profile could not be read: {exc}") from None
    for c in every:
        if c.get("name") == component:
            return str(Path(target) / (c.get("path") or "."))
    raise DeployError(f"no component named {component!r}")


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
        at = ledger.phase_of(Path(target), run_id)
    except Exception as exc:
        return [f"{PHASE} could not tell whether this run owes {RESULT}: {exc} "
                f"- a phase nobody could read is not a run that has not "
                f"reached {PHASE}"]
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
    try:
        # Asked of `deploys_of`, not of `command_of(target, "")`, which reads
        # only the FIRST component: a first component with no command of its own
        # answered "nothing to deploy" for a workspace whose second one deploys
        # (review B4-1).
        by_name, units = deploys_of(Path(target), run_id)
        if not units:
            # A repository that declares no deploy is not one whose deploy
            # failed, and neither is a run none of whose components deploys. The
            # phase is inactive, or this run has nothing in it to deploy; this
            # judge does not invent an obligation for either.
            return []
        if by_name:
            # T549: judged component by component. The top-level fields are only
            # the LAST deploy, which is not the workspace.
            return components_owed(Path(target), run_id, found)
    except DeployError as exc:
        return [str(exc)]
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
    # One act, but split into named deploys: the last one's success does not
    # speak for an earlier one that failed.
    try:
        return components_owed(Path(target), run_id, found)
    except DeployError as exc:
        return [str(exc)]


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
    if args.component:
        names = [str(c.get("name")) for c in guardrails.components(target)]
        if args.component not in names:
            print(f"refused: no component named {args.component!r} - one of "
                  f"{', '.join(names)}", file=sys.stderr)
            return 2
    by_name, units = deploys_of(target, args.run)
    # T503: which deploy this is, said on its own line - G5 can stand while the
    # deploy is still refused for naming no component.
    unnamed = "" if args.component else needs_component(target, args.run)
    command, source = ("", "") if unnamed else command_of(target, args.component)
    blocked = gate_problems(target, args.run)

    if not args.deploy:
        found = {"command": command, "command_source": source, "gate": GATE,
                 "by_name": [{"component": name, "command": cmd, "source": src}
                             for name, cmd, src in units] if by_name else [],
                 "refused": [unnamed, *blocked] if unnamed else blocked,
                 "recorded": recorded(target, args.run)}
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True, default=str))
        else:
            if unnamed:
                print("deploy commands, one per component this run changed:")
                for name, cmd, src in units:
                    print(f"  {name}: {cmd}  [{src}]")
                print(f"  !! {unnamed}")
            else:
                print(f"deploy command: {command or '(none declared)'}"
                      + (f"  [{source}]" if source else ""))
            print(f"{GATE}: " + ("stands" if not blocked else "REFUSED"))
            for line in blocked:
                print(f"  !! {line}")
            print("  --deploy runs it; nothing was executed")
        return 1 if (blocked or unnamed or not command) else 0

    if unnamed:
        # A missing --component is the caller's to fix, like any other refused
        # argument - not `3`, which says the deploy could not look.
        print(f"refused: {unnamed}", file=sys.stderr)
        return 2
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
