"""The parallel-run model — item 37: a run works in its own trees, never yours.

Item 35 shipped the WALL — `confinement()` refuses a write outside
`<worktrees_root>/<run-id>` — and nothing had ever created the thing it guards.
Agents were told to work in a worktree by prose in an SOP, and prose is not a
directory. This module is the mechanism: one branch and one linked worktree per
touched repository and branch pattern - components of one repository whose
patterns give one branch share one tree, each working in its own directory
inside it, so they are isolated from each other only where the components are
on different branches or in different repositories (T550) - a base commit
recorded at P4 start so every later diff is against a fixed point, and removal
at closure.

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
    """A tree that cannot be made, moved, or trusted. `notes` carries the plan's notes beside a
    refusal: they say how widely its checks reached (batch 25, D25-58, D25-69)."""

    def __init__(self, message: str, notes: list[str] | None = None) -> None:
        super().__init__(message)
        self.notes = list(notes or [])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(where: Path, *args: str, check: bool = True, raw: bool = False) -> str:
    """One git call, with the failure text kept. `check=False` returns "" for a
    non-zero exit, which several callers want as "the question has no answer"
    rather than as an error. `raw=True` returns the output as git wrote it, for
    `-z` output: not stripped, which took a leading space off the first path
    (review B11-10); not read in text mode, whose newline translation turns a CR
    in a path into a LF (review B11-13); and decoded losslessly, a byte that is not
    UTF-8 kept as a surrogate, so a path handed back to git names the same bytes
    (review B11-18)."""
    decoding = {} if raw else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        proc = subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                              timeout=300, **decoding)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"git {' '.join(args)} in {where}: {exc}") from None
    out, err = ((proc.stdout.decode("utf-8", "surrogateescape"),
                 proc.stderr.decode("utf-8", "replace")) if raw else (proc.stdout, proc.stderr))
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


def tree_at(policy: dict, target: Path, run_id: str, name: str) -> Path:
    """Where one tree for one run lives.

    `<worktrees_root>/<run-id>/<name>` — deliberately the same shape
    `policy.confinement` allows, so the wall and the thing it guards cannot
    disagree about the address. Since batch 25 (T550) a tree holds every component of one
    repository whose branch pattern gives its branch, `name` the first of them the profile
    declares there (decision 49), and each component's `home` is its own directory inside it.
    """
    return root_of(policy, target) / run_id / name


def tree_of(entry: dict) -> str:
    """A record entry's tree: its `tree` where one is recorded, else its `home` - a run recorded
    before batch 25 reads each component's `home` as its tree, so a legacy record's distinct trees
    stay distinct (D25-7). The ONE reading every grouping site takes."""
    return str(entry.get("tree") or entry.get("home") or "")


def _same(a: str | Path, b: str | Path) -> bool:
    """Whether two spellings name one place, resolved - a stored and a live spelling of one root
    differ, so a literal comparison never fires (D25-107)."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def place_of(entry: dict) -> str:
    """A worktree-mode entry's place in its tree: its `home` relative to its tree's root, "" for
    a component at its repository's root and for a record from before batch 25, whose `home` is
    its tree (D25-7)."""
    tree, home = tree_of(entry), str(entry.get("home") or "")
    if not tree or not home or _same(tree, home):
        return ""
    try:
        rel = Path(os.path.relpath(home, tree)).as_posix()
    except ValueError:
        return ""
    return "" if rel == ".." or rel.startswith("../") else rel


def home_gone(target: Path, name: str, entry: dict) -> str:
    """What every reader says of a component whose `home` is absent while its tree is present -
    the run's own work can remove a component's directory (`git rm -r services/b` committed in
    the tree) - never "its recorded tree is gone" (design pass 25, D25-105): "`b`'s directory
    `services/b` is not in <tree>", the tree spelled as the committed record spells it, since
    `50-suite.json` carries the words. "" where `home` is there, or its tree is gone too."""
    home, tree = str(entry.get("home") or ""), tree_of(entry)
    if entry.get("mode") != "worktree" or not home or not tree:
        return ""
    if Path(home).is_dir() or not Path(tree).is_dir():
        return ""
    return (f"`{name}`'s directory `{place_of(entry) or entry.get('path') or '.'}` is not in "
            f"{_stored_home(target, tree)}")


def home_way(name: str, entry: dict) -> str:
    """The way on from a `home` gone from its tree, in the words every reader gives it (design
    pass 25, D25-105, D25-123)."""
    return (f"restore `{place_of(entry) or entry.get('path') or '.'}` in the tree, or drop "
            f"`{name}` from the profile for this run")


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
        for field in ("home", "tree"):
            if isinstance(entry, dict) and isinstance(entry.get(field), str):
                entry[field] = _live_home(target, entry[field])
    return data


def save_record(target: Path, run_id: str, data: dict) -> None:
    path = record_path(target, run_id)
    if not path.parent.is_dir():
        # T398/T256: the relative ACTIVE location, not the absolute archive
        # fallback run_dir returns for a missing run - an operator must not be
        # sent to a directory that does not exist. Twin of stage.py:704/:819.
        raise WorktreeError(
            f"no run folder for {run_id} at {ledger.RUNS_REL.as_posix()}/{run_id}")
    # `home` and, since batch 25, `tree`: both stored relative, both read back live (D25-6).
    stored = {**data, "components": {
        name: ({**entry, **{field: _stored_home(target, entry[field]) for field in ("home", "tree")
                            if isinstance(entry.get(field), str)}}
               if isinstance(entry, dict) else entry)
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


# ── which repository, and where in it (design pass 25, section 1) ───────────


def repo_key(where: Path) -> tuple[str, str]:
    """(the repository's key, why it could not be read): git's common directory for `where`,
    absolute and resolved, `--path-format` written before `--git-common-dir`, which it governs
    (D25-26a). `--git-common-dir` alone answers relative to the cwd, so two repositories collided
    and one repository at two depths split (round 1). Git before 2.31 echoes the unknown flag and
    answers relative, so a relative last line is joined to the resolved directory. A failure is
    the component's own problem, never raised (D25-43)."""
    try:
        out = git(where, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except WorktreeError as exc:
        return "", str(exc)
    line = out.splitlines()[-1].strip() if out else ""
    if not line:
        return "", f"git rev-parse --git-common-dir in {where} answered nothing"
    found = Path(line)
    if not found.is_absolute():
        found = where.resolve() / found
    return str(found.resolve()), ""


def _facts(policy: dict, target: Path, run_id: str, comp: dict) -> dict:
    """What `plan` reads of one DECLARED component, in the run or not: its branch, its directory,
    its repository's key - `state` "repo", "none" (a directory in no repository) or "unknown"
    (missing, or git could not say, with `why`) - its place in that repository (`--show-prefix`,
    as `suite` asks it, T596; never `relative_to(--show-toplevel)`, which raises through a
    junction) and its checkout (`--show-toplevel`): one repository may be checked out twice."""
    vcs = comp.get("vcs") or {}
    pattern = guardrails.branch_pattern(policy, vcs)
    path = comp.get("path") or "."
    where = guardrails.component_dir(target, path).resolve()
    out = {"comp": comp, "name": comp["name"], "path": path, "vcs": vcs, "pattern": pattern,
           "branch": guardrails.branch_for(pattern, run_id), "where": where, "state": "unknown",
           "why": "", "key": "", "place": "", "checkout": ""}
    if not where.is_dir():
        out["why"] = f"{path} is not a directory"
        return out
    if not is_repo(where):
        out["state"] = "none"
        return out
    key, why = repo_key(where)
    if not key:
        out["why"] = why
        return out
    try:
        place = git(where, "rev-parse", "--show-prefix")
        checkout = git(where, "rev-parse", "--show-toplevel")
    except WorktreeError as exc:
        out["why"] = str(exc)
        return out
    out.update(state="repo", key=key, place=place.strip().strip("/"),
               checkout=str(Path(checkout).resolve()))
    return out


def place_entry(at: Path, base: str, place: str) -> tuple[str, str]:
    """git's answer for a component's place in a commit - the ONE helper creation, a join and the
    rebase's note ask (design pass 25, section 2): `git --literal-pathspecs ls-tree --full-tree
    <base> -- <place>`, git's EXIT read first, then its entry. Five answers: "dir" (040000);
    "submodule" (160000, which a fresh checkout leaves uninitialised - accepted, as `main` makes
    it, D25-118, D25-128); "file" or "symlink" (a blob, 100644/100755 or 120000); "none" (exit 0
    and no entry: the place holds nothing git tracks); "error" (a non-zero exit, 1 included -
    could-not-look, with git's own text, since a tree object missing on the way to the place
    prints the same nothing as an untracked place, D25-139). Never `-r`, which answers a
    directory by its first file (D25-134); never `cat-file -t`, which cannot answer for a
    submodule (D25-128); `--literal-pathspecs`, since a place beginning `:` is otherwise
    pathspec magic (D25-136); never an empty place or base, nor `.` or a trailing slash, each
    answered for something else (D25-135, D25-138, D25-142)."""
    if not place or not base or place in (".", "./") or place.endswith("/"):
        raise WorktreeError(f"place_entry was asked of place {place!r} at base {base!r} - a "
                            f"question git answers for something else")
    try:
        proc = subprocess.run(["git", "-C", str(at), "--literal-pathspecs", "ls-tree",
                               "--full-tree", base, "--", place], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "error", str(exc)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return "error", tail[-1] if tail else f"git exited {proc.returncode} saying nothing"
    lines = proc.stdout.strip().splitlines()
    if not lines:
        return "none", ""
    mode = lines[0].split(None, 1)[0]
    kind = {"040000": "dir", "160000": "submodule", "120000": "symlink"}.get(mode, "file")
    return kind, mode


def _place_problem(name: str, place: str, base: str, answer: tuple[str, str]) -> str:
    """What creation says of a component whose place is not a directory in its tree's base, in
    each answer's own words (D25-105, D25-116, D25-119, D25-129, D25-130); "" for a directory or
    a submodule."""
    kind, detail = answer
    short = base[:12]
    if kind in ("dir", "submodule"):
        return ""
    if kind in ("file", "symlink"):
        return (f"`{place}` is a {kind} in `{short}`, not a directory; make it a directory and "
                f"commit that, or point the component's `path` at the directory")
    if kind == "none":
        return (f"`{place}` holds nothing git tracks, so a tree made from `{short}` would not "
                f"hold it; commit a file in it, or, where `.gitignore` ignores it, `git add -f` "
                f"it or un-ignore it")
    return f"`{name}`: could not look whether `{place}` is a directory in `{short}` ({detail})"


def _join_problem(run_id: str, name: str, place: str, tree: Path, head: str, base: str,
                  answer: tuple[str, str], mate: str) -> str:
    """What a join says of a component whose place is not a directory in its tree (D25-97): what
    it looked at, the rebase offered only where the tree's base lacks the directory and its
    history never held it - a deletion the base postdates, or a directory only an ancestor holds,
    has no later commit to rebase onto (D25-106, D25-114, D25-125)."""
    if answer[0] == "error":
        return (f"`{name}`: could not look whether `{place}` is a directory in {tree} "
                f"({answer[1]})")
    kind = answer[0]
    said = (f"`{place}` is a {kind} in {tree} (the tree is at `{head[:12]}`), not a directory"
            if kind in ("file", "symlink") else
            f"`{place}` is not a directory in {tree} (the tree is at `{head[:12]}`)")
    in_base = place_entry(tree, base, place)[0] if base else "error"
    held = git(tree, "log", "-1", "--format=%H", base, "--", place, check=False) if base else ""
    if in_base in ("none", "file", "symlink") and not held:
        said += (f"; the tree's base lacks it, so rebase the tree onto a later commit that holds "
                 f"it, if one does (`tools/sdlc/worktree.py --run {run_id} --component {mate} "
                 f"--rebase <ref>`), then add `{name}`")
    return said + f"; or run `{name}` as a run of its own"


def _is_live(checkout: Path, tree: Path, rows: list[tuple[str, dict]]) -> bool:
    """Whether this run's recorded tree is live (D25-49): its directory exists, `git worktree
    list` knows it, and no row naming it carries a `removed_at` newer than the tree's own `at` -
    the newest `at` among those rows, the one reading of it (D25-101), so a tree-mate's removal
    from before a re-create never withholds it (D25-73)."""
    if not tree.is_dir():
        return False
    listed = [line.split(" ", 1)[1] for line in
              git(checkout, "worktree", "list", "--porcelain", check=False).splitlines()
              if line.startswith("worktree ")]
    if not any(_same(p, tree) for p in listed):
        return False
    newest = max(str(e.get("at") or "") for _, e in rows)
    return not any(str(e.get("removed_at") or "") > newest for _, e in rows)


PROFILE_REL = f".sdlc/{guardrails.PROFILE_NAME}"


def plan(target: Path, run_id: str, names: list[str] | None = None) -> dict:
    """What `create` would do, per component, without doing any of it: `{"steps": [...],
    "notes": [...]}`.

    Every refusal is collected rather than raised at the first one: an operator
    fixing a workspace should need one round trip. The same steps are what
    `create` executes, so `--dry-run` is the plan and not a description of it.

    **The components of one repository whose branch patterns give one branch share one tree**
    (T550; design pass 25, sections 1-2): the repository is git's common directory, a group is
    its components on one branch, its tree is named after the first component the profile
    declares in that repository on that branch, and its base is the group's checkouts' `HEAD`.
    Patterns giving different branches keep a tree each, as on `main` (decision 50). What must
    agree is checked at the scope its hazard has; the other checks at their own levels. A group
    whose tree this run already has registers inside it (stepwise creation). The notes say how
    widely the checks reached - a declared component outside the run whose repository could not
    be determined - and never reach the committed record (D25-53e, D25-58).
    """
    policy = guardrails.load(target)
    every = guardrails.components(target)
    if names is not None and (missing := sorted(set(names) - {c["name"] for c in every})):
        raise WorktreeError(f"no component named {', '.join(map(repr, missing))} - one of "
                            + ", ".join(repr(c["name"]) for c in every))
    wanted = {id(c) for c in every if names is None or c["name"] in names}
    record = load_record(target, run_id)
    facts = [_facts(policy, Path(target), run_id, comp) for comp in every]
    in_run = [f for f in facts if id(f["comp"]) in wanted]
    notes = [f"`{f['name']}`'s repository could not be determined ({f['why']}), so the agreements "
             f"below were not judged against it"
             for f in facts if id(f["comp"]) not in wanted and f["state"] == "unknown"]
    steps: list[dict] = []
    step_of: dict[int, dict] = {}
    groups: dict[tuple[str, str], list[dict]] = {}
    seen: dict[str, dict] = {}
    for f in in_run:
        comp, vcs = f["comp"], f["vcs"]
        step: dict = {"name": f["name"], "path": f["path"],
                      "role": comp.get("role", "authoritative"),
                      "problems": guardrails.vcs_problems(policy, comp), "branch": f["branch"],
                      "pattern": f["pattern"], "where": str(f["where"])}
        step_of[id(f)] = step
        if (first := seen.get(f["name"])) is not None:
            # D25-22: the record is keyed by name, so within one tree - no collision at `git
            # worktree add` - the second silently replaced the first.
            step["problems"].append(f"two components are named `{f['name']}` ({first['path']} "
                                    f"and {f['path']}); the run's record is keyed by name, so "
                                    f"one would replace the other - rename one in the profile")
        seen.setdefault(f["name"], step)
        steps.append(step)
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
            # P9 for ever, whatever the run changed. In no repository, an empty
            # base, as `main` records one (D25-32).
            step["base"] = (git(f["where"], "rev-parse", "HEAD", check=False)
                            if f["where"].is_dir() else "")
            continue
        step["mode"] = "worktree"
        if f["state"] == "unknown":
            step["problems"].append(f"{f['path']} is not a directory" if not f["where"].is_dir()
                                    else f"`{f['name']}`'s repository could not be determined "
                                         f"({f['why']})")
        elif f["state"] == "none":
            step["problems"].append(f"`{f['name']}` is not in a git repository, so it cannot "
                                    f"have a worktree")
        else:
            groups.setdefault((f["key"], f["branch"]), []).append(f)

    # `vcs.deny_raw_git` over EVERY component the profile declares in a repository the run
    # touches, in the run or not, by its value - any tree is that repository's second `.git`,
    # whichever component the run holds (D25-12, D25-36b, D25-37, D25-53c).
    for key in sorted({f["key"] for f in in_run if f["state"] == "repo"}):
        mates = [f for f in facts if f["state"] == "repo" and f["key"] == key]
        trues = [f for f in mates if f["vcs"].get("deny_raw_git")]
        falses = [f for f in mates if not f["vcs"].get("deny_raw_git")]
        if trues and falses:
            lead = next(step_of[id(f)] for f in mates if id(f) in step_of)
            lead["problems"].append(
                f"`{trues[0]['name']}` ({trues[0]['path']}) sets vcs.deny_raw_git true and "
                f"`{falses[0]['name']}` ({falses[0]['path']}) false in {PROFILE_REL}, but they are "
                f"one repository, and any tree is its second .git whichever of them a run "
                f"holds; set deny_raw_git true for both, or give them separate repositories")

    checkouts_checked: set[str] = set()
    repos_checked: set[str] = set()
    for (key, branch), members in groups.items():
        namer = next(f for f in facts
                     if f["state"] == "repo" and f["key"] == key and f["branch"] == branch)
        tree = tree_at(policy, Path(target), run_id, namer["name"])
        lead = step_of[id(members[0])]
        problems: list[str] = []
        if why := guardrails.tree_name_problem(namer["name"]):
            problems.append(f"component `{namer['name']}` names this run's tree for its "
                            f"repository's components on branch {branch}, and {why}; rename "
                            f"it in the profile")
        else:
            wall = (root_of(policy, Path(target)) / run_id).resolve()
            try:
                inside = tree.resolve().relative_to(wall) != Path(".")
            except ValueError:
                inside = False
            if not inside:
                problems.append(f"the tree {tree} resolves outside {wall}; rename "
                                f"`{namer['name']}` in the profile")
        rows = [(n, e) for n, e in record["components"].items()
                if isinstance(e, dict) and e.get("mode") == "worktree"
                and _same(tree_of(e), tree)]
        checkout = Path(members[0]["checkout"])
        live = bool(rows) and _is_live(checkout, tree, rows)
        if live:
            # A component joining takes the tree's recorded branch and base, whatever its own
            # checkout's `HEAD` is: the two-commit refusal judges a tree's FIRST creation (D25-96).
            base = str(max(rows, key=lambda r: str(r[1].get("at") or ""))[1].get("base") or "")
        else:
            heads: dict[str, list[dict]] = {}
            for f in members:
                heads.setdefault(f["checkout"], []).append(f)
            commits = {co: git(Path(co), "rev-parse", "HEAD", check=False) for co in heads}
            if len(set(commits.values())) > 1:
                c1 = next(iter(heads))
                f1 = heads[c1][0]
                c2, f2 = next((co, fs[0]) for co, fs in heads.items()
                              if commits[co] != commits[c1])
                problems.append(
                    f"`{f1['name']}` (at `{c1}`, HEAD `{commits[c1][:12]}`) and `{f2['name']}` "
                    f"(at `{c2}`, HEAD `{commits[c2][:12]}`) would share one tree but start from "
                    f"two commits; one tree has one base, so check one out at the other's "
                    f"commit (`git -C {c2} checkout --detach {commits[c1]}`, which moves that "
                    f"checkout), give them per-component branch patterns (a tree each), or run "
                    f"them as two runs")
            base = commits[members[0]["checkout"]]
            if not base and key not in repos_checked:
                problems.append(f"{members[0]['path']} has no commits yet")
            repos_checked.add(key)
            if git(checkout, "rev-parse", "--verify", "--quiet", branch, check=False):
                problems.append(f"branch {branch} already exists in {members[0]['path']}")
                if rows:
                    notes.append(f"this run's record names the removed tree {tree} "
                                 f"({', '.join(n for n, _ in rows)}), which left branch "
                                 f"{branch} behind")
            # The DESTINATION is part of what a plan is about. `git worktree add`
            # refuses an occupied one, and until item 156 nothing looked - so that
            # refusal arrived half way through creation, with the trees before it
            # already made. Validated here, the common case never gets that far.
            if tree.exists() and (not tree.is_dir() or any(tree.iterdir())):
                problems.append(f"{namer['name']}: {tree} is not empty - git worktree add "
                                f"refuses an occupied destination. A stopped run leaves one; "
                                f"remove it, or use a different run id")
            for co, fs in heads.items():
                if co in checkouts_checked:
                    continue
                checkouts_checked.add(co)
                # `intake.dirty_paths`, not a second `git status` - the run ledger is written
                # INTO a single-repo target by intake itself. Asked once per CHECKOUT: `git
                # status` answers for the checkout it is asked in (round 2).
                dirty, why = intake_mod.dirty_paths(Path(co))
                who = ", ".join(f"`{f['name']}`" for f in fs)
                if dirty is None:
                    problems.append(f"{co} ({who}): {why}")
                elif dirty:
                    problems.append(
                        f"{co}, the checkout of {who}, has {len(dirty)} uncommitted change(s) "
                        f"- the base commit would describe a tree nobody can reconstruct, and "
                        f"the run's diffs would carry someone else's work: "
                        + ", ".join(dirty[:5]))

        # Over the components that will share this tree, those this run already recorded in it
        # included - their `vcs` re-read from the profile by name (D25-38).
        mates = list(members)
        for n, _ in rows:
            if not any(f["name"] == n for f in mates):
                mates += [f for f in facts if f["name"] == n][:1]
        if len(mates) > 1:
            for field in ("push_remote", "commit_command", "push_command"):
                values: dict[str, dict] = {}
                for f in mates:
                    values.setdefault(str(f["vcs"].get(field) or "").strip(), f)
                if len(values) > 1:
                    (v1, f1), (v2, f2) = list(values.items())[:2]
                    problems.append(
                        f"`{f1['name']}` ({f1['path']}) sets vcs.{field} {v1!r} and "
                        f"`{f2['name']}` ({f2['path']}) {v2!r} in {PROFILE_REL}, but they would "
                        f"share one tree and its branch; make them agree, or give one a branch "
                        f"pattern of its own, which gives it a tree of its own")
            for f in mates:
                if f["comp"].get("role") == "fork":
                    other = next(g for g in mates if g is not f)
                    problems.append(
                        f"`{f['name']}` is a fork and would share a tree with `{other['name']}` "
                        f"- pushing the tree's branch would push the fork's directory with the "
                        f"rest; give `{f['name']}` a branch pattern of its own, which gives it a "
                        f"tree of its own; or change its role")

        recorded = {n for n, _ in rows}
        mate = sorted(recorded)[0] if recorded else ""
        for f in members:
            step = step_of[id(f)]
            step.update(tree=str(tree), home=str(tree / f["place"]) if f["place"] else str(tree),
                        base=base, place=f["place"], checkout=f["checkout"], live=live)
            if live and f["name"] in recorded:
                held = record["components"][f["name"]]
                if held.get("path") != f["path"]:
                    step["problems"].append(
                        f"the record holds `{f['name']}` at {held.get('path')} and the profile at "
                        f"{f['path']}; restore the profile's path for this run, or start a new "
                        f"run")
                step["accepted"] = True
                continue
            if not f["place"] or not base:
                continue        # a root component's empty place is never asked (D25-135)
            if live:
                head = git(tree, "rev-parse", "HEAD", check=False)
                answer = place_entry(tree, head, f["place"]) if head else ("error", "no HEAD")
                if answer[0] not in ("dir", "submodule"):
                    step["problems"].append(_join_problem(run_id, f["name"], f["place"], tree,
                                                          head, base, answer, mate))
            elif why := _place_problem(f["name"], f["place"], base,
                                       place_entry(Path(f["checkout"]), base, f["place"])):
                step["problems"].append(why)
        lead["problems"] += problems
    return {"steps": steps, "notes": notes}


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
    """Make this run's branches and trees. All or nothing. Returns the record, with the plan's
    `notes`, and the components it `made` and `accepted` beside it (D25-79, D25-93) - never in the
    committed record.

    A partial creation is worse than none: some components in the run's trees and some not is a
    run whose diffs come from two different worlds. So every problem is
    found first, and the first one refuses the whole thing.

    **One `git worktree add` per tree** (T550): a group of one repository's components on one
    branch gets one tree, each component registered at its own directory inside it; a group
    whose tree this run already has registers with no `worktree add`, and a component already
    recorded there is accepted and left as it is - but for a stale `removed_at`, which is cleared
    (D25-92), as re-making a tree clears it on every row naming it (D25-101).

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
    planned = plan(target, run_id, names)
    steps, notes = planned["steps"], planned["notes"]
    if problems := [p for s in steps for p in s["problems"]]:
        raise WorktreeError(f"{len(problems)} problem(s), and none of the trees were "
                            f"made: " + "; ".join(problems), notes)
    if not steps:
        raise WorktreeError("no components to work in", notes)
    record = load_record(target, run_id)
    if dry_run:
        return {"components": {s["name"]: s for s in steps}, "dry_run": True, "notes": notes}
    policy = guardrails.load(target)
    made: list[tuple[Path, Path, str]] = []
    remade: list[str] = []
    fresh: list[str] = []
    accepted: list[str] = []
    # The guarded region is EVERYTHING after the first tree can exist, not just
    # the `worktree add`. The record write was one step outside it, and with no
    # run folder - a run id nobody opened, or a ledger removed under a live run
    # - `create` made both trees and both branches and then raised while writing
    # the record, unwinding nothing: exactly the stuck state this function's
    # unwind exists to prevent, reached one step past where it was watching.
    try:
        for s in steps:
            if s["mode"] == "worktree" and not s.get("live") \
                    and not any(_same(s["tree"], t) for t in remade):
                spot = Path(s["tree"])
                claim_root(policy, Path(target))
                spot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    git(Path(s["checkout"]), "worktree", "add", "-b", s["branch"],
                        str(spot), s["base"])
                except WorktreeError as exc:
                    raise WorktreeError(f"{s['name']}: {exc}") from None
                made.append((Path(s["checkout"]), spot, s["branch"]))
                remade.append(s["tree"])
            if s.get("accepted"):
                held = record["components"][s["name"]]
                rows = [e for e in record["components"].values()
                        if isinstance(e, dict) and _same(tree_of(e), s["tree"])]
                newest = max(str(e.get("at") or "") for e in rows)
                if held.get("removed_at") and str(held["removed_at"]) < newest:
                    held.pop("removed_at")
                accepted.append(s["name"])
                continue
            record["components"][s["name"]] = {
                "mode": s["mode"], "path": s["path"], "branch": s["branch"],
                "pattern": s["pattern"], "base": s.get("base", ""),
                "home": s.get("home", s["where"]), "role": s["role"],
                "why": s.get("why", ""), "at": _now(),
                **({"tree": s["tree"]} if s["mode"] == "worktree" else {}),
            }
            fresh.append(s["name"])
        for entry in record["components"].values():
            if isinstance(entry, dict) and any(_same(tree_of(entry), t) for t in remade):
                entry.pop("removed_at", None)
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
        raise WorktreeError(f"{exc}. All or nothing, so {took}{end}", notes) from None
    return {**record, "notes": notes, "made": fresh, "accepted": accepted}


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
    """`serving_from`'s answer for one recorded deploy - one whose `where` lies in `spot`, at its
    root or in any directory inside it, never the reverse: a component's own deploy runs at its
    `home` inside its tree, which asked by the tree's root alone was missed, and the tree removed
    from under it (batch 25, D25-30)."""
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
        ran, at = where.resolve(), Path(spot).resolve()
    except OSError:
        ran, at = where, Path(spot)
    if ran != at and at not in ran.parents:
        return ""
    # Named by where it ran: the tree's own root, or a component's directory inside it.
    spot = spot if ran == at else ran
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
    # Each distinct tree checked once and removed once, its removal stamped on EVERY component of
    # it - stamped on one, `--list` showed a live tree-mate whose tree was gone (batch 25, D25-39).
    # A record from before batch 25 reads each `home` as its tree, so each stays its own.
    groups: list[tuple[str, list[dict]]] = []
    names_of: dict[int, list[str]] = {}
    for name, entry in sorted(record["components"].items()):
        if entry["mode"] != "worktree":
            continue
        tree = tree_of(entry)
        group = next((g for g in groups if _same(g[0], tree)), None)
        if group is None:
            group = (tree, [])
            groups.append(group)
        group[1].append(entry)
        names_of.setdefault(id(group[1]), []).append(name)
    removable: list[tuple[str, list[dict], Path]] = []
    for tree, rows in groups:
        name = ", ".join(names_of[id(rows)])
        spot = Path(tree)
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
        removable.append((name, rows, spot))
    # NOTHING has been deleted yet. Every refusal is known, so the run stops
    # here with all of its trees intact rather than part-way through.
    if holding:
        save_record(target, run_id, record)
        raise WorktreeError("; ".join(holding))
    for name, rows, spot in removable:
        args = ["worktree", "remove", str(spot)] + (["--force"] if force else [])
        try:
            git(guardrails.component_dir(target, rows[0]["path"]), *args)
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
        stamp = _now()
        for entry in rows:
            entry["removed_at"] = stamp
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
    # The tree's root, which git answers alike from any directory of the tree, and which exists
    # where a component's directory the run removed does not (batch 25, D25-26c, D25-112).
    where = Path(tree_of(entry)) if entry["mode"] == "worktree" else \
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

    **Once per tree** (batch 25, T550). Every git act of it - `rebase`, `diff --diff-filter=U`,
    `rebase --abort`, `rev-parse <onto>` - is asked at the tree's root, never at `home`, which
    the run's own work may have removed (D25-112, D25-122); the new base is written on EVERY
    component of the tree (D25-2), so a tree-mate's own call finds it rebased and says so. It
    checks nothing before it moves - `onto` cannot say what the rebase produces (D25-111) - and,
    after the record is written, reads each tree-mate's place in the tree's new `HEAD` through
    the one place helper, naming in `notes` every one whose directory the rebase left out
    (D25-105, D25-120, D25-121, D25-146).
    """
    entry = load_record(target, run_id)["components"].get(name)
    if entry is None:
        raise WorktreeError(f"run {run_id} has no tree recorded for {name!r}")
    if entry["mode"] != "worktree":
        why = entry.get("why") or "no reason recorded"
        raise WorktreeError(f"{name} works in place ({why}) - rebase it with the "
                            f"repository's own tooling")
    spot = Path(tree_of(entry))
    ahead = upstream_commits(target, run_id, name, onto)
    if not ahead:
        return {"name": name, "onto": onto, "upstream": [], "rebased": False,
                "conflicts": [], "note": "already on the latest upstream", "notes": []}
    try:
        git(spot, "rebase", onto)
    except WorktreeError:
        conflicts = [line for line in
                     git(spot, "diff", "--name-only", "--diff-filter=U",
                         check=False).splitlines() if line.strip()]
        git(spot, "rebase", "--abort", check=False)
        return {"name": name, "onto": onto, "upstream": ahead, "rebased": False,
                "conflicts": conflicts, "notes": [],
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
    moved = git(spot, "rev-parse", onto, check=False)
    stamp = _now()
    mates = [(n, e) for n, e in sorted(record["components"].items())
             if isinstance(e, dict) and e.get("mode") == "worktree" and _same(tree_of(e), spot)]
    for _, e in mates:
        e["previous_base"] = e["base"]
        e["base"] = moved or e["base"]
        e["rebased_at"] = stamp
        e["rebased_onto"] = onto
    save_record(target, run_id, record)
    # Read AFTER the record is written, and never raising: the rebase happened, and a raise on
    # either side of the write misreports it or strands the tree with its old base (D25-146).
    # Only THIS tree's components (D25-121); a root component's empty place is never asked
    # (D25-135).
    notes: list[str] = []
    for n, e in mates:
        if not (place := place_of(e)):
            continue
        kind, detail = place_entry(spot, "HEAD", place)
        if kind in ("dir", "submodule"):
            continue
        notes.append(f"could not look whether `{n}`'s directory `{place}` is in "
                     f"{_stored_home(target, str(spot))} after the rebase ({detail})"
                     if kind == "error" else
                     f"`{n}`'s directory `{place}` is not in {_stored_home(target, str(spot))} "
                     f"after the rebase; restore it in the tree, or drop `{n}` from the "
                     f"profile for this run")
    entry = record["components"][name]
    return {"name": name, "onto": onto, "upstream": ahead, "rebased": True,
            "conflicts": [], "base": entry["base"], "notes": notes,
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
                # Each of the plan's notes on a line of its own (D25-58, D25-81).
                for note in result.get("notes") or []:
                    print(f"  note: {note}")
                print("(plan only - nothing was made)" if args.dry_run else
                      f"{len(result['components'])} component(s) ready - made "
                      f"{len(result['made'])}, accepted {len(result['accepted'])}")
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
                for note in result.get("notes") or []:
                    print(f"  note: {note}")
            return 1 if result["conflicts"] else 0
    except (WorktreeError, guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        # A refusal prints the plan's notes beside its problems: they say how widely the
        # checks reached (D25-69).
        for note in getattr(exc, "notes", None) or []:
            print(f"  note: {note}")
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
