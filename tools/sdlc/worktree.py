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
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import intake as intake_mod  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1
RECORD = "worktrees.json"

#: How a component's work is being done. `worktree` is the model; `in-place` is
#: the honest fallback for a repository raw git may not be run against.
MODES = ("worktree", "in-place")


class WorktreeError(RuntimeError):
    """A tree that cannot be made, moved, or trusted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(where: Path, *args: str, check: bool = True, raw: bool = False) -> str:
    """One git call, with the failure text kept. `check=False` returns "" for a
    non-zero exit, which several callers want as "the question has no answer"
    rather than as an error. `raw=True` returns the output as git wrote it, for
    `-z` output: neither stripped, which took a leading space off the first path
    (review B11-10), nor read in text mode, whose newline translation turns a CR
    in a path into a LF (review B11-13)."""
    text = {} if raw else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        proc = subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                              timeout=300, **text)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"git {' '.join(args)} in {where}: {exc}") from None
    out, err = ((proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))
                if raw else (proc.stdout, proc.stderr))
    if proc.returncode != 0:
        if not check:
            return ""
        tail = (err or out).strip().splitlines()
        raise WorktreeError(f"git {' '.join(args)} in {where} failed: "
                            + (tail[-1] if tail else f"exit {proc.returncode}"))
    return out if raw else out.strip()


def is_repo(path: Path) -> bool:
    return bool(git(path, "rev-parse", "--git-dir", check=False)) if path.is_dir() else False


# ── where a run's trees live ─────────────────────────────────────────────────


def root_of(policy: dict, target: Path) -> Path:
    """The worktrees root, from the constitution and never a constant — the
    plan is explicit that it comes from the developer's own configuration."""
    return Path(target) / policy["worktrees_root"]


def own_trees(target: Path) -> str:
    """This run model's own directory inside the repository, repo-relative.

    A gate that scans the repository will walk it, because it IS inside the
    repository — `claim_root` puts it there on purpose, and the `.gitignore`
    it writes only stops GIT from seeing it. Any scanner told to ignore git's
    exclusions walks straight in.

    Returned as a repo-relative POSIX prefix rather than a path, because the
    callers are matching what a scanner PRINTED and a scanner prints strings.
    `""` when the question cannot be answered — a target with no readable
    policy — so a caller filters nothing rather than filtering everything.
    """
    try:
        rules = guardrails.load(Path(target))
    except (guardrails.PolicyError, OSError, ValueError):
        return ""
    raw = str(rules.get("worktrees_root") or "").strip()
    return raw.replace("\\", "/").strip("/")


def is_own_tree(target: Path, path: str) -> bool:
    """Is this repo-relative path inside a tree the harness made for itself?

    The question two scanning gates were not asking. A worktree is a full copy
    of the repository, so everything in it is a SECOND report of a fact that is
    already reported at its real path — and at an address that does not exist
    in the repository the reviewer is looking at.

    Measured on a real repository: 36 of 72 manifests and 386 of 789 secret
    findings, with one run open; three-fold with two. The count moves when a
    run is opened or closed, with nothing about the code having changed.
    """
    prefix = own_trees(target)
    if not prefix:
        return False
    clean = str(path).replace("\\", "/")
    # NOT `lstrip("./")`. That takes a character SET, so it eats the leading dot
    # of `.sdlc-worktrees` and the prefix never matches - the filter silently
    # passes everything it exists to catch. Its own test caught it.
    if clean.startswith("./"):
        clean = clean[2:]
    return clean == prefix or clean.startswith(prefix + "/")


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

#: The one line that does the work. Anything else in the file is the adopter's.
IGNORE_RULE = "*"

#: Appended when the rule has gone missing, and it says why to whoever meets it.
RESTORED_IGNORE = (
    "# Restored by worktree.py: without the rule below, the repository this\n"
    "# directory sits in is dirty for ever and every later run is refused.\n"
    "# Your other lines above are kept.\n"
    "*\n")


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

    **The RULE is asserted on every call, not merely the file's existence.**
    Item 156: writing it only when absent left an edited file unrepaired, so the
    very failure above became permanent - `?? .sdlc-worktrees/` for ever, and a
    re-run never fixed it. The missing rule is APPENDED rather than the file
    overwritten, because somebody may legitimately have extended it, and the
    appended text says why it is back.
    """
    root = root_of(policy, target)
    # T497: the `*` rule below is written into `root/.gitignore`. A root that IS
    # the repository - or is not inside it - would put that rule in a
    # .gitignore the repository relies on. `policy.validate` refuses such a
    # value by its text; this refuses what it resolves to, before anything is
    # written, for a policy that never passed through there.
    try:
        inside = root.resolve().relative_to(Path(target).resolve()) != Path(".")
    except ValueError:
        inside = False
    if not inside:
        raise WorktreeError(f"worktrees_root {policy.get('worktrees_root')!r} is not "
                            f"a directory inside the repository - the trees, and the "
                            f"ignore rule that hides them, need one of their own")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".gitignore"
    if not marker.is_file():
        marker.write_text(SELF_IGNORE, encoding="utf-8", newline="\n")
        return root
    text = marker.read_text(encoding="utf-8")
    # `rstrip`, and the asymmetry is git's, not a preference: git ignores
    # TRAILING whitespace on a pattern (unless a backslash quotes it) and does
    # NOT ignore leading whitespace, so `"   *   "` is a pattern for a name
    # beginning with three spaces - not the rule. Compared with `strip()` it
    # counted as the rule already present, nothing was appended, and the target
    # read `?? .sdlc-worktrees/`: the failure this repair exists to fix,
    # surviving inside the repair.
    if not any(line.rstrip() == IGNORE_RULE for line in text.splitlines()):
        if text and not text.endswith("\n"):
            text += "\n"
        marker.write_text(text + RESTORED_IGNORE, encoding="utf-8", newline="\n")
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
        # T398: the relative record location, not the absolute path. The run
        # EXISTS (path.is_file above), so run_rel derives the correct location
        # for an archived run too, unlike a typed .sdlc/runs/<id> literal.
        rel = f"{ledger.run_rel(target, run_id)}/{RECORD}"
        raise WorktreeError(f"{rel} is not readable JSON ({exc}) - the record of "
                            f"where a run's work lives cannot be guessed at") from None
    if not isinstance(data, dict):
        raise WorktreeError(
            f"{ledger.run_rel(target, run_id)}/{RECORD} is not an object")
    data.setdefault("components", {})
    for entry in data["components"].values():
        if isinstance(entry, dict) and isinstance(entry.get("home"), str):
            entry["home"] = _live_home(target, entry["home"])
    return data


def save_record(target: Path, run_id: str, data: dict) -> None:
    path = record_path(target, run_id)
    if not path.parent.is_dir():
        # T398/T256: the relative ACTIVE location, not the absolute archive
        # fallback run_dir returns for a missing run - an operator must not be
        # sent to a directory that does not exist. Twin of stage.py:704/:819.
        raise WorktreeError(
            f"no run folder for {run_id} at {ledger.RUNS_REL.as_posix()}/{run_id}")
    stored = {**data, "components": {
        name: ({**entry, "home": _stored_home(target, entry["home"])}
               if isinstance(entry, dict) and isinstance(entry.get("home"), str)
               else entry)
        for name, entry in (data.get("components") or {}).items()}}
    # LF on every platform (batch-2 review, F4): the record is committed with the run.
    path.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def _stored_home(target: Path, home: str) -> str:
    """A tree's `home` as the COMMITTED record carries it: relative to the
    workspace (T498).

    The record travels with the run, and an absolute path is this machine's
    coordinate - meaningless in another checkout, and a private one in a
    committed file (item 120's rule, which `deploy.record_where` follows for the
    deploy record). A sibling repository is `../name` from the target; only a
    tree on another DRIVE, which no relative path can reach, is kept as written.
    The profile no longer spells that `..` itself: a component's `path` is
    relative to the workspace root, and `workspace_root` says where that is
    (T512). Every reader gets it back absolute from
    `load_record`, so none of them had to change.
    """
    if not home:
        return home
    try:
        rel = os.path.relpath(os.path.abspath(home), os.path.abspath(target))
    except ValueError:
        return home
    return Path(rel).as_posix()


def _live_home(target: Path, home: str) -> str:
    """The stored `home` resolved against the LIVE workspace root. An absolute
    one - a record from before T498, or a tree on another drive - is kept."""
    if not home or Path(home).is_absolute():
        return home
    return os.path.normpath(Path(target) / home)


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
        where = guardrails.component_dir(target, step["path"]).resolve()
        step["where"] = str(where)
        if vcs.get("deny_raw_git"):
            # Stated, not skipped. A worktree carries only the primary `.git`, so
            # a repository with a second one would lose it — and losing it
            # quietly is how an open-core split publishes to the wrong place.
            step["mode"] = "in-place"
            step["why"] = ("vcs.deny_raw_git is set: a worktree carries only the "
                           "primary .git, so this component works in its own checkout"
                           + (f" ({vcs['notes']})" if vcs.get("notes") else ""))
            # A base all the same (review B4-10). `deny_raw_git` forbids commit,
            # push and worktree - not a read - and with no base the component
            # could never be diffed: run at P5 on every run and owed a deploy at
            # P9 for ever, whatever the run changed.
            step["base"] = (git(where, "rev-parse", "HEAD", check=False)
                            if where.is_dir() else "")
            out.append(step)
            continue
        step["mode"] = "worktree"
        step["home"] = str(home(policy, Path(target), run_id, comp["name"]))
        # The DESTINATION is part of what a plan is about. `git worktree add`
        # refuses an occupied one, and until item 156 nothing looked - so that
        # refusal arrived half way through creation, with the components before
        # it already made. Validated here, the common case never gets that far.
        spot = Path(step["home"])
        if spot.exists() and (not spot.is_dir() or any(spot.iterdir())):
            step["problems"].append(
                f"{step['name']}: {step['home']} is not empty - git worktree add "
                f"refuses an occupied destination. A stopped run leaves one; "
                f"remove it, or use a different run id")
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


def _unwind(made: list[tuple[Path, Path, str]]) -> tuple[list[str], list[str]]:
    """Take back the trees a failed `create` had already made.

    What each line says is what actually happened, including a removal that
    itself failed: a tree left behind is the state the operator has to deal
    with, and hiding it inside a tidier message is how item 156 started.

    Returns (removed, stuck) rather than one list, because the CALLER's summary
    is different in the two cases and got it wrong while it could not tell them
    apart: a run with a tree still on disk is not a run with no trees.
    """
    removed: list[str] = []
    stuck: list[str] = []
    for where, spot, branch in reversed(made):
        try:
            git(where, "worktree", "remove", "--force", str(spot))
        except WorktreeError as exc:
            stuck.append(f"{spot} could NOT be removed ({exc}) - it is still "
                         f"there, with branch {branch}; remove it by hand")
            continue
        git(where, "branch", "-D", branch, check=False)
        removed.append(f"removed {spot} and branch {branch}")
    return removed, stuck


def create(target: Path, run_id: str, names: list[str] | None = None,
           dry_run: bool = False) -> dict:
    """Make this run's branches and trees. All or nothing.

    A partial creation is worse than none: half the components isolated and half
    not is a run whose diffs come from two different worlds. So every problem is
    found first, and the first one refuses the whole thing.

    **And a failure the plan could not foresee is unwound rather than left.**
    Validation closes the common case; it cannot close a race, a permission or a
    disk. Item 156 measured what the second component's failure used to leave:
    the first tree on disk with its branch registered in `git worktree list` and
    NO record written - so `remove` could not see it, and a re-run died at the
    branch-exists check with no way forward through the harness. Every tree made
    in this call is taken back before the error propagates, and the error names
    what it took - for ANY failure after the first tree exists, the record write
    included, because a raise one step outside the unwind leaves the same state.
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
    made: list[tuple[Path, Path, str]] = []
    # The guarded region is EVERYTHING after the first tree can exist, not just
    # the `worktree add`. The record write was one step outside it, and with no
    # run folder - a run id nobody opened, or a ledger removed under a live run
    # - `create` made both trees and both branches and then raised while writing
    # the record, unwinding nothing: exactly the stuck state this function's
    # unwind exists to prevent, reached one step past where it was watching.
    try:
        for s in steps:
            if s["mode"] == "worktree":
                spot = Path(s["home"])
                claim_root(policy, Path(target))
                spot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    git(Path(s["where"]), "worktree", "add", "-b", s["branch"],
                        str(spot), s["base"])
                except WorktreeError as exc:
                    raise WorktreeError(f"{s['name']}: {exc}") from None
                made.append((Path(s["where"]), spot, s["branch"]))
            record["components"][s["name"]] = {
                "mode": s["mode"], "path": s["path"], "branch": s["branch"],
                "pattern": s["pattern"], "base": s.get("base", ""),
                "home": s.get("home", s["where"]), "role": s["role"],
                "why": s.get("why", ""), "at": _now(),
            }
        record["run_id"] = run_id
        save_record(target, run_id, record)
    except WorktreeError as exc:
        removed, stuck = _unwind(made)
        took = "; ".join(removed + stuck) or "nothing had been made before it"
        # The end of this sentence is read by somebody deciding whether to go
        # looking, so it says which case they are in. "This run has no trees"
        # was printed even when a tree WAS on disk with its branch - and no
        # record was written, so `remove` cannot find it for them either.
        if stuck:
            end = (f". No record was written, so `remove` cannot find them: this "
                   f"run HAS {len(stuck)} tree(s) the harness could not clean, "
                   f"listed above. Take each one back by hand - `git worktree "
                   f"remove --force <path>` then `git branch -D <branch>` in the "
                   f"component - before re-running this run id")
        else:
            end = ". No record was written: this run has no trees."
        raise WorktreeError(f"{exc}. All or nothing, so {took}{end}") from None
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


def serving_from(target: Path, run_id: str, spot: Path) -> str:
    """Why this tree may still be in use, or "".

    **The harness knows a deploy ran from here, and that is enough.** It does
    NOT know a pid - `90-deploy.json` carries the command, the tree and the
    exit code and no process id, so a refusal naming one would be inventing it.
    What it can say is the true and actionable thing: run X deployed from this
    directory and the deploy reported success, so something may still be
    serving out of it.

    **Item 129: THREE states, because `deployed` is only two.** `deploy.py`
    records `exit_code: None` when the command could not be run to completion,
    under a comment saying could-not-look is its own answer here as everywhere
    else - and then writes `deployed: code == 0` on the next line, which folds
    that answer into False. Reading the folded boolean made a TIMED-OUT deploy
    indistinguishable from one that never started, and a timeout is the one
    shape a still-serving foreground process produces: `subprocess.run` gave up
    waiting, which is the opposite of knowing nothing is there.

    So `deployed` is left exactly as it is - five callers read it and it
    correctly means *the deploy reported success* - and the question is asked
    here instead:

    * exit 0 - it deployed, something may be serving. HOLD.
    * a real non-zero - the tool ran and reported failure. Removable, because
      refusing on every failed deploy makes close-out impossible, which is the
      shape that gets a check switched off.
    * None - nobody knows. HOLD, and say so in those words.

    **A deploy that could not START also records None, and it holds too.** That
    is deliberate and it is the cheaper mistake: `--force` costs a keystroke,
    while on the walk this item comes from, removing a live service's tree
    destroyed the very script that knew how to stop it. Telling the two apart
    needs `deploy.py` to record an OUTCOME rather than an exit code, which
    changes a schema five callers read - filed, not done. The trigger is
    somebody meeting the false hold often enough to say so.

    Read through `stage.DEPLOY_RESULT` rather than a second spelling of the
    name - item 94's rule, and this module is not in the ledger check's bundle,
    so the import costs nothing.
    """
    from tools.sdlc import stage as stages

    path = ledger.run_dir(Path(target), run_id) / stages.DEPLOY_RESULT
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(row, dict):
        return ""
    # T549: the top-level fields are only the LAST deploy. Each component a
    # workspace deployed by name is kept under `components`, and a tree an
    # EARLIER component deployed from may be serving just the same.
    kept = row.get("components")
    for entry in [row, *(kept.values() if isinstance(kept, dict) else ())]:
        if isinstance(entry, dict) and (why := _serving(target, entry, spot)):
            return why
    return ""


def _serving(target: Path, row: dict, spot: Path) -> str:
    """`serving_from`'s answer for one recorded deploy."""
    code = row.get("exit_code", 0)
    unknown = code is None
    if not row.get("deployed") and not unknown:
        return ""
    stored = str(row.get("where") or "").strip()
    if not stored:
        return ""
    # Resolved against the LIVE workspace root: `deploy.py` records `where`
    # relative to the run root (item 120), so the tree it deployed from is
    # `target / where`. An OLD absolute record still matches because that
    # division keeps an absolute right-hand side.
    where = Path(target) / stored
    try:
        same = where.resolve() == Path(spot).resolve()
    except OSError:
        same = str(where) == str(Path(spot))
    if not same:
        return ""
    command = row.get("command") or "the deploy command"
    if unknown:
        # Named separately, because the repair differs: a deploy that SUCCEEDED
        # is stopped by whoever knows how, while one nobody watched finish has
        # to be LOOKED at before anybody can say what is running.
        return (f"a deploy for this run started from {spot} (`{command}`) and "
                f"never reported an exit code, so whether anything is still "
                f"serving out of this tree is unknown - look before you delete "
                f"it, or pass --force. You do not undeploy at close-out")
    return (f"a deploy for this run ran from {spot} and reported success "
            f"(`{command}`), so something "
            f"may still be serving out of this tree - stop it first, or pass "
            f"--force. You do not undeploy at close-out")


def remove(target: Path, run_id: str, force: bool = False) -> list[str]:
    """Take this run's trees away at closure.

    **Every reason to refuse is collected BEFORE anything is deleted**, and
    that ordering is the item rather than a nicety. `git worktree remove` is
    not atomic: pointed at a tree holding an open file it deletes every tracked
    file it can, deregisters the worktree, and only then fails - so on the first
    whole-lifecycle walk it removed the run's own `deploy.py`, the script that
    knew how to stop the service still serving from that directory, and left the
    operator a live process in a tree git no longer believed in. A guard that
    destroys what it guards, which item 44's `emit_check` was the last instance
    of.

    Two reasons to refuse, both nameable in advance. Uncommitted work: an
    agent's unfinished edit is the only copy of it. And a deploy that ran from
    this tree: you do not undeploy at close-out, and the tree is a resource
    rather than a record, so leaving it is the cheaper mistake.

    A git failure that survives both checks is reported with what to do about
    it, and **what was already removed is SAVED before the error propagates** -
    a partial removal that loses its own record leaves the record disagreeing
    with the disk, which was measured rather than supposed.
    """
    record = load_record(target, run_id)
    done: list[str] = []
    holding: list[str] = []
    removable: list[tuple[str, dict, Path]] = []
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
        if not force and (why := serving_from(target, run_id, spot)):
            holding.append(f"{name}: {why}")
            continue
        removable.append((name, entry, spot))
    # NOTHING has been deleted yet. Every refusal is known, so the run stops
    # here with all of its trees intact rather than part-way through.
    if holding:
        save_record(target, run_id, record)
        raise WorktreeError("; ".join(holding))
    for name, entry, spot in removable:
        args = ["worktree", "remove", str(spot)] + (["--force"] if force else [])
        try:
            git(guardrails.component_dir(target, entry["path"]), *args)
        except WorktreeError as exc:
            save_record(target, run_id, record)
            raise WorktreeError(
                f"{name}: {spot} could not be removed ({exc}). git deletes what "
                f"it can before failing, so this tree may be PART removed - "
                f"check it before reusing it. A file held open by a running "
                f"process is the usual cause; stop whatever is using the tree, "
                f"then remove it by hand. "
                + (f"Removed before this: {', '.join(d for d in done)}."
                   if done else "Nothing was removed before this.")) from None
        entry["removed_at"] = _now()
        done.append(f"{name}: removed {spot}")
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
    mine, basis = components_of(target, run_id)
    if basis == "unknown":
        # **Could not look is not "no overlap"**, and until item 117 this could
        # not be reached: `policy.components` synthesised a component for an
        # unreadable profile rather than raising, so the `unknown` basis was a
        # guarded path with no live route to it. Now there is one, and folding
        # it into the empty answer would have this advisory reassure a person
        # precisely when it has nothing to reassure them with — item 100's
        # finding about this same function, arriving from the other side.
        return [f"the components of run {run_id} could not be determined - "
                f"neither a worktree record nor a readable profile - so no "
                f"overlap was checked. This is could-not-look, not a clear run"]
    if not mine:
        return []
    findings = []
    for other in active_runs(target, skip=run_id):
        theirs, _ = components_of(target, other)
        if shared := sorted(mine & theirs):
            findings.append(f"run {other} is also working in {', '.join(shared)} - "
                            f"advisory: sequence them, or expect a rebase before merge"
                            + ("" if basis == "record" else
                               f" (compared on the components the profile declares, "
                               f"because this run has not made its trees yet)"))
    return findings


def components_of(target: Path, run_id: str) -> tuple[set[str], str]:
    """The components a run works in, and how that was decided.

    **The record if it exists, the PROFILE otherwise, and the difference is
    said out loud.** `overlap`'s own docstring says it exists so *"a human sees
    the collision at G1, while sequencing the two runs is still cheap"* — and
    the record is written at P4, which is two phases AFTER G1. So for the whole
    window this check is for, it read an absent record as an empty component
    set and answered *no other live run shares a component*, every time.

    Measured on one run: 0 overlaps before `--create`, 2 immediately after, with
    nothing about the repository having changed in between.

    Before the trees exist, the honest answer is what the run COULD touch, which
    is what the profile declares. That is wider than the truth for a run whose
    scope turns out narrower, and wider is the right direction for an advisory:
    it is answered before anything has been built, when sequencing is still a
    conversation rather than a rebase.
    """
    try:
        recorded = set(load_record(target, run_id)["components"])
    except (WorktreeError, OSError):
        recorded = set()
    if recorded:
        return recorded, "record"
    try:
        return {c["name"] for c in guardrails.components(Path(target))}, "profile"
    except (guardrails.PolicyError, OSError, ValueError, KeyError):
        # An unreadable profile leaves this advisory unable to answer. It says
        # nothing rather than claiming no overlap, and `unknown` is the caller's
        # signal that the two are different answers.
        #
        # **`policy.components` DOES raise now, and this branch is why it was
        # kept.** Until item 117 it did not: unreadable YAML, an absent file and
        # an empty one all synthesised the single component named after the
        # directory, so the branch was unreachable through that path and was
        # held open only because a bare call's failure mode would be a traceback
        # out of an ADVISORY check. The profile now refuses what it cannot read
        # (`ProfileError`, a `PolicyError`), which this already catches — an
        # absent profile still answers, because absent is not unreadable.
        return set(), "unknown"


# ── catching up with upstream ────────────────────────────────────────────────


def upstream_commits(target: Path, run_id: str, name: str, onto: str) -> list[str]:
    """What landed on `onto` since this run's base — the delta an impact
    re-check is computed over. Empty means the rebase is a no-op and there is
    nothing new to re-scope.

    **A git call that FAILS is could-not-look, and it raises.** Item 156: read
    through `check=False` a base commit that no longer exists - a force-push, a
    gc - came back `""`, and `rebase` rendered that as "already on the latest
    upstream" while upstream had commits. There is no answer to return that is
    not a lie, so the question is refused and the missing base is named.
    """
    entry = load_record(target, run_id)["components"].get(name)
    if entry is None:
        raise WorktreeError(f"run {run_id} has no tree recorded for {name!r}")
    where = Path(entry["home"]) if entry["mode"] == "worktree" else \
        guardrails.component_dir(target, entry["path"])
    span = f"{entry['base']}..{onto}"
    try:
        log = git(where, "log", "--oneline", span)
    except WorktreeError as exc:
        raise WorktreeError(
            f"could not look: git log {span} in {where} failed ({exc}). The base "
            f"{entry['base']} recorded for {name} is not in that repository - a "
            f"force-push or a gc takes one away - so what upstream has cannot be "
            f"read, and an empty answer here would be reported as already caught "
            f"up. Re-create this run's tree from a base that exists, or record "
            f"the commit it really started from.") from None
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
            # `finding(s)`, not `overlap(s)`: since item 117 one of these rows
            # is *the components could not be determined*, and calling that an
            # overlap would state a collision nobody measured — the same false
            # certainty, one word along, as the empty answer it replaced.
            print(f"{len(findings)} finding(s) - advisory" if findings
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
