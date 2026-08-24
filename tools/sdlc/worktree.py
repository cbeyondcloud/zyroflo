"""The parallel-run model — item 37: a run works in its own trees, never yours.

Item 35 shipped the WALL — `confinement()` refuses a write outside
`<worktrees_root>/<run-id>` — and nothing had ever created the thing it guards.
Agents were told to work in a worktree by prose in an SOP, and prose is not a
directory. This module is the mechanism: one branch and one linked worktree per
touched component, a base commit recorded at P4 start so every later diff is
against a fixed point, and removal at closure.

Three properties are the item, and each is a way concurrency goes wrong:

**A run never touches the developer's checkout.** Their tree stays theirs while
any number of runs proceed, which is what makes "parallel" true rather than
aspirational. The one exception is stated rather than worked around: a
component with `deny_raw_git` gets **no worktree at all**, because a worktree
carries only the primary `.git` and a repository with a second one silently
loses half its state. That component is recorded as `in-place` with the reason,
not skipped in silence.

**The branch pattern is shared, not duplicated.** `branch_pattern` is one
string that the worktree model keys on to CREATE a branch and the CI ledger
gate keys on to decide which branch is a run's. Before this, the gate hardcoded
`sdlc/` — so a repository that renamed its branches would have kept its merge
gate installed, green, and silently not asking the merge question at all. Both
sides now call `policy.run_of_branch`, and a test holds them together.

**Overlap is advisory and says so.** Two runs touching one component are
flagged at G1 so a human can sequence them. There is no lock: the design's own
ruling is optimistic concurrency, with rebase-before-merge as the enforcement
that actually holds — the same model the hosting platform uses. A lock service
would be a new single point of failure guarding a race that rebase already
resolves.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import intake as intake_mod  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1
RECORD = "worktrees.json"

#: How a component's work is being done. `worktree` is the model; `in-place` is
#: the honest fallback for a repository raw git may not be run against.
MODES = ("worktree", "in-place")


class WorktreeError(RuntimeError):
    """A tree that cannot be made, moved, or trusted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(where: Path, *args: str, check: bool = True) -> str:
    """One git call, with the failure text kept. `check=False` returns "" for a
    non-zero exit, which several callers want as "the question has no answer"
    rather than as an error."""
    try:
        proc = subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"git {' '.join(args)} in {where}: {exc}") from None
    if proc.returncode != 0:
        if not check:
            return ""
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        raise WorktreeError(f"git {' '.join(args)} in {where} failed: "
                            + (tail[-1] if tail else f"exit {proc.returncode}"))
    return proc.stdout.strip()


def is_repo(path: Path) -> bool:
    return bool(git(path, "rev-parse", "--git-dir", check=False)) if path.is_dir() else False


# ── where a run's trees live ─────────────────────────────────────────────────


def root_of(policy: dict, target: Path) -> Path:
    """The worktrees root, from the constitution and never a constant — the
    plan is explicit that it comes from the developer's own configuration."""
    return Path(target) / policy["worktrees_root"]


def home(policy: dict, target: Path, run_id: str, name: str) -> Path:
    """Where one component's tree for one run lives.

    `<worktrees_root>/<run-id>/<component>` — deliberately the same shape
    `policy.confinement` allows, so the wall and the thing it guards cannot
    disagree about the address.
    """
    return root_of(policy, target) / run_id / name


#: Written into the worktrees root the first time one is made. A directory whose
#: own `.gitignore` is `*` ignores everything in it INCLUDING that file, so git
#: stops seeing the directory at all.
SELF_IGNORE = "# Written by worktree.py. A run's trees are not the repository's content.\n*\n"


def claim_root(policy: dict, target: Path) -> Path:
    """Make the worktrees root, and make git blind to it.

    Without this, the FIRST worktree a single-repo target creates dirties that
    repository permanently: `.sdlc-worktrees/` sits inside the tree it is about,
    so intake's clean-tree check refuses every later run and the developer's own
    `git status` is noisy forever. Measured, on the two-runs-on-one-repo case
    item 42 exists to test — run one worked and run two could never open.

    A self-ignoring directory rather than a line in the adopter's root
    `.gitignore`: that file is theirs, a tool that edits it will eventually edit
    it wrongly, and negation rules there behave differently depending on what
    else the repository already ignores. This works regardless.
    """
    root = root_of(policy, target)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".gitignore"
    if not marker.is_file():
        marker.write_text(SELF_IGNORE, encoding="utf-8", newline="\n")
    return root


def record_path(target: Path, run_id: str) -> Path:
    return ledger.run_dir(target, run_id) / RECORD


def load_record(target: Path, run_id: str) -> dict:
    path = record_path(target, run_id)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "components": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WorktreeError(f"{path} is not readable JSON ({exc}) - the record of "
                            f"where a run's work lives cannot be guessed at") from None
    if not isinstance(data, dict):
        raise WorktreeError(f"{path} is not an object")
    data.setdefault("components", {})
    return data


def save_record(target: Path, run_id: str, data: dict) -> None:
    path = record_path(target, run_id)
    if not path.parent.is_dir():
        raise WorktreeError(f"no run folder for {run_id} at {path.parent}")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ── creating ─────────────────────────────────────────────────────────────────


def plan(target: Path, run_id: str, names: list[str] | None = None) -> list[dict]:
    """What `create` would do, per component, without doing any of it.

    Every refusal is collected rather than raised at the first one: an operator
    fixing a workspace should need one round trip. The same list is what
    `create` executes, so `--dry-run` is the plan and not a description of it.
    """
    policy = guardrails.load(target)
    every = guardrails.components(target)
    wanted = [c for c in every if names is None or c["name"] in names]
    if names is not None and (missing := sorted(set(names) - {c["name"] for c in every})):
        raise WorktreeError(f"no component named {', '.join(map(repr, missing))} - one of "
                            + ", ".join(repr(c["name"]) for c in every))
    out: list[dict] = []
    for comp in wanted:
        step: dict = {"name": comp["name"], "path": comp.get("path") or ".",
                      "role": comp.get("role", "authoritative"), "problems": []}
        step["problems"] += guardrails.vcs_problems(policy, comp)
        vcs = comp.get("vcs") or {}
        pattern = guardrails.branch_pattern(policy, vcs)
        step["branch"] = guardrails.branch_for(pattern, run_id)
        step["pattern"] = pattern
        where = (Path(target) / step["path"]).resolve()
        step["where"] = str(where)
        if vcs.get("deny_raw_git"):
            # Stated, not skipped. A worktree carries only the primary `.git`, so
            # a repository with a second one would lose it — and losing it
            # quietly is how an open-core split publishes to the wrong place.
            step["mode"] = "in-place"
            step["why"] = ("vcs.deny_raw_git is set: a worktree carries only the "
                           "primary .git, so this component works in its own checkout"
                           + (f" ({vcs['notes']})" if vcs.get("notes") else ""))
            out.append(step)
            continue
        step["mode"] = "worktree"
        step["home"] = str(home(policy, Path(target), run_id, comp["name"]))
        if not where.is_dir():
            step["problems"].append(f"{step['path']} is not a directory")
        elif not is_repo(where):
            step["problems"].append(f"{step['path']} is not a git repository - there "
                                    f"is no branch to make and no base to record")
        else:
            # `intake.dirty_paths`, not a second `git status` — the run ledger is
            # written INTO a single-repo target by intake itself, so a check that
            # does not exclude it refuses every run on the harness's headline case.
            dirty, why = intake_mod.dirty_paths(where)
            if dirty is None:
                step["problems"].append(f"{step['path']}: {why}")
            elif dirty:
                step["problems"].append(
                    f"{step['path']} has {len(dirty)} uncommitted change(s) - the "
                    f"base commit would describe a tree nobody can reconstruct, and "
                    f"the run's diffs would carry someone else's work: "
                    + ", ".join(dirty[:5]))
            step["base"] = git(where, "rev-parse", "HEAD", check=False)
            if not step["base"]:
                step["problems"].append(f"{step['path']} has no commits yet")
            if git(where, "rev-parse", "--verify", "--quiet", step["branch"], check=False):
                step["problems"].append(f"branch {step['branch']} already exists in "
                                        f"{step['path']}")
        out.append(step)
    return out


def create(target: Path, run_id: str, names: list[str] | None = None,
           dry_run: bool = False) -> dict:
    """Make this run's branches and trees. All or nothing.

    A partial creation is worse than none: half the components isolated and half
    not is a run whose diffs come from two different worlds. So every problem is
    found first, and the first one refuses the whole thing.
    """
    steps = plan(target, run_id, names)
    if problems := [p for s in steps for p in s["problems"]]:
        raise WorktreeError(f"{len(problems)} problem(s), and none of the trees were "
                            f"made: " + "; ".join(problems))
    if not steps:
        raise WorktreeError("no components to work in")
    record = load_record(target, run_id)
    if dry_run:
        return {"components": {s["name"]: s for s in steps}, "dry_run": True}
    policy = guardrails.load(target)
    for s in steps:
        if s["mode"] == "worktree":
            spot = Path(s["home"])
            claim_root(policy, Path(target))
            spot.parent.mkdir(parents=True, exist_ok=True)
            git(Path(s["where"]), "worktree", "add", "-b", s["branch"], str(spot),
                s["base"])
        record["components"][s["name"]] = {
            "mode": s["mode"], "path": s["path"], "branch": s["branch"],
            "pattern": s["pattern"], "base": s.get("base", ""),
            "home": s.get("home", s["where"]), "role": s["role"],
            "why": s.get("why", ""), "at": _now(),
        }
    record["run_id"] = run_id
    save_record(target, run_id, record)
    return record


def base_of(target: Path, run_id: str, name: str) -> str:
    """The commit this run started from, which every later diff is against.

    Recorded at creation rather than derived later: a merge base recomputed
    after upstream has moved answers a different question than the one P4 asked.
    """
    entry = load_record(target, run_id)["components"].get(name)
    if entry is None:
        raise WorktreeError(f"run {run_id} has no tree recorded for {name!r}")
    return entry["base"]


# ── removing ─────────────────────────────────────────────────────────────────


def remove(target: Path, run_id: str, force: bool = False) -> list[str]:
    """Take this run's trees away at closure.

    Refuses a tree with uncommitted work in it. An agent's unfinished edit is
    still the only copy of that work, and `git worktree remove` deletes the
    directory — so the default is to stop and say which tree, not to tidy.
    """
    record = load_record(target, run_id)
    done: list[str] = []
    holding: list[str] = []
    for name, entry in sorted(record["components"].items()):
        if entry["mode"] != "worktree":
            continue
        spot = Path(entry["home"])
        if not spot.is_dir():
            done.append(f"{name}: already gone")
            continue
        if not force and (dirty := git(spot, "status", "--porcelain", check=False)):
            holding.append(f"{name}: {len(dirty.splitlines())} uncommitted change(s) in "
                           f"{spot} - removing it would delete the only copy "
                           f"(--force overrides)")
            continue
        args = ["worktree", "remove", str(spot)] + (["--force"] if force else [])
        git(Path(target) / entry["path"], *args)
        entry["removed_at"] = _now()
        done.append(f"{name}: removed {spot}")
    if holding:
        save_record(target, run_id, record)
        raise WorktreeError("; ".join(holding))
    save_record(target, run_id, record)
    return done


# ── two runs at once ─────────────────────────────────────────────────────────


def active_runs(target: Path, skip: str = "") -> list[str]:
    """Run ids with a folder under `runs/`, excluding archived and terminal ones.

    Enumerates FOLDERS rather than parsing every `state.json`, for item 44's
    recorded reason: an advisory check that dies on one malformed file names no
    overlap at all.
    """
    out = []
    base = ledger.runs_dir(target)
    if not base.is_dir():
        return out
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name == ledger.ARCHIVE_NAME or entry.name == skip:
            continue
        try:
            state = ledger.load(target, entry.name)
        except Exception:
            out.append(entry.name)
            continue
        if state.get("status") not in ledger.TERMINAL:
            out.append(entry.name)
    return out


def overlap(target: Path, run_id: str) -> list[str]:
    """Other live runs working in the same components as this one.

    ADVISORY, and the wording says so. There is no lock by design: the ruling on
    file is optimistic concurrency with rebase-before-merge as the enforcement,
    because a lock service is a new thing to be down and it guards a race that
    the rebase resolves anyway. What this buys is that a human sees the
    collision at G1, while sequencing the two runs is still cheap.
    """
    mine = set(load_record(target, run_id)["components"])
    if not mine:
        return []
    findings = []
    for other in active_runs(target, skip=run_id):
        try:
            theirs = set(load_record(target, other)["components"])
        except WorktreeError:
            continue
        if shared := sorted(mine & theirs):
            findings.append(f"run {other} is also working in {', '.join(shared)} - "
                            f"advisory: sequence them, or expect a rebase before merge")
    return findings


# ── catching up with upstream ────────────────────────────────────────────────


def upstream_commits(target: Path, run_id: str, name: str, onto: str) -> list[str]:
    """What landed on `onto` since this run's base — the delta an impact
    re-check is computed over. Empty means the rebase is a no-op and there is
    nothing new to re-scope."""
    entry = load_record(target, run_id)["components"].get(name)
    if entry is None:
        raise WorktreeError(f"run {run_id} has no tree recorded for {name!r}")
    where = Path(entry["home"]) if entry["mode"] == "worktree" else \
        Path(target) / entry["path"]
    log = git(where, "log", "--oneline", f"{entry['base']}..{onto}", check=False)
    return [line for line in log.splitlines() if line.strip()]


def rebase(target: Path, run_id: str, name: str, onto: str) -> dict:
    """Move this run's work onto the latest upstream, before P8.

    **A conflict is aborted, not left in the tree.** A repository stopped
    mid-rebase is a state no later step in this lifecycle knows how to read, and
    an agent that finds one will improvise. The conflicting paths are reported
    instead, with what they mean: resolving them is NEW code that nobody has
    reviewed, so it re-enters at G3 rather than sliding into the merge.
    """
    entry = load_record(target, run_id)["components"].get(name)
    if entry is None:
        raise WorktreeError(f"run {run_id} has no tree recorded for {name!r}")
    if entry["mode"] != "worktree":
        why = entry.get("why") or "no reason recorded"
        raise WorktreeError(f"{name} works in place ({why}) - rebase it with the "
                            f"repository's own tooling")
    spot = Path(entry["home"])
    ahead = upstream_commits(target, run_id, name, onto)
    if not ahead:
        return {"name": name, "onto": onto, "upstream": [], "rebased": False,
                "conflicts": [], "note": "already on the latest upstream"}
    try:
        git(spot, "rebase", onto)
    except WorktreeError:
        conflicts = [line for line in
                     git(spot, "diff", "--name-only", "--diff-filter=U",
                         check=False).splitlines() if line.strip()]
        git(spot, "rebase", "--abort", check=False)
        return {"name": name, "onto": onto, "upstream": ahead, "rebased": False,
                "conflicts": conflicts,
                "note": ("the rebase was ABORTED and the tree left as it was. "
                         "Resolving these is new code nobody has reviewed, so it "
                         "re-enters at G3 - it does not slide into the merge")}
    # The base MOVES, and it has to. After a rebase the run's work sits on top of
    # `onto`, so the run's own diff is against `onto` and not against where it
    # started — leaving the old base recorded made `upstream_commits` report the
    # same commits forever, so the delta re-check never emptied and a caller
    # re-scoped the same arrivals on every pass. The base still does not follow
    # upstream on its own; it follows a rebase, which is the act of catching up.
    record = load_record(target, run_id)
    entry = record["components"][name]
    entry["previous_base"] = entry["base"]
    entry["base"] = git(spot, "rev-parse", onto, check=False) or entry["base"]
    entry["rebased_at"] = _now()
    entry["rebased_onto"] = onto
    save_record(target, run_id, record)
    return {"name": name, "onto": onto, "upstream": ahead, "rebased": True,
            "conflicts": [], "base": entry["base"],
            "note": (f"{len(ahead)} upstream commit(s) arrived - re-run the impact "
                     f"check over them before P8")}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR",
                    help="the workspace root the run lives in")
    ap.add_argument("--run", metavar="ID", help="the run id")
    ap.add_argument("--component", action="append", default=[], metavar="NAME",
                    help="limit to these components (default: all)")
    ap.add_argument("--create", action="store_true",
                    help="make this run's branches and trees")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --create, print the plan and change nothing")
    ap.add_argument("--list", action="store_true", help="this run's trees")
    ap.add_argument("--remove", action="store_true", help="take them away (P11)")
    ap.add_argument("--force", action="store_true",
                    help="with --remove, discard uncommitted work")
    ap.add_argument("--overlap", action="store_true",
                    help="other live runs in the same components (advisory)")
    ap.add_argument("--rebase", metavar="REF",
                    help="rebase --component onto REF, aborting on conflict")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    names = args.component or None

    try:
        if args.create:
            if not args.run:
                print("!! --create needs --run")
                return 2
            result = create(target, args.run, names, args.dry_run)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                for name, e in sorted(result["components"].items()):
                    where = e.get("home", e.get("where", ""))
                    print(f"  {name:<20} {e['mode']:<9} {e.get('branch', '')}  {where}")
                    if e.get("why"):
                        print(f"      {e['why']}")
                print("(plan only - nothing was made)" if args.dry_run else
                      f"{len(result['components'])} component(s) ready")
            return 0
        if args.list:
            if not args.run:
                print("!! --list needs --run")
                return 2
            record = load_record(target, args.run)
            if args.json:
                print(json.dumps(record, indent=2, sort_keys=True))
            else:
                for name, e in sorted(record["components"].items()):
                    gone = " (removed)" if e.get("removed_at") else ""
                    print(f"  {name:<20} {e['mode']:<9} {e['branch']}  "
                          f"{e['base'][:7]}  {e['home']}{gone}")
                if not record["components"]:
                    print("  (no trees recorded for this run)")
            return 0
        if args.remove:
            if not args.run:
                print("!! --remove needs --run")
                return 2
            for line in remove(target, args.run, args.force):
                print(f"  {line}")
            return 0
        if args.overlap:
            if not args.run:
                print("!! --overlap needs --run")
                return 2
            findings = overlap(target, args.run)
            for f in findings:
                print(f"  !! {f}")
            print(f"{len(findings)} overlap(s) - advisory" if findings
                  else "no other live run shares a component")
            return 1 if findings else 0
        if args.rebase:
            if not (args.run and names and len(names) == 1):
                print("!! --rebase needs --run and exactly one --component")
                return 2
            result = rebase(target, args.run, names[0], args.rebase)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"{result['name']} onto {result['onto']}: "
                      f"{len(result['upstream'])} upstream commit(s)")
                for c in result["conflicts"]:
                    print(f"  !! conflict: {c}")
                print(f"  {result['note']}")
            return 1 if result["conflicts"] else 0
    except (WorktreeError, guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
