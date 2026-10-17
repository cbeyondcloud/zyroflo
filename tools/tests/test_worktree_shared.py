"""Batch 25 (T550; design pass 25): components that share one git repository, in worktree mode.

`main` gave every component a worktree of its own: with the default branch pattern the second
component of a repository collided on the branch name, and with per-component patterns each tree
was a worktree of the WHOLE repository while the component's commands ran at the tree's root. Now
the components of one repository whose patterns give ONE branch share one tree, each component's
`home` its own directory inside it; patterns giving different branches keep a tree each.

Everything here is synthetic: real git repositories made in a temporary directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

DEV = ["-c", "user.email=dev@x.invalid", "-c", "user.name=Dev"]


def git(where: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(where), *DEV, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def commit(where: Path, message: str = "base") -> None:
    git(where, "add", "-A")
    git(where, "commit", "-qm", message)


def component(name: str, path: str, **vcs) -> str:
    """One component of the profile's `components:` list."""
    role = vcs.pop("role", "authoritative")
    lines = [f'  - name: "{name}"', f'    path: "{path}"', f"    role: {role}", "    vcs:"]
    for key in ("commit_command", "push_command", "push_remote", "branch_pattern"):
        lines.append(f'      {key}: "{vcs.get(key, "")}"')
    if "deny_raw_git" in vcs:
        lines.append(f"      deny_raw_git: {str(vcs['deny_raw_git']).lower()}")
    return "\n".join(lines) + "\n"


def write_profile(root: Path, *components: str) -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ncomponents:\n" + "".join(components), encoding="utf-8")


def init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True,
                   timeout=120, capture_output=True)
    return path


def mono(root: Path, *components: str,
         files: tuple[str, ...] = ("services/a/app.py", "services/b/app.py")) -> Path:
    """A repository at the workspace root holding the components' directories, committed -
    the probe's monorepo (`services/a`, `services/b`) unless told otherwise."""
    init(root)
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    write_profile(root, *(components or (component("a", "services/a"),
                                         component("b", "services/b"))))
    commit(root)
    return root


def run_in(root: Path, rid: str = "r1") -> str:
    L.create(root, "requirement", "a change", run_id=rid)
    return rid


def worktrees(root: Path) -> list[str]:
    return [line.split(" ", 1)[1] for line in git(root, "worktree", "list", "--porcelain")
            .splitlines() if line.startswith("worktree ")]


def same(a: str | Path, b: str | Path) -> bool:
    return Path(a).resolve() == Path(b).resolve()


# ── one repository, one branch: one tree (sections 1 and 2) ─────────────────────────────


def test_one_repository_on_the_default_pattern_shares_one_tree(tmp_path):
    """D25-8, D25-18: `main` refused `b` at its branch; now `a` and `b` share one tree and one
    branch, named after the first component, each `home` its own directory inside it."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    made = W.create(root, rid)
    a, b = made["components"]["a"], made["components"]["b"]
    tree = root / ".sdlc-worktrees" / rid / "a"
    assert same(a["tree"], tree) and same(b["tree"], tree)
    assert same(a["home"], tree / "services" / "a") and same(b["home"], tree / "services" / "b")
    assert a["branch"] == b["branch"] == f"sdlc/{rid}" and a["base"] == b["base"]
    assert len(worktrees(root)) == 2, "the checkout and ONE tree"


def test_a_stepwise_run_gives_the_record_a_one_call_run_gives(tmp_path):
    """D25-8, D25-18: `--create --component a`, then `b` - each exits 0, and the record is the
    one a one-call run writes (its `at` stamps aside)."""
    def strip(record: dict, root: Path) -> dict:
        """The record as a run in `root` would write it anywhere: no stamps, no base (each
        workspace is its own repository), its paths relative to the workspace."""
        return {name: {k: (os.path.relpath(v, root) if k in ("home", "tree") else v)
                       for k, v in entry.items() if k not in ("at", "base")}
                for name, entry in record["components"].items()}
    one = mono(tmp_path / "one")
    W.create(one, run_in(one))
    two = mono(tmp_path / "two")
    rid = run_in(two)
    for name in ("a", "b"):
        assert W.main(["--target", str(two), "--run", rid, "--create",
                       "--component", name]) == 0
    stepwise = W.load_record(two, rid)
    assert strip(stepwise, two) == strip(W.load_record(one, "r1"), one)
    assert stepwise["components"]["a"]["base"] == stepwise["components"]["b"]["base"]


def test_two_patterns_in_one_repository_keep_a_tree_each(tmp_path):
    """D25-34, D25-42 (decision 50): patterns giving different branches keep a tree each, as on
    `main` - each `home` now its component's directory inside its own tree."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "services/b", branch_pattern="sdlc/b-<run-id>"))
    rid = run_in(root)
    made = W.create(root, rid)["components"]
    trees = root / ".sdlc-worktrees" / rid
    assert same(made["a"]["tree"], trees / "a") and same(made["b"]["tree"], trees / "b")
    assert same(made["a"]["home"], trees / "a" / "services" / "a")
    assert same(made["b"]["home"], trees / "b" / "services" / "b")
    assert made["b"]["branch"] == f"sdlc/b-{rid}"


def test_a_single_component_below_its_root_works_in_its_own_directory(tmp_path):
    """D25-9: `services/a` alone - its `home` is its directory inside the tree, where `main` gave it
    the tree's root."""
    root = mono(tmp_path / "ws", component("a", "services/a"), files=("services/a/app.py",))
    rid = run_in(root)
    a = W.create(root, rid)["components"]["a"]
    assert same(a["home"], Path(a["tree"]) / "services" / "a")


def test_a_component_at_its_repositorys_root_records_its_tree_as_its_home(tmp_path):
    """A repository holding one component at its root is unchanged: its `home` is its `tree`, and
    only the new field is written."""
    root = mono(tmp_path / "ws", component("app", "."), files=("app.py",))
    rid = run_in(root)
    entry = W.create(root, rid)["components"]["app"]
    assert same(entry["home"], entry["tree"]) and same(entry["tree"],
                                                       root / ".sdlc-worktrees" / rid / "app")


def test_two_repositories_keep_a_tree_each(tmp_path):
    """D25-3: two repositories at one depth - two keys, two trees, each component at its tree's
    root, as on `main`."""
    ws = tmp_path / "ws"
    for name in ("api", "lib"):
        init(ws / name)
        (ws / name / "app.py").write_text("x = 1\n", encoding="utf-8")
        commit(ws / name)
    write_profile(ws, component("api", "api"), component("lib", "lib"))
    rid = run_in(ws)
    made = W.create(ws, rid)["components"]
    assert not same(made["api"]["tree"], made["lib"]["tree"])
    for name in ("api", "lib"):
        assert same(made[name]["home"], made[name]["tree"])


def test_one_repository_at_two_depths_shares_one_tree(tmp_path):
    """D25-3: a component at a repository's root and one in a directory of it are one repository
    - the key is git's common directory, never the path - so one tree."""
    ws = tmp_path / "ws"
    init(ws / "repo")
    for rel in ("app.py", "sub/app.py"):
        (ws / "repo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / "repo" / rel).write_text("x = 1\n", encoding="utf-8")
    commit(ws / "repo")
    write_profile(ws, component("x", "repo"), component("y", "repo/sub"))
    rid = run_in(ws)
    made = W.create(ws, rid)["components"]
    assert same(made["x"]["tree"], made["y"]["tree"])
    assert same(made["y"]["home"], Path(made["x"]["tree"]) / "sub")


def test_a_directory_in_no_repository_is_named_and_never_grouped(tmp_path):
    """D25-3: a component whose directory answers no repository is named where it would get a
    tree."""
    root = mono(tmp_path / "ws")
    write_profile(root, component("a", "services/a"), component("loose", "../loose"))
    (tmp_path / "loose").mkdir()
    commit(root)
    rid = run_in(root)
    with pytest.raises(W.WorktreeError, match="`loose` is not in a git repository, so it "
                                              "cannot have a worktree"):
        W.create(root, rid)


def test_the_committed_record_stores_the_tree_relative(tmp_path):
    """D25-6: the record travels with the run - `tree`, like `home`, is stored relative to the
    workspace and read back absolute."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    stored = json.loads(W.record_path(root, rid).read_text(encoding="utf-8"))
    for entry in stored["components"].values():
        assert not Path(entry["tree"]).is_absolute() and ":" not in entry["tree"]
        assert entry["tree"] == f".sdlc-worktrees/{rid}/a"


# ── the tree's name (section 2; D25-25, D25-33, D25-40, D25-56, D25-71, D25-94, D25-104) ──


@pytest.mark.parametrize("name", ["../x", "D:evil", "a\\b", "a/b", ".", "..", "nul", "nul.txt",
                                  "NUL.tar.gz", "conin$", "conout$", "lpt0", "com1", "x.",
                                  "x ", "a<b", "a>b", 'a"b', "a|b", "a?b", "a*b", "a:b",
                                  "tab\tname"])
def test_a_name_windows_or_git_refuses_names_no_tree(name):
    assert P.tree_name_problem(name), name


@pytest.mark.parametrize("name", ["a b", "café", "com0", "web", "api-v2", "x.y"])
def test_a_name_both_accept_names_a_tree(name):
    assert P.tree_name_problem(name) == ""


def test_a_component_whose_name_cannot_name_a_tree_is_refused_with_its_remedy(tmp_path):
    """D25-36d, D25-37: the problem names the component, even one the run does not hold, and its
    remedy - rename it in the profile."""
    root = mono(tmp_path / "ws", component("nul", "services/a"), component("b", "services/b"))
    rid = run_in(root)
    with pytest.raises(W.WorktreeError, match=r"`nul`.*rename it in the profile"):
        W.create(root, rid, ["b"])


def test_the_graph_bundles_reserved_names_are_unchanged():
    """D25-95: `export_okf` answers another question - the file names Windows cannot write - so
    its own set stays byte-identical."""
    from tools.graph import export_okf
    assert export_okf._RESERVED == {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                                    *(f"lpt{i}" for i in range(1, 10))}


# ── checkouts, and what must agree (section 1 and 2) ─────────────────────────────────────


def second_checkout(root: Path, where: Path, move: bool = True) -> str:
    """A linked worktree of `root`'s repository at `where`, on branch `side`, one commit ahead of
    `root` where `move` is set - so one repository is checked out twice. Returns the first
    commit."""
    first = git(root, "rev-parse", "HEAD")
    git(root, "worktree", "add", "-q", "-b", "side", str(where))
    if move:
        (where / "services" / "b" / "more.py").write_text("y = 2\n", encoding="utf-8")
        commit(where, "ahead")
    return first


def test_one_repository_through_two_checkouts_at_two_commits_on_one_pattern_is_refused(tmp_path):
    """D25-5, D25-26d, D25-37: `a` in the main checkout, `b` in a linked worktree one commit ahead,
    one pattern - one tree has one base, so the plan refuses, naming both checkouts and commits; its
    detached remedy, followed, is made; so is running `a` as a run of its own."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "../other/services/b"))
    first = second_checkout(root, tmp_path / "other")
    rid = run_in(root)
    with pytest.raises(W.WorktreeError) as refused:
        W.create(root, rid)
    said = str(refused.value)
    assert "would share one tree but start from two commits" in said
    assert "checkout --detach" in said and str(tmp_path / "other") in said.replace("/", os.sep)
    assert W.create(root, rid, ["a"])["made"] == ["a"], "two runs: `a` alone is made"
    root2 = mono(tmp_path / "ws2", component("a", "services/a"),
                 component("b", "../other2/services/b"))
    first2 = second_checkout(root2, tmp_path / "other2")
    git(tmp_path / "other2", "checkout", "-q", "--detach", first2)
    made = W.create(root2, run_in(root2))["components"]
    assert made["a"]["base"] == made["b"]["base"] == first2


def test_dirt_in_a_second_checkout_alone_is_refused(tmp_path):
    """D25-20: `git status` answers for the checkout it is asked in, so it is asked once per
    checkout - the second checkout's uncommitted work refuses the run, as `main` refuses it."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "../other/services/b"))
    second_checkout(root, tmp_path / "other", move=False)
    (tmp_path / "other" / "services" / "b" / "wip.py").write_text("z = 3\n", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match=r"the checkout of `b`, has 1 uncommitted change"):
        W.create(root, run_in(root))


def test_two_checkouts_at_two_commits_on_two_patterns_are_made_each_from_its_own(tmp_path):
    """D25-34, D25-68: per-component patterns keep a tree each, as `main` makes them, each tree's
    recorded base its own checkout's `HEAD`."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "../other/services/b", branch_pattern="sdlc/b-<run-id>"))
    first = second_checkout(root, tmp_path / "other")
    made = W.create(root, run_in(root))["components"]
    assert made["a"]["base"] == first
    assert made["b"]["base"] == git(tmp_path / "other", "rev-parse", "HEAD") != first


@pytest.mark.parametrize("field, value", [("push_remote", "origin"),
                                          ("commit_command", "git commit -s"),
                                          ("push_command", "git push --no-verify")])
def test_components_sharing_a_tree_that_disagree_are_refused_and_nothing_is_made(
        tmp_path, field, value):
    """D25-11, D25-21: each tree-sharing key must agree - the problem names the file, both
    components and both values - and nothing is made."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "services/b", **{field: value}))
    rid = run_in(root)
    with pytest.raises(W.WorktreeError) as refused:
        W.create(root, rid)
    said = str(refused.value)
    assert f"sets vcs.{field} ''" in said and repr(value) in said and ".sdlc/profile.yml" in said
    assert "`a` (services/a)" in said and "`b` (services/b)" in said
    assert not (root / ".sdlc-worktrees" / rid).exists()


@pytest.mark.parametrize("names", [None, ["a"], ["b"]])
def test_mixed_deny_raw_git_in_one_repository_is_refused_for_any_run(tmp_path, names):
    """D25-12, D25-21, D25-37: `deny_raw_git` must agree over EVERY component the profile declares
    in the repository - a run naming only the in-place one, or only the other, is refused too."""
    root = mono(tmp_path / "ws", component("a", "services/a", deny_raw_git=True),
                component("b", "services/b", deny_raw_git=False))
    with pytest.raises(W.WorktreeError, match="set deny_raw_git true for both, or give them "
                                              "separate repositories"):
        W.create(root, run_in(root), names)


def test_deny_raw_git_written_false_beside_one_absent_agrees(tmp_path):
    """D25-36b: an absent key reads false, so `false` beside absent agrees."""
    root = mono(tmp_path / "ws", component("a", "services/a", deny_raw_git=False),
                component("b", "services/b"))
    assert sorted(W.create(root, run_in(root))["made"]) == ["a", "b"]


def test_a_fork_sharing_a_tree_is_refused_and_its_remedy_is_made(tmp_path):
    """D25-67, D25-77, D25-91: a fork may not share a tree - pushing its branch would push the
    fork's directory with the rest; given a pattern of its own it gets a tree of its own, made, as a
    fork alone in its tree is made on `main`."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "services/b", role="fork"))
    with pytest.raises(W.WorktreeError, match="give `b` a branch pattern of its own, which gives "
                                              "it a tree of its own; or change its role"):
        W.create(root, run_in(root))
    root2 = mono(tmp_path / "ws2", component("a", "services/a"),
                 component("b", "services/b", role="fork", branch_pattern="sdlc/b-<run-id>"))
    made = W.create(root2, run_in(root2))["components"]
    assert not same(made["a"]["tree"], made["b"]["tree"])


def test_two_in_place_components_with_two_patterns_are_made(tmp_path):
    """Round 2: two in-place components of one repository share no tree, so need not agree on the
    tree-sharing keys - made, as on `main`."""
    root = mono(tmp_path / "ws", component("a", "services/a", deny_raw_git=True),
                component("b", "services/b", deny_raw_git=True, branch_pattern="sdlc/b-<run-id>",
                          push_remote="origin"))
    made = W.create(root, run_in(root))["components"]
    assert made["a"]["mode"] == made["b"]["mode"] == "in-place"


def test_a_stepwise_join_that_disagrees_is_refused_through_the_plan(tmp_path):
    """D25-38: `a` made, then `b` disagreeing on a tree-sharing key - checked against the tree's
    recorded components, re-read from the profile by name - is refused, nothing registered."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "services/b", push_remote="origin"))
    rid = run_in(root)
    W.create(root, rid, ["a"])
    with pytest.raises(W.WorktreeError, match="would share one tree and its branch"):
        W.create(root, rid, ["b"])
    assert list(W.load_record(root, rid)["components"]) == ["a"]


def test_a_missing_directory_beside_a_bad_remote_collects_both_problems(tmp_path):
    """D25-43: a missing directory raised out of `plan`; now both problems are collected."""
    root = mono(tmp_path / "ws", component("a", "services/a", push_remote="nowhere"),
                component("b", "services/missing"))
    with pytest.raises(W.WorktreeError) as refused:
        W.create(root, run_in(root))
    said = str(refused.value)
    assert "push_remote 'nowhere'" in said and "services/missing is not a directory" in said


def test_two_components_of_one_name_are_refused_naming_both_paths(tmp_path):
    """D25-22: the record is keyed by name, so within one tree the second silently replaced the
    first; now both paths are named and the remedy is to rename one."""
    root = mono(tmp_path / "ws", component("a", "services/a"), component("a", "services/b"))
    with pytest.raises(W.WorktreeError, match=r"two components are named `a` \(services/a and "
                                              r"services/b\).*rename one in the profile"):
        W.create(root, run_in(root))


def test_a_joining_component_takes_its_trees_recorded_base(tmp_path):
    """D25-96: a component joining a live tree takes the tree's branch and base, whatever its own
    checkout's `HEAD` has moved to."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    base = W.create(root, rid, ["a"])["components"]["a"]["base"]
    (root / "services" / "b" / "later.py").write_text("w = 4\n", encoding="utf-8")
    commit(root, "later")
    made = W.create(root, rid, ["b"])
    assert made["components"]["b"]["base"] == base and made["made"] == ["b"]


def test_a_leftover_branch_in_a_second_repository_is_refused(tmp_path):
    """D25-53f: the existing branch is checked once per group - its branch, in its repository - so a
    leftover branch of the run's name in a second repository still refuses."""
    ws = tmp_path / "ws"
    for name in ("api", "lib"):
        init(ws / name)
        (ws / name / "app.py").write_text("x = 1\n", encoding="utf-8")
        commit(ws / name)
    write_profile(ws, component("api", "api"), component("lib", "lib"))
    rid = run_in(ws)
    git(ws / "lib", "branch", f"sdlc/{rid}")
    with pytest.raises(W.WorktreeError, match=f"branch sdlc/{rid} already exists in lib"):
        W.create(ws, rid)


def test_a_declared_component_outside_the_run_whose_repository_is_unknown_is_noted(
        tmp_path, capsys):
    """D25-53e, D25-58: never dropped in silence - the plan notes it, the run is made, the doors
    print the note, and the committed record holds no note (D25-81)."""
    root = mono(tmp_path / "ws", component("a", "services/a"), component("b", "services/gone"))
    rid = run_in(root)
    assert W.main(["--target", str(root), "--run", rid, "--create", "--component", "a"]) == 0
    printed = capsys.readouterr().out
    assert "note: `b`'s repository could not be determined (services/gone is not a directory)" \
        in printed
    stored = json.loads(W.record_path(root, rid).read_text(encoding="utf-8"))
    assert "notes" not in stored and "made" not in stored and "accepted" not in stored


def test_a_refusal_prints_the_plans_notes_beside_its_problems(tmp_path, capsys):
    """D25-69: a note says how widely the checks reached, so a refusal prints it too."""
    root = mono(tmp_path / "ws", component("a", "services/a", push_remote="nowhere"),
                component("b", "services/gone"))
    assert W.main(["--target", str(root), "--run", run_in(root), "--create", "--component",
                   "a"]) == 2
    printed = capsys.readouterr().out
    assert "!! " in printed and "note: `b`'s repository could not be determined" in printed


def test_an_in_place_component_in_no_repository_is_made(tmp_path):
    """D25-32: an in-place component needs no repository, as on `main`, and records an empty
    base."""
    ws = tmp_path / "ws"
    (ws / "loose").mkdir(parents=True)
    write_profile(ws, component("loose", "loose", deny_raw_git=True))
    entry = W.create(ws, run_in(ws))["components"]["loose"]
    assert (entry["mode"], entry["base"]) == ("in-place", "")


def test_a_failure_in_a_repositorys_second_tree_unwinds_its_first(tmp_path, monkeypatch):
    """D25-10, D25-80: `made` holds one entry per tree, so a repository whose patterns give two
    trees has the first taken back when the second fails."""
    root = mono(tmp_path / "ws", component("a", "services/a"),
                component("b", "services/b", branch_pattern="sdlc/b-<run-id>"))
    rid = run_in(root)
    real = W.git

    def failing(where, *args, **kw):
        if args[:2] == ("worktree", "add") and str(args[4]).endswith("b"):
            raise W.WorktreeError("a stub failure making the second tree")
        return real(where, *args, **kw)
    monkeypatch.setattr(W, "git", failing)
    with pytest.raises(W.WorktreeError, match="removed .* and branch sdlc/r1"):
        W.create(root, rid)
    monkeypatch.undo()
    assert len(worktrees(root)) == 1 and not (root / ".sdlc-worktrees" / rid / "a").exists()


# ── the place helper's five answers (section 2) ──────────────────────────────────────────


def test_a_component_directory_git_tracks_nothing_in_is_refused_with_its_way_on(tmp_path):
    """D25-105: an empty directory is no change `git status` reports, so the dirty check missed it
    and the tree was made without it; the place's entry says so."""
    root = mono(tmp_path / "ws", files=("services/a/app.py",))
    (root / "services" / "b").mkdir()
    with pytest.raises(W.WorktreeError, match="`services/b` holds nothing git tracks, so a tree "
                                              "made from `.*` would not hold it; commit a file"):
        W.create(root, run_in(root))


def test_an_ignored_component_directory_is_refused_in_its_own_words(tmp_path):
    """D25-116: a directory `.gitignore` ignores holds nothing git tracks - the way on names the
    forced add and the un-ignore."""
    root = mono(tmp_path / "ws", files=("services/a/app.py", ".gitignore"))
    (root / ".gitignore").write_text("services/b/\n", encoding="utf-8")
    commit(root, "ignore b")
    (root / "services" / "b").mkdir()
    (root / "services" / "b" / "app.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match="where `.gitignore` ignores it, `git add -f` it or "
                                              "un-ignore it"):
        W.create(root, run_in(root))


def test_a_place_the_commit_records_as_a_symlink_is_refused_at_a_join(tmp_path):
    """D25-130, D25-137: `services/b` committed as a symlink (built with `update-index
    --cacheinfo`, no privilege), then replaced by a directory in the checkout - a join against the
    tree made before it is refused in its own words, with no rebase offered, the base's history
    holding the place."""
    root = mono(tmp_path / "ws", files=("services/a/app.py",))
    blob = subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                          input="services/a", capture_output=True, text=True, check=True,
                          timeout=120).stdout.strip()
    git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},services/b")
    git(root, "commit", "-qm", "b is a symlink")
    git(root, "checkout", "-q", "--", ".")
    rid = run_in(root)
    W.create(root, rid, ["a"])
    git(root, "rm", "-q", "--cached", "services/b")
    target = root / "services" / "b"
    if target.is_symlink() or target.is_file():
        target.unlink()
    target.mkdir()
    (target / "app.py").write_text("x = 1\n", encoding="utf-8")
    commit(root, "b is a directory")
    with pytest.raises(W.WorktreeError) as refused:
        W.create(root, rid, ["b"])
    said = str(refused.value)
    assert "`services/b` is a symlink in" in said and "or run `b` as a run of its own" in said
    assert "rebase" not in said


def test_a_component_path_naming_a_file_is_refused(tmp_path):
    """D25-119: a `path` naming a file is no directory."""
    root = mono(tmp_path / "ws", component("a", "services/a/app.py"),
                files=("services/a/app.py",))
    with pytest.raises(W.WorktreeError, match="services/a/app.py is not a directory"):
        W.create(root, run_in(root))


def test_an_uninitialised_submodule_component_is_accepted(tmp_path):
    """D25-118, D25-128: a fresh clone leaves a submodule uninitialised - an empty directory the
    parent's commit records as `160000` - which the helper accepts, as `main` makes it, the tree
    holding the directory empty."""
    sub = init(tmp_path / "sub")
    (sub / "lib.py").write_text("x = 1\n", encoding="utf-8")
    commit(sub)
    parent = mono(tmp_path / "parent", files=("services/a/app.py",))
    subprocess.run(["git", "-C", str(parent), "-c", "protocol.file.allow=always", "submodule",
                    "add", "-q", str(sub), "services/b"], check=True, capture_output=True,
                   timeout=120)
    commit(parent, "the submodule")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(parent), str(clone)], check=True,
                   capture_output=True, timeout=120)
    made = W.create(clone, run_in(clone))["components"]
    assert same(made["b"]["home"], Path(made["b"]["tree"]) / "services" / "b")
    assert Path(made["b"]["home"]).is_dir() and not any(Path(made["b"]["home"]).iterdir())


def drop_object(root: Path, rev: str) -> None:
    """Delete the loose object `rev` names from `root`'s store."""
    sha = git(root, "rev-parse", rev)
    loose = root / ".git" / "objects" / sha[:2] / sha[2:]
    os.chmod(loose, 0o644)          # git writes its objects read-only
    loose.unlink()


def test_a_commits_root_tree_missing_from_the_store_is_could_not_look(tmp_path):
    """D25-129, D25-134: git's failure is its own problem with its own text, never "holds
    nothing git tracks"."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    drop_object(root, "HEAD^{tree}")
    with pytest.raises(W.WorktreeError) as refused:
        W.create(root, rid)
    assert "could not look whether `services/a` is a directory" in str(refused.value)
    assert "holds nothing git tracks" not in str(refused.value)


def test_a_tree_object_missing_between_the_root_and_the_place_is_could_not_look(tmp_path):
    """D25-139: git exits 1 with nothing on stdout - the output no entry gives - told apart by the
    exit alone."""
    root = mono(tmp_path / "ws")
    base = git(root, "rev-parse", "HEAD")
    drop_object(root, "HEAD:services")
    assert W.place_entry(root, base, "services/b")[0] == "error"


def test_a_places_own_tree_object_missing_is_answered_by_its_parents_entry(tmp_path):
    """D25-134: the parent tree's entry answers `040000`, exit 0 - accepted; `git worktree add`
    then refuses on its own, as on `main` (D25-63's class, Deferred)."""
    root = mono(tmp_path / "ws")
    base = git(root, "rev-parse", "HEAD")
    drop_object(root, "HEAD:services/b")
    assert W.place_entry(root, base, "services/b") == ("dir", "040000")


def test_the_place_helper_asks_literally_never_recursively_and_never_for_a_dot(tmp_path,
                                                                                monkeypatch):
    """D25-136, D25-138, D25-142: `--literal-pathspecs`, never `-r`, never a trailing slash or `.`,
    and never an empty place or base."""
    root = mono(tmp_path / "ws")
    base = git(root, "rev-parse", "HEAD")
    seen: list[list[str]] = []
    real = subprocess.run

    def spy(argv, *a, **kw):
        seen.append(list(argv))
        return real(argv, *a, **kw)
    monkeypatch.setattr(W.subprocess, "run", spy)
    assert W.place_entry(root, base, "services/b")[0] == "dir"
    (argv,) = seen
    assert "--literal-pathspecs" in argv and "-r" not in argv
    assert argv[-1] == "services/b" and argv[-2] == "--"
    for bad in ("", ".", "./", "services/b/"):
        with pytest.raises(W.WorktreeError):
            W.place_entry(root, base, bad)
    with pytest.raises(W.WorktreeError):
        W.place_entry(root, "", "services/b")


def test_a_colon_place_beside_its_tracked_namesake_has_no_entry(tmp_path):
    """D25-134, D25-136: read as pathspec magic, `:services/b` answered for `services/b`; read
    literally, it names no entry."""
    root = mono(tmp_path / "ws")
    assert W.place_entry(root, git(root, "rev-parse", "HEAD"), ":services/b") == ("none", "")


def test_a_root_components_empty_place_is_never_asked(tmp_path, monkeypatch):
    """D25-135: a commit always has a root tree, and an empty pathspec is git's own refusal."""
    root = mono(tmp_path / "ws", component("app", "."), files=("app.py",))
    asked: list[str] = []
    monkeypatch.setattr(W, "place_entry",
                        lambda at, base, place: asked.append(place) or ("dir", ""))
    W.create(root, run_in(root))
    assert asked == []


# ── stepwise creation and live trees (section 2), and the CLI ──────────────────────────────


def test_a_repeat_create_on_live_trees_accepts_and_leaves_the_record_as_it_is(tmp_path, capsys):
    """D25-79: `main` refused on the occupied destination and the existing branch; a live tree is
    recognised from the run's record and accepted - made 0, accepted 2 - no field dropped."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    record = W.load_record(root, rid)
    record["components"]["a"]["rebased_at"] = "2026-10-17T00:00:00+00:00"
    W.save_record(root, rid, record)
    assert W.main(["--target", str(root), "--run", rid, "--create"]) == 0
    assert "2 component(s) ready - made 0, accepted 2" in capsys.readouterr().out
    assert W.load_record(root, rid)["components"]["a"]["rebased_at"] == "2026-10-17T00:00:00+00:00"


def test_a_stale_removed_at_on_a_live_tree_is_cleared(tmp_path):
    """D25-92: kept, `--list` called a live tree removed."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    record = W.load_record(root, rid)
    record["components"]["a"]["removed_at"] = "2000-01-01T00:00:00+00:00"
    W.save_record(root, rid, record)
    W.create(root, rid)
    assert "removed_at" not in W.load_record(root, rid)["components"]["a"]


def test_a_changed_path_is_refused_with_its_way_on(tmp_path):
    """D25-92, D25-102: a component already recorded is accepted only while its recorded `path` is
    still the profile's."""
    root = mono(tmp_path / "ws", files=("services/a/app.py", "services/b/app.py",
                                        "services/c/app.py"))
    rid = run_in(root)
    W.create(root, rid)
    write_profile(root, component("a", "services/a"), component("b", "services/c"))
    commit(root, "b moved")
    with pytest.raises(W.WorktreeError, match="the record holds `b` at services/b and the "
                                              "profile at services/c; restore the profile's path "
                                              "for this run, or start a new run"):
        W.create(root, rid)


def close_out_by_hand(root: Path, rid: str, keep_branch: bool = True) -> None:
    """Take the run's one tree away as `remove` would, stamping `removed_at` on every row."""
    git(root, "worktree", "remove", "--force", str(root / ".sdlc-worktrees" / rid / "a"))
    if not keep_branch:
        git(root, "branch", "-D", f"sdlc/{rid}")
    record = W.load_record(root, rid)
    for entry in record["components"].values():
        entry["removed_at"] = "2999-01-01T00:00:00+00:00"
    W.save_record(root, rid, record)


def test_create_after_close_out_is_answered_by_the_leftover_branch_with_a_note(tmp_path, capsys):
    """D25-49, D25-60: only a LIVE tree is accepted; after close-out the branch the removal left
    answers, and a note names the removed tree."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    close_out_by_hand(root, rid)
    assert W.main(["--target", str(root), "--run", rid, "--create"]) == 2
    printed = capsys.readouterr().out
    assert f"branch sdlc/{rid} already exists" in printed
    assert "note: this run's record names the removed tree" in printed


def test_re_making_a_tree_clears_removed_at_on_every_row_naming_it(tmp_path):
    """D25-101: re-making a tree clears `removed_at` on every row naming it, not only the rows the
    call names."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    close_out_by_hand(root, rid, keep_branch=False)
    W.create(root, rid, ["a"])
    record = W.load_record(root, rid)["components"]
    assert "removed_at" not in record["a"] and "removed_at" not in record["b"]


def test_a_tree_mates_old_removed_at_does_not_withhold_acceptance(tmp_path):
    """D25-73: liveness reads the newest `at` among the rows naming the tree."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    record = W.load_record(root, rid)
    record["components"]["b"]["removed_at"] = "2000-01-01T00:00:00+00:00"
    W.save_record(root, rid, record)
    assert sorted(W.create(root, rid)["accepted"]) == ["a", "b"]


def test_create_json_keeps_mains_fields_and_says_what_it_made_and_accepted(tmp_path, capsys):
    """D25-81, D25-93: `--create --json` keeps `main`'s `components`, `run_id` and
    `schema_version`, and carries the notes and what it made and accepted."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    assert W.main(["--target", str(root), "--run", rid, "--create", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {"components", "run_id", "schema_version", "notes", "made", "accepted"} <= set(out)
    assert sorted(out["made"]) == ["a", "b"] and out["accepted"] == []


def test_create_dry_run_json_keeps_dry_run(tmp_path, capsys):
    """D25-103: `--dry-run` prints the plan and `dry_run`, the notes beside them, as `main`."""
    root = mono(tmp_path / "ws")
    assert W.main(["--target", str(root), "--run", run_in(root), "--create", "--dry-run",
                   "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and "notes" in out and set(out["components"]) == {"a", "b"}


def test_list_prints_no_notes(tmp_path, capsys):
    """D25-58, D25-69: `--list` reads the record and computes no plan, so it prints none."""
    root = mono(tmp_path / "ws", component("a", "services/a"), component("b", "services/gone"))
    rid = run_in(root)
    W.create(root, rid, ["a"])
    capsys.readouterr()
    assert W.main(["--target", str(root), "--run", rid, "--list"]) == 0
    assert "note:" not in capsys.readouterr().out


def test_a_record_written_on_another_machine_resolves(tmp_path):
    """D25-6: `home` and `tree` stored relative resolve against the live workspace, wherever it
    is."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    moved = tmp_path / "elsewhere"
    (moved / ".sdlc" / "runs" / rid).mkdir(parents=True)
    (moved / ".sdlc" / "runs" / rid / "worktrees.json").write_bytes(
        W.record_path(root, rid).read_bytes())
    entry = W.load_record(moved, rid)["components"]["b"]
    assert same(entry["tree"], moved / ".sdlc-worktrees" / rid / "a")
    assert same(entry["home"], moved / ".sdlc-worktrees" / rid / "a" / "services" / "b")
