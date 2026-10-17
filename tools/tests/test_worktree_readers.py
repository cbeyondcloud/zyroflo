"""Batch 25 (T550; design pass 25, section 3): the run's record read by each reader at its level.

Components of one repository on one branch share one tree, each `home` its own directory inside
it. A reader that acts on the repository - its branch, its commits, its trees, its release - or
that is a phase's work address reads `tree_of(entry)`; a reader that runs a command the COMPONENT
declares reads its `home`; a command inherited from the workspace's top level runs where it was
written to run, at `tree_of`.

Real git repositories in a temporary directory, real commands through a probe script run by
`python` (as the deploy tests run theirs), and the CLIs spawned where the contract is a process.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import prepass  # noqa: E402
from tools.sdlc import approve  # noqa: E402
from tools.sdlc import deploy as D  # noqa: E402
from tools.sdlc import release as R  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as S  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

DEV = ["-c", "user.email=dev@x.invalid", "-c", "user.name=Dev"]
HEAD = ("gate_profile: team\n"
        "phases: [intake, requirements, design, implementation, test, review, triage, release, "
        "deploy, closeout]\n"
        "review:\n  block_on: [critical, high]\n")

#: Says where it ran, or writes a mark or a report there.
PROBE = '''import os, sys
mode, args = sys.argv[1], sys.argv[2:]
if mode in ("junit", "cobertura"):
    os.makedirs(os.path.dirname(args[0]) or ".", exist_ok=True)
if mode == "cwd":
    print(os.getcwd())
elif mode == "mark":
    open(args[0], "w", encoding="utf-8").write(os.getcwd())
elif mode == "junit":
    open(args[0], "w", encoding="utf-8").write("<testsuite tests='%s'/>" % args[1])
elif mode == "cobertura":
    body = "".join("<class filename='%s'><lines><line number='1' hits='1'/></lines></class>"
                   % name for name in args[1:])
    open(args[0], "w", encoding="utf-8").write(
        "<coverage><packages><package><classes>%s</classes></package></packages></coverage>"
        % body)
'''


def git(where: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(where), *DEV, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def commit(where: Path, message: str = "base") -> None:
    git(where, "add", "-A")
    git(where, "commit", "-qm", message)


def commit_in(tree: Path, rel: str, text: str, message: str = "the change") -> None:
    """The run's change in its tree, committed on its branch as P4 does."""
    (tree / rel).parent.mkdir(parents=True, exist_ok=True)
    (tree / rel).write_text(text, encoding="utf-8")
    commit(tree, message)


def upstream(repo: Path, rel: str, text: str, message: str = "upstream") -> None:
    """A commit on the checkout's own branch adding `rel` alone - the run folder beside it stays
    out of it."""
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "--", rel)
    git(repo, "commit", "-qm", message)


def drop(where: Path, rel: str, message: str = "drop it") -> None:
    git(where, "rm", "-rq", "--", rel)
    git(where, "commit", "-qm", message)


def init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True,
                   timeout=120, capture_output=True)
    return path


def files(root: Path, *rels: str) -> None:
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n", encoding="utf-8")


def probe(tmp_path: Path) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(PROBE, encoding="utf-8")
    return path


def cmd(script: Path, *args: str) -> str:
    """The probe as a profile command: a YAML double-quoted value, so its quotes escaped."""
    return f'python \\"{script.as_posix()}\\" ' + " ".join(args)


def comp(name: str, path: str, pattern: str = "", role: str = "authoritative",
         deny: bool = False, **commands: str) -> str:
    lines = [f'  - name: "{name}"', f'    path: "{path}"', f"    role: {role}"]
    lines += [f'    {key}: "{value}"' for key, value in commands.items()]
    if pattern or deny:
        lines.append("    vcs:")
        lines += [f'      branch_pattern: "{pattern}"'] if pattern else []
        lines += ["      deny_raw_git: true"] if deny else []
    return "\n".join(lines) + "\n"


def profile(root: Path, *comps: str, top: str = "") -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(
        HEAD + top + "components:\n" + "".join(comps), encoding="utf-8")


def mono(root: Path, *comps: str, top: str = "",
         paths: tuple[str, ...] = ("services/a/app.py", "services/b/app.py")) -> Path:
    """A repository at the workspace root holding the components' directories, committed - the
    probe's monorepo (`services/a`, `services/b`) unless told otherwise."""
    init(root)
    files(root, *paths)
    profile(root, *(comps or (comp("a", "services/a"), comp("b", "services/b"))), top=top)
    commit(root)
    return root


def estate(ws: Path, layout: dict[str, tuple[str, ...]]) -> Path:
    """A workspace root in no repository, holding one committed repository per key."""
    for name, rels in layout.items():
        init(ws / name)
        files(ws / name, *rels)
        commit(ws / name)
    return ws


def run_in(root: Path, rid: str = "r1") -> str:
    L.create(root, "requirement", "a change", run_id=rid)
    return rid


def at_p9(root: Path) -> str:
    """A run at P9 with G5 taken for real, as `test_deploy` builds one."""
    rid = L.create(root, "deploy-change", "a config change")["run_id"]
    folder = L.run_dir(root, rid) / "40-impl"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("the change\n", encoding="utf-8")
    L.record_artifact(root, rid, "40-impl/")
    (L.run_dir(root, rid) / "90-deploy.md").write_text(
        "what will be deployed, where, and how to undo it\n", encoding="utf-8")
    L.record_artifact(root, rid, "90-deploy.md")
    L.advance(root, rid, phase="P9", note="ready to deploy")
    approve.record(root, rid, D.GATE, "approve", "owner@example.test",
                   notes="deploy approved for this build")
    return rid


def entry(root: Path, rid: str, name: str) -> tuple[Path, Path]:
    """`name`'s tree and `home`, from the run's own record."""
    row = W.load_record(root, rid)["components"][name]
    return Path(W.tree_of(row)), Path(row["home"])


def same(a, b) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def carry_run_folder(root: Path, rid: str, into: Path) -> None:
    """Commit the run's folder onto the branch checked out at `into`, as P8's step 2 asks -
    forced, so a folder the checkout ignores is committed all the same."""
    shutil.copytree(L.run_dir(root, rid), into / ".sdlc" / "runs" / rid)
    git(into, "add", "-f", "--", f".sdlc/runs/{rid}")
    git(into, "commit", "-qm", "the run folder")


def door(tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / tool), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


# ── P5: each command by its source, reports from both roots, changed files from the root ────


def test_p5_runs_a_declared_command_at_home_and_an_inherited_one_at_the_tree_root(tmp_path):
    """D25-29: a command the component declares runs in its own directory inside the tree; one
    it inherits from the workspace's top level runs at the tree's root, where it was written to
    run - `gradle build` in `services/a` finds no build file."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws",
                comp("a", "services/a", build_command=cmd(script, "mark", "own.txt")),
                comp("b", "services/b"),
                top=(f'test_command: "{cmd(script, "mark", "top.txt")}"\n'
                     f'coverage_command: "{cmd(script, "mark", "cov.txt")}"\n'))
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    S.execute(root, rid, changed=["a"])
    assert (home / "own.txt").is_file() and not (tree / "own.txt").exists()
    for inherited in ("top.txt", "cov.txt"):
        assert (tree / inherited).is_file() and not (home / inherited).exists(), inherited


def test_each_components_own_report_counts_once_and_never_anothers(tmp_path):
    """D25-17, D25-36c: `a`'s command writes its report in its own directory and `b`'s at the
    tree's root; `c`'s writes none. Each test step counts its own - read under `home` and the
    tree's root alike - and never another's: a report there before a step is not that step's
    (T595's floor, taken over both roots), so `c` ran nothing anyone can see, and one report two
    walks find is one report."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws",
                comp("a", "services/a", test_command=cmd(script, "junit", "TEST-a.xml", "3")),
                comp("b", "services/b",
                     test_command=cmd(script, "junit", "../../TEST-b.xml", "5")),
                comp("c", "services/c", test_command="exit 0"),
                paths=("services/a/app.py", "services/b/app.py", "services/c/app.py"))
    rid = run_in(root)
    W.create(root, rid)
    steps = {row["component"]: row for row in S.execute(
        root, rid, changed=["a", "b", "c"], steps=("test",))["steps"]}
    assert steps["a"]["verdict"] == steps["b"]["verdict"] == "green", steps
    assert steps["c"]["verdict"] == "unmeasurable", steps
    tree, home_a = entry(root, rid, "a")
    _tree, home_b = entry(root, rid, "b")
    assert S.tests_reported((home_a, tree)) == 8, "a report two walks find is one report"
    before = S.report_stamps((home_b, tree))
    (tree / "TEST-b.xml").write_text("<testsuite tests='7'/>", encoding="utf-8")
    assert S.tests_reported((home_b, tree), before=before) == 7, "a's report is never b's"


def test_a_root_coverage_report_matches_the_changed_files_spelled_from_the_root(tmp_path):
    """D25-28, D25-45a, D25-44: `a`'s coverage command writes a cobertura report under the tree's
    root naming both components' `app.py`. `a`'s changed file, spelled from the repository's root,
    matches its own entry - component-relative, `app.py` matched both, which is no match - and the
    row names the report relative to the tree's root, where it lies."""
    script = probe(tmp_path)
    cover = cmd(script, "cobertura", "../../reports/coverage.xml", "services/a/app.py",
                "services/b/app.py")
    root = mono(tmp_path / "ws", comp("a", "services/a", coverage_command=cover),
                comp("b", "services/b"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    assert S.changed_files(root, rid)[0] == {"a": ["services/a/app.py"], "b": []}
    row = S.execute(root, rid, changed=["a"], steps=("coverage",))["coverage"]["a"]
    assert (row["matched"], row["of"], row["report"]) == (1, 1, "reports/coverage.xml"), row


def test_a_deep_report_under_home_and_a_root_report_are_found_once_each(tmp_path):
    """D25-44: each walk's depth is measured from its own root - a report five levels under `a`'s
    directory is seven under the tree's root, past REPORT_DEPTH, and is found from `home`; a
    report at the tree's root is found from the tree - each once."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    deep = home / "build" / "test-results" / "test" / "x" / "TEST-deep.xml"
    deep.parent.mkdir(parents=True)
    deep.write_text("<testsuite tests='2'/>", encoding="utf-8")
    (tree / "TEST-root.xml").write_text("<testsuite tests='3'/>", encoding="utf-8")
    assert len(deep.relative_to(tree).parts) > S.REPORT_DEPTH
    assert sorted(p.name for p in S.report_stamps((home, tree))) == ["TEST-deep.xml",
                                                                     "TEST-root.xml"]
    assert S.tests_reported((home, tree)) == 5


def test_an_uncommitted_change_and_a_non_ascii_path_are_spelled_from_the_root(tmp_path):
    """D25-48, D25-59: diffed against the working tree, as `main` diffs it, so a tracked change
    not yet committed counts; read with `-z`, so a non-ASCII path is never quoted; each spelled
    from the repository's root."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    commit_in(tree, "services/a/café.py", "y = 1\n")
    (home / "app.py").write_text("x = 3\n", encoding="utf-8")
    diffs = S.changed_files(root, rid)[0]
    assert sorted(diffs["a"]) == ["services/a/app.py", "services/a/café.py"], diffs
    assert diffs["b"] == []


def test_a_monorepo_build_file_in_no_components_directory_runs_every_sharer(tmp_path):
    """D25-78: asked of the tree's root, the bound counted a monorepo's build file for none; asked
    of the workspace root's place in its repository, it counts, and every component sharing the
    checkout runs as changed, as on `main` and as T552's ruling states."""
    root = mono(tmp_path / "ws", paths=("services/a/app.py", "services/b/app.py",
                                        "gradle/libs.versions.toml", "tools/build/x.py"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "gradle/libs.versions.toml", "v = 2\n")
    commit_in(tree, "tools/build/x.py", "y = 2\n")
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert sorted(unknown) == ["a", "b"], notes
    assert any("gradle/libs.versions.toml" in n and "run as changed" in n for n in notes), notes
    assert not any("named and not counted" in n for n in notes), notes


def test_a_change_in_a_sibling_or_a_nested_repository_is_named_not_counted(tmp_path):
    """D25-88: the new bound answers for one of THIS RUN's trees of the workspace root's own
    repository only; a sibling repository's tree and a nested one's get `main`'s answer, so a
    file there in no component's directory is named and not counted."""
    root = init(tmp_path / "ws")
    files(root, "services/a/app.py")
    (root / ".gitignore").write_text("nested/\n", encoding="utf-8")
    profile(root, comp("a", "services/a"), comp("s", "../sib/one"), comp("n", "nested/one"))
    commit(root)
    for repo in (init(tmp_path / "sib"), init(root / "nested")):
        files(repo, "one/app.py", "other/x.py")
        commit(repo)
    rid = run_in(root)
    W.create(root, rid)
    for name in ("s", "n"):
        commit_in(entry(root, rid, name)[0], "other/x.py", "x = 2\n")
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert unknown == [], notes
    assert sum("named and not counted" in n and "other/x.py" in n for n in notes) == 2, notes


def test_in_place_components_in_a_second_checkout_get_mains_bound(tmp_path):
    """D25-98, D25-124: another checkout of the workspace root's own repository that is not this
    run's tree - here holding in-place components - gets `main`'s answer: a file there in no
    component's directory is named and not counted (gated on the common directory alone, a
    second checkout's `tools/build/x.py` ran both components as changed)."""
    root = mono(tmp_path / "ws", comp("c", "../ws2/services/c", deny=True),
                comp("d", "../ws2/services/d", deny=True),
                paths=("services/c/app.py", "services/d/app.py", "tools/build/x.py"))
    git(root, "worktree", "add", "-q", "-b", "other", str(tmp_path / "ws2"))
    rid = run_in(root)
    made = W.create(root, rid)["components"]
    assert made["c"]["mode"] == made["d"]["mode"] != "worktree"
    upstream(tmp_path / "ws2", "tools/build/x.py", "y = 2\n")
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert unknown == [] and any("named and not counted" in n for n in notes), notes


def test_an_in_place_components_own_checkout_is_never_read_as_a_run_tree(tmp_path):
    """D25-124: `_bound` is handed the worktree-mode entries' recorded trees, never `tree_of`
    over every entry - an in-place component's `home` can be its own checkout's root (here `y`,
    whose record names no base, so it claims nothing there), a second checkout of the workspace
    root's repository, where `main`'s bound names a file in no component's directory and counts
    it for none."""
    root = mono(tmp_path / "ws", comp("x", "../side/x", deny=True),
                comp("y", "../side", deny=True), paths=("x/app.py", "other/z.py"))
    side = tmp_path / "side"
    git(root, "worktree", "add", "-q", "-b", "other", str(side))
    rid = run_in(root)
    head = git(side, "rev-parse", "HEAD")
    row = {"mode": "in-place", "branch": "other", "pattern": "", "role": "authoritative",
           "why": "deny_raw_git", "at": "2026-01-01T00:00:00+00:00"}
    W.save_record(root, rid, {"components": {
        "x": {**row, "path": "../side/x", "base": head, "home": str(side / "x")},
        "y": {**row, "path": "../side", "base": "", "home": str(side)}}})
    upstream(side, "other/z.py", "z = 2\n")
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert unknown == ["y"], notes
    assert any("named and not counted" in n and "other/z.py" in n for n in notes), notes


def test_a_workspace_root_in_no_repository_gets_mains_bound(tmp_path):
    """D25-99: where the workspace root answers no repository, no tree is this workspace's -
    P5 answers as `main`, naming the file and counting it for none, never refusing."""
    ws = estate(tmp_path / "ws", {"mono": ("services/a/app.py", "services/b/app.py",
                                           "tools/build/x.py")})
    profile(ws, comp("a", "mono/services/a"), comp("b", "mono/services/b"))
    rid = run_in(ws)
    W.create(ws, rid)
    commit_in(entry(ws, rid, "a")[0], "tools/build/x.py", "y = 2\n")
    _diffs, notes, unknown = S.changed_files(ws, rid)
    assert unknown == [] and any("named and not counted" in n for n in notes), notes


def test_the_bound_answers_for_a_run_tree_however_its_root_is_spelled(tmp_path):
    """D25-107: the stored and live spellings of one tree root differ, so a literal comparison
    never fires; compared resolved, a build file in no component's directory counts."""
    root = mono(tmp_path / "ws", paths=("services/a/app.py", "services/b/app.py",
                                        "gradle/libs.versions.toml"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    top = W.git(tree, "rev-parse", "--show-toplevel")
    spelled = str(tree.parent / ".." / tree.parent.name / tree.name)
    assert spelled != top
    counts, _outside = S._bound(root, top, {"services/a/", "services/b/"}, [spelled])
    assert counts("gradle/libs.versions.toml")


def test_git_failing_for_a_run_tree_is_noted_and_mains_bound_taken(tmp_path, monkeypatch):
    """D25-109: where git cannot say whether one of this run's trees is a checkout of the
    workspace's repository, the bound is taken as `main` takes it and a note says so."""
    root = mono(tmp_path / "ws", paths=("services/a/app.py", "services/b/app.py",
                                        "gradle/libs.versions.toml"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "gradle/libs.versions.toml", "v = 2\n")
    real = W.repo_key
    monkeypatch.setattr(W, "repo_key",
                        lambda where: ("", "boom") if same(where, tree) else real(where))
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert unknown == [], notes
    assert any("git could not say whether" in n and "(boom), so the bound was taken as `main` "
               "takes it" in n for n in notes), notes


def test_a_home_the_runs_own_commit_removed_is_said_and_its_steps_blocked(tmp_path):
    """D25-115, D25-105: the run's own work removed `services/b` from the tree. Every reader says
    so - never "its recorded tree is gone" - and P5 starts nothing there: each step `blocked`
    with no exit, the way on named, so the phase stops ("not run" let it go green)."""
    root = mono(tmp_path / "ws", comp("a", "services/a"),
                comp("b", "services/b", build_command="exit 0", test_command="exit 0"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "b")
    drop(tree, "services/b", "the run removes b")
    said = f"`b`'s directory `services/b` is not in {W._stored_home(root, str(tree))}"
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert "b" in unknown and any(said in n for n in notes), notes
    assert not any("its recorded tree is gone" in n for n in notes), notes
    out = S.execute(root, rid, changed=["b"], steps=("build", "test"))
    rows = [row for row in out["steps"] if row["component"] == "b"]
    assert [row["step"] for row in rows] == ["build", "test"], rows
    assert all(row["verdict"] == "blocked" and row["exit_code"] is None for row in rows), rows
    assert rows[0]["note"] == (f"{said}, so nothing was started there - restore `services/b` "
                               f"in the tree, or drop `b` from the profile for this run")
    assert out["verdict"] != "green"


# ── {worktree}: every phase's address ────────────────────────────────────────────────────


def test_the_worktree_value_is_the_trees_root_for_a_component_and_for_the_run(tmp_path):
    """D25-27, D25-14: `{worktree}` - P4's work address, P6's review address - is the tree's root:
    the run's patch is written from it and P6's pre-pass resolves the patch's paths against it,
    which from `a`'s directory copied nothing. With no component, the one tree the run's
    components share; with two trees, the workspace root, as before."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    base = W.create(root, rid)["components"]["a"]["base"]
    tree, home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    address = Path(ST._worktree_for(root, rid, "a"))
    assert same(address, tree) and same(ST._worktree_for(root, rid, ""), tree)
    patch = W.git(address, "format-patch", "--stdout", base)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    copied, _dropped, notes = prepass.diff_scope(address, patch, scratch)
    assert copied == ["services/a/app.py"], notes
    assert prepass.diff_scope(home, patch, tmp_path)[0] == [], "from `home` it copied nothing"
    two = mono(tmp_path / "two", comp("a", "services/a"),
               comp("b", "services/b", pattern="sdlc/b-<run-id>"))
    rid2 = run_in(two)
    W.create(two, rid2)
    assert same(ST._worktree_for(two, rid2, ""), two)


# ── P9: the deploy's cwd by its command's source ────────────────────────────────────────


def test_a_named_deploy_runs_a_declared_command_at_home_an_inherited_one_at_the_root(tmp_path):
    """D25-1, D25-29: P9's deploy of one component runs in its own directory inside the tree when
    the component declares the command, and at the tree's root when it inherits the workspace's -
    where that command was written to run."""
    script = probe(tmp_path)
    where = cmd(script, "cwd")
    root = mono(tmp_path / "ws", comp("a", "services/a"),
                comp("b", "services/b", deploy_command=where),
                top=f'deploy_command: "{where}"\n')
    rid = at_p9(root)
    W.create(root, rid)
    tree, home_b = entry(root, rid, "b")
    inherited = D.run(root, rid, "a")
    assert inherited["command_source"] == "top-level", inherited
    assert same(inherited["stdout"].strip(), tree), inherited
    declared = D.run(root, rid, "b")
    assert declared["command_source"] == "component", declared
    assert same(declared["stdout"].strip(), home_b), declared


def test_a_one_act_of_an_inherited_command_runs_in_the_shared_tree(tmp_path):
    """D25-19 (decision 51): no component declares a deploy command, so the deploy is one act of
    the inherited one; the run's components share ONE tree, so it runs there - the tree holds the
    whole change - and `root_lacks` does not arise, though the root lacks the change."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", top=f'deploy_command: "{cmd(script, "cwd")}"\n')
    rid = at_p9(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    assert D.root_lacks(root, rid) == ""
    row = D.run(root, rid)
    assert row["deployed"] and row["component"] == "", row
    assert same(row["stdout"].strip(), tree), row


def test_a_one_act_over_two_trees_waits_for_the_root_to_hold_the_change(tmp_path):
    """D25-19: with two trees - patterns giving two branches - the one act runs at the root,
    refused until the root holds the change: never run at the root unguarded."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", comp("a", "services/a"),
                comp("b", "services/b", pattern="sdlc/b-<run-id>"),
                top=f'deploy_command: "{cmd(script, "cwd")}"\n')
    rid = at_p9(root)
    W.create(root, rid)
    commit_in(entry(root, rid, "a")[0], "services/a/app.py", "x = 2\n")
    with pytest.raises(D.DeployError, match="does not hold this run's change"):
        D.run(root, rid)
    assert D.recorded(root, rid) is None


def test_root_lacks_reads_a_shared_tree_once(tmp_path):
    """D25-35, D25-19: `root_lacks` reads each distinct tree's change once and names its file
    once - counted by record entries, a shared tree's file was named once per component."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", comp("a", "services/a"), comp("b", "services/b"),
                comp("c", "services/c", pattern="sdlc/c-<run-id>"),
                paths=("services/a/app.py", "services/b/app.py", "services/c/app.py"),
                top=f'deploy_command: "{cmd(script, "cwd")}"\n')
    rid = at_p9(root)
    W.create(root, rid)
    commit_in(entry(root, rid, "a")[0], "services/a/app.py", "x = 2\n")
    said = D.root_lacks(root, rid)
    assert said.count("services/a/app.py") == 1, said
    assert "lacks 1 of a and b's changed file(s)" in said, said


def test_a_one_component_workspace_deploys_its_own_command_at_home_both_ways(tmp_path,
                                                                             monkeypatch):
    """D25-133: a profile of one component declaring its own deploy command makes THAT command
    the one act; it runs where `--component a` runs it - `<tree>/services/a` - recorded under ""
    with `root_lacks` never asked; and at a removed `home` it is refused before it starts, the
    preview exiting 1."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", comp("a", "services/a", deploy_command=cmd(script, "cwd")),
                paths=("services/a/app.py", "README.md"))
    rid = at_p9(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")

    def asked(*_args):
        raise AssertionError("root_lacks was asked of a one act that runs at a home")

    monkeypatch.setattr(D, "root_lacks", asked)
    one = D.run(root, rid)
    assert one["component"] == "" and same(one["stdout"].strip(), home), one
    assert same(D.run(root, rid, "a")["stdout"].strip(), home)
    monkeypatch.undo()
    drop(tree, "services/a")
    before = D.recorded(root, rid)
    with pytest.raises(D.DeployError, match=r"^`a`'s directory `services/a` is not in .*; "
                                            r"restore `services/a` in the tree, or drop `a` "
                                            r"from the profile for this run$"):
        D.run(root, rid)
    assert D.recorded(root, rid) == before, "a refused deploy wrote a record"
    preview = door("deploy.py", "--target", str(root), "--run", rid)
    assert preview.returncode == 1, (preview.stdout, preview.stderr)
    assert "!! `a`'s directory `services/a` is not in" in preview.stdout, preview.stdout


def test_a_one_act_at_home_is_never_asked_root_lacks_whatever_trees_the_record_holds(tmp_path):
    """D25-133: `root_lacks` is asked only of a one act that runs at the root, so the
    distinct-trees threshold never moves a component's own command off its `home` - here the
    record still holds `b` in a tree of its own, holding a change the root lacks: the preview
    stands and the deploy runs at `a`'s `home`."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", comp("a", "services/a"),
                comp("b", "services/b", pattern="sdlc/b-<run-id>"))
    rid = at_p9(root)
    W.create(root, rid)
    commit_in(entry(root, rid, "b")[0], "services/b/app.py", "x = 2\n")
    profile(root, comp("a", "services/a", deploy_command=cmd(script, "cwd")))
    preview = door("deploy.py", "--target", str(root), "--run", rid)
    assert preview.returncode == 0 and "!!" not in preview.stdout, (preview.stdout,
                                                                    preview.stderr)
    row = D.run(root, rid)
    assert row["deployed"] and same(row["stdout"].strip(), entry(root, rid, "a")[1]), row


def test_a_one_act_whose_component_the_record_lacks_runs_in_its_own_checkout(tmp_path):
    """D25-140: the profile's one component declares its own command and the run's record holds
    only another component, one the profile dropped: the one act runs where `--component a` runs
    it - `a`'s own checkout - never in the other component's directory (B4-2's rule)."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws")
    rid = at_p9(root)
    W.create(root, rid, ["b"])
    profile(root, comp("a", "services/a", deploy_command=cmd(script, "cwd")))
    row = D.run(root, rid)
    assert row["deployed"] and same(row["stdout"].strip(), root / "services" / "a"), row


def test_a_one_act_beside_a_dropped_tree_mate_deploys_at_home_without_root_lacks(tmp_path):
    """D25-141: the record still holds `b`, a tree-mate the profile dropped, and the tree holds
    the change. The profile's one component declares its own command, so its one act runs at its
    `home` with `root_lacks` not asked - where `main` refused the unnamed form until the root held
    the change, while `--component a` already deployed there."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws")
    rid = at_p9(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    profile(root, comp("a", "services/a", deploy_command=cmd(script, "cwd")))
    row = D.run(root, rid)
    assert row["deployed"] and same(row["stdout"].strip(), home), row


def test_a_named_deploy_from_a_components_home_holds_the_close_out(tmp_path):
    """D25-30: the serving question is asked of every recorded deploy whose `where` lies in the
    tree - asked by the tree's root alone, a by-name deploy from `services/a` was missed and the
    tree removed from under it."""
    root = mono(tmp_path / "ws", comp("a", "services/a", deploy_command="exit 0"),
                comp("b", "services/b", deploy_command="exit 0"))
    rid = at_p9(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    row = D.run(root, rid, "a")
    assert row["deployed"] and same(root / row["where"], home), row
    with pytest.raises(W.WorktreeError, match="may still be serving"):
        W.remove(root, rid)
    assert tree.is_dir(), "the tree was removed from under a deploy"


def test_p9_refuses_a_declared_command_at_a_removed_home_and_runs_an_inherited_one(tmp_path):
    """D25-113, D25-105: the run's own commit removed `services/`. `b` declares its deploy
    command - its cwd is its `home` - so that deploy is refused before it starts, with its way
    on, nothing recorded and the close-out not held by it; `a` inherits the workspace's command,
    which runs at the tree's root, and deploys."""
    script = probe(tmp_path)
    where = cmd(script, "cwd")
    root = mono(tmp_path / "ws", comp("a", "services/a"),
                comp("b", "services/b", deploy_command=where),
                top=f'deploy_command: "{where}"\n')
    rid = at_p9(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    drop(tree, "services", "the run removes both directories")
    with pytest.raises(D.DeployError) as refused:
        D.run(root, rid, "b")
    assert str(refused.value) == (
        f"`b`'s directory `services/b` is not in {W._stored_home(root, str(tree))}; restore "
        f"`services/b` in the tree, or drop `b` from the profile for this run")
    assert D.recorded(root, rid) is None and W.serving_from(root, rid, tree) == ""
    row = D.run(root, rid, "a")
    assert row["deployed"] and same(row["stdout"].strip(), tree), row


def test_the_removed_home_refusal_is_listed_by_the_preview_and_the_door_exits_2(tmp_path):
    """D25-123, D25-132: asked in the door beside `needs_component` and `root_lacks`, named or
    not: the preview lists it and exits 1, `--json` carries it among the refusals, and `--deploy`
    exits 2 - never 3, which says the deploy could not look."""
    root = mono(tmp_path / "ws", comp("a", "services/a", deploy_command="exit 0"),
                comp("b", "services/b", deploy_command="exit 0"))
    rid = at_p9(root)
    W.create(root, rid)
    drop(entry(root, rid, "b")[0], "services/b")
    args = ("--target", str(root), "--run", rid, "--component", "b")
    preview = door("deploy.py", *args)
    assert preview.returncode == 1, (preview.stdout, preview.stderr)
    assert "!! `b`'s directory `services/b` is not in" in preview.stdout, preview.stdout
    listed = json.loads(door("deploy.py", *args, "--json").stdout)
    assert any(why.startswith("`b`'s directory `services/b`") for why in listed["refused"]), \
        listed
    deploy = door("deploy.py", *args, "--deploy")
    assert deploy.returncode == 2, (deploy.stdout, deploy.stderr)
    assert deploy.stderr.startswith("refused: `b`'s directory `services/b`"), deploy.stderr
    assert "Traceback" not in preview.stderr + deploy.stderr
    assert D.recorded(root, rid) is None


def test_a_deploy_whose_checkout_directory_is_gone_is_refused_before_it_starts(tmp_path):
    """D25-149: the record does not hold `a`, so `--component a` runs in `a`'s own checkout - and
    that directory is gone: refused before it starts, with its way on; started there, the
    runner's exit `None` held P9 owed for ever."""
    root = mono(tmp_path / "ws", comp("a", "services/a", deploy_command="exit 0"),
                comp("b", "services/b", deploy_command="exit 0"))
    rid = at_p9(root)
    W.create(root, rid, ["b"])
    shutil.rmtree(root / "services" / "a")
    with pytest.raises(D.DeployError, match=r"^`a`'s directory `services/a` is not in the "
                                            r"workspace checkout; restore it, or drop `a` from "
                                            r"the profile for this run$"):
        D.run(root, rid, "a")
    assert D.recorded(root, rid) is None


# ── the repository's readers: branch, push wall, release ────────────────────────────────


def test_branch_of_answers_for_one_shared_tree_and_not_across_repositories(tmp_path):
    """D25-31: the branch where every component shares one tree; two trees recording one branch
    name - in two repositories - are two branches, so "" as before."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    assert R.branch_of(root, rid) == f"sdlc/{rid}"
    ws = estate(tmp_path / "estate", {"one": ("app.py",), "two": ("app.py",)})
    profile(ws, comp("one", "one"), comp("two", "two"))
    rid2 = run_in(ws)
    rows = W.create(ws, rid2)["components"]
    assert rows["one"]["branch"] == rows["two"]["branch"] and R.branch_of(ws, rid2) == ""


def test_the_push_walls_cli_allows_a_shared_tree_run_whose_branch_carries_its_folder(tmp_path):
    """D25-1: the push wall's CLI on a shared-tree run - the branch from the record, the wall
    standing at the tree's root - exits 0 once the branch carries the run folder."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    carry_run_folder(root, rid, entry(root, rid, "a")[0])
    proc = door("release.py", "--target", str(root), "--run", rid)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert f"branch sdlc/{rid} -> origin: allowed" in proc.stdout, proc.stdout


def test_the_wall_reads_the_run_folder_from_a_home_below_the_trees_root(tmp_path):
    """D25-51: from a component's own directory the bare `ls-tree` answers nothing at exit 0 -
    the cardinal rule's trap; with `--full-tree` the folder is read wherever the wall stands, and
    a branch without it is still refused."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    assert R.unpublished_run(root, rid, f"sdlc/{rid}", where=home), "not carried: refused"
    carry_run_folder(root, rid, tree)
    assert R.unpublished_run(root, rid, f"sdlc/{rid}", where=home) == []


def test_a_workspace_in_a_repositorys_subdirectory_is_read_by_its_prefix(tmp_path):
    """D25-76, D25-87: the workspace and its `.sdlc/` in `ws/` of its repository - the run folder
    is `ws/.sdlc/runs/<run-id>` from the repository's root, read by the install target's own
    prefix, with trees and without."""
    repo = init(tmp_path / "repo")
    ws = repo / "ws"
    files(ws, "services/a/app.py", "services/b/app.py")
    profile(ws, comp("a", "services/a"), comp("b", "services/b"))
    commit(repo)
    # `intake.dirty_paths` exempts `.sdlc/runs` at the repository's root alone, so a workspace
    # in a subdirectory reads its own run folder as uncommitted work (`main`'s; T685, Deferred);
    # excluded here, as an adopter would ignore it.
    (repo / ".git" / "info" / "exclude").write_text("ws/.sdlc/runs/" + chr(10),
                                                    encoding="utf-8")
    rid = run_in(ws)
    W.create(ws, rid)
    tree, _home = entry(ws, rid, "a")
    assert R.unpublished_run(ws, rid, f"sdlc/{rid}", where=tree), "not carried: refused"
    carry_run_folder(ws, rid, tree / "ws")
    assert R.unpublished_run(ws, rid, f"sdlc/{rid}", where=tree) == []
    assert R.unpublished_run(ws, rid, "main", where=ws), "not committed on main: refused"
    git(repo, "add", "-f", "--", f"ws/.sdlc/runs/{rid}")
    git(repo, "commit", "-qm", "the run folder")
    assert R.unpublished_run(ws, rid, "main", where=ws) == []


def test_a_target_installed_above_the_workspace_root_reads_its_own_folder(tmp_path):
    """D25-89: `workspace_root: ws` - the target holds `.sdlc/` at its repository's root and the
    workspace is `ws/` below it. The run folder is `.sdlc/runs/<run-id>` by the TARGET's prefix;
    by the workspace root's, `ws/.sdlc/...` refused a push `main` allows."""
    repo = init(tmp_path / "repo")
    files(repo, "ws/services/a/app.py", "ws/services/b/app.py")
    profile(repo, comp("a", "services/a"), comp("b", "services/b"), top='workspace_root: "ws"\n')
    commit(repo)
    rid = run_in(repo)
    W.create(repo, rid)
    tree, home = entry(repo, rid, "a")
    assert same(home, tree / "ws" / "services" / "a")
    carry_run_folder(repo, rid, tree)
    assert R.unpublished_run(repo, rid, f"sdlc/{rid}", where=tree) == []


def test_a_target_in_no_repository_is_asked_with_no_prefix_never_skipped(tmp_path):
    """D25-90: a multi-repository workspace's root is in no repository, so it answers no prefix:
    the wall asks `main`'s question - refusing a branch without the folder, never skipping the
    check, never raising."""
    ws = estate(tmp_path / "estate", {"one": ("app.py",), "two": ("app.py",)})
    profile(ws, comp("one", "one"), comp("two", "two"))
    rid = run_in(ws)
    W.create(ws, rid)
    tree, _home = entry(ws, rid, "one")
    assert R.unpublished_run(ws, rid, f"sdlc/{rid}", where=tree), "asked, and refused"
    carry_run_folder(ws, rid, tree)
    assert R.unpublished_run(ws, rid, f"sdlc/{rid}", where=tree) == []


def test_a_fork_given_its_own_pattern_leaves_the_shared_trees_push_allowed(tmp_path):
    """D25-4, D25-16, D25-47, D25-67: two repositories on the default pattern, the second holding
    a `fork`. Sharing its repository's tree, the fork is refused by the plan; the remedy followed
    - a pattern of its own - is made, the first repository's shared tree pushes (exit 0), and the
    fork's own branch is refused at the wall, as on `main`."""
    ws = estate(tmp_path / "estate", {"one": ("services/a/app.py", "services/b/app.py"),
                                      "two": ("c/app.py", "f/app.py")})
    shared = (comp("a", "one/services/a"), comp("b", "one/services/b"), comp("c", "two/c"))
    profile(ws, *shared, comp("f", "two/f", role="fork"))
    rid = run_in(ws)
    with pytest.raises(W.WorktreeError, match="fork"):
        W.create(ws, rid)
    # Declared first: `main`'s wall asks the first component whose pattern claims the branch, and
    # the default pattern claims `sdlc/f-<run-id>` too (D25-46, its order dependence, Deferred).
    profile(ws, comp("f", "two/f", role="fork", pattern="sdlc/f-<run-id>"), *shared)
    W.create(ws, rid)
    carry_run_folder(ws, rid, entry(ws, rid, "a")[0])
    pushed = door("release.py", "--target", str(ws), "--run", rid, "--branch", f"sdlc/{rid}")
    assert pushed.returncode == 0 and "allowed" in pushed.stdout, (pushed.stdout, pushed.stderr)
    fork = door("release.py", "--target", str(ws), "--run", rid, "--branch", f"sdlc/f-{rid}")
    assert fork.returncode == 1 and "role is `fork`" in fork.stdout, (fork.stdout, fork.stderr)


def test_the_release_cut_runs_at_the_trees_root(tmp_path):
    """D25-9, D25-23, D25-36a: a single component at `services/a` has `<tree>/services/a` as its
    `home`; the release cut - every step, and the artifact version dial - runs at the tree's root,
    as `main` runs it, so the changelog lands there."""
    script = probe(tmp_path)
    root = mono(tmp_path / "ws", comp("a", "services/a"), paths=("services/a/app.py",),
                top=f'release:\n  changelog_command: "{cmd(script, "mark", "CHANGELOG.md")}"\n')
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    assert same(home, tree / "services" / "a") and same(R.worktree_for(root, rid), tree)
    R.cut(root, rid, version="1.0.0")
    assert (tree / "CHANGELOG.md").is_file() and not (home / "CHANGELOG.md").exists()


# ── the repository's git acts: rebase, upstream, remove ─────────────────────────────────


def test_rebasing_one_component_moves_every_row_of_its_tree(tmp_path):
    """D25-2: one rebase moves the shared tree, so `base`, `previous_base`, `rebased_at` and
    `rebased_onto` are written on both rows; neither `upstream_commits` nor `changed_files`
    credits the other with the arrival, and the tree-mate's own call says it is caught up."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    upstream(root, "README.md", "upstream\n")
    result = W.rebase(root, rid, "a", "main")
    assert result["rebased"] and result["notes"] == [], result
    head = git(root, "rev-parse", "main")
    rows = W.load_record(root, rid)["components"]
    for name in ("a", "b"):
        row = rows[name]
        assert (row["base"], row["rebased_onto"]) == (head, "main"), row
        assert row["previous_base"] != head and row["rebased_at"] == rows["a"]["rebased_at"]
    assert W.upstream_commits(root, rid, "b", "main") == []
    assert W.rebase(root, rid, "b", "main")["note"] == "already on the latest upstream"
    diffs, notes, unknown = S.changed_files(root, rid)
    assert (diffs, unknown) == ({"a": ["services/a/app.py"], "b": []}, []), notes


def test_a_rebase_that_leaves_a_tree_mates_directory_out_is_made_and_noted(tmp_path, capsys):
    """D25-105, D25-111: upstream removed `services/b`. The rebase is made, as `main` makes it -
    `onto` cannot say what a rebase produces - and a note after it names `b`, whose directory the
    tree no longer holds, with its way on; the CLI prints it, and the readers go on saying so."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "services/a/app.py", "x = 2\n")
    drop(root, "services/b", "upstream drops b")
    assert W.main(["--target", str(root), "--run", rid, "--component", "a",
                   "--rebase", "main"]) == 0
    where = W._stored_home(root, str(tree))
    note = (f"`b`'s directory `services/b` is not in {where} after the rebase; restore it in "
            f"the tree, or drop `b` from the profile for this run")
    assert f"  note: {note}" in capsys.readouterr().out
    assert W.load_record(root, rid)["components"]["b"]["rebased_onto"] == "main"
    _diffs, notes, unknown = S.changed_files(root, rid)
    assert "b" in unknown and any(f"`b`'s directory `services/b` is not in {where}" in n
                                  for n in notes), notes


def test_a_rebase_keeping_a_run_added_directory_and_mains_no_op_say_nothing(tmp_path):
    """D25-111: a directory the run's own branch added is in no upstream commit - a pre-check
    asked of `onto` refused this rebase - and the rebase keeps it: made, no note. A rebase with
    nothing upstream is `main`'s no-op: no note either."""
    root = mono(tmp_path / "ws", comp("a", "services/a"), comp("c", "services/c"),
                paths=("services/a/app.py",))
    rid = run_in(root)
    W.create(root, rid, ["a"])
    tree, _home = entry(root, rid, "a")
    commit_in(tree, "services/c/app.py", "z = 1\n", "the run adds c")
    files(root, "services/c/app.py")
    assert W.create(root, rid, ["c"])["made"] == ["c"]
    noop = W.rebase(root, rid, "a", "main")
    assert (noop["note"], noop["notes"]) == ("already on the latest upstream", []), noop
    upstream(root, "README.md", "upstream\n")
    result = W.rebase(root, rid, "a", "main")
    assert result["rebased"] and result["notes"] == [], result


def test_a_rebase_notes_every_answer_but_a_directory_in_gits_own_text(tmp_path, monkeypatch):
    """D25-146, D25-118: after the move each tree-mate's place is read through the one helper - a
    directory or a submodule needs no note; a git failure is could-not-look, in git's own text,
    read after the record is written, so the rebase that happened is recorded."""
    root = mono(tmp_path / "ws", comp("a", "services/a"), comp("b", "services/b"),
                comp("c", "services/c"),
                paths=("services/a/app.py", "services/b/app.py", "services/c/app.py"))
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    upstream(root, "README.md", "upstream\n")
    answers = {"services/a": ("dir", "040000"), "services/b": ("submodule", "160000"),
               "services/c": ("error", "fatal: not a tree object")}
    monkeypatch.setattr(W, "place_entry", lambda _at, _base, place: answers[place])
    result = W.rebase(root, rid, "a", "main")
    assert result["notes"] == [
        f"could not look whether `c`'s directory `services/c` is in "
        f"{W._stored_home(root, str(tree))} after the rebase (fatal: not a tree object)"], result
    assert W.load_record(root, rid)["components"]["c"]["rebased_onto"] == "main"


def test_a_root_component_is_never_asked_its_empty_place_after_a_rebase(tmp_path):
    """D25-120, D25-135: a component at its repository's root has an empty place, never asked -
    git refuses an empty pathspec - so a rebase of its tree notes nothing for it, beside a
    component below it."""
    root = mono(tmp_path / "ws", comp("r", "."), comp("a", "services/a"),
                paths=("app.py", "services/a/app.py"))
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "r")
    assert same(home, tree) and same(entry(root, rid, "a")[0], tree)
    upstream(root, "README.md", "upstream\n")
    result = W.rebase(root, rid, "a", "main")
    assert result["rebased"] and result["notes"] == [], result


def test_a_rebase_notes_only_its_own_trees_components(tmp_path):
    """D25-121: read over the whole record, the note named another tree's component; read over
    THIS tree's, it names `b` alone - `c`, of another repository, is never asked."""
    ws = estate(tmp_path / "estate", {"one": ("services/a/app.py", "services/b/app.py"),
                                      "two": ("services/c/app.py",)})
    profile(ws, comp("a", "one/services/a"), comp("b", "one/services/b"),
            comp("c", "two/services/c"))
    rid = run_in(ws)
    W.create(ws, rid)
    drop(ws / "one", "services/b", "upstream drops b")
    notes = W.rebase(ws, rid, "a", "main")["notes"]
    assert len(notes) == 1 and notes[0].startswith("`b`'s directory `services/b`"), notes


def test_a_rebase_naming_a_component_whose_home_the_run_removed_acts_at_the_root(tmp_path):
    """D25-112, D25-122, D25-26c: the run's own commit removed `services/a`; asked at that `home`,
    `--rebase` blamed a force-push or a gc. Every act is asked at the tree's root, so the rebase
    is answered as `main` answers it, and the note says `a`'s directory is not in the tree."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "a")
    drop(tree, "services/a", "the run removes a")
    upstream(root, "README.md", "upstream\n")
    assert len(W.upstream_commits(root, rid, "a", "main")) == 1
    result = W.rebase(root, rid, "a", "main")
    assert result["rebased"] and not home.exists(), result
    assert result["notes"] == [
        f"`a`'s directory `services/a` is not in {W._stored_home(root, str(tree))} after the "
        f"rebase; restore it in the tree, or drop `a` from the profile for this run"]
    assert W.load_record(root, rid)["components"]["b"]["base"] == git(root, "rev-parse", "main")


def test_root_components_are_created_and_rebased_as_main_makes_them(tmp_path):
    """D25-110: a one-component-at-root run, and a multi-repository estate of root components:
    each tree its component's own, and each rebase moving its own tree's row alone."""
    root = mono(tmp_path / "ws", comp("app", "."), paths=("app.py",))
    rid = run_in(root)
    W.create(root, rid)
    tree, home = entry(root, rid, "app")
    assert same(tree, home)
    upstream(root, "README.md", "upstream\n")
    assert W.rebase(root, rid, "app", "main")["notes"] == []
    ws = estate(tmp_path / "estate", {"one": ("app.py",), "two": ("app.py",)})
    profile(ws, comp("one", "one"), comp("two", "two"))
    rid2 = run_in(ws)
    W.create(ws, rid2)
    upstream(ws / "two", "README.md", "upstream\n")
    assert W.rebase(ws, rid2, "two", "main")["rebased"]
    rows = W.load_record(ws, rid2)["components"]
    assert "rebased_at" not in rows["one"] and rows["two"]["rebased_at"], rows


def test_removing_a_shared_tree_stamps_both_rows(tmp_path, capsys):
    """D25-39: the tree is checked once and removed once, and its removal stamps `removed_at` on
    every component of it - stamped on one, `--list` showed a live tree-mate whose tree was
    gone; "already gone" is said once for the tree."""
    root = mono(tmp_path / "ws")
    rid = run_in(root)
    W.create(root, rid)
    tree, _home = entry(root, rid, "a")
    assert W.remove(root, rid) == [f"a, b: removed {tree}"]
    assert not tree.exists()
    rows = W.load_record(root, rid)["components"]
    assert rows["a"]["removed_at"] and rows["a"]["removed_at"] == rows["b"]["removed_at"]
    assert W.main(["--target", str(root), "--run", rid, "--list"]) == 0
    assert capsys.readouterr().out.count("(removed)") == 2
    assert W.remove(root, rid) == ["a, b: already gone"]


def test_a_legacy_two_repository_record_reads_as_before(tmp_path):
    """D25-7: a record written before batch 25 has no `tree`, so each `home` is its own tree and
    its distinct trees stay distinct through `root_lacks`, `rebase` and `remove`."""
    ws = estate(tmp_path / "estate", {"one": ("app.py",), "two": ("app.py",)})
    profile(ws, comp("one", "one"), comp("two", "two"))
    rid = run_in(ws)
    record, homes = {}, {}
    for name in ("one", "two"):
        base = git(ws / name, "rev-parse", "HEAD")
        homes[name] = ws / ".sdlc-worktrees" / rid / name
        git(ws / name, "worktree", "add", "-q", "-b", f"sdlc/{rid}", str(homes[name]), base)
        record[name] = {"mode": "worktree", "path": name, "branch": f"sdlc/{rid}",
                        "pattern": "", "base": base, "home": str(homes[name]),
                        "role": "authoritative", "why": "", "at": "2026-01-01T00:00:00+00:00"}
    W.save_record(ws, rid, {"components": record})
    commit_in(homes["one"], "app.py", "x = 2\n")
    assert "lacks 1 of one's changed file(s): app.py" in D.root_lacks(ws, rid)
    upstream(ws / "two", "README.md", "upstream\n")
    assert W.rebase(ws, rid, "two", "main")["rebased"]
    rows = W.load_record(ws, rid)["components"]
    assert "rebased_at" not in rows["one"] and rows["two"]["rebased_at"], rows
    assert W.remove(ws, rid) == [f"one: removed {homes['one']}", f"two: removed {homes['two']}"]
